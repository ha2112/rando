"""
Build a cached index mapping UUID -> file paths + metadata from a LOCAL reMarkable data folder.

Usage:
    python build_index.py /path/to/xochitl
    python build_index.py /path/to/xochitl --cache /path/to/uuid_index.json
    python build_index.py   # defaults to CWD
"""

import json
import argparse
from pathlib import Path

CACHE_FILE_DEFAULT_NAME = Path(__file__).resolve().parent.parent / "uuid_index.json"
_BASE_DIR: Path = Path.home() / "Downloads" / "RemarkableSync" 
DEFAULT_CACHE_DIR = _BASE_DIR / ".RM_FILES"

def build_index(local_dir: Path) -> dict:
    index = {}

    metadata_files = list(local_dir.glob("*.metadata"))
    if not metadata_files:
        raise RuntimeError(f"No .metadata files found in: {local_dir}")

    for meta_path in metadata_files:
        uuid = meta_path.stem

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue  # skip malformed/unreadable metadata

        base = local_dir / uuid

        index[uuid] = {
            "uuid": uuid,
            "name": meta.get("visibleName", ""),
            "parent": meta.get("parent"),
            "pdf": str(base.with_suffix(".pdf")),
            "content": str(base.with_suffix(".content")),
            "pagedata": str(base.with_suffix(".pagedata")),
            "rm_dir": str(base),
            "thumbnails": str(local_dir / f"{uuid}.thumbnails"),
        }

    return index


def save_index(index: dict, cache_file: Path) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build UUID index from a local reMarkable xochitl folder."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=None,
        help="Path to local xochitl folder (default: current directory)",
    )
    parser.add_argument(
        "--cache",
        default=None,
        help="Output path for uuid_index.json (default: <folder>/../uuid_index.json)",
    )
    args = parser.parse_args()

    local_dir = DEFAULT_CACHE_DIR
    cache_file = (
        Path(args.cache).resolve()
        if args.cache
        else local_dir.parent / CACHE_FILE_DEFAULT_NAME
    )

    print(f"[+] Building index from: {local_dir}")

    if not local_dir.exists():
        print(f"[-] ERROR: Folder does not exist: {local_dir}")
        exit(1)

    try:
        idx = build_index(local_dir)
    except Exception as e:
        print(f"[-] ERROR: {e}")
        exit(1)

    print(f"[+] Found {len(idx)} documents")
    save_index(idx, cache_file)
    print(f"[+] Saved to {cache_file}")

    # Example query
    query = "survival"
    print(f"\n[+] Example search: contains '{query}'")
    matches = [(u, v) for u, v in idx.items() if query in v["name"].lower()]
    for u, v in matches:
        print(f"  {u} -> {v['name']}")
    if not matches:
        print("  (no matches)")