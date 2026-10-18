#!/usr/bin/env python3
"""Export Immich metadata and face regions to JSON and XMP, in one file.

Configuration is loaded at the CLI boundary. ImmichClient owns HTTP and the
search protocol; export records are ordinary dictionaries shared by the
API and offline JSON paths. Selection and XMP rendering do not perform I/O.
The writers preserve record ordering and report incomplete exports as failures.
"""

import argparse
import json
import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Any, Dict, Generator, Iterable, Iterator, List, Mapping, Optional, Tuple, TypedDict
from xml.sax.saxutils import escape as _xml_escape

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

FACE_BOX_KEYS = (
    "boundingBoxX1",
    "boundingBoxY1",
    "boundingBoxX2",
    "boundingBoxY2",
)
DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


# --- Data passed between the application boundaries ---


class ExportAsset(TypedDict):
    """The API projection, not a schema validator for existing JSON files.

    Nested EXIF, person and face dictionaries retain their additional fields.
    Offline records may also contain aliases and extra top-level fields;
    filtering and rendering must not rebuild those records from this annotation.
    """

    asset_id: str
    original_path: str
    file_name: str
    width: Any
    height: Any
    exifInfo: Dict[str, Any]
    people: List[Dict[str, Any]]


@dataclass(frozen=True)
class ApiSettings:
    """Connection values resolved once, before any HTTP work begins."""

    base_url: str
    api_key: str = field(default="", repr=False)
    request_timeout: int = 30
    retry_attempts: int = 3
    face_request_workers: int = 8

    @classmethod
    def from_config(cls, config: "ConfigLoader") -> "ApiSettings":
        connection = config.get_immich_config()
        return cls(
            base_url=connection["base_url"],
            api_key=connection["api_key"],
            **config.get_settings_config(),
        )


@dataclass(frozen=True)
class XmpResult:
    """Rendered text and its region counts; neither is an export-side effect."""

    content: str
    region_counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class ExportStatistics:
    """Count distinct sidecars successfully written during this export."""

    files_created: int = 0
    faces_processed: int = 0
    people: Dict[str, int] = field(default_factory=dict)

    def record_write(self, result: XmpResult) -> None:
        self.files_created += 1
        self.faces_processed += sum(result.region_counts.values())
        for name, count in result.region_counts.items():
            self.people[name] = self.people.get(name, 0) + count


# --- Configuration and logging ---


class CustomFormatter(logging.Formatter):
    """Use compact output for info/warning and detailed output otherwise."""

    FORMATS = {
        logging.INFO: "%(message)s",
        logging.WARNING: "%(message)s",
    }

    def format(self, record: logging.LogRecord) -> str:
        self._style._fmt = self.FORMATS.get(record.levelno, "%(asctime)s - %(levelname)s - %(message)s")
        return super().format(record)


def setup_logging(debug: bool = False) -> None:
    """Configure logging with different formats for debug vs info."""
    handler = logging.StreamHandler()
    handler.setFormatter(CustomFormatter(datefmt="%Y-%m-%d %H:%M:%S"))
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=[handler])


class ConfigLoader:
    """Configuration loader that supports JSON files and environment variables."""

    def __init__(self, config_file: str = "config.json"):
        """Initialize configuration loader."""
        self.config_file = config_file
        self.config_data: Dict[str, Any] = {}
        self.load_config()

    def load_config(self) -> None:
        """Load configuration from file and environment variables."""
        # Load from JSON file if it exists
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
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
            "IMMICH_BASE_URL": ["immich", "base_url"],
            "IMMICH_API_KEY": ["immich", "api_key"],
            "IMMICH_EMAIL": ["immich", "email"],
            "IMMICH_PASSWORD": ["immich", "password"],
            "IMMICH_REQUEST_TIMEOUT": ["settings", "request_timeout"],
            "IMMICH_RETRY_ATTEMPTS": ["settings", "retry_attempts"],
            "IMMICH_FACE_REQUEST_WORKERS": ["settings", "face_request_workers"],
            "OUTPUT_XMP_DIR": ["output", "xmp_export_dir"],
            "OUTPUT_JSON_DIR": ["output", "json_export_dir"],
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
            current = current.setdefault(key, {})

        # Convert numeric values
        if path[-1] in ["request_timeout", "retry_attempts", "face_request_workers"]:
            try:
                current[path[-1]] = int(value)
            except ValueError:
                current[path[-1]] = value
        else:
            current[path[-1]] = value

    def get(self, path: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'immich.base_url')."""
        current: Any = self.config_data
        sentinel = object()

        for key in path.split("."):
            if not isinstance(current, dict):
                return default
            current = current.get(key, sentinel)
            if current is sentinel:
                return default

        return default if current is None else current

    def get_immich_config(self) -> Dict[str, str]:
        """Get Immich connection configuration."""
        base_url = self.get("immich.base_url", "https://www.blahblah.com") or "https://www.blahblah.com"
        return {
            "base_url": str(base_url),
            "api_key": self.get("immich.api_key", "") or "",
            "email": self.get("immich.email", "") or "",
            "password": self.get("immich.password", "") or "",
        }

    def get_output_config(self) -> Dict[str, str]:
        """Get output configuration."""
        return {
            "xmp_export_dir": self.get("output.xmp_export_dir", "xmp_sidecars"),
            "json_export_dir": self.get("output.json_export_dir", "json_exports"),
        }

    def get_settings_config(self) -> Dict[str, Any]:
        """Get general settings configuration."""
        face_request_workers = self.get("settings.face_request_workers", 8)
        try:
            face_request_workers = max(1, min(16, int(face_request_workers)))
        except (TypeError, ValueError):
            face_request_workers = 8
        return {
            "request_timeout": self.get("settings.request_timeout", 30),
            "retry_attempts": self.get("settings.retry_attempts", 3),
            "face_request_workers": face_request_workers,
        }

    def validate_immich_config(self) -> bool:
        """Validate that required Immich configuration is present."""
        immich_config = self.get_immich_config()

        if immich_config["base_url"] == "https://www.blahblah.com":
            logger.error("❌ Configuration error: Please update the Immich server URL")
            logger.error("   Set it in config.json or use environment variable:")
            logger.error("   IMMICH_BASE_URL")
            return False

        has_api_key = bool(immich_config["api_key"])
        has_credentials = bool(immich_config["email"] and immich_config["password"])
        if not has_api_key and not has_credentials:
            logger.error("❌ Configuration error: API key OR Email and password are required")
            logger.error("   Please set them in config.json or use environment variables:")
            logger.error("   IMMICH_API_KEY or (IMMICH_EMAIL and IMMICH_PASSWORD)")
            logger.error("   Note: If using an API key, it must have at least 'asset.read' and 'face.read' permissions.")
            return False

        return True

    def log_config_summary(self) -> None:
        """Log a summary of loaded configuration."""
        logger.info("\n📋 Configuration Summary:")
        logger.info(f"   Server URL: {self.get('immich.base_url')}")
        logger.info("   Auth Method: API Key" if self.get("immich.api_key") else f"   Auth Method: Email ({self.get('immich.email')})")
        logger.info(f"   Timeout: {self.get('settings.request_timeout')}s")
        logger.info(f"   Retry Attempts: {self.get('settings.retry_attempts')}")
        logger.info(f"   Face Request Workers: {self.get_settings_config()['face_request_workers']}")
        logger.info(f"   JSON output directory: {self.get('output.json_export_dir')}")
        logger.info(f"   XMP output directory: {self.get('output.xmp_export_dir')}")


# --- Immich boundary: transport, search, face hydration ---


def create_http_session(retries: int) -> requests.Session:
    """Create a requests session with automatic retries."""
    session = requests.Session()
    retry_strategy = Retry(
        total = retries,
        backoff_factor = 1,  # Wait 1s, 2s, 4s between retries
        status_forcelist = [429, 500, 502, 503, 504],
        allowed_methods = ["HEAD", "GET", "POST", "OPTIONS"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class ImmichClient:
    """Own the connection and isolate the current Immich API contract.

    Request shape, response parsing, pagination and face hydration stay here.
    A later structured-filter/cursor migration must not change the dictionaries
    consumed by selection or the file writers. No future API path is enabled.
    """

    def __init__(
        self,
        settings: ApiSettings,
        *,
        session: Optional[requests.Session] = None,
        access_token: Optional[str] = None,
    ):
        self.settings = settings
        self.access_token = access_token
        # An injected session belongs to the caller; normal CLI sessions are owned.
        self._owns_session = session is None
        self.session = create_http_session(settings.retry_attempts) if session is None else session

    def __enter__(self) -> "ImmichClient":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self._owns_session:
            self.session.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        session: Optional[requests.Session] = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Send a request using explicit settings and the current authentication."""
        headers = DEFAULT_HEADERS.copy()
        if self.settings.api_key:
            headers["x-api-key"] = self.settings.api_key
        elif self.access_token:
            headers["Cookie"] = f"immich_access_token={self.access_token}"
        transport = self.session if session is None else session
        response = transport.request(
            method,
            f"{self.settings.base_url.rstrip('/')}/api{path}",
            headers=headers,
            timeout=self.settings.request_timeout,
            **kwargs,
        )
        response.raise_for_status()
        return response

    def authenticate(self, email: str, password: str) -> Optional[str]:
        """Log in once; subsequent requests use the returned access token."""
        try:
            self.access_token = self.request(
                "POST", "/auth/login", json={"email": email, "password": password}
            ).json()["accessToken"]
            return self.access_token
        except requests.exceptions.RequestException as e:
            logger.error(f"Authentication failed: {e}")
        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Failed to parse authentication response: {e}")
        return None

    @staticmethod
    def _search_payload(
        page: int,
        album_id: Optional[str],
        library_id: Optional[str],
        known_visible_only: bool,
    ) -> Dict[str, Any]:
        """Preserve the page-based request, without mixing in new fields."""
        payload: Dict[str, Any] = {"page": page, "size": 1000, "withExif": True}
        if known_visible_only:
            payload["withPeople"] = True
        if album_id:
            payload["albumIds"] = [album_id]
        if library_id:
            payload["libraryId"] = library_id
        return payload

    @staticmethod
    def _search_page(search_data: Any, page: int) -> Tuple[List[Dict[str, Any]], Optional[Any]]:
        """Accept the existing nested and flat search response envelopes."""
        assets_data = search_data.get("assets") if isinstance(search_data, dict) else None
        assets_data = assets_data if assets_data is not None else search_data
        if not isinstance(assets_data, dict):
            raise ValueError(f"❌ Error: Unexpected search response shape on page {page} (expected dict)")
        items = assets_data.get("items") or []
        if not isinstance(items, list):
            raise ValueError(f"❌ Error: Unexpected 'items' type on page {page} (expected list)")
        return items, assets_data.get("nextPage")

    @staticmethod
    def _has_known_visible_person(item: Dict[str, Any]) -> bool:
        """Cheap metadata prefilter, not the final face-selection predicate."""
        return any(
            (person.get("name") or "").strip().lower() not in ("", "unknown")
            and not person.get("isHidden")
            for person in item.get("people") or []
            if isinstance(person, dict)
        )

    def get_asset_people(
        self, asset_id: str, *, session: Optional[requests.Session] = None,
    ) -> List[Dict[str, Any]]:
        """Group faces by first-seen person without narrowing nested metadata."""
        if not asset_id:
            return []
        faces = self.request("GET", "/faces", session=session, params={"id": asset_id}).json()
        if not isinstance(faces, list):
            raise ValueError(f"❌ Error: Unexpected faces response shape for asset {asset_id} (expected list)")
        people_by_id: Dict[str, Dict[str, Any]] = {}
        for face in faces:
            if not isinstance(face, dict):
                continue
            person = face.get("person")
            face_data = {key: value for key, value in face.items() if key != "person"}
            if isinstance(person, dict) and person.get("id"):
                person_data = people_by_id.setdefault(str(person["id"]), {**person, "faces": []})
            else:
                person_data = people_by_id.setdefault("__unknown__", {"name": "Unknown", "faces": []})
            person_data["faces"].append(face_data)
        return list(people_by_id.values())

    def _page_people(self, items: List[Dict[str, Any]]) -> Iterator[List[Dict[str, Any]]]:
        """Finish bounded page work, then expose ordered results, not futures.

        Resolving results must remain lazy: a failed face lookup past max_assets
        has run, but must not fail an export that never consumes that result.
        Sessions are closed before returning, even if the iterator is abandoned.
        """
        if not items:
            return iter(())
        worker_count = min(self.settings.face_request_workers, len(items))
        worker_sessions: Queue[requests.Session] = Queue()

        def fetch_people(item: Dict[str, Any]) -> List[Dict[str, Any]]:
            worker_session = worker_sessions.get()
            try:
                return self.get_asset_people(item.get("id", ""), session=worker_session)
            finally:
                worker_sessions.put(worker_session)

        try:
            for _ in range(worker_count):
                worker_sessions.put(create_http_session(self.settings.retry_attempts))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [executor.submit(fetch_people, item) for item in items]
        finally:
            while not worker_sessions.empty():
                worker_sessions.get().close()

        def resolve_people(future: Future) -> List[Dict[str, Any]]:
            try:
                return future.result()
            except StopIteration as e:
                # map/zip must not mistake a worker failure for normal exhaustion.
                raise RuntimeError("Face lookup raised StopIteration") from e

        return map(resolve_people, futures)

    @staticmethod
    def _export_record(item: Dict[str, Any], people: List[Dict[str, Any]]) -> ExportAsset:
        """The same seven-field API projection as the original JSON format."""
        return {
            "asset_id": item.get("id", ""),
            "original_path": item.get("originalPath", ""),
            "file_name": item.get("originalFileName", "Unknown"),
            "width": item.get("width"),
            "height": item.get("height"),
            "exifInfo": item.get("exifInfo") or {},
            "people": people,
        }

    def iter_assets(
        self,
        *,
        album_id: Optional[str] = None,
        library_id: Optional[str] = None,
        known_visible_only: bool = False,
    ) -> Generator[ExportAsset, None, None]:
        """Yield hydrated records in search order; pagination is private here."""
        page = 1
        while True:
            try:
                payload = self._search_payload(page, album_id, library_id, known_visible_only)
                items, next_page = self._search_page(
                    self.request("POST", "/search/metadata", json=payload).json(), page,
                )
                if not items:
                    return
                if known_visible_only:
                    items = [item for item in items if self._has_known_visible_person(item)]
                for item, people in zip(items, self._page_people(items)):
                    yield self._export_record(item, people)
                logger.debug(f"Finished processing page {page}")
                if not next_page:
                    return
                try:
                    page = int(next_page)
                except (TypeError, ValueError):
                    page += 1
            except Exception as e:
                logger.error(f"❌ Error collecting assets on page {page}: {e}")
                raise


# --- Export selection: shared by API and existing JSON records ---


def normalize_people(raw_people: Any) -> List[Dict[str, Any]]:
    """Keep only people that still have at least one face with a complete bounding box."""
    people: List[Dict[str, Any]] = []
    for person in raw_people or []:
        faces = [
            face
            for face in (person.get("faces") or [])
            if all(face.get(key) is not None for key in FACE_BOX_KEYS)
        ]
        if faces:
            people.append({**person, "faces": faces})
    return people


def filter_asset_for_export(asset_data: Mapping[str, Any], export: str) -> Optional[Dict[str, Any]]:
    """Return a copy of asset_data filtered for the selected export mode, or None to skip it."""
    people = normalize_people(asset_data.get("people"))

    if export == "assets-with-known-visible-faces":
        visible_people: List[Dict[str, Any]] = []
        for person in people:
            person_name = (person.get("name") or "").strip()
            if not person_name or person_name.lower() == "unknown" or person.get("isHidden"):
                continue
            faces = [
                face
                for face in (person.get("faces") or [])
                if not face.get("isHidden") and face.get("isVisible", True) is not False
            ]
            if faces:
                visible_people.append({**person, "faces": faces})
        people = visible_people

    if export != "all-assets" and not people:
        return None

    return {**asset_data, "people": people}


def process_assets(
    client: ImmichClient,
    max_assets: Optional[int] = None,
    album_id: Optional[str] = None,
    library_id: Optional[str] = None,
    export: str = "all-assets",
) -> Iterator[Dict[str, Any]]:
    """Apply export selection and its limit, without knowing the search protocol."""
    yielded_assets = 0
    export_label = {
        "all-assets": "assets",
        "assets-with-faces": "assets with faces",
        "assets-with-known-visible-faces": "assets with known visible faces",
    }.get(export, "assets")
    logger.info(f"   Collecting {export_label}...")
    records = client.iter_assets(
        album_id=album_id,
        library_id=library_id,
        known_visible_only=export == "assets-with-known-visible-faces",
    )
    try:
        # Check the selection limit BEFORE pulling the next hydrated result.
        # A for-loop with a limit check in its body would consume one too many.
        while max_assets is None or yielded_assets < max_assets:
            try:
                record = next(records)
            except StopIteration:
                break
            asset_info = filter_asset_for_export(record, export)
            if asset_info is None:
                continue
            yielded_assets += 1
            people = asset_info.get("people") or []
            if logger.isEnabledFor(logging.DEBUG):
                total_faces = sum(len(person.get("faces") or []) for person in people)
                logger.debug(
                    f"Asset {yielded_assets}: {asset_info['file_name']} - "
                    f"{len(people)} people, {total_faces} faces"
                )
            elif yielded_assets % 100 == 0:
                logger.info(f"   Progress: Found {yielded_assets} {export_label}...")
            yield asset_info
        else:
            logger.info(f"   Reached maximum asset limit: {max_assets}")
    finally:
        records.close()
    logger.info(f"✅ Processing completed: Found {yielded_assets} {export_label}")


# --- Pure XMP rendering: geometry, metadata, keywords and face regions ---


def _parse_orientation(orientation: Any) -> int:
    """Parse Exif orientation strings or ints into a standard integer 1-8."""
    if isinstance(orientation, int):
        return orientation
    normalized = str(orientation).lower()
    if any(x in normalized for x in ["6", "right top", "right-top", "90 cw"]):
        return 6
    if any(x in normalized for x in ["3", "bottom right", "180"]):
        return 3
    if any(x in normalized for x in ["8", "left bottom", "left-bottom", "270 cw"]):
        return 8
    if any(x in normalized for x in ["2", "top right", "mirror horizontal"]):
        return 2
    if any(x in normalized for x in ["4", "bottom left", "mirror vertical"]):
        return 4
    if "5" in normalized:
        return 5
    if "7" in normalized:
        return 7
    return 1


def _calculate_unrotated_face_coords(
    face: Dict[str, Any],
    orientation_val: int,
    raw_w: int,
    raw_h: int,
) -> Tuple[float, float, float, float]:
    """Transform Immich visual face coordinates back to raw unrotated space."""
    ml_w = face.get("imageWidth")
    ml_h = face.get("imageHeight")

    # Fallback to visually rotated dimensions if ml_w/ml_h is missing
    if not ml_w or not ml_h:
        is_sideways = orientation_val in [5, 6, 7, 8]
        ml_w, ml_h = (raw_h, raw_w) if is_sideways else (raw_w, raw_h)

    # Normalize coordinates relative to the ML canvas (Visual Space)
    vis_x1 = face.get("boundingBoxX1", 0) / ml_w if ml_w else 0
    vis_y1 = face.get("boundingBoxY1", 0) / ml_h if ml_h else 0
    vis_x2 = face.get("boundingBoxX2", 0) / ml_w if ml_w else 0
    vis_y2 = face.get("boundingBoxY2", 0) / ml_h if ml_h else 0

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
        raw_cx, raw_cy = 1.0 - vis_cx, 1.0 - vis_cy
    elif orientation_val == 4:  # Mirror vertical
        raw_cy = 1.0 - vis_cy
    elif orientation_val == 5:  # Mirror horizontal and rotate 270 CW
        raw_cx, raw_cy, raw_fw, raw_fh = vis_cy, vis_cx, vis_fh, vis_fw
    elif orientation_val == 6:  # Rotate 90 CW (Standard Portrait)
        raw_cx, raw_cy, raw_fw, raw_fh = vis_cy, 1.0 - vis_cx, vis_fh, vis_fw
    elif orientation_val == 7:  # Mirror horizontal and rotate 90 CW
        raw_cx, raw_cy, raw_fw, raw_fh = 1.0 - vis_cy, 1.0 - vis_cx, vis_fh, vis_fw
    elif orientation_val == 8:  # Rotate 270 CW (Inverse Portrait)
        raw_cx, raw_cy, raw_fw, raw_fh = 1.0 - vis_cy, vis_cx, vis_fh, vis_fw

    def clamp01(value: float) -> float:
        # Handle NaN
        if value != value:
            return 0.0
        return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value

    return tuple(clamp01(value) for value in (raw_cx, raw_cy, raw_fw, raw_fh))  # type: ignore[return-value]


def _safe_int(value: Any) -> int:
    """Convert numeric-ish values to int; return 0 for anything invalid."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _resolve_image_geometry(
    asset_data: Mapping[str, Any], people: List[Dict[str, Any]], exif_info: Dict[str, Any],
) -> Tuple[int, int, int]:
    """Resolve raw dimensions and orientation without modifying source metadata."""
    # MWG regions must be relative to the UNROTATED (raw) image dimensions.
    raw_w = _safe_int(exif_info.get("exifImageWidth")) or _safe_int(asset_data.get("width"))
    raw_h = _safe_int(exif_info.get("exifImageHeight")) or _safe_int(asset_data.get("height"))
    orientation_val = _parse_orientation(exif_info.get("orientation"))

    if not raw_w or not raw_h:
        sample_face = next(
            (
                face
                for person in people
                for face in (person.get("faces") or [])
                if _safe_int(face.get("imageWidth")) and _safe_int(face.get("imageHeight"))
            ),
            None,
        )
        if sample_face:
            face_w = _safe_int(sample_face.get("imageWidth"))
            face_h = _safe_int(sample_face.get("imageHeight"))
            # If the ML canvas was sideways, flip back to the raw image orientation.
            raw_w, raw_h = (face_h, face_w) if orientation_val in [5, 6, 7, 8] else (face_w, face_h)

    return raw_w, raw_h, orientation_val


def _xml_text(value: Any) -> str:
    """Escape both element text and attribute values using the original rules."""
    return "" if value is None else _xml_escape(str(value), {'"': "&quot;", "'": "&apos;"})


def _exif_text(exif_info: Dict[str, Any], key: str) -> str:
    value = exif_info.get(key, "")
    return "" if value is None else str(value)


def _xmp_date(value: str) -> str:
    return value if not value or "T" in value else f"{value}T12:00:00"


def _append_tag(lines: List[str], tag: str, value: str) -> None:
    if value:
        lines.append(f"   <{tag}>{_xml_text(value)}</{tag}>")


def _append_metadata_tags(
    lines: List[str], asset_data: Mapping[str, Any], exif_info: Dict[str, Any], raw_w: int, raw_h: int,
) -> None:
    """Append camera, date, dimension, location and identity tags in stable order."""
    for tag, key in (
        ("tiff:Make", "make"),
        ("tiff:Model", "model"),
        ("exif:LensModel", "lensModel"),
        ("exif:FNumber", "fNumber"),
        ("exif:ExposureTime", "exposureTime"),
        ("exif:ISOSpeedRatings", "iso"),
        ("exif:FocalLength", "focalLength"),
        ("photoshop:City", "city"),
        ("photoshop:State", "state"),
        ("photoshop:Country", "country"),
    ):
        _append_tag(lines, tag, _exif_text(exif_info, key))

    for tag, value in (
        ("exif:DateTimeOriginal", _xmp_date(_exif_text(exif_info, "dateTimeOriginal"))),
        ("exif:DateTimeDigitized", _xmp_date(_exif_text(exif_info, "dateTimeDigitized"))),
    ):
        _append_tag(lines, tag, str(value))

    if raw_w and raw_h:
        for tag, value in (
            ("tiff:ImageWidth", raw_w),
            ("tiff:ImageLength", raw_h),
            ("exif:ExifImageWidth", raw_w),
            ("exif:ExifImageHeight", raw_h),
        ):
            _append_tag(lines, tag, str(value))

    latitude = _exif_text(exif_info, "latitude")
    longitude = _exif_text(exif_info, "longitude")
    if latitude and longitude:
        _append_tag(lines, "exif:GPSLatitude", latitude)
        _append_tag(lines, "exif:GPSLongitude", longitude)

    file_name = asset_data.get("file_name") or asset_data.get("originalFileName") or ""
    _append_tag(lines, "xmp:Identifier", file_name)
    lines.append("   <xmp:CreatorTool>Immich XMP Export Tool</xmp:CreatorTool>")


def _render_face_regions(
    people: List[Dict[str, Any]], raw_w: int, raw_h: int, orientation_val: int,
) -> Tuple[List[str], Dict[str, int]]:
    """Render valid rectangles and count only the regions present in the text."""
    lines: List[str] = []
    region_counts: Dict[str, int] = {}
    if not people or not raw_w or not raw_h:
        return lines, region_counts
    lines.extend([
        '   <mwg-rs:Regions rdf:parseType="Resource">',
        "    <mwg-rs:AppliedToDimensions",
        f'     stDim:w="{raw_w}"',
        f'     stDim:h="{raw_h}"',
        '     stDim:unit="pixel"/>',
        "    <mwg-rs:RegionList>",
        "    <rdf:Bag>",
    ])
    for person in people:
        person_name = (person.get("name") or "Unknown").strip() or "Unknown"
        for face in person.get("faces") or []:
            raw_cx, raw_cy, raw_fw, raw_fh = _calculate_unrotated_face_coords(face, orientation_val, raw_w, raw_h)
            if raw_fw <= 0.0 or raw_fh <= 0.0:
                continue
            lines.extend([
                "     <rdf:li>",
                "      <rdf:Description",
                f'       mwg-rs:Name="{_xml_text(person_name)}"',
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
            region_counts[person_name] = region_counts.get(person_name, 0) + 1
    lines.extend(["    </rdf:Bag>", "    </mwg-rs:RegionList>", "   </mwg-rs:Regions>"])
    return lines, region_counts


def render_xmp(
    asset_data: Mapping[str, Any],
    *,
    now: datetime,
    require_face_regions: bool = False,
) -> XmpResult:
    """Render one record with an explicit clock value and no external mutation."""
    people = normalize_people(asset_data.get("people"))
    exif_info = asset_data.get("exifInfo") or {}
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S")
    raw_w, raw_h, orientation_val = _resolve_image_geometry(asset_data, people, exif_info)
    if require_face_regions and (not people or not raw_w or not raw_h):
        return XmpResult("")

    lines = f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
   xmlns:mwg-rs="http://www.metadataworkinggroup.com/schemas/regions/"
   xmlns:dc="http://purl.org/dc/elements/1.1/"
   xmlns:xmp="http://ns.adobe.com/xap/1.0/"
   xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
   xmlns:exif="http://ns.adobe.com/exif/1.0/"
   xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
   xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"
   xmlns:Iptc4xmpCore="http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/"
   xmlns:stDim="http://ns.adobe.com/xap/1.0/sType/Dimensions#"
   xmlns:stArea="http://ns.adobe.com/xmp/sType/Area#"
   xmp:ModifyDate="{timestamp}"
   xmp:MetadataDate="{timestamp}">""".splitlines()

    _append_metadata_tags(lines, asset_data, exif_info, raw_w, raw_h)

    unique_people = sorted({(person.get("name") or "").strip() for person in people} - {"", "Unknown"})
    if unique_people:
        lines.extend(["   <dc:subject>", "    <rdf:Bag>"])
        lines.extend(f"     <rdf:li>{_xml_text(name)}</rdf:li>" for name in unique_people)
        lines.extend(["    </rdf:Bag>", "   </dc:subject>"])

    region_lines, region_counts = _render_face_regions(people, raw_w, raw_h, orientation_val)
    if require_face_regions and not region_counts:
        return XmpResult("")
    lines.extend(region_lines)

    lines.extend(
        """  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
""".splitlines()
    )
    return XmpResult("\n".join(lines), region_counts)


# --- Filesystem boundary: sidecars, streaming JSON, export accounting ---


def save_xmp_sidecar(
    original_path: str,
    xmp_content: str,
    output_dir: str = "",
    *,
    used_paths: Optional[Dict[Path, str]] = None,
) -> bool:
    """Save XMP content to sidecar file, creating same directory structure in output_dir."""
    if not xmp_content.strip():
        return False  # Skip empty XMP

    try:
        # Strip leading slashes to prevent jumping to filesystem root.
        clean_path = str(original_path).lstrip("/\\")

        # Strip Windows drive letters (e.g., C:) if they somehow exist.
        if len(clean_path) > 1 and clean_path[1] == ":":
            clean_path = clean_path[2:].lstrip("/\\")

        original_path_obj = Path(clean_path)

        # Neutralize traversal (..). Without this, output_base / ../../etc can escape.
        safe_parent = Path(*[part for part in original_path_obj.parent.parts if part not in ("..", ".", "")])
        filename = original_path_obj.name + ".xmp"

        if output_dir:
            output_base = Path(output_dir)
            candidate = output_base / safe_parent / filename

            # Enforce containment even after resolving (handles traversal and symlinks).
            base_resolved = output_base.resolve(strict=False)
            xmp_path = candidate.resolve(strict=False)
            if base_resolved != xmp_path and base_resolved not in xmp_path.parents:
                raise ValueError(f"❌ Refusing to write outside output directory: {xmp_path}")
        else:
            xmp_path = Path(filename)

        # Reserve each resolved destination once per export, even if its write
        # fails. A later asset must not silently take over that destination.
        if used_paths is not None:
            path_key = xmp_path.resolve(strict=False)
            if path_key in used_paths:
                raise ValueError(
                    f"Path collision: {original_path!r} and {used_paths[path_key]!r} "
                    f"map to {xmp_path}; skipping the later asset"
                )
            used_paths[path_key] = str(original_path)

        xmp_path.parent.mkdir(parents=True, exist_ok=True)
        with open(xmp_path, "w", encoding="utf-8") as f:
            f.write(xmp_content)

        logger.debug(f"Saved XMP sidecar: {xmp_path}")
        return True
    except (IOError, ValueError) as e:
        logger.error(f"❌ Error saving XMP file: {e}")
        return False


def write_assets_json(
    assets: Iterable[Dict[str, Any]],
    json_output_dir: str,
    *,
    immich_server: str,
) -> Optional[str]:
    """Spool records one at a time, then atomically publish the complete JSON.

    The spool is necessary because the header precedes the final selected count.
    A failed rerun must leave any previous final export untouched.
    """
    output_path = Path(json_output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_filename = f"immich_assets_export_{timestamp}.json"
    json_file_path = output_path / json_filename
    assets_tmp_path = output_path / f".{json_filename}.assets.tmp"
    json_tmp_path = output_path / f".{json_filename}.json.tmp"

    total_assets = 0
    try:
        with open(assets_tmp_path, "w", encoding="utf-8") as assets_file:
            first_asset = True
            for asset_info in assets:
                if not first_asset:
                    assets_file.write(",\n")
                json.dump(asset_info, assets_file, ensure_ascii=False, separators=(",", ":"))
                first_asset = False
                total_assets += 1

        if total_assets == 0:
            logger.warning("No matching assets found")
            return None

        with open(json_tmp_path, "w", encoding="utf-8") as out_file, open(assets_tmp_path, "r", encoding="utf-8") as assets_file:
            out_file.write("{\n")
            out_file.write(f'  "export_timestamp": {json.dumps(datetime.now().isoformat(), ensure_ascii=False)},\n')
            out_file.write(f'  "immich_server": {json.dumps(immich_server.rstrip("/"), ensure_ascii=False)},\n')
            out_file.write(f'  "total_assets": {total_assets},\n')
            out_file.write('  "assets": [\n')
            while True:
                chunk = assets_file.read(1024 * 1024)
                if not chunk:
                    break
                out_file.write(chunk)
            out_file.write("\n  ]\n}\n")

        json_tmp_path.replace(json_file_path)

        json_file_path_abs = json_file_path.absolute()
        logger.info("✅ JSON export completed!")
        logger.info("\n📊 Statistics:")
        logger.info(f"   Total assets exported: {total_assets}")
        logger.info(f"   JSON file: {json_file_path_abs}")
        return str(json_file_path_abs)
    except Exception as e:
        logger.error(f"❌ Error exporting JSON file: {e}")
        return None
    finally:
        for tmp_path in (assets_tmp_path, json_tmp_path):
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass


def _write_export_summary(
    stats: ExportStatistics,
    output_path: Path,
    total_assets: int,
    json_source: Optional[str],
    top_people_to_print: int,
    *,
    success: bool,
) -> bool:
    """Publish complete summary JSON and report any publication failure."""
    summary_file = output_path / "export_summary.json"
    summary_tmp_path = output_path / ".export_summary.json.tmp"
    summary_data: Dict[str, Any] = {
        "export_timestamp": datetime.now().isoformat(),
        "total_assets": total_assets,
        "total_xmp_files_created": stats.files_created,
        "total_faces_processed": stats.faces_processed,
        "unique_people": len(stats.people),
        "people_statistics": stats.people,
        "output_directory": str(output_path.absolute()),
        "export_status": "completed" if success else "failed",
    }
    if json_source:
        summary_data["json_source"] = json_source

    try:
        with open(summary_tmp_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)
        summary_tmp_path.replace(summary_file)
    except IOError as e:
        logger.error(f"❌ Error saving summary file: {e}")
        return False
    finally:
        try:
            summary_tmp_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"⚠️  Could not remove temporary summary file: {e}")

    if success:
        logger.info("✅ XMP export completed!")
    else:
        logger.error("❌ XMP export incomplete. See the errors above.")
    logger.info("\n📊 Statistics:")
    logger.info(f"   Total assets processed: {total_assets}")
    logger.info(f"   XMP sidecar files created: {stats.files_created}")
    logger.info(f"   Total faces processed: {stats.faces_processed}")
    logger.info(f"   Unique people: {len(stats.people)}")
    logger.info(f"   Output directory: {output_path.absolute()}")
    logger.info(f"   Summary file: {summary_file.absolute()}")

    if stats.people:
        logger.info(f"\n👥 People found (top {top_people_to_print}):")
        for person, count in sorted(stats.people.items(), key=lambda item: item[1], reverse=True)[:top_people_to_print]:
            logger.info(f"   {person}: {count} faces")
    return True


def write_xmp_for_assets(
    assets: Iterable[Dict[str, Any]],
    output_dir: str = "xmp_sidecars",
    *,
    total_assets: Optional[int] = None,
    json_source: Optional[str] = None,
    progress_every: int = 500,
    top_people_to_print: int = 10,
    export: str = "all-assets",
) -> bool:
    """
    Take processed assets and write XMP sidecars + a summary file.

    total_assets is optional so the same function works for:
    - a JSON-loaded list
    - a streaming API generator
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # A streamed export can fail after changing sidecars. Invalidate the old
    # summary before making any changes; if that fails, leave sidecars alone.
    try:
        (output_path / "export_summary.json").unlink(missing_ok=True)
    except OSError as e:
        logger.error(f"❌ Cannot invalidate the previous export summary: {e}")
        return False

    stats = ExportStatistics()
    used_paths: Dict[Path, str] = {}
    write_failed = False
    processed_count = 0
    require_face_regions = export != "all-assets"

    count_label = f"for {total_assets} assets" if total_assets else "for streamed assets"
    logger.info(f"\n   Creating XMP files {count_label}...")
    logger.info(f"   Output directory: {output_path.absolute()}")

    try:
        for processed_count, asset_data in enumerate(assets, 1):
            file_label = asset_data.get("file_name") or asset_data.get("originalFileName") or "Unknown"
            rendered = render_xmp(asset_data, now=datetime.now(), require_face_regions=require_face_regions)
            xmp_content = rendered.content
            if not xmp_content.strip():
                logger.warning(f"   Warning: Empty XMP content for asset {file_label}")
            else:
                asset_id = asset_data.get("asset_id") or asset_data.get("id") or f"idx_{processed_count}"
                original_path = asset_data.get("original_path") or asset_data.get("originalPath") or f"unknown_{asset_id}.jpg"

                if save_xmp_sidecar(original_path, xmp_content, str(output_path), used_paths=used_paths):
                    stats.record_write(rendered)
                else:
                    write_failed = True

            if progress_every and processed_count % progress_every == 0:
                suffix = f"/{total_assets}" if total_assets else ""
                logger.info(f"   Progress: {processed_count}{suffix} assets processed")
    except Exception as e:
        logger.error(f"❌ XMP export stopped after asset {processed_count}: {e}")
        return False

    if processed_count == 0:
        logger.warning("No assets found")
        return False

    success = stats.files_created > 0 and not write_failed
    summary_written = _write_export_summary(
        stats, output_path, total_assets or processed_count, json_source, top_people_to_print,
        success=success,
    )

    if stats.files_created == 0:
        logger.error("\n❌ No XMP files were created (all assets were skipped or writes failed).")
        return False

    return success and summary_written


# --- Workflows and command-line entry point ---


def export_assets_to_json(
    client: ImmichClient,
    json_output_dir: str = "json_exports",
    max_assets: Optional[int] = None,
    album_id: Optional[str] = None,
    library_id: Optional[str] = None,
    export: str = "all-assets",
) -> Optional[str]:
    """Stage 1: stream selected API records to the intermediate JSON writer."""
    logger.info("\n   Starting export to JSON format (Stage 1)...")
    return write_assets_json(
        process_assets(client, max_assets, album_id, library_id, export),
        json_output_dir,
        immich_server=client.settings.base_url,
    )


def export_assets_to_xmp_from_json(
    json_file_path: str,
    output_dir: str = "xmp_sidecars",
    export: str = "all-assets",
) -> bool:
    """Export asset data to XMP format from JSON file (Stage 2)."""
    logger.info("   Starting export to XMP format from JSON file (Stage 2)...")
    logger.info(f"   JSON source: {Path(json_file_path).absolute()}")

    try:
        with open(json_file_path, "r", encoding="utf-8") as f:
            export_data = json.load(f)
        if not isinstance(export_data, dict) or not isinstance(export_data.get("assets"), list):
            raise ValueError("Expected a JSON object containing an 'assets' array")
        processed_assets: List[Dict[str, Any]] = []
        for asset in export_data["assets"]:
            if not isinstance(asset, dict):
                raise ValueError("Each entry in 'assets' must be an object")
            filtered_asset = filter_asset_for_export(asset, export)
            if filtered_asset is not None:
                processed_assets.append(filtered_asset)
    except (IOError, ValueError, TypeError, AttributeError) as e:
        logger.error(f"❌ Error loading JSON file: {e}")
        return False

    if not processed_assets:
        logger.warning("No matching assets found in JSON file")
        return False

    logger.info(f"   Loaded {len(processed_assets)} assets from JSON file")
    return write_xmp_for_assets(
        processed_assets,
        output_dir,
        total_assets=len(processed_assets),
        json_source=json_file_path,
        export=export,
    )


def export_assets_to_xmp(
    client: ImmichClient,
    output_dir: str = "xmp_sidecars",
    max_assets: Optional[int] = None,
    album_id: Optional[str] = None,
    library_id: Optional[str] = None,
    export: str = "all-assets",
) -> bool:
    """Direct one-stage export: stream Immich API results straight to XMP sidecars."""
    logger.info("   Starting DIRECT export to XMP format (API -> XMP, no JSON)...")
    return write_xmp_for_assets(
        process_assets(client, max_assets, album_id, library_id, export),
        output_dir,
        export=export,
    )


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Export Immich asset metadata and optional face recognition data to XMP format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run both stages (default): Export to JSON then generate XMP
  python export_xmp.py

  # Run direct one-stage export (no JSON file written)
  python export_xmp.py --direct-xmp

  # Export only assets with any valid face area
  python export_xmp.py --export assets-with-faces

  # Export only assets with known, non-hidden faces; write only those face regions
  python export_xmp.py --export assets-with-known-visible-faces

  # Run only Stage 1: Export to JSON file
  python export_xmp.py --stage1-only

  # Run only Stage 2: Generate XMP from existing JSON file
  python export_xmp.py --stage2-only --json-file path/to/export.json

  # Specify custom output directories
  python export_xmp.py --json-dir my_json_exports --xmp-dir my_xmp_files

  # Filter by a specific album or library
  python export_xmp.py --album-id "your-album-uuid"
  python export_xmp.py --library-id "your-library-uuid"
        """,
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stage1-only", action="store_true", help="Run only Stage 1: Export asset data to JSON file")
    mode.add_argument("--stage2-only", action="store_true", help="Run only Stage 2: Generate XMP files from existing JSON file")
    mode.add_argument("--direct-xmp", action="store_true", help="Run direct export: query Immich and write XMP sidecars directly")

    parser.add_argument(
        "--export",
        choices=["all-assets", "assets-with-faces", "assets-with-known-visible-faces"],
        default="all-assets",
        help="Select which assets and face data to export (default: all-assets)",
    )
    parser.add_argument("--json-file", type=str, help="Path to JSON file for Stage 2 (required with --stage2-only)")
    parser.add_argument("--json-dir", type=str, default=None, help="Directory for JSON exports (default: from config)")
    parser.add_argument("--xmp-dir", type=str, default=None, help="Directory for XMP output (default: from config)")
    parser.add_argument("--max-assets", type=int, default=None, help="Maximum number of assets to process (for debugging)")
    parser.add_argument("--album-id", type=str, default=None, help="Process only assets from this specific album ID")
    parser.add_argument("--library-id", type=str, default=None, help="Process only assets from this specific library ID")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging for detailed outputs")

    args = parser.parse_args()
    if args.stage2_only and not args.json_file:
        parser.error("--json-file is required when using --stage2-only")
    if args.direct_xmp and args.json_file:
        parser.error("--direct-xmp cannot be combined with --json-file")
    return args


def main() -> int:
    """Export metadata and return zero only when the requested stages succeed."""
    args = parse_arguments()
    setup_logging(args.debug)

    if args.stage2_only:
        logger.info("Running Stage 2 only: Generate XMP from JSON file")
        logger.info(f"   Export mode: {args.export}")
        xmp_dir = args.xmp_dir or ConfigLoader().get_output_config()["xmp_export_dir"]
        success = export_assets_to_xmp_from_json(args.json_file, xmp_dir, args.export)
        if success:
            logger.info("\n🎉 XMP files generated successfully from JSON!")
            logger.info(f"   Check the '{Path(xmp_dir).absolute()}' directory for XMP sidecar files.")
        else:
            logger.error("\n❌ Failed to generate XMP files from JSON")
        return 0 if success else 1

    config = ConfigLoader()
    config.log_config_summary()
    if not config.validate_immich_config():
        return 1

    output_config = config.get_output_config()
    immich_config = config.get_immich_config()

    json_dir = args.json_dir or output_config["json_export_dir"]
    xmp_dir = args.xmp_dir or output_config["xmp_export_dir"]

    logger.debug(f"JSON output directory: {json_dir}")
    logger.debug(f"XMP output directory: {xmp_dir}")
    logger.info(f"   Export mode: {args.export}")
    if args.max_assets:
        logger.info(f"   Maximum assets to process: {args.max_assets}")

    with ImmichClient(ApiSettings.from_config(config)) as client:
        if not immich_config["api_key"] and not client.authenticate(immich_config["email"], immich_config["password"]):
            logger.error("❌ Authentication failed. Please check your credentials and server URL.")
            return 1

        if args.direct_xmp:
            logger.info("\n   Running direct XMP export (single stage): API -> XMP (no intermediate JSON)")
            success = export_assets_to_xmp(
                client, xmp_dir, max_assets=args.max_assets, album_id=args.album_id,
                library_id=args.library_id, export=args.export,
            )
            if success:
                logger.info("\n🎉 Direct XMP export completed successfully!")
                logger.info(f"   XMP files: {Path(xmp_dir).absolute()}")
            else:
                logger.error("\n❌ Direct XMP export failed")
            return 0 if success else 1

        json_file_path = export_assets_to_json(
            client, json_dir, max_assets=args.max_assets, album_id=args.album_id,
            library_id=args.library_id, export=args.export,
        )
        if not json_file_path:
            logger.error("❌ Failed to export asset data to JSON")
            return 1

        if args.stage1_only:
            logger.info("\n🎉 Stage 1 completed successfully!")
            logger.info(f"   JSON file created: {json_file_path}")
            logger.info("   Use this file with --stage2-only --json-file to generate XMP files later.")
            return 0

        logger.info("\n   Proceeding to Stage 2: Generate XMP files from JSON...")
        if export_assets_to_xmp_from_json(json_file_path, xmp_dir, args.export):
            logger.info("\n🎉 Both stages completed successfully!")
            return 0
        else:
            logger.error("\n❌ Failed to generate XMP files from JSON")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
