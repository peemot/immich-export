# Immich XMP Export

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Export metadata from [Immich](https://immich.app/) to `.xmp` sidecar files for use in photo tools such as digiKam, XnView MP, and Lightroom.

The script reads from Immich and writes separate JSON exports and XMP sidecars. It does not modify Immich records or your media files. By default, it exports every matched asset to JSON, then generates XMP sidecars. You can filter by faces, album, or library, or skip the intermediate JSON file.

## What gets exported

- Asset metadata, including camera, lens, dimensions, dates, GPS fields, and original file identifiers where available.
- MWG-compatible face regions from Immich, with person names when available.
- Known person names as XMP keywords.
- XMP sidecars in a folder tree that mirrors the original Immich asset paths.

## Prerequisites

1. Python 3.10+
2. Immich v1.100.0 or newer (see [Choosing the search API](#choosing-the-search-api))
3. An Immich API key with at least `asset.read` and `face.read` permissions, or an Immich email/password login

## Quick start

### 1. Clone the repository

```bash
git clone https://github.com/peemot/immich-export.git
cd immich-export
```

### 2. Install dependencies

```bash
python -m pip install requests
```

### 3. Configure Immich access

Copy [config.json.template](config.json.template) to `config.json`:

```bash
cp config.json.template config.json
```

On Windows, use `copy config.json.template config.json`.

Set `immich.base_url` to your server URL without an `/api` suffix. Fill in either `api_key` or both `email` and `password`; a non-empty API key takes precedence. Keep `config.json` out of version control.

### 4. Run the export

Run from the project directory so the script can find `config.json` and use the expected relative output paths:

```bash
python export_xmp.py
```

## Configuration reference

The template includes every supported setting, with defaults for optional settings. Environment variables override `config.json`; command-line options override the corresponding search API and output-directory settings. Missing settings use built-in defaults.

| JSON path | Environment variable | Description |
|-----------|----------------------|-------------|
| `immich.base_url` | `IMMICH_BASE_URL` | Immich server URL |
| `immich.api_key` | `IMMICH_API_KEY` | API key |
| `immich.email` | `IMMICH_EMAIL` | Login email, used if no API key is set |
| `immich.password` | `IMMICH_PASSWORD` | Login password, used if no API key is set |
| `settings.request_timeout` | `IMMICH_REQUEST_TIMEOUT` | Request timeout in seconds |
| `settings.retry_attempts` | `IMMICH_RETRY_ATTEMPTS` | Retries for 429 and 5xx responses |
| `settings.face_request_workers` | `IMMICH_FACE_REQUEST_WORKERS` | Concurrent face requests; limited to 1-16 |
| `settings.search_api` | `IMMICH_SEARCH_API` | `legacy` or `structured` |
| `output.xmp_export_dir` | `OUTPUT_XMP_DIR` | XMP output directory |
| `output.json_export_dir` | `OUTPUT_JSON_DIR` | JSON output directory |

To configure access without a file, set `IMMICH_BASE_URL` and either `IMMICH_API_KEY` or both `IMMICH_EMAIL` and `IMMICH_PASSWORD`.

### Choosing the search API

The default is `legacy`. For a server using structured search, select it for one run:

```bash
python export_xmp.py --search-api structured
```

For a persistent preference, set `settings.search_api` in `config.json` or `IMMICH_SEARCH_API`. Structured search requires [Immich v3.2.0](https://immich.app/blog/v3.2.0-release) or newer and is needed if your server no longer supports legacy search. The script does not switch automatically. Compatibility with a running Immich v4 server has not been verified.

## Usage

### Processing modes

| Flag | Description |
|------|-------------|
| none | Run both stages: export Immich data to JSON, then generate XMP files |
| `--direct-xmp` | Query Immich and write XMP sidecars directly, without a JSON file |
| `--stage1-only` | Export Immich data to JSON only |
| `--stage2-only` | Generate XMP files from an existing JSON file; requires `--json-file` |

### Options

| Flag | Description |
|------|-------------|
| `--export VALUE` | Select assets and face records; Look at [Export selection](#export-selection); default: `all-assets` |
| `--json-file FILE` | Existing JSON file for `--stage2-only` |
| `--json-dir DIR` | JSON output directory |
| `--xmp-dir DIR` | XMP output directory |
| `--album-id UUID` | Process only assets from one album |
| `--library-id UUID` | Process only assets from one library |
| `--search-api MODE` | Select `legacy` or `structured` |
| `--max-assets N` | Limit processed assets for a smaller test run |
| `--debug` | Enable verbose logging |

### Examples

```bash
# Direct Immich-to-XMP run
python export_xmp.py --direct-xmp

# Export only assets with any valid face region
python export_xmp.py --export assets-with-faces

# Export only assets with known, visible, non-hidden faces
python export_xmp.py --export assets-with-known-visible-faces

# Export a single album or library
python export_xmp.py --album-id "your-album-uuid"
python export_xmp.py --library-id "your-library-uuid"

# Generate XMP from an existing JSON export, without connecting to Immich
python export_xmp.py --stage2-only --json-file data.json --xmp-dir xmp_sidecars
```

## Export selection

`--export` selects both assets and face records in all processing modes.

| Value | Assets included | Face records included |
|-------|-----------------|-----------------------|
| `all-assets` | Every matched asset, including assets without faces | Face metadata when present |
| `assets-with-faces` | Assets with at least one valid face region | All valid face records, including unknown people |
| `assets-with-known-visible-faces` | Assets with at least one known, visible, non-hidden face | Only known, visible, non-hidden face records |

For `assets-with-known-visible-faces`, "known" means the person has a non-empty name other than `Unknown`. Hidden people and hidden or non-visible face records are excluded. Other asset metadata is still preserved for every exported asset.

With `--stage2-only`, the JSON file must already contain the required assets and face records. A narrower JSON export cannot restore omitted data.

## Output structure

### JSON export, Stage 1

```text
json_exports/
└── immich_assets_export_20260313_143022.json
```

The JSON export lets you generate XMP later without querying Immich again. With `--stage2-only --json-file FILE --xmp-dir DIR`, no server connection, credentials, or config file are needed.

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

- Generated JSON and XMP files may contain names, face regions, GPS/location fields, camera metadata, timestamps, and original asset paths.
- The script strips unsafe path components and refuses to write XMP files outside the configured output directory.

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| Authentication failed | Check the server URL and credentials. API keys need `asset.read` and `face.read` permissions. |
| No XMP files were generated | Check filters, output permissions, and whether Immich returned any assets. Some `--export` modes intentionally skip assets. |
| Missing face regions | Make sure face recognition has finished in Immich. Check whether the selected `--export` mode excludes unknown, hidden, or non-visible faces. |
| Sidecars exist but contain no face regions | This can be expected. The default mode still creates sidecars for assets without writable face regions. |
| Permission error or `Refusing to write outside output directory` | Check output-folder permissions and inspect any unusual asset paths. |
| Unexpected output folder structure | XMP sidecars mirror Immich asset paths. Inspect `xmp_sidecars/` before merging it into your photo library. |
| Large library is slow | Try a smaller run with `--max-assets`, `--album-id`, or `--library-id`. |
| Config file not found | Copy `config.json.template` to `config.json`, or configure the tool with environment variables. |
| Structured search is not supported | Use Immich v3.2.0 or newer. Select `--search-api legacy` only if the server still supports the legacy search fields. |
| Script is silent or seems stuck | Run with `--debug` for verbose logging. |

## Tests

Run from the project directory after installing the dependency:

```bash
python -m unittest discover -s tests -v
```

Tests run locally without an Immich server, credentials, or personal export data.

## Contributing

Issues and pull requests are welcome.
