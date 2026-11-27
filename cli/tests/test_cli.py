"""Executable CLI contracts against isolated files and an independent HTTP fixture."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import jwt


class CLIContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def respond(self):
                cls.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": self.rfile.read(int(self.headers.get("Content-Length", "0"))),
                    }
                )
                status, content, media_type = (
                    cls.responses.pop(0)
                    if cls.responses
                    else (500, b'{"detail":"unexpected request"}', "application/json")
                )
                cls.release_response.wait()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", media_type)
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A cancelled Job observation deliberately closes the response socket.

            do_GET = do_POST = respond

            def log_message(self, *args):
                pass

        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        type(self).requests = []
        type(self).responses = []
        type(self).release_response = threading.Event()
        self.release_response.set()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.connections_file = self.root / "connections.json"
        self.personal = {
            "base_url": self.url + "/personal",
            "jwt_secret": "personal-synthetic-secret-at-least-32-bytes",
        }
        self.work = {
            "base_url": self.url + "/work",
            "jwt_secret": "work-synthetic-secret-at-least-32-bytes",
        }
        self.connections_file.write_text(
            json.dumps({"default": "personal", "connections": {"personal": self.personal}}),
            encoding="utf-8",
        )

    def invoke(self, *arguments, input=None):
        env = {key: value for key, value in os.environ.items() if not key.startswith("INKCRE_CLI_")}
        env["NO_PROXY"] = "127.0.0.1,localhost"
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "inkcre_cli.main",
                "--connections-file",
                str(self.connections_file),
                "--json",
                *arguments,
            ],
            input=input,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
            check=False,
        )

    def json_response(self, value, status=200):
        self.responses.append((status, json.dumps(value).encode(), "application/json"))

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def test_connections_survive_new_processes_and_temporary_selection(self):
        self.connections_file.unlink()
        for name, connection in (("personal", self.personal), ("work", self.work)):
            self.success(
                self.invoke("connection", "set", name, "--input", "-", input=json.dumps(connection))
            )
        listed = self.success(self.invoke("connection", "list"))
        self.assertEqual(
            listed,
            {
                "default": "personal",
                "connections": {
                    "personal": {"base_url": self.url + "/personal"},
                    "work": {"base_url": self.url + "/work"},
                },
            },
        )
        self.assertEqual(self.success(self.invoke("connection", "get", "work")), self.work)
        self.success(self.invoke("connection", "use", "work"))
        for options, prefix, secret in (
            (("--connection", "personal"), "personal", self.personal["jwt_secret"]),
            ((), "work", self.work["jwt_secret"]),
        ):
            self.json_response({"id": prefix})
            self.assertEqual(self.success(self.invoke(*options, "peer", "get")), {"id": prefix})
            request = self.requests[-1]
            self.assertEqual(request["path"], f"/{prefix}/peers/self")
            claims = jwt.decode(
                request["authorization"].removeprefix("Bearer "),
                secret,
                algorithms=["HS256"],
                audience="inkcre-api",
                issuer="inkcre-peer",
            )
            self.assertEqual(claims["role"], "authenticated")
        self.assertEqual(self.success(self.invoke("connection", "list"))["default"], "work")
        self.success(self.invoke("connection", "delete", "work"))
        self.assertEqual(
            self.success(self.invoke("connection", "list")),
            {"default": None, "connections": {"personal": {"base_url": self.url + "/personal"}}},
        )

    def test_invalid_connection_reports_local_field_error_without_overwriting_saved_value(self):
        result = self.invoke(
            "connection",
            "set",
            "personal",
            "--input-json",
            '{"base_url":"not-a-url","jwt_secret":"x"}',
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        errors = json.loads(result.stderr)["detail"]
        self.assertEqual(
            [(item["loc"], item["type"]) for item in errors], [(["base_url"], "url_parsing")]
        )
        self.assertEqual(self.success(self.invoke("connection", "get", "personal")), self.personal)
        self.assertEqual(self.requests, [])

    def test_http_failure_and_failed_job_have_distinct_exit_contracts(self):
        self.json_response({"detail": "invalid credentials"}, status=401)
        rejected = self.invoke("job", "get", "17")
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(rejected.stdout, "")
        self.assertEqual(
            json.loads(rejected.stderr), {"detail": "invalid credentials", "http_status": 401}
        )
        record = {"id": 17, "status": "failed", "state": {"error": "source unavailable"}}
        self.json_response(record)
        self.assertEqual(self.success(self.invoke("job", "get", "17")), record)
        self.assertEqual([item["path"] for item in self.requests], ["/personal/jobs/17"] * 2)

    def test_partial_batch_keeps_full_success_and_errors_on_separate_stream(self):
        records = [
            {"type": "block", "id": 42, "content": "正文" * 5000},
            {
                "type": "relation",
                "id": 8,
                "error": {"code": "not_found", "message": "relation 8 not found"},
            },
        ]
        self.json_response(records)
        result = self.invoke("get", "block:42", "relation:8")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), records)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertIn("逐项错误", json.loads(result.stderr)["detail"])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["path"], "/personal/entities/get")
        self.assertEqual(
            json.loads(self.requests[0]["body"]),
            {
                "entities": [{"type": "block", "id": 42}, {"type": "relation", "id": 8}],
                "content": "raw",
            },
        )

    def test_job_wait_budget_cancels_inflight_read_without_aborting_job(self):
        self.json_response({"id": 17, "status": "running"})
        # Hold the HTTP response until the CLI exits, so only its total observation
        # budget can finish the command. The fixture uses no sleeps or timeout response.
        self.release_response.clear()
        try:
            result = self.invoke("job", "wait", "17", "--for", "0.5s")
        finally:
            self.release_response.set()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("尚未取得 Job 记录", json.loads(result.stderr)["detail"])
        self.assertEqual(
            [(request["method"], request["path"]) for request in self.requests],
            [("GET", "/personal/jobs/17")],
        )

    def test_binary_export_remains_readable_after_directory_move_without_refetch(self):
        # Handwritten protocol fixture, independent of Core's MIME producer and CLI decoder.
        mime = (
            b"--fixture\r\nContent-Type: application/json\r\n\r\n"
            b'{"value":{"a/b~c":[null],"ordinary":null,"text":"cid:blob"},'
            b'"parts":{"/a~1b~0c/0":"blob"}}\r\n'
            b"--fixture\r\nContent-Type: application/octet-stream\r\nContent-ID: <blob>\r\n\r\n"
            b"\x00\xffhello\r\n--fixture--\r\n"
        )
        for content, media_type, nested in (
            (mime, 'multipart/related; boundary="fixture"', True),
            (b"\x00\xffwhole", "application/octet-stream", False),
        ):
            with self.subTest(media_type=media_type):
                self.responses.append((200, content, media_type))
                result = self.invoke(
                    "resolver",
                    "invoke",
                    "block:42",
                    "--method",
                    "get_solved_content",
                    "--output-dir",
                    str(self.root / "exports"),
                )
                entry = Path(self.success(result)["file"])
                moved = self.root / ("nested" if nested else "raw")
                entry.parent.rename(moved)
                entry = moved / entry.name
                if nested:
                    saved = json.loads(entry.read_text(encoding="utf-8"))
                    self.assertIsNone(saved["ordinary"])
                    self.assertEqual(saved["text"], "cid:blob")
                    binary = Path(saved["a/b~c"][0]["file"])
                    self.assertFalse(binary.is_absolute())
                    self.assertEqual((entry.parent / binary).read_bytes(), b"\x00\xffhello")
                else:
                    self.assertEqual(entry.read_bytes(), b"\x00\xffwhole")
        self.assertEqual(len(self.requests), 2)
        for request in self.requests:
            self.assertEqual(request["method"], "POST")
            self.assertEqual(
                request["path"], "/personal/blocks/42/resolver/methods/get_solved_content"
            )
            self.assertEqual(json.loads(request["body"]), {})


if __name__ == "__main__":
    unittest.main()
