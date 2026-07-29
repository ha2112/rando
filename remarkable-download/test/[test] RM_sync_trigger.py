#!/Users/academicweapon/Documents/CodingTypeShii/Repos/rando/remarkable-download/.remarkable-download/bin/python3

"""
Lightweight reMarkable sync trigger.

Updates:
- lastModified
- modified=true
- synced=false
- version += 1

inside UUID.metadata over SSH.
"""

import json
import subprocess
import time
from pathlib import Path
import re

REMOTE_DIR = "/home/root/.local/share/remarkable/xochitl"
DEFAULT_HOSTS = ["remarkable-usb", "remarkable-home", "remarkable-hotspot"]


def parse_ssh_config():
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
    hosts = sorted(
        set(DEFAULT_HOSTS + parse_ssh_config()),
        key=lambda x: (x not in DEFAULT_HOSTS, x)
    )

    for host in hosts:
        try:
            result = subprocess.run(
                ["ssh", host, "echo ok"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                print(f"[+] Connected via: {host}")
                return host

        except Exception:
            pass

    raise RuntimeError("Could not connect to reMarkable")


def ssh_read(host, remote_path):
    result = subprocess.run(
        ["ssh", host, f"cat '{remote_path}'"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    return result.stdout


def ssh_write(host, remote_path, content):
    cmd = f"cat > '{remote_path}' << 'EOF'\n{content}\nEOF"

    result = subprocess.run(
        ["ssh", host, cmd],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def trigger_sync(uuid):
    host = find_working_host()

    metadata_path = f"{REMOTE_DIR}/{uuid}.metadata"

    print(f"[+] Reading {metadata_path}")

    raw = ssh_read(host, metadata_path)
    data = json.loads(raw)

    now_ms = str(int(time.time() * 1000))
    open_now_ms = str(int(time.time() * 1000 - 12000))

    data["lastModified"] = now_ms
    data["lastOpened"] = open_now_ms 
    # data["modified"] = True
    data["synced"] = False
    # data["version"] = data.get("version", 0) + 1

    updated = json.dumps(data, indent=4)

    print("[+] Writing updated metadata...")
    ssh_write(host, metadata_path, updated)

    print("[+] Sync trigger updated successfully")
    # print(f"    version       = {data['version']}")
    print(f"    lastModified  = {now_ms}")
    print(f"    lastOpened  = {open_now_ms}")


if __name__ == "__main__":
    # Replace with your document UUID
    UUID = "3eb07d71-45a5-427a-a0ef-8981489092a2" # test trigger
    trigger_sync(UUID)