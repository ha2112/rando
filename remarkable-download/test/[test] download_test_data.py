from pathlib import Path
from dataclasses import dataclass
from typing import Iterable
import subprocess

# =========================================================
# Configuration
# =========================================================

REMOTE_ROOT = "/home/root/.local/share/remarkable/xochitl"

VALID_MODES = {
    "hotspot",
    "home",
    "usb",
}


@dataclass(frozen=True)
class RemarkableDocument:
    name: str
    uuid: str


DOCUMENTS = {
    "graph_of_agent_rm": RemarkableDocument(
        name="graph_of_agent_rm",
        uuid="c08b42a6-5be9-4517-9d63-38ae279538c2",
    ),
    "nhap_rm": RemarkableDocument(
        name="nhap_rm",
        uuid="ac6c6386-7180-4d1e-aa5a-409c47135a3d",
    ),
    "how_to_paper": RemarkableDocument(
        name="how_to_paper",
        uuid="2b683e50-82bf-425d-95a2-21dd7909c84f",
    ),
    "piano_helper": RemarkableDocument(
        name="piano_helper",
        uuid="fcf5d7ba-95e1-421b-a7d8-e03f1d634897",
    ),
}


# =========================================================
# Utilities
# =========================================================

def build_remote(mode: str, path: str) -> str:
    """
    Build remote rsync path.
    """

    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode: {mode}")

    return f"remarkable-{mode}:{path}"


def rsync(
    remote: str,
    local: Path,
) -> None:
    """
    Execute rsync transfer.
    """

    cmd = [
        "rsync",
        "-avz",
        "--progress",
        remote,
        str(local),
    ]

    print(" ".join(cmd))

    subprocess.run(cmd, check=True)


def fetch_document(
    document: RemarkableDocument,
    mode: str,
) -> None:
    """
    Download all known artifacts for a reMarkable document.
    """

    destination = (
        Path(__file__).resolve().parent.parent
        / "test_data"
        / document.name
    )

    destination.mkdir(parents=True, exist_ok=True)

    base_remote = f"{REMOTE_ROOT}/{document.uuid}"

    # =====================================================
    # Main notebook directory
    # =====================================================

    rsync(
        build_remote(mode, base_remote),
        destination,
    )

    # =====================================================
    # Sidecar files
    # =====================================================

    sidecar_patterns: Iterable[str] = [
        f"{base_remote}.content",
        f"{base_remote}.metadata",
        f"{base_remote}.pagedata",
        f"{base_remote}.bookmarks",
    ]

    for item in sidecar_patterns:
        try:
            rsync(
                build_remote(mode, item),
                destination,
            )
        except subprocess.CalledProcessError:
            print(f"Skipped missing file: {item}")

    # =====================================================
    # Optional directories
    # =====================================================

    optional_dirs: Iterable[str] = [
        f"{base_remote}.thumbnails",
        f"{base_remote}.textconversion",
    ]

    for item in optional_dirs:
        try:
            rsync(
                build_remote(mode, item),
                destination,
            )
        except subprocess.CalledProcessError:
            print(f"Skipped missing directory: {item}")


# =========================================================
# Entry
# =========================================================

if __name__ == "__main__":
    fetch_document(
        document=DOCUMENTS["how_to_paper"],
        mode="hotspot",
    )