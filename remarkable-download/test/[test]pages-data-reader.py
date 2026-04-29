import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _unwrap(value: Any) -> Any:
    """
    Extract `.value` if present (reMarkable CRDT wrapper),
    otherwise return as-is.
    """
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _safe_int(x: Any) -> Optional[int]:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def extract_pages_info(file_path: str) -> Dict[str, Any]:
    """
    Extract structured page information from a reMarkable content JSON file.
    """

    path = Path(file_path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    cpages = data.get("cPages", {})
    raw_pages = cpages.get("pages", [])

    pages: List[Dict[str, Any]] = []

    for i, p in enumerate(raw_pages):
        page = {
            "page_number": i,
            "id": p.get("id"),

            # actual ordering / CRDT index
            "idx": _unwrap(p.get("idx")),

            # redirect index (original pdf page order)
            "redir": _unwrap(p.get("redir")),

            # template (usually "Blank")
            "template": _unwrap(p.get("template")),

            # timestamps
            "modified": _safe_int(p.get("modifed")),
            "scroll_time": _unwrap(p.get("scrollTime")),

            # viewport info (useful for rendering position)
            "vertical_scroll": _safe_float(_unwrap(p.get("verticalScroll"))),
        }

        pages.append(page)

    # Sort pages by logical order (redir if exists, else fallback)
    pages_sorted = sorted(
        pages,
        key=lambda x: (x["redir"] if x["redir"] is not None else x["page_number"])
    )

    result = {
        "page_count": data.get("pageCount"),
        "orientation": data.get("orientation"),
        "last_opened": _unwrap(cpages.get("lastOpened")),
        "pages": pages_sorted,
    }

    return result

from pathlib import Path

graph_of_agent_suffix = "graph_of_agent_RM/c08b42a6-5be9-4517-9d63-38ae279538c2.content"
nhap_suffix = "nhap_rm/ac6c6386-7180-4d1e-aa5a-409c47135a3d.content"

# Use project-relative path (no hardcoded home path)
BASE_DIR = Path(__file__).resolve().parent.parent / "remarkable-download"
dot_content_path = BASE_DIR / nhap_suffix
dot_content_path = str(dot_content_path)

info = extract_pages_info(dot_content_path)

print("page_number" + " | " +  "redir: real number (original pdf)" +" | "+ "uuid")
for p in info["pages"]:
    print(p["page_number"],"|",p["redir"],"|",p["id"])


print(info["orientation"])
for p in info["pages"]:
    print(p)