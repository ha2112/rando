"""
youtube_browser_uploader.py
────────────────────────────────────────────────────────────────────────────
Recursively scans a DJI footage directory, identifies video files tagged
[VID], and uploads them to YouTube as PRIVATE videos via browser automation
(Playwright) — no API key or OAuth client secrets required.

Two modes:

  1) Launch mode (default) — for files of any size, including > 50 MB.
     Do NOT set BROWSER_CDP_URL. The script launches your system Google Chrome
     and uses a persistent profile (browser_profile/). Log in to Google once
     in the window that opens; later runs reuse the session. Google may show
     "browser not safe" on first login; retry or use a different Google account.

  2) CDP mode — only for files ≤ 50 MB. Set BROWSER_CDP_URL=http://localhost:9222
     and start Chrome/Arc with --remote-debugging-port=9222. No file size limit
     in the browser, but Playwright cannot send files > 50 MB over CDP.

Naming conventions expected (Supports both formats below):
  Old: [DD-MM-YY_HH-MM-SS]_[VID]_description_#X.mp4
  New: [DD-MM-YY_HH-MM-SS]_description_[VID]_#X.mp4
  Folder: [YYYY.MM.DD] #N Folder Description

Dependencies (install once):
  pip install playwright python-dotenv tqdm
  playwright install chromium
"""

import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from tqdm import tqdm

# ── Configuration ─────────────────────────────────────────────────────────────

load_dotenv()

FOOTAGE_FOLDER  = os.getenv("DJI_FOOTAGE_FOLDER_PATH", "")

# Launch mode: persistent Chrome profile (no 50 MB limit). CDP mode: set URL (50 MB limit).
BROWSER_PROFILE = Path(__file__).parent / "browser_profile"
BROWSER_CDP_URL = os.getenv("BROWSER_CDP_URL", "").strip() or None

# Playwright cannot send files > 50 MB to a browser connected over CDP.
CDP_FILE_SIZE_LIMIT_BYTES = 50 * 1024 * 1024  # 50 MB

UPLOAD_DB_FILE  = Path(__file__).parent / "uploaded_files.json"

# How long (ms) to wait for YouTube UI elements before giving up
NAV_TIMEOUT       = 60_000   # page navigation
ELEM_TIMEOUT      = 30_000   # individual element
UPLOAD_BTN_TIMEOUT = 60_000  # upload button (page can load slowly)
UPLOAD_TIMEOUT    = 3_600_000  # 1 hour — for very large files

# ── Filename / foldername parsers ─────────────────────────────────────────────

VIDEO_FILE_RE = re.compile(
    r"^\[(\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\]_(.+)_#(\d+)\.(mp4|mov)$",
    re.IGNORECASE,
)

FOLDER_RE = re.compile(
    r"^\[(\d{4}\.\d{2}\.\d{2})\]\s*#(\d+)\s+(.+)$"
)


def parse_video_filename(filename: str) -> dict | None:
    m = VIDEO_FILE_RE.match(filename)
    if not m:
        return None
    timestamp, raw_description, part_num, ext = m.groups()
    clean_description = raw_description.replace("[VID]", "").strip("_")
    return {
        "timestamp":   timestamp,
        "description": clean_description,
        "part_num":    int(part_num),
        "extension":   ext.lower(),
    }


def parse_folder_name(folder_name: str) -> dict | None:
    m = FOLDER_RE.match(folder_name)
    if not m:
        return None
    date, num, description = m.groups()
    return {
        "date":        date,
        "num":         int(num),
        "description": description.strip(),
    }


# ── Database helpers ──────────────────────────────────────────────────────────

def load_db() -> dict:
    if UPLOAD_DB_FILE.exists():
        with open(UPLOAD_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_db(db: dict) -> None:
    with open(UPLOAD_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def is_uploaded(db: dict, file_path: Path) -> bool:
    key = str(file_path.resolve())
    return db.get(key, {}).get("status") == "uploaded"


def mark_uploaded(db: dict, file_path: Path, video_url: str) -> None:
    key = str(file_path.resolve())
    db[key] = {
        "status":      "uploaded",
        "video_url":   video_url,
        "filename":    file_path.name,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ── Metadata builders ─────────────────────────────────────────────────────────

def build_title(folder_info: dict, part_num: int) -> str:
    nice_desc = folder_info["description"].title()
    return f"{nice_desc} ({folder_info['date']}) #{part_num}"


def build_description(file_path: Path, parsed_file: dict) -> str:
    ts_raw   = parsed_file["timestamp"]
    date_str = ts_raw[:8].replace("-", "/")
    time_str = ts_raw[9:].replace("-", ":")
    return (
        f"📁 Original file: {file_path.name}\n"
        f"📅 Recorded on:   {date_str} at {time_str}\n\n"
        f"Auto-uploaded by DJI Footage Manager.\n"
        f"Privacy: Private — do not publish."
    )


# ── Browser upload logic ──────────────────────────────────────────────────────

def ensure_logged_in(page) -> None:
    """
    Navigate to YouTube Studio. If not logged in, prompt user to log in
    in the browser they started (then press ENTER to continue).
    """
    print("🌐  Opening YouTube Studio…")
    page.goto("https://studio.youtube.com", timeout=NAV_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)

    if "accounts.google.com" in page.url or "signin" in page.url.lower():
        print(
            "\n⚠️  Not logged in in this browser.\n"
            "   Log in to Google/YouTube in the browser window you started,\n"
            "   then press ENTER here to continue…"
        )
        input()
        page.goto("https://studio.youtube.com", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)

    print("✅  Logged in to YouTube Studio.\n")


def upload_video_browser(page, file_path: Path, title: str, description: str, dry_run: bool = False) -> str:
    """
    Upload a single video through the YouTube Studio browser UI.
    Returns the final video URL (or a placeholder in dry-run mode).

    Steps mirrored from the Studio UI:
      1. Click the Upload Videos button
      2. Feed the file path to the hidden <input type="file">
      3. Fill in title and description
      4. Set privacy to Private
      5. Publish and capture the resulting video URL
    """
    if dry_run:
        print(f"   [DRY RUN] Would upload: {file_path.name}")
        print(f"   [DRY RUN] Title: {title}")
        return "https://youtu.be/DRY_RUN_ID"

    print(f"   📂  Navigating to YouTube Studio…")
    page.goto("https://studio.youtube.com", timeout=NAV_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
    page.wait_for_timeout(2_000)  # let Studio UI finish rendering

    # ── Step 1: open the upload dialog ────────────────────────────────────
    # Try legacy selector first; Studio sometimes uses "Create" → "Upload videos"
    upload_btn = page.locator("ytcp-button#upload-btn")
    try:
        upload_btn.wait_for(state="visible", timeout=UPLOAD_BTN_TIMEOUT)
        upload_btn.click()
    except PlaywrightTimeoutError:
        print("   ℹ️   Trying Create → Upload videos…")
        create_btn = page.get_by_role("button", name=re.compile(r"Create|Upload|Tạo|Tải lên", re.I)).first
        create_btn.wait_for(state="visible", timeout=ELEM_TIMEOUT)
        create_btn.click()
        page.wait_for_timeout(800)
        # "Upload video(s)" or localized equivalent (e.g. "Tải video lên")
        upload_item = page.get_by_role("menuitem", name=re.compile(r"Upload\s*video|Tải.*video|Tải lên", re.I)).first
        upload_item.wait_for(state="visible", timeout=ELEM_TIMEOUT)
        upload_item.click()

    # ── Step 2: inject the file path into the hidden file input ───────────
    # YouTube Studio uses a hidden <input type="file"> inside the dialog.
    # set_input_files() is the Playwright-native way to bypass the OS file dialog.
    file_input = page.locator("input[type='file']")
    file_input.wait_for(state="attached", timeout=ELEM_TIMEOUT)
    file_input.set_input_files(str(file_path))
    print(f"   ⬆️   File queued for upload.")

    # ── Step 3: wait for the details panel to appear ──────────────────────
    # After selecting a file YouTube shows "Upload in progress" + the details form.
    details_panel = page.locator("ytcp-uploads-dialog")
    details_panel.wait_for(state="visible", timeout=ELEM_TIMEOUT)

    # ── Step 4: fill in title ──────────────────────────────────────────────
    title_field = page.locator(
        "ytcp-uploads-dialog ytcp-social-suggestions-textbox[label='Title (required)'] #textbox"
    )
    title_field.wait_for(state="visible", timeout=ELEM_TIMEOUT)
    title_field.triple_click()          # select all existing placeholder text
    title_field.fill(title[:100])       # YouTube title max is 100 chars

    # ── Step 5: fill in description ───────────────────────────────────────
    desc_field = page.locator(
        "ytcp-uploads-dialog ytcp-social-suggestions-textbox[label='Description'] #textbox"
    )
    desc_field.wait_for(state="visible", timeout=ELEM_TIMEOUT)
    desc_field.click()
    desc_field.fill(description)

    # ── Step 6: advance through "Audience" screen (Not made for kids) ────
    # Click "Next" three times to pass through Details → Video elements → Checks
    for step_label in ("Details → Audience", "Video elements", "Checks"):
        next_btn = page.locator("ytcp-uploads-dialog ytcp-button#next-button")
        next_btn.wait_for(state="visible", timeout=ELEM_TIMEOUT)
        next_btn.click()
        print(f"   ➡️   Passed: {step_label}")
        page.wait_for_timeout(1_500)   # brief pause to let the UI animate

    # ── Step 7: set visibility to Private ────────────────────────────────
    private_radio = page.locator("tp-yt-paper-radio-button[name='PRIVATE']")
    private_radio.wait_for(state="visible", timeout=ELEM_TIMEOUT)
    private_radio.click()
    print("   🔒  Visibility set to Private.")

    # ── Step 8: wait for upload to complete, then publish ─────────────────
    # The Publish/Save button stays disabled until the upload+processing is done.
    # We poll until it's enabled (or hit our 1-hour timeout).
    publish_btn = page.locator("ytcp-uploads-dialog ytcp-button#done-button")
    publish_btn.wait_for(state="visible", timeout=ELEM_TIMEOUT)

    print("   ⏳  Waiting for upload to finish…")
    with tqdm(desc="   Upload progress", unit="s", leave=True, colour="green") as pbar:
        deadline = time.time() + UPLOAD_TIMEOUT / 1000
        while time.time() < deadline:
            if publish_btn.is_enabled():
                break
            pbar.update(5)
            page.wait_for_timeout(5_000)
        else:
            raise TimeoutError("Upload timed out after 1 hour.")

    publish_btn.click()
    print("   📤  Publish clicked.")

    # ── Step 9: capture the resulting video URL ───────────────────────────
    # Studio shows a "Your video is now live" dialog with a shareable link.
    video_link = page.locator("ytcp-video-info a.ytcp-video-info")
    try:
        video_link.wait_for(state="visible", timeout=ELEM_TIMEOUT)
        href = video_link.get_attribute("href") or "https://studio.youtube.com"
    except PlaywrightTimeoutError:
        # Fallback: grab the URL from the current page if the link doesn't appear
        href = page.url

    # Dismiss the confirmation dialog if it's still open
    close_btn = page.locator("ytcp-uploads-still-processing-dialog ytcp-button#close-button")
    if close_btn.is_visible():
        close_btn.click()

    return href


# ── File discovery ─────────────────────────────────────────────────────────────

def discover_video_files(root: Path) -> list[tuple[Path, Path]]:
    results = []
    for file_path in sorted(root.rglob("*")):
        if any(part.lower() == "temp" for part in file_path.parts):
            continue
        if file_path.suffix.lower() not in (".mp4", ".mov"):
            continue
        if "[VID]" not in file_path.name:
            continue
        if file_path.parent == root:
            continue
        results.append((file_path, file_path.parent))
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Dry-run prompt ────────────────────────────────────────────────────
    input_for_dry_run = input("[YOUTUBE UPLOADER] Dry run (theoretical run): y/n\n").lower()
    DRY_RUN = input_for_dry_run != "n"

    if DRY_RUN:
        print("\n🚨 DRY RUN MODE ENABLED: Browser will not open, nothing will be uploaded.\n")

    # ── Validate configuration ────────────────────────────────────────────
    if not FOOTAGE_FOLDER:
        sys.exit("❌  DJI_FOOTAGE_FOLDER_PATH is not set in your .env file.")

    root = Path(FOOTAGE_FOLDER)
    if not root.is_dir():
        sys.exit(f"❌  Footage folder not found: {root}")

    # ── Discover [VID] files ──────────────────────────────────────────────
    print(f"🔍  Scanning: {root}")
    video_pairs = discover_video_files(root)
    print(f"   Found {len(video_pairs)} [VID] file(s) total.\n")

    if not video_pairs:
        print("Nothing to do. Exiting.")
        return

    # ── Load upload state database ────────────────────────────────────────
    db       = load_db()
    skipped  = 0
    uploaded = 0
    failed   = 0

    # ── Dry run: no browser, just report what would be uploaded ───────────
    if DRY_RUN:
        print("🚨 DRY RUN: no browser, listing what would be uploaded.\n")
        for idx, (file_path, folder_path) in enumerate(video_pairs, start=1):
            if is_uploaded(db, file_path):
                print(f"[{idx}/{len(video_pairs)}] {file_path.name}  ⏭️  Already uploaded — skip")
                skipped += 1
                continue
            parsed = parse_video_filename(file_path.name)
            if not parsed:
                print(f"[{idx}/{len(video_pairs)}] {file_path.name}  ⚠️  Bad filename — skip")
                skipped += 1
                continue
            folder_info = parse_folder_name(folder_path.name) or {"date": "?", "num": 0, "description": folder_path.name}
            title = build_title(folder_info, parsed["part_num"])
            print(f"[{idx}/{len(video_pairs)}] {file_path.name}  📤  Would upload: {title}")
            uploaded += 1
        print("=" * 60)
        print(f"✅  (DRY RUN) Would upload : {uploaded}")
        print(f"⏭️   Skipped  : {skipped}")
        print("=" * 60)
        return

    # ── Launch or connect to browser ─────────────────────────────────────
    use_cdp = BROWSER_CDP_URL is not None
    if not use_cdp:
        BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        if use_cdp:
            print("🔗  Connecting to your browser (BROWSER_CDP_URL)…")
            browser = pw.chromium.connect_over_cdp(BROWSER_CDP_URL)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
        else:
            print("🧭  Launching Chrome (no 50 MB limit). Log in to Google when the window opens.\n")
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE),
                channel="chrome",
                headless=False,
                accept_downloads=False,
                args=["--start-maximized"],
                no_viewport=True,
            )
            page = context.pages[0] if context.pages else context.new_page()

        ensure_logged_in(page)

        # ── Process each file ─────────────────────────────────────────────
        for idx, (file_path, folder_path) in enumerate(video_pairs, start=1):

            print(f"[{idx}/{len(video_pairs)}] {file_path.name}")

            if is_uploaded(db, file_path):
                print("   ⏭️   Already uploaded — skipping.\n")
                skipped += 1
                continue

            if use_cdp and file_path.stat().st_size > CDP_FILE_SIZE_LIMIT_BYTES:
                size_mb = file_path.stat().st_size / (1024 * 1024)
                print(f"   ❌  File is {size_mb:.1f} MB. Over 50 MB not supported in CDP mode.\n")
                print("   ℹ️  Unset BROWSER_CDP_URL in .env to use launch mode (no size limit).\n")
                failed += 1
                continue

            parsed_file = parse_video_filename(file_path.name)
            if not parsed_file:
                print("   ⚠️   Filename doesn't match [VID] pattern — skipping.\n")
                skipped += 1
                continue

            folder_info = parse_folder_name(folder_path.name)
            if not folder_info:
                print("   ℹ️   Non-standard folder name; using it verbatim.")
                folder_info = {
                    "date":        "unknown",
                    "num":         0,
                    "description": folder_path.name,
                }

            title       = build_title(folder_info, parsed_file["part_num"])
            description = build_description(file_path, parsed_file)
            print(f"   📝  Title: {title}")

            try:
                video_url = upload_video_browser(page, file_path, title, description, dry_run=DRY_RUN)

                if not DRY_RUN:
                    mark_uploaded(db, file_path, video_url)
                    save_db(db)
                    print(f"   ✅  Uploaded → {video_url}\n")
                else:
                    print(f"   [DRY RUN] Upload complete.\n")

                uploaded += 1

            except PlaywrightTimeoutError as err:
                print(f"   ❌  Browser timeout: {err}\n")
                failed += 1
                if not DRY_RUN:
                    save_db(db)

            except Exception as exc:
                print(f"   ❌  Unexpected error: {exc}\n")
                failed += 1
                save_db(db)

        if not use_cdp:
            context.close()

    # ── Final summary ─────────────────────────────────────────────────────
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