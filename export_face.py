#!/usr/bin/env python3
"""
Face recognition export script for Immich.
Uses search API to get asset IDs, then detailed API to get face data.
Exports face recognition data to DigiKam-compatible XMP format.
"""

import requests
import json
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path


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
            'IMMICH_EMAIL': ['immich', 'email'],
            'IMMICH_PASSWORD': ['immich', 'password'],
            'IMMICH_REQUEST_TIMEOUT': ['settings', 'request_timeout'],
            'IMMICH_RETRY_ATTEMPTS': ['settings', 'retry_attempts'],
            'OUTPUT_DIGIKAM_XMP_DIR': ['output', 'digikam_xmp_dir']
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
            'email': self.get('immich.email', ''),
            'password': self.get('immich.password', '')
        }
    
    def get_output_config(self) -> Dict[str, str]:
        """Get output configuration."""
        return {
            'digikam_xmp_dir': self.get('output.digikam_xmp_dir', 'digikam_xmp_sidecars')
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
        
        if not immich_config['email'] or not immich_config['password']:
            print("❌ Configuration error: Email and password are required")
            print("   Please set them in config.json or use environment variables:")
            print("   IMMICH_EMAIL and IMMICH_PASSWORD")
            return False
        
        if immich_config['base_url'] == 'https://www.blahblah.com':
            print("❌ Configuration error: Please update the Immich server URL")
            print("   Set it in config.json or use environment variable:")
            print("   IMMICH_BASE_URL")
            return False
        
        return True
    
    def print_config_summary(self) -> None:
        """Print a summary of loaded configuration."""
        print("\n📋 Configuration Summary:")
        print(f"   Server URL: {self.get('immich.base_url')}")
        print(f"   Email: {self.get('immich.email')}")
        print(f"   Timeout: {self.get('settings.request_timeout')}s")
        print(f"   Retry Attempts: {self.get('settings.retry_attempts')}")


# Global config loader instance
config = ConfigLoader()

# Get configuration from config file
immich_config = config.get_immich_config()
IMMICH_BASE_URL = immich_config['base_url']
IMMICH_API_BASE = f"{IMMICH_BASE_URL}/api"
output_config = config.get_output_config()

DEFAULT_HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}


def authenticate(email: str, password: str) -> Optional[str]:
    """Authenticate with Immich API and return access token."""
    payload = json.dumps({"email": email, "password": password})
    
    try:
        response = requests.post(f"{IMMICH_API_BASE}/auth/login", 
                               headers=DEFAULT_HEADERS, data=payload)
        response.raise_for_status()
        return response.json()["accessToken"]
    except requests.exceptions.RequestException as e:
        print(f"Authentication failed: {e}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Failed to parse authentication response: {e}")
        return None


def get_auth_headers(access_token: str) -> Dict[str, str]:
    """Get headers with authentication cookie."""
    headers = DEFAULT_HEADERS.copy()
    headers['Cookie'] = f'immich_access_token={access_token}'
    return headers


def get_all_asset_ids(access_token: str) -> List[str]:
    """Get all asset IDs efficiently using search API."""
    asset_ids = []
    page = 1
    
    print("Collecting asset IDs...")
    
    while True:
        try:
            # Search for assets - just get basic info with IDs
            search_payload = {
                "page": page,
                "size": 200,  # Larger batch size for efficiency
                "isVisible": True
            }
            
            response = requests.post(
                f"{IMMICH_API_BASE}/search/metadata",
                headers=get_auth_headers(access_token),
                json=search_payload
            )
            response.raise_for_status()
            
            search_data = response.json()
            assets_data = search_data.get('assets', {})
            items = assets_data.get('items', [])
            
            if not items:
                break
                
            # Extract just the IDs
            page_ids = [item.get('id', '') for item in items if item.get('id')]
            asset_ids.extend(page_ids)
            
            print(f"  Page {page}: Collected {len(page_ids)} IDs, total: {len(asset_ids)}")
            
            # Check next page
            next_page = assets_data.get('nextPage')
            if not next_page:
                print("  No more pages available")
                break
                
            try:
                page = int(next_page)
            except (ValueError, TypeError):
                print(f"Warning: Invalid nextPage value: {next_page}, stopping collection")
                break
            
        except requests.exceptions.RequestException as e:
            print(f"Error collecting asset IDs on page {page}: {e}")
            break
        except (KeyError, json.JSONDecodeError) as e:
            print(f"Error parsing ID collection response on page {page}: {e}")
            break
    
    print(f"✅ Collected {len(asset_ids)} asset IDs")
    return asset_ids


def get_asset_with_faces(access_token: str, asset_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed asset info including face data."""
    try:
        response = requests.get(f"{IMMICH_API_BASE}/assets/{asset_id}", 
                              headers=get_auth_headers(access_token))
        response.raise_for_status()
        
        return response.json()
        
    except requests.exceptions.RequestException as e:
        print(f"Error getting asset {asset_id}: {e}")
        return None
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Error parsing asset response {asset_id}: {e}")
        return None


def create_digikam_xmp_content(asset_data: Dict[str, Any]) -> str:
    """Create DigiKam-compatible XMP content for face recognition data."""
    
    # Get people data from asset
    people = asset_data.get('people', [])
    if not people:
        return ""
    
    # XMP header
    xmp_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 4.4.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
   xmlns:mwg-rs="http://www.metadataworkinggroup.com/schemas/regions/"
   xmlns:dc="http://purl.org/dc/elements/1.1/"
   xmlns:xmp="http://ns.adobe.com/xap/1.0/"
   xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
   xmlns:stDim="http://ns.adobe.com/xap/1.0/sType/Dimensions#"
   xmlns:stArea="http://ns.adobe.com/xap/1.0/sType/Area#"
   mwg-rs:Regions=""
   xmp:ModifyDate="{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}"
   xmp:MetadataDate="{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}">
   <mwg-rs:Regions>
    <rdf:Bag>
'''
    
    # Add face regions from people data
    for person in people:
        person_name = person.get('name', 'Unknown')
        for face in person.get('faces', []):
            # Convert bounding box to XMP format (normalized coordinates)
            x1 = face.get('boundingBoxX1', 0)
            y1 = face.get('boundingBoxY1', 0)
            x2 = face.get('boundingBoxX2', 0)
            y2 = face.get('boundingBoxY2', 0)
            
            # Calculate center and dimensions
            width = x2 - x1
            height = y2 - y1
            center_x = x1 + width / 2
            center_y = y1 + height / 2
            
            # Get image dimensions for normalization
            exif_info = asset_data.get('exifInfo', {})
            image_width = exif_info.get('exifImageWidth', 2160)
            image_height = exif_info.get('exifImageHeight', 1440)
            
            # Normalize coordinates (0-1 range)
            norm_x = center_x / image_width if image_width > 0 else 0
            norm_y = center_y / image_height if image_height > 0 else 0
            norm_w = width / image_width if image_width > 0 else 0
            norm_h = height / image_height if image_height > 0 else 0
            
            # XMP region format
            region_xml = f'''     <rdf:li>
      <rdf:Description
       mwg-rs:Name="{person_name}"
       mwg-rs:Type="Face"
       mwg-rs:Extensions="">
       <mwg-rs:Area
        stArea:x="{norm_x:.6f}"
        stArea:y="{norm_y:.6f}"
        stArea:w="{norm_w:.6f}"
        stArea:h="{norm_h:.6f}"
        stArea:unit="normalized"/>
      </rdf:Description>
     </rdf:li>
'''
            xmp_content += region_xml
    
    # XMP footer
    xmp_content += '''    </rdf:Bag>
   </mwg-rs:Regions>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
'''
    
    return xmp_content


def save_xmp_sidecar(original_path: str, xmp_content: str, output_dir: str = "") -> bool:
    """Save XMP content to sidecar file, creating same directory structure in output_dir."""
    if not xmp_content.strip():
        return False  # Skip empty XMP
        
    try:
        # Create sidecar filename (same name with .xmp extension)
        original_path_obj = Path(original_path)
        filename = original_path_obj.stem + '.xmp'
        
        if output_dir:
            output_base = Path(output_dir)
            
            # Extract relative path from original path
            # For: /myphoto/2025-09/yuhuan/file.jpg -> relative: 2025-09/yuhuan/file.xmp
            original_parts = original_path_obj.parts
            
            # Build relative directory structure (skip root like '/myphoto')
            if len(original_parts) > 2:  # Has subdirectories
                # Take all parts after the root directory
                relative_parts = original_parts[1:-1]  # Skip root and filename
                if relative_parts:
                    relative_dir = Path(*relative_parts)
                    xmp_path = output_base / relative_dir / filename
                else:
                    xmp_path = output_base / filename
            else:
                # Just filename, no subdirectories
                xmp_path = output_base / filename
            
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


def process_assets_with_faces(access_token: str) -> List[Dict[str, Any]]:
    """Process assets and collect those with face recognition data."""
    processed_assets = []
    
    print("Step 1: Collecting asset IDs...")
    asset_ids = get_all_asset_ids(access_token)
    
    if not asset_ids:
        print("No asset IDs collected")
        return processed_assets
    
    print(f"\nStep 2: Processing {len(asset_ids)} assets for face data...")
    
    # Process assets one by one to avoid hardcoded batch limits
    total_with_faces = 0
    
    for i, asset_id in enumerate(asset_ids):
        detailed_asset = get_asset_with_faces(access_token, asset_id)
        
        if detailed_asset:
            people = detailed_asset.get('people', [])
            
            if people and len(people) > 0:
                # Count total faces
                total_faces = sum(len(person.get('faces', [])) for person in people)
                
                file_name = detailed_asset.get('originalFileName', 'Unknown')
                print(f"    Asset {total_with_faces+1}: {file_name} - {len(people)} people, {total_faces} faces")
                
                # Prepare asset info
                asset_info = {
                    'asset_id': asset_id,
                    'original_path': detailed_asset.get('originalPath', ''),
                    'file_name': file_name,
                    'exifInfo': detailed_asset.get('exifInfo', {}),
                    'people': people  # Include people data directly in asset
                }
                
                processed_assets.append(asset_info)
                
                total_with_faces += 1
        
        # Progress update every 20 assets
        if (i + 1) % 20 == 0:
            print(f"    Progress: {i+1}/{len(asset_ids)} assets processed")
    
    print(f"\n✅ Processing completed: Found {total_with_faces} assets with faces")
    return processed_assets


def export_faces_to_digikam_xmp(access_token: str, output_dir: str = "xmp_sidecars") -> bool:
    """Export face recognition data to DigiKam XMP sidecar files."""
    print("Starting face recognition export to DigiKam XMP format...")
    
    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    # Process assets with faces
    processed_assets = process_assets_with_faces(access_token)
    
    if not processed_assets:
        print("No assets with faces found")
        return False
    
    total_files_created = 0
    total_faces_processed = 0
    person_stats = {}
    
    print(f"\nCreating XMP files for {len(processed_assets)} assets with faces...")
    
    for i, asset_data in enumerate(processed_assets):
        people_data = asset_data.get('people', [])
        
        if people_data:
            # Create XMP content
            xmp_content = create_digikam_xmp_content(asset_data)
            
            if xmp_content.strip():  # Only proceed if XMP content is not empty
                # Save XMP sidecar file
                original_path = asset_data.get('original_path', f"unknown_{asset_data['asset_id']}.jpg")
                if save_xmp_sidecar(original_path, xmp_content, str(output_path)):
                    total_files_created += 1
                    total_faces_processed += sum(len(person.get('faces', [])) for person in people_data)
                    
                    # Update person statistics
                    for person in people_data:
                        person_name = person.get('name', 'Unknown')
                        if person_name not in person_stats:
                            person_stats[person_name] = 0
                        person_stats[person_name] += len(person.get('faces', []))
                    
                    if (i + 1) % 5 == 0:  # Progress update every 5 files
                        print(f"    Progress: {i+1}/{len(processed_assets)} XMP files created")
            else:
                print(f"    Warning: Empty XMP content for asset {asset_data.get('file_name', 'Unknown')}")
        else:
            print(f"    Warning: No people data for asset {asset_data.get('file_name', 'Unknown')}")
    
    # Create summary file
    summary_file = output_path / "export_summary.json"
    summary_data = {
        'export_timestamp': datetime.now().isoformat(),
        'total_assets': len(processed_assets),
        'total_xmp_files_created': total_files_created,
        'total_faces_processed': total_faces_processed,
        'people_statistics': person_stats,
        'output_directory': str(output_path.absolute())
    }
    
    try:
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"Error saving summary file: {e}")
    
    print(f"\n✅ DigiKam XMP export completed!")
    print(f"📊 Statistics:")
    print(f"   Total assets processed: {len(processed_assets)}")
    print(f"   XMP sidecar files created: {total_files_created}")
    print(f"   Total faces processed: {total_faces_processed}")
    print(f"   Unique people: {len(person_stats)}")
    print(f"   Output directory: {output_path.absolute()}")
    print(f"   Summary file: {summary_file}")
    
    # Print person statistics
    if person_stats:
        print(f"\n👥 People found:")
        for person, count in sorted(person_stats.items(), key=lambda x: x[1], reverse=True)[:20]:  # Top 20
            print(f"   {person}: {count} faces")
    
    return True


def main():
    """Main function to export face recognition data to DigiKam XMP format."""
    # Print configuration summary
    config.print_config_summary()
    
    # Validate configuration
    if not config.validate_immich_config():
        return
    
    # Get configuration
    immich_config = config.get_immich_config()
    email = immich_config['email']
    password = immich_config['password']
    output_config = config.get_output_config()
    output_dir = output_config['digikam_xmp_dir']
    
    print("Starting Immich to DigiKam XMP export...")
    print(f"Server: {IMMICH_BASE_URL}")
    print(f"Output directory: {output_dir}")
    
    # Step 1: Authenticate
    access_token = authenticate(email, password)
    if not access_token:
        print("❌ Authentication failed. Please check your credentials and server URL.")
        return
    
    print("✅ Authentication successful")
    
    # Step 2: Export to DigiKam XMP format
    success = export_faces_to_digikam_xmp(access_token, output_dir)
    
    if success:
        print(f"\n🎉 Face recognition data exported successfully to DigiKam XMP format!")
        print(f"   Check the '{output_dir}' directory for XMP sidecar files.")
    else:
        print("\n❌ Failed to export face recognition data to XMP format")


if __name__ == "__main__":
    main()