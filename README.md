# Immich XMP Export Tool

[![Python 3.6+](https://img.shields.io/badge/Python-3.6+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Python tool to securely export asset metadata from your [Immich](https://immich.app/) photo management system into standard XMP sidecar files, including face recognition regions when they are available.

## 🌟 Features

- **📝 Universal XMP Export**: Writes sidecars for all matched assets, even without face data.
- **🔍 Face Extraction**: Retrieves bounding boxes and labels directly from the Immich API.
- **🎯 Highly Compatible**: MWG-compliant XMPs for digiKam, XnView MP, Adobe Lightroom, etc.
- **🔄 Auto-Rotation**: Adjusts coordinates based on orientation EXIF for broad compatibility.
- **📁 Preserves Structure**: Replicates your library's folder structure for seamless merging.
- **🔑 Secure Auth**: Supports both Immich API Keys (recommended) and Email/Password login.
- **🚀 Flexible Workflow**: Direct export (`--direct-xmp`) or two-stage JSON-to-XMP (default).
- **🎛️ Unified Export Selector**: Use `--export` to choose all assets, assets with faces, or assets with known visible faces.
- **🗂️ Targeted Exports**: Filter by specific `--album-id` or `--library-id`.
- **⚡ Efficient**: Smart batched API requests with support for large photo libraries.

## 📑 Project Overview

This project exports Immich asset metadata into XMP sidecar files. When face data exists and valid regions can be calculated, it is written as MWG-compatible face regions. When face data does not exist or face regions cannot be written, the script still creates an XMP sidecar for the asset by default so you can keep sidecar coverage consistent across your library.

The single `--export` flag controls which assets are exported and which face records are included. All processing modes (`--direct-xmp`, `--stage1-only`, `--stage2-only`, and the default two-stage run) use the same export selection rules, so the same `--export` value produces the same asset and face-data scope.

## Prerequisites

1. **Python 3.6+**
2. **Immich:** Immich v1.100.0 or newer.

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/peemot/immich-export.git
cd immich-export
```

### 2. Install Dependencies

```bash
pip install requests
```

### 3. Configuration

You can configure the tool using either a `config.json` file or Environment Variables.

#### Method 1: Using `config.json` (Recommended)

Copy the template and fill in your details:

```bash
cp config.json.template config.json
```

Example `config.json`:

```json
{
  "immich": {
    "base_url": "https://your-immich-server.com",
    "api_key": "your-api-key",
    "email": "your-email@example.com",
    "password": "your-password"
  },
  "settings": {
    "request_timeout": 30,
    "retry_attempts": 3
  },
  "output": {
    "xmp_export_dir": "xmp_sidecars",
    "json_export_dir": "json_exports"
  }
}
```

*(Note: If `api_key` is provided, the tool uses it and ignores `email` and `password`. The API key must have at least the `asset.read` permission.)*

#### Method 2: Using Environment Variables

You can override or entirely replace the JSON config with environment variables:

```bash
export IMMICH_BASE_URL="https://your-immich-server.com"
export IMMICH_API_KEY="your-api-key"
```

### Configuration Precedence

Configuration priority is evaluated as follows:

1. **Command Line Arguments** (highest priority for paths and execution logic)
2. **Environment Variables** (highest priority for credentials and server settings)
3. **`config.json`**
4. **Built-in defaults**

## ⚙️ Configuration Reference

| JSON Path | Environment Variable | Default | Description |
|-----------|----------------------|---------|-------------|
| `immich.base_url` | `IMMICH_BASE_URL` | - | Your Immich server URL |
| `immich.api_key` | `IMMICH_API_KEY` | - | API key (Requires `asset.read` permission) |
| `immich.email` | `IMMICH_EMAIL` | - | Login email (Fallback if no API key) |
| `immich.password` | `IMMICH_PASSWORD` | - | Login password (Fallback if no API key) |
| `settings.request_timeout` | `IMMICH_REQUEST_TIMEOUT` | `30` | Network request timeout in seconds |
| `settings.retry_attempts` | `IMMICH_RETRY_ATTEMPTS` | `3` | Retries for 5xx/429 network errors |
| `output.xmp_export_dir` | `OUTPUT_XMP_DIR` | `xmp_sidecars` | Target directory for generated `.xmp` files |
| `output.json_export_dir` | `OUTPUT_JSON_DIR` | `json_exports` | Target directory for intermediate JSON dumps |

## 💻 Usage

The script is highly customizable through command-line arguments. Basic execution runs the default two-stage workflow (JSON export → XMP generation) with `--export all-assets`, so it processes **all matched assets**.

```bash
python export_xmp.py [OPTIONS]
```

### Processing Modes

All processing modes respect the same `--export` option.

| Flag | Description |
|------|-------------|
| *(None)* | **Default:** Two-stage workflow (Export to JSON, then generate XMP files) |
| `--direct-xmp` | **Direct Export:** Skip JSON file, write XMP sidecars directly to disk |
| `--stage1-only` | **Stage 1:** Export data to JSON file only (No XMP generation) |
| `--stage2-only` | **Stage 2:** Generate XMPs from an existing JSON file (Requires `--json-file`) |

### Filtering & Options

| Flag | Example | Description |
|------|---------|-------------|
| `--export` | `--export assets-with-faces` | Select which assets and face data are exported. Choices are listed below. Default: `all-assets` |
| `--json-file` | `--json-file data.json` | Path to existing JSON file (Required for `--stage2-only`) |
| `--json-dir` | `--json-dir ./jsons` | Set custom directory for JSON exports (overrides config values) |
| `--xmp-dir` | `--xmp-dir ./xmps` | Set custom directory for XMP sidecars (overrides config values) |
| `--album-id` | `--album-id "uuid"` | Process only assets from a specific album |
| `--library-id`| `--library-id "uuid"`| Process only assets from a specific library |
| `--max-assets`| `--max-assets 50` | Limit processed assets (Useful for testing) |
| `--debug` | `--debug` | Enable verbose logging for troubleshooting |

### Export Selection

`--export` is the only flag that controls asset and face-data selection. It has three values:

| Value | What gets exported | Face data included |
|-------|--------------------|--------------------|
| `all-assets` | Every matched asset, including assets without faces | Complete face metadata when present |
| `assets-with-faces` | Only matched assets that contain valid face areas | Complete face metadata for those assets |
| `assets-with-known-visible-faces` | Only matched assets with at least one known, non-hidden face | Only known, non-hidden face records |

For `assets-with-known-visible-faces`, “known” means the person has a non-empty name other than `Unknown`; hidden people or hidden/non-visible face records are excluded. The rest of the asset metadata is still preserved for every exported asset.

When using `--stage2-only`, the JSON file must contain the assets and face records needed for the selected export value. A JSON file created with a narrower `--export` value cannot restore assets or face records that were not written to that JSON file.

Examples:

```bash
# Default: export every matched asset with complete metadata
python export_xmp.py --export all-assets

# Export only assets that contain face regions
python export_xmp.py --export assets-with-faces

# Export only assets with known visible faces, and include only those face records
python export_xmp.py --export assets-with-known-visible-faces

# The same export selector works in direct mode
python export_xmp.py --direct-xmp --export assets-with-known-visible-faces

# The same export selector also works in Stage 2 from JSON
python export_xmp.py --stage2-only --json-file path/to/export.json --export assets-with-known-visible-faces
```

## 📁 Output Structure

The tool securely generates files in the configured output directories without modifying your original library.

### JSON Export (Stage 1)

```text
json_exports/
├── immich_assets_export_20260313_143022.json  # Complete export payload for selected assets
```

### XMP Sidecars (Stage 2 or Direct Mode)

The `xmp_sidecars` directory mirrors your Immich library's internal folder structure.

```text
xmp_sidecars/
├── export_summary.json         # Overall statistics (XMP files created, faces processed, people found)
├── admin/
│   └── 2023/
│       └── photo1.jpg.xmp      # XMP sidecar file
└── family/
    └── 2024/
        └── photo2.jpg.xmp
```

To apply the XMP tags, you simply copy or merge this output directory into your actual photo library root.

## 📂 Project Structure

```text
immich-export/
├── export_xmp.py            # Main export script with built-in config loader
├── config.json.template     # Configuration template file
├── json_exports/            # Default directory for Stage-1 JSON dumps
├── xmp_sidecars/            # Default directory for generated XMP sidecar files
│   ├── export_summary.json  # Auto-generated export statistics report
│   └── ...                  # Mirrored photo directory structure
└── README.md                # Project documentation
```

## 👨‍💻 Development & Validation

### Code Style

- Written in Python 3.x using native type annotations (`typing` module).
- Follows PEP 8 naming conventions.
- Includes structured error handling for API exceptions, JSON decoding, and file IO operations.

### Useful Commands

```bash
# Check Python syntax
python -m py_compile export_xmp.py

# Run basic import test to verify ConfigLoader logic
python -c "from export_xmp import ConfigLoader; print('Config loader OK')"

# Test environment variable overrides
python -c "
import os
os.environ['IMMICH_EMAIL'] = 'test@example.com'
os.environ['IMMICH_PASSWORD'] = 'testpass'
from export_xmp import ConfigLoader
config = ConfigLoader()
print('Environment config test OK')
"
```

## 🛡️ Security Best Practices & Important Notes

1. **Configuration Security:** Never commit `config.json` to version control. Ensure it stays in `.gitignore`.
2. **Sensitive Data Protection:** Use Environment Variables for production deployments to avoid credential leakage.
3. **Template Usage:** Keep `config.json.template` updated with new options to act as configuration documentation.
4. **Path Traversal Shield:** The script strictly enforces output containment. If an Immich asset claims to originate from an unexpected path such as `../../etc`, the script neutralizes the traversal attempt and keeps writes within the configured output directory.
5. **API Limits & Retries:** The script paginates at 200 assets per request and auto-retries on 429/5xx status codes.

## 🐛 Troubleshooting & Common Issues

**Q: Authentication failed?**
**A:** Double-check your server URL (ensure it does not end with `/api`). If using an API key, verify it has `asset.read` permissions.

**Q: No XMP files were generated?**
**A:** By default, the script writes one XMP file for every matched asset. If no files were generated, check your filters, output permissions, and whether any assets were returned by Immich. If you are using `--export assets-with-faces`, only assets with valid face areas are exported. If you are using `--export assets-with-known-visible-faces`, only assets with at least one known, non-hidden face are exported.

**Q: Output directory permission error or "Refusing to write outside output directory"?**
**A:** The script includes path-traversal protection to prevent overwriting critical system files. It will explicitly refuse to write `.xmp` files outside the designated output directory. Ensure you have proper write permissions for the output folder.

**Q: Empty XMP files or missing face regions?**
**A:** Ensure your photos have been fully processed for face recognition in Immich. With the default `--export all-assets`, assets without faces still produce metadata sidecars, and assets with people but no exportable face regions still keep the rest of their metadata and keywords. Use `--export assets-with-faces` to export only assets that contain valid face areas. Use `--export assets-with-known-visible-faces` to export only assets with known, non-hidden faces and to omit unknown or hidden face records.

**Q: How does the tool handle large libraries?**
**A:** The script automatically paginates processing (200 assets at a time) and handles network retries automatically. Processing progress is logged to the console in real time.

**Q: Does this modify my original photos?**
**A:** No. This script is strictly read-only against your Immich server. It generates separate `.xmp` sidecar files in a distinct output directory.

**Q: Config file not found?**
**A:** Ensure you copied `config.json.template` to `config.json`, or rely entirely on Environment Variables.

**Q: Script is silent or seems stuck?**
**A:** Run the script with `--debug` to get verbose real-time logging of asset processing and pagination steps.

## 🔄 Recent Changes

- **Added unified `--export` selection** with `all-assets`, `assets-with-faces`, and `assets-with-known-visible-faces`.
- **Removed the old face-only CLI option** in favor of the single `--export` flag.
- **Default export remains `all-assets`**, writing XMP for all matched assets even when no faces are present.
- Renamed the main script to `export_xmp.py` and aligned export naming with asset-wide behavior.
- Added direct export mode with `--direct-xmp`.
- Added API key authentication support.
- Added filtering with `--album-id` and `--library-id`.
- Added `--max-assets` for safer testing.
- Added configurable output paths via JSON config, environment variables, or CLI.
- Improved API efficiency by using `/search/metadata` to fetch EXIF and people data in the same batch.

## 🤝 Contributing

Issues, bug reports, and Pull Requests are always welcome!

---

**⭐ If this tool saved you time, please consider giving it a Star on GitHub!**
