# Immich Scripts - IFLOW Project Documentation

## 📑 Project Overview

This documentation covers the Python script designed to export face recognition data from the [Immich](https://immich.app/) photo management system into DigiKam-compatible XMP format sidecar files. 

### ✨ Core Features
- **Smart Data Extraction**: Uses Immich's advanced `/search/metadata` API to fetch fully populated assets (including EXIF and People data) in efficient, paginated batches.
- **DigiKam & Standard Compatibility**: Converts face data into MWG (Metadata Working Group) compliant XMP sidecar files, supported by digiKam, XnView MP, Adobe Lightroom, etc.
- **Coordinate Translation**: Translates Immich's visual ML bounding boxes back into raw, unrotated EXIF coordinates, perfectly matching the original image canvas.
- **EXIF Preservation**: Extracts and embeds Immich EXIF data (camera make/model, GPS, exposure, dates) directly into the generated XMP files.
- **Directory Mirroring**: Output XMP files replicate your original photo library's folder structure for seamless merging.

### ⚙️ Flexibility & Execution
- **Multiple Execution Modes**: Support for Direct memory-to-disk XMP generation, or a Two-stage (JSON dump -> XMP generation) process for auditing.
- **Granular Filtering**: Filter exports by specific Album IDs or Library IDs.
- **Built-in Configuration Loader**: No external dependencies needed to parse standard JSON configs or environment variables.

### 🔐 Security & Safety
- **Authentication**: Supports secure API Key authentication (recommended) or legacy Email/Password.
- **Path Traversal Protection**: Actively neutralizes malicious or malformed `originalPath` references to prevent writing files outside the designated output directory.

---

## 📂 Project Structure

```text
/Users/username/immich_scripts/
├── export_face.py           # Main export script with built-in config loader
├── config.json.template     # Configuration template file
├── json_exports/            # Default directory for Stage-1 JSON dumps
├── xmp_sidecars/            # Default directory for generated XMP sidecar files
│   ├── export_summary.json  # Auto-generated export statistics report
│   └── myphoto/             # Mirrored sample photo directory structure
├── .gitignore               # Git ignore file configuration (includes config.json)
└── IFLOW.md                 # Project documentation (this file)
```

---

## 🛠️ Configuration Management

The script uses a flexible configuration hierarchy. Priority is evaluated as follows:
1. **Command Line Arguments** (Highest priority for paths and execution logic)
2. **Environment Variables** (Highest priority for credentials & server settings)
3. **`config.json`** file
4. **Built-in defaults** (Lowest priority)

### Setup Instructions

1. **Copy the template file:**
   ```bash
   cp config.json.template config.json
   ```

2. **Edit `config.json` with your actual values:**
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
   > **Note:** Using an `api_key` is highly recommended. If provided, `email` and `password` are ignored. The API key must have at least `asset.read` permissions.

3. **Or use Environment Variables (ideal for servers/Docker):**
   * `IMMICH_BASE_URL` mapped to `immich.base_url`
   * `IMMICH_API_KEY` mapped to `immich.api_key`
   * `IMMICH_EMAIL` mapped to `immich.email`
   * `IMMICH_PASSWORD` mapped to `immich.password`
   * `IMMICH_REQUEST_TIMEOUT` mapped to `settings.request_timeout`
   * `IMMICH_RETRY_ATTEMPTS` mapped to `settings.retry_attempts`
   * `OUTPUT_XMP_DIR` mapped to `output.xmp_export_dir`
   * `OUTPUT_JSON_DIR` mapped to `output.json_export_dir`

---

## 🚀 Usage Instructions

### 1. Basic Workflow (Two-Stage Default)
By default, the script fetches data, saves a large JSON payload, and then builds XMP files from that JSON.
```bash
python export_face.py
```
*Outputs: XMP sidecars preserving folder structure, and an `export_summary.json` statistics report.*

### 2. Direct Export (Fastest)
Queries the Immich API and writes XMP sidecars directly to disk, skipping the intermediate JSON step.
```bash
python export_face.py --direct-xmp
```

### 3. Split Two-Stage Execution
If you want to save the API dump to JSON first, manually review it, and then generate XMPs later:
```bash
# Stage 1: Export only the JSON
python export_face.py --stage1-only

# Stage 2: Generate XMPs from the saved JSON
python export_face.py --stage2-only --json-file path/to/export.json
```

### 4. Filtering and Custom Paths
You can target specific albums/libraries, limit assets for testing, or override output folders.
```bash
# Filter by a specific album or library
python export_face.py --album-id "your-album-uuid"
python export_face.py --library-id "your-library-uuid"

# Specify custom output directories
python export_face.py --json-dir my_json_exports --xmp-dir my_xmp_files

# Process only 50 assets (useful for debugging)
python export_face.py --max-assets 50

# Enable debug logging for detailed console outputs
python export_face.py --debug
```

---

## 👨‍💻 Development Conventions & Commands

### Code Style
- Written in Python 3.x using native type annotations (`typing` module).
- Follows PEP 8 naming conventions (lowercase with underscores for variables/functions, CamelCase for classes).
- Includes structured error handling: try-except blocks for API exceptions, JSON decoding, and IO operations.

### Testing and Validation
Verify your setup or test the script independently using these commands:

```bash
# Check Python syntax
python -m py_compile export_face.py

# Run basic import test to verify ConfigLoader logic
python -c "from export_face import ConfigLoader; print('Config loader OK')"

# Test environment variable overrides
python -c "
import os
os.environ['IMMICH_EMAIL'] = 'test@example.com'
os.environ['IMMICH_PASSWORD'] = 'testpass'
from export_face import ConfigLoader
config = ConfigLoader()
print('Environment config test OK')
"
```

---

## 🛡️ Security Best Practices & Important Notes

1. **Configuration Security:** Never commit `config.json` to version control (ensure it stays in `.gitignore`).
2. **Sensitive Data Protection:** Use Environment Variables for production deployments to avoid credential leakage.
3. **Template Usage:** Keep `config.json.template` updated with new options to act as configuration documentation. Always copy it to `config.json` before editing.
4. **Path Traversal Shield:** The script strictly enforces paths. If an Immich asset claims to originate from a system-level folder (e.g., `../../etc`), the script neutralizes the traversal attempts and enforces containment within your defined `xmp-dir`.
5. **API Limits & Retries:** The script paginates at 200 assets per request and auto-retries on 429/5xx status codes. Be mindful of total API load on lower-powered Immich host servers.

---

## 🆘 Troubleshooting

* **Authentication Failed:** 
  Ensure your server URL does *not* end with `/api` (the script handles appending `/api`). If using an API key, confirm it is active and granted the `asset.read` scope.
* **Empty XMP Files / Missing Tags:** 
  Ensure your photos have been fully processed for Face Recognition in the Immich administration dashboard. Only assets containing face data will generate an XMP file.
* **Refusing to Write Outside Directory:** 
  The script guards against path traversal vulnerabilities. If an Immich asset original path points abnormally outside expected root trees, it will be caught and aborted for that file. Ensure you have write permissions to the targeted output directory.
* **API Call Failed / Timeout:** 
  Check network connection and server status. You can increase `IMMICH_REQUEST_TIMEOUT` and `IMMICH_RETRY_ATTEMPTS` via env vars or `config.json` if your server is slow to respond.
* **Config File Not Found:** 
  Ensure you copied `config.json.template` to `config.json`. Alternatively, rely completely on Environment Variables.
* **Script is silent or stuck:**
  Run the script with `--debug` to get verbose real-time logging of asset processing and pagination steps.

---

## 🔄 Recent Changes

- **Added debug logging:** Use `--debug` to get verbose real-time logging
- **Added API Key Authentication:** Script now favors API keys over email/password, vastly improving security.
- **Added Direct Export Mode:** Skip JSON generation entirely using the `--direct-xmp` flag for faster local syncs.
- **Added Granular Targeting:** Support for `--album-id` and `--library-id` CLI arguments.
- **Added Asset Limiting:** Included a `--max-assets` argument for safe testing of large libraries.
- **Dynamic Output Configuration:** Output paths are fully controllable via JSON (`output.json_export_dir` / `output.xmp_export_dir`) or CLI (`--json-dir`, `--xmp-dir`).
- **Improved API Efficiency:** Utilizing the `/search/metadata` endpoint allows fetching EXIF and Face bounding boxes inside the same batch, severely reducing overhead API calls.
