# Immich Face Recognition Export Tool

[![Python 3.x](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Python tool to export face recognition data from Immich photo management system to DigiKam-compatible XMP format files.

## 🌟 Features

- **🔍 Face Recognition Data Export** - Retrieve complete face recognition data from Immich API
- **📁 Directory Structure Preservation** - Output files maintain original photo directory structure
- **🎯 DigiKam Compatible** - Generate standard XMP sidecar files, fully compatible with DigiKam
- **⚙️ Flexible Configuration** - Support JSON configuration files and environment variables
- **🔒 Security First** - Sensitive configurations automatically git-ignored, protecting your authentication info
- **📊 Detailed Statistics** - Generate comprehensive export statistics reports
- **🚀 Efficient Processing** - Smart batch processing, supports large photo libraries

## 🚀 Quick Start

### 1. Clone Repository
```bash
git clone https://github.com/yuhuan417/immich-scripts.git
cd immich-scripts
```

### 2. Install Dependencies
```bash
pip install requests
```

### 3. Configuration Setup

#### Method 1: Using Config File (Recommended)
```bash
# Copy template file
cp config.json.template config.json

# Edit configuration file
ano config.json
```

Fill in your Immich server information in `config.json`:
```json
{
  "immich": {
    "base_url": "https://your-immich-server.com",
    "email": "your-email@example.com",
    "password": "your-password"
  },
  "settings": {
    "request_timeout": 30,
    "retry_attempts": 3
  },
  "output": {
    "digikam_xmp_dir": "digikam_xmp_sidecars"
  }
}
```

#### Method 2: Using Environment Variables
```bash
export IMMICH_BASE_URL="https://your-immich-server.com"
export IMMICH_EMAIL="your-email@example.com"
export IMMICH_PASSWORD="your-password"
```

### 4. Run Export
```bash
python export_face.py
```

## 📖 Detailed Documentation

For complete project documentation, please refer to [IFLOW.md](IFLOW.md), which includes:

- 🔧 Complete configuration instructions
- 🛠️ Development guide
- 🔍 Troubleshooting
- 🛡️ Security best practices
- 📋 Detailed feature descriptions

## 📁 Output Structure

After running the script, the following will be generated in the configured output directory:

```
digikam_xmp_sidecars/
├── export_summary.json          # Export statistics report
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

## 🔧 Configuration Options

| Configuration Item | Environment Variable | Default Value | Description |
|--------------------|---------------------|---------------|-------------|
| `immich.base_url` | `IMMICH_BASE_URL` | - | Immich server address |
| `immich.email` | `IMMICH_EMAIL` | - | Login email |
| `immich.password` | `IMMICH_PASSWORD` | - | Login password |
| `settings.request_timeout` | `IMMICH_REQUEST_TIMEOUT` | 30 | API request timeout (seconds) |
| `settings.retry_attempts` | `IMMICH_RETRY_ATTEMPTS` | 3 | Number of retry attempts |
| `output.digikam_xmp_dir` | `OUTPUT_DIGIKAM_XMP_DIR` | digikam_xmp_sidecars | Output directory |

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
export IMMICH_EMAIL="test@example.com"
export IMMICH_PASSWORD="testpass"
python -c "from export_face import ConfigLoader; config = ConfigLoader(); print('Config OK')"
```

## 🐛 Common Issues

### Q: Authentication failed?
**A:** Check if server address, email, and password are correct, and ensure the server is accessible.

### Q: No XMP files generated?
**A:** Confirm that your photos have been processed for face recognition in Immich. Only photos containing face data will generate XMP files.

### Q: Output directory permission error?
**A:** Ensure the script has permission to create and write to the configured output directory.

### Q: How to handle large photo libraries?
**A:** The script automatically paginates processing and supports large photo libraries. Processing progress will be displayed in real-time.

## 📄 License

MIT License - See [LICENSE](LICENSE) file for details

## 🤝 Contributing

Issues and Pull Requests are welcome!

## 📞 Contact

For questions or suggestions, please create an issue on GitHub.

---

**⭐ If this project is helpful to you, please give it a Star!**