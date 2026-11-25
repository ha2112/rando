"""Interactive manga builder: search → pick chapters → EPUB for Kindle (KOReader) / PDF for reMarkable 2.

Run:  python manga_builder.py
"""

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from builder import DEVICES, fit_page, write_epub, write_pdf
from sources import SOURCES, fmt_number

HERE = Path(__file__).resolve().parent
DOWNLOAD_DIR = HERE / "downloads"  # raw images, kept so re-runs resume instead of re-downloading
OUTPUT_DIR = HERE / "output"  # finished EPUB / PDF files
WORKERS = 6  # parallel image downloads per chapter
RETRIES = 3
CHUNK_SIZE = 50  # chapters per output file


def safe_name(s):
    """Make a string safe to use as a file or folder name."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", s).strip(" .")[:120] or "untitled"


def parse_selection(text, chapters):
    """'all', '5', '1-10', '5,7,9-12', '10.5' -> matching chapters (by chapter number)."""
    text = text.strip().lower()
    if text == "all":
        return list(chapters)
    ranges = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?", part)
        if not m:
            raise ValueError(f"can't read '{part}' (use e.g. 1-10, 5, 7.5, all)")
        lo = float(m.group(1))
        hi = float(m.group(2)) if m.group(2) else lo
        ranges.append((min(lo, hi), max(lo, hi)))
    return [c for c in chapters if any(lo <= c.number <= hi for lo, hi in ranges)]


def parse_quality(text):
    """'cap1448-q70' -> (1448, 70); 'uncap-100' -> (None, 100). The 'q' is optional."""
    m = re.fullmatch(r"(?:cap(\d+)|uncap)-q?(\d+)", text.strip().lower())
    if not m:
        raise ValueError(f"can't read '{text}' (use e.g. cap1448-q70 or uncap-100)")
    cap = int(m.group(1)) if m.group(1) else None
    quality = int(m.group(2))
    if cap == 0:
        raise ValueError("cap must be more than 0")
    if not 1 <= quality <= 100:
        raise ValueError("quality must be 1-100")
    return cap, quality


def quality_text(device):
    """The device's current setting written the way parse_quality reads it."""
    return f"{'cap' + str(device.cap) if device.cap else 'uncap'}-q{device.quality}"


def is_valid_image(path):
    """True if the file exists and Pillow can verify it (catches truncated downloads)."""
    try:
        if path.stat().st_size == 0:
            return False
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def download_chapter(src, series, chapter):
    """Download one chapter's pages; already-valid files are skipped. Returns paths of good pages."""
    # the page list itself can hit a transient server error (seen with MangaDex), so retry it too
    for attempt in range(RETRIES):
        try:
            urls = src.pages(chapter)
            break
        except requests.RequestException:
            if attempt == RETRIES - 1:
                raise
            time.sleep(2 ** attempt)
    folder = DOWNLOAD_DIR / safe_name(src.name) / safe_name(series.title) / f"ch-{fmt_number(chapter.number)}"
    folder.mkdir(parents=True, exist_ok=True)
    headers = {"Referer": chapter.referer} if chapter.referer else {}

    def fetch(item):
        i, url = item
        ext = os.path.splitext(urlparse(url).path)[1].lower() or ".jpg"
        path = folder / f"{i:03d}{ext}"
        if is_valid_image(path):
            return path  # resume: already downloaded
        for attempt in range(RETRIES):
            try:
                path.write_bytes(src.get(url, headers=headers).content)
                if is_valid_image(path):
                    return path
            except requests.RequestException:
                pass
            time.sleep(2 ** attempt)  # back off: 1s, 2s, 4s
        print(f"    ! page {i} failed after {RETRIES} tries (re-run to retry): {url}")
        return None

    with ThreadPoolExecutor(WORKERS) as pool:
        paths = list(pool.map(fetch, enumerate(urls, 1)))
    return [p for p in paths if p]


def build(src, series, chapters, devices):
    """Build in chunks of CHUNK_SIZE chapters; each chunk's files are written as soon as it's done."""
    outputs = []
    for start in range(0, len(chapters), CHUNK_SIZE):
        outputs += build_chunk(src, series, chapters[start:start + CHUNK_SIZE], devices,
                               start, len(chapters))
    return outputs


def build_chunk(src, series, chapters, devices, offset, total):
    """Download one chunk of chapters and write one file per device. Returns the output paths."""
    rendered = {d.key: [] for d in devices}  # device key -> [(chapter label, [jpeg bytes])]
    built = []  # chapters that actually made it in
    for n, ch in enumerate(chapters, offset + 1):
        print(f"[{n}/{total}] {ch.label}")
        try:
            paths = download_chapter(src, series, ch)
        except Exception as e:  # one bad chapter shouldn't sink the whole build
            print(f"    ! skipped chapter: {e}")
            continue
        if not paths:
            print("    ! no pages, skipped")
            continue
        for d in devices:
            pages = []
            for p in paths:
                with Image.open(p) as im:
                    pages += fit_page(im, d)
            rendered[d.key].append((ch.label, pages))
        built.append(ch)
        print(f"    {len(paths)} pages")

    if not built:
        print("Nothing was downloaded in this chunk, no files written.")
        return []
    # label like "Ch. 1-10" from the chapters actually built
    first, last = fmt_number(built[0].number), fmt_number(built[-1].number)
    span = f"Ch. {first}" if first == last else f"Ch. {first}-{last}"

    OUTPUT_DIR.mkdir(exist_ok=True)
    outputs = []
    for d in devices:
        out = OUTPUT_DIR / f"{safe_name(series.title)} - {span} ({d.name}).{d.fmt}"
        writer = write_epub if d.fmt == "epub" else write_pdf
        writer(out, f"{series.title} ({span})", rendered[d.key], d)
        print(f"Wrote {out}")  # report now, not after the whole build
        outputs.append(out)
    return outputs


# ---------- interactive prompts ----------

def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def choose(prompt, n, allow_zero=False):
    """Ask for a number 1..n, return a 0-based index (-1 if 0 is allowed and entered)."""
    lo = 0 if allow_zero else 1
    while True:
        a = ask(prompt)
        if a.isdigit() and lo <= int(a) <= n:
            return int(a) - 1
        print(f"  enter a number from {lo} to {n}")


def search_all(query, sources):
    """Search every source; a source that's down is reported and skipped."""
    results = []
    for src in sources:
        try:
            results += [(src, s) for s in src.search(query)]
        except requests.ConnectionError:
            print(f"  ! {src.name}: can't connect (offline, or your network/DNS blocks it)")
        except Exception as e:
            print(f"  ! {src.name} search failed: {str(e)[:150]}")
    return results


def pick_series(sources):
    while True:
        query = ask("\nSearch manga")
        if not query:
            continue
        results = search_all(query, sources)
        if not results:
            print("  no results, try another search")
            continue
        for i, (src, s) in enumerate(results, 1):
            info = f"  ({s.info})" if s.info else ""
            print(f"{i:>3}. [{src.name}] {s.title}{info}")
        print("  0. search again")
        idx = choose("Pick a series", len(results), allow_zero=True)
        if idx < 0:
            continue
        src, series = results[idx]
        try:
            chapters = src.chapters(series)
        except Exception as e:
            print(f"  ! couldn't load chapters: {e}")
            continue
        if not chapters:
            print("  no English chapters available there, pick another")
            continue
        return src, series, chapters


def pick_chapters(chapters):
    print(f"\n{len(chapters)} chapters: {chapters[0].label}  …  {chapters[-1].label}")
    while True:
        text = ask("Chapters (e.g. 1-10, 5,7,9-12, all; 'list' to show them)", "all")
        if text.lower() == "list":
            for c in chapters:
                print(f"  {c.label}")
            continue
        try:
            selected = parse_selection(text, chapters)
        except ValueError as e:
            print(f"  {e}")
            continue
        if selected:
            return selected
        print("  no chapters match that")


def pick_devices():
    options = list(DEVICES.values())
    for i, d in enumerate(options, 1):
        print(f"  {i}. {d.name} ({d.fmt.upper()})")
    print(f"  {len(options) + 1}. both")
    while True:
        a = ask("Build for", str(len(options) + 1))
        if a.isdigit() and 1 <= int(a) <= len(options):
            return [options[int(a) - 1]]
        if a == str(len(options) + 1):
            return options
        print(f"  enter a number from 1 to {len(options) + 1}")


def pick_quality(device):
    """Ask for this device's cap/quality; Enter keeps the default."""
    while True:
        text = ask(f"{device.name} quality (cap<pixels>|uncap)-q<1-100>", quality_text(device))
        try:
            cap, quality = parse_quality(text)
        except ValueError as e:
            print(f"  {e}")
            continue
        return replace(device, cap=cap, quality=quality)


def main():
    sources = [cls() for cls in SOURCES]
    try:
        src, series, chapters = pick_series(sources)
        selected = pick_chapters(chapters)
        print(f"\n{len(selected)} chapter(s) selected from {series.title} [{src.name}]")
        devices = [pick_quality(d) for d in pick_devices()]  # only asks for chosen devices
        print()
        build(src, series, selected, devices)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(1)


if __name__ == "__main__":
    main()
