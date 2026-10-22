# reMarkable Download Tools

`rm-sync` downloads a reMarkable tablet's document library over SSH and renders
notebooks and annotated PDFs locally. It uses `rsync` for transfers, `rmscene`
for `.rm` scene files, and PyMuPDF for PDF output.

## Quick start

Requires Python 3.10 or newer, `ssh`, `rsync`, and a reachable tablet with SSH
key authentication configured. See [SETUP.md](SETUP.md) for SSH profiles,
configuration, and command details.

Run from this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

ssh remarkable-usb 'echo ok'
rm-sync sync --mode usb
```

The SSH alias must match a `Host` entry in `~/.ssh/config`. Omit `--mode` to
probe the profiles listed in `rm_sync/config.py`.

## Commands

| Command | Behavior |
| --- | --- |
| `rm-sync sync` | Download, clean up managed output, and render new or changed documents. |
| `rm-sync pull` | Download without rendering; also clean up managed output. |
| `rm-sync render` | Render new or changed documents from the local cache, offline. |
| `rm-sync status` | Compare the local cache with the last render state. |
| `rm-sync trigger` | Update tablet metadata to request cloud sync for every discovered document. |
| `rm-sync init` | Write a configuration file interactively; runtime loading is not implemented. |

Global options go **before** the subcommand; command-specific options go after:

```bash
rm-sync --verbose sync --mode usb
rm-sync --force render
rm-sync render --uuids UUID_1 --uuids UUID_2
rm-sync --dry-run sync
rm-sync --dry-run trigger --mode usb
```

Explicit UUIDs always re-render when passed to `sync` or `render`. Repeat
`--uuids` (or `-u`) for each document.

## Local files and cleanup

Defaults are defined in [`rm_sync/config.py`](rm_sync/config.py):

| Location | Contents |
| --- | --- |
| `~/Downloads/RemarkableSync/.RM_FILES/` | Raw tablet files and `.rm_render_state.json`. |
| `~/Downloads/RemarkableSync/done/` | Rendered PDFs arranged by tablet folder and document name. |
| `~/Downloads/RemarkableSync/done/.TRASH/` | PDFs removed from active output during cleanup. |

Use `--cache` and, where supported, `--output` to override these paths.

The cache and output are managed directories. Full downloads use
`rsync --delete`, removing cached files no longer present on the tablet. Both
`sync` and `pull` move deleted documents' PDFs to `.TRASH/`; full runs also move
PDFs with no matching tablet document and reconcile output folders. Keep
unrelated files elsewhere. `pull` manages the default output directory and has
no `--output` option.

`--force` on `sync` or `render` also permanently deletes regular files already
in `.TRASH/`. `--dry-run` has side effects in the current implementation; see
[the setup guide](SETUP.md#preview-and-force-behavior).

## Rendering limits

Rendering supports `.rm` v6 scene files, handwritten strokes, and snapped text
highlights. Some brushes use approximations. Missing ink files and supported
scene extensions are reported as partial fidelity in the render-state manifest.
Typed scene text, unresolved eraser operations, and unknown scene blocks outside
the explicitly supported compatibility case fail rendering. Custom template
download is not implemented.

## Development

After installation, run the automated tests from this folder:

```bash
python -m unittest discover -s test -p 'test_*.py'
```

Fixture tests use local files under `test_data/` and skip when their manifests
are absent. Scripts named `[test] ...` are separate exploratory utilities;
some connect to or modify the tablet. See
[SETUP.md](SETUP.md#project-layout-and-diagnostics) for the source layout and
fixture comparison tools.
