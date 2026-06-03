# rm-sync — reMarkable Sync & Render CLI

`rm-sync` is a local CLI tool for downloading and rendering reMarkable tablet
documents into annotated PDFs. After a one-time editable install, `rm-sync`
is available from any terminal.

## One-time setup

```bash
cd /path/to/remarkable-download
pip install -e .
```

The `-e` (editable) flag means any change to the source files in `rm_sync/`
takes effect immediately — no reinstall needed.

## What you get

```bash
rm-sync --help
```

| Command          | What it does                              |
|------------------|-------------------------------------------|
| `rm-sync sync`   | Full pipeline: rsync → trash scan → render |
| `rm-sync pull`   | Rsync files from the tablet (no render)    |
| `rm-sync render` | Render PDFs from the local cache only      |
| `rm-sync status` | Show which docs are new / changed / deleted |
| `rm-sync init`   | Interactive setup → `~/.config/rm-sync/config.toml` |

Global flags available on every command:

| Flag            | Effect                                          |
|-----------------|-------------------------------------------------|
| `--dry-run`     | Preview what would happen without making changes |
| `--force`       | Bypass change detection; re-render everything    |
| `--verbose`, `-v` | Enable DEBUG-level logging                     |

## Package structure

```
rm_sync/
├── __init__.py   # makes this a Python package
├── cli.py        # Typer CLI entry point (NEW)
├── client.py     # SSH / rsync transport layer
├── config.py     # hardcoded defaults (SSH profiles, paths, pen settings)
├── main.py       # pipeline orchestration (sync → render)
├── models.py     # data classes (DocumentMeta, PageInfo, Stroke, …)
├── parser.py     # .rm v6 binary parser (uses rmscene)
└── renderer.py   # PDF assembler (uses PyMuPDF)
pyproject.toml    # editable install config
requirements.txt  # dependencies
```

## How the import bridge works

The existing files in `rm_sync/` use flat imports:

```python
from client import RsyncClient        # not: from rm_sync.client import ...
from config import PROFILES
```

This works when running from inside `rm_sync/`, but breaks when `rm_sync` is
imported as an installed package.  `rm_sync/__init__.py` solves this by
injecting its own directory into `sys.path` before any submodule loads — so
every flat import resolves correctly without touching any existing source file.

`cli.py` follows the same flat-import convention and is the **only** file you
need to touch when adding new commands.

## Interactive config (`rm-sync init`)

Running `rm-sync init` prompts for:

- SSH profile aliases (one per connection mode: USB, home, hotspot, Wi-Fi)
- Local output and cache directories
- Profile auto-detection order

These are saved to `~/.config/rm-sync/config.toml`.  At runtime, values in
this file override the hardcoded defaults in `rm_sync/config.py`.

## SSH prerequisites

Each profile name in `config.py` (or your `config.toml`) must have a
matching `Host` entry in `~/.ssh/config`:

```
Host remarkable-usb
    HostName 10.11.99.1
    User root
    IdentityFile ~/.ssh/id_rsa_remarkable

Host remarkable-home
    HostName 192.168.1.42
    User root
    IdentityFile ~/.ssh/id_rsa_remarkable
```

The tablet must have SSH enabled (Settings → Help → Copyrights and Licenses
→ tap "General Information" → enable SSH; the password is shown on the
tablet screen).

## Typical workflows

**Daily sync — pull everything, render changed:**
```bash
rm-sync sync
```

**Just download, render later:**
```bash
rm-sync pull
```

**Re-render everything from cache (offline, after code changes):**
```bash
rm-sync render --force
```

**Check what's new before syncing:**
```bash
rm-sync status
```

**Preview pipeline without touching files:**
```bash
rm-sync --dry-run sync
```

**Render specific documents:**
```bash
rm-sync render --uuids abc123... def456...
```

## Dependency notes

- `rmscene` — parses reMarkable `.rm` v6 binary files
- `PyMuPDF` (fitz) — reads base PDFs and assembles annotated output
- `typer[all]` — CLI framework (includes `rich` for colored output)
- `rsync` — must be on `$PATH` (standard on macOS and Linux)

## No changes to core logic

The sync pipeline (`client.py`, `main.py`, `parser.py`, `renderer.py`,
`config.py`, `models.py`) is untouched.  `cli.py` is a thin wrapper that
calls the same functions the old `python main.py` script used — it just
exposes them as proper subcommands with nice help text and formatting.
