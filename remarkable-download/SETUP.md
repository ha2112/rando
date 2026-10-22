# rm-sync setup and usage

## Installation

Use Python 3.10 or newer and make sure `ssh` and `rsync` are on `PATH`.
From this repository's `remarkable-download` folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
rm-sync --help
```

The editable installation registers `rm-sync` as a console command and installs
the dependencies declared in `pyproject.toml`. Source edits take effect without
reinstalling. Activate the environment in each new shell, or invoke its
`.venv/bin/rm-sync` directly.

## SSH profiles

Enable SSH access on the tablet and configure SSH key authentication using the
tablet's own login details. Create matching aliases in `~/.ssh/config`, for
example:

```sshconfig
Host remarkable-usb
    HostName 10.11.99.1
    User root
    IdentityFile ~/.ssh/id_rsa_remarkable

Host remarkable-home
    HostName 192.168.1.42
    User root
    IdentityFile ~/.ssh/id_rsa_remarkable
```

Replace the home IP and key path with your values. Confirm the connection before
running a download:

```bash
ssh remarkable-usb 'echo ok'
```

The default profiles in [`rm_sync/config.py`](rm_sync/config.py) are `usb`,
`home`, `hotspot`, and `wifi-hust-b1`. When `--mode` is omitted, the CLI probes
`hotspot`, `home`, `wifi-hust-b1`, then `usb`, choosing the first reachable alias.
Use `--mode usb` (or another profile key) to select a connection explicitly.

## Configuration and paths

Runtime defaults come from `rm_sync/config.py`. Edit `PROFILES` and
`PROFILE_ORDER` there to change aliases and connection priority.

`rm-sync init` writes `~/.config/rm-sync/config.toml`, but the current code does
**not** read that file at runtime. Its saved values do not override defaults.
Use the source configuration and command-line path options instead.

Default locations:

| Setting | Path |
| --- | --- |
| Tablet library | `/home/root/.local/share/remarkable/xochitl` |
| Local cache | `~/Downloads/RemarkableSync/.RM_FILES/` |
| Rendered output | `~/Downloads/RemarkableSync/done/` |
| Render state | `<cache>/.rm_render_state.json` |
| Removed output | `<output>/.TRASH/` |

`sync`, `render`, and `status` accept `--cache` / `-c` and `--output` / `-o`.
`pull` accepts `--cache`, but always uses the default output for cleanup.
Keep the same cache/output pair across commands so status and cleanup refer to
the intended render state. The cache is a mirror, not an archival backup:
full downloads delete cached files missing from the tablet.

## Workflows

Global flags (`--dry-run`, `--force`, `--verbose` / `-v`) precede the subcommand.
Use `rm-sync COMMAND --help` to inspect command-specific options.

Normal runs show CLI progress, summaries, and errors while suppressing debug,
info, and warning logs. Use `--verbose` or `-v` to include those diagnostic logs
and tracebacks for failed documents:

```bash
rm-sync --verbose sync --mode usb
```

```bash
# Download the whole library, clean up output, and render changed documents.
rm-sync sync --mode usb

# Download now; render from the cache later without connecting to the tablet.
rm-sync pull --mode usb
rm-sync render

# Compare cached documents against the last render; this does not query the tablet.
rm-sync status

# Use a dedicated cache and output directory.
rm-sync sync --cache /path/to/cache --output /path/to/pdfs
rm-sync status --cache /path/to/cache --output /path/to/pdfs

# Select documents; repeat the option for each UUID.
rm-sync sync --mode usb --uuids UUID_1 --uuids UUID_2
rm-sync render --uuids UUID_1 --uuids UUID_2

# Re-render everything, and purge existing trash files.
rm-sync --force render
```

Explicit UUIDs bypass change detection for those documents. Otherwise, successful
renders are tracked using source-file hashes, renderer revision, and render
settings; unchanged documents with existing output PDFs are skipped.

Both `sync` and `pull` move previously rendered PDFs for deleted or trashed
documents into `.TRASH/`. Full runs also move PDFs without matching active
tablet documents into `.TRASH/`, preserve current tablet folders (including
empty ones), and remove obsolete empty output folders. Keep unrelated PDFs
outside the managed output directory. `render` also checks for outputs whose
metadata has disappeared from the local cache.

### Preview and force behavior

```bash
rm-sync --dry-run sync --mode usb
rm-sync --dry-run pull --mode usb
rm-sync --dry-run trigger --mode usb
```

Dry-run `sync` and `pull` print a plan without transferring files, but path
resolution can create local directories. Importing configuration also creates
the default `done`, `logs`, and `templates` directories.

Dry-run `trigger` still connects over SSH to discover metadata filenames, but
does not modify them. Dry-run `render` performs orphan cleanup **before** its
preview check, so it can move existing PDFs into `.TRASH/`. Combining it with
`--force` can also purge trash. Do not treat dry-run `render` as read-only.

`--force` on `sync` and `render` bypasses change detection and permanently
deletes regular files directly inside `.TRASH/`; nested directories are left
alone. These options do not add preview/force behavior to `init` or `status`.

## Requesting tablet cloud sync

```bash
rm-sync --dry-run trigger --mode usb
rm-sync trigger --mode usb
```

`trigger` discovers all tablet `.metadata` files and updates each UUID's
`lastModified` and `lastOpened` timestamps, sets `modified = true` and
`synced = false`, and resets `version = 0`. This modifies tablet metadata to
request cloud synchronization; it does not confirm that a cloud upload finished.

The standalone `rm_sync_trigger_all.py` is an older alternative with its own
host discovery. Prefer the CLI for explicit profile selection and dry-run support.

## Project layout and diagnostics

```text
rm_sync/
├── __init__.py  # allows existing flat module imports inside the package
├── cli.py       # Typer commands
├── client.py    # SSH / rsync transport
├── config.py    # runtime defaults and rendering settings
├── main.py      # pipeline, render state, and output cleanup
├── models.py    # document, page, stroke, and highlight data
├── parser.py    # document metadata and .rm scene decoding
├── brushes.py   # ink geometry and colors
└── renderer.py  # annotated PDF assembly
test/           # automated tests and separate exploratory scripts
tools/          # local fixture verification tools
pyproject.toml  # package dependencies and console entry point
```

From this folder, with the virtual environment active:

```bash
python -m unittest discover -s test -p 'test_*.py'
```

Tests requiring local fixture manifests skip when those files are absent.
The `[test] ...` scripts are excluded by this pattern and may access the tablet.

With the corresponding frozen fixtures under `test_data/`, run:

```bash
python tools/verify_compatibility.py --output /tmp/rm-sync-compatibility
python tools/compare_pen_fixture.py --output /tmp/rm-sync-pen
```

Both tools accept `--fixture` to select another fixture directory. Pen comparison
also requires Poppler's `pdftoppm` on `PATH`. Reports go into the supplied output
directory; the tools do not need a tablet connection.

For partial renders, inspect `fidelity` and `fidelity_warnings` in
`<cache>/.rm_render_state.json`. Approximate brushes, preserved extensions, and
missing ink sources can produce partial fidelity; typed scene text, unresolved
eraser operations, and unsupported scene blocks can fail a document. CLI
`sync`, `render`, and `trigger` return exit code `2` when some documents fail;
connection failures in `sync`, `pull`, and `trigger` return `1`.
