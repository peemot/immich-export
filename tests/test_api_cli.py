"""Configuration, HTTP, worker lifecycle, and command-line regression tests."""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from unittest.mock import Mock, patch

import export_xmp as exporter


def setUpModule():
    guard = patch.object(exporter.requests.Session, "request", side_effect=AssertionError("Unexpected HTTP request"))
    blocked_requests = guard.start()
    unittest.addModuleCleanup(guard.stop)
    unittest.addModuleCleanup(blocked_requests.assert_not_called)


def config_stub(api_key="test-key"):
    config = Mock(spec=exporter.ConfigLoader)
    config.get_immich_config.return_value = {
        "base_url": "https://example.invalid/", "api_key": api_key,
        "email": "user@example.invalid", "password": "test-password",
    }
    config.get_settings_config.return_value = {
        "request_timeout": 12, "retry_attempts": 2, "face_request_workers": 2,
    }
    config.get_output_config.return_value = {"json_export_dir": "json", "xmp_export_dir": "xmp"}
    config.validate_immich_config.return_value = True
    return config


class TimedQueue(Queue):
    """A lost worker session must fail a test instead of blocking it forever."""

    def get(self, block=True, timeout=None):
        return super().get(block=block, timeout=2 if timeout is None else timeout)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config.json"

    def test_environment_overrides_file_and_retains_unspecified_settings(self):
        self.path.write_text(json.dumps({
            "immich": {"base_url": "https://file.invalid", "api_key": "file-key"},
            "settings": {"request_timeout": 30, "retry_attempts": 5},
            "output": {"xmp_export_dir": "file-xmp", "json_export_dir": "file-json"},
        }), encoding="utf-8")
        os.environ.update({
            "IMMICH_BASE_URL": "https://env.invalid", "IMMICH_API_KEY": "env-key",
            "IMMICH_REQUEST_TIMEOUT": "7", "OUTPUT_XMP_DIR": "env-xmp",
        })
        config = exporter.ConfigLoader(str(self.path))
        self.assertEqual("https://env.invalid", config.get_immich_config()["base_url"])
        self.assertEqual("env-key", config.get_immich_config()["api_key"])
        self.assertEqual(
            {"request_timeout": 7, "retry_attempts": 5, "face_request_workers": 8}, config.get_settings_config()
        )
        self.assertEqual({"xmp_export_dir": "env-xmp", "json_export_dir": "file-json"}, config.get_output_config())
        self.assertTrue(config.validate_immich_config())

    def test_invalid_file_uses_environment_and_defaults(self):
        self.path.write_text("{invalid", encoding="utf-8")
        os.environ.update(IMMICH_BASE_URL="https://example.invalid", IMMICH_API_KEY="test-key")
        with self.assertLogs(exporter.logger, level="WARNING"):
            config = exporter.ConfigLoader(str(self.path))
        self.assertTrue(config.validate_immich_config())
        self.assertEqual(30, config.get_settings_config()["request_timeout"])
        self.assertEqual("xmp_sidecars", config.get_output_config()["xmp_export_dir"])

    def test_worker_count_is_bounded_and_invalid_values_use_default(self):
        self.path.write_text("{}", encoding="utf-8")
        config = exporter.ConfigLoader(str(self.path))
        for value, expected in ((0, 1), (99, 16), ("3", 3), ("invalid", 8), (None, 8)):
            with self.subTest(value=value):
                config.config_data = {"settings": {"face_request_workers": value}}
                self.assertEqual(expected, config.get_settings_config()["face_request_workers"])

    def test_credentials_require_server_and_either_key_or_complete_login(self):
        cases = [
            ({"api_key": "key"}, False),
            ({"base_url": "https://example.invalid", "api_key": "key"}, True),
            ({"base_url": "https://example.invalid", "email": "a", "password": "b"}, True),
            ({"base_url": "https://example.invalid", "email": "a"}, False),
        ]
        for immich, expected in cases:
            with self.subTest(immich=immich):
                self.path.write_text(json.dumps({"immich": immich}), encoding="utf-8")
                config = exporter.ConfigLoader(str(self.path))
                with patch.object(exporter.logger, "error"):
                    self.assertEqual(expected, config.validate_immich_config())


class HttpTests(unittest.TestCase):
    def test_auth_headers_timeout_url_and_http_error_propagation(self):
        for key, token, auth_headers in (
            ("key", "token", {"x-api-key": "key"}),
            ("", "token", {"Cookie": "immich_access_token=token"}),
            ("", None, {}),
        ):
            with (
                self.subTest(key=key, token=token),
                patch.object(exporter, "get_config", return_value=config_stub(key)),
            ):
                session = Mock(spec=exporter.requests.Session)
                response = session.request.return_value
                self.assertIs(response, exporter.api_request(
                    session, "GET", "/faces", token=token, params={"id": "photo"}
                ))
                session.request.assert_called_once_with(
                    "GET", "https://example.invalid/api/faces", timeout=12, params={"id": "photo"},
                    headers={"Content-Type": "application/json", "Accept": "application/json", **auth_headers},
                )
                response.raise_for_status.assert_called_once_with()
                response.raise_for_status.side_effect = exporter.requests.exceptions.HTTPError("403")
                with self.assertRaises(exporter.requests.exceptions.HTTPError):
                    exporter.api_request(session, "GET", "/faces", token=token)

    @patch.object(exporter, "api_request")
    def test_login_handles_success_http_error_and_malformed_response(self, request):
        session = Mock()
        request.return_value.json.return_value = {"accessToken": "token"}
        self.assertEqual("token", exporter.authenticate(session, "email", "password"))
        request.assert_called_once_with(session, "POST", "/auth/login", json={"email": "email", "password": "password"})
        for failure in ("http", "json", "missing-token"):
            with self.subTest(failure=failure), self.assertLogs(exporter.logger, level="ERROR"):
                request.side_effect = exporter.requests.exceptions.HTTPError("401") if failure == "http" else None
                request.return_value.json.side_effect = (
                    json.JSONDecodeError("bad", "", 0) if failure == "json" else None
                )
                request.return_value.json.return_value = {}
                self.assertIsNone(exporter.authenticate(session, "email", "password"))

    def test_retries_include_metadata_posts_and_temporary_server_errors(self):
        with exporter.create_http_session(4) as session:
            for url in ("http://example.invalid", "https://example.invalid"):
                retries = session.get_adapter(url).max_retries
                self.assertEqual(4, retries.total)
                for status in (429, 500, 502, 503, 504):
                    self.assertTrue(retries.is_retry("POST", status))
                    self.assertTrue(retries.is_retry("GET", status))
                self.assertFalse(retries.is_retry("GET", 401))


class FaceWorkerTests(unittest.TestCase):
    def test_out_of_order_completion_keeps_asset_order_and_closes_sessions(self):
        second_finished = threading.Event()
        sessions = [Mock(spec=exporter.requests.Session) for _ in range(2)]

        class ObservedExecutor(ThreadPoolExecutor):
            def submit(self, function, item):
                future = super().submit(function, item)
                if item["id"] == "second":
                    # Signal actual completion, not merely entry into the second worker.
                    future.add_done_callback(lambda done: second_finished.set())
                return future

        def fetch(session, asset_id, token):
            self.assertIn(session, sessions)
            self.assertEqual("token", token)
            if asset_id == "first":
                self.assertTrue(second_finished.wait(timeout=2), "Second face request did not run concurrently")
            return [asset_id]

        with (
            patch.object(exporter, "get_config", return_value=config_stub()),
            patch.object(exporter, "Queue", TimedQueue),
            patch.object(exporter, "ThreadPoolExecutor", ObservedExecutor),
            patch.object(exporter, "create_http_session", side_effect=sessions) as create,
            patch.object(exporter, "get_asset_people", side_effect=fetch),
        ):
            futures = exporter.get_page_asset_people([{"id": "first"}, {"id": "second"}], "token")
        self.assertEqual([["first"], ["second"]], [future.result(timeout=3) for future in futures])
        self.assertEqual(2, create.call_count)
        for session in sessions:
            session.close.assert_called_once_with()

    def test_failed_request_returns_worker_session_and_preserves_exception(self):
        config = config_stub()
        config.get_settings_config.return_value["face_request_workers"] = 1
        session = Mock(spec=exporter.requests.Session)
        error = exporter.requests.exceptions.Timeout("face lookup failed")
        with (
            patch.object(exporter, "get_config", return_value=config),
            patch.object(exporter, "Queue", TimedQueue),
            patch.object(exporter, "create_http_session", return_value=session),
            patch.object(exporter, "get_asset_people", side_effect=[error, ["second"]]),
        ):
            futures = exporter.get_page_asset_people([{"id": "first"}, {"id": "second"}], None)
        with self.assertRaises(exporter.requests.exceptions.Timeout):
            futures[0].result(timeout=3)
        self.assertEqual(["second"], futures[1].result(timeout=3))
        session.close.assert_called_once_with()


class CommandLineTests(unittest.TestCase):
    def test_failed_login_stops_before_export(self):
        with (
            patch.object(sys, "argv", ["export_xmp.py"]),
            patch.object(exporter, "setup_logging"),
            patch.object(exporter, "get_config", return_value=config_stub(api_key="")),
            patch.object(exporter, "create_http_session"),
            patch.object(exporter, "authenticate", return_value=None),
            patch.object(exporter, "export_assets_to_json") as stage1,
            patch.object(exporter, "export_assets_to_xmp") as direct,
            self.assertLogs(exporter.logger, level="ERROR"),
        ):
            exporter.main()
        stage1.assert_not_called()
        direct.assert_not_called()

    def test_invalid_argument_combinations_are_rejected(self):
        for args in (
            ["--stage2-only"], ["--stage1-only", "--direct-xmp"],
            ["--direct-xmp", "--json-file", "data.json"], ["--export", "invalid"],
        ):
            with self.subTest(args=args), patch.object(sys, "argv", ["export_xmp.py", *args]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    exporter.parse_arguments()
                self.assertEqual(2, raised.exception.code)

    def test_modes_route_cli_overrides_and_use_correct_authentication(self):
        for mode, api_key in (([], "key"), (["--stage1-only"], "key"), (["--direct-xmp"], "")):
            with self.subTest(mode=mode):
                argv = ["export_xmp.py", *mode, "--json-dir", "cli-json", "--xmp-dir", "cli-xmp",
                        "--max-assets", "2", "--album-id", "album", "--library-id", "library",
                        "--export", "assets-with-faces"]
                session = Mock()
                with (
                    patch.object(sys, "argv", argv),
                    patch.object(exporter, "setup_logging"),
                    patch.object(exporter, "get_config", return_value=config_stub(api_key)),
                    patch.object(exporter, "create_http_session", return_value=session),
                    patch.object(exporter, "authenticate", return_value="token") as login,
                    patch.object(exporter, "export_assets_to_json", return_value="interim.json") as stage1,
                    patch.object(exporter, "export_assets_to_xmp_from_json", return_value=True) as stage2,
                    patch.object(exporter, "export_assets_to_xmp", return_value=True) as direct,
                ):
                    exporter.main()
                if api_key:
                    login.assert_not_called()
                else:
                    login.assert_called_once_with(session, "user@example.invalid", "test-password")
                if mode == ["--direct-xmp"]:
                    direct.assert_called_once_with(
                        session, "token", "cli-xmp", 2, "album", "library", "assets-with-faces"
                    )
                    stage1.assert_not_called()
                    stage2.assert_not_called()
                else:
                    stage1.assert_called_once_with(
                        session, None, "cli-json", 2, "album", "library", "assets-with-faces"
                    )
                    direct.assert_not_called()
                    if mode:
                        stage2.assert_not_called()
                    else:
                        stage2.assert_called_once_with("interim.json", "cli-xmp", "assets-with-faces")


if __name__ == "__main__":
    unittest.main()
