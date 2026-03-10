#!/usr/bin/env python3
"""
Face recognition export script for Immich.
Uses search API to get face data.
Exports face recognition data to XMP format, supported by digiKam, XnView MP and others.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import os
import argparse
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape


class ConfigLoader:
    """Configuration loader that supports JSON files and environment variables."""
    
    def __init__(self, config_file: str = "config.json"):
        """Initialize configuration loader."""
        self.config_file = config_file
        self.config_data = {}
        self.load_config()
    
    def load_config(self) -> None:
        """Load configuration from file and environment variables."""
        # Load from JSON file if it exists
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config_data = json.load(f)
                print(f"✅ Configuration loaded from {self.config_file}")
            except (json.JSONDecodeError, IOError) as e:
                print(f"⚠️  Error loading config file {self.config_file}: {e}")
                print("   Using environment variables and defaults")
                self.config_data = {}
        else:
            print(f"⚠️  Config file {self.config_file} not found")
            print("   Using environment variables and defaults")
        
        # Override with environment variables if present
        self._load_from_env()
    
    def _load_from_env(self) -> None:
        """Load configuration from environment variables."""
        env_mappings = {
            'IMMICH_BASE_URL': ['immich', 'base_url'],
            'IMMICH_API_KEY': ['immich', 'api_key'],
            'IMMICH_EMAIL': ['immich', 'email'],
            'IMMICH_PASSWORD': ['immich', 'password'],
            'IMMICH_REQUEST_TIMEOUT': ['settings', 'request_timeout'],
            'IMMICH_RETRY_ATTEMPTS': ['settings', 'retry_attempts'],
            'OUTPUT_XMP_DIR': ['output', 'xmp_export_dir']
        }
        
        for env_var, config_path in env_mappings.items():
            env_value = os.getenv(env_var)
            if env_value:
                self._set_nested_value(self.config_data, config_path, env_value)
                print(f"✅ Loaded {env_var} from environment")
    
    def _set_nested_value(self, data: Dict[str, Any], path: list, value: str) -> None:
        """Set nested dictionary value from path list."""
        current = data
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        # Convert numeric values
        if path[-1] in ['request_timeout', 'retry_attempts']:
            try:
                current[path[-1]] = int(value)
            except ValueError:
                current[path[-1]] = value
        else:
            current[path[-1]] = value
    
    def get(self, path: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'immich.base_url')."""
        keys = path.split('.')
        current = self.config_data
        
        try:
            for key in keys:
                current = current[key]
            return current
        except (KeyError, TypeError):
            return default
    
    def get_immich_config(self) -> Dict[str, str]:
        """Get Immich connection configuration."""
        return {
            'base_url': self.get('immich.base_url', 'https://www.blahblah.com'),
            'api_key': self.get('immich.api_key', ''),
            'email': self.get('immich.email', ''),
            'password': self.get('immich.password', '')
        }
    
    def get_output_config(self) -> Dict[str, str]:
        """Get output configuration."""
        return {
            'xmp_export_dir': self.get('output.xmp_export_dir', 'xmp_sidecars'),
            'json_export_dir': self.get('output.json_export_dir', 'json_exports')
        }
    
    def get_settings_config(self) -> Dict[str, Any]:
        """Get general settings configuration."""
        return {
            'request_timeout': self.get('settings.request_timeout', 30),
            'retry_attempts': self.get('settings.retry_attempts', 3)
        }
    
    def validate_immich_config(self) -> bool:
        """Validate that required Immich configuration is present."""
        immich_config = self.get_immich_config()
        
        if immich_config['base_url'] == 'https://www.blahblah.com':
            print("❌ Configuration error: Please update the Immich server URL")
            print("   Set it in config.json or use environment variable:")
            print("   IMMICH_BASE_URL")
            return False

        has_api_key = bool(immich_config['api_key'])
        has_credentials = bool(immich_config['email'] and immich_config['password'])

        if not has_api_key and not has_credentials:
            print("❌ Configuration error: API key OR Email and password are required")
            print("   Please set them in config.json or use environment variables:")
            print("   IMMICH_API_KEY or (IMMICH_EMAIL and IMMICH_PASSWORD)")
            print("   Note: If using an API key, it must have at least 'asset.read' permission.")
            return False
        
        return True
    
    def print_config_summary(self) -> None:
        """Print a summary of loaded configuration."""
        print("\n📋 Configuration Summary:")
        print(f"   Server URL: {self.get('immich.base_url')}")
        if self.get('immich.api_key'):
            print("   Auth Method: API Key")
        else:
            print(f"   Auth Method: Email ({self.get('immich.email')})")
        print(f"   Timeout: {self.get('settings.request_timeout')}s")
        print(f"   Retry Attempts: {self.get('settings.retry_attempts')}")


# Global config loader instance
config = ConfigLoader()

# Get configuration from config file
immich_config = config.get_immich_config()
IMMICH_BASE_URL = immich_config['base_url'].rstrip('/')
IMMICH_API_BASE = f"{IMMICH_BASE_URL}/api"
output_config = config.get_output_config()
settings_config = config.get_settings_config()

REQUEST_TIMEOUT = settings_config['request_timeout']
RETRY_ATTEMPTS = settings_config['retry_attempts']

DEFAULT_HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

def create_http_session(retries: int) -> requests.Session:
    """Create a requests session with automatic retries."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=1,  # Wait 1s, 2s, 4s between retries
        status_forcelist=[429, 500, 502, 503, 504],
        # By default, urllib3 doesn't retry POST requests but Immich search API uses POST.
        allowed_methods=["HEAD", "GET", "POST", "OPTIONS"] 
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

# Initialize the global session
http_session = create_http_session(RETRY_ATTEMPTS)


def api_request(method: str, path: str, *, token: str | None = None, **kwargs) -> requests.Response:
    headers = DEFAULT_HEADERS.copy()
    api_key = immich_config.get('api_key')
    
    if api_key:
        headers["x-api-key"] = api_key
    elif token:
        headers["Cookie"] = f"immich_access_token={token}"

    resp = http_session.request(
        method,
        f"{IMMICH_API_BASE}{path}",
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        **kwargs,
    )
    resp.raise_for_status()
    return resp


def authenticate(email: str, password: str) -> Optional[str]:
    """Authenticate with Immich API and return access token."""
    payload = {"email": email, "password": password}
    
    try:
        response = api_request("POST", "/auth/login", json=payload)
        return response.json()["accessToken"]
    except requests.exceptions.RequestException as e:
        print(f"Authentication failed: {e}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Failed to parse authentication response: {e}")
        return None


def _parse_orientation(orientation) -> int:
    """Parse Exif orientation strings or ints into a standard integer 1-8."""
    if isinstance(orientation, int):
        return orientation
        
    if isinstance(orientation, str):
        o_lower = str(orientation).lower()
        if any(x in o_lower for x in['6', 'right top', 'right-top', '90 cw']):
            return 6
        elif any(x in o_lower for x in['3', 'bottom right', '180']):
            return 3
        elif any(x in o_lower for x in['8', 'left bottom', 'left-bottom', '270 cw']):
            return 8
        elif any(x in o_lower for x in['2', 'top right', 'mirror horizontal']):
            return 2
        elif any(x in o_lower for x in ['4', 'bottom left', 'mirror vertical']):
            return 4
        elif '5' in o_lower:
            return 5
        elif '7' in o_lower:
            return 7
            
    return 1


def _calculate_unrotated_face_coords(face: Dict[str, Any], orientation_val: int, raw_w: int, raw_h: int):
    """Transform Immich visual face coordinates back to raw unrotated space."""
    ml_w = face.get('imageWidth')
    ml_h = face.get('imageHeight')

    # Fallback to visually rotated dimensions if ml_w/ml_h is missing
    if not ml_w or not ml_h:
        is_sideways = orientation_val in [5, 6, 7, 8]
        ml_w, ml_h = (raw_h, raw_w) if is_sideways else (raw_w, raw_h)

    # Normalize coordinates relative to the ML canvas (Visual Space)
    vis_x1 = face.get('boundingBoxX1', 0) / ml_w if ml_w else 0
    vis_y1 = face.get('boundingBoxY1', 0) / ml_h if ml_h else 0
    vis_x2 = face.get('boundingBoxX2', 0) / ml_w if ml_w else 0
    vis_y2 = face.get('boundingBoxY2', 0) / ml_h if ml_h else 0

    # Calculate Center and Size in Visual Space
    vis_cx = (vis_x1 + vis_x2) / 2.0
    vis_cy = (vis_y1 + vis_y2) / 2.0
    vis_fw = abs(vis_x2 - vis_x1)
    vis_fh = abs(vis_y2 - vis_y1)

    # --- INVERSE TRANSFORMATION ---
    raw_cx, raw_cy = vis_cx, vis_cy
    raw_fw, raw_fh = vis_fw, vis_fh

    if orientation_val == 2:    # Mirror horizontal
        raw_cx = 1.0 - vis_cx
    elif orientation_val == 3:  # Rotate 180
        raw_cx = 1.0 - vis_cx
        raw_cy = 1.0 - vis_cy
    elif orientation_val == 4:  # Mirror vertical
        raw_cy = 1.0 - vis_cy
    elif orientation_val == 5:  # Mirror horizontal and rotate 270 CW
        raw_cx, raw_cy = vis_cy, vis_cx
        raw_fw, raw_fh = vis_fh, vis_fw
    elif orientation_val == 6:  # Rotate 90 CW (Standard Portrait)
        raw_cx, raw_cy = vis_cy, 1.0 - vis_cx
        raw_fw, raw_fh = vis_fh, vis_fw
    elif orientation_val == 7:  # Mirror horizontal and rotate 90 CW
        raw_cx, raw_cy = 1.0 - vis_cy, 1.0 - vis_cx
        raw_fw, raw_fh = vis_fh, vis_fw
    elif orientation_val == 8:  # Rotate 270 CW (Inverse Portrait)
        raw_cx, raw_cy = 1.0 - vis_cy, vis_cx
        raw_fw, raw_fh = vis_fh, vis_fw

    return raw_cx, raw_cy, raw_fw, raw_fh


def create_xmp_content(asset_data: Dict[str, Any]) -> str:
    """Create XMP content for face recognition data with EXIF information."""

    people = asset_data.get("people") or []
    if not people:
        return ""

    exif_info = asset_data.get("exifInfo") or {}
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def xml_text(value: Any) -> str:
        """Escape text for safe XML embedding (also safe for attribute values)."""
        if value is None:
            return ""
        return _xml_escape(str(value), {'"': "&quot;", "'": "&apos;"})

    def exif_str(key: str, default: str = "") -> str:
        """Read EXIF value as string; returns default/empty for None."""
        val = exif_info.get(key, default)
        return "" if val is None else str(val)

    def xmp_date(date_str: str) -> str:
        """Ensure XMP date-time format (adds a dummy time if only date is present)."""
        if not date_str:
            return ""
        return date_str if "T" in date_str else f"{date_str}T12:00:00"

    def add_tag(lines: List[str], ns_tag: str, value: str, *, allow_empty: bool = False) -> None:
        """Append a simple <ns:Tag>value</ns:Tag> if value exists (or allow_empty)."""
        if value or allow_empty:
            lines.append(f"   <{ns_tag}>{xml_text(value)}</{ns_tag}>")

    # MWG regions must be relative to the UNROTATED (raw) image dimensions.
    raw_w = int(exif_info.get("exifImageWidth") or asset_data.get("width") or 0)
    raw_h = int(exif_info.get("exifImageHeight") or asset_data.get("height") or 0)
    orientation_val = _parse_orientation(exif_info.get("orientation"))

    lines: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0">',
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
        '  <rdf:Description rdf:about=""',
        '   xmlns:mwg-rs="http://www.metadataworkinggroup.com/schemas/regions/"',
        '   xmlns:dc="http://purl.org/dc/elements/1.1/"',
        '   xmlns:xmp="http://ns.adobe.com/xap/1.0/"',
        '   xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"',
        '   xmlns:exif="http://ns.adobe.com/exif/1.0/"',
        '   xmlns:tiff="http://ns.adobe.com/tiff/1.0/"',
        '   xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"',
        '   xmlns:Iptc4xmpCore="http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/"',
        '   xmlns:stDim="http://ns.adobe.com/xap/1.0/sType/Dimensions#"',
        '   xmlns:stArea="http://ns.adobe.com/xmp/sType/Area#"',
        f'   xmp:ModifyDate="{now}"',
        f'   xmp:MetadataDate="{now}">',
    ]

    # --- EXIF / TIFF (camera + lens) ---
    add_tag(lines, "tiff:Make", exif_str("make"))
    add_tag(lines, "tiff:Model", exif_str("model"))
    add_tag(lines, "exif:LensModel", exif_str("lensModel"))

    # --- Exposure settings ---
    add_tag(lines, "exif:FNumber", exif_str("fNumber"))
    add_tag(lines, "exif:ExposureTime", exif_str("exposureTime"))
    add_tag(lines, "exif:ISOSpeedRatings", exif_str("iso"))
    add_tag(lines, "exif:FocalLength", exif_str("focalLength"))

    # --- Image dimensions (raw / unrotated) ---
    if raw_w and raw_h:
        add_tag(lines, "tiff:ImageWidth", str(raw_w))
        add_tag(lines, "tiff:ImageLength", str(raw_h))
        add_tag(lines, "exif:ExifImageWidth", str(raw_w))
        add_tag(lines, "exif:ExifImageHeight", str(raw_h))

    # --- Dates ---
    add_tag(lines, "exif:DateTimeOriginal", xmp_date(exif_str("dateTimeOriginal")))
    add_tag(lines, "exif:DateTimeDigitized", xmp_date(exif_str("dateTimeDigitized")))

    # --- GPS + place names ---
    latitude = exif_str("latitude")
    longitude = exif_str("longitude")
    if latitude and longitude:
        add_tag(lines, "exif:GPSLatitude", latitude)
        add_tag(lines, "exif:GPSLongitude", longitude)

    add_tag(lines, "photoshop:City", exif_str("city"))
    add_tag(lines, "photoshop:State", exif_str("state"))
    add_tag(lines, "photoshop:Country", exif_str("country"))

    # --- File info ---
    file_name = asset_data.get("file_name", "") or ""
    add_tag(lines, "xmp:Identifier", file_name)
    lines.append("   <xmp:CreatorTool>Immich Face Export Tool</xmp:CreatorTool>")

    # --- General people keywords (dc:subject) ---
    names =[(p.get("name") or "").strip() for p in people]
    unique_people = sorted({name for name in names if name and name != "Unknown"})
        
    if unique_people:
        lines.extend(["   <dc:subject>", "    <rdf:Bag>"])
        for name in unique_people:
            lines.append(f"     <rdf:li>{xml_text(name)}</rdf:li>")
        lines.extend(["    </rdf:Bag>", "   </dc:subject>"])

    # --- Face Regions (MWG) ---
    lines.extend(
        [
            '   <mwg-rs:Regions rdf:parseType="Resource">',
            "    <mwg-rs:AppliedToDimensions",
            f'     stDim:w="{raw_w}"',
            f'     stDim:h="{raw_h}"',
            '     stDim:unit="pixel"/>',
            "    <mwg-rs:RegionList>",
            "    <rdf:Bag>",
        ]
    )

    def add_face_region(person_name: str, face: Dict[str, Any]) -> None:
        raw_cx, raw_cy, raw_fw, raw_fh = _calculate_unrotated_face_coords(
            face, orientation_val, raw_w, raw_h
        )
        lines.extend(
            [
                "     <rdf:li>",
                "      <rdf:Description",
                f'       mwg-rs:Name="{xml_text(person_name)}"',
                '       mwg-rs:Type="Face">',
                "       <mwg-rs:Area",
                f'        stArea:x="{raw_cx:.6f}"',
                f'        stArea:y="{raw_cy:.6f}"',
                f'        stArea:w="{raw_fw:.6f}"',
                f'        stArea:h="{raw_fh:.6f}"',
                '        stArea:unit="normalized"/>',
                "      </rdf:Description>",
                "     </rdf:li>",
            ]
        )

    for person in people:
        person_name = (person.get("name") or "Unknown").strip() or "Unknown"
        for face in (person.get("faces") or []):
            add_face_region(person_name, face)

    lines.extend(
        [
            "    </rdf:Bag>",
            "    </mwg-rs:RegionList>",
            "   </mwg-rs:Regions>",
            "  </rdf:Description>",
            " </rdf:RDF>",
            "</x:xmpmeta>",
            "",
        ]
    )

    return "\n".join(lines)


def save_xmp_sidecar(original_path: str, xmp_content: str, output_dir: str = "") -> bool:
    """Save XMP content to sidecar file, creating same directory structure in output_dir."""
    if not xmp_content.strip():
        return False  # Skip empty XMP
        
    try:
        # Strip leading slashes to prevent Python from jumping to the root drive.
        clean_path = str(original_path).lstrip('/\\')
        
        # Strip Windows drive letters (e.g., C:) if they somehow exist
        if len(clean_path) > 1 and clean_path[1] == ':':
            clean_path = clean_path[2:].lstrip('/\\')
            
        original_path_obj = Path(clean_path)
        filename = original_path_obj.name + '.xmp'
        
        if output_dir:
            output_base = Path(output_dir)
            
            # Because clean_path has no root, this is guaranteed to stay inside the output_base directory!
            rel_dir = original_path_obj.parent
            xmp_path = output_base / rel_dir / filename
            
            # Ensure the parent directories exist
            xmp_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            # If no output directory, use current directory
            xmp_path = Path(filename)
        
        with open(xmp_path, 'w', encoding='utf-8') as f:
            f.write(xmp_content)
        
        print(f"    Saved XMP sidecar: {xmp_path}")
        return True
        
    except IOError as e:
        print(f"    Error saving XMP file: {e}")
        return False


def process_assets_with_faces(access_token: str, max_assets: Optional[int] = None) -> List[Dict[str, Any]]:
    """Process assets and collect those with face recognition data efficiently."""
    processed_assets =[]
    page = 1
    
    print("Collecting assets with faces...")
    
    while True:
        # Check limit
        if max_assets is not None and len(processed_assets) >= max_assets:
            print(f"  Reached maximum asset limit: {max_assets}")
            break
            
        try:
            # withPeople=True: Ask Immich to ONLY return assets that have detected faces
            # withExif=True: Embed the EXIF data directly in this list response
            search_payload = {
                "page": page,
                "size": 200,          # Fetch 200 fully-populated assets at once
                "withPeople": True,
                "withExif": True
            }
            
            # The search endpoint acts as searchAssets when payload matches metadata parameters
            response = api_request("POST", "/search/metadata", token=access_token, json=search_payload)
            search_data = response.json()
            
            # Extract items list
            assets_data = search_data.get('assets', search_data)
            items = assets_data.get('items',[])
            
            if not items:
                print("  No more items available")
                break
                
            for item in items:
                # Stop if we hit the limit during the page loop
                if max_assets is not None and len(processed_assets) >= max_assets:
                    break
                    
                people = item.get('people',[])
                
                # Because of withPeople=True, this shouldn't be empty, but we check just in case
                if not people:
                    continue
                    
                total_faces = sum(len(person.get('faces') or[]) for person in people)
                file_name = item.get('originalFileName', 'Unknown')
                
                print(f"    Asset {len(processed_assets)+1}: {file_name} - {len(people)} people, {total_faces} faces")
                
                asset_info = {
                    'asset_id': item.get('id', ''),
                    'original_path': item.get('originalPath', ''),
                    'file_name': file_name,
                    'exifInfo': item.get('exifInfo', {}),
                    'people': people
                }
                
                processed_assets.append(asset_info)
            
            # Check next page
            next_page = assets_data.get('nextPage')
            if not next_page:
                break
                
            # Safely increment page (in case Immich ever switches to string-based cursors)
            try:
                page = int(next_page)
            except (ValueError, TypeError):
                page += 1
                
        except requests.exceptions.RequestException as e:
            print(f"Error collecting assets on page {page}: {e}")
            break
        except (KeyError, json.JSONDecodeError) as e:
            print(f"Error parsing asset collection response on page {page}: {e}")
            break
            
    print(f"\n✅ Processing completed: Found {len(processed_assets)} assets with faces")
    return processed_assets


def export_faces_to_json(access_token: str, json_output_dir: str = "json_exports", max_assets: Optional[int] = None) -> Optional[str]:
    """Export face recognition data to JSON file (Stage 1)."""
    print("Starting face recognition export to JSON format (Stage 1)...")
    
    # Create output directory
    output_path = Path(json_output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Process assets with faces
    processed_assets = process_assets_with_faces(access_token, max_assets)
    
    if not processed_assets:
        print("No assets with faces found")
        return None
    
    # Create comprehensive JSON export
    export_data = {
        'export_timestamp': datetime.now().isoformat(),
        'immich_server': IMMICH_BASE_URL,
        'total_assets': len(processed_assets),
        'assets': processed_assets
    }
    
    # Generate filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    json_filename = f"immich_faces_export_{timestamp}.json"
    json_file_path = output_path / json_filename
    
    try:
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ JSON export completed!")
        print(f"📊 Statistics:")
        print(f"   Total assets with faces: {len(processed_assets)}")
        print(f"   JSON file: {json_file_path}")
        
        return str(json_file_path)
        
    except IOError as e:
        print(f"❌ Error saving JSON file: {e}")
        return None


def write_xmp_for_assets(
    processed_assets: List[Dict[str, Any]],
    output_dir: str = "xmp_sidecars",
    *,
    json_source: Optional[str] = None,
    progress_every: int = 5,
    top_people_to_print: int = 20
) -> bool:
    """
    Common implementation: take a list of processed assets (each containing 'people' face data)
    and write XMP sidecars + a summary file.

    Args:
        processed_assets: List of asset dicts produced by process_assets_with_faces()
                         or loaded from the stage-1 JSON export.
        output_dir: Base directory where XMP sidecars (mirroring folder structure) will be written.
        json_source: Optional path to JSON file if assets came from stage-2 JSON processing.
        progress_every: Print progress every N assets (set to 0 to disable).
        top_people_to_print: Print top N people by face count.

    Returns:
        True if at least one XMP file was created, otherwise False.
    """
    if not processed_assets:
        print("No assets to write XMP for.")
        return False

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    total_files_created = 0
    total_faces_processed = 0
    person_stats: Dict[str, int] = {}

    print(f"\nCreating XMP files for {len(processed_assets)} assets with faces...")
    print(f"Output directory: {output_path.absolute()}")

    for i, asset_data in enumerate(processed_assets):
        people_data = asset_data.get("people") or []
        file_label = asset_data.get("file_name") or asset_data.get("originalFileName") or "Unknown"

        if not people_data:
            print(f"    Warning: No people data for asset {file_label}")
            if progress_every and (i + 1) % progress_every == 0:
                print(f"    Progress: {i+1}/{len(processed_assets)} assets processed")
            continue

        # Create XMP content
        xmp_content = create_xmp_content(asset_data)

        if not xmp_content.strip():
            print(f"    Warning: Empty XMP content for asset {file_label}")
            if progress_every and (i + 1) % progress_every == 0:
                print(f"    Progress: {i+1}/{len(processed_assets)} assets processed")
            continue

        # Determine original path for sidecar naming/structure
        asset_id = asset_data.get("asset_id") or asset_data.get("id") or f"idx_{i}"
        original_path = asset_data.get("original_path") or asset_data.get("originalPath") or f"unknown_{asset_id}.jpg"

        # Save XMP sidecar file
        if save_xmp_sidecar(original_path, xmp_content, str(output_path)):
            total_files_created += 1

            # Count faces + update per-person stats only if file was actually saved
            for person in people_data:
                person_name = (person.get("name") or "Unknown").strip() or "Unknown"
                faces = person.get("faces") or []
                face_count = len(faces)

                total_faces_processed += face_count
                person_stats[person_name] = person_stats.get(person_name, 0) + face_count

        if progress_every and (i + 1) % progress_every == 0:
            print(f"    Progress: {i+1}/{len(processed_assets)} assets processed")

    # Summary
    summary_file = output_path / "export_summary.json"
    summary_data: Dict[str, Any] = {
        "export_timestamp": datetime.now().isoformat(),
        "total_assets": len(processed_assets),
        "total_xmp_files_created": total_files_created,
        "total_faces_processed": total_faces_processed,
        "unique_people": len(person_stats),
        "people_statistics": person_stats,
        "output_directory": str(output_path.absolute()),
    }
    if json_source:
        summary_data["json_source"] = json_source

    try:
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"Error saving summary file: {e}")

    # Print stats
    print(f"\n✅ XMP export completed!")
    print(f"📊 Statistics:")
    print(f"   Total assets processed: {len(processed_assets)}")
    print(f"   XMP sidecar files created: {total_files_created}")
    print(f"   Total faces processed: {total_faces_processed}")
    print(f"   Unique people: {len(person_stats)}")
    print(f"   Output directory: {output_path.absolute()}")
    print(f"   Summary file: {summary_file}")

    if person_stats:
        print(f"\n👥 People found (top {top_people_to_print}):")
        for person, count in sorted(person_stats.items(), key=lambda x: x[1], reverse=True)[:top_people_to_print]:
            print(f"   {person}: {count} faces")

    if total_files_created == 0:
        print("\n❌ No XMP files were created (all assets were skipped or writes failed).")
        return False

    return True


def export_faces_to_xmp_from_json(json_file_path: str, output_dir: str = "xmp_sidecars") -> bool:
    """Export face recognition data to XMP format from JSON file (Stage 2)."""
    print("Starting face recognition export to XMP format from JSON file (Stage 2)...")
    print(f"JSON source: {json_file_path}")

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            export_data = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"❌ Error loading JSON file: {e}")
        return False

    processed_assets = export_data.get("assets") or []
    if not processed_assets:
        print("No assets found in JSON file")
        return False

    print(f"Loaded {len(processed_assets)} assets from JSON file")
    return write_xmp_for_assets(processed_assets, output_dir, json_source=json_file_path)


def export_faces_to_xmp(access_token: str, output_dir: str = "xmp_sidecars", max_assets: Optional[int] = None) -> bool:
    """
    Direct one-stage export: Immich API -> processed assets -> XMP sidecars (no JSON intermediate).
    """
    print("Starting DIRECT face recognition export to XMP format (API -> XMP, no JSON)...")

    processed_assets = process_assets_with_faces(access_token, max_assets)
    if not processed_assets:
        print("No assets with faces found")
        return False

    return write_xmp_for_assets(processed_assets, output_dir)


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Export face recognition data from Immich to XMP format',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Run both stages (default): Export to JSON then generate XMP
  python export_face.py
  
  # Run direct one-stage export (no JSON file written)
  python export_face.py --direct-xmp
  
  # Run only Stage 1: Export to JSON file
  python export_face.py --stage1-only
  
  # Run only Stage 2: Generate XMP from existing JSON file
  python export_face.py --stage2-only --json-file path/to/export.json
  
  # Specify custom output directories
  python export_face.py --json-dir my_json_exports --xmp-dir my_xmp_files
        '''
    )
    
    parser.add_argument(
        '--stage1-only', 
        action='store_true',
        help='Run only Stage 1: Export face data to JSON file'
    )
    
    parser.add_argument(
        '--stage2-only',
        action='store_true', 
        help='Run only Stage 2: Generate XMP files from existing JSON file'
    )
    
    parser.add_argument(
        '--direct-xmp',
        action='store_true',
        help='Run direct export: query Immich and write XMP sidecars directly (no intermediate JSON stage)'
    )
    
    parser.add_argument(
        '--json-file',
        type=str,
        help='Path to JSON file for Stage 2 (required with --stage2-only)'
    )
    
    parser.add_argument(
        '--json-dir',
        type=str,
        default=None,
        help='Directory for JSON exports (default: from config)'
    )
    
    parser.add_argument(
        '--xmp-dir',
        type=str,
        default=None,
        help='Directory for XMP output (default: from config)'
    )
    
    parser.add_argument(
        '--max-assets',
        type=int,
        default=None,
        help='Maximum number of assets to process (for debugging)'
    )
    
    return parser.parse_args()


def main():
    """Main function to export face recognition data with two-stage processing."""
    args = parse_arguments()
    
    # Validate argument combinations
    if args.stage1_only and args.stage2_only:
        print("❌ Error: Cannot specify both --stage1-only and --stage2-only")
        return
    
    if args.stage2_only and not args.json_file:
        print("❌ Error: --json-file is required when using --stage2-only")
        return
    
    if args.direct_xmp and (args.stage1_only or args.stage2_only):
        print("❌ Error: --direct-xmp cannot be combined with --stage1-only or --stage2-only")
        return

    if args.direct_xmp and args.json_file:
        print("❌ Error: --json-file is only used with --stage2-only; do not use it with --direct-xmp")
        return
    
    # Print configuration summary
    config.print_config_summary()
    
    # Stage 2 only mode - no need for Immich authentication
    if args.stage2_only:
        print("Running Stage 2 only: Generate XMP from JSON file")
        
        # Use custom XMP directory if specified, otherwise use config
        xmp_dir = args.xmp_dir or config.get_output_config()['xmp_export_dir']
        
        success = export_faces_to_xmp_from_json(args.json_file, xmp_dir)
        
        if success:
            print(f"\n🎉 XMP files generated successfully from JSON!")
            print(f"   Check the '{xmp_dir}' directory for XMP sidecar files.")
        else:
            print("\n❌ Failed to generate XMP files from JSON")
        return
    
    # Stage 1 or both stages - need Immich authentication
    if not config.validate_immich_config():
        return
    
    # Get configuration
    immich_config = config.get_immich_config()
    api_key = immich_config['api_key']
    email = immich_config['email']
    password = immich_config['password']
    output_config = config.get_output_config()
    
    # Use custom directories if specified, otherwise use config
    json_dir = args.json_dir or output_config['json_export_dir']
    xmp_dir = args.xmp_dir or output_config['xmp_export_dir']
    
    print("Starting Immich face recognition export...")
    print(f"Server: {IMMICH_BASE_URL}")
    print(f"JSON output directory: {json_dir}")
    print(f"XMP output directory: {xmp_dir}")
    if args.max_assets:
        print(f"Maximum assets to process: {args.max_assets}")
    
    # Authenticate
    if api_key:
        print("✅ Using API Key for authentication")
        access_token = "api_key_used"  # Dummy token for backwards compatibility
    else:
        access_token = authenticate(email, password)
        if not access_token:
            print("❌ Authentication failed. Please check your credentials and server URL.")
            return
        print("✅ Authentication successful")
    
    # Direct one-stage export (API -> XMP), no JSON written
    if args.direct_xmp:
        print("\nRunning direct XMP export (single stage): API -> XMP (no intermediate JSON)")
        success = export_faces_to_xmp(access_token, xmp_dir, args.max_assets)
        if success:
            print(f"\n🎉 Direct XMP export completed successfully!")
            print(f"   XMP files: {xmp_dir}")
        else:
            print("\n❌ Direct XMP export failed")
        return
    
    # Stage 1: Export to JSON (default path)
    json_file_path = export_faces_to_json(access_token, json_dir, args.max_assets)
    
    if not json_file_path:
        print("❌ Failed to export face data to JSON")
        return
    
    if args.stage1_only:
        print(f"\n🎉 Stage 1 completed successfully!")
        print(f"   JSON file created: {json_file_path}")
        print(f"   Use this file with --stage2-only --json-file to generate XMP files later.")
        return
    
    # Stage 2: Generate XMP from JSON
    print(f"\nProceeding to Stage 2: Generate XMP files from JSON...")
    success = export_faces_to_xmp_from_json(json_file_path, xmp_dir)
    
    if success:
        print(f"\n🎉 Both stages completed successfully!")
        print(f"   JSON export: {json_file_path}")
        print(f"   XMP files: {xmp_dir}")
    else:
        print("\n❌ Failed to generate XMP files from JSON")


if __name__ == "__main__":
    main()
