import os
import re
import requests
from PIL import Image
from bs4 import BeautifulSoup

BASE_URL = "https://www.twinexorcists.com"
CHAPTER_URL_TEMPLATE = BASE_URL + "/manga/twin-star-exorcists-chapter-{chapter_number}/"
BASE_FOLDER = "twin_star_exorcist"

headers = {"User-Agent": "Mozilla/5.0"}


def natural_key(s):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


def find_corrupted_images(base_folder):
    corrupted = []  # list of (chapter_number, image_index, file_path)

    subfolders = sorted(
        [d for d in os.listdir(base_folder)
         if os.path.isdir(os.path.join(base_folder, d)) and d.startswith("chapter-")],
        key=natural_key
    )

    print(f"Scanning {len(subfolders)} chapters...\n")

    for subfolder in subfolders:
        chapter_num = int(re.search(r'\d+', subfolder).group())
        folder_path = os.path.join(base_folder, subfolder)

        image_files = sorted(
            [f for f in os.listdir(folder_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))],
            key=natural_key
        )

        for fname in image_files:
            fpath = os.path.join(folder_path, fname)
            try:
                with Image.open(fpath) as im:
                    im.verify()  # verify integrity without fully decoding
            except Exception:
                img_index = int(os.path.splitext(fname)[0])
                corrupted.append((chapter_num, img_index, fpath))
                print(f"  [CORRUPT] {fpath}")

    return corrupted


def get_image_urls_for_chapter(chapter_number):
    url = CHAPTER_URL_TEMPLATE.format(chapter_number=chapter_number)
    res = requests.get(url, headers=headers, timeout=15)
    soup = BeautifulSoup(res.text, "html.parser")

    urls = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and ("jpg" in src or "png" in src):
            urls.append(src)

    return urls


def redownload_image(url, path):
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    with open(path, "wb") as f:
        f.write(response.content)
    # Verify the newly downloaded file is valid
    with Image.open(path) as im:
        im.verify()


def main():
    print("=" * 60)
    print("  Corrupted Image Scanner & Redownloader")
    print("=" * 60 + "\n")

    corrupted = find_corrupted_images(BASE_FOLDER)

    if not corrupted:
        print("\nNo corrupted images found. All good!")
        return

    print(f"\nFound {len(corrupted)} corrupted image(s):\n")
    for ch, idx, path in corrupted:
        print(f"  Chapter {ch:>3}  |  image index {idx:>3}  |  {path}")

    print(f"\nProceed to redownload all {len(corrupted)} corrupted image(s)? [y/N] ", end="")
    answer = input().strip().lower()

    if answer != "y":
        print("Aborted. No files were changed.")
        return

    # Group by chapter to avoid fetching the same chapter page multiple times
    by_chapter = {}
    for ch, idx, path in corrupted:
        by_chapter.setdefault(ch, []).append((idx, path))

    success, failed = 0, []

    for chapter_num in sorted(by_chapter.keys()):
        entries = by_chapter[chapter_num]
        print(f"\nFetching URL list for chapter {chapter_num}...")
        try:
            urls = get_image_urls_for_chapter(chapter_num)
        except Exception as e:
            print(f"  ERROR fetching chapter {chapter_num} page: {e}")
            for _, path in entries:
                failed.append(path)
            continue

        for img_index, path in entries:
            if img_index >= len(urls):
                print(f"  ERROR: index {img_index} out of range ({len(urls)} URLs found) — {path}")
                failed.append(path)
                continue
            url = urls[img_index]
            try:
                redownload_image(url, path)
                print(f"  OK  {path}")
                success += 1
            except Exception as e:
                print(f"  FAIL  {path}  —  {e}")
                failed.append(path)

    print(f"\n{'=' * 60}")
    print(f"Done.  {success} redownloaded successfully,  {len(failed)} failed.")
    if failed:
        print("\nStill failing:")
        for p in failed:
            print(f"  {p}")


if __name__ == "__main__":
    main()