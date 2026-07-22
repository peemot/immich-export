# Immich XMP Export

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Export metadata from [Immich](https://immich.app/) to `.xmp` sidecar files for use in photo tools such as digiKam, XnView MP, and Lightroom.

The script reads asset metadata and face regions from the Immich API, then writes new files under separate output directories. It does not modify Immich records, edit your media files, or write sidecars into your photo library unless you copy them there yourself.

By default, it creates a sidecar for every matched asset. You can limit the export to assets with faces, known visible faces, a specific album, or a specific library. The default workflow saves an intermediate JSON export before generating XMP.

## What gets exported

- Asset metadata, including camera, lens, dimensions, dates, GPS fields, and original file identifiers where available.
- MWG-compatible face regions from Immich, with person names when available.
- Known person names as XMP keywords.
- XMP sidecars in a folder tree that mirrors the original Immich asset paths.
- An optional intermediate JSON export and an `export_summary.json` report.

## Prerequisites

1. Python 3.10+
2. Immich v1.100.0 or newer
3. An Immich API key with at least `asset.read` and `face.read` permissions, or an Immich email/password login

## Quick start

### 1. Clone the repository

```bash
git clone https://github.com/peemot/immich-export.git
cd immich-export
```

### 2. Install dependencies

```bash
pip install requests
```

### 3. Configure Immich access

Copy the template and fill in your server URL and credentials:

```bash
cp config.json.template config.json
```

Example `config.json`:

```json
{
  "immich": {
    "base_url": "https://your-immich-server.com",
    "api_key": "your-api-key",
    "email": "",
    "password": ""
  },
  "settings": {
    "request_timeout": 30,
    "retry_attempts": 3,
    "face_request_workers": 8
  },
  "output": {
    "xmp_export_dir": "xmp_sidecars",
    "json_export_dir": "json_exports"
  }
}
```

If `api_key` is set, the script uses it and ignores `email` and `password`.

You can also configure the connection with environment variables:

```bash
export IMMICH_BASE_URL="https://your-immich-server.com"
export IMMICH_API_KEY="your-api-key"
```

### 4. Run the export

```bash
python export_xmp.py
```

The default run writes a JSON export first, then creates XMP files from that JSON. Use direct mode to skip the JSON file:

```bash
python export_xmp.py --direct-xmp
```

## Configuration reference

Configuration is applied in this order:

1. Command-line arguments for paths and execution options
2. Environment variables for credentials and server settings
3. `config.json`
4. Built-in defaults

| JSON path | Environment variable | Default | Description |
|-----------|----------------------|---------|-------------|
| `immich.base_url` | `IMMICH_BASE_URL` | - | Immich server URL |
| `immich.api_key` | `IMMICH_API_KEY` | - | API key; requires `asset.read` and `face.read` permissions |
| `immich.email` | `IMMICH_EMAIL` | - | Login email, used only if no API key is set |
| `immich.password` | `IMMICH_PASSWORD` | - | Login password, used only if no API key is set |
| `settings.request_timeout` | `IMMICH_REQUEST_TIMEOUT` | `30` | Network request timeout in seconds |
| `settings.retry_attempts` | `IMMICH_RETRY_ATTEMPTS` | `3` | Retries for 429 and 5xx responses |
| `settings.face_request_workers` | `IMMICH_FACE_REQUEST_WORKERS` | `8` | Concurrent `/api/faces` requests; clamped to 1-16 |
| `output.xmp_export_dir` | `OUTPUT_XMP_DIR` | `xmp_sidecars` | Directory for generated `.xmp` files |
| `output.json_export_dir` | `OUTPUT_JSON_DIR` | `json_exports` | Directory for intermediate JSON exports |

## Usage

```bash
python export_xmp.py [OPTIONS]
```

### Processing modes

All processing modes use the same `--export` selection rules.

| Flag | Description |
|------|-------------|
| none | Run both stages: export Immich data to JSON, then generate XMP files |
| `--direct-xmp` | Query Immich and write XMP sidecars directly, without a JSON file |
| `--stage1-only` | Export Immich data to JSON only |
| `--stage2-only` | Generate XMP files from an existing JSON file; requires `--json-file` |

### Options

| Flag | Example | Description |
|------|---------|-------------|
| `--export` | `--export assets-with-faces` | Select which assets and face records to export. Default: `all-assets` |
| `--json-file` | `--json-file data.json` | Existing JSON file for `--stage2-only` |
| `--json-dir` | `--json-dir ./jsons` | Directory for JSON exports; overrides config |
| `--xmp-dir` | `--xmp-dir ./xmps` | Directory for XMP sidecars; overrides config |
| `--album-id` | `--album-id "uuid"` | Process only assets from one album |
| `--library-id` | `--library-id "uuid"` | Process only assets from one library |
| `--max-assets` | `--max-assets 50` | Limit processed assets; useful for test runs |
| `--debug` | `--debug` | Enable verbose logging |

### Examples

```bash
# Default two-stage run: JSON export, then XMP generation
python export_xmp.py

# Direct Immich-to-XMP run
python export_xmp.py --direct-xmp

# Export only assets with any valid face region
python export_xmp.py --export assets-with-faces

# Export only assets with known, visible, non-hidden faces
python export_xmp.py --export assets-with-known-visible-faces

# Export a single album or library
python export_xmp.py --album-id "your-album-uuid"
python export_xmp.py --library-id "your-library-uuid"
```

## Export selection

`--export` controls both which assets are exported and which face records are written. The same rules apply in default, direct, Stage 1, and Stage 2 modes.

| Value | Assets included | Face records included | Use when you want |
|-------|-----------------|-----------------------|-------------------|
| `all-assets` | Every matched asset, including assets without faces | Face metadata when present | Sidecars for the whole selected set |
| `assets-with-faces` | Only matched assets with at least one valid face region | All valid face records for those assets | Face-region exports, including unknown people |
| `assets-with-known-visible-faces` | Only matched assets with at least one known, visible, non-hidden face | Only known, visible, non-hidden face records | Named-person exports without unknown or hidden faces |

For `assets-with-known-visible-faces`, "known" means the person has a non-empty name other than `Unknown`. Hidden people and hidden or non-visible face records are excluded. Other asset metadata is still preserved for every exported asset.

When using `--stage2-only`, the JSON file must already contain the assets and face records required by the selected export value. A JSON file created with a narrower `--export` value cannot restore data that was not written to that file.

## Output structure

The script writes to the configured output directories. It does not modify your original library.

### JSON export, Stage 1

```text
json_exports/
└── immich_assets_export_20260313_143022.json
```

### XMP sidecars, Stage 2 or direct mode

The `xmp_sidecars` directory mirrors the original Immich asset paths.

```text
xmp_sidecars/
├── export_summary.json
├── admin/
│   └── 2023/
│       └── photo1.jpg.xmp
└── family/
    └── 2024/
        └── photo2.jpg.xmp
```

To apply the sidecars, inspect the generated tree and copy or merge it into your photo library root.

## Safety and privacy notes

- The script reads from Immich and writes separate JSON/XMP files. It does not edit image files or update Immich records.
- `config.json` can contain an API key, email, or password. Do not commit it to version control.
- Generated JSON and XMP files may contain names, face regions, GPS/location fields, camera metadata, timestamps, and original asset paths.
- The script strips unsafe path components and refuses to write XMP files outside the configured output directory.
- Asset search requests are paginated at 1000 assets per request. Face data is retrieved concurrently within each metadata page, using up to `settings.face_request_workers` `/api/faces` requests at a time. Requests are retried on 429 and 5xx responses.

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| Authentication failed | Check that `immich.base_url` does not end with `/api`. If using an API key, verify it has `asset.read` and `face.read` permissions. |
| No XMP files were generated | Check filters, output permissions, and whether Immich returned any assets. Some `--export` modes intentionally skip assets. |
| Missing face regions | Make sure face recognition has finished in Immich. Check whether the selected `--export` mode excludes unknown, hidden, or non-visible faces. |
| Sidecars exist but contain no face regions | This can be expected. The default mode still creates sidecars for assets without writable face regions. |
| Permission error or `Refusing to write outside output directory` | Check output-folder permissions and inspect any unusual asset paths. |
| Unexpected output folder structure | XMP sidecars mirror Immich asset paths. Inspect `xmp_sidecars/` before merging it into your photo library. |
| Large library behavior | Use `--max-assets`, `--album-id`, or `--library-id` for smaller test runs. |
| Config file not found | Copy `config.json.template` to `config.json`, or configure the tool with environment variables. |
| Script is silent or seems stuck | Run with `--debug` for verbose logging. |

## Contributing

Issues and pull requests are welcome.
