import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beo_drafter import server as server_module
from beo_drafter.server import (
    DATA_DIR,
    DEFAULT_BIND,
    DrafterHandler,
    MAX_BODY_BYTES,
    MAX_IN_FLIGHT_REQUESTS,
    REQUEST_DEADLINE_SECONDS,
    SECURITY_HEADERS,
    _form_page,
    build_parser,
    draft_from_request,
    fixture_names,
    ollama_allowed_over_http,
    resolve_bind,
    resolve_http_client,
)


class ServerUnitTests(unittest.TestCase):
    def test_fixture_names_match_disk(self):
        self.assertEqual(set(fixture_names()), {path.stem for path in (ROOT / "fixtures").glob("*.json")})

    def test_draft_from_fixture_uses_mock(self):
        result = draft_from_request({"fixture": "wedding", "client": "mock"})
        self.assertEqual(result["beo"]["overview"]["event_name"], "Kim & Alvarez Reception")
        self.assertEqual(result["beo"]["drafter"], "mock")
        self.assertEqual(result["beo"]["overview"]["status"], "draft")
        self.assertIn("**UNAPPROVED DRAFT**", result["markdown"])

    def test_draft_from_pasted_brief(self):
        brief = json.loads((ROOT / "fixtures" / "offsite-lunch.json").read_text(encoding="utf-8"))
        result = draft_from_request({"brief": brief, "client": "mock"})
        self.assertEqual(result["beo"]["source_brief_id"], brief["id"])

    def test_unknown_fixture_is_rejected(self):
        with self.assertRaises(ValueError):
            draft_from_request({"fixture": "../secrets", "client": "mock"})

    def test_bind_defaults_to_localhost(self):
        self.assertEqual(DEFAULT_BIND, "127.0.0.1")
        self.assertEqual(resolve_bind(environ={}), "127.0.0.1")
        self.assertEqual(resolve_bind(environ={"BEO_BIND": ""}), "127.0.0.1")
        self.assertEqual(resolve_bind(cli_host=None, environ={}), "127.0.0.1")

    def test_bind_0_0_0_0_requires_flag_or_env(self):
        self.assertEqual(resolve_bind(environ={"BEO_BIND": "0.0.0.0"}), "0.0.0.0")
        self.assertEqual(resolve_bind(cli_host="0.0.0.0", environ={}), "0.0.0.0")
        # Flag wins over a different env value.
        self.assertEqual(resolve_bind(cli_host="0.0.0.0", environ={"BEO_BIND": "127.0.0.1"}), "0.0.0.0")
        parser = build_parser()
        self.assertIsNone(parser.parse_args([]).bind)
        self.assertEqual(parser.parse_args(["--bind", "0.0.0.0"]).bind, "0.0.0.0")
        self.assertEqual(resolve_bind(parser.parse_args(["--bind", "0.0.0.0"]).bind, environ={}), "0.0.0.0")

    def test_ollama_is_rejected_over_http_by_default(self):
        previous = os.environ.pop("DEMO_ALLOW_OLLAMA", None)
        try:
            self.assertFalse(ollama_allowed_over_http({}))
            self.assertFalse(ollama_allowed_over_http({"DEMO_ALLOW_OLLAMA": "true"}))
            self.assertFalse(ollama_allowed_over_http({"DEMO_ALLOW_OLLAMA": "0"}))
            with self.assertRaisesRegex(ValueError, "DEMO_ALLOW_OLLAMA=1"):
                resolve_http_client({"client": "ollama"}, environ={})
            with self.assertRaisesRegex(ValueError, "DEMO_ALLOW_OLLAMA=1"):
                draft_from_request({"fixture": "wedding", "client": "ollama"})
            page = _form_page().decode("utf-8")
            self.assertIn('value="mock"', page)
            self.assertNotIn('value="ollama"', page)
        finally:
            if previous is None:
                os.environ.pop("DEMO_ALLOW_OLLAMA", None)
            else:
                os.environ["DEMO_ALLOW_OLLAMA"] = previous

    def test_ollama_over_http_requires_explicit_allow(self):
        previous = {
            "DEMO_ALLOW_OLLAMA": os.environ.get("DEMO_ALLOW_OLLAMA"),
            "OLLAMA_MODEL": os.environ.get("OLLAMA_MODEL"),
            "OLLAMA_CONNECTION_ID": os.environ.get("OLLAMA_CONNECTION_ID"),
            "OLLAMA_HOST": os.environ.get("OLLAMA_HOST"),
            "OLLAMA_API_KEY": os.environ.get("OLLAMA_API_KEY"),
        }
        try:
            os.environ["DEMO_ALLOW_OLLAMA"] = "1"
            for key in ("OLLAMA_MODEL", "OLLAMA_CONNECTION_ID", "OLLAMA_HOST", "OLLAMA_API_KEY"):
                os.environ.pop(key, None)
            self.assertTrue(ollama_allowed_over_http())
            self.assertEqual(resolve_http_client({"client": "ollama"}), "ollama")
            page = _form_page().decode("utf-8")
            self.assertIn('value="ollama"', page)
            self.assertIn('value="mock" selected', page)
            # Selectable, but missing model must fail before any network call.
            with self.assertRaisesRegex(RuntimeError, "OLLAMA_MODEL"):
                draft_from_request({"fixture": "wedding", "client": "ollama"})
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_html_page_escapes_ampersand_once(self):
        result = draft_from_request({"fixture": "wedding", "client": "mock"})
        page = _form_page(markdown=result["markdown"], fixture="wedding").decode("utf-8")
        self.assertIn("Kim &amp; Alvarez", page)
        self.assertNotIn("&amp;amp;", page)
        self.assertGreaterEqual(page.count("Kim &amp; Alvarez"), 1)

    def test_html_page_keeps_script_and_onerror_inert(self):
        brief = json.loads((ROOT / "fixtures" / "wedding.json").read_text(encoding="utf-8"))
        brief["event_name"] = "<script>alert(1)</script>"
        brief["notes"] = '<img src=x onerror=alert(1)>'
        result = draft_from_request({"brief": brief, "client": "mock"})
        page = _form_page(markdown=result["markdown"], fixture="wedding").decode("utf-8")
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)
        self.assertNotIn("&amp;lt;script", page)

    def test_http_ignores_client_paths_and_does_not_approve(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "caller-out"
            receipts = Path(tmp) / "caller-receipts.jsonl"
            result = draft_from_request(
                {
                    "fixture": "wedding",
                    "client": "mock",
                    "approve": "true",
                    "out": str(out),
                    "receipts": str(receipts),
                }
            )
            self.assertEqual(result["beo"]["overview"]["status"], "draft")
            self.assertIn("**UNAPPROVED DRAFT**", result["markdown"])
            self.assertNotIn("**APPROVED**", result["markdown"])
            self.assertFalse(out.exists())
            self.assertFalse(receipts.exists())
            self.assertFalse(Path(str(receipts) + ".anchor").exists())
            # Fixed data dir must not be created by an HTTP draft either.
            self.assertFalse((DATA_DIR / "wedding.json").exists())
            self.assertFalse((DATA_DIR / "synth-wedding-001.json").exists())


class ServerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DrafterHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _open(self, path, data=None, content_type="application/json"):
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method="GET" if data is None else "POST",
            headers={"Content-Type": content_type} if data is not None else {},
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_health_and_form_are_mock_only(self):
        with self._open("/health") as response:
            self.assertEqual(json.loads(response.read().decode("utf-8")), {"ok": True})
        with self._open("/") as response:
            body = response.read().decode("utf-8")
        self.assertIn('value="mock"', body)
        self.assertNotIn('value="ollama"', body)
        self.assertNotIn('name="approve"', body)
        self.assertNotIn("checkbox", body.lower())

    def test_html_form_post_escapes_ampersand_once(self):
        form = b"fixture=wedding&client=mock"
        with self._open("/draft", data=form, content_type="application/x-www-form-urlencoded") as response:
            page = response.read().decode("utf-8")
        self.assertIn("Kim &amp; Alvarez", page)
        self.assertNotIn("&amp;amp;", page)

    def test_post_ollama_is_rejected_and_approve_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "http-out"
            payload = {
                "fixture": "wedding",
                "client": "ollama",
                "approve": True,
                "out": str(out),
                "receipts": str(Path(tmp) / "r.jsonl"),
            }
            raw = json.dumps(payload).encode("utf-8")
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self._open("/draft", data=raw)
            self.assertEqual(raised.exception.code, 400)
            err = json.loads(raised.exception.read().decode("utf-8"))
            self.assertIn("DEMO_ALLOW_OLLAMA=1", err["error"])
            self.assertFalse(out.exists())

            ok_payload = json.dumps(
                {"fixture": "wedding", "client": "mock", "approve": True, "out": str(out)}
            ).encode("utf-8")
            with self._open("/draft", data=ok_payload) as response:
                body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(body["beo"]["overview"]["status"], "draft")
            self.assertIn("**UNAPPROVED DRAFT**", body["markdown"])
            self.assertFalse(out.exists())


    def test_responses_send_security_headers_and_no_server_banner(self):
        with self._open("/health") as response:
            headers = response.headers
            data = response.read()
        self.assertEqual(json.loads(data.decode("utf-8")), {"ok": True})
        expected = dict(SECURITY_HEADERS)
        self.assertEqual(headers.get("Content-Security-Policy"), expected["Content-Security-Policy"])
        self.assertIn("default-src 'none'", headers.get("Content-Security-Policy", ""))
        self.assertIn("style-src 'unsafe-inline'", headers.get("Content-Security-Policy", ""))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        server = headers.get("Server") or ""
        self.assertNotIn("Python", server)
        self.assertNotIn("BaseHTTP", server)

    def test_over_the_concurrency_cap_returns_503(self):
        release = threading.Event()
        entered = threading.Semaphore(0)
        original = server_module.draft_from_request

        def blocked(payload):
            entered.release()
            if not release.wait(timeout=10):
                raise RuntimeError("test blocker timed out")
            return original(payload)

        server_module.draft_from_request = blocked
        threads: list[threading.Thread] = []
        errors: list[BaseException] = []
        try:
            body = b'{"fixture": "wedding", "client": "mock"}'

            def hold():
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(
                            self.base + "/draft",
                            data=body,
                            method="POST",
                            headers={"Content-Type": "application/json"},
                        ),
                        timeout=15,
                    ).read()
                except Exception as exc:
                    errors.append(exc)

            for _ in range(MAX_IN_FLIGHT_REQUESTS):
                thread = threading.Thread(target=hold, daemon=True)
                threads.append(thread)
                thread.start()
            for _ in range(MAX_IN_FLIGHT_REQUESTS):
                self.assertTrue(entered.acquire(timeout=5))
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(
                    urllib.request.Request(
                        self.base + "/draft",
                        data=body,
                        method="POST",
                        headers={"Content-Type": "application/json"},
                    ),
                    timeout=5,
                )
            self.assertEqual(raised.exception.code, 503)
            payload = raised.exception.read()
            self.assertIn(b"too many concurrent requests", payload)
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=5)
            server_module.draft_from_request = original
        self.assertEqual(errors, [])

    def test_http_hides_path_bearing_errors_but_keeps_validation_text(self):
        original = server_module.draft_from_request

        def boom(payload):
            raise ValueError("cannot read out/../secret-brief.json")

        server_module.draft_from_request = boom
        try:
            raw = b'{"fixture": "wedding"}'
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self._open("/draft", data=raw)
            self.assertEqual(raised.exception.code, 400)
            err = json.loads(raised.exception.read().decode("utf-8"))
            self.assertEqual(err["error"], "could not draft this request")
            self.assertNotIn("secret-brief", err["error"])
        finally:
            server_module.draft_from_request = original

class _CountingHandler(DrafterHandler):
    """Records how many body bytes the handler pulled off the socket."""

    bytes_read = 0

    def setup(self):
        super().setup()
        raw_read = self.rfile.read

        def counting_read(size=-1):
            data = raw_read(size)
            type(self).bytes_read += len(data)
            return data

        self.rfile.read = counting_read


class RequestLimitTests(unittest.TestCase):
    """MED-3: body cap, required Content-Length, and a per-connection timeout."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        _CountingHandler.bytes_read = 0

    def _raw_post(self, headers: str, body: bytes = b"", timeout: float = 5.0) -> tuple[int, bytes]:
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as sock:
            sock.sendall(f"POST /draft HTTP/1.1\r\nHost: x\r\n{headers}\r\n".encode() + body)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        data = b"".join(chunks)
        status = int(data.split(b" ", 2)[1])
        return status, data

    def test_handler_has_a_timeout(self):
        self.assertIsNotNone(DrafterHandler.timeout)
        self.assertLessEqual(DrafterHandler.timeout, 15)
        self.assertEqual(DrafterHandler.timeout, server_module.REQUEST_TIMEOUT_SECONDS)
        self.assertGreater(DrafterHandler.request_deadline, 0)
        self.assertLessEqual(DrafterHandler.request_deadline, 15)
        self.assertEqual(DrafterHandler.request_deadline, REQUEST_DEADLINE_SECONDS)

    def test_oversized_body_is_rejected_without_buffering_it(self):
        declared = MAX_BODY_BYTES + 1
        started = time.monotonic()
        status, data = self._raw_post(
            f"Content-Type: application/json\r\nContent-Length: {declared}\r\n",
            b"{" * 1024,
        )
        self.assertEqual(status, 413)
        self.assertIn(b"larger than", data)
        self.assertEqual(_CountingHandler.bytes_read, 0)
        self.assertLess(time.monotonic() - started, 3)

    def test_huge_declared_length_is_rejected(self):
        status, _ = self._raw_post("Content-Type: application/json\r\nContent-Length: 10000000000\r\n")
        self.assertEqual(status, 413)

    def test_negative_length_is_rejected_not_hung(self):
        started = time.monotonic()
        status, _ = self._raw_post("Content-Type: application/json\r\nContent-Length: -1\r\n")
        self.assertEqual(status, 400)
        self.assertLess(time.monotonic() - started, 3)

    def test_non_integer_length_is_rejected(self):
        status, _ = self._raw_post("Content-Type: application/json\r\nContent-Length: abc\r\n")
        self.assertEqual(status, 400)

    def test_missing_length_is_rejected(self):
        status, _ = self._raw_post("Content-Type: application/json\r\n")
        self.assertEqual(status, 400)

    def test_body_at_the_cap_is_read(self):
        body = json.dumps({"fixture": "wedding", "client": "mock", "pad": "x" * 10}).encode()
        status, data = self._raw_post(
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n",
            body,
        )
        self.assertEqual(status, 200)
        self.assertIn(b"UNAPPROVED DRAFT", data)

    def test_unexpected_errors_get_a_generic_message(self):
        original = server_module.draft_from_request

        def boom(payload):
            raise RuntimeError("/secret/internal/path exploded")

        server_module.draft_from_request = boom
        try:
            body = b'{"fixture": "wedding"}'
            status, data = self._raw_post(
                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n",
                body,
            )
        finally:
            server_module.draft_from_request = original
        self.assertEqual(status, 400)
        self.assertIn(b"could not draft this request", data)
        self.assertNotIn(b"/secret/internal/path", data)

    def test_transfer_encoding_plus_length_is_rejected(self):
        started = time.monotonic()
        status, data = self._raw_post(
            "Transfer-Encoding: chunked\r\nContent-Type: application/json\r\nContent-Length: 20\r\n",
            b"x" * 20,
        )
        self.assertEqual(status, 400)
        self.assertIn(b"Transfer-Encoding", data)
        self.assertEqual(_CountingHandler.bytes_read, 0)
        self.assertLess(time.monotonic() - started, 3)

    def test_duplicate_content_length_is_rejected(self):
        started = time.monotonic()
        status, data = self._raw_post(
            "Content-Type: application/json\r\nContent-Length: 40\r\nContent-Length: 999999\r\n",
            b"x" * 40,
        )
        self.assertEqual(status, 400)
        self.assertIn(b"duplicate Content-Length", data)
        self.assertEqual(_CountingHandler.bytes_read, 0)
        self.assertLess(time.monotonic() - started, 3)

    def test_request_deadline_cuts_off_a_trickle(self):
        previous = DrafterHandler.request_deadline
        DrafterHandler.request_deadline = 1
        try:
            started = time.monotonic()
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                sock.sendall(
                    b"POST /draft HTTP/1.1\r\nHost: x\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 20\r\n\r\n"
                )
                try:
                    for _ in range(20):
                        sock.sendall(b"x")
                        time.sleep(0.4)
                    sock.recv(65536)
                except OSError:
                    pass
            self.assertLess(time.monotonic() - started, 3)
        finally:
            DrafterHandler.request_deadline = previous

