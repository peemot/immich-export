#!/usr/bin/env python3
"""
Face recognition export script for Immich.
Uses search API to get face data.
Exports face recognition data to XMP format, supported by digiKam, XnView MP and others.
"""

import argparse
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Iterator
from xml.sax.saxutils import escape as _xml_escape
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Logging Setup ---
logger = logging.getLogger(__name__)

def setup_logging(debug: bool = False):
    """Configure logging with different formats for debug vs info."""
    level = logging.DEBUG if debug else logging.INFO
    
    class CustomFormatter(logging.Formatter):
        def format(self, record):
            if record.levelno == logging.INFO:
                self._style._fmt = "%(message)s"
            elif record.levelno == logging.WARNING:
                self._style._fmt = "%(message)s"
            else:
                self._style._fmt = "%(asctime)s - %(levelname)s - %(message)s"
            return super().format(record)

    handler = logging.StreamHandler()
    handler.setFormatter(CustomFormatter(datefmt="%Y-%m-%d %H:%M:%S"))
    
    logging.basicConfig(level=level, handlers=[handler])


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
                logger.info(f"✅ Configuration loaded from {self.config_file}")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"⚠️  Error loading config file {self.config_file}: {e}")
                logger.warning("   Using environment variables and defaults")
                self.config_data = {}
        else:
            logger.warning(f"⚠️  Config file {self.config_file} not found")
            logger.warning("   Using environment variables and defaults")
        
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
            'OUTPUT_XMP_DIR': ['output', 'xmp_export_dir'],
            'OUTPUT_JSON_DIR': ['output', 'json_export_dir']
        }
        
        for env_var, config_path in env_mappings.items():
            env_value = os.getenv(env_var)
            if env_value:
                self._set_nested_value(self.config_data, config_path, env_value)
                logger.debug(f"✅  Loaded {env_var} from environment")
    
    def _set_nested_value(self, data: Dict[str, Any], path: List[str], value: str) -> None:
        """Set nested dictionary value from path list."""
        current = data
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        # Convert numeric values
        if path[-1] in['request_timeout', 'retry_attempts']:
            try:
                current[path[-1]] = int(value)
            except ValueError:
                current[path[-1]] = value
        else:
            current[path[-1]] = value
    
    def get(self, path: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'immich.base_url')."""
        keys = path.split(".")
        current: Any = self.config_data
        sentinel = object()

        for key in keys:
            if not isinstance(current, dict):
                return default
            current = current.get(key, sentinel)
            if current is sentinel:
                return default

        return default if current is None else current
    
    def get_immich_config(self) -> Dict[str, str]:
        """Get Immich connection configuration."""
        base_url = self.get("immich.base_url", "https://www.blahblah.com")
        if not base_url:
            base_url = "https://www.blahblah.com"

        return {
            "base_url": str(base_url),
            "api_key": self.get("immich.api_key", "") or "",
            "email": self.get("immich.email", "") or "",
            "password": self.get("immich.password", "") or "",
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
            logger.error("❌ Configuration error: Please update the Immich server URL")
            logger.error("   Set it in config.json or use environment variable:")
            logger.error("   IMMICH_BASE_URL")
            return False

        has_api_key = bool(immich_config['api_key'])
        has_credentials = bool(immich_config['email'] and immich_config['password'])

        if not has_api_key and not has_credentials:
            logger.error("❌ Configuration error: API key OR Email and password are required")
            logger.error("   Please set them in config.json or use environment variables:")
            logger.error("   IMMICH_API_KEY or (IMMICH_EMAIL and IMMICH_PASSWORD)")
            logger.error("   Note: If using an API key, it must have at least 'asset.read' permission.")
            return False
        
        return True
    
    def log_config_summary(self) -> None:
        """Log a summary of loaded configuration."""
        logger.info("\n📋 Configuration Summary:")
        logger.info(f"   Server URL: {self.get('immich.base_url')}")
        if self.get('immich.api_key'):
            logger.info("   Auth Method: API Key")
        else:
            logger.info(f"   Auth Method: Email ({self.get('immich.email')})")
        logger.info(f"   Timeout: {self.get('settings.request_timeout')}s")
        logger.info(f"   Retry Attempts: {self.get('settings.retry_attempts')}")
        logger.info(f"   JSON output directory: {self.get('output.json_export_dir')}")
        logger.info(f"   XMP output directory: {self.get('output.xmp_export_dir')}")

# Lazy global config loader
_CONFIG_INSTANCE: Optional[ConfigLoader] = None

def get_config() -> ConfigLoader:
    global _CONFIG_INSTANCE
    if _CONFIG_INSTANCE is None:
        _CONFIG_INSTANCE = ConfigLoader()
    return _CONFIG_INSTANCE

DEFAULT_HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

def create_http_session(retries: int) -> requests.Session:
    """Create a requests session with automatic retries."""
    session = requests.Session()
    retry_strategy = Retry(
        total = retries,
        backoff_factor = 1,  # Wait 1s, 2s, 4s between retries
        status_forcelist = [429, 500, 502, 503, 504],
        allowed_methods = ["HEAD", "GET", "POST", "OPTIONS"] 
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def api_request(session: requests.Session, method: str, path: str, *, token: Optional[str] = None, **kwargs) -> requests.Response:
    config = get_config()
    immich_config = config.get_immich_config()
    settings_config = config.get_settings_config()
    
    base_url = immich_config["base_url"].rstrip("/")
    api_base = f"{base_url}/api"
    
    headers = DEFAULT_HEADERS.copy()
    api_key = immich_config.get('api_key')
    
    if api_key:
        headers["x-api-key"] = api_key
    elif token:
        headers["Cookie"] = f"immich_access_token={token}"
    
    resp = session.request(
        method,
        f"{api_base}{path}",
        headers=headers,
        timeout=settings_config['request_timeout'],
        **kwargs,
    )
    resp.raise_for_status()
    return resp


def authenticate(session: requests.Session, email: str, password: str) -> Optional[str]:
    """Authenticate with Immich API and return access token."""
    payload = {"email": email, "password": password}
    try:
        response = api_request(session, "POST", "/auth/login", json=payload) 
        return response.json()["accessToken"]
    except requests.exceptions.RequestException as e:
        logger.error(f"Authentication failed: {e}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        logger.error(f"Failed to parse authentication response: {e}")
        return None


def _parse_orientation(orientation: Any) -> int:
    """Parse Exif orientation strings or ints into a standard integer 1-8."""
    if isinstance(orientation, int): return orientation
    o = str(orientation).lower()
    if any(x in o for x in ['6', 'right top', 'right-top', '90 cw']): return 6
    if any(x in o for x in ['3', 'bottom right', '180']): return 3
    if any(x in o for x in ['8', 'left bottom', 'left-bottom', '270 cw']): return 8
    if any(x in o for x in ['2', 'top right', 'mirror horizontal']): return 2
    if any(x in o for x in ['4', 'bottom left', 'mirror vertical']): return 4
    if '5' in o: return 5
    if '7' in o: return 7
    return 1


def _calculate_unrotated_face_coords(
    face: Dict[str, Any],
    orientation_val: int,
    raw_w: int, raw_h: int
) -> Tuple[float, float, float, float]:
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

    def clamp01(v: float) -> float:
        # Handle NaN
        if v != v: return 0.0
        if v < 0.0: return 0.0
        if v > 1.0: return 1.0
        return v

    return clamp01(raw_cx), clamp01(raw_cy), clamp01(raw_fw), clamp01(raw_fh)


def create_xmp_content(asset_data: Dict[str, Any]) -> str:
    """Create XMP content for face recognition data with EXIF information."""
    raw_people = asset_data.get("people") or []
    people: List[Dict[str, Any]] = []

    for person in raw_people:
        valid_faces = []
        for face in (person.get("faces") or []):
            if all(
                face.get(key) is not None
                for key in (
                    "boundingBoxX1",
                    "boundingBoxY1",
                    "boundingBoxX2",
                    "boundingBoxY2",
                )
            ):
                valid_faces.append(face)

        if not valid_faces:
            continue

        normalized_person = dict(person)
        normalized_person["faces"] = valid_faces
        people.append(normalized_person)

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

    def safe_int(value: Any) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    # MWG regions must be relative to the UNROTATED (raw) image dimensions.
    raw_w = safe_int(exif_info.get("exifImageWidth")) or safe_int(asset_data.get("width"))
    raw_h = safe_int(exif_info.get("exifImageHeight")) or safe_int(asset_data.get("height"))
    orientation_val = _parse_orientation(exif_info.get("orientation"))

    if not raw_w or not raw_h:
        sample_face = next(
            (
                face
                for person in people
                for face in (person.get("faces") or [])
                if safe_int(face.get("imageWidth")) and safe_int(face.get("imageHeight"))
            ),
            None,
        )
        if sample_face:
            face_w = safe_int(sample_face.get("imageWidth"))
            face_h = safe_int(sample_face.get("imageHeight"))
            if orientation_val in [5, 6, 7, 8]:
                raw_w, raw_h = face_h, face_w
            else:
                raw_w, raw_h = face_w, face_h

    if not raw_w or not raw_h:
        return ""

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

    add_tag(lines, "tiff:Make", exif_str("make"))
    add_tag(lines, "tiff:Model", exif_str("model"))
    add_tag(lines, "exif:LensModel", exif_str("lensModel"))
    add_tag(lines, "exif:FNumber", exif_str("fNumber"))
    add_tag(lines, "exif:ExposureTime", exif_str("exposureTime"))
    add_tag(lines, "exif:ISOSpeedRatings", exif_str("iso"))
    add_tag(lines, "exif:FocalLength", exif_str("focalLength"))
    add_tag(lines, "tiff:ImageWidth", str(raw_w))
    add_tag(lines, "tiff:ImageLength", str(raw_h))
    add_tag(lines, "exif:ExifImageWidth", str(raw_w))
    add_tag(lines, "exif:ExifImageHeight", str(raw_h))
    add_tag(lines, "exif:DateTimeOriginal", xmp_date(exif_str("dateTimeOriginal")))
    add_tag(lines, "exif:DateTimeDigitized", xmp_date(exif_str("dateTimeDigitized")))

    latitude = exif_str("latitude")
    longitude = exif_str("longitude")
    if latitude and longitude:
        add_tag(lines, "exif:GPSLatitude", latitude)
        add_tag(lines, "exif:GPSLongitude", longitude)

    add_tag(lines, "photoshop:City", exif_str("city"))
    add_tag(lines, "photoshop:State", exif_str("state"))
    add_tag(lines, "photoshop:Country", exif_str("country"))

    file_name = asset_data.get("file_name") or asset_data.get("originalFileName") or ""
    add_tag(lines, "xmp:Identifier", file_name)
    lines.append("   <xmp:CreatorTool>Immich Face Export Tool</xmp:CreatorTool>")

    names = [(p.get("name") or "").strip() for p in people]
    unique_people = sorted({name for name in names if name and name != "Unknown"})
    if unique_people:
        lines.extend(["   <dc:subject>", "    <rdf:Bag>"])
        for name in unique_people:
            lines.append(f"     <rdf:li>{xml_text(name)}</rdf:li>")
        lines.extend(["    </rdf:Bag>", "   </dc:subject>"])

    lines.extend([
        '   <mwg-rs:Regions rdf:parseType="Resource">',
        "    <mwg-rs:AppliedToDimensions",
        f'     stDim:w="{raw_w}"',
        f'     stDim:h="{raw_h}"',
        '     stDim:unit="pixel"/>',
        "    <mwg-rs:RegionList>",
        "    <rdf:Bag>",
    ])

    regions_written = 0

    def add_face_region(person_name: str, face: Dict[str, Any]) -> None:
        nonlocal regions_written

        raw_cx, raw_cy, raw_fw, raw_fh = _calculate_unrotated_face_coords(
            face, orientation_val, raw_w, raw_h
        )
        if raw_fw <= 0.0 or raw_fh <= 0.0:
            return

        lines.extend([
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
        ])
        regions_written += 1

    for person in people:
        person_name = (person.get("name") or "Unknown").strip() or "Unknown"
        for face in (person.get("faces") or []):
            add_face_region(person_name, face)

    if regions_written == 0:
        return ""

    lines.extend([
        "    </rdf:Bag>",
        "    </mwg-rs:RegionList>",
        "   </mwg-rs:Regions>",
        "  </rdf:Description>",
        " </rdf:RDF>",
        "</x:xmpmeta>",
        "",
    ])

    return "\n".join(lines)


def save_xmp_sidecar(original_path: str, xmp_content: str, output_dir: str = "") -> bool:
    """Save XMP content to sidecar file, creating same directory structure in output_dir."""
    if not xmp_content.strip():
        return False  # Skip empty XMP

    try:
        # Strip leading slashes to prevent jumping to filesystem root.
        clean_path = str(original_path).lstrip("/\\")

        # Strip Windows drive letters (e.g., C:) if they somehow exist
        if len(clean_path) > 1 and clean_path[1] == ":":
            clean_path = clean_path[2:].lstrip("/\\")

        original_path_obj = Path(clean_path)
        # Neutralize traversal (..). Without this, output_base / ../../etc can escape.
        safe_parent_parts = [p for p in original_path_obj.parent.parts if p not in ("..", ".", "")]
        safe_parent = Path(*safe_parent_parts)

        filename = original_path_obj.name + ".xmp"

        if output_dir:
            output_base = Path(output_dir)
            candidate = output_base / safe_parent / filename

            # Enforce containment even after resolving (handles traversal and symlinks)
            base_resolved = output_base.resolve(strict=False)
            xmp_path = candidate.resolve(strict=False)

            if base_resolved != xmp_path and base_resolved not in xmp_path.parents:
                raise ValueError(f"❌ Refusing to write outside output directory: {xmp_path}")

            xmp_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            xmp_path = Path(filename)

        with open(xmp_path, "w", encoding="utf-8") as f:
            f.write(xmp_content)

        logger.debug(f"Saved XMP sidecar: {xmp_path}")
        return True

    except (IOError, ValueError) as e:
        logger.error(f"❌ Error saving XMP file: {e}")
        return False


def process_assets_with_faces(
    session: requests.Session,
    access_token: str,
    max_assets: Optional[int] = None,
    album_id: Optional[str] = None,
    library_id: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """Yield assets that contain at least one valid face region."""
    page = 1
    progress_interval = 100
    yielded_assets = 0

    logger.info("   Collecting assets with faces...")

    while True:
        if max_assets is not None and yielded_assets >= max_assets:
            logger.info(f"   Reached maximum asset limit: {max_assets}")
            break

        try:
            search_payload = {
                "page": page,
                "size": 200,         # Fetch 200 fully-populated assets at once
                "withPeople": True,  # Ask Immich to INCLUDE people/faces in each returned 
                "withExif": True,    # Embed the EXIF data directly in this list response
            }

            if album_id:
                search_payload["albumIds"] = [album_id]
            if library_id:
                search_payload["libraryId"] = library_id

            # The search endpoint acts as searchAssets when payload matches metadata parameters
            response = api_request(
                session,
                "POST",
                "/search/metadata",
                token=access_token,
                json=search_payload,
            )
            search_data = response.json()

            assets_data = search_data.get("assets")
            if assets_data is None:
                assets_data = search_data

            if not isinstance(assets_data, dict):
                logger.error(
                    f"❌ Error: Unexpected search response shape on page {page} (expected dict)"
                )
                break

            items = assets_data.get("items")
            if items is None:
                items = []

            if not isinstance(items, list):
                logger.error(
                    f"❌ Error: Unexpected 'items' type on page {page} (expected list)"
                )
                break

            if not items:
                break

            for item in items:
                if max_assets is not None and yielded_assets >= max_assets:
                    break

                raw_people = item.get("people") or []
                normalized_people: List[Dict[str, Any]] = []
                total_faces = 0

                for person in raw_people:
                    valid_faces = []
                    for face in (person.get("faces") or []):
                        if all(
                            face.get(key) is not None
                            for key in (
                                "boundingBoxX1",
                                "boundingBoxY1",
                                "boundingBoxX2",
                                "boundingBoxY2",
                            )
                        ):
                            valid_faces.append(face)

                    if not valid_faces:
                        continue

                    normalized_person = dict(person)
                    normalized_person["faces"] = valid_faces
                    normalized_people.append(normalized_person)
                    total_faces += len(valid_faces)

                if not normalized_people:
                    continue

                yielded_assets += 1
                file_name = item.get("originalFileName", "Unknown")

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        f"Asset {yielded_assets}: {file_name} - "
                        f"{len(normalized_people)} people, {total_faces} faces"
                    )
                elif progress_interval and yielded_assets % progress_interval == 0:
                    logger.info(f"   Progress: Found {yielded_assets} assets with faces...")

                yield {
                    "asset_id": item.get("id", ""),
                    "original_path": item.get("originalPath", ""),
                    "file_name": file_name,
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "exifInfo": item.get("exifInfo") or {},
                    "people": normalized_people,
                }

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Finished processing page {page}")

            next_page = assets_data.get("nextPage")
            if not next_page:
                break

            try:
                page = int(next_page)
            except (ValueError, TypeError):
                page += 1

        except Exception as e:
            logger.error(f"❌ Error collecting assets on page {page}: {e}")
            break

    logger.info(f"✅ Processing completed: Found {yielded_assets} assets with faces")


def export_faces_to_json(
    session: requests.Session,
    access_token: str,
    json_output_dir: str = "json_exports",
    max_assets: Optional[int] = None,
    album_id: Optional[str] = None,
    library_id: Optional[str] = None
) -> Optional[str]:
    """Export face recognition data to JSON file (Stage 1) without holding the full library in memory."""
    logger.info("\n   Starting face recognition export to JSON format (Stage 1)...")

    output_path = Path(json_output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_filename = f"immich_faces_export_{timestamp}.json"
    json_file_path = output_path / json_filename
    assets_tmp_path = output_path / f".{json_filename}.assets.tmp"

    base_url = get_config().get_immich_config()["base_url"].rstrip("/")
    total_assets = 0

    try:
        with open(assets_tmp_path, "w", encoding="utf-8") as assets_file:
            first_asset = True

            for asset_info in process_assets_with_faces(
                session,
                access_token,
                max_assets,
                album_id,
                library_id,
            ):
                if not first_asset:
                    assets_file.write(",\n")
                json.dump(asset_info, assets_file, ensure_ascii=False, separators=(",", ":"))
                first_asset = False
                total_assets += 1

        if total_assets == 0:
            logger.warning("No assets with faces found")
            return None

        export_timestamp = datetime.now().isoformat()

        with open(json_file_path, "w", encoding="utf-8") as out_file, open(
            assets_tmp_path, "r", encoding="utf-8"
        ) as assets_file:
            out_file.write("{\n")
            out_file.write(
                f'  "export_timestamp": {json.dumps(export_timestamp, ensure_ascii=False)},\n'
            )
            out_file.write(
                f'  "immich_server": {json.dumps(base_url, ensure_ascii=False)},\n'
            )
            out_file.write(f'  "total_assets": {total_assets},\n')
            out_file.write('  "assets": [\n')

            while True:
                chunk = assets_file.read(1024 * 1024)
                if not chunk:
                    break
                out_file.write(chunk)

            out_file.write("\n  ]\n}\n")

        json_file_path_abs = json_file_path.absolute()
        logger.info("✅ JSON export completed!")
        logger.info("\n📊 Statistics:")
        logger.info(f"   Total assets with faces: {total_assets}")
        logger.info(f"   JSON file: {json_file_path_abs}")

        return str(json_file_path_abs)

    except IOError as e:
        logger.error(f"❌ Error saving JSON file: {e}")
        return None

    finally:
        try:
            if assets_tmp_path.exists():
                assets_tmp_path.unlink()
        except OSError:
            pass


def write_xmp_for_assets(
    processed_assets: List[Dict[str, Any]],
    output_dir: str = "xmp_sidecars",
    *,
    json_source: Optional[str] = None,
    progress_every: int = 500,
    top_people_to_print: int = 10
) -> bool:
    """
    Take a list of processed assets and write XMP sidecars + a summary file.

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
        logger.warning("No assets to write XMP for.")
        return False

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    total_files_created = 0
    total_faces_processed = 0
    person_stats: Dict[str, int] = {}

    logger.info(f"\n   Creating XMP files for {len(processed_assets)} assets with faces...")
    logger.info(f"   Output directory: {output_path.absolute()}")

    for i, asset_data in enumerate(processed_assets):
        people_data = asset_data.get("people") or[]
        file_label = asset_data.get("file_name") or asset_data.get("originalFileName") or "Unknown"

        if not people_data:
            logger.warning(f"   Warning: No people data for asset {file_label}")
            if progress_every and (i + 1) % progress_every == 0:
                logger.info(f"   Progress: {i+1}/{len(processed_assets)} assets processed")
            continue

        # Create XMP content
        xmp_content = create_xmp_content(asset_data)

        if not xmp_content.strip():
            logger.warning(f"   Warning: Empty XMP content for asset {file_label}")
            if progress_every and (i + 1) % progress_every == 0:
                logger.info(f"   Progress: {i+1}/{len(processed_assets)} assets processed")
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
                faces = person.get("faces") or[]
                face_count = len(faces)

                total_faces_processed += face_count
                person_stats[person_name] = person_stats.get(person_name, 0) + face_count

        if progress_every and (i + 1) % progress_every == 0:
            logger.info(f"   Progress: {i+1}/{len(processed_assets)} assets processed")

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
        logger.error(f"❌ Error saving summary file: {e}")

    # Print stats
    logger.info(f"✅ XMP export completed!")
    logger.info(f"\n📊 Statistics:")
    logger.info(f"   Total assets processed: {len(processed_assets)}")
    logger.info(f"   XMP sidecar files created: {total_files_created}")
    logger.info(f"   Total faces processed: {total_faces_processed}")
    logger.info(f"   Unique people: {len(person_stats)}")
    logger.info(f"   Output directory: {output_path.absolute()}")
    logger.info(f"   Summary file: {summary_file.absolute()}")

    if person_stats:
        logger.info(f"\n👥 People found (top {top_people_to_print}):")
        for person, count in sorted(person_stats.items(), key=lambda x: x[1], reverse=True)[:top_people_to_print]:
            logger.info(f"   {person}: {count} faces")

    if total_files_created == 0:
        logger.error("\n❌ No XMP files were created (all assets were skipped or writes failed).")
        return False

    return True


def export_faces_to_xmp_from_json(json_file_path: str, output_dir: str = "xmp_sidecars") -> bool:
    """Export face recognition data to XMP format from JSON file (Stage 2)."""
    logger.info("   Starting face recognition export to XMP format from JSON file (Stage 2)...")
    logger.info(f"   JSON source: {Path(json_file_path).absolute()}")

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            export_data = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"❌ Error loading JSON file: {e}")
        return False

    processed_assets = export_data.get("assets") or[]
    if not processed_assets:
        logger.warning("No assets found in JSON file")
        return False

    logger.info(f"   Loaded {len(processed_assets)} assets from JSON file")
    return write_xmp_for_assets(processed_assets, output_dir, json_source=json_file_path)


def export_faces_to_xmp(
    session: requests.Session,
    access_token: str,
    output_dir: str = "xmp_sidecars",
    max_assets: Optional[int] = None,
    album_id: Optional[str] = None,
    library_id: Optional[str] = None
) -> bool:
    """Direct one-stage export: stream Immich API results straight to XMP sidecars."""
    logger.info("   Starting DIRECT face recognition export to XMP format (API -> XMP, no JSON)...")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    total_assets = 0
    total_files_created = 0
    total_faces_processed = 0
    person_stats: Dict[str, int] = {}

    logger.info(f"   Output directory: {output_path.absolute()}")

    for asset_data in process_assets_with_faces(
        session,
        access_token,
        max_assets,
        album_id,
        library_id,
    ):
        total_assets += 1
        people_data = asset_data.get("people") or []
        file_label = asset_data.get("file_name") or asset_data.get("originalFileName") or "Unknown"

        xmp_content = create_xmp_content(asset_data)
        if not xmp_content.strip():
            logger.warning(f"   Warning: Empty XMP content for asset {file_label}")
            continue

        asset_id = asset_data.get("asset_id") or asset_data.get("id") or f"idx_{total_assets}"
        original_path = (
            asset_data.get("original_path")
            or asset_data.get("originalPath")
            or f"unknown_{asset_id}.jpg"
        )

        if save_xmp_sidecar(original_path, xmp_content, str(output_path)):
            total_files_created += 1

            for person in people_data:
                person_name = (person.get("name") or "Unknown").strip() or "Unknown"
                face_count = len(person.get("faces") or [])
                if face_count <= 0:
                    continue
                total_faces_processed += face_count
                person_stats[person_name] = person_stats.get(person_name, 0) + face_count

    if total_assets == 0:
        logger.warning("No assets with faces found")
        return False

    summary_file = output_path / "export_summary.json"
    summary_data: Dict[str, Any] = {
        "export_timestamp": datetime.now().isoformat(),
        "total_assets": total_assets,
        "total_xmp_files_created": total_files_created,
        "total_faces_processed": total_faces_processed,
        "unique_people": len(person_stats),
        "people_statistics": person_stats,
        "output_directory": str(output_path.absolute()),
    }

    try:
        with open(summary_file, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.error(f"❌ Error saving summary file: {e}")

    logger.info("✅ XMP export completed!")
    logger.info("\n📊 Statistics:")
    logger.info(f"   Total assets processed: {total_assets}")
    logger.info(f"   XMP sidecar files created: {total_files_created}")
    logger.info(f"   Total faces processed: {total_faces_processed}")
    logger.info(f"   Unique people: {len(person_stats)}")
    logger.info(f"   Output directory: {output_path.absolute()}")
    logger.info(f"   Summary file: {summary_file.absolute()}")

    if person_stats:
        logger.info("\n👥 People found (top 10):")
        for person, count in sorted(person_stats.items(), key=lambda x: x[1], reverse=True)[:10]:
            logger.info(f"   {person}: {count} faces")

    if total_files_created == 0:
        logger.error("\n❌ No XMP files were created (all assets were skipped or writes failed).")
        return False

    return True


def parse_arguments() -> argparse.Namespace:
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
  
  # Filter by a specific album or library
  python export_face.py --album-id "your-album-uuid"
  python export_face.py --library-id "your-library-uuid"
        '''
    )
    
    parser.add_argument('--stage1-only', action='store_true', help='Run only Stage 1: Export face data to JSON file')
    parser.add_argument('--stage2-only', action='store_true', help='Run only Stage 2: Generate XMP files from existing JSON file')
    parser.add_argument('--direct-xmp', action='store_true', help='Run direct export: query Immich and write XMP sidecars directly')
    parser.add_argument('--json-file', type=str, help='Path to JSON file for Stage 2 (required with --stage2-only)')
    parser.add_argument('--json-dir', type=str, default=None, help='Directory for JSON exports (default: from config)')
    parser.add_argument('--xmp-dir', type=str, default=None, help='Directory for XMP output (default: from config)')
    parser.add_argument('--max-assets', type=int, default=None, help='Maximum number of assets to process (for debugging)')
    parser.add_argument('--album-id', type=str, default=None, help='Process only assets from this specific album ID')
    parser.add_argument('--library-id', type=str, default=None, help='Process only assets from this specific library ID')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging for detailed outputs')
    
    return parser.parse_args()


def main():
    """Main function to export face recognition data with two-stage processing."""
    args = parse_arguments()
    
    # Initialize logger based on argument
    setup_logging(args.debug)
    
    if args.stage1_only and args.stage2_only:
        return logger.error("❌ Error: Cannot specify both --stage1-only and --stage2-only")
    if args.stage2_only and not args.json_file:
        return logger.error("❌ Error: --json-file is required when using --stage2-only")
    if args.direct_xmp and (args.stage1_only or args.stage2_only or args.json_file):
        return logger.error("❌ Error: --direct-xmp cannot be combined with stage args or --json-file")
    
    config = get_config()
    config.log_config_summary()
    
    if args.stage2_only:
        logger.info("Running Stage 2 only: Generate XMP from JSON file")
        # Use custom XMP directory if specified, otherwise use config
        xmp_dir = args.xmp_dir or config.get_output_config()['xmp_export_dir']
        
        success = export_faces_to_xmp_from_json(args.json_file, xmp_dir)
        
        if success:
            logger.info(f"\n🎉 XMP files generated successfully from JSON!")
            logger.info(f"   Check the '{Path(xmp_dir).absolute()}' directory for XMP sidecar files.")
        else:
            logger.error("\n❌ Failed to generate XMP files from JSON")
        return
    
    # Stage 1 or both stages - need Immich authentication
    if not config.validate_immich_config():
        return
    
    # Get configuration
    immich_config = config.get_immich_config()
    api_key = immich_config['api_key']
    email = immich_config['email']
    password = immich_config['password']
    base_url = immich_config['base_url'].rstrip('/')
    
    output_config = config.get_output_config()
    settings_config = config.get_settings_config()
    
    # Use custom directories if specified, otherwise use config
    json_dir = args.json_dir or output_config['json_export_dir']
    xmp_dir = args.xmp_dir or output_config['xmp_export_dir']
    
    logger.debug(f"JSON output directory: {json_dir}")
    logger.debug(f"XMP output directory: {xmp_dir}")
    if args.max_assets:
        logger.info(f"   Maximum assets to process: {args.max_assets}")
    
    session = create_http_session(settings_config['retry_attempts'])
    
    # Authenticate
    if api_key:
        access_token = "api_key_used"  # Dummy token for backwards compatibility
    else:
        access_token = authenticate(session, email, password)
        if not access_token:
            logger.error("❌ Authentication failed. Please check your credentials and server URL.")
            return
    
    # Direct one-stage export (API -> XMP), no JSON written
    if args.direct_xmp:
        logger.info("\n   Running direct XMP export (single stage): API -> XMP (no intermediate JSON)")
        success = export_faces_to_xmp(session, access_token, xmp_dir, args.max_assets, args.album_id, args.library_id)
        if success:
            logger.info(f"\n🎉 Direct XMP export completed successfully!")
            logger.info(f"   XMP files: {Path(xmp_dir).absolute()}")
        else:
            logger.error("\n❌ Direct XMP export failed")
        return
    
    # Stage 1: Export to JSON (default path)
    json_file_path = export_faces_to_json(session, access_token, json_dir, args.max_assets, args.album_id, args.library_id)
    
    if not json_file_path:
        logger.error("❌ Failed to export face data to JSON")
        return
    
    if args.stage1_only:
        logger.info(f"\n🎉 Stage 1 completed successfully!")
        logger.info(f"   JSON file created: {json_file_path}")
        logger.info(f"   Use this file with --stage2-only --json-file to generate XMP files later.")
        return
    
    # Stage 2: Generate XMP from JSON
    logger.info(f"\n   Proceeding to Stage 2: Generate XMP files from JSON...")
    success = export_faces_to_xmp_from_json(json_file_path, xmp_dir)
    
    if success:
        logger.info(f"\n🎉 Both stages completed successfully!")
    else:
        logger.error("\n❌ Failed to generate XMP files from JSON")

if __name__ == "__main__":
    main()
