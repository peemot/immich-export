# Immich Face Recognition Export Tool

[![Python 3.x](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Python tool to securely export face recognition data from your [Immich](https://immich.app/) photo management system into standard XMP sidecar files.

## 🌟 Features

- **🔍 Face Data Extraction**: Retrieves face recognition bounding boxes and labels directly from the Immich API.
- **🎯 Highly Compatible**: Generates standard XMP sidecar files utilizing MWG (Metadata Working Group) region tags, making them compatible with digiKam, XnView MP, Adobe Lightroom, and other photo management software.
- **🔄 Smart Coordinate Transformation**: Automatically handles image rotation/orientation EXIF data, converting Immich's visual coordinates back to the raw image's coordinate space.
- **📁 Preserves Directory Structure**: Output XMP files replicate your original photo library's folder structure for easy merging.
- **🔑 Secure Authentication**: Supports both Immich API Keys (recommended) and Email/Password login.
- **🚀 Flexible Processing Modes**: Run a direct memory-to-disk export (`--direct-xmp`) or use a two-stage process (saving intermediate JSON) to review data before generating XMP files.
- **🗂️ Targeted Exports**: Filter exported assets by specific `album-id` or `library-id`.
- **🚀 Efficient Processing** - Smart batch processing, supports large photo libraries

## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/peemot/immich-export.git
cd immich-export
```

### 2. Install Dependencies
```bash
pip install requests urllib3
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

## 💻 Usage

The script is highly customizable through command-line arguments.

### Processing Modes

**1. Default Workflow (Two-Stage)**
Pulls data from Immich, saves it as a JSON file, and then generates XMP sidecars from that JSON.
```bash
python export_face.py
```

**2. Direct Export (Fastest)**
Queries Immich and immediately writes XMP sidecars to disk, skipping the intermediate JSON file.
```bash
python export_face.py --direct-xmp
```

**3. Run Stages Independently**
```bash
# Stage 1: Export only the JSON file
python export_face.py --stage1-only

# Stage 2: Generate XMP files from a previously downloaded JSON file
python export_face.py --stage2-only --json-file path/to/export.json
```

### Filtering and Options
```bash
# Filter by a specific Album or Library
python export_face.py --album-id "your-album-uuid"
python export_face.py --library-id "your-library-uuid"

# Specify custom output directories (overrides config values)
python export_face.py --json-dir ./my_jsons --xmp-dir ./my_xmps

# Limit processed assets (great for testing)
python export_face.py --max-assets 50

# Enable debug logging for detailed outputs and troubleshooting
python export_face.py --debug
```

## 📁 Output Structure

The tool securely generates files in the configured output directories without modifying your original library.

### JSON Export (Stage 1)
```text
json_exports/
├── immich_faces_export_20260313_143022.json  # Complete API payload export
```

### XMP Sidecars (Stage 2 or Direct Mode)
The `xmp_sidecars` directory mirrors your Immich library's internal folder structure.
```text
xmp_sidecars/
├── export_summary.json         # Overall statistics (faces processed, people found)
├── admin/
│   └── 2023/
│       └── photo1.jpg.xmp      # XMP sidecar file
└── family/
    └── 2024/
        └── photo2.jpg.xmp
```
*To apply the XMP tags, you simply copy/merge this output directory into your actual photo library root.*

## ⚙️ Configuration Reference

| JSON Path | Environment Variable | Default | Description |
|-----------|----------------------|---------|-------------|
| `immich.base_url` | `IMMICH_BASE_URL` | - | Your Immich server URL |
| `immich.api_key` | `IMMICH_API_KEY` | - | API key (Requires `asset.read` permission) |
| `immich.email` | `IMMICH_EMAIL` | - | Login email (Fallback if no API key) |
| `immich.password` | `IMMICH_PASSWORD` | - | Login password (Fallback if no API key) |
| `settings.request_timeout`| `IMMICH_REQUEST_TIMEOUT`| `30` | Network request timeout in seconds |
| `settings.retry_attempts` | `IMMICH_RETRY_ATTEMPTS` | `3` | Retries for 5xx/429 network errors |
| `output.xmp_export_dir` | `OUTPUT_XMP_DIR` | `xmp_sidecars` | Target directory for generated `.xmp` files |
| `output.json_export_dir`| `OUTPUT_JSON_DIR` | `json_exports` | Target directory for intermediate JSON dumps |

## 🐛 Troubleshooting & Common Issues

**Q: Authentication failed?**
**A:** Double-check your server URL (ensure it doesn't end with `/api`). If using an API key, verify it has `asset.read` permissions. 

**Q: No XMP files were generated?**
**A:** Only assets that have recognized people/faces in Immich will generate an XMP file. Ensure Immich's machine learning face detection has finished running on your library.

**Q: Output directory permission error or "Refusing to write outside output directory"?**
**A:** The script includes path-traversal protection to prevent overwriting critical system files. It will explicitly refuse to write `.xmp` files outside the designated output directory. Ensure you have proper write permissions for the output folder.

**Q: How does the tool handle large libraries?**
**A:** The script automatically paginates processing (200 assets at a time) and handles network retries automatically. Processing progress will be logged to the console in real-time.

**Q: Does this modify my original photos?**
**A:** No. This script is strictly read-only against your Immich server. It generates *separate* `.xmp` sidecar files in a distinct output directory.

## 📖 Detailed Documentation

For complete project documentation, please refer to [IFLOW.md](IFLOW.md), which includes:

- 🔧 Complete configuration instructions
- 🛠️ Development guide
- 🔍 Troubleshooting
- 🛡️ Security best practices
- 📋 Detailed feature descriptions

## 📄 License

MIT License - See the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Issues, bug reports, and Pull Requests are always welcome!

---

**⭐ If this tool saved you time, please consider giving it a Star on GitHub!**
