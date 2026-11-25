import requests
from bs4 import BeautifulSoup
import os
import re
from PIL import Image

BASE_URL = "https://www.twinexorcists.com"
CHAPTER_URL_TEMPLATE = BASE_URL + "/manga/twin-star-exorcists-chapter-{chapter_number}/"

headers = {
    "User-Agent": "Mozilla/5.0"
}

def natural_key(s):
    """Sort key that handles embedded numbers correctly (e.g. chapter-9 < chapter-10)."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]

def get_image_urls(url):
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")

    images = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src and ("jpg" in src or "png" in src):
            images.append(src)

    return images

def download_images(image_urls, folder="chapter"):
    os.makedirs(folder, exist_ok=True)
    paths = []

    for i, url in enumerate(image_urls):
        img_data = requests.get(url, headers=headers).content
        filename = f"{i:03}.jpg"
        path = os.path.join(folder, filename)
        with open(path, "wb") as f:
            f.write(img_data)
        paths.append(path)

    return paths

def images_to_pdf_from_folder(base_folder, output="output.pdf"):
    # Step 1: Collect images per direct subfolder only (no deeper nesting)
    subfolder_images = {}
    for root, dirs, files in os.walk(base_folder):
        if root == base_folder:
            continue
        rel = os.path.relpath(root, base_folder)
        # Skip any nested subdirectories (only process direct children)
        if os.sep in rel:
            continue
        image_files = sorted(
            [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png'))],
            key=natural_key
        )
        if image_files:
            subfolder_images[rel] = [os.path.join(root, f) for f in image_files]

    if not subfolder_images:
        print("No images found in any subfolder of", base_folder)
        return

    # Step 2: Sort subfolders with natural sort so chapter-9 comes before chapter-10
    sorted_subs = sorted(subfolder_images.keys(), key=natural_key)

    print("Subfolders included in PDF (in order):")
    for sub in sorted_subs:
        print(f"  - {sub}  ({len(subfolder_images[sub])} images)")

    # Step 3: Flatten image paths in correct order
    image_paths = [p for sub in sorted_subs for p in subfolder_images[sub]]
    print(f"\nTotal images to process: {len(image_paths)}")

    # Step 4: Load and convert images
    imgs = []
    for p in image_paths:
        try:
            with Image.open(p) as im:
                imgs.append(im.convert("RGB"))
        except OSError as e:
            print(f"Skipping invalid image: {p}\n  {e}")

    if not imgs:
        raise ValueError("No valid images to write to PDF.")

    imgs[0].save(output, save_all=True, append_images=imgs[1:])
    print(f"\nWrote PDF: {output}")

if __name__ == "__main__":
    base_folder = "twin_star_exorcist"
    os.makedirs(base_folder, exist_ok=True)
    progress_file = os.path.join(base_folder, "chapter_progress.txt")
    all_image_paths = []
    output_pdf_path = os.path.join(base_folder, "twin-star-exorcists-full.pdf")
    images_to_pdf_from_folder(base_folder, output=output_pdf_path)