"""
youtube_uploader.py
────────────────────────────────────────────────────────────────────────────
Recursively scans a DJI footage directory, identifies video files tagged
[VID], and uploads them to YouTube as PRIVATE videos.

Upload mechanism: Selenium + Safari (bypasses YouTube Data API quota limits)
Cookie persistence: youtube_cookies.pkl (avoids repeated manual logins)

Naming conventions expected (Supports both formats below):
  Old: [DD-MM-YY_HH-MM-SS]_[VID]_description_#X.mp4
  New: [DD-MM-YY_HH-MM-SS]_description_[VID]_#X.mp4
  Folder: [YYYY.MM.DD] #N Folder Description

Dependencies (install once):
  pip install selenium python-dotenv tqdm

Safari setup (one-time, run in Terminal):
  safaridriver --enable
  (System Settings → Privacy & Security → Developer Tools → enable Terminal)
"""

import json
import os
import pickle
import re
import sys
import time
from pathlib import Path

# Third-party imports
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm

# ── Configuration ────────────────────────────────────────────────────────────

load_dotenv()

FOOTAGE_FOLDER  = os.getenv("DJI_FOOTAGE_FOLDER_PATH", "")

# Paths for cookie/session persistence and upload state tracking
COOKIE_FILE     = Path(__file__).parent / "youtube_cookies.pkl"
UPLOAD_DB_FILE  = Path(__file__).parent / "uploaded_files.json"

# Timeout constants (in seconds)
TIMEOUT_SHORT        = 30          # For routine DOM interactions
TIMEOUT_UPLOAD       = 1200        # 20 min — large file upload + processing
TIMEOUT_SAVE         = 120         # Waiting for "Saved" confirmation modal

# How long to pause between files to avoid triggering abuse detection
INTER_FILE_PAUSE     = 3           # seconds

# ── Filename / folder-name parsers ────────────────────────────────────────────

VIDEO_FILE_RE = re.compile(
    r"^\[(\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\]_(.+)_#(\d+)\.(mp4|mov)$",
    re.IGNORECASE,
)

FOLDER_RE = re.compile(
    r"^\[(\d{4}\.\d{2}\.\d{2})\]\s*#(\d+)\s+(.+)$"
)


def parse_video_filename(filename: str) -> dict | None:
    """Return parsed components of a [VID] file, or None if no match."""
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
    """Return parsed components of a dated folder, or None if no match."""
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
    """Return True if this file has already been successfully uploaded."""
    key = str(file_path.resolve())
    return db.get(key, {}).get("status") == "uploaded"


def mark_uploaded(db: dict, file_path: Path, video_id: str) -> None:
    """Record a successful upload in the database."""
    key = str(file_path.resolve())
    db[key] = {
        "status":      "uploaded",
        "video_id":    video_id,
        "filename":    file_path.name,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ── Metadata builders ─────────────────────────────────────────────────────────

def build_title(folder_info: dict, part_num: int) -> str:
    """
    Construct a clean YouTube title from folder metadata + part number.
    Example: "Learning At Canopy Studyspace (2026.01.21) #3"
    """
    nice_desc = folder_info["description"].title()
    return f"{nice_desc} ({folder_info['date']}) #{part_num}"


def build_description(file_path: Path, parsed_file: dict) -> str:
    """Build an archival description embedding the original filename and timestamp."""
    ts_raw   = parsed_file["timestamp"]
    date_str = ts_raw[:8].replace("-", "/")
    time_str = ts_raw[9:].replace("-", ":")
    return (
        f"📁 Original file: {file_path.name}\n"
        f"📅 Recorded on:   {date_str} at {time_str}\n\n"
        f"Auto-uploaded by DJI Footage Manager.\n"
        f"Privacy: Private — do not publish."
    )


# ── Safari / Selenium helpers ─────────────────────────────────────────────────

def create_driver() -> webdriver.Safari:
    """
    Instantiate a visible Safari WebDriver session.
    safaridriver must already be enabled on this Mac
    (run `safaridriver --enable` once in Terminal).
    """
    options = webdriver.SafariOptions()
    # Safari does not support headless mode — window is always visible, which
    # is intentional so you can monitor the upload and intervene if needed.
    driver = webdriver.Safari(options=options)
    driver.set_page_load_timeout(60)
    return driver


def wait(driver, timeout: int = TIMEOUT_SHORT) -> WebDriverWait:
    """Return a pre-configured WebDriverWait for the given driver and timeout."""
    return WebDriverWait(
        driver,
        timeout,
        poll_frequency=1,
        ignored_exceptions=(StaleElementReferenceException,),
    )


# ── Cookie-based session management ──────────────────────────────────────────

def _cookies_appear_valid(driver: webdriver.Safari) -> bool:
    """
    After injecting cookies and refreshing, check if we landed on a page that
    suggests we are authenticated (i.e. NOT on accounts.google.com login page).
    """
    current = driver.current_url
    # If Google pushed us to the sign-in page the cookies are stale/invalid
    return "accounts.google.com" not in current


def load_or_create_session(driver: webdriver.Safari) -> None:
       """
       Ensure the Safari session is authenticated to YouTube.

       New strategy:
       - Never try to log in to Google inside WebDriver (Google blocks that as "not secure").
       - Instead, rely on an existing Safari login session and snapshot those cookies.
       """

       # Open YouTube using the regular Safari profile
       driver.get("https://www.youtube.com")
       time.sleep(3)

       # If we already have a cookie file, try restoring from it first
       if COOKIE_FILE.exists():
           print("🍪  Found existing cookie file — attempting to restore session…")
           try:
               with open(COOKIE_FILE, "rb") as f:
                   cookies = pickle.load(f)

               for cookie in cookies:
                   if "expiry" in cookie:
                       cookie["expiry"] = int(cookie["expiry"])
                   if cookie.get("sameSite") not in ("Strict", "Lax", "None"):
                       cookie.pop("sameSite", None)
                   try:
                       driver.add_cookie(cookie)
                   except WebDriverException:
                       pass

               driver.refresh()
               time.sleep(3)

               if _cookies_appear_valid(driver):
                   print("✅  Session restored from cookies — no login required.\n")
                   return
               else:
                   print("⚠️   Saved cookies appear expired. Will try Safari's current session.\n")
           except Exception as exc:
               print(f"⚠️   Could not load cookie file ({exc}). Will try Safari's current session.\n")

       # At this point, either we had no cookie file or it failed.
       # Do NOT go to accounts.google.com; instead, require that the user already
       # be logged into YouTube in normal Safari.
       if "accounts.google.com" in driver.current_url:
           sys.exit(
               "❌  You are not logged into YouTube in Safari.\n"
               "    1. Close this script.\n"
               "    2. Open Safari manually, go to https://www.youtube.com and sign in.\n"
               "    3. Then re-run this script."
           )

       # We are on youtube.com and Safari's normal profile is already authenticated.
       # Capture those cookies for future runs.
       print("✅  Detected existing Safari YouTube session — saving cookies for future runs…")
       cookies = driver.get_cookies()
       for cookie in cookies:
           if "expiry" in cookie:
               cookie["expiry"] = int(cookie["expiry"])
           if cookie.get("sameSite") not in ("Strict", "Lax", "None"):
               cookie.pop("sameSite", None)

       with open(COOKIE_FILE, "wb") as f:
           pickle.dump(cookies, f)

       print("✅  Cookies saved to youtube_cookies.pkl — future runs will skip manual login.\n")

# ── YouTube Studio upload flow ────────────────────────────────────────────────

# XPath constants — isolated here so they're easy to update if YouTube changes its DOM
_XPATHS = {
    # ── Studio home ───────────────────────────────────────────────────────────
    "create_button":     (
        "//ytcp-button[@id='create-icon'] | "
        "//button[@aria-label='Create'] | "
        "//yt-icon-button[@id='create-icon']"
    ),
    "upload_videos_item": (
        "//tp-yt-paper-item[.//yt-formatted-string[contains(text(),'Upload videos')]] | "
        "//tp-yt-paper-item[contains(normalize-space(),'Upload videos')]"
    ),

    # ── Upload dialog — step 1: file + details ────────────────────────────────
    # The <input type="file"> is hidden but still in the DOM; send_keys injects the path
    "file_input":        "//input[@type='file']",
    # Title contenteditable div inside the title textarea component
    "title_field":       (
        "//ytcp-social-suggestions-textbox[@id='title-textarea']"
        "//div[@contenteditable='true']"
    ),
    # Description contenteditable div
    "description_field": (
        "//ytcp-social-suggestions-textbox[@id='description-textarea']"
        "//div[@contenteditable='true']"
    ),

    # ── Upload progress ───────────────────────────────────────────────────────
    # YouTube Studio shows one of these strings when the raw upload is done
    "upload_done": (
        "//*[contains(@class,'progress-label') and ("
        "  contains(normalize-space(),'Upload complete') or "
        "  contains(normalize-space(),'Processing') or "
        "  contains(normalize-space(),'processing will begin') or "
        "  contains(normalize-space(),'checks complete')"
        ")]"
    ),

    # ── Wizard navigation ─────────────────────────────────────────────────────
    "next_button":        "//ytcp-button[@id='next-button']//button",
    "not_made_for_kids":  (
        "//tp-yt-paper-radio-button[@name='VIDEO_NOT_MADE_FOR_KIDS'] | "
        "//ytcp-video-metadata-editor//input[@value='VIDEO_NOT_MADE_FOR_KIDS']"
    ),

    # ── Visibility step ───────────────────────────────────────────────────────
    "private_radio":      (
        "//tp-yt-paper-radio-button[@name='PRIVATE'] | "
        "//ytcp-ve[@id='PRIVATE']//tp-yt-paper-radio-button"
    ),
    "done_button":        "//ytcp-button[@id='done-button']//button",

    # ── Post-save confirmation ────────────────────────────────────────────────
    # After clicking Save, Studio shows a dialog with the video URL
    "video_url_link":     (
        "//ytcp-video-info//a[contains(@href,'youtu.be')] | "
        "//a[contains(@href,'youtu.be')]"
    ),
    # Fallback: 'Uploading… Your video has been saved' banner or close button
    "close_button":       (
        "//ytcp-button[@id='close-button']//button | "
        "//ytcp-uploads-still-processing-dialog//ytcp-button[.//yt-formatted-string[contains(text(),'Close')]]//button"
    ),
}


def _find(driver: webdriver.Safari, xpath: str, timeout: int = TIMEOUT_SHORT):
    """Wait for an element to be present and return it."""
    return wait(driver, timeout).until(
        EC.presence_of_element_located((By.XPATH, xpath))
    )


def _click(driver: webdriver.Safari, xpath: str, timeout: int = TIMEOUT_SHORT) -> None:
    """Wait for an element to be clickable, scroll it into view, then click."""
    el = wait(driver, timeout).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.4)   # brief pause after scroll so the page doesn't jerk mid-click
    el.click()


def _clear_and_type(
    driver: webdriver.Safari,
    xpath: str,
    text: str,
    timeout: int = TIMEOUT_SHORT,
) -> None:
    """
    Focus a contenteditable div, select all existing text, replace it with
    *text*, then blur the field to trigger any change listeners.
    """
    el = wait(driver, timeout).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    el.click()
    time.sleep(0.3)

    # Select all + delete is the most reliable way to clear a contenteditable
    el.send_keys(Keys.COMMAND, "a")
    time.sleep(0.2)
    el.send_keys(Keys.DELETE)
    time.sleep(0.2)

    # Type the new text in chunks to avoid event-listener quirks with long strings
    CHUNK = 200
    for i in range(0, len(text), CHUNK):
        el.send_keys(text[i : i + CHUNK])
        time.sleep(0.1)

    # Blur the field
    el.send_keys(Keys.TAB)


def _extract_video_id(driver: webdriver.Safari) -> str:
    """
    Try to extract the YouTube video ID from the post-save dialog.
    Returns the ID string or "unknown" if it cannot be found.
    """
    try:
        link_el = wait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, _XPATHS["video_url_link"]))
        )
        href = link_el.get_attribute("href")   # e.g. https://youtu.be/dQw4w9WgXcQ
        return href.rstrip("/").split("/")[-1]
    except TimeoutException:
        pass

    # Fallback: parse the video ID out of the current URL if Studio redirected
    # to a video detail page (pattern: /video/{VIDEO_ID}/...)
    url = driver.current_url
    m = re.search(r"/video/([A-Za-z0-9_-]{8,})", url)
    if m:
        return m.group(1)

    return "unknown"


def upload_video(
    driver: webdriver.Safari,
    file_path: Path,
    title: str,
    description: str,
) -> str:
    """
    Upload a single video to YouTube Studio as a PRIVATE video via Selenium.

    Steps
    ─────
    1.  Navigate to YouTube Studio.
    2.  Open the Create → Upload videos dialog.
    3.  Inject the local file path into the hidden <input type="file">.
    4.  Fill in title and description.
    5.  Wait (up to 20 min) for the upload + initial processing to complete.
    6.  Click through the three "Next" wizard steps.
    7.  Select "Private" visibility.
    8.  Click "Save" and wait for the confirmation dialog.
    9.  Extract and return the YouTube video ID.

    Returns the video ID string (may be "unknown" if extraction fails).
    Raises WebDriverException / TimeoutException on unrecoverable failures.
    """

    # ── 1. Navigate to Studio ─────────────────────────────────────────────
    print("   🌐  Navigating to YouTube Studio…")
    driver.get("https://studio.youtube.com")
    time.sleep(3)

    # If Studio redirected us back to a login wall, our cookies have expired
    if "accounts.google.com" in driver.current_url:
        raise WebDriverException(
            "Session expired — cookies are no longer valid. "
            "Delete youtube_cookies.pkl and re-run to log in again."
        )

    # ── 2. Open the upload dialog ─────────────────────────────────────────
    print("   🖱️   Opening upload dialog…")
    _click(driver, _XPATHS["create_button"])
    time.sleep(0.8)
    _click(driver, _XPATHS["upload_videos_item"])
    time.sleep(1.5)

    # ── 3. Inject the file path ───────────────────────────────────────────
    print(f"   📂  Injecting file path: {file_path.name}")
    file_input = _find(driver, _XPATHS["file_input"])
    # The input is hidden; send_keys works without needing to make it visible
    file_input.send_keys(str(file_path.resolve()))
    print("   ⏫  File handed to browser — upload in progress…")

    # ── 4. Fill in title ──────────────────────────────────────────────────
    # YouTube pre-fills the title with the raw filename; wait for the title
    # field to appear (it renders after the file is accepted), then overwrite it.
    print(f"   ✏️   Setting title: {title}")
    _clear_and_type(driver, _XPATHS["title_field"], title, timeout=60)

    # ── 5. Fill in description ────────────────────────────────────────────
    print("   📝  Setting description…")
    _clear_and_type(driver, _XPATHS["description_field"], description)

    # ── 6. Select "Not made for kids" ────────────────────────────────────
    # This radio button must be selected before Next becomes enabled
    try:
        _click(driver, _XPATHS["not_made_for_kids"], timeout=20)
    except TimeoutException:
        # The field may already be pre-selected or absent on this account
        pass

    # ── 7. Wait for upload to complete (up to 20 minutes) ─────────────────
    print(
        f"   ⏳  Waiting for upload to finish (timeout: {TIMEOUT_UPLOAD // 60} min)…"
    )
    # Use a live tqdm bar driven by elapsed time so the terminal isn't silent
    with tqdm(
        total=TIMEOUT_UPLOAD,
        unit="s",
        desc="   ⏫  Upload/processing",
        bar_format="{l_bar}{bar}| {elapsed} elapsed",
        colour="cyan",
        leave=True,
    ) as pbar:
        start = time.time()
        while True:
            elapsed = int(time.time() - start)
            pbar.n = min(elapsed, TIMEOUT_UPLOAD)
            pbar.refresh()

            # Check if the progress label shows completion
            try:
                driver.find_element(By.XPATH, _XPATHS["upload_done"])
                pbar.n = TIMEOUT_UPLOAD   # jump bar to 100%
                pbar.refresh()
                break
            except NoSuchElementException:
                pass

            if elapsed >= TIMEOUT_UPLOAD:
                raise TimeoutException(
                    f"Upload did not complete within {TIMEOUT_UPLOAD // 60} minutes."
                )
            time.sleep(2)

    print("   ✅  Upload/processing complete. Proceeding through wizard…")

    # ── 8. Wizard: click "Next" three times ───────────────────────────────
    # Step 1→2: Details → Video elements
    # Step 2→3: Video elements → Checks
    # Step 3→4: Checks → Visibility
    for step_num in range(1, 4):
        print(f"   ▶️   Wizard step {step_num}/3 — clicking Next…")
        _click(driver, _XPATHS["next_button"], timeout=30)
        time.sleep(1.2)

    # ── 9. Set visibility to Private ──────────────────────────────────────
    print("   🔒  Setting visibility to Private…")
    _click(driver, _XPATHS["private_radio"], timeout=30)
    time.sleep(0.8)

    # ── 10. Save ───────────────────────────────────────────────────────────
    print("   💾  Clicking Save…")
    _click(driver, _XPATHS["done_button"], timeout=30)

    # Wait for the post-save confirmation dialog to appear
    # (it contains the final video link / ID)
    print(f"   ⏳  Waiting for save confirmation (timeout: {TIMEOUT_SAVE}s)…")
    try:
        wait(driver, TIMEOUT_SAVE).until(
            lambda d: (
                # Either the video link appeared in the dialog…
                len(d.find_elements(By.XPATH, _XPATHS["video_url_link"])) > 0
                # …or the close/dismiss button appeared
                or len(d.find_elements(By.XPATH, _XPATHS["close_button"])) > 0
            )
        )
    except TimeoutException:
        print("   ⚠️   Save confirmation timed out — assuming success and continuing.")

    # ── 11. Extract video ID ───────────────────────────────────────────────
    video_id = _extract_video_id(driver)

    # Close the confirmation dialog if it's still open
    try:
        close_btn = driver.find_element(By.XPATH, _XPATHS["close_button"])
        close_btn.click()
        time.sleep(1)
    except NoSuchElementException:
        pass

    return video_id


# ── File discovery ────────────────────────────────────────────────────────────

def discover_video_files(root: Path) -> list[tuple[Path, Path]]:
    """
    Walk *root* recursively and collect every file whose name contains [VID].
    Returns a sorted list of (video_file_path, parent_folder_path) tuples.
    """
    results = []
    for file_path in sorted(root.rglob("*")):
        # Skip anything inside a "Temp" folder (case-insensitive)
        if any(part.lower() == "temp" for part in file_path.parts):
            continue
        if file_path.suffix.lower() not in (".mp4", ".mov"):
            continue
        if "[VID]" not in file_path.name:
            continue
        # Files placed directly in the root (not inside a sub-folder) are skipped
        if file_path.parent == root:
            continue
        results.append((file_path, file_path.parent))
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:

    # ── Dry-run prompt ─────────────────────────────────────────────────────
    input_for_dry_run = input(
        "[YOUTUBE UPLOADER] Dry run (theoretical run): y/n\n"
    ).strip().lower()
    DRY_RUN = input_for_dry_run != "n"

    if DRY_RUN:
        print("\n🚨 DRY RUN MODE ENABLED: Videos will not be uploaded, databases will not be changed.\n")

    # ── Validate configuration ─────────────────────────────────────────────
    if not FOOTAGE_FOLDER:
        sys.exit("❌  DJI_FOOTAGE_FOLDER_PATH is not set in your .env file.")

    root = Path(FOOTAGE_FOLDER)
    if not root.is_dir():
        sys.exit(f"❌  Footage folder not found: {root}")

    # ── Discover [VID] files ───────────────────────────────────────────────
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

    # ── Start Safari / authenticate ────────────────────────────────────────
    driver = None
    if not DRY_RUN:
        print("🦁  Launching Safari via safaridriver…")
        driver = create_driver()
        try:
            load_or_create_session(driver)
        except Exception as exc:
            driver.quit()
            sys.exit(f"❌  Authentication failed: {exc}")
    else:
        print("🦁  [DRY RUN] Skipping Safari launch.\n")

    # ── Process each file ──────────────────────────────────────────────────
    try:
        for idx, (file_path, folder_path) in enumerate(video_pairs, start=1):

            print(f"[{idx}/{len(video_pairs)}] {file_path.name}")

            # Skip already-uploaded files
            if is_uploaded(db, file_path):
                print("   ⏭️   Already uploaded — skipping.\n")
                skipped += 1
                continue

            # Parse filename
            parsed_file = parse_video_filename(file_path.name)
            if not parsed_file:
                print("   ⚠️   Filename doesn't match [VID] pattern — skipping.\n")
                skipped += 1
                continue

            # Parse folder name (with graceful fallback)
            folder_info = parse_folder_name(folder_path.name)
            if not folder_info:
                print("   ℹ️   Non-standard folder name; using it verbatim.")
                folder_info = {
                    "date":        "unknown",
                    "num":         0,
                    "description": folder_path.name,
                }

            # Build metadata
            title       = build_title(folder_info, parsed_file["part_num"])
            description = build_description(file_path, parsed_file)
            print(f"   📝  Title: {title}")

            # ── Upload ────────────────────────────────────────────────────
            try:
                if DRY_RUN:
                    print("   [DRY RUN] Would upload: Private | browser-based (no API quota)")
                    print("   [DRY RUN] Upload skipped.\n")
                    uploaded += 1
                else:
                    video_id = upload_video(driver, file_path, title, description)

                    mark_uploaded(db, file_path, video_id)
                    save_db(db)

                    if video_id != "unknown":
                        print(f"   ✅  Uploaded → https://youtu.be/{video_id}\n")
                    else:
                        print("   ✅  Uploaded (video ID could not be extracted — check Studio)\n")
                    uploaded += 1

                    # Polite pause between uploads
                    if idx < len(video_pairs):
                        time.sleep(INTER_FILE_PAUSE)

            except TimeoutException as exc:
                print(f"   ❌  Timed out during upload: {exc}\n")
                failed += 1
                if not DRY_RUN:
                    save_db(db)

            except WebDriverException as exc:
                print(f"   ❌  Browser/WebDriver error: {exc}\n")
                failed += 1
                if not DRY_RUN:
                    save_db(db)

            except Exception as exc:
                print(f"   ❌  Unexpected error: {exc}\n")
                failed += 1
                if not DRY_RUN:
                    save_db(db)

    finally:
        # Always close the browser cleanly, even if we crash mid-run
        if driver is not None:
            driver.quit()

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