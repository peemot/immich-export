# Immich Scripts - IFLOW Project Documentation

## Project Overview

This is a Python script for the Immich photo management system, used to export face recognition data from Immich into DigiKam-compatible XMP format files.

**Main Features:**
- Retrieve photo assets and face recognition data from Immich API
- Convert face data into DigiKam-compatible XMP sidecar files
- Support batch processing and directory structure preservation
- Support for both API Key (recommended) and Email/Password authentication
- Provide configuration file management and environment variable support
- Flexible execution modes (Direct XMP generation or Two-stage JSON->XMP)
- Filter exports by specific Album or Library IDs
- Built-in configuration loader (no separate config file needed)
- Coordinate Translation: Translates Immich's visual ML bounding boxes back to raw, unrotated EXIF coordinates
- EXIF Preservation: Extracts and embeds Immich EXIF data directly into the generated XMP files.

**Core Technologies:**
- Python 3.x
- Requests library for API calls
- JSON configuration management
- XMP metadata format processing

## Project Structure

```text
/Users/username/immich_scripts/
├── export_face.py           # Main export script with built-in configuration loader
├── config.json.template     # Configuration template file
├── json_exports/            # Output directory for Stage-1 JSON dumps
├── xmp_sidecars/            # Output directory (XMP sidecar files)
│   └── myphoto/             # Sample photo directory structure
├── .gitignore               # Git ignore file configuration (includes config.json)
└── IFLOW.md                 # Project documentation (this file)
```

## Core Files Description

### export_face.py
The main export script that includes:
- Built-in configuration loader supporting JSON files and environment variables
- Immich API authentication (API Key or Email/Password)
- Batch retrieval of photo asset IDs with optional album/library filtering
- Get detailed face recognition data
- Generate DigiKam-compatible XMP sidecar files
- Preserve original directory structure
- Generate export statistics report
- Support for CLI arguments to control flow (`--direct-xmp`, `--stage1-only`, etc.)

**Configuration Class Features:**
- Load configuration from JSON files
- Support environment variable override
- Configuration validation and default value handling
- Support nested configuration path access

### config.json.template
Configuration template file that provides:
- Example configuration structure
- All available configuration options
- Default values and descriptions
- Copy this file to `config.json` and customize for your needs

**Main Configuration Items:**
- `immich.base_url`: Immich server address
- `immich.api_key`: API key for authentication (Requires `asset.read` permissions)
- `immich.email`: Login email (used if API key is not provided)
- `immich.password`: Login password (used if API key is not provided)
- `settings.request_timeout`: Request timeout in seconds (default: 30)
- `settings.retry_attempts`: Retry attempts on failure (default: 3)
- `output.xmp_export_dir`: XMP output directory (default: "xmp_sidecars")
- `output.json_export_dir`: JSON export directory (default: "json_exports")

## Configuration Management

### Configuration Security
- **config.json**: Actual configuration file (git-ignored for security)
- **config.json.template**: Template file with example values
- **Environment Variables**: Alternative to config.json for sensitive data

### Setup Instructions

1. **Copy the template file:**
   ```bash
   cp config.json.template config.json
   ```

2. **Edit config.json with your actual values:**
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
   *Note: Using an `api_key` is recommended. If provided, `email` and `password` are ignored.*

3. **Or use environment variables (recommended for server deployments):**
   ```bash
   export IMMICH_BASE_URL="https://your-immich-server.com"
   export IMMICH_API_KEY="your-api-key"
   export OUTPUT_XMP_DIR="my_xmp_files"
   ```

### Configuration Priority
1. Command Line Arguments (for execution logic and paths)
2. Environment variables (highest priority for credentials/settings)
3. config.json file
4. Built-in defaults (lowest priority)

## Usage Instructions

### 1. Basic Workflow (Two-Stage Default)
By default, the script fetches data, saves a large JSON payload, and then builds XMP files from it.
```bash
python export_face.py
```

After export completion, the configured output directory will contain:
- XMP sidecar files (preserving original directory structure)
- `export_summary.json` export statistics report

### 2. Direct Export (Fastest)
Query the Immich API and write XMP sidecars directly, skipping the intermediate JSON step.
```bash
python export_face.py --direct-xmp
```

### 3. Filtering and Limits
You can target specific albums/libraries, or limit the number of assets for testing.
```bash
# Target a specific album
python export_face.py --album-id "your-album-uuid"

# Target a specific library
python export_face.py --library-id "your-library-uuid"

# Process only 50 assets (useful for debugging)
python export_face.py --max-assets 50
```

### 4. Split Two-Stage Execution
If you want to save the API dump to JSON first, manually review it, and then generate XMPs later:
```bash
# Stage 1: Export only the JSON
python export_face.py --stage1-only

# Stage 2: Generate XMPs from the saved JSON
python export_face.py --stage2-only --json-file path/to/export.json
```

## Development and Runtime Commands

### Basic Runtime
```bash
# Run main script directly
python export_face.py

# The script will automatically load config.json or use environment variables
```

### Testing and Validation
```bash
# Check Python syntax
python -m py_compile export_face.py

# Run basic import test
python -c "from export_face import ConfigLoader; print('Config loader OK')"

# Test with sample configuration
python -c "
import os
os.environ['IMMICH_EMAIL'] = 'test@example.com'
os.environ['IMMICH_PASSWORD'] = 'testpass'
from export_face import ConfigLoader
config = ConfigLoader()
print('Environment config test OK')
"
```

## Development Conventions

### Code Style
- Use Python type annotations
- Follow PEP 8 naming conventions
- Functions and variables use lowercase with underscores
- Class names use camel case

### Error Handling
- Use try-except for API call exceptions
- Provide detailed error information and logs
- Support retry mechanism and timeout settings

### Configuration Management
- Built-in configuration loader (no separate file needed)
- Support both JSON files and environment variables
- Provide reasonable default values
- Configuration validation and error prompts
- Template file for easy setup

### Output Format
- XMP files use UTF-8 encoding
- Maintain compatibility with DigiKam
- Generate detailed export statistics

## Important Notes

1. **Configuration Security**: Never commit config.json to version control
2. **API Limits**: Pay attention to Immich API call frequency limits
3. **Directory Structure**: Output preserves original photo directory structure
4. **Face Data**: Only photos containing face data will generate XMP files
5. **Authentication**: Ensure login credentials are correct
6. **Template Usage**: Always copy config.json.template to config.json before editing
7. **Permissions**: Ensure your API key has at least `asset.read` permissions.

## Security Best Practices

### Sensitive Data Protection
- config.json is automatically git-ignored
- Use environment variables for production deployments
- Never share your config.json file
- Rotate passwords regularly

### Template Management
- Keep config.json.template updated with new options
- Use template as documentation for available settings
- Document any configuration changes in your deployment

## Troubleshooting

### Common Issues
- **Authentication Failed**: If using an API key, ensure it is active and has `asset.read`. If using email/password, verify login functionality on the web portal.
- **Empty XMP Files / Missing Tags**: Ensure your photos have been fully processed for Face Recognition in the Immich administration dashboard.
- **Refusing to Write Outside Directory**: The script guards against path traversal vulnerabilities. If an Immich asset original path points abnormally outside expected root trees, it will be caught and aborted for that file.
- **API Call Failed**: Check network connection and server status
- **Directory Creation Failed**: Check write permissions for output directory
- **Configuration Errors**: Verify config.json format or environment variables
- **Config File Not Found**: Ensure you copied config.json.template to config.json

### Log Information
The script outputs detailed processing information, including:
- Configuration loading status
- API call progress
- File processing statistics
- Error and warning messages


## Recent Changes
- **Added API Key Authentication**: Script now favors API keys over email/password, improving security.
- **Added Direct Export Mode**: Skip JSON generation using the `--direct-xmp` flag for faster local syncs.
- **Added Granular Targeting**: Support for `--album-id` and `--library-id` CLI arguments.
- **Added Asset Limiting**: Included a `--max-assets` argument for safe testing.
- **Updated Output Configuration**: Output paths are dynamically controllable via JSON `output.json_export_dir` / `output.xmp_export_dir` or via CLI (`--json-dir`, `--xmp-dir`).
```
