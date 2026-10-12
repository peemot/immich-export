"""Offline regression tests for the JSON, face-region, and XMP export paths."""

import copy
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from concurrent.futures import Future
from datetime import datetime
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
    # process_assets catches broad exceptions; check calls even if it swallowed the error.
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


def completed(value):
    result = Future()
    result.set_result(value)
    return result


def region_names(root):
    return [node.get(f"{{{NS['mwg']}}}Name") for node in root.findall(REGION_PATH, NS)]


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

    @patch.object(exporter, "api_request")
    def test_api_faces_are_grouped_by_person_and_unknown(self, request):
        request.return_value.json.return_value = [
            {**face(), "person": {"id": "p1", "name": "Ada"}},
            {**face(boundingBoxX1=500), "person": {"id": "p1", "name": "Ada"}},
            {**face(), "person": None},
            "unexpected face entry",
        ]
        groups = exporter.get_asset_people(Mock(), "photo-1", "token")
        self.assertEqual(["Ada", "Unknown"], [p["name"] for p in groups])
        self.assertEqual([2, 1], [len(p["faces"]) for p in groups])
        self.assertNotIn("person", groups[0]["faces"][0])
        self.assertEqual({"id": "photo-1"}, request.call_args.kwargs["params"])
        self.assertEqual([], exporter.get_asset_people(Mock(), ""))
        request.return_value.json.return_value = {"not": "a list"}
        with self.assertRaisesRegex(ValueError, "faces response shape"):
            exporter.get_asset_people(Mock(), "photo-1")


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
        root = ET.fromstring(exporter.create_xmp_content(photo, require_face_regions=True))

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
                root = ET.fromstring(exporter.create_xmp_content(photo, require_face_regions=True))
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
        root = ET.fromstring(exporter.create_xmp_content(photo))
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
        root = ET.fromstring(exporter.create_xmp_content(photo))
        self.assertEqual(["Ada", "Bea"], [node.text for node in root.findall(".//dc:subject/rdf:Bag/rdf:li", NS)])
        self.assertCountEqual(["Bea", "Ada", "Ada", "Ada", "Unknown"], region_names(root))

    def test_required_regions_reject_missing_or_zero_area(self):
        self.assertEqual("", exporter.create_xmp_content(asset(), require_face_regions=True))
        zero_area = asset(people=[person(faces=[face(boundingBoxX2=144)])])
        self.assertEqual("", exporter.create_xmp_content(zero_area, require_face_regions=True))
        ET.fromstring(exporter.create_xmp_content(asset()))  # All-assets allows metadata-only sidecars.


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
                    patch.object(exporter, "get_config", return_value=config),
                    patch.object(exporter, "Queue", TimedQueue),
                    patch.object(exporter.requests.Session, "request", side_effect=http_response) as transport,
                    patch.object(exporter, "datetime") as clock,
                    exporter.create_http_session(0) as session,
                ):
                    clock.now.return_value = datetime(2026, 7, 22, 12)
                    source = exporter.export_assets_to_json(session, json_output_dir=directory, export=mode)
                    photos = json.loads(Path(source).read_text(encoding="utf-8"))["assets"]
                    self.assertEqual(expected_ids, [photo["asset_id"] for photo in photos])
                    portrait = photos[-1]
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
                    self.assertEqual(expected_faces, [f["id"] for p in portrait["people"] for f in p["faces"]])

                    staged, direct = Path(directory) / "staged", Path(directory) / "direct"
                    self.assertTrue(exporter.export_assets_to_xmp_from_json(source, str(staged), mode))
                    self.assertTrue(exporter.export_assets_to_xmp(session, output_dir=str(direct), export=mode))
                    for output in (staged, direct):
                        self.assertEqual(
                            {f"{key}.jpg.xmp" for key in expected_ids}, {p.name for p in output.rglob("*.xmp")}
                        )
                        root = ET.parse(output / "photos/portrait.jpg.xmp").getroot()
                        self.assertEqual(expected_names, region_names(root))
                        area = root.find(".//mwg:Area", NS)
                        self.assertEqual((.4, .8, .4, .2), tuple(
                            float(area.get(f"{{{NS['stArea']}}}{key}")) for key in ("x", "y", "w", "h")
                        ))
                    for key in expected_ids:
                        relative = f"photos/{key}.jpg.xmp"
                        self.assertEqual((staged / relative).read_bytes(), (direct / relative).read_bytes())
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

    @patch.object(exporter, "get_page_asset_people")
    @patch.object(exporter, "api_request")
    def test_search_paginates_and_limit_counts_exported_assets(self, request, page_people):
        pages = [
            {"assets": {"items": [{"id": "no-face"}, {"id": "photo-1"}], "nextPage": "2"}},
            {"assets": {"items": [{"id": "photo-2"}, {"id": "photo-3"}], "nextPage": None}},
        ]
        request.side_effect = [Mock(json=Mock(return_value=page)) for page in pages]
        page_people.side_effect = lambda items, token: [
            completed([] if item["id"] == "no-face" else [person()]) for item in items
        ]
        photos = list(exporter.process_assets(
            Mock(), max_assets=2, album_id="album-a", library_id="library-b", export="assets-with-faces"
        ))

        self.assertEqual(["photo-1", "photo-2"], [p["asset_id"] for p in photos])
        self.assertEqual([1, 2], [call.kwargs["json"]["page"] for call in request.call_args_list])
        for call in request.call_args_list:
            self.assertEqual("/search/metadata", call.args[2])
            self.assertEqual(["album-a"], call.kwargs["json"]["albumIds"])
            self.assertEqual("library-b", call.kwargs["json"]["libraryId"])
            self.assertTrue(call.kwargs["json"]["withExif"])
            self.assertNotIn("withPeople", call.kwargs["json"])

    @patch.object(exporter, "get_page_asset_people")
    @patch.object(exporter, "api_request")
    def test_known_visible_search_filters_metadata_before_face_lookup(self, request, page_people):
        items = [
            {"id": "hidden", "people": [{"name": "Ada", "isHidden": True}]},
            {"id": "unknown", "people": [{"name": "Unknown"}]},
            {"id": "visible", "people": [{"name": "Ada"}]},
        ]
        request.return_value.json.return_value = {"assets": {"items": items, "nextPage": None}}
        page_people.return_value = [completed([person()])]
        photos = list(exporter.process_assets(Mock(), export="assets-with-known-visible-faces"))
        self.assertEqual(["visible"], [p["asset_id"] for p in photos])
        self.assertEqual([items[2]], page_people.call_args.args[0])
        self.assertTrue(request.call_args.kwargs["json"]["withPeople"])

    @patch.object(exporter, "process_assets")
    @patch.object(exporter, "get_config")
    def test_stage1_preserves_complete_assets_and_stage2_can_read_them(self, config, process):
        config.return_value.get_immich_config.return_value = {"base_url": "https://example.invalid/"}
        photos = [asset("photo-1", [person("Éva")]), asset("photo-2")]
        process.return_value = iter(photos)
        with tempfile.TemporaryDirectory() as directory:
            path = exporter.export_assets_to_json(Mock(), json_output_dir=directory)
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
                self.assertIsNone(exporter.export_assets_to_json(Mock(), json_output_dir=directory))
            self.assertEqual([], list(Path(directory).iterdir()))

    @patch.object(exporter, "get_page_asset_people")
    @patch.object(exporter, "api_request")
    def test_stage1_later_api_failure_leaves_no_partial_json(self, request, people):
        request.side_effect = [
            Mock(json=Mock(return_value={"assets": {"items": [{"id": "photo-1"}], "nextPage": "2"}})),
            exporter.requests.exceptions.Timeout("page 2 failed"),
        ]
        people.return_value = [completed([])]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertLogs(exporter.logger, level="ERROR") as messages:
                self.assertIsNone(exporter.export_assets_to_json(Mock(), json_output_dir=directory))
            self.assertEqual(2, request.call_count)
            self.assertTrue(any("page 2 failed" in message for message in messages.output))
            self.assertEqual([], list(Path(directory).iterdir()))

    @patch.object(exporter, "process_assets")
    def test_stage1_write_error_is_reported_and_temp_file_is_removed(self, process):
        process.return_value = iter([asset()])
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(exporter.json, "dump", side_effect=OSError("disk full")):
                with self.assertLogs(exporter.logger, level="ERROR"):
                    self.assertIsNone(exporter.export_assets_to_json(Mock(), json_output_dir=directory))
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
                patch.object(exporter, "get_config", side_effect=AssertionError("Stage 2 tried to load credentials")),
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
                Mock(), "token", str(output), max_assets=1, album_id="album-a", export="assets-with-faces"
            ))
            self.assertEqual(("token", 1, "album-a", None, "assets-with-faces"), process.call_args.args[1:])
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
                self.assertTrue(exporter.write_xmp_for_assets(iter(photos), directory))
            summary = json.loads((Path(directory) / "export_summary.json").read_text(encoding="utf-8"))
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

    @patch.object(exporter, "get_page_asset_people")
    @patch.object(exporter, "api_request")
    def setUp(self, request, people):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        request.side_effect = [
            Mock(json=Mock(return_value={"assets": {"items": [{"id": "photo-1"}], "nextPage": "2"}})),
            exporter.requests.exceptions.Timeout("page 2 failed"),
        ]
        people.return_value = [completed([])]
        with self.assertLogs(exporter.logger, level="ERROR") as messages:
            self.success = exporter.export_assets_to_xmp(Mock(), output_dir=directory.name)
        self.assertEqual(2, request.call_count)
        self.assertTrue(any("page 2 failed" in message for message in messages.output))
        self.assertFalse((Path(directory.name) / "export_summary.json").exists())

    def test_later_api_failure_should_not_report_successful_export(self):
        self.assertFalse(self.success, "A partial export was reported as successful")


if __name__ == "__main__":
    unittest.main()
