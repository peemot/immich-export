# Immich Face Recognition Export Tool

[![Python 3.x](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Python tool to export face recognition data from Immich photo management system to standard XMP format files.

## 🌟 Features

- **🔍 Face Recognition Data Export** - Retrieve complete face recognition data from Immich API
- **📁 Directory Structure Preservation** - Output files maintain original photo directory structure
- **🎯 Compatible** - Generate standard XMP sidecar files, compatible with DigiKam, XnView MP and other software
- **🔑 Multiple Auth Methods** - Support for both API Key (recommended) and Email/Password authentication
- **⚙️ Flexible Configuration** - Support JSON configuration files and environment variables
- **🗂️ Library & Album Filtering** - Export faces only from specific albums or libraries
- **📊 Detailed Statistics** - Generate comprehensive export statistics reports
- **🚀 Efficient Processing** - Smart batch processing, supports large photo libraries
- **🎯 Debug-Friendly** - Support limiting processed assets quantity for testing

## 🚀 Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/peemot/immich-export.git
cd immich-export
```

### 2. Install Dependencies
```bash
pip install requests urllib3
```

### 3. Configuration Setup

#### Method 1: Using Config File (Recommended)
```bash
# Copy template file
cp config.json.template config.json

# Edit configuration file
nano config.json
```

Fill in your Immich server information in `config.json`. You can use an **API Key** (recommended) OR **Email/Password**:
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
*(Note: If you provide an `api_key`, the script will use that and ignore the email/password.)*

#### Method 2: Using Environment Variables
```bash
export IMMICH_BASE_URL="https://your-immich-server.com"
export IMMICH_API_KEY="your-api-key"
# OR use email/password:
# export IMMICH_EMAIL="your-email@example.com"
# export IMMICH_PASSWORD="your-password"
```

### 4. Run Export

#### Basic Usage
```bash
# Run complete workflow (export to JSON then generate XMP)
python export_face.py
```

#### Advanced Usage
```bash
# Run direct one-stage export (query Immich and write XMP sidecars directly, skipping JSON)
python export_face.py --direct-xmp

# Run only Stage 1: Export to JSON file
python export_face.py --stage1-only

# Run only Stage 2: Generate XMP from existing JSON file
python export_face.py --stage2-only --json-file path/to/export.json

# Filter by a specific album or library
python export_face.py --album-id "your-album-uuid"
python export_face.py --library-id "your-library-uuid"

# Limit processed assets for testing (e.g., process only 50 assets)
python export_face.py --max-assets 50

# Specify custom output directories
python export_face.py --json-dir my_json_exports --xmp-dir my_xmp_files
```

## 📖 Detailed Documentation

For complete project documentation, please refer to [IFLOW.md](IFLOW.md), which includes:

- 🔧 Complete configuration instructions
- 🛠️ Development guide
- 🔍 Troubleshooting
- 🛡️ Security best practices
- 📋 Detailed feature descriptions

## 📁 Output Structure

After running the script, the following will be generated in the configured output directories:

### JSON Export (Stage 1)
```
json_exports/
├── immich_faces_export_20260313_143022.json  # Complete face data export
└── ...
```

### XMP Files (Stage 2 or Direct Mode)
```
xmp_sidecars/
├── export_summary.json         # Export statistics report
├── your-photo1.jpg.xmp         # XMP sidecar file
├── your-photo2.jpg.xmp
└── subdirectory/
    ├── photo3.jpg.xmp
    └── photo4.jpg.xmp
```

## 🎯 Use Cases

- **📸 Photo Management Migration** - Maintain face recognition data when migrating from Immich to DigiKam
- **🔖 Metadata Backup** - Backup face recognition information in standard XMP format
- **👥 People Tag Management** - Sync people tags between different photo management software
- **📊 Data Analysis** - Analyze person appearance frequency and distribution in photo libraries
- **🔄 Workflow Flexibility** - Choose between Direct Export or Two-Stage processing
- **🗂️ Targeted Exports** - Process specific subsets of your library using Album or Library IDs
- **🧪 Development & Testing** - Limit processed assets for debugging and development purposes

## 🔧 Configuration Options

| Configuration Item | Environment Variable | Default Value | Description |
|--------------------|---------------------|---------------|-------------|
| `immich.base_url` | `IMMICH_BASE_URL` | - | Immich server address |
| `immich.api_key` | `IMMICH_API_KEY` | - | API key (Requires `asset.read` permission) |
| `immich.email` | `IMMICH_EMAIL` | - | Login email (if not using API key) |
| `immich.password` | `IMMICH_PASSWORD` | - | Login password (if not using API key) |
| `settings.request_timeout` | `IMMICH_REQUEST_TIMEOUT` | 30 | API request timeout (seconds) |
| `settings.retry_attempts` | `IMMICH_RETRY_ATTEMPTS` | 3 | Number of retry attempts on 5xx/429 errors |
| `output.xmp_export_dir` | `OUTPUT_XMP_DIR` | xmp_sidecars | XMP output directory |
| `output.json_export_dir` | `OUTPUT_JSON_DIR` | json_exports | JSON export directory |

## 🛠️ Development

### Code Checking
```bash
# Syntax check
python -m py_compile export_face.py

# Import test
python -c "from export_face import ConfigLoader; print('OK')"
```

### Configuration Testing
```bash
# Test environment variable configuration
export IMMICH_API_KEY="test_key"
python -c "from export_face import ConfigLoader; config = ConfigLoader(); print('Config OK')"
```

## 🐛 Common Issues

### Q: Authentication failed?
**A:** Check if server address is correct. If using an API key, ensure it has `asset.read` permissions. If using email/password, ensure they are correct and the server is accessible.

### Q: No XMP files generated?
**A:** Confirm that your photos have been processed for face recognition in Immich. Only photos containing face data will generate XMP files.

### Q: Output directory permission error or "Refusing to write outside output directory"?
**A:** Ensure the script has permission to write to the configured output directory. The script has security protections built-in to prevent malicious path traversal, so it will refuse to save files outside the designated XMP directory.

### Q: How to handle large photo libraries?
**A:** The script automatically paginates processing (200 assets at a time) and handles network retries automatically. Processing progress will be displayed in real-time. 

### Q: What is the difference between Direct Export and Two-Stage Processing?
**A:** 
*   **Direct Export (`--direct-xmp`)**: The fastest method. It pulls data from Immich and immediately writes XMP sidecars. No intermediate JSON is saved.
*   **Two-Stage Processing (`--stage1-only` and `--stage2-only`)**: It first saves a large `.json` file containing all API responses. You can then review/modify this JSON file and generate XMP files from it later. It's the default behavior if no flags are passed.

## 📄 License

MIT License - See [LICENSE](LICENSE) file for details

## 🤝 Contributing

Issues and Pull Requests are welcome!

## 📞 Contact

For questions or suggestions, please create an issue on GitHub.

---

**⭐ If this project is helpful to you, please give it a Star!**
```
