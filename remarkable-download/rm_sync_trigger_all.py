#!/Users/academicweapon/Documents/CodingTypeShii/Repos/rando/remarkable-download/.remarkable-download/bin/python3

"""
Trigger sync for every document on the reMarkable tablet.

Connects over SSH, lists all .metadata files in the xochitl directory,
and updates each document's metadata to flag it as needing a cloud sync:

    - lastModified = now
    - lastOpened   = now - 12s
    - modified     = True
    - synced       = False

Usage:
    python rm_sync_trigger_all.py
"""

import json
import re
import subprocess
import time
from pathlib import Path

REMOTE_DIR = "/home/root/.local/share/remarkable/xochitl"
DEFAULT_HOSTS = ["remarkable-usb", "remarkable-home", "remarkable-hotspot"]


# ---------------------------------------------------------------------------
# SSH helpers  (same logic as the test)
# ---------------------------------------------------------------------------

def parse_ssh_config():
    """Parse ~/.ssh/config and return all non-wildcard Host entries."""
    config = Path.home() / ".ssh" / "config"
    if not config.exists():
        return []

    hosts = set()
    with open(config, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*Host\s+(.+)", line)
            if m:
                for host in m.group(1).split():
                    if host != "*" and "*" not in host and "?" not in host:
                        hosts.add(host)
    return list(hosts)


def find_working_host():
    """Probe known hosts in order; return the first reachable one."""
    hosts = sorted(
        set(DEFAULT_HOSTS + parse_ssh_config()),
        key=lambda x: (x not in DEFAULT_HOSTS, x),
    )

    for host in hosts:
        try:
            result = subprocess.run(
                ["ssh", host, "echo ok"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                print(f"[+] Connected via: {host}")
                return host
        except Exception:
            pass

    raise RuntimeError("Could not connect to reMarkable")


def ssh_read(host, remote_path):
    """Read a remote file over SSH."""
    result = subprocess.run(
        ["ssh", host, f"cat '{remote_path}'"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def ssh_write(host, remote_path, content):
    """Write content to a remote file over SSH."""
    cmd = f"cat > '{remote_path}' << 'EOF'\n{content}\nEOF"
    result = subprocess.run(
        ["ssh", host, cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def ssh_list_uuids(host):
    """List all document UUIDs on the device by scanning remote .metadata files.

    Returns a list of UUID strings sorted alphabetically.
    """
    result = subprocess.run(
        ["ssh", host, f"ls '{REMOTE_DIR}'/*.metadata"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list remote files: {result.stderr}")

    uuids = []
    for line in result.stdout.strip().splitlines():
        path = line.strip()
        if path.endswith(".metadata"):
            uuid = path.rsplit("/", 1)[-1].replace(".metadata", "")
            uuids.append(uuid)

    return sorted(uuids)


# ---------------------------------------------------------------------------
# Sync trigger
# ---------------------------------------------------------------------------

def trigger_sync(host, uuid):
    """Update a single document's metadata to trigger cloud sync."""
    metadata_path = f"{REMOTE_DIR}/{uuid}.metadata"
    print(f"    {uuid} …", end=" ", flush=True)

    raw = ssh_read(host, metadata_path)
    data = json.loads(raw)

    now_ms = str(int(time.time() * 1000))
    open_now_ms = str(int(time.time() * 1000 - 12000))

    data["lastModified"] = now_ms
    data["lastOpened"] = open_now_ms
    data["modified"] = True
    data["synced"] = False
    data["version"] = data.get("version", 0) + 1

    updated = json.dumps(data, indent=4)
    ssh_write(host, metadata_path, updated)
    print("done")


def main():
    print("[*] Finding reachable reMarkable host…")
    host = find_working_host()

    print("[*] Discovering documents on device…")
    uuids = ssh_list_uuids(host)
    print(f"[+] Found {len(uuids)} document(s)\n")

    ok = 0
    errors = []

    for uuid in uuids:
        try:
            trigger_sync(host, uuid)
            ok += 1
        except Exception as e:
            print(f"FAILED ({e})")
            errors.append((uuid, e))

    print(f"\n[+] Done. {ok}/{len(uuids)} document(s) triggered.")
    if errors:
        print(f"[-] {len(errors)} failure(s):")
        for uuid, err in errors:
            print(f"      {uuid}  —  {err}")


if __name__ == "__main__":
    main()
