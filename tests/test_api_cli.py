"""Configuration, HTTP, worker lifecycle, and command-line regression tests."""

import contextlib
import io
import json
import os
import sys
import subprocess
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


def make_client(config=None, *, session=None, token=None):
    settings = exporter.ApiSettings.from_config(config if config is not None else config_stub())
    return exporter.ImmichClient(
        settings, session=Mock(spec=exporter.requests.Session) if session is None else session,
        access_token=token,
    )


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
            with self.subTest(key=key, token=token):
                session = Mock(spec=exporter.requests.Session)
                client = make_client(config_stub(key), session=session, token=token)
                response = session.request.return_value
                self.assertIs(response, client.request("GET", "/faces", params={"id": "photo"}))
                session.request.assert_called_once_with(
                    "GET", "https://example.invalid/api/faces", timeout=12, params={"id": "photo"},
                    headers={"Content-Type": "application/json", "Accept": "application/json", **auth_headers},
                )
                response.raise_for_status.assert_called_once_with()
                response.raise_for_status.side_effect = exporter.requests.exceptions.HTTPError("403")
                with self.assertRaises(exporter.requests.exceptions.HTTPError):
                    client.request("GET", "/faces")

    @patch.object(exporter.ImmichClient, "request")
    def test_login_handles_success_http_error_and_malformed_response(self, request):
        client = make_client()
        request.return_value.json.return_value = {"accessToken": "token"}
        self.assertEqual("token", client.authenticate("email", "password"))
        self.assertEqual("token", client.access_token)
        request.assert_called_once_with("POST", "/auth/login", json={"email": "email", "password": "password"})
        for failure in ("http", "json", "missing-token"):
            with self.subTest(failure=failure), self.assertLogs(exporter.logger, level="ERROR"):
                request.side_effect = exporter.requests.exceptions.HTTPError("401") if failure == "http" else None
                request.return_value.json.side_effect = (
                    json.JSONDecodeError("bad", "", 0) if failure == "json" else None
                )
                request.return_value.json.return_value = {}
                self.assertIsNone(client.authenticate("email", "password"))

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
        client = make_client(token="token")

        class ObservedExecutor(ThreadPoolExecutor):
            def submit(self, function, item):
                future = super().submit(function, item)
                if item["id"] == "second":
                    # Signal actual completion, not merely entry into the second worker.
                    future.add_done_callback(lambda done: second_finished.set())
                return future

        def fetch(asset_id, *, session):
            self.assertIn(session, sessions)
            self.assertEqual("token", client.access_token)
            if asset_id == "first":
                self.assertTrue(second_finished.wait(timeout=2), "Second face request did not run concurrently")
            return [asset_id]

        with (
            patch.object(exporter, "Queue", TimedQueue),
            patch.object(exporter, "ThreadPoolExecutor", ObservedExecutor),
            patch.object(exporter, "create_http_session", side_effect=sessions) as create,
            patch.object(client, "get_asset_people", side_effect=fetch),
        ):
            results = client._page_people([{"id": "first"}, {"id": "second"}])
        self.assertEqual([["first"], ["second"]], list(results))
        self.assertEqual(2, create.call_count)
        for session in sessions:
            session.close.assert_called_once_with()

    def test_failed_request_returns_worker_session_and_preserves_exception(self):
        config = config_stub()
        config.get_settings_config.return_value["face_request_workers"] = 1
        session = Mock(spec=exporter.requests.Session)
        client = make_client(config)
        error = exporter.requests.exceptions.Timeout("face lookup failed")
        with (
            patch.object(exporter, "Queue", TimedQueue),
            patch.object(exporter, "create_http_session", return_value=session),
            patch.object(client, "get_asset_people", side_effect=[error, ["second"]]),
        ):
            results = client._page_people([{"id": "first"}, {"id": "second"}])
        with self.assertRaises(exporter.requests.exceptions.Timeout):
            next(results)
        self.assertEqual(["second"], next(results))
        session.close.assert_called_once_with()

    def test_worker_stopiteration_is_not_mistaken_for_result_exhaustion(self):
        config = config_stub()
        config.get_settings_config.return_value["face_request_workers"] = 1
        session = Mock(spec=exporter.requests.Session)
        client = make_client(config)
        error = StopIteration("injected face worker failure")
        with (
            patch.object(exporter, "Queue", TimedQueue),
            patch.object(exporter, "create_http_session", return_value=session),
            patch.object(client, "get_asset_people", side_effect=[["before"], error, ["after"]]) as fetch,
        ):
            results = client._page_people([{"id": value} for value in ("before", "failure", "after")])

        # All work finishes and sessions close before results are consumed.
        self.assertEqual(3, fetch.call_count)
        session.close.assert_called_once_with()
        self.assertEqual(["before"], next(results))
        with self.assertRaises(RuntimeError) as raised:
            next(results)
        self.assertIs(error, raised.exception.__cause__)
        self.assertEqual(["after"], next(results))
        with self.assertRaises(StopIteration):
            next(results)


class CommandLineTests(unittest.TestCase):
    def test_failed_login_stops_before_export(self):
        with (
            patch.object(sys, "argv", ["export_xmp.py"]),
            patch.object(exporter, "setup_logging"),
            patch.object(exporter, "ConfigLoader", return_value=config_stub(api_key="")),
            patch.object(exporter, "create_http_session") as create,
            patch.object(exporter.ImmichClient, "authenticate", return_value=None),
            patch.object(exporter, "export_assets_to_json") as stage1,
            patch.object(exporter, "export_assets_to_xmp") as direct,
            self.assertLogs(exporter.logger, level="ERROR"),
        ):
            self.assertEqual(1, exporter.main())
        stage1.assert_not_called()
        direct.assert_not_called()
        create.return_value.close.assert_called_once_with()

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
        def authenticate(client, email, password):
            client.access_token = "token"
            return "token"

        for mode, api_key in (([], "key"), (["--stage1-only"], "key"), (["--direct-xmp"], "")):
            with self.subTest(mode=mode):
                argv = ["export_xmp.py", *mode, "--json-dir", "cli-json", "--xmp-dir", "cli-xmp",
                        "--max-assets", "2", "--album-id", "album", "--library-id", "library",
                        "--export", "assets-with-faces"]
                session = Mock()
                with (
                    patch.object(sys, "argv", argv),
                    patch.object(exporter, "setup_logging"),
                    patch.object(exporter, "ConfigLoader", return_value=config_stub(api_key)),
                    patch.object(exporter, "create_http_session", return_value=session),
                    patch.object(exporter.ImmichClient, "authenticate", autospec=True, side_effect=authenticate) as login,
                    patch.object(exporter, "export_assets_to_json", return_value="interim.json") as stage1,
                    patch.object(exporter, "export_assets_to_xmp_from_json", return_value=True) as stage2,
                    patch.object(exporter, "export_assets_to_xmp", return_value=True) as direct,
                ):
                    self.assertEqual(0, exporter.main())
                client = (direct if mode == ["--direct-xmp"] else stage1).call_args.args[0]
                self.assertIs(session, client.session)
                self.assertEqual(api_key, client.settings.api_key)
                self.assertEqual(12, client.settings.request_timeout)
                self.assertEqual(2, client.settings.retry_attempts)
                if api_key:
                    login.assert_not_called()
                    self.assertIsNone(client.access_token)
                else:
                    login.assert_called_once_with(client, "user@example.invalid", "test-password")
                    self.assertEqual("token", client.access_token)
                options = dict(max_assets=2, album_id="album", library_id="library", export="assets-with-faces")
                if mode == ["--direct-xmp"]:
                    direct.assert_called_once_with(client, "cli-xmp", **options)
                    stage1.assert_not_called()
                    stage2.assert_not_called()
                else:
                    stage1.assert_called_once_with(client, "cli-json", **options)
                    direct.assert_not_called()
                    if mode:
                        stage2.assert_not_called()
                    else:
                        stage2.assert_called_once_with("interim.json", "cli-xmp", "assets-with-faces")
                session.close.assert_called_once_with()

    def test_failed_stages_return_nonzero_in_every_processing_mode(self):
        cases = (
            ([], None, True),
            (["--stage1-only"], None, True),
            ([], "interim.json", False),
            (["--direct-xmp"], "interim.json", False),
            (["--stage2-only", "--json-file", "interim.json", "--xmp-dir", "xmp"], "interim.json", False),
        )
        for arguments, json_result, xmp_result in cases:
            with (
                self.subTest(arguments=arguments, json_result=json_result),
                patch.object(sys, "argv", ["export_xmp.py", *arguments]),
                patch.object(exporter, "setup_logging"),
                patch.object(exporter, "ConfigLoader", return_value=config_stub()),
                patch.object(exporter, "create_http_session"),
                patch.object(exporter, "export_assets_to_json", return_value=json_result),
                patch.object(exporter, "export_assets_to_xmp_from_json", return_value=xmp_result),
                patch.object(exporter, "export_assets_to_xmp", return_value=xmp_result),
                self.assertLogs(exporter.logger, level="INFO") as messages,
            ):
                self.assertEqual(1, exporter.main())
            self.assertFalse(any("successfully" in message for message in messages.output))


class CompatibilityCommandLineTests(unittest.TestCase):
    # run_path executes the real __main__ entry point in a separate interpreter.
    # The transport guard is also installed in that child, where parent mocks do
    # not apply; a swallowed attempted request still fails at guard teardown.
    CHILD_ENTRY = """
import runpy
import sys
from unittest.mock import patch
script, *arguments = sys.argv[1:]
sys.argv = [script, *arguments]
with patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected child HTTP request')) as request:
    try:
        runpy.run_path(script, run_name='__main__')
    finally:
        request.assert_not_called()
"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        # Deliberately no usable credentials. Never read the user's config.json.
        (self.root / "config.json").write_text("{}", encoding="utf-8")
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.upper().startswith(("IMMICH_", "OUTPUT_", "PYTHONPATH", "PYTHONSTARTUP"))}
        self.environment["PYTHONUTF8"] = "1"

    def run_entrypoint(self, *arguments):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", self.CHILD_ENTRY, str(Path(exporter.__file__).resolve()), *arguments],
            cwd=self.root, env=self.environment, capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertNotIn("Unexpected child HTTP request", result.stderr)
        self.assertNotIn("Expected 'request' to not have been called", result.stderr)
        return result

    def test_cli_defaults_remain_unchanged(self):
        with patch.object(sys, "argv", ["export_xmp.py"]):
            self.assertEqual({
                "stage1_only": False, "stage2_only": False, "direct_xmp": False,
                "export": "all-assets", "json_file": None, "json_dir": None,
                "xmp_dir": None, "max_assets": None, "album_id": None,
                "library_id": None, "debug": False,
            }, vars(exporter.parse_arguments()))

    def test_nonpositive_limits_are_accepted_by_parser(self):
        for value in ("0", "-1"):
            with self.subTest(value=value), patch.object(sys, "argv", ["export_xmp.py", "--max-assets", value]):
                self.assertEqual(int(value), exporter.parse_arguments().max_assets)

    def test_argument_errors_exit_two_in_real_entrypoint(self):
        for arguments in (("--stage2-only",), ("--export", "invalid"), ("--direct-xmp", "--stage1-only")):
            with self.subTest(arguments=arguments):
                result = self.run_entrypoint(*arguments)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn("usage:", result.stderr)
                self.assertFalse(list(self.root.rglob("*.xmp")))

    def test_invalid_configuration_exits_nonzero_without_network(self):
        result = self.run_entrypoint()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("Configuration error:", result.stderr)
        self.assertEqual(["config.json"], [path.name for path in self.root.iterdir()])

    def test_handled_stage2_failures_exit_nonzero_in_real_entrypoint(self):
        cases = (("missing", None), ("invalid", "{invalid"), ("empty", '{"assets": []}'),
                 ("wrong-root", "[]"), ("missing-assets", "{}"), ("wrong-assets", '{"assets": {}}'),
                 ("wrong-asset", '{"assets": [null]}'), ("wrong-people", '{"assets": [{"people": [null]}]}'),
                 ("unwritable-regions", json.dumps({"assets": [{"asset_id": "zero-area", "width": 100, "height": 100,
                     "people": [{"name": "Ada", "faces": [{"boundingBoxX1": 0, "boundingBoxY1": 0,
                         "boundingBoxX2": 0, "boundingBoxY2": 10}]}]}]})))
        for name, content in cases:
            with self.subTest(case=name):
                source = self.root / (name + ".json")
                if content is not None:
                    source.write_text(content, encoding="utf-8")
                output = self.root / name
                result = self.run_entrypoint(
                    "--stage2-only", "--json-file", str(source), "--xmp-dir", str(output), "--export", "assets-with-faces",
                )
                self.assertEqual(1, result.returncode, result.stderr)
                self.assertIn("Failed to generate XMP files from JSON", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(list(output.rglob("*.xmp")))

    def test_partial_sidecar_failure_exits_nonzero_and_keeps_successful_file(self):
        source = self.root / "source.json"
        source.write_text(json.dumps({"assets": [
            {"original_path": "/blocked.jpg", "people": []},
            {"original_path": "/written.jpg", "people": []},
        ]}), encoding="utf-8")
        output = self.root / "xmp"
        (output / "blocked.jpg.xmp").mkdir(parents=True)
        result = self.run_entrypoint("--stage2-only", "--json-file", str(source), "--xmp-dir", str(output))
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertNotIn("completed!", result.stderr)
        self.assertNotIn("successfully", result.stderr)
        self.assertTrue((output / "written.jpg.xmp").is_file())
        summary = json.loads((output / "export_summary.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", summary["export_status"])
        self.assertEqual(1, summary["total_xmp_files_created"])

    def test_path_collision_exits_nonzero_without_overwriting_first_asset(self):
        source = self.root / "source.json"
        source.write_text(json.dumps({"assets": [
            {"original_path": "/same.jpg", "file_name": "first.jpg", "people": []},
            {"original_path": "same.jpg", "file_name": "last.jpg", "people": []},
        ]}), encoding="utf-8")
        output = self.root / "xmp"
        result = self.run_entrypoint("--stage2-only", "--json-file", str(source), "--xmp-dir", str(output))
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("Path collision", result.stderr)
        content = (output / "same.jpg.xmp").read_text(encoding="utf-8")
        self.assertIn("<xmp:Identifier>first.jpg</xmp:Identifier>", content)
        self.assertNotIn("last.jpg", content)

    def test_unhandled_output_directory_error_exits_nonzero(self):
        source = self.root / "source.json"
        source.write_text('{"assets": [{"asset_id": "photo", "people": []}]}', encoding="utf-8")
        output = self.root / "not-a-directory"
        output.write_bytes(b"existing file")
        result = self.run_entrypoint("--stage2-only", "--json-file", str(source), "--xmp-dir", str(output))
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("FileExistsError", result.stderr)
        self.assertEqual(b"existing file", output.read_bytes())

    def test_offline_stage2_entrypoint_ignores_api_selection_options(self):
        source = self.root / "source.json"
        source.write_text('{"assets": [{"asset_id": "one", "people": []}, {"asset_id": "two", "people": []}]}',
                          encoding="utf-8")
        output = self.root / "xmp"
        result = self.run_entrypoint(
            "--stage2-only", "--json-file", str(source), "--xmp-dir", str(output),
            "--max-assets", "1", "--album-id", "ignored-album", "--library-id", "ignored-library",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Configuration error:", result.stderr)
        self.assertEqual({"unknown_one.jpg.xmp", "unknown_two.jpg.xmp"}, {path.name for path in output.rglob("*.xmp")})
        summary = json.loads((output / "export_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(2, summary["total_assets"])

    def test_stage2_uses_configured_output_without_validating_immich_credentials(self):
        source = self.root / "source.json"
        source.write_text('{"assets": [{"asset_id": "photo", "people": []}]}', encoding="utf-8")
        (self.root / "config.json").write_text(json.dumps({"output": {"xmp_export_dir": "from-config"}}), encoding="utf-8")
        result = self.run_entrypoint("--stage2-only", "--json-file", str(source))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Configuration error:", result.stderr)
        self.assertTrue((self.root / "from-config/unknown_photo.jpg.xmp").is_file())


class ClientBoundaryTests(unittest.TestCase):
    def test_explicit_settings_do_not_reread_configuration(self):
        config = config_stub()
        client = make_client(config)
        config.get_immich_config.assert_called_once_with()
        config.get_settings_config.assert_called_once_with()
        config.get_immich_config.side_effect = AssertionError("Configuration reread during HTTP work")
        config.get_settings_config.side_effect = AssertionError("Settings reread during HTTP work")
        with patch.object(exporter, "ConfigLoader", side_effect=AssertionError("Hidden configuration dependency")):
            client.request("GET", "/faces", params={"id": "photo"})
        client.session.request.assert_called_once_with(
            "GET", "https://example.invalid/api/faces", params={"id": "photo"}, timeout=12,
            headers={"Content-Type": "application/json", "Accept": "application/json", "x-api-key": "test-key"},
        )
        self.assertNotIn("test-key", repr(client.settings))

    def test_client_closes_owned_sessions_but_not_borrowed_sessions(self):
        for owned in (True, False):
            for fail in (True, False):
                with self.subTest(owned=owned, fail=fail):
                    session = Mock(spec=exporter.requests.Session)
                    with patch.object(exporter, "create_http_session", return_value=session) as create:
                        client = exporter.ImmichClient(
                            exporter.ApiSettings("https://example.invalid"),
                            session=None if owned else session,
                        )
                        try:
                            with client as entered:
                                self.assertIs(client, entered)
                                if fail:
                                    raise ValueError("body failed")
                        except ValueError as error:
                            self.assertTrue(fail)
                            self.assertEqual("body failed", str(error))
                        else:
                            self.assertFalse(fail, "Client suppressed an exception")
                    self.assertEqual(int(owned), create.call_count)
                    self.assertEqual(int(owned), session.close.call_count)

    def test_main_closes_session_after_unhandled_export_failure(self):
        session = Mock(spec=exporter.requests.Session)
        with (
            patch.object(sys, "argv", ["export_xmp.py", "--stage1-only"]),
            patch.object(exporter, "setup_logging"),
            patch.object(exporter, "ConfigLoader", return_value=config_stub()),
            patch.object(exporter, "create_http_session", return_value=session),
            patch.object(exporter, "export_assets_to_json", side_effect=FileExistsError("output is a file")),
            self.assertRaisesRegex(FileExistsError, "output is a file"),
        ):
            exporter.main()
        session.close.assert_called_once_with()

    def test_worker_startup_failure_closes_sessions_already_created(self):
        client = make_client()
        session = Mock(spec=exporter.requests.Session)
        with (
            patch.object(exporter, "Queue", TimedQueue),
            patch.object(exporter, "create_http_session", side_effect=[session, RuntimeError("startup failed")]),
            patch.object(client, "get_asset_people") as fetch,
            self.assertRaisesRegex(RuntimeError, "startup failed"),
        ):
            client._page_people([{"id": "first"}, {"id": "second"}])
        fetch.assert_not_called()
        session.close.assert_called_once_with()

    def test_worker_sessions_close_even_when_results_are_never_consumed(self):
        client = make_client()
        sessions = [Mock(spec=exporter.requests.Session) for _ in range(2)]
        with (
            patch.object(exporter, "Queue", TimedQueue),
            patch.object(exporter, "create_http_session", side_effect=sessions),
            patch.object(client, "get_asset_people", side_effect=exporter.requests.exceptions.Timeout("unconsumed")) as fetch,
        ):
            results = client._page_people([{"id": "first"}, {"id": "second"}])
        self.assertEqual(2, fetch.call_count)
        for session in sessions:
            session.close.assert_called_once_with()
        # Failure belongs to consumption, not to fetch completion or cleanup.
        with self.assertRaisesRegex(exporter.requests.exceptions.Timeout, "unconsumed"):
            next(results)

    def test_login_token_is_used_for_metadata_and_face_worker_requests(self):
        calls = []
        lock = threading.Lock()

        def respond(method, url, **kwargs):
            with lock:
                calls.append((method, url, kwargs))
            if url.endswith("/auth/login"):
                data = {"accessToken": "login-token"}
            elif url.endswith("/search/metadata"):
                data = {"assets": {"items": [{"id": "photo"}], "nextPage": None}}
            elif url.endswith("/faces"):
                data = []
            else:
                raise AssertionError(f"Unexpected URL: {url}")
            response = exporter.requests.Response()
            response.status_code = 200
            response._content = json.dumps(data).encode("utf-8")
            response.encoding = "utf-8"
            return response

        with (
            patch.object(exporter.requests.Session, "request", side_effect=respond),
            patch.object(exporter, "Queue", TimedQueue),
            exporter.ImmichClient(exporter.ApiSettings("https://example.invalid", retry_attempts=0)) as client,
        ):
            self.assertEqual("login-token", client.authenticate("email", "password"))
            records = list(exporter.process_assets(client))
        self.assertEqual(["photo"], [record["asset_id"] for record in records])
        self.assertEqual(["/auth/login", "/search/metadata", "/faces"],
                         [url.removeprefix("https://example.invalid/api") for _, url, _ in calls])
        self.assertEqual({"email": "email", "password": "password"}, calls[0][2]["json"])
        self.assertNotIn("Cookie", calls[0][2]["headers"])
        for _, _, kwargs in calls[1:]:
            self.assertEqual("immich_access_token=login-token", kwargs["headers"]["Cookie"])
            self.assertNotIn("x-api-key", kwargs["headers"])


if __name__ == "__main__":
    unittest.main()
