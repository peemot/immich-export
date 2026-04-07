# Immich XMP Export Tool

[![Python 3.6+](https://img.shields.io/badge/Python-3.6+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Python tool to securely export asset metadata from your [Immich](https://immich.app/) photo management system into standard XMP sidecar files, including face recognition regions when they are available.

## 🌟 Features

- **📝 XMP for Every Matched Asset**: By default, writes an XMP sidecar for every matched asset, even when no faces are present or face regions cannot be written.
- **🔍 Face Data Extraction**: Retrieves face recognition bounding boxes and labels directly from the Immich API.
- **🎯 Highly Compatible**: MWG-compliant XMP sidecars for digiKam, XnView MP, Adobe Lightroom, and other photo management software.
- **🔄 Coordinate Transformation**: Automatically handles orientation EXIF data to ensure compatibility with other tools.
- **📁 Preserves Directory Structure**: Replicates your library structure for seamless sidecar merging.
- **🔑 Secure Authentication**: Supports both Immich API Keys (recommended) and Email/Password login.
- **🚀 Flexible Workflow**: Choose direct XMP export (`--direct-xmp`) or a two-stage JSON-to-XMP process (default).
- **🎛️ Legacy Face-Only Mode**: Use `--faces-only` to export only assets where at least one valid face region can be written.
- **🗂️ Targeted Exports**: Filter exported assets by specific `album-id` or `library-id`.
- **⚡ Efficient Processing**: Smart batch processing with support for large photo libraries.

## 📑 Project Overview

This project exports Immich asset metadata into XMP sidecar files. When face data exists and valid regions can be calculated, it is written as MWG-compatible face regions. When face data does not exist or face regions cannot be written, the script still creates an XMP sidecar for the asset by default so you can keep sidecar coverage consistent across your library.

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

The script is highly customizable through command-line arguments.

### Processing Modes

By default, the script creates XMP sidecars for **all matched assets**. Use `--faces-only` to export only assets where at least one valid face region can be written.

**1. Default Workflow (Two-Stage)**

Pulls data from Immich, saves it as a JSON file, and then generates XMP sidecars from that JSON.

```bash
python export_xmp.py
```

**2. Direct Export (Fastest)**

Queries Immich and immediately writes XMP sidecars to disk, skipping the intermediate JSON file.

```bash
python export_xmp.py --direct-xmp
```

**3. Face-Only**

Exports only assets where at least one valid face region can be written.

```bash
python export_xmp.py --faces-only
```

**4. Run Stages Independently**

```bash
# Stage 1: Export only the JSON file
python export_xmp.py --stage1-only

# Stage 2: Generate XMP files from a previously downloaded JSON file
python export_xmp.py --stage2-only --json-file path/to/export.json

# Stage 2 with legacy face-only filtering
python export_xmp.py --stage2-only --json-file path/to/export.json --faces-only
```

### Filtering and Options

```bash
# Filter by a specific Album or Library
python export_xmp.py --album-id "your-album-uuid"
python export_xmp.py --library-id "your-library-uuid"

# Specify custom output directories (overrides config values)
python export_xmp.py --json-dir ./my_jsons --xmp-dir ./my_xmps

# Limit processed assets (great for testing)
python export_xmp.py --max-assets 50

# Keep previous face-only behavior
python export_xmp.py --faces-only

# Enable debug logging for detailed outputs and troubleshooting
python export_xmp.py --debug
```

## 📁 Output Structure

The tool securely generates files in the configured output directories without modifying your original library.

### JSON Export (Stage 1)

```text
json_exports/
├── immich_assets_export_20260313_143022.json  # Complete export payload for matched assets
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
**A:** By default, the script writes one XMP file for every matched asset. If no files were generated, check your filters, output permissions, and whether any assets were returned by Immich. If you are using `--faces-only`, only assets where a valid face region can be written will generate an XMP file.

**Q: Output directory permission error or "Refusing to write outside output directory"?**
**A:** The script includes path-traversal protection to prevent overwriting critical system files. It will explicitly refuse to write `.xmp` files outside the designated output directory. Ensure you have proper write permissions for the output folder.

**Q: Empty XMP files or missing face regions?**
**A:** Ensure your photos have been fully processed for face recognition in Immich. Assets without faces still produce metadata sidecars by default, and assets with people but no exportable face regions still keep the rest of their metadata and keywords. Use `--faces-only` to skip those cases and keep the legacy face-region-only behavior.

**Q: How does the tool handle large libraries?**
**A:** The script automatically paginates processing (200 assets at a time) and handles network retries automatically. Processing progress is logged to the console in real time.

**Q: Does this modify my original photos?**
**A:** No. This script is strictly read-only against your Immich server. It generates separate `.xmp` sidecar files in a distinct output directory.

**Q: Config file not found?**
**A:** Ensure you copied `config.json.template` to `config.json`, or rely entirely on Environment Variables.

**Q: Script is silent or seems stuck?**
**A:** Run the script with `--debug` to get verbose real-time logging of asset processing and pagination steps.

## 🔄 Recent Changes

- **Default export now writes XMP for all matched assets**, even when no faces are present.
- **Added `--faces-only`** to preserve the previous face-only export behavior, including skipping assets when a face region cannot be written.
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
