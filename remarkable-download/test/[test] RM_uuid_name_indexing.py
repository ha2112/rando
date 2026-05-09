#!/usr/bin/env python3

"""
Build a cached index mapping UUID -> file paths + metadata from a reMarkable device over SSH.
Tries all plausible SSH config hostnames in ~/.ssh/config for connection (usb, hotspot, home).
Outputs a JSON cache locally for fast lookup.
"""

import json
import subprocess
from pathlib import Path
import os
import re

# CONFIG
REMOTE_DIR = "/home/root/.local/share/remarkable/xochitl"
CACHE_FILE = Path(__file__).resolve().parent.parent / "uuid_index.json"
DEFAULT_HOSTS = ["remarkable-usb", "remarkable-home", "remarkable-hotspot"]

def parse_ssh_config(config_path=None):
    """
    Parse hosts in ~/.ssh/config. Naive parse: returns a set of aliases.
    """
    if config_path is None:
        config_path = Path.home() / ".ssh" / "config"
    if not Path(config_path).exists():
        return []

    hosts = set()
    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*Host\s+(.+)", line)
            if m:
                # May be multiple hosts separated by whitespace:
                for host in m.group(1).split():
                    if host != "*" and not ("?" in host or "*" in host):
                        hosts.add(host.strip())
    return list(hosts)

def try_ssh_ls_possibly_many(hostnames):
    """
    Try each SSH hostname. Returns first successful list of files and the SSH hostname used.
    """
    for host in hostnames:
        try:
            cmd = ["ssh", host, f"ls {REMOTE_DIR}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                files = result.stdout.splitlines()
                if files:
                    print(f"[+] Connected using host: '{host}'")
                    return files, host
        except Exception as e:
            pass
    raise RuntimeError(f"Could not connect to any SSH profile! Hosts tried: {hostnames}")

def ssh_cat(path, ssh_host):
    """Read remote file using the current ssh_host"""
    cmd = ["ssh", ssh_host, f"cat {path}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout

def build_index():
    # Get all hosts from config, prefer known relevant hostnames
    ssh_hosts = set(DEFAULT_HOSTS + parse_ssh_config())
    ssh_hosts = sorted(ssh_hosts, key=lambda x: (x not in DEFAULT_HOSTS, x))
    files, ssh_host = try_ssh_ls_possibly_many(ssh_hosts)

    index = {}

    for f in files:
        if not f.endswith(".metadata"):
            continue

        uuid = f.replace(".metadata", "")
        metadata_path = f"{REMOTE_DIR}/{f}"

        try:
            content = ssh_cat(metadata_path, ssh_host)
            meta = json.loads(content)
        except Exception:
            continue

        visible_name = meta.get("visibleName", "")
        parent = meta.get("parent")

        # Build paths
        base = f"{REMOTE_DIR}/{uuid}"

        entry = {
            "uuid": uuid,
            "name": visible_name,
            "parent": parent,
            "pdf": f"{base}.pdf",
            "content": f"{base}.content",
            "pagedata": f"{base}.pagedata",
            "rm_dir": base,
            "thumbnails": f"{base}.thumbnails",
        }

        index[uuid] = entry

    return index

def save_index(index):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    print("[+] Building index from reMarkable (trying all SSH configs)...")
    try:
        idx = build_index()
    except Exception as e:
        print(f"[-] ERROR: {e}")
        exit(1)

    print(f"[+] Found {len(idx)} documents")
    save_index(idx)

    print(f"[+] Saved to {CACHE_FILE}")

    query = "piano"
    # Example query
    print(f"\n[+] Example search: contains {query}")
    for u, v in idx.items():
        if query in v["name"].lower():
            print(f"{u} -> {v['name']}")
