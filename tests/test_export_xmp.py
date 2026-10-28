"""Offline regression tests for the JSON, face-region, and XMP export paths."""

import copy
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from queue import Queue
from unittest.mock import Mock, patch

import export_xmp as exporter


NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "exif": "http://ns.adobe.com/exif/1.0/",
    "mwg": "http://www.metadataworkinggroup.com/schemas/regions/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "stArea": "http://ns.adobe.com/xmp/sType/Area#",
    "stDim": "http://ns.adobe.com/xap/1.0/sType/Dimensions#",
    "tiff": "http://ns.adobe.com/tiff/1.0/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
}
REGION_PATH = ".//mwg:RegionList/rdf:Bag/rdf:li/rdf:Description"


def setUpModule():
    """Any accidental real HTTP request must fail the offline suite."""
    guard = patch.object(exporter.requests.Session, "request", side_effect=AssertionError("Unexpected HTTP request"))
    blocked_requests = guard.start()
    unittest.addModuleCleanup(guard.stop)
    # Export error handlers may catch exceptions; check attempted requests too.
    unittest.addModuleCleanup(blocked_requests.assert_not_called)


class TimedQueue(Queue):
    """Bound session-pool waits in tests that run actual face workers."""

    def get(self, block=True, timeout=None):
        return super().get(block=block, timeout=2 if timeout is None else timeout)


def face(**changes):
    # Synthetic coordinates on a representative 1440 x 1920 ML canvas.
    value = {
        "boundingBoxX1": 144,
        "boundingBoxY1": 384,
        "boundingBoxX2": 432,
        "boundingBoxY2": 1152,
        "imageWidth": 1440,
        "imageHeight": 1920,
    }
    value.update(changes)
    return value


def person(name="Ada", faces=None, **changes):
    value = {"name": name, "faces": [face()] if faces is None else faces, "isHidden": False}
    value.update(changes)
    return value


def asset(asset_id="photo-1", people=None, original_path=None):
    # EXIF dimensions are raw; asset dimensions may describe the rotated image.
    return {
        "asset_id": asset_id,
        "original_path": original_path or f"/photos/2026/{asset_id}.jpg",
        "file_name": f"{asset_id}.jpg",
        "width": 3024,
        "height": 4032,
        "exifInfo": {
            "orientation": "6",
            "exifImageWidth": 4032,
            "exifImageHeight": 3024,
            "dateTimeOriginal": "2026-07-22T12:34:56+03:00",
            "make": "Example Camera",
        },
        "people": [] if people is None else people,
    }


def make_client(config=None, *, access_token=None):
    """Unit-test client with a borrowed, inert transport and explicit settings."""
    settings = (exporter.ApiSettings.from_config(config) if config is not None else
                exporter.ApiSettings(base_url="https://example.invalid/", api_key="key"))
    return exporter.ImmichClient(settings, session=Mock(spec=exporter.requests.Session), access_token=access_token)


def region_names(root):
    return [node.get(f"{{{NS['mwg']}}}Name") for node in root.findall(REGION_PATH, NS)]


def xmp_semantics(content):
    """Compare metadata exactly while treating RDF bags as unordered collections."""
    def visit(node):
        children = [visit(child) for child in node]
        if node.tag == f"{{{NS['rdf']}}}Bag":
            children.sort()
        text = node.text if node.text and node.text.strip() else ""
        return node.tag, tuple(sorted(node.attrib.items())), text, tuple(children)
    return visit(ET.fromstring(content))


def raw_api_fixture():
    # Hand-authored synthetic API responses, NOT generated from asset()/person()/face().
    # These exercise the expected contract; they are not recordings from an Immich release.
    pages = {
        1: {"assets": {"items": [
            {"id": "unknown", "originalPath": "/photos/unknown.jpg", "originalFileName": "unknown.jpg",
             "width": 4000, "height": 3000, "exifInfo": {"orientation": "1"}, "people": []},
            {"id": "empty", "originalPath": "/photos/empty.jpg", "originalFileName": "empty.jpg",
             "width": 640, "height": 480, "exifInfo": None, "people": []},
        ], "nextPage": "2"}},
        2: {"assets": {"items": [
            {"id": "portrait", "originalPath": "/photos/portrait.jpg", "originalFileName": "portrait.jpg",
             "width": 3024, "height": 4032,
             "exifInfo": {"orientation": "6", "exifImageWidth": 4032, "exifImageHeight": 3024,
                          "make": "Example Camera", "dateTimeOriginal": "2026-07-22T12:34:56+03:00"},
             "people": [{"id": "p1", "name": "Éva", "isHidden": False}]},
        ], "nextPage": None}},
    }
    faces = {
        "unknown": [
            {"id": "f0", "boundingBoxX1": 0, "boundingBoxY1": 0,
             "boundingBoxX2": 100, "boundingBoxY2": 150, "imageWidth": 1000, "imageHeight": 750,
             "person": None},
        ],
        "empty": [],
        "portrait": [
            {"id": "f1", "boundingBoxX1": 144, "boundingBoxY1": 384,
             "boundingBoxX2": 432, "boundingBoxY2": 1152, "imageWidth": 1440, "imageHeight": 1920,
             "person": {"id": "p1", "name": "Éva", "isHidden": False}},
            {"id": "f2", "boundingBoxX1": 720, "boundingBoxY1": 192,
             "boundingBoxX2": 1008, "boundingBoxY2": 768, "imageWidth": 1440, "imageHeight": 1920,
             "isVisible": False, "person": {"id": "p1", "name": "Éva", "isHidden": False}},
            {"id": "f3", "boundingBoxX1": 400, "boundingBoxY1": 500,
             "boundingBoxX2": 600, "boundingBoxY2": 700, "imageWidth": 1440, "imageHeight": 1920,
             "person": {"id": "p2", "name": "Bea", "isHidden": True}},
            {"id": "f4", "boundingBoxX1": 800, "boundingBoxY1": 900,
             "boundingBoxX2": 1000, "boundingBoxY2": 1100, "imageWidth": 1440, "imageHeight": 1920,
             "person": None},
            {"id": "f5", "boundingBoxX1": 300, "boundingBoxY1": 400,
             "boundingBoxX2": 500, "boundingBoxY2": 600, "imageWidth": 1440, "imageHeight": 1920,
             "isHidden": True, "person": {"id": "p1", "name": "Éva", "isHidden": False}},
        ],
    }
    return pages, faces


class FaceSelectionTests(unittest.TestCase):
    def test_modes_keep_expected_faces_without_mutating_source(self):
        valid = face()
        incomplete = face(boundingBoxY2=None)
        original = asset(people=[
            person("Ada", [valid, face(isHidden=True), face(isVisible=False), incomplete]),
            person("Unknown"),
            person("Bea", isHidden=True),
            person("  "),
        ])
        snapshot = copy.deepcopy(original)

        all_assets = exporter.filter_asset_for_export(original, "all-assets")
        any_faces = exporter.filter_asset_for_export(original, "assets-with-faces")
        known = exporter.filter_asset_for_export(original, "assets-with-known-visible-faces")

        self.assertEqual([3, 1, 1, 1], [len(p["faces"]) for p in all_assets["people"]])
        self.assertEqual(["Ada", "Unknown", "Bea", "  "], [p["name"] for p in any_faces["people"]])
        self.assertEqual([3, 1, 1, 1], [len(p["faces"]) for p in any_faces["people"]])
        self.assertEqual(["Ada"], [p["name"] for p in known["people"]])
        self.assertEqual([valid], known["people"][0]["faces"])
        self.assertEqual(original, snapshot)

    def test_empty_or_incomplete_faces_are_skipped_only_in_face_modes(self):
        for people in (None, [], [person(faces=[])], [person(faces=[face(boundingBoxX1=None)])]):
            for mode in ("all-assets", "assets-with-faces", "assets-with-known-visible-faces"):
                with self.subTest(people=people, mode=mode):
                    photo = asset(people=people)
                    photo["people"] = people
                    result = exporter.filter_asset_for_export(photo, mode)
                    if mode == "all-assets":
                        self.assertEqual([], result["people"])
                        self.assertEqual(photo["exifInfo"], result["exifInfo"])
                    else:
                        self.assertIsNone(result)

    def test_known_mode_rejects_blank_and_case_insensitive_unknown_names(self):
        for name in (None, "", "  ", "Unknown", " UNKNOWN ", "unknown"):
            with self.subTest(name=name):
                self.assertIsNone(exporter.filter_asset_for_export(
                    asset(people=[person(name)]), "assets-with-known-visible-faces"
                ))

    @patch("export_xmp.ImmichClient.request")
    def test_api_faces_are_grouped_by_person_and_unknown(self, request):
        request.return_value.json.return_value = [
            {**face(), "person": {"id": "p1", "name": "Ada"}},
            {**face(boundingBoxX1=500), "person": {"id": "p1", "name": "Ada"}},
            {**face(), "person": None},
            "unexpected face entry",
        ]
        client = make_client(access_token="token")
        groups = client.get_asset_people("photo-1")
        self.assertEqual(["Ada", "Unknown"], [p["name"] for p in groups])
        self.assertEqual([2, 1], [len(p["faces"]) for p in groups])
        self.assertNotIn("person", groups[0]["faces"][0])
        self.assertEqual({"id": "photo-1"}, request.call_args.kwargs["params"])
        self.assertEqual([], client.get_asset_people(""))
        request.return_value.json.return_value = {"not": "a list"}
        with self.assertRaisesRegex(ValueError, "faces response shape"):
            client.get_asset_people("photo-1")


class RegionTests(unittest.TestCase):
    def test_inverse_exif_orientations_preserve_center_and_size(self):
        box = {
            "boundingBoxX1": 100, "boundingBoxX2": 300,
            "boundingBoxY1": 100, "boundingBoxY2": 300,
            "imageWidth": 1000, "imageHeight": 500,
        }
        # Visual (x, y, w, h) = (.2, .4, .2, .4).
        expected = {
            1: (.2, .4, .2, .4), 2: (.8, .4, .2, .4),
            3: (.8, .6, .2, .4), 4: (.2, .6, .2, .4),
            5: (.4, .2, .4, .2), 6: (.4, .8, .4, .2),
            7: (.6, .8, .4, .2), 8: (.6, .2, .4, .2),
        }
        for orientation, values in expected.items():
            with self.subTest(orientation=orientation):
                actual = exporter._calculate_unrotated_face_coords(box, orientation, 4032, 3024)
                for a, b in zip(actual, values):
                    self.assertAlmostEqual(a, b)

        # If the ML canvas is absent, sideways images use reversed raw dimensions.
        without_canvas = {k: v for k, v in face().items() if not k.startswith("image")}
        without_canvas.update(boundingBoxX1=300, boundingBoxX2=900, boundingBoxY1=400, boundingBoxY2=1200)
        actual = exporter._calculate_unrotated_face_coords(without_canvas, 6, 4000, 3000)
        self.assertEqual((.2, .8, .2, .2), tuple(round(v, 6) for v in actual))

    def test_xmp_is_valid_xml_with_escaped_names_and_raw_dimensions(self):
        photo = asset(people=[person('Ada & <Bea> "C"')])
        photo["exifInfo"].update(city="A & B", latitude=59.25, longitude=25.1)
        root = ET.fromstring(exporter.render_xmp(photo, require_face_regions=True, now=datetime(2026, 1, 2, 3, 4, 5)).content)

        dimensions = root.find(".//mwg:AppliedToDimensions", NS)
        self.assertEqual("4032", dimensions.get(f"{{{NS['stDim']}}}w"))
        self.assertEqual("3024", dimensions.get(f"{{{NS['stDim']}}}h"))
        self.assertEqual("4032", root.findtext(".//tiff:ImageWidth", namespaces=NS))
        region = root.find(".//mwg:Area", NS)
        self.assertIsNotNone(region)
        self.assertEqual(
            ("0.400000", "0.800000", "0.400000", "0.200000"),
            tuple(region.get(f"{{{NS['stArea']}}}{key}") for key in ("x", "y", "w", "h")),
        )
        self.assertEqual(['Ada & <Bea> "C"'], region_names(root))
        self.assertEqual('Ada & <Bea> "C"', root.findtext(".//dc:subject/rdf:Bag/rdf:li", namespaces=NS))
        self.assertEqual("A & B", root.findtext(".//photoshop:City", namespaces=NS))
        self.assertEqual("normalized", region.get(f"{{{NS['stArea']}}}unit"))

    def test_orientation_strings_and_missing_values(self):
        cases = [(str(n), n) for n in range(1, 9)] + [
            (None, 1), ("0", 1), ("unrecognized", 1), ("right-top", 6),
            ("Rotate 180", 3), ("Rotate 270 CW", 8),
            ("Mirror horizontal", 2), ("Mirror vertical", 4),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(expected, exporter._parse_orientation(value))

    def test_dimension_fallbacks_use_asset_then_face_canvas(self):
        for dimensions, expected in (({"width": 4000, "height": 3000}, (4000, 3000)),
                                     ({"width": None, "height": None}, (1920, 1440))):
            with self.subTest(dimensions=dimensions):
                photo = asset(people=[person()])
                photo.update(dimensions)
                photo["exifInfo"] = {"orientation": "6"}
                root = ET.fromstring(exporter.render_xmp(photo, require_face_regions=True, now=datetime(2026, 1, 2, 3, 4, 5)).content)
                actual = root.find(".//mwg:AppliedToDimensions", NS)
                self.assertEqual(tuple(map(str, expected)), tuple(
                    actual.get(f"{{{NS['stDim']}}}{key}") for key in ("w", "h")
                ))

    def test_camera_dates_gps_and_identifier_survive_serialization(self):
        photo = asset()
        photo["exifInfo"].update({
            "model": "Camera B", "lensModel": "Lens & Co", "fNumber": 1.7,
            "exposureTime": "1/160", "iso": 64, "focalLength": 5.11,
            "city": "A & B", "state": "North", "country": "Example",
            "dateTimeDigitized": "2026-07-21", "latitude": 0, "longitude": -25.1,
        })
        root = ET.fromstring(exporter.render_xmp(photo, now=datetime(2026, 1, 2, 3, 4, 5)).content)
        expected = {
            "tiff:Make": "Example Camera", "tiff:Model": "Camera B",
            "exif:LensModel": "Lens & Co", "exif:FNumber": "1.7",
            "exif:ExposureTime": "1/160", "exif:ISOSpeedRatings": "64",
            "exif:FocalLength": "5.11", "photoshop:City": "A & B",
            "photoshop:State": "North", "photoshop:Country": "Example",
            "exif:DateTimeOriginal": "2026-07-22T12:34:56+03:00",
            "exif:DateTimeDigitized": "2026-07-21T12:00:00",
            "exif:GPSLatitude": "0", "exif:GPSLongitude": "-25.1",
            "xmp:Identifier": "photo-1.jpg",
        }
        for tag, value in expected.items():
            with self.subTest(tag=tag):
                self.assertEqual(value, root.findtext(f".//{tag}", namespaces=NS))

    def test_keywords_are_unique_but_each_face_keeps_its_region(self):
        photo = asset(people=[person("Bea"), person("Ada", [face(), face()]), person("Ada"), person("Unknown")])
        root = ET.fromstring(exporter.render_xmp(photo, now=datetime(2026, 1, 2, 3, 4, 5)).content)
        self.assertEqual(["Ada", "Bea"], [node.text for node in root.findall(".//dc:subject/rdf:Bag/rdf:li", NS)])
        self.assertCountEqual(["Bea", "Ada", "Ada", "Ada", "Unknown"], region_names(root))

    def test_required_regions_reject_missing_or_zero_area(self):
        self.assertEqual("", exporter.render_xmp(asset(), require_face_regions=True, now=datetime(2026, 1, 2, 3, 4, 5)).content)
        zero_area = asset(people=[person(faces=[face(boundingBoxX2=144)])])
        self.assertEqual("", exporter.render_xmp(zero_area, require_face_regions=True, now=datetime(2026, 1, 2, 3, 4, 5)).content)
        ET.fromstring(exporter.render_xmp(asset(), now=datetime(2026, 1, 2, 3, 4, 5)).content)  # All-assets allows metadata-only sidecars.


class ExportPipelineTests(unittest.TestCase):
    def test_raw_api_to_json_and_both_xmp_paths_for_every_export_mode(self):
        pages, faces = raw_api_fixture()
        cases = [
            ("all-assets", ["unknown", "empty", "portrait"], ["Éva", "Éva", "Éva", "Bea", "Unknown"]),
            ("assets-with-faces", ["unknown", "portrait"], ["Éva", "Éva", "Éva", "Bea", "Unknown"]),
            ("assets-with-known-visible-faces", ["portrait"], ["Éva"]),
        ]
        for mode, expected_ids, expected_names in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                config = Mock()
                config.get_settings_config.return_value = {
                    "face_request_workers": 2, "retry_attempts": 0, "request_timeout": 12,
                }
                config.get_immich_config.return_value = {"base_url": "https://example.invalid", "api_key": "key"}

                def http_response(method, url, **kwargs):
                    if (method, url) == ("POST", "https://example.invalid/api/search/metadata"):
                        payload = kwargs["json"]
                        data = pages[payload["page"]]
                    elif (method, url) == ("GET", "https://example.invalid/api/faces"):
                        data = faces[kwargs["params"]["id"]]
                    else:
                        self.fail(f"Unexpected HTTP request: {method} {url}")
                    response = exporter.requests.Response()
                    response.status_code = 200
                    response._content = json.dumps(data).encode("utf-8")
                    response.encoding = "utf-8"
                    return response

                with (
                    patch.object(exporter, "ConfigLoader", return_value=config),
                    patch.object(exporter, "Queue", TimedQueue),
                    patch.object(exporter.requests.Session, "request", side_effect=http_response) as transport,
                    patch.object(exporter, "datetime") as clock,
                    configured_client() as client,
                ):
                    clock.now.return_value = datetime(2026, 7, 22, 12)
                    source = exporter.export_assets_to_json(client, json_output_dir=directory, export=mode)
                    photos = json.loads(Path(source).read_text(encoding="utf-8"))["assets"]
                    self.assertEqual(sorted(expected_ids), [photo["asset_id"] for photo in photos])
                    portrait = next(photo for photo in photos if photo["asset_id"] == "portrait")
                    self.assertEqual("/photos/portrait.jpg", portrait["original_path"])
                    self.assertEqual("portrait.jpg", portrait["file_name"])
                    self.assertEqual((3024, 4032), (portrait["width"], portrait["height"]))
                    self.assertEqual({
                        "orientation": "6", "exifImageWidth": 4032, "exifImageHeight": 3024,
                        "make": "Example Camera", "dateTimeOriginal": "2026-07-22T12:34:56+03:00",
                    }, portrait["exifInfo"])
                    expected_faces = (
                        ["f1"] if mode == "assets-with-known-visible-faces" else ["f1", "f2", "f5", "f3", "f4"]
                    )
                    self.assertCountEqual(expected_faces, [f["id"] for p in portrait["people"] for f in p["faces"]])

                    staged, direct = Path(directory) / "staged", Path(directory) / "direct"
                    self.assertTrue(exporter.export_assets_to_xmp_from_json(source, str(staged), mode))
                    self.assertTrue(exporter.export_assets_to_xmp(client, output_dir=str(direct), export=mode))
                    for output in (staged, direct):
                        self.assertEqual(
                            {f"{key}.jpg.xmp" for key in expected_ids}, {p.name for p in output.rglob("*.xmp")}
                        )
                        root = ET.parse(output / "photos/portrait.jpg.xmp").getroot()
                        self.assertCountEqual(expected_names, region_names(root))
                        named_region = next(node for node in root.findall(REGION_PATH, NS)
                                            if node.get(f"{{{NS['mwg']}}}Name") == "Éva")
                        area = named_region.find("mwg:Area", NS)
                        self.assertEqual((.4, .8, .4, .2), tuple(
                            float(area.get(f"{{{NS['stArea']}}}{key}")) for key in ("x", "y", "w", "h")
                        ))
                    for key in expected_ids:
                        relative = f"photos/{key}.jpg.xmp"
                        self.assertEqual(xmp_semantics((staged / relative).read_bytes()),
                                         xmp_semantics((direct / relative).read_bytes()))
                    # Both runs must reach page 2 even when known-visible skips all of page 1.
                    search_pages = [c.kwargs["json"]["page"] for c in transport.call_args_list if c.args[0] == "POST"]
                    self.assertEqual([1, 2, 1, 2], search_pages)
                    expected_lookups = ["portrait"] if mode == "assets-with-known-visible-faces" else [
                        "unknown", "empty", "portrait",
                    ]
                    actual_lookups = []
                    # Assert outside exporter calls so its broad exception handler cannot hide failures.
                    for call in transport.call_args_list:
                        method, url = call.args
                        self.assertEqual("key", call.kwargs["headers"]["x-api-key"])
                        self.assertEqual(12, call.kwargs["timeout"])
                        if method == "POST":
                            self.assertEqual("https://example.invalid/api/search/metadata", url)
                            payload = call.kwargs["json"]
                            self.assertTrue(payload["withExif"])
                            self.assertEqual(
                                mode == "assets-with-known-visible-faces", payload.get("withPeople", False)
                            )
                        else:
                            self.assertEqual(("GET", "https://example.invalid/api/faces"), (method, url))
                            actual_lookups.append(call.kwargs["params"]["id"])
                    self.assertCountEqual(expected_lookups * 2, actual_lookups)

    @patch("export_xmp.ImmichClient._page_people")
    @patch("export_xmp.ImmichClient.request")
    def test_search_paginates_and_limit_counts_exported_assets(self, request, page_people):
        pages = [
            {"assets": {"items": [{"id": "no-face"}, {"id": "photo-1"}], "nextPage": "2"}},
            {"assets": {"items": [{"id": "photo-2"}, {"id": "photo-3"}], "nextPage": None}},
        ]
        request.side_effect = [Mock(json=Mock(return_value=page)) for page in pages]
        page_people.side_effect = lambda items: [
            [] if item["id"] == "no-face" else [person()] for item in items
        ]
        photos = list(exporter.process_assets(
            make_client(), max_assets=2, album_id="album-a", library_id="library-b", export="assets-with-faces"
        ))

        self.assertEqual(["photo-1", "photo-2"], [p["asset_id"] for p in photos])
        self.assertEqual([1, 2], [call.kwargs["json"]["page"] for call in request.call_args_list])
        for call in request.call_args_list:
            self.assertEqual("/search/metadata", call.args[1])
            self.assertEqual(["album-a"], call.kwargs["json"]["albumIds"])
            self.assertEqual("library-b", call.kwargs["json"]["libraryId"])
            self.assertTrue(call.kwargs["json"]["withExif"])
            self.assertNotIn("withPeople", call.kwargs["json"])

    @patch("export_xmp.ImmichClient._page_people")
    @patch("export_xmp.ImmichClient.request")
    def test_known_visible_search_filters_metadata_before_face_lookup(self, request, page_people):
        items = [
            {"id": "hidden", "people": [{"name": "Ada", "isHidden": True}]},
            {"id": "unknown", "people": [{"name": "Unknown"}]},
            {"id": "visible", "people": [{"name": "Ada"}]},
        ]
        request.return_value.json.return_value = {"assets": {"items": items, "nextPage": None}}
        page_people.return_value = [[person()]]
        photos = list(exporter.process_assets(make_client(), export="assets-with-known-visible-faces"))
        self.assertEqual(["visible"], [p["asset_id"] for p in photos])
        self.assertEqual([items[2]], page_people.call_args.args[0])
        self.assertTrue(request.call_args.kwargs["json"]["withPeople"])

    @patch.object(exporter, "process_assets")
    def test_stage1_preserves_complete_assets_and_stage2_can_read_them(self, process):
        photos = [asset("photo-1", [person("Éva")]), asset("photo-2")]
        process.return_value = iter(photos)
        with tempfile.TemporaryDirectory() as directory:
            path = exporter.export_assets_to_json(make_client(), json_output_dir=directory)
            self.assertIsNotNone(path)
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(2, data["total_assets"])
            self.assertEqual("https://example.invalid", data["immich_server"])
            self.assertEqual(photos, data["assets"])
            self.assertEqual({"export_timestamp", "immich_server", "total_assets", "assets"}, set(data))
            self.assertEqual([Path(path)], list(Path(directory).iterdir()))
            output = Path(directory) / "xmp"
            self.assertTrue(exporter.export_assets_to_xmp_from_json(path, str(output)))
            self.assertEqual(["Éva"], region_names(ET.parse(output / "photos/2026/photo-1.jpg.xmp").getroot()))
            ET.parse(output / "photos/2026/photo-2.jpg.xmp")

    @patch.object(exporter, "process_assets", return_value=iter(()))
    def test_stage1_empty_export_leaves_no_final_json(self, _process):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertLogs(exporter.logger, level="WARNING"):
                self.assertIsNone(exporter.export_assets_to_json(make_client(), json_output_dir=directory))
            self.assertEqual([], list(Path(directory).iterdir()))

    @patch("export_xmp.ImmichClient._page_people")
    @patch("export_xmp.ImmichClient.request")
    def test_stage1_later_api_failure_leaves_no_partial_json(self, request, people):
        request.side_effect = [
            Mock(json=Mock(return_value={"assets": {"items": [{"id": "photo-1"}], "nextPage": "2"}})),
            exporter.requests.exceptions.Timeout("page 2 failed"),
        ]
        people.return_value = [[]]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertLogs(exporter.logger, level="ERROR") as messages:
                self.assertIsNone(exporter.export_assets_to_json(make_client(), json_output_dir=directory))
            self.assertEqual(2, request.call_count)
            self.assertTrue(any("page 2 failed" in message for message in messages.output))
            self.assertEqual([], list(Path(directory).iterdir()))

    @patch.object(exporter, "process_assets")
    def test_stage1_write_error_is_reported_and_temp_file_is_removed(self, process):
        process.return_value = iter([asset()])
        with tempfile.TemporaryDirectory() as directory:
            with patch("builtins.open", side_effect=OSError("disk full")):
                with self.assertLogs(exporter.logger, level="ERROR"):
                    self.assertIsNone(exporter.export_assets_to_json(make_client(), json_output_dir=directory))
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_stage2_cli_works_without_immich_config_and_filters_sidecars(self):
        data = {
            "export_timestamp": "2026-07-22T19:23:56",
            "immich_server": "https://example.invalid",
            "total_assets": 3,
            "assets": [
                asset("named", [person("Ada", [face(), face(isHidden=True)]), person("Unknown")]),
                asset("unknown", [person("Unknown")]),
                asset("no-face"),
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "interim.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            output = Path(directory) / "known"
            arguments = ["export_xmp.py", "--stage2-only", "--json-file", str(source),
                         "--xmp-dir", str(output), "--export", "assets-with-known-visible-faces"]
            with (
                patch.object(sys, "argv", arguments),
                patch.object(exporter, "setup_logging"),
                patch.object(exporter, "ConfigLoader", side_effect=AssertionError("Stage 2 tried to load credentials")),
            ):
                exporter.main()

            files = list(output.rglob("*.xmp"))
            self.assertEqual(["named.jpg.xmp"], [path.name for path in files])
            root = ET.parse(files[0]).getroot()
            self.assertEqual(1, len(root.findall(".//mwg:Area", NS)))
            self.assertEqual(["Ada"], [node.text for node in root.findall(".//dc:subject/rdf:Bag/rdf:li", NS)])
            summary = json.loads((output / "export_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(1, summary["total_assets"])
            self.assertEqual(1, summary["total_xmp_files_created"])
            self.assertEqual(1, summary["total_faces_processed"])
            self.assertEqual({"Ada": 1}, summary["people_statistics"])
            self.assertEqual(str(source), summary["json_source"])

            all_output = Path(directory) / "all"
            self.assertTrue(exporter.export_assets_to_xmp_from_json(str(source), str(all_output)))
            self.assertEqual(3, len(list(all_output.rglob("*.xmp"))))
            ET.parse(all_output / "photos/2026/no-face.jpg.xmp")

    def test_invalid_or_empty_interim_json_does_not_write_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "interim.json"
            output = Path(directory) / "output"
            for content in ("{invalid", '{"assets": []}'):
                with self.subTest(content=content):
                    source.write_text(content, encoding="utf-8")
                    with self.assertLogs(exporter.logger, level="WARNING"):
                        self.assertFalse(exporter.export_assets_to_xmp_from_json(str(source), str(output)))
                    self.assertEqual([], list(output.rglob("*.xmp")))

    @patch.object(exporter, "process_assets")
    def test_direct_export_accepts_iterator_and_writes_no_intermediate_json(self, process):
        process.return_value = iter([asset("photo-1", [person("Unknown")])])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "xmp"
            self.assertTrue(exporter.export_assets_to_xmp(
                make_client(access_token="token"), str(output), max_assets=1, album_id="album-a", export="assets-with-faces"
            ))
            self.assertEqual("token", process.call_args.args[0].access_token)
            self.assertEqual((1, "album-a", None, "assets-with-faces"), process.call_args.args[1:])
            root = ET.parse(output / "photos/2026/photo-1.jpg.xmp").getroot()
            self.assertEqual(["Unknown"], region_names(root))
            summary = json.loads((output / "export_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(1, summary["total_faces_processed"])
            self.assertNotIn("json_source", summary)
            self.assertEqual([output / "export_summary.json"], list(Path(directory).rglob("*.json")))

    def test_summary_counts_faces_across_successful_files_only(self):
        photos = [asset("first", [person("Ada", [face(), face()])]),
                  asset("second", [person("Ada")]), asset("failed", [person("Bea")])]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(exporter, "save_xmp_sidecar", side_effect=[True, True, False]):
                self.assertFalse(exporter.write_xmp_for_assets(iter(photos), directory))
            summary = json.loads((Path(directory) / "export_summary.json").read_text(encoding="utf-8"))
            self.assertEqual("failed", summary["export_status"])
            self.assertEqual(3, summary["total_assets"])
            self.assertEqual(2, summary["total_xmp_files_created"])
            self.assertEqual(3, summary["total_faces_processed"])
            self.assertEqual(1, summary["unique_people"])
            self.assertEqual({"Ada": 3}, summary["people_statistics"])

    def test_no_successful_sidecar_means_export_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(exporter, "save_xmp_sidecar", return_value=False):
                with self.assertLogs(exporter.logger, level="ERROR"):
                    self.assertFalse(exporter.write_xmp_for_assets([asset()], directory))
            summary = json.loads((Path(directory) / "export_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(0, summary["total_xmp_files_created"])

    def test_sidecar_paths_neutralize_traversal_and_reject_empty_content(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "output"
            self.assertTrue(exporter.save_xmp_sidecar("/../../photos/2026/photo.jpg", "<xmp/>", str(base)))
            self.assertEqual("<xmp/>", (base / "photos/2026/photo.jpg.xmp").read_text())
            self.assertFalse(exporter.save_xmp_sidecar("/photos/photo.jpg", "", str(base)))

    def test_sidecar_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "output"
            base.mkdir()
            outside = Path(directory) / "outside"
            outside.mkdir()
            try:
                (base / "linked").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlinks are unavailable")
            with self.assertLogs(exporter.logger, level="ERROR"):
                self.assertFalse(exporter.save_xmp_sidecar("linked/photo.jpg", "<xmp/>", str(base)))
            self.assertFalse((outside / "photo.jpg.xmp").exists())


class RegionSummaryTests(unittest.TestCase):
    """Summaries count face regions that were actually written."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        photo = asset(people=[person("Ada", [face(), face(boundingBoxX2=144)])])
        self.assertTrue(exporter.write_xmp_for_assets([photo], directory.name))
        root = ET.parse(Path(directory.name) / "photos/2026/photo-1.jpg.xmp").getroot()
        summary = json.loads((Path(directory.name) / "export_summary.json").read_text(encoding="utf-8"))
        self.written_regions = len(root.findall(".//mwg:Area", NS))
        self.reported_faces = summary["total_faces_processed"]
        self.reported_people = summary["people_statistics"]
        self.assertEqual(1, self.written_regions)
        self.assertIsInstance(self.reported_faces, int)

    def test_summary_should_count_written_regions_not_degenerate_faces(self):
        self.assertEqual(self.written_regions, self.reported_faces)
        self.assertEqual({"Ada": self.written_regions}, self.reported_people)


class PartialExportTests(unittest.TestCase):
    """A later API failure cannot report a partial XMP export as complete."""

    @patch("export_xmp.ImmichClient._page_people")
    @patch("export_xmp.ImmichClient.request")
    def setUp(self, request, people):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        request.side_effect = [
            Mock(json=Mock(return_value={"assets": {"items": [{"id": "photo-1"}], "nextPage": "2"}})),
            exporter.requests.exceptions.Timeout("page 2 failed"),
        ]
        people.return_value = [[]]
        with self.assertLogs(exporter.logger, level="ERROR") as messages:
            self.success = exporter.export_assets_to_xmp(make_client(), output_dir=directory.name)
        self.assertEqual(2, request.call_count)
        self.assertTrue(any("page 2 failed" in message for message in messages.output))
        self.assertFalse((Path(directory.name) / "export_summary.json").exists())

    def test_later_api_failure_should_not_report_successful_export(self):
        self.assertFalse(self.success, "A partial export was reported as successful")

# Small synthetic inputs live beside the tests; no external fixture files are needed.
EXPORT_JSON_NAME = "immich_assets_export_20260102_030405.json"
EXPORT_MODES = ("all-assets", "assets-with-faces", "assets-with-known-visible-faces")


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 1, 2, 3, 4, 5, 123456)
        return value if tz is None else value.replace(tzinfo=timezone.utc).astimezone(tz)


def compatibility_api_data():
    """Fresh synthetic pages with rejected assets, rich metadata, and all orientations."""
    pages, faces = raw_api_fixture()
    named = {
        "id": "person-main", "name": '  \u00c9va & "Ada" <Team>  ', "isHidden": False,
        "birthDate": None, "color": "", "favorite": False,
        "extension": {"zero": 0, "false": False, "null": None, "empty": "", "array": [0, False, None, "", "\u96ea"]},
    }
    hidden = {"id": "person-hidden", "name": "Bea", "isHidden": True}

    def item(key, who, **changes):
        result = {
            "id": key, "originalPath": f"/photos/{key}.jpg", "originalFileName": f"{key}.jpg",
            "width": 3024, "height": 4032,
            "exifInfo": {"orientation": "6", "exifImageWidth": 4032, "exifImageHeight": 3024},
            "people": who,
        }
        result.update(changes)
        return result

    rich = item("rich", [named, hidden], originalPath="/photos/2026/\u00c9va & Ada.jpg",
                originalFileName="\u00c9va & Ada.jpg", exifInfo={
        "orientation": "6", "exifImageWidth": "4032", "exifImageHeight": 3024,
        "make": "Camera & Co <raw>", "model": 'Model "A"', "lensModel": "Prime '50'",
        "fNumber": 0.0, "exposureTime": "1/125", "iso": 0, "focalLength": 50.5,
        "dateTimeOriginal": "2020-02-03", "dateTimeDigitized": "2020-02-03T04:05:06+03:00",
        "latitude": 0, "longitude": -0.0, "city": "", "state": None, "country": "Eesti",
        "description": "line one\nline two\t\u96ea", "rating": 0,
        "extension": {"null": None, "false": False, "zero": 0, "empty": "",
                      "array": [None, False, 0, "", "\u00c4\u00d6\u00dc"], "object": {}},
    }, checksum="intentionally-not-exported", unrelatedAssetField={"retained": False})
    zero = {"id": "zero-person", "name": "Zero area", "isHidden": False}
    no_dim = {"id": "no-dim-person", "name": "No dimensions", "isHidden": False}
    fallback = {"id": "fallback-person", "name": "\u96ea", "isHidden": False}
    first_page = pages[1]["assets"]["items"] + [item("hidden", [hidden])]
    second_page = [
        rich, item("zero-area", [zero]),
        item("no-dimensions", [no_dim], width=None, height=0,
             exifInfo={"exifImageWidth": None, "exifImageHeight": 0}),
        item("fallback", [fallback], originalPath="", originalFileName=None,
             width="not-a-number", height=None, exifInfo={"orientation": "8"}),
    ]
    faces.pop("portrait")
    faces["unknown"][0]["id"] = "unknown-face"
    faces.update({
        "hidden": [face(id="hidden-face", person=hidden)],
        "rich": [
            face(id="main-1", person=named, confidence=0.0,
                 extension={"empty": "", "null": None, "false": False, "zero": 0,
                            "label": "\u96ea", "coordinates": [144, 384, 432, 1152]}),
            face(id="main-2", person={**named, "name": "Later conflicting name is ignored"}, boundingBoxX1=600),
            face(id="main-hidden", person=named, isHidden=True),
            face(id="main-invisible", person=named, isVisible=False),
            face(id="incomplete", person=named, boundingBoxY2=None),
            face(id="hidden-person", person=hidden),
            face(id="anonymous", person=None),
            face(id="duplicate-name", person={**named, "id": "second-person-same-name"}),
            face(id="degenerate", person=named, boundingBoxX2=144),
            face(id="idless-person", person={"id": "", "name": "Idless name is discarded"}),
            "non-object-face-is-ignored",
        ],
        "zero-area": [face(id="zero-face", person=zero, boundingBoxX2=144)],
        "no-dimensions": [{k: v for k, v in face(id="no-dim-face", person=no_dim).items()
                           if k not in ("imageWidth", "imageHeight")}],
        "fallback": [face(id="fallback-face", person=fallback, boundingBoxX1=100, boundingBoxY1=50,
                          boundingBoxX2=300, boundingBoxY2=150, imageWidth=1000, imageHeight=500)],
    })
    orientations = []
    for value in (2, 3, 4, 5, 7, 8):
        key = f"orientation-{value}"
        who = {"id": f"person-{value}", "name": f"Orientation {value}", "isHidden": False}
        orientations.append(item(key, [who], width=4000, height=3000,
                                 exifInfo={"orientation": value, "exifImageWidth": 4000, "exifImageHeight": 3000}))
        faces[key] = [face(id=f"face-{value}", person=who, boundingBoxX1=100, boundingBoxY1=100,
                           boundingBoxX2=300, boundingBoxY2=300, imageWidth=1000, imageHeight=500)]
    return {
        "pages": {"1": {"assets": {"items": first_page, "nextPage": "2"}},
                  "2": {"assets": {"items": second_page, "nextPage": "3"}},
                  "3": {"assets": {"items": orientations, "nextPage": None}}},
        "faces": faces,
    }


# One unchanged rich record from the original Stage A JSON capture.
# Only the envelope count is reduced to one; tests never generate this expectation.
LEGACY_JSON = (
    '{\n'
    '  "export_timestamp": "2026-01-02T03:04:05.123456",\n'
    '  "immich_server": "https://example.invalid",\n'
    '  "total_assets": 1,\n'
    '  "assets": [\n'
    '{"asset_id":"rich","original_path":"/photos/2026/Éva & Ada.jpg","file_name":"Éva & Ada.jpg","width":3024,'
    '"height":4032,"exifInfo":{"orientation":"6","exifImageWidth":"4032","exifImageHeight":3024,'
    '"make":"Camera & Co <raw>","model":"Model \\"A\\"","lensModel":"Prime \'50\'","fNumber":0.0,'
    '"exposureTime":"1/125","iso":0,"focalLength":50.5,"dateTimeOriginal":"2020-02-03",'
    '"dateTimeDigitized":"2020-02-03T04:05:06+03:00","latitude":0,"longitude":-0.0,"city":"","state":null,'
    '"country":"Eesti","description":"line one\\nline two\\t雪","rating":0,"extension":{"null":null,"false":false,'
    '"zero":0,"empty":"","array":[null,false,0,"","ÄÖÜ"],"object":{}}},"people":[{"id":"person-main",'
    '"name":"  Éva & \\"Ada\\" <Team>  ","isHidden":false,"birthDate":null,"color":"","favorite":false,'
    '"extension":{"zero":0,"false":false,"null":null,"empty":"","array":[0,false,null,"","雪"]},'
    '"faces":[{"id":"main-1","boundingBoxX1":144,"boundingBoxY1":384,"boundingBoxX2":432,"boundingBoxY2":1152,'
    '"imageWidth":1440,"imageHeight":1920,"confidence":0.0,"extension":{"empty":"","null":null,"false":false,'
    '"zero":0,"label":"雪","coordinates":[144,384,432,1152]}},{"id":"main-2","boundingBoxX1":600,'
    '"boundingBoxY1":384,"boundingBoxX2":432,"boundingBoxY2":1152,"imageWidth":1440,"imageHeight":1920},'
    '{"id":"main-hidden","boundingBoxX1":144,"boundingBoxY1":384,"boundingBoxX2":432,"boundingBoxY2":1152,'
    '"imageWidth":1440,"imageHeight":1920,"isHidden":true},{"id":"main-invisible","boundingBoxX1":144,'
    '"boundingBoxY1":384,"boundingBoxX2":432,"boundingBoxY2":1152,"imageWidth":1440,"imageHeight":1920,'
    '"isVisible":false},{"id":"degenerate","boundingBoxX1":144,"boundingBoxY1":384,"boundingBoxX2":144,'
    '"boundingBoxY2":1152,"imageWidth":1440,"imageHeight":1920}]},{"id":"person-hidden","name":"Bea",'
    '"isHidden":true,"faces":[{"id":"hidden-person","boundingBoxX1":144,"boundingBoxY1":384,"boundingBoxX2":432,'
    '"boundingBoxY2":1152,"imageWidth":1440,"imageHeight":1920}]},{"name":"Unknown","faces":[{"id":"anonymous",'
    '"boundingBoxX1":144,"boundingBoxY1":384,"boundingBoxX2":432,"boundingBoxY2":1152,"imageWidth":1440,'
    '"imageHeight":1920},{"id":"idless-person","boundingBoxX1":144,"boundingBoxY1":384,"boundingBoxX2":432,'
    '"boundingBoxY2":1152,"imageWidth":1440,"imageHeight":1920}]},{"id":"second-person-same-name",'
    '"name":"  Éva & \\"Ada\\" <Team>  ","isHidden":false,"birthDate":null,"color":"","favorite":false,'
    '"extension":{"zero":0,"false":false,"null":null,"empty":"","array":[0,false,null,"","雪"]},'
    '"faces":[{"id":"duplicate-name","boundingBoxX1":144,"boundingBoxY1":384,"boundingBoxX2":432,'
    '"boundingBoxY2":1152,"imageWidth":1440,"imageHeight":1920}]}]}'
    '\n  ]\n}\n'
)


def structured_api_data(data=None):
    """Express synthetic result pages with opaque cursors instead of page numbers."""
    data = copy.deepcopy(compatibility_api_data() if data is None else data)
    keys = list(data["pages"])
    cursors = {key: None if index == 0 else f"opaque-cursor/{index}+=end"
               for index, key in enumerate(keys)}
    pages = {}
    for key, page in data["pages"].items():
        assets = page["assets"]
        next_page = assets.pop("nextPage")
        assets["nextCursor"] = cursors[next_page] if next_page is not None else None
        pages[cursors[key]] = page
    data["pages"] = pages
    return data


class SyntheticAPI:
    """In-memory transport; face requests still run in real worker threads."""

    def __init__(self, data=None, *, search_api="legacy"):
        self.data = compatibility_api_data() if data is None else data
        self.search_api = search_api
        self.face_errors = {}
        self.page_errors = {}
        self.calls = []
        self.unexpected_calls = []
        self.lock = threading.Lock()
        self.structured_pages_requested = 0

    def request(self, method, url, **kwargs):
        with self.lock:
            self.calls.append((method, url, copy.deepcopy(kwargs)))
        if (method, url) == ("POST", "https://example.invalid/api/search/metadata"):
            payload = kwargs["json"]
            if self.search_api == "structured":
                if "page" in payload or "filter" not in payload:
                    self.unexpected_calls.append("Structured mode sent a legacy search request")
                    raise AssertionError(self.unexpected_calls[-1])
                key = payload.get("cursor")
                if key is None:
                    self.structured_pages_requested = 0
                self.structured_pages_requested += 1
                if self.structured_pages_requested > len(self.data["pages"]) + 1:
                    raise AssertionError("Structured pagination looped without rejecting a repeated cursor")
            else:
                key = str(payload["page"])
            if key in self.page_errors:
                raise self.page_errors[key]
            values = self.data["pages"]
        elif (method, url) == ("GET", "https://example.invalid/api/faces"):
            key = kwargs["params"]["id"]
            if key in self.face_errors:
                raise self.face_errors[key]
            values = self.data["faces"]
        else:
            key, values = (method, url), {}
        if key not in values:
            message = f"Unexpected synthetic request: {method} {url}, key={key!r}"
            with self.lock:
                self.unexpected_calls.append(message)
            raise AssertionError(message)
        response = exporter.requests.Response()
        response.status_code = 200
        response._content = json.dumps(values[key], ensure_ascii=False).encode("utf-8")
        response.encoding = "utf-8"
        return response

    def search_payloads(self):
        return [kwargs["json"] for method, _, kwargs in self.calls if method == "POST"]

    def face_ids(self):
        return [kwargs["params"]["id"] for method, _, kwargs in self.calls if method == "GET"]


@contextmanager
def configured_client():
    """Create the current client under the synthetic configuration and transport."""
    with exporter.ImmichClient(exporter.ApiSettings.from_config(exporter.ConfigLoader())) as client:
        yield client


@contextmanager
def api_environment(api=None, *, search_api="legacy"):
    api = SyntheticAPI() if api is None else api
    config = Mock(spec=exporter.ConfigLoader)
    config.get_immich_config.return_value = {
        "base_url": "https://example.invalid/", "api_key": "synthetic-key", "email": "", "password": "",
    }
    config.get_settings_config.return_value = {
        "face_request_workers": 2, "retry_attempts": 0, "request_timeout": 12,
        "search_api": search_api,
    }
    config.get_output_config.return_value = {"json_export_dir": "json_exports", "xmp_export_dir": "xmp_sidecars"}
    config.validate_immich_config.return_value = True
    with (
        patch.object(exporter, "ConfigLoader", return_value=config),
        patch.object(exporter, "datetime", FixedClock),
        patch.object(exporter, "Queue", TimedQueue),
        patch.object(exporter.requests.Session, "request", side_effect=api.request),
    ):
        try:
            yield api
        finally:
            # Also catch unexpected requests swallowed by production error handling.
            if api.unexpected_calls:
                raise AssertionError("\n".join(api.unexpected_calls))


class SerializationCompatibilityTests(unittest.TestCase):
    """Pin representative original bytes; use explicit assertions for the wider pipeline."""

    def assert_rich_xmp(self, content, mode):
        # Fixed hashes from the original Stage A sidecars, not the current renderer.
        expected_hash = (
            "6b818d5a39f13a9808840e06bc6f8785f92c36f5dce12650ce7b6c75b948b3fb"
            if mode == "assets-with-known-visible-faces" else
            "edc978b06fbf6314b982a42af1ab4893ce9082943a7582266c90c49b98699c4a"
        )
        # Text-mode writers use native newlines. Do not normalize anything else.
        content = content.replace(os.linesep.encode("ascii"), b"\n")
        self.assertEqual(expected_hash, hashlib.sha256(content).hexdigest(),
                         "Original rich XMP bytes changed (including formatting, order, precision, or metadata)")
        ET.fromstring(content)

    def assert_summary(self, output, selected, written, people, source=None, *, preserve_people_order=True):
        expected = {
            "export_timestamp": "2026-01-02T03:04:05.123456",
            "total_assets": selected, "total_xmp_files_created": written,
            "total_faces_processed": sum(people.values()), "unique_people": len(people),
            "people_statistics": people, "output_directory": str(output.absolute()),
            "export_status": "completed",
        }
        if source is not None:
            expected["json_source"] = str(source)
        content = (output / "export_summary.json").read_bytes()
        self.assertEqual(expected, json.loads(content))
        if not preserve_people_order:
            expected["people_statistics"] = json.loads(content)["people_statistics"]
        self.assertEqual(json.dumps(expected, indent=2, ensure_ascii=False).replace("\n", os.linesep).encode("utf-8"),
                         content)

    def test_json_writer_preserves_legacy_values_with_sorted_records_and_keys(self):
        data = json.loads(LEGACY_JSON)
        before = copy.deepcopy(data)
        expected = copy.deepcopy(data)
        people = expected["assets"][0]["people"]
        people[:] = [people[2], people[1], people[0], people[3]]
        faces = people[2]["faces"]
        faces[:] = [faces[4], faces[0], faces[1], faces[2], faces[3]]
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "datetime", FixedClock):
            source = exporter.write_assets_json(iter(data["assets"]), directory, immich_server=data["immich_server"])
            self.assertEqual(EXPORT_JSON_NAME, Path(source).name)
            exported = Path(source).read_text(encoding="utf-8")
            self.assertEqual(expected, json.loads(exported))
            self.assertEqual(json.dumps(expected["assets"][0], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                             exported.splitlines()[5])
            self.assertEqual(8, len(exported.splitlines()))
            self.assertEqual([Path(source)], list(Path(directory).iterdir()))
        self.assertEqual(before, data)

    def test_stage1_staged_and_direct_preserve_selection_outputs_and_summary(self):
        orientations = [f"orientation-{value}" for value in (2, 3, 4, 5, 7, 8)]
        orientation_counts = {f"Orientation {value}": 1 for value in (2, 3, 4, 5, 7, 8)}
        name = '\u00c9va & "Ada" <Team>'
        all_people = {"Unknown": 3, "Bea": 2, name: 5, "\u96ea": 1, **orientation_counts}
        cases = [
            ("all-assets", None,
             ["unknown", "empty", "hidden", "rich", "zero-area", "no-dimensions", "fallback"] + orientations, all_people),
            ("assets-with-faces", None,
             ["unknown", "hidden", "rich", "zero-area", "no-dimensions", "fallback"] + orientations, all_people),
            ("assets-with-known-visible-faces", None,
             ["rich", "zero-area", "no-dimensions", "fallback"] + orientations,
             {name: 3, "\u96ea": 1, **orientation_counts}),
            ("assets-with-known-visible-faces", 2, ["rich", "zero-area"], {name: 3}),
        ]
        for mode, limit, selected, counts in cases:
            with self.subTest(mode=mode, limit=limit), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                with api_environment(), configured_client() as client:
                    source = exporter.export_assets_to_json(
                        client, json_output_dir=str(work / "json"), max_assets=limit, export=mode,
                    )
                    self.assertEqual(str((work / "json" / EXPORT_JSON_NAME).absolute()), source)
                    data = json.loads(Path(source).read_bytes())
                    paths_by_id = {key: f"/photos/{key}.jpg" for key in selected}
                    paths_by_id.update({"rich": "/photos/2026/\u00c9va & Ada.jpg", "fallback": ""})
                    self.assertEqual(sorted(selected, key=paths_by_id.__getitem__),
                                     [record["asset_id"] for record in data["assets"]])
                    self.assertEqual(len(selected), data["total_assets"])
                    self.assertTrue(exporter.export_assets_to_xmp_from_json(source, str(work / "staged"), mode))
                    self.assertTrue(exporter.export_assets_to_xmp(
                        client, output_dir=str(work / "direct"), max_assets=limit, export=mode,
                    ))
                # Zero-area/missing-dimension assets consume the limit, but cannot write face-only XMP.
                written = selected if mode == "all-assets" else [
                    key for key in selected if key not in ("zero-area", "no-dimensions")
                ]
                special_paths = {"rich": "photos/2026/\u00c9va & Ada.jpg.xmp", "fallback": "unknown_fallback.jpg.xmp"}
                paths = {special_paths.get(key, f"photos/{key}.jpg.xmp") for key in written}
                for kind in ("staged", "direct"):
                    output = work / kind
                    self.assertEqual(paths | {"export_summary.json"},
                                     {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()})
                    self.assert_summary(output, len(selected), len(written), counts, source if kind == "staged" else None,
                                        preserve_people_order=kind == "direct")
                    if kind == "direct":
                        self.assert_rich_xmp((output / special_paths["rich"]).read_bytes(), mode)
                for path in paths:
                    staged = (work / "staged" / path).read_bytes()
                    self.assertEqual(xmp_semantics(staged), xmp_semantics((work / "direct" / path).read_bytes()))
                    ET.fromstring(staged)
                self.assertEqual({"json", "staged", "direct"}, {p.name for p in work.iterdir()})

    def test_stage2_reads_legacy_json_in_every_mode_without_config_or_http(self):
        for mode in EXPORT_MODES:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "legacy.json"
                source.write_text(LEGACY_JSON, encoding="utf-8")
                before = source.read_bytes()
                output = Path(directory) / "xmp"
                with (
                    patch.object(exporter, "datetime", FixedClock),
                    patch.object(exporter, "ConfigLoader", side_effect=AssertionError("Stage 2 requested config")) as config,
                    patch.object(exporter.requests.Session, "request", side_effect=AssertionError("Stage 2 requested HTTP")) as http,
                ):
                    self.assertTrue(exporter.export_assets_to_xmp_from_json(str(source), str(output), mode))
                config.assert_not_called()
                http.assert_not_called()
                self.assertEqual({"photos/2026/\u00c9va & Ada.jpg.xmp", "export_summary.json"},
                                 {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()})
                self.assert_rich_xmp((output / "photos/2026/\u00c9va & Ada.jpg.xmp").read_bytes(), mode)
                name = '\u00c9va & "Ada" <Team>'
                counts = {name: 3} if mode == "assets-with-known-visible-faces" else {name: 5, "Bea": 1, "Unknown": 2}
                self.assert_summary(output, 1, 1, counts, source)
                self.assertEqual(before, source.read_bytes())

    def test_api_projection_preserves_nested_metadata_types_and_first_person_record(self):
        raw = compatibility_api_data()
        with api_environment(), configured_client() as client:
            records = list(exporter.process_assets(client))
        rich = next(record for record in records if record["asset_id"] == "rich")
        self.assertEqual({"asset_id", "original_path", "file_name", "width", "height", "exifInfo", "people"}, set(rich))
        self.assertEqual(raw["pages"]["2"]["assets"]["items"][0]["exifInfo"], rich["exifInfo"])
        self.assertIs(type(rich["exifInfo"]["iso"]), int)
        self.assertIs(type(rich["exifInfo"]["fNumber"]), float)
        self.assertIs(type(rich["exifInfo"]["extension"]["false"]), bool)
        self.assertIsNone(rich["exifInfo"]["state"])
        self.assertEqual("", rich["exifInfo"]["city"])
        first = rich["people"][0]
        self.assertEqual(raw["faces"]["rich"][0]["person"], {k: v for k, v in first.items() if k != "faces"})
        self.assertEqual(["main-1", "main-2", "main-hidden", "main-invisible", "degenerate"],
                         [item["id"] for item in first["faces"]])
        self.assertEqual({k: v for k, v in raw["faces"]["rich"][0].items() if k != "person"}, first["faces"][0])
        self.assertEqual(["anonymous", "idless-person"], [item["id"] for item in rich["people"][2]["faces"]])
        self.assertEqual({}, next(record for record in records if record["asset_id"] == "empty")["exifInfo"])

    def test_selection_rendering_and_writing_do_not_mutate_extended_legacy_records(self):
        record = json.loads(LEGACY_JSON)["assets"][0]
        record["extension"] = {"null": None, "false": False, "zero": 0, "empty": "", "array": [None, 0, False]}
        before = copy.deepcopy(record)
        for mode in EXPORT_MODES:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                filtered = exporter.filter_asset_for_export(record, mode)
                filtered_before = copy.deepcopy(filtered)
                self.assertEqual(before["extension"], filtered["extension"])
                with patch.object(exporter, "datetime", FixedClock):
                    self.assertTrue(exporter.write_xmp_for_assets([filtered], directory, export=mode))
                self.assertEqual(filtered_before, filtered)
                self.assertEqual(before, record)

    def test_rich_metadata_and_region_semantics_are_independently_specified(self):
        with tempfile.TemporaryDirectory() as directory, api_environment(), configured_client() as client:
            output = Path(directory)
            self.assertTrue(exporter.export_assets_to_xmp(client, output_dir=str(output)))
            root = ET.parse(output / "photos/2026/\u00c9va & Ada.jpg.xmp").getroot()
            name = '\u00c9va & "Ada" <Team>'
            self.assertEqual([name] * 4 + ["Bea", "Unknown", "Unknown", name], region_names(root))
            self.assertEqual(["Bea", name], [node.text for node in root.findall(".//dc:subject/rdf:Bag/rdf:li", NS)])
            for tag, value in (("exif:ISOSpeedRatings", "0"), ("exif:FNumber", "0.0"),
                               ("exif:GPSLatitude", "0"), ("exif:GPSLongitude", "-0.0"),
                               ("tiff:ImageWidth", "4032"), ("tiff:ImageLength", "3024"),
                               ("exif:DateTimeOriginal", "2020-02-03T12:00:00")):
                self.assertEqual(value, root.find(".//" + tag, NS).text)
            self.assertIsNone(root.find(".//photoshop:City", NS))
            self.assertIsNone(root.find(".//photoshop:State", NS))
            area = root.find(".//mwg:Area", NS)
            self.assertEqual(["0.400000", "0.800000", "0.400000", "0.200000"],
                             [area.get(f"{{{NS['stArea']}}}{key}") for key in ("x", "y", "w", "h")])
            summary = json.loads((output / "export_summary.json").read_bytes())
            self.assertEqual((13, 13, 17, 10), tuple(summary[key] for key in
                             ("total_assets", "total_xmp_files_created", "total_faces_processed", "unique_people")))


class CompatibilityBoundaryTests(unittest.TestCase):
    def test_legacy_search_payload_is_exact_and_does_not_mix_in_future_api_fields(self):
        for mode in EXPORT_MODES:
            with self.subTest(mode=mode), api_environment() as api, configured_client() as client:
                list(exporter.process_assets(client, album_id="album-a", library_id="library-b", export=mode))
                expected = [{"page": page, "size": 1000, "withExif": True,
                             **({"withPeople": True} if mode == "assets-with-known-visible-faces" else {}),
                             "albumIds": ["album-a"], "libraryId": "library-b"} for page in (1, 2, 3)]
                self.assertEqual(expected, api.search_payloads())
                if mode == "assets-with-known-visible-faces":
                    self.assertTrue({"unknown", "empty", "hidden"}.isdisjoint(api.face_ids()))

    def test_legacy_rejects_cursor_responses_before_faces_without_switching_api(self):
        pagination_cases = (
            {"nextCursor": None},
            {"nextCursor": "opaque-cursor"},
            {"nextPage": "2", "nextCursor": "opaque-cursor"},
            {"nextPage": None, "nextCursor": "opaque-cursor"},
        )
        for nested, pagination in product((False, True), pagination_cases):
            with self.subTest(nested=nested, pagination=pagination):
                raw = compatibility_api_data()
                response = {"items": raw["pages"]["1"]["assets"]["items"], **pagination}
                raw["pages"]["1"] = {"assets": response} if nested else response
                api = SyntheticAPI(raw)
                with api_environment(api), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"), self.assertRaises(RuntimeError) as raised:
                        list(exporter.process_assets(client))
                self.assertIn("--search-api structured", str(raised.exception))
                self.assertEqual([{"page": 1, "size": 1000, "withExif": True}], api.search_payloads())
                self.assertEqual([], api.face_ids())

    def test_legacy_pages_accept_null_nextcursor_alongside_nextpage(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                raw = compatibility_api_data()
                expected_ids = []
                for key, page in raw["pages"].items():
                    assets = page["assets"]
                    expected_ids.extend(item["id"] for item in assets["items"])
                    assets["nextCursor"] = None
                    if not nested:
                        raw["pages"][key] = assets
                api = SyntheticAPI(raw)
                with api_environment(api), configured_client() as client:
                    records = list(exporter.process_assets(client))
                self.assertEqual(expected_ids, [record["asset_id"] for record in records])
                self.assertEqual([1, 2, 3], [payload["page"] for payload in api.search_payloads()])
                self.assertCountEqual(expected_ids, api.face_ids())

    def test_face_failure_after_limit_is_not_observed_even_though_fetch_ran(self):
        for mode, limit, failed_id, expected_ids in (
            ("all-assets", 1, "hidden", ["unknown"]),
            ("assets-with-known-visible-faces", 2, "fallback", ["rich", "zero-area"]),
        ):
            with self.subTest(mode=mode):
                api = SyntheticAPI()
                api.face_errors[failed_id] = exporter.requests.exceptions.Timeout("unconsumed failure")
                with api_environment(api), configured_client() as client:
                    records = list(exporter.process_assets(client, max_assets=limit, export=mode))
                self.assertEqual(expected_ids, [record["asset_id"] for record in records])
                self.assertIn(failed_id, api.face_ids())

    def test_face_failure_before_limit_propagates(self):
        api = SyntheticAPI()
        error = exporter.requests.exceptions.Timeout("consumed face failure")
        api.face_errors["unknown"] = error
        with api_environment(api), configured_client() as client, self.assertLogs(exporter.logger, level="ERROR"):
            with self.assertRaises(exporter.requests.exceptions.Timeout) as raised:
                list(exporter.process_assets(client, max_assets=1))
        self.assertIs(error, raised.exception)

    def test_limit_counts_selected_assets_not_rejected_assets(self):
        raw = compatibility_api_data()
        page = raw["pages"]["1"]["assets"]
        page["items"][0], page["items"][1] = page["items"][1], page["items"][0]
        with api_environment(SyntheticAPI(raw)) as api, configured_client() as client:
            records = list(exporter.process_assets(client, max_assets=1, export="assets-with-faces"))
        self.assertEqual(["unknown"], [record["asset_id"] for record in records])
        self.assertEqual([1], [payload["page"] for payload in api.search_payloads()])

    def test_nonpositive_limits_yield_nothing_without_http(self):
        for limit in (0, -1):
            with self.subTest(limit=limit), api_environment() as api, configured_client() as client:
                self.assertEqual([], list(exporter.process_assets(client, max_assets=limit)))
                self.assertEqual([], api.calls)

    def test_empty_page_stops_even_when_next_page_is_present(self):
        raw = compatibility_api_data()
        raw["pages"]["1"]["assets"]["items"] = []
        with api_environment(SyntheticAPI(raw)) as api, configured_client() as client:
            self.assertEqual([], list(exporter.process_assets(client)))
        self.assertEqual([1], [payload["page"] for payload in api.search_payloads()])
        self.assertEqual([], api.face_ids())

    def test_flat_search_envelope_and_nonnumeric_next_page_are_accepted(self):
        raw = compatibility_api_data()
        raw["pages"]["1"] = {"items": raw["pages"]["1"]["assets"]["items"][:1], "nextPage": "continue"}
        raw["pages"]["2"] = {"items": raw["pages"]["2"]["assets"]["items"][:1], "nextPage": None}
        with api_environment(SyntheticAPI(raw)) as api, configured_client() as client:
            records = list(exporter.process_assets(client))
        self.assertEqual(["unknown", "rich"], [record["asset_id"] for record in records])
        self.assertEqual([1, 2], [payload["page"] for payload in api.search_payloads()])

    def test_malformed_search_response_aborts_instead_of_becoming_empty_success(self):
        for response in ([], None, {"assets": []}, {"assets": {"items": "invalid"}}):
            with self.subTest(response=response):
                raw = compatibility_api_data()
                raw["pages"]["1"] = response
                with api_environment(SyntheticAPI(raw)), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"), self.assertRaises(ValueError):
                        list(exporter.process_assets(client))

    def test_failed_stage1_rerun_preserves_previous_json_and_cleans_temporary_files(self):
        api = SyntheticAPI()
        api.page_errors["2"] = exporter.requests.exceptions.Timeout("second page unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            previous = output / EXPORT_JSON_NAME
            previous.write_bytes(b"previous export must survive")
            with api_environment(api), configured_client() as client, self.assertLogs(exporter.logger, level="ERROR"):
                self.assertIsNone(exporter.export_assets_to_json(client, json_output_dir=str(output)))
            self.assertEqual(b"previous export must survive", previous.read_bytes())
            self.assertEqual([previous], list(output.iterdir()))

    def test_stage1_publish_failure_preserves_previous_json_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            previous = output / EXPORT_JSON_NAME
            previous.write_bytes(b"old export")
            with (
                api_environment(), configured_client() as client,
                patch.object(Path, "replace", side_effect=OSError("publish failed")),
                self.assertLogs(exporter.logger, level="ERROR"),
            ):
                self.assertIsNone(exporter.export_assets_to_json(client, json_output_dir=str(output), max_assets=1))
            self.assertEqual(b"old export", previous.read_bytes())
            self.assertEqual([previous], list(output.iterdir()))

    def test_stage1_serializes_each_asset_before_requesting_the_next(self):
        def records():
            # Reuse and mutate one record. Buffering live dictionaries would
            # incorrectly export the second asset twice instead of snapshots.
            record = asset("first")
            yield record
            record.clear()
            record.update(asset("second"))
            yield record

        with tempfile.TemporaryDirectory() as directory, api_environment():
            with patch.object(exporter, "process_assets", return_value=records()):
                source = exporter.export_assets_to_json(make_client(), json_output_dir=directory)
            self.assertIsNotNone(source)
            self.assertEqual(["first", "second"], [record["asset_id"] for record in json.loads(Path(source).read_bytes())["assets"]])

    def test_successful_same_timestamp_stage1_replaces_previous_export(self):
        with tempfile.TemporaryDirectory() as directory, api_environment(), configured_client() as client:
            first = exporter.export_assets_to_json(client, json_output_dir=directory)
            second = exporter.export_assets_to_json(client, json_output_dir=directory, max_assets=1)
            self.assertEqual(first, second)
            data = json.loads(Path(second).read_bytes())
            self.assertEqual(1, data["total_assets"])
            self.assertEqual(["unknown"], [item["asset_id"] for item in data["assets"]])
            self.assertEqual([Path(second)], list(Path(directory).iterdir()))

    def test_late_direct_failure_leaves_partial_sidecars_but_removes_stale_summary(self):
        api = SyntheticAPI()
        api.page_errors["2"] = exporter.requests.exceptions.Timeout("second page unavailable")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            previous_summary = output / "export_summary.json"
            previous_summary.write_bytes(b"previous summary")
            with api_environment(api), configured_client() as client, self.assertLogs(exporter.logger, level="ERROR"):
                self.assertFalse(exporter.export_assets_to_xmp(client, output_dir=directory))
            self.assertEqual({"unknown.jpg.xmp", "empty.jpg.xmp", "hidden.jpg.xmp"},
                             {path.name for path in output.rglob("*.xmp")})
            self.assertFalse(previous_summary.exists())

    def test_one_actual_sidecar_open_failure_fails_export_and_counts_only_written_regions(self):
        photos = [asset("failed", [person("Bea")]), asset("written", [person("Ada")])]
        original_open = open
        with tempfile.TemporaryDirectory() as directory:
            def failing_open(path, *args, **kwargs):
                if str(path).endswith("failed.jpg.xmp"):
                    raise OSError("sidecar is not writable")
                return original_open(path, *args, **kwargs)
            with patch("builtins.open", side_effect=failing_open), self.assertLogs(exporter.logger, level="ERROR"):
                self.assertFalse(exporter.write_xmp_for_assets(photos, directory))
            summary = json.loads((Path(directory) / "export_summary.json").read_bytes())
            self.assertEqual("failed", summary["export_status"])
            self.assertEqual((2, 1, 1), tuple(summary[key] for key in
                             ("total_assets", "total_xmp_files_created", "total_faces_processed")))
            self.assertEqual({"Ada": 1}, summary["people_statistics"])
            self.assertEqual(["written.jpg.xmp"], [path.name for path in Path(directory).rglob("*.xmp")])

    def test_summary_open_failure_fails_export_without_a_success_message(self):
        original_open = open
        with tempfile.TemporaryDirectory() as directory:
            def failing_open(path, *args, **kwargs):
                if Path(path).name == ".export_summary.json.tmp":
                    raise OSError("summary is not writable")
                return original_open(path, *args, **kwargs)
            with patch("builtins.open", side_effect=failing_open), self.assertLogs(exporter.logger, level="INFO") as messages:
                self.assertFalse(exporter.write_xmp_for_assets([asset()], directory))
            self.assertFalse(any("completed!" in message for message in messages.output))
            self.assertFalse((Path(directory) / "export_summary.json").exists())
            self.assertFalse((Path(directory) / ".export_summary.json.tmp").exists())
            self.assertEqual(1, len(list(Path(directory).rglob("*.xmp"))))

    def test_output_directory_creation_errors_propagate(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-a-directory"
            output.write_bytes(b"existing file")
            with self.assertRaises(OSError):
                exporter.export_assets_to_json(make_client(), json_output_dir=str(output))
            with self.assertRaises(OSError):
                exporter.write_xmp_for_assets([asset()], str(output))
            self.assertEqual(b"existing file", output.read_bytes())

    def test_summary_write_and_publish_failures_remove_stale_and_partial_summaries(self):
        def fail_during_dump(data, stream, **kwargs):
            stream.write('{"partial":')
            raise OSError("disk full")

        for phase in ("write", "publish"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                summary = output / "export_summary.json"
                summary.write_bytes(b"previous summary")
                failure = (patch.object(exporter.json, "dump", side_effect=fail_during_dump) if phase == "write"
                           else patch.object(Path, "replace", side_effect=OSError("publish failed")))
                with failure, self.assertLogs(exporter.logger, level="INFO") as messages:
                    self.assertFalse(exporter.write_xmp_for_assets([asset()], directory))
                self.assertFalse(summary.exists())
                self.assertFalse((output / ".export_summary.json.tmp").exists())
                self.assertEqual(1, len(list(output.rglob("*.xmp"))))
                self.assertFalse(any("completed!" in message for message in messages.output))

    def test_failed_summary_invalidation_leaves_sidecars_and_asset_stream_untouched(self):
        consumed = []

        def records():
            consumed.append(True)
            yield asset("photo", [person("Ada")])

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            summary = output / "export_summary.json"
            summary.write_bytes(b"previous summary")
            sidecar = output / "photos/2026/photo.jpg.xmp"
            sidecar.parent.mkdir(parents=True)
            sidecar.write_bytes(b"previous sidecar")
            with patch.object(Path, "unlink", side_effect=PermissionError("summary locked")), self.assertLogs(
                exporter.logger, level="ERROR",
            ):
                self.assertFalse(exporter.write_xmp_for_assets(records(), directory))
            self.assertEqual([], consumed)
            self.assertEqual(b"previous summary", summary.read_bytes())
            self.assertEqual(b"previous sidecar", sidecar.read_bytes())

    def test_colliding_paths_keep_first_sidecar_and_count_its_regions_once(self):
        photos = [asset("first", [person("Ada")], "/photos/same.jpg"),
                  asset("last", [person("Bea")], "/photos/../same.jpg")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            target = output / "photos/same.jpg.xmp"
            target.parent.mkdir()
            target.write_bytes(b"pre-existing sidecar")
            unrelated = output / "unrelated.xmp"
            unrelated.write_bytes(b"must not be removed")
            with self.assertLogs(exporter.logger, level="ERROR") as messages:
                self.assertFalse(exporter.write_xmp_for_assets(photos, directory))
            self.assertTrue(any("Path collision" in message for message in messages.output))
            self.assertEqual(["Ada"], region_names(ET.parse(target).getroot()))
            self.assertEqual("first.jpg", ET.parse(target).find(".//xmp:Identifier", NS).text)
            self.assertEqual(b"must not be removed", unrelated.read_bytes())
            summary = json.loads((output / "export_summary.json").read_bytes())
            self.assertEqual("failed", summary["export_status"])
            self.assertEqual(2, summary["total_assets"])
            self.assertEqual(1, summary["total_xmp_files_created"])
            self.assertEqual(1, summary["total_faces_processed"])
            self.assertEqual({"Ada": 1}, summary["people_statistics"])

    def test_collisions_through_symlinks_keep_first_asset_and_continue_with_other_paths(self):
        photos = [asset("first", [person("Ada")], "/photos/same.jpg"),
                  asset("collision", [person("Bea")], "/alias/same.jpg"),
                  asset("other", [person("Cara")], "/photos/other.jpg")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "photos").mkdir()
            try:
                (output / "alias").symlink_to(output / "photos", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlinks are unavailable")
            with self.assertLogs(exporter.logger, level="ERROR"):
                self.assertFalse(exporter.write_xmp_for_assets(photos, directory))
            self.assertEqual(["Ada"], region_names(ET.parse(output / "photos/same.jpg.xmp").getroot()))
            self.assertEqual(["Cara"], region_names(ET.parse(output / "photos/other.jpg.xmp").getroot()))
            summary = json.loads((output / "export_summary.json").read_bytes())
            self.assertEqual((3, 2, 2), tuple(summary[key] for key in
                             ("total_assets", "total_xmp_files_created", "total_faces_processed")))
            self.assertEqual({"Ada": 1, "Cara": 1}, summary["people_statistics"])

    def test_failed_write_still_reserves_its_destination_against_collisions(self):
        photos = [asset("first", [person("Ada")], "/photos/same.jpg"),
                  asset("collision", [person("Bea")], "/photos/same.jpg"), asset("other")]
        original_open = open
        attempts = []

        def failing_open(path, *args, **kwargs):
            if Path(path).name == "same.jpg.xmp":
                attempts.append(path)
                raise OSError("sidecar is not writable")
            return original_open(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            with patch("builtins.open", side_effect=failing_open), self.assertLogs(exporter.logger, level="ERROR") as messages:
                self.assertFalse(exporter.write_xmp_for_assets(photos, directory))
            self.assertEqual(1, len(attempts))
            self.assertTrue(any("Path collision" in message for message in messages.output))
            summary = json.loads((Path(directory) / "export_summary.json").read_bytes())
            self.assertEqual(1, summary["total_xmp_files_created"])
            self.assertEqual({}, summary["people_statistics"])

    def test_later_run_can_replace_previously_exported_sidecar_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for name in ("Ada", "Bea"):
                self.assertTrue(exporter.write_xmp_for_assets([asset("photo", [person(name)])], directory))
                self.assertEqual([name], region_names(ET.parse(output / "photos/2026/photo.jpg.xmp").getroot()))
                summary = json.loads((output / "export_summary.json").read_bytes())
                self.assertEqual("completed", summary["export_status"])
                self.assertEqual(1, summary["total_xmp_files_created"])
                self.assertEqual({name: 1}, summary["people_statistics"])

    def test_windows_style_paths_keep_current_host_specific_interpretation(self):
        cases = [("C:/photos/archive/photo.jpg", "photos/archive/photo.jpg.xmp"),
                 (r"C:\photos\archive\photo.jpg", "photos/archive/photo.jpg.xmp" if os.name == "nt"
                  else r"photos\archive\photo.jpg.xmp"),
                 (r"\\nas\photos\photo.jpg", "nas/photos/photo.jpg.xmp" if os.name == "nt"
                  else r"nas\photos\photo.jpg.xmp")]
        for original, relative in cases:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                self.assertTrue(exporter.save_xmp_sidecar(original, "<xmp/>", directory))
                files = [path.relative_to(directory).as_posix() for path in Path(directory).rglob("*.xmp")]
                self.assertEqual([relative], files)

    def test_stage2_recomputes_total_instead_of_trusting_export_header(self):
        data = json.loads(LEGACY_JSON)
        data["total_assets"] = 99999
        data["assets"].append(asset("empty"))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "legacy.json"
            source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            before = source.read_bytes()
            output = Path(directory) / "xmp"
            with patch.object(exporter, "datetime", FixedClock):
                self.assertTrue(exporter.export_assets_to_xmp_from_json(str(source), str(output), "assets-with-faces"))
            summary = json.loads((output / "export_summary.json").read_bytes())
            self.assertEqual((1, 1, 8, 3), tuple(summary[key] for key in
                             ("total_assets", "total_xmp_files_created", "total_faces_processed", "unique_people")))
            self.assertEqual(1, len(list(output.rglob("*.xmp"))))
            self.assertEqual(before, source.read_bytes())

    def test_stage2_accepts_existing_raw_name_aliases_and_fallback_identifiers(self):
        raw_style = {"id": "raw-id", "originalPath": "/alias/raw.jpg", "originalFileName": "alias.jpg", "people": []}
        missing_path = {"asset_id": "no-path", "people": []}
        no_id = {"people": []}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "legacy.json"
            source.write_text(json.dumps({"assets": [raw_style, missing_path, no_id]}), encoding="utf-8")
            output = Path(directory) / "xmp"
            self.assertTrue(exporter.export_assets_to_xmp_from_json(str(source), str(output)))
            self.assertEqual({"alias/raw.jpg.xmp", "unknown_no-path.jpg.xmp", "unknown_idx_3.jpg.xmp"},
                             {path.relative_to(output).as_posix() for path in output.rglob("*.xmp")})
            self.assertEqual("alias.jpg", ET.parse(output / "alias/raw.jpg.xmp").find(".//xmp:Identifier", NS).text)


class DeterministicJsonTests(unittest.TestCase):
    def test_shuffled_records_nested_arrays_and_object_keys_produce_identical_bytes(self):
        photos = [
            asset("z", [person("Zoe", [face(id="z-face"), face(id="a-face")], id="z-person"),
                        person("Ada", id="a-person")], "/same.jpg"),
            asset("a", original_path="/same.jpg"),
            asset("last-id", original_path="/alpha.jpg"),
            asset("unicode", original_path="/雪.jpg"),
        ]
        photos[0]["extension"] = {"coordinates": [3, 1, 2], "flags": [None, False, 0, ""],
                                  "metadata": {"z": "last", "a": "first"}}
        before = copy.deepcopy(photos)

        def reverse_keys(value):
            if isinstance(value, dict):
                return {key: reverse_keys(item) for key, item in reversed(list(value.items()))}
            if isinstance(value, list):
                return [reverse_keys(item) for item in value]
            return value

        shuffled = reverse_keys(list(reversed(photos)))
        for photo in shuffled:
            photo["people"].reverse()
            for who in photo["people"]:
                who["faces"].reverse()
        shuffled_before = copy.deepcopy(shuffled)
        results = []
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "datetime", FixedClock):
            for number, records in enumerate((photos, shuffled)):
                output = Path(directory) / str(number)
                source = exporter.write_assets_json(iter(records), str(output), immich_server="https://example.invalid")
                self.assertIsNotNone(source)
                self.assertEqual([Path(source)], list(output.iterdir()))
                results.append(Path(source).read_bytes())
        self.assertEqual(results[0], results[1])
        data = json.loads(results[0])
        self.assertEqual(["last-id", "a", "z", "unicode"], [photo["asset_id"] for photo in data["assets"]])
        people = data["assets"][2]["people"]
        self.assertEqual(["a-person", "z-person"], [who["id"] for who in people])
        self.assertEqual(["a-face", "z-face"], [item["id"] for item in people[1]["faces"]])
        self.assertEqual(before[0]["extension"], data["assets"][2]["extension"])
        self.assertEqual(before, photos)
        self.assertEqual(shuffled_before, shuffled)
        lines = results[0].decode("utf-8").splitlines()
        self.assertEqual(4, len(lines[5:-2]))
        for line, photo in zip(lines[5:-2], data["assets"]):
            self.assertEqual(json.dumps(photo, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                             line.removesuffix(","))

    def test_missing_and_duplicate_ids_use_content_tiebreakers_without_dropping_records(self):
        photos = [
            {"original_path": "/same.jpg", "asset_id": "same", "marker": "z", "people": [
                {"name": "Unknown", "faces": [face(boundingBoxX1=200), face(boundingBoxX1=100)]},
                {"id": "person", "name": "Zoe", "faces": [face(id="same", boundingBoxX1=200),
                                                             face(id="same", boundingBoxX1=100)]},
                {"id": "person", "name": "Ada", "faces": None},
            ]},
            {"original_path": "/same.jpg", "asset_id": "same", "marker": "a", "people": None},
            {"originalPath": "/alias.jpg", "id": "alias-id", "people": []},
            {"file_name": "idless.jpg"},
            {"file_name": "idless.jpg"},
        ]
        reversed_photos = copy.deepcopy(list(reversed(photos)))
        reversed_photos[-1]["people"].reverse()
        for who in reversed_photos[-1]["people"]:
            if isinstance(who.get("faces"), list):
                who["faces"].reverse()
        results = []
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "datetime", FixedClock):
            for records in (photos, reversed_photos):
                source = exporter.write_assets_json(iter(records), directory, immich_server="https://example.invalid")
                self.assertIsNotNone(source)
                results.append(Path(source).read_bytes())
        self.assertEqual(results[0], results[1])
        data = json.loads(results[0])
        self.assertEqual(5, data["total_assets"])
        self.assertEqual(2, data["assets"].count({"file_name": "idless.jpg"}))
        self.assertEqual("alias-id", data["assets"][2]["id"])
        by_marker = {photo["marker"]: photo for photo in data["assets"][3:]}
        self.assertEqual({"a", "z"}, set(by_marker))
        self.assertIsNone(by_marker["a"]["people"])
        self.assertEqual(["Unknown", "Zoe", "Ada"], [who["name"] for who in by_marker["z"]["people"]])

    def test_new_export_time_changes_only_the_timestamp_line(self):
        results = []
        with tempfile.TemporaryDirectory() as directory:
            for now in (datetime(2026, 1, 2, 3, 4, 5), datetime(2026, 2, 3, 4, 5, 6)):
                with patch.object(exporter, "datetime") as clock:
                    clock.now.return_value = now
                    source = exporter.write_assets_json([asset("b"), asset("a")], directory,
                                                        immich_server="https://example.invalid")
                self.assertIsNotNone(source)
                results.append(Path(source).read_text(encoding="utf-8").splitlines())
        self.assertEqual(len(results[0]), len(results[1]))
        self.assertEqual([1], [i for i, (left, right) in enumerate(zip(*results)) if left != right])
        self.assertIn('"export_timestamp"', results[0][1])

    def test_one_metadata_change_affects_only_its_asset_line(self):
        photos = [asset("c"), asset("a"), asset("b")]
        results = []
        with tempfile.TemporaryDirectory() as directory, patch.object(exporter, "datetime", FixedClock):
            for changed in (False, True):
                if changed:
                    photos.reverse()
                    photos[0]["exifInfo"]["rating"] = 5
                source = exporter.write_assets_json(iter(photos), directory, immich_server="https://example.invalid")
                self.assertIsNotNone(source)
                results.append(Path(source).read_text(encoding="utf-8").splitlines())
        self.assertEqual(len(results[0]), len(results[1]))
        self.assertEqual([6], [i for i, (left, right) in enumerate(zip(*results)) if left != right])
        self.assertEqual("b", json.loads(results[1][6].removesuffix(","))["asset_id"])

    def test_temporary_json_failures_close_files_and_preserve_previous_export(self):
        original_open = open
        for phase in ("write", "read", "short-read"):
            closed = []

            class FailingSpool:
                def __init__(self, stream):
                    self.stream = stream

                def __getattr__(self, name):
                    return getattr(self.stream, name)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()
                    closed.append(True)

                def write(self, content):
                    if phase == "write":
                        self.stream.write(content[:10])
                        raise OSError("injected disk failure")
                    return self.stream.write(content)

                def read(self, length):
                    if phase == "read":
                        raise OSError("injected disk failure")
                    return self.stream.read(length - 1 if phase == "short-read" else length)

            def failing_open(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                return FailingSpool(stream) if Path(path).name == "assets.jsonl" else stream

            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                previous = output / EXPORT_JSON_NAME
                previous.write_bytes(b"previous export")
                with (
                    patch.object(exporter, "datetime", FixedClock),
                    patch("builtins.open", side_effect=failing_open),
                    self.assertLogs(exporter.logger, level="ERROR"),
                ):
                    self.assertIsNone(exporter.write_assets_json([asset("z"), asset("a")], directory,
                                                                immich_server="https://example.invalid"))
                self.assertEqual([True], closed)
                self.assertEqual(b"previous export", previous.read_bytes())
                self.assertEqual([previous], list(output.iterdir()))


class ExportOrderingCompatibilityTests(unittest.TestCase):
    """Stage 1 sorts after selection; Stage 2 respects the saved record order."""

    def test_equal_capture_timestamps_sort_json_after_server_order_selection_and_limits(self):
        orders = (("z-photo", "a-photo", "m-photo"), ("m-photo", "a-photo", "z-photo"))
        for mode, order, limit in product(EXPORT_MODES, orders, (None, 1, 2)):
            with self.subTest(mode=mode, order=order, limit=limit), tempfile.TemporaryDirectory() as directory:
                known_person = {"id": "person-a", "name": "Ada", "isHidden": False}
                items = [{
                    "id": asset_id, "originalPath": f"/photos/{asset_id}.jpg",
                    "originalFileName": f"{asset_id}.jpg", "width": 3024, "height": 4032,
                    "exifInfo": {"dateTimeOriginal": "2020-01-02T03:04:05+00:00", "orientation": "6"},
                    "people": [known_person],
                } for asset_id in order]
                data = {
                    "pages": {
                        "1": {"assets": {"items": items[:2], "nextPage": "2"}},
                        "2": {"assets": {"items": items[2:], "nextPage": None}},
                    },
                    "faces": {asset_id: [{**face(), "id": f"face-{asset_id}", "person": known_person}]
                              for asset_id in order},
                }
                before = copy.deepcopy(data)
                api = SyntheticAPI(data)
                with api_environment(api), configured_client() as client:
                    source = exporter.export_assets_to_json(
                        client, json_output_dir=directory, max_assets=limit, export=mode,
                    )
                self.assertIsNotNone(source)
                exported = json.loads(Path(source).read_bytes())
                expected_order = sorted(order if limit is None else order[:limit])
                self.assertEqual(expected_order, [item["asset_id"] for item in exported["assets"]])
                self.assertEqual(len(expected_order), exported["total_assets"])
                self.assertEqual([1, 2] if limit is None else [1],
                                 [payload["page"] for payload in api.search_payloads()])
                self.assertEqual(before, data)

    def test_stage2_keeps_tied_timestamp_order_for_colliding_sidecars(self):
        photos = [asset("z-photo", [person("Zoe")], "/photos/tied.jpg"),
                  asset("a-photo", [person("Ada")], "/photos/tied.jpg")]
        for mode, reverse in product(EXPORT_MODES, (False, True)):
            with self.subTest(mode=mode, reverse=reverse), tempfile.TemporaryDirectory() as directory:
                ordered = list(reversed(photos)) if reverse else photos
                source = Path(directory) / "input.json"
                source.write_text(json.dumps({"total_assets": 2, "assets": ordered}), encoding="utf-8")
                before = source.read_bytes()
                output = Path(directory) / "xmp"
                with patch.object(exporter, "datetime", FixedClock), self.assertLogs(exporter.logger, level="ERROR"):
                    self.assertFalse(exporter.export_assets_to_xmp_from_json(str(source), str(output), mode))
                root = ET.parse(output / "photos/tied.jpg.xmp").getroot()
                expected_name = ordered[0]["people"][0]["name"]
                self.assertEqual([expected_name], region_names(root))
                self.assertEqual(ordered[0]["file_name"], root.find(".//xmp:Identifier", NS).text)
                summary = json.loads((output / "export_summary.json").read_bytes())
                self.assertEqual("failed", summary["export_status"])
                self.assertEqual({expected_name: 1}, summary["people_statistics"])
                self.assertEqual((2, 1, 1), tuple(summary[key] for key in
                                 ("total_assets", "total_xmp_files_created", "total_faces_processed")))
                self.assertEqual({"photos/tied.jpg.xmp", "export_summary.json"},
                                 {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()})
                self.assertEqual(before, source.read_bytes())


class WorkerFailureCompatibilityTests(unittest.TestCase):
    """Use actual workers: a failed result must not silently end a metadata page."""

    PAGE_IDS = ("before", "failure", "after")

    @classmethod
    def failing_api(cls, failed_id):
        ids = (*cls.PAGE_IDS, "next-page")
        items = [{
            "id": value, "originalPath": f"/photos/{value}.jpg",
            "originalFileName": f"{value}.jpg", "width": 4000, "height": 3000,
            "people": [{"id": "person-a", "name": "Ada", "isHidden": False}],
        } for value in ids]
        api = SyntheticAPI({
            "pages": {
                "1": {"assets": {"items": items[:3], "nextPage": "2"}},
                "2": {"assets": {"items": items[3:], "nextPage": None}},
            },
            "faces": {value: [face(person={"id": "person-a", "name": "Ada", "isHidden": False})]
                      for value in ids},
        })
        api.face_errors[failed_id] = StopIteration("injected face worker failure")
        return api

    def test_stage1_worker_stopiteration_does_not_publish_partial_json(self):
        for mode, failed_id, has_previous in product(EXPORT_MODES, self.PAGE_IDS, (False, True)):
            with self.subTest(mode=mode, failed_id=failed_id, has_previous=has_previous):
                api = self.failing_api(failed_id)
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory)
                    previous = output / EXPORT_JSON_NAME
                    if has_previous:
                        previous.write_bytes(b"previous JSON export")
                    with (
                        api_environment(api), configured_client() as client,
                        patch.object(exporter.logger, "error") as errors,
                    ):
                        result = exporter.export_assets_to_json(client, json_output_dir=directory, export=mode)
                    self.assertIsNone(result)
                    self.assertTrue(errors.called)
                    expected = {EXPORT_JSON_NAME: b"previous JSON export"} if has_previous else {}
                    self.assertEqual(expected, {path.name: path.read_bytes() for path in output.iterdir()})
                self.assertEqual([1], [payload["page"] for payload in api.search_payloads()])
                self.assertCountEqual(self.PAGE_IDS, api.face_ids())

    def test_direct_worker_stopiteration_does_not_publish_success_summary(self):
        for mode, failed_id, has_previous in product(EXPORT_MODES, self.PAGE_IDS, (False, True)):
            with self.subTest(mode=mode, failed_id=failed_id, has_previous=has_previous):
                api = self.failing_api(failed_id)
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory)
                    summary = output / "export_summary.json"
                    if has_previous:
                        summary.write_bytes(b"previous summary")
                    with (
                        api_environment(api), configured_client() as client,
                        patch.object(exporter.logger, "error") as errors,
                    ):
                        result = exporter.export_assets_to_xmp(client, output_dir=directory, export=mode)
                    self.assertFalse(result)
                    self.assertTrue(errors.called)
                    consumed = self.PAGE_IDS[:self.PAGE_IDS.index(failed_id)]
                    expected = {f"photos/{value}.jpg.xmp" for value in consumed}
                    self.assertFalse(summary.exists())
                    self.assertEqual(expected, {path.relative_to(output).as_posix()
                                                for path in output.rglob("*") if path.is_file()})
                self.assertEqual([1], [payload["page"] for payload in api.search_payloads()])
                self.assertCountEqual(self.PAGE_IDS, api.face_ids())

    def test_worker_stopiteration_after_limit_does_not_fail_either_export(self):
        for mode, route, failed_id in product(EXPORT_MODES, ("stage1", "direct"), self.PAGE_IDS[1:]):
            with self.subTest(mode=mode, route=route, failed_id=failed_id):
                api = self.failing_api(failed_id)
                with tempfile.TemporaryDirectory() as directory:
                    output = Path(directory)
                    with (
                        api_environment(api), configured_client() as client,
                        patch.object(exporter.logger, "error") as errors,
                    ):
                        if route == "stage1":
                            result = exporter.export_assets_to_json(
                                client, json_output_dir=directory, max_assets=1, export=mode,
                            )
                        else:
                            result = exporter.export_assets_to_xmp(
                                client, output_dir=directory, max_assets=1, export=mode,
                            )
                    self.assertTrue(result)
                    errors.assert_not_called()
                    if route == "stage1":
                        data = json.loads(Path(result).read_bytes())
                        self.assertEqual(["before"], [item["asset_id"] for item in data["assets"]])
                        self.assertEqual(1, data["total_assets"])
                        self.assertEqual([Path(result)], list(output.iterdir()))
                    else:
                        self.assertEqual({"before.jpg.xmp"}, {path.name for path in output.rglob("*.xmp")})
                        data = json.loads((output / "export_summary.json").read_bytes())
                        self.assertEqual((1, 1, 1), tuple(data[key] for key in (
                            "total_assets", "total_xmp_files_created", "total_faces_processed",
                        )))
                        self.assertEqual({"Ada": 1}, data["people_statistics"])
                self.assertEqual([1], [payload["page"] for payload in api.search_payloads()])
                self.assertCountEqual(self.PAGE_IDS, api.face_ids())


class FunctionalBoundaryTests(unittest.TestCase):
    def test_renderer_uses_explicit_time_without_configuration_clock_or_io(self):
        record = asset(people=[person("Ada")])
        before = copy.deepcopy(record)
        fixed_time = datetime(2001, 2, 3, 4, 5, 6)
        with (
            patch.object(exporter, "ConfigLoader", side_effect=AssertionError("Renderer requested configuration")),
            patch.object(exporter, "datetime") as clock,
            patch("builtins.open", side_effect=AssertionError("Renderer attempted file I/O")),
        ):
            clock.now.side_effect = AssertionError("Renderer read the clock")
            result = exporter.render_xmp(record, now=fixed_time, require_face_regions=True)
        root = ET.fromstring(result.content)
        description = root.find(".//rdf:Description", NS)
        self.assertEqual("2001-02-03T04:05:06", description.get(f"{{{NS['xmp']}}}ModifyDate"))
        self.assertEqual("2001-02-03T04:05:06", description.get(f"{{{NS['xmp']}}}MetadataDate"))
        self.assertEqual({"Ada": 1}, result.region_counts)
        self.assertEqual(before, record)

    def test_render_results_and_export_statistics_are_independent(self):
        record = asset(people=[person("Ada", faces=[face(), face(boundingBoxX2=144)])])
        first = exporter.render_xmp(record, now=FixedClock.now())
        second = exporter.render_xmp(record, now=FixedClock.now())
        self.assertEqual(first.content, second.content)
        self.assertEqual({"Ada": 1}, first.region_counts)
        stats = exporter.ExportStatistics()
        self.assertEqual((0, 0, {}), (stats.files_created, stats.faces_processed, stats.people))
        stats.record_write(first)
        first.region_counts["Ada"] = 99
        self.assertEqual({"Ada": 1}, second.region_counts)
        self.assertEqual((1, 1, {"Ada": 1}), (stats.files_created, stats.faces_processed, stats.people))
        empty = exporter.render_xmp(asset(), now=FixedClock.now(), require_face_regions=True)
        self.assertEqual(("", {}), (empty.content, empty.region_counts))

    def test_json_writer_accepts_records_without_a_client_or_configuration(self):
        record = asset()
        record["extension"] = {"zero": 0, "null": None, "false": False, "nested": ["", {"more": 1}]}
        before = copy.deepcopy(record)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(exporter, "ConfigLoader", side_effect=AssertionError("Writer requested configuration")),
                patch.object(exporter, "ImmichClient", side_effect=AssertionError("Writer requested an API client")),
                patch.object(exporter, "datetime", FixedClock),
            ):
                result = exporter.write_assets_json(iter([record]), directory, immich_server="https://example.invalid/")
            data = json.loads(Path(result).read_bytes())
        self.assertEqual([before], data["assets"])
        self.assertIs(type(data["assets"][0]["extension"]["zero"]), int)
        self.assertIs(type(data["assets"][0]["extension"]["false"]), bool)
        self.assertEqual("https://example.invalid", data["immich_server"])
        self.assertEqual(1, data["total_assets"])
        self.assertEqual(before, record)

    def test_selection_checks_limit_before_pull_and_closes_its_source(self):
        closed = []

        def source():
            try:
                yield asset("no-face")
                yield asset("selected", [person("Ada")])
                raise AssertionError("Consumed a result beyond the selected-asset limit")
            finally:
                closed.append(True)

        client = Mock(spec=exporter.ImmichClient)
        client.iter_assets.return_value = source()
        records = list(exporter.process_assets(client, max_assets=1, export="assets-with-faces"))
        self.assertEqual(["selected"], [record["asset_id"] for record in records])
        self.assertEqual([True], closed)
        client.iter_assets.assert_called_once_with(album_id=None, library_id=None, known_visible_only=False)

    def test_closing_an_abandoned_selection_closes_its_source(self):
        closed = []

        def source():
            try:
                yield asset("first")
                raise AssertionError("Read after consumer stopped")
            finally:
                closed.append(True)

        client = Mock(spec=exporter.ImmichClient)
        client.iter_assets.return_value = source()
        records = exporter.process_assets(client)
        self.assertEqual("first", next(records)["asset_id"])
        records.close()
        self.assertEqual([True], closed)

class StructuredSearchTests(unittest.TestCase):
    """Exercise the public pipeline against the structured search wire contract."""

    def test_structured_payload_maps_filters_and_uses_only_opaque_cursors(self):
        for mode, album_id, library_id in product(EXPORT_MODES, (None, "album-a"), (None, "library-b")):
            with self.subTest(mode=mode, album_id=album_id, library_id=library_id):
                api = SyntheticAPI(structured_api_data(), search_api="structured")
                with api_environment(api, search_api="structured"), configured_client() as client:
                    list(exporter.process_assets(client, album_id=album_id, library_id=library_id, export=mode))
                filters = {"trashedAt": {"eq": None}}
                if album_id:
                    filters["albumIds"] = {"all": [album_id]}
                if library_id:
                    filters["libraryId"] = {"eq": library_id}
                expected = []
                for cursor in api.data["pages"]:
                    payload = {"size": 1000, "withExif": True, "filter": filters}
                    if mode == "assets-with-known-visible-faces":
                        payload["withPeople"] = True
                    if cursor is not None:
                        payload["cursor"] = cursor
                    expected.append(payload)
                self.assertEqual(expected, api.search_payloads())
                # Auth scope is supplied by Immich; no new owner lookup or restriction.
                self.assertTrue(all(url.endswith(("/search/metadata", "/faces")) for _, url, _ in api.calls))

    def test_structured_and_legacy_produce_equal_json_and_xmp_for_every_selection(self):
        for mode, limit in product(EXPORT_MODES, (None, 2)):
            with self.subTest(mode=mode, limit=limit), tempfile.TemporaryDirectory() as directory:
                raw = compatibility_api_data()
                # The legacy INNER JOIN requires an EXIF row. An empty object
                # represents a present row without the selected metadata fields.
                raw["pages"]["1"]["assets"]["items"][1]["exifInfo"] = {}
                results = {}
                for protocol in ("legacy", "structured"):
                    work = Path(directory) / protocol
                    data = copy.deepcopy(raw) if protocol == "legacy" else structured_api_data(raw)
                    api = SyntheticAPI(data, search_api=protocol)
                    with api_environment(api, search_api=protocol), configured_client() as client:
                        source = exporter.export_assets_to_json(
                            client, str(work / "json"), max_assets=limit, album_id="album-a",
                            library_id="library-b", export=mode,
                        )
                        self.assertIsNotNone(source)
                        exported = json.loads(Path(source).read_bytes())
                        self.assertTrue(exporter.export_assets_to_xmp_from_json(source, str(work / "staged"), mode))
                        self.assertTrue(exporter.export_assets_to_xmp(
                            client, str(work / "direct"), max_assets=limit, album_id="album-a",
                            library_id="library-b", export=mode,
                        ))
                    outputs = {}
                    for kind in ("staged", "direct"):
                        output = work / kind
                        outputs[kind] = {
                            path.relative_to(output).as_posix(): path.read_bytes()
                            for path in output.rglob("*.xmp")
                        }
                        summary = json.loads((output / "export_summary.json").read_bytes())
                        summary.pop("output_directory")
                        summary.pop("json_source", None)
                        outputs[kind + "_summary"] = summary
                    self.assertEqual(set(outputs["staged"]), set(outputs["direct"]))
                    for path in outputs["staged"]:
                        self.assertEqual(xmp_semantics(outputs["staged"][path]),
                                         xmp_semantics(outputs["direct"][path]))
                    self.assertEqual(outputs["staged_summary"], outputs["direct_summary"])
                    if limit is not None:
                        expected_ids = {
                            "all-assets": {"unknown", "empty"},
                            "assets-with-faces": {"unknown", "hidden"},
                            "assets-with-known-visible-faces": {"rich", "zero-area"},
                        }[mode]
                        self.assertEqual(expected_ids, {item["asset_id"] for item in exported["assets"]})
                    results[protocol] = exported, outputs
                self.assertEqual(results["legacy"], results["structured"])

    def test_missing_exif_rows_are_excluded_but_empty_metadata_is_retained(self):
        raw = structured_api_data()
        prototype = raw["pages"][None]["assets"]["items"][0]
        absent = {key: value for key, value in prototype.items() if key != "exifInfo"}
        absent["id"] = "absent"
        items = [absent, {**prototype, "id": "null", "exifInfo": None},
                 {**prototype, "id": "empty-exif", "exifInfo": {}},
                 {**prototype, "id": "rich-exif", "exifInfo": {"iso": 0, "description": ""}}]
        raw["pages"] = {None: {"assets": {"items": items, "nextCursor": None}}}
        raw["faces"] = {"empty-exif": [], "rich-exif": []}
        api = SyntheticAPI(raw, search_api="structured")
        with api_environment(api, search_api="structured"), configured_client() as client:
            records = list(exporter.process_assets(client))
        self.assertEqual(["empty-exif", "rich-exif"], [item["asset_id"] for item in records])
        self.assertEqual({}, records[0]["exifInfo"])
        self.assertEqual({"iso": 0, "description": ""}, records[1]["exifInfo"])
        self.assertCountEqual(["empty-exif", "rich-exif"], api.face_ids())

    def test_short_pages_continue_when_first_page_is_rejected_by_person_filter(self):
        api = SyntheticAPI(structured_api_data(), search_api="structured")
        with api_environment(api, search_api="structured"), configured_client() as client:
            records = list(exporter.process_assets(client, export="assets-with-known-visible-faces"))
        self.assertEqual(list(api.data["pages"]), [item.get("cursor") for item in api.search_payloads()])
        self.assertTrue({"unknown", "empty", "hidden"}.isdisjoint(api.face_ids()))
        self.assertEqual("rich", records[0]["asset_id"])
        self.assertEqual("orientation-8", records[-1]["asset_id"])

    def test_empty_raw_page_with_cursor_continues_to_later_results(self):
        raw = structured_api_data()
        raw["pages"][None]["assets"]["items"] = []
        api = SyntheticAPI(raw, search_api="structured")
        with api_environment(api, search_api="structured"), configured_client() as client:
            records = list(exporter.process_assets(client))
        self.assertEqual("rich", records[0]["asset_id"])
        self.assertEqual("orientation-8", records[-1]["asset_id"])
        self.assertEqual(list(raw["pages"]), [item.get("cursor") for item in api.search_payloads()])

    def test_null_cursor_ends_search_with_absent_or_null_nextpage(self):
        for nested, include_next_page in product((False, True), (False, True)):
            with self.subTest(nested=nested, include_next_page=include_next_page):
                raw = structured_api_data()
                first = raw["pages"][None]["assets"]
                first["items"][1]["exifInfo"] = {}
                first["nextCursor"] = None
                if include_next_page:
                    first["nextPage"] = None
                raw["pages"][None] = {"assets": first} if nested else first
                api = SyntheticAPI(raw, search_api="structured")
                with api_environment(api, search_api="structured"), configured_client() as client:
                    records = list(exporter.process_assets(client))
                self.assertEqual(["unknown", "empty", "hidden"], [item["asset_id"] for item in records])
                self.assertEqual([None], [item.get("cursor") for item in api.search_payloads()])

    def test_structured_rejects_legacy_pagination_before_hydration(self):
        for nested, terminal, empty in product((False, True), (False, True), (False, True)):
            with self.subTest(nested=nested, terminal=terminal, empty=empty):
                raw = structured_api_data()
                first = raw["pages"][None]["assets"]
                first["nextPage"] = "2"
                if terminal:
                    first["nextCursor"] = None
                if empty:
                    first["items"] = []
                raw["pages"][None] = {"assets": first} if nested else first
                api = SyntheticAPI(raw, search_api="structured")
                with api_environment(api, search_api="structured"), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"), self.assertRaises(RuntimeError) as raised:
                        list(exporter.process_assets(client))
                self.assertIn("structured", str(raised.exception).lower())
                self.assertIn("nextPage", str(raised.exception))
                self.assertEqual([None], [item.get("cursor") for item in api.search_payloads()])
                self.assertEqual([], api.face_ids())

    def test_interleaved_searches_keep_pagination_independent_when_one_is_abandoned(self):
        for protocol in ("legacy", "structured"):
            with self.subTest(protocol=protocol):
                raw = compatibility_api_data()
                for page in raw["pages"].values():
                    page["assets"]["items"] = page["assets"]["items"][:1]
                if protocol == "structured":
                    raw = structured_api_data(raw)
                api = SyntheticAPI(raw, search_api=protocol)
                with api_environment(api, search_api=protocol), configured_client() as client:
                    abandoned = client.iter_assets()
                    completed = client.iter_assets()
                    try:
                        self.assertEqual("unknown", next(abandoned)["asset_id"])
                        self.assertEqual("unknown", next(completed)["asset_id"])
                        self.assertEqual("rich", next(abandoned)["asset_id"])
                        abandoned.close()
                        self.assertEqual(["rich", "orientation-2"],
                                         [item["asset_id"] for item in completed])
                    finally:
                        abandoned.close()
                        completed.close()
                keys = list(raw["pages"])
                expected = [keys[0], keys[0], keys[1], keys[1], keys[2]]
                actual = [item.get("cursor") if protocol == "structured" else str(item["page"])
                          for item in api.search_payloads()]
                self.assertEqual(expected, actual)

    def test_missing_cursor_is_a_compatibility_error_without_legacy_fallback(self):
        for items in ([], [{"id": "photo", "exifInfo": {}}]):
            with self.subTest(items=items):
                raw = {"pages": {None: {"assets": {"items": items, "nextPage": None}}}, "faces": {}}
                api = SyntheticAPI(raw, search_api="structured")
                with api_environment(api, search_api="structured"), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"), self.assertRaises(RuntimeError) as raised:
                        list(exporter.process_assets(client))
                self.assertIn("structured", str(raised.exception).lower())
                self.assertEqual(1, len(api.search_payloads()))
                self.assertEqual([], api.face_ids())

    def test_invalid_cursor_and_item_shapes_fail_before_face_requests(self):
        responses = [
            {"assets": {"items": [], "nextCursor": value}}
            for value in ("", 0, False, [], {})
        ] + [
            {"assets": {"items": value, "nextCursor": None}}
            for value in (None, "invalid", {}, [None], ["not-an-asset"])
        ]
        for response in responses:
            with self.subTest(response=response):
                api = SyntheticAPI({"pages": {None: response}, "faces": {}}, search_api="structured")
                with api_environment(api, search_api="structured"), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"), self.assertRaises(ValueError):
                        list(exporter.process_assets(client))
                self.assertEqual(1, len(api.search_payloads()))
                self.assertEqual([], api.face_ids())

    def test_repeated_cursor_and_cursor_cycle_are_rejected_before_duplicate_hydration(self):
        for repeat_on_page in (2, 3):
            with self.subTest(repeat_on_page=repeat_on_page):
                raw = structured_api_data()
                keys = list(raw["pages"])
                raw["pages"][keys[repeat_on_page - 1]]["assets"]["nextCursor"] = keys[1]
                api = SyntheticAPI(raw, search_api="structured")
                with api_environment(api, search_api="structured"), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"), self.assertRaises(ValueError) as raised:
                        list(exporter.process_assets(client))
                self.assertIn("cursor", str(raised.exception).lower())
                self.assertEqual(keys[:repeat_on_page], [item.get("cursor") for item in api.search_payloads()])
                self.assertEqual(len(api.face_ids()), len(set(api.face_ids())))
                last_items = raw["pages"][keys[repeat_on_page - 1]]["assets"]["items"]
                self.assertTrue({item["id"] for item in last_items}.isdisjoint(api.face_ids()))

    def test_unsupported_structured_requests_have_actionable_errors_without_retrying_legacy(self):
        for status in (400, 404, 405, 410, 422):
            with self.subTest(status=status):
                api = SyntheticAPI(structured_api_data(), search_api="structured")
                response = exporter.requests.Response()
                response.status_code = status
                api.page_errors[None] = exporter.requests.exceptions.HTTPError("request rejected", response=response)
                with api_environment(api, search_api="structured"), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"), self.assertRaises(RuntimeError) as raised:
                        list(exporter.process_assets(client))
                self.assertIn("structured", str(raised.exception).lower())
                self.assertEqual(1, len(api.search_payloads()))
                self.assertEqual([], api.face_ids())

    def test_auth_and_server_errors_retain_the_original_http_error(self):
        for status in (401, 403, 429, 500, 503):
            with self.subTest(status=status):
                api = SyntheticAPI(structured_api_data(), search_api="structured")
                response = exporter.requests.Response()
                response.status_code = status
                error = exporter.requests.exceptions.HTTPError("search failed", response=response)
                api.page_errors[None] = error
                with api_environment(api, search_api="structured"), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"), self.assertRaises(type(error)) as raised:
                        list(exporter.process_assets(client))
                self.assertIs(error, raised.exception)
                self.assertEqual(1, len(api.search_payloads()))

    def test_missing_face_endpoint_is_not_misreported_as_search_compatibility(self):
        api = SyntheticAPI(structured_api_data(), search_api="structured")
        response = exporter.requests.Response()
        response.status_code = 404
        error = exporter.requests.exceptions.HTTPError("face not found", response=response)
        api.face_errors["unknown"] = error
        with api_environment(api, search_api="structured"), configured_client() as client:
            with self.assertLogs(exporter.logger, level="ERROR"), self.assertRaises(type(error)) as raised:
                list(exporter.process_assets(client, max_assets=1))
        self.assertIs(error, raised.exception)

    def test_face_failure_beyond_selected_limit_is_not_consumed(self):
        api = SyntheticAPI(structured_api_data(), search_api="structured")
        api.face_errors["hidden"] = exporter.requests.exceptions.Timeout("unused later asset")
        with api_environment(api, search_api="structured"), configured_client() as client:
            records = list(exporter.process_assets(client, max_assets=1))
        self.assertEqual(["unknown"], [item["asset_id"] for item in records])
        self.assertIn("hidden", api.face_ids())
        self.assertEqual(1, len(api.search_payloads()))

    def test_incompatible_search_page_preserves_previous_json(self):
        for failed_page, mismatch in product((0, 1), ("missing-cursor", "mixed-terminal", "mixed-continuing")):
            with self.subTest(failed_page=failed_page, mismatch=mismatch), tempfile.TemporaryDirectory() as directory:
                raw = structured_api_data()
                keys = list(raw["pages"])
                page = raw["pages"][keys[failed_page]]["assets"]
                if mismatch == "missing-cursor":
                    page.pop("nextCursor")
                else:
                    page["nextPage"] = str(failed_page + 2)
                    if mismatch == "mixed-terminal":
                        page["nextCursor"] = None
                api = SyntheticAPI(raw, search_api="structured")
                output = Path(directory)
                previous = output / EXPORT_JSON_NAME
                previous.write_bytes(b"previous complete export")
                with api_environment(api, search_api="structured"), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"):
                        self.assertIsNone(exporter.export_assets_to_json(client, str(output)))
                self.assertEqual(b"previous complete export", previous.read_bytes())
                self.assertEqual([previous], list(output.iterdir()))
                self.assertEqual(keys[:failed_page + 1],
                                 [item.get("cursor") for item in api.search_payloads()])
                self.assertCountEqual(["unknown", "hidden"] if failed_page else [], api.face_ids())

    def test_mixed_pagination_failure_in_direct_export_removes_stale_success_summary(self):
        for failed_page, terminal in product((0, 1), (False, True)):
            with self.subTest(failed_page=failed_page, terminal=terminal), tempfile.TemporaryDirectory() as directory:
                raw = structured_api_data()
                keys = list(raw["pages"])
                page = raw["pages"][keys[failed_page]]["assets"]
                page["nextPage"] = str(failed_page + 2)
                if terminal:
                    page["nextCursor"] = None
                api = SyntheticAPI(raw, search_api="structured")
                output = Path(directory)
                summary = output / "export_summary.json"
                summary.write_text('{"export_status":"completed"}', encoding="utf-8")
                with api_environment(api, search_api="structured"), configured_client() as client:
                    with self.assertLogs(exporter.logger, level="ERROR"):
                        self.assertFalse(exporter.export_assets_to_xmp(client, str(output)))
                self.assertFalse(summary.exists())
                self.assertFalse((output / ".export_summary.json.tmp").exists())
                sidecars = {path.relative_to(output).as_posix() for path in output.rglob("*.xmp")}
                self.assertEqual({"photos/unknown.jpg.xmp", "photos/hidden.jpg.xmp"} if failed_page else set(),
                                 sidecars)
                self.assertEqual(keys[:failed_page + 1],
                                 [item.get("cursor") for item in api.search_payloads()])
                self.assertCountEqual(["unknown", "hidden"] if failed_page else [], api.face_ids())


if __name__ == "__main__":
    unittest.main()
