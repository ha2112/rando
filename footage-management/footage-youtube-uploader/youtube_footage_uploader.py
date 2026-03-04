"""
youtube_uploader.py
────────────────────────────────────────────────────────────────────────────
Recursively scans a DJI footage directory, identifies video files tagged
[VID], and uploads them to YouTube as PRIVATE videos.

Naming conventions expected (Supports both formats below):
  Old: [DD-MM-YY_HH-MM-SS]_[VID]_description_#X.mp4
  New:[DD-MM-YY_HH-MM-SS]_description_[VID]_#X.mp4
  Folder: [YYYY.MM.DD] #N Folder Description

Dependencies (install once):
  pip install google-auth-oauthlib google-api-python-client tqdm python-dotenv

Place client_secrets.json (OAuth 2.0 Desktop app) next to this script.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

# Third-party imports
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from tqdm import tqdm

# ── Configuration ────────────────────────────────────────────────────────────

# Load environment variables from a .env file (useful for hiding system paths)
load_dotenv()

# Read the target footage folder from environment variables
FOOTAGE_FOLDER   = os.getenv("DJI_FOOTAGE_FOLDER_PATH", "")

# Paths for OAuth secrets and local state tracking, stored adjacent to this script
CLIENT_SECRETS   = Path(__file__).parent / "client_secrets.json"
TOKEN_FILE       = Path(__file__).parent / "token.json"
UPLOAD_DB_FILE   = Path(__file__).parent / "uploaded_files.json"

# YouTube API scopes (this specific scope allows uploading and managing videos)
SCOPES           =["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_API_NAME = "youtube"
YOUTUBE_API_VER  = "v3"

# Chunk size for resumable uploads (1 MB is a good balance for API stability and memory)
CHUNK_SIZE       = 1 * 1024 * 1024   # 1 MB

# Retry settings for transient HTTP errors (e.g., brief network disconnects)
MAX_RETRIES      = 5
RETRY_SLEEP_BASE = 2   # seconds  (exponential back-off: 2, 4, 8, 16, 32 s)

# ── Filename / foldername parsers ─────────────────────────────────────────────

# Matches: [DD-MM-YY_HH-MM-SS]_anything_#X.mp4
# Group 1: Timestamp (DD-MM-YY_HH-MM-SS)
# Group 2: Raw description string (which contains [VID] somewhere)
# Group 3: Part number (X)
# Group 4: File extension (mp4 or mov)
VIDEO_FILE_RE = re.compile(
    r"^\[(\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\]_(.+)_#(\d+)\.(mp4|mov)$",
    re.IGNORECASE,
)

# Matches: [YYYY.MM.DD] #N Description
# Group 1: Date (YYYY.MM.DD)
# Group 2: Folder index number
# Group 3: Human-readable description
FOLDER_RE = re.compile(
    r"^\[(\d{4}\.\d{2}\.\d{2})\]\s*#(\d+)\s+(.+)$"
)


def parse_video_filename(filename: str) -> dict | None:
    """Return parsed components of a [VID] file, or None if no match."""
    m = VIDEO_FILE_RE.match(filename)
    if not m:
        return None
    
    # Extract data using regex capture groups
    timestamp, raw_description, part_num, ext = m.groups()
    
    # Clean up the description by removing the [VID] tag and any stray underscores.
    # This automatically fixes both:
    # Old Format: [Timestamp]_[VID]_description_#X
    # New Format:[Timestamp]_description_[VID]_#X
    clean_description = raw_description.replace("[VID]", "").strip("_")
    
    return {
        "timestamp":   timestamp,           # e.g., 27-02-26_15-13-00
        "description": clean_description,   # e.g., flying_over_park
        "part_num":    int(part_num),       # e.g., 1
        "extension":   ext.lower(),         # e.g., mp4
    }


def parse_folder_name(folder_name: str) -> dict | None:
    """Return parsed components of a dated folder, or None if no match."""
    m = FOLDER_RE.match(folder_name)
    if not m:
        return None
    
    date, num, description = m.groups()
    return {
        "date":        date,                # e.g., 2026.02.27
        "num":         int(num),            # e.g., 3
        "description": description.strip(), # e.g., Ha Long Bay Trip
    }


# ── Database helpers ──────────────────────────────────────────────────────────

def load_db() -> dict:
    """Load the upload-tracking JSON database (creates it if missing)."""
    if UPLOAD_DB_FILE.exists():
        with open(UPLOAD_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_db(db: dict) -> None:
    """Persist the upload-tracking database to disk."""
    with open(UPLOAD_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def is_uploaded(db: dict, file_path: Path) -> bool:
    """Return True if this file has already been uploaded successfully."""
    # Use the absolute resolved path as the unique dictionary key
    key = str(file_path.resolve())
    return db.get(key, {}).get("status") == "uploaded"


def mark_uploaded(db: dict, file_path: Path, video_id: str) -> None:
    """Record a successful upload in the database."""
    key = str(file_path.resolve())
    # Save the resulting YouTube Video ID and the exact time of upload
    db[key] = {
        "status":      "uploaded",
        "video_id":    video_id,
        "filename":    file_path.name,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ── YouTube auth & client ─────────────────────────────────────────────────────

def get_authenticated_service():
    """
    Return an authenticated YouTube API service object.
    Credentials are cached in token.json so subsequent runs skip the browser.
    """
    creds = None

    # Load previously saved session tokens if they exist
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    # If there are no valid credentials available, let the user log in.
    if not creds or not creds.valid:
        # If tokens exist but are expired, automatically refresh them using the refresh token
        if creds and creds.expired and creds.refresh_token:
            print("🔄  Refreshing access token…")
            creds.refresh(Request())
        else:
            # Otherwise, initiate a full OAuth 2.0 flow
            if not CLIENT_SECRETS.exists():
                sys.exit(
                    f"❌  client_secrets.json not found at {CLIENT_SECRETS}\n"
                    "   See SETUP_GUIDE.md for instructions."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS), SCOPES
            )
            # Opens a browser tab for Google login; redirect back to localhost
            # port=0 tells the OS to assign an available port dynamically
            creds = flow.run_local_server(port=0)

        # Save the new/refreshed tokens for the next run
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("✅  Credentials saved to token.json")

    # Build and return the Google API client
    return build(YOUTUBE_API_NAME, YOUTUBE_API_VER, credentials=creds)


# ── Metadata builders ─────────────────────────────────────────────────────────

def build_title(folder_info: dict, part_num: int) -> str:
    """
    Construct a clean YouTube title from folder metadata + part number.
    Example: "Learning At Canopy Studyspace (2026.01.21) - Part #3"
    """
    nice_desc = folder_info["description"].title()
    return f"{nice_desc} ({folder_info['date']}) #{part_num}"


def build_description(file_path: Path, parsed_file: dict) -> str:
    """
    Build an archival description that embeds the original filename
    and the timestamp extracted from it.
    """
    ts_raw   = parsed_file["timestamp"]      # DD-MM-YY_HH-MM-SS
    date_str = ts_raw[:8].replace("-", "/")  # Converts DD-MM-YY to DD/MM/YY
    time_str = ts_raw[9:].replace("-", ":")  # Converts HH-MM-SS to HH:MM:SS

    return (
        f"📁 Original file: {file_path.name}\n"
        f"📅 Recorded on:   {date_str} at {time_str}\n\n"
        f"Auto-uploaded by DJI Footage Manager.\n"
        f"Privacy: Private — do not publish."
    )


# ── Resumable upload with progress bar ───────────────────────────────────────

def upload_video(
    youtube,
    file_path: Path,
    title: str,
    description: str,
) -> str:
    """
    Upload a single video to YouTube (private) with a live tqdm progress bar.
    Returns the YouTube video ID on success.
    Raises HttpError on unrecoverable API errors.
    """
    file_size = file_path.stat().st_size

    # Prepare the YouTube video resource metadata
    body = {
        "snippet": {
            "title":       title[:100],   # YouTube enforces a strict 100-character limit on titles
            "description": description,
            "categoryId":  "22",          # Category 22 corresponds to "People & Blogs"
        },
        "status": {
            "privacyStatus":           "private",   # ← ALWAYS private to prevent accidental public release
            "selfDeclaredMadeForKids": False,       # Required COPPA compliance
        },
    }

    # Prepare the local file stream for upload
    media = MediaFileUpload(
        str(file_path),
        mimetype="video/*",
        chunksize=CHUNK_SIZE,
        resumable=True,         # Enables chunked upload to safely handle large files and network drops
    )

    # Initialize the API insert request (this doesn't execute the upload yet)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response    = None
    retry_count = 0
    bytes_sent  = 0

    # Initialize the visual progress bar (tqdm)
    with tqdm(
        total=file_size,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc=f"  ↑ {file_path.name[:48]}",
        leave=True,
        colour="green",
    ) as pbar:
        while response is None:
            try:
                # Upload the next CHUNK_SIZE of bytes
                status, response = request.next_chunk()
                if status:
                    new_bytes = int(status.resumable_progress)
                    pbar.update(new_bytes - bytes_sent)
                    bytes_sent = new_bytes

            except HttpError as err:
                # 500-level errors are typically server-side issues
                if err.resp.status in (500, 502, 503, 504):
                    retry_count += 1
                    if retry_count > MAX_RETRIES:
                        raise # Give up after maximum allowed retries
                    
                    # Exponential back-off: pause longer after each consecutive failure
                    sleep_time = RETRY_SLEEP_BASE ** retry_count
                    print(
                        f"\n   ⚠️  Server error {err.resp.status}. "
                        f"Retrying in {sleep_time}s… ({retry_count}/{MAX_RETRIES})"
                    )
                    time.sleep(sleep_time)
                else:
                    # Non-retryable errors bubble up immediately
                    raise   

        # Advance bar to 100% after the final chunk
        pbar.update(file_size - bytes_sent)

    return response["id"]


# ── File discovery ────────────────────────────────────────────────────────────

def discover_video_files(root: Path) -> list[tuple[Path, Path]]:
    """
    Walk *root* recursively and collect every file whose name contains [VID].
    Returns a sorted list of (video_file_path, parent_folder_path) tuples.
    """
    results =[]
    # rglob("*") recursively finds all files and directories inside the root path
    for file_path in sorted(root.rglob("*")):
        # Skip folders named "Temp" (case-insensitive match)
        # If any parent folder (not just immediate) in the path is "Temp" (case-insensitive), skip
        if any(part.lower() == "temp" for part in file_path.parts):
            continue
        # Only process recognized video formats
        if file_path.suffix.lower() not in (".mp4", ".mov"):
            continue
        # Enforce the strict naming tag
        if "[VID]" not in file_path.name:
            continue
        # Enforce directory structure: Skip files placed directly in the root path
        if file_path.parent == root:
            continue
        
        results.append((file_path, file_path.parent))
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Dry run selection (CLI-friendly) ───────────────────────────────────
    #
    # Priority:
    #   1. Command-line flag (for automation: cron/launchd/etc.)
    #   2. Interactive prompt (for manual runs)
    #
    # Flags:
    #   --no-dry-run / --live / -L  → real uploads, no prompt
    #   --dry-run / -d              → dry run, no prompt
    DRY_RUN = True  # default to dry run if nothing specified

    args = [a.lower() for a in sys.argv[1:]]
    if any(a in ("--no-dry-run", "--live", "-l", "-L") for a in args):
        DRY_RUN = False
    elif any(a in ("--dry-run", "-d") for a in args):
        DRY_RUN = True
    else:
        # No flags given → fall back to interactive selection
        input_for_dry_run = input(
            "[YOUTUBE UPLOADER] Dry run (theoretical run): y/n\n"
        ).lower()
        DRY_RUN = False if input_for_dry_run == "n" else True

    if DRY_RUN:
        print("\n🚨 DRY RUN MODE ENABLED: Videos will not be uploaded, databases will not be changed.\n")

    # ── Validate configuration ─────────────────────────────────────────────
    if not FOOTAGE_FOLDER:
        sys.exit("❌  DJI_FOOTAGE_FOLDER_PATH is not set in your .env file.")

    root = Path(FOOTAGE_FOLDER)
    if not root.is_dir():
        sys.exit(f"❌  Footage folder not found: {root}")

    # ── Authenticate with YouTube ──────────────────────────────────────────
    if not DRY_RUN:
        print("🔐  Authenticating with YouTube…")
        youtube = get_authenticated_service()
        print("✅  Authenticated.\n")
    else:
        print("🔐  [DRY RUN] Skipping YouTube authentication.\n")
        youtube = None

    # ── Discover[VID] files ───────────────────────────────────────────────
    print(f"🔍  Scanning: {root}")
    video_pairs = discover_video_files(root)
    print(f"   Found {len(video_pairs)} [VID] file(s) total.\n")

    if not video_pairs:
        print("Nothing to do. Exiting.")
        return

    # ── Load upload state database ─────────────────────────────────────────
    db       = load_db()
    skipped  = 0
    uploaded = 0
    failed   = 0

    # ── Process each file ──────────────────────────────────────────────────
    for idx, (file_path, folder_path) in enumerate(video_pairs, start=1):

        print(f"[{idx}/{len(video_pairs)}] {file_path.name}")

        # Check local DB to skip files that were already uploaded in a previous run
        if is_uploaded(db, file_path):
            print("   ⏭️   Already uploaded — skipping.\n")
            skipped += 1
            continue

        # Parse the filename against our expected regex pattern
        parsed_file = parse_video_filename(file_path.name)
        if not parsed_file:
            print("   ⚠️   Filename doesn't match [VID] pattern — skipping.\n")
            skipped += 1
            continue

        # Parse the parent folder name for contextual metadata (used in YouTube title)
        folder_info = parse_folder_name(folder_path.name)
        if not folder_info:
            # Graceful fallback: If folder doesn't match the regex, use its raw name
            print("   ℹ️   Non-standard folder name; using it verbatim.")
            folder_info = {
                "date":        "unknown",
                "num":         0,
                "description": folder_path.name,
            }

        # Build final metadata payloads to send to YouTube
        title       = build_title(folder_info, parsed_file["part_num"])
        description = build_description(file_path, parsed_file)
        print(f"   📝  Title: {title}")

        # ── Upload ──────────────────────────────────────────────────────────
        try:
            if DRY_RUN:
                # Simulate the upload behavior
                print(f"   [DRY RUN] Would upload payload: Private | Category 22")
                print("   [DRY RUN] Upload skipped.\n")
                uploaded += 1
            else:
                # Perform the chunked upload
                video_id = upload_video(youtube, file_path, title, description)
                
                # Record success locally so we never upload this specific file again
                mark_uploaded(db, file_path, video_id)
                save_db(db)   # persist immediately after each success
                
                print(f"   ✅  Uploaded → https://youtu.be/{video_id}\n")
                uploaded += 1

        except HttpError as err:
            # ── Handle YouTube specific HTTP errors ────────────────────────
            
            # Status 400 with reason=uploadLimitExceeded means the channel's
            # upload cap has been reached for now. Stop immediately so we
            # don't waste time attempting further uploads in this run.
            if err.resp.status == 400:
                try:
                    error_body = json.loads(err.content.decode())
                    reason = (
                        error_body.get("error", {})
                                  .get("errors", [{}])[0]
                                  .get("reason", "")
                    )
                except Exception:
                    reason = ""

                if reason == "uploadLimitExceeded":
                    if not DRY_RUN:
                        save_db(db)
                    print(
                        "\n🚫  YouTube upload limit for this account has been reached.\n"
                        f"   Progress saved  → {uploaded} uploaded, "
                        f"{skipped} skipped, {failed} failed.\n"
                        "   Try again later when YouTube resets your upload limit.\n"
                    )
                    sys.exit(0)

            # Status 403 often means we hit YouTube's strict daily API quota limit
            if err.resp.status == 403:
                try:
                    error_body = json.loads(err.content.decode())
                    reason = (
                        error_body.get("error", {})
                                  .get("errors", [{}])[0]
                                  .get("reason", "")
                    )
                except Exception:
                    reason = ""

                # If quota is explicitly exceeded, halt the script but save progress
                if "quotaExceeded" in reason or "forbidden" in str(err).lower():
                    if not DRY_RUN:
                        save_db(db)
                    print(
                        "\n🚫  YouTube API daily quota exceeded.\n"
                        f"   Progress saved  → {uploaded} uploaded, "
                        f"{skipped} skipped, {failed} failed.\n"
                        "   Run the script again tomorrow to resume.\n"
                    )
                    sys.exit(0)

            print(f"   ❌  Upload failed (HTTP {err.resp.status}): {err}\n")
            failed += 1

        except Exception as exc:
            # Catch-all for unexpected crashes (e.g., file unreadable, disk detached)
            print(f"   ❌  Unexpected error: {exc}\n")
            failed += 1
            if not DRY_RUN:
                save_db(db)   # always persist on any unexpected failure to lock in prior successes

    # ── Final summary ──────────────────────────────────────────────────────
    print("=" * 60)
    dry_run_text = "(DRY RUN) " if DRY_RUN else ""
    print(f"✅  {dry_run_text}Uploaded : {uploaded}")
    print(f"⏭️   Skipped  : {skipped}")
    print(f"❌  Failed   : {failed}")
    if not DRY_RUN:
        print(f"📋  DB file  : {UPLOAD_DB_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()