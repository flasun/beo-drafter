import io
import json
import os
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beo_drafter.clients.mock import MockModelClient
from beo_drafter.clients import ollama as ollama_module
from beo_drafter.clients.ollama import DEFAULT_GATEWAY_BASE, MAX_RESPONSE_BYTES, OllamaCloudClient
from beo_drafter.drafter import draft, load_brief
from beo_drafter.schema import validate_beo


class _FakeResponse:
    def __init__(self, payload: dict | None = None, raw: bytes | None = None):
        self._raw = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._raw if size < 0 else self._raw[:size]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class OllamaClientTests(unittest.TestCase):
    def test_reads_env_and_does_not_expose_key_in_repr_or_str(self):
        previous = {
            "OLLAMA_HOST": os.environ.get("OLLAMA_HOST"),
            "OLLAMA_API_KEY": os.environ.get("OLLAMA_API_KEY"),
            "OLLAMA_MODEL": os.environ.get("OLLAMA_MODEL"),
            "OLLAMA_CONNECTION_ID": os.environ.get("OLLAMA_CONNECTION_ID"),
        }
        try:
            os.environ["OLLAMA_HOST"] = ""
            os.environ["OLLAMA_API_KEY"] = "replace-me-for-test"
            os.environ["OLLAMA_MODEL"] = "replace-me"
            os.environ["OLLAMA_CONNECTION_ID"] = "test-connection"
            client = OllamaCloudClient()
            self.assertEqual(client.api_key, "replace-me-for-test")
            self.assertEqual(client.model, "replace-me")
            self.assertEqual(client.connection_id, "test-connection")
            for rendered in (repr(client), str(client)):
                self.assertIn("api_key_set=True", rendered)
                self.assertIn("connection_id_set=True", rendered)
                self.assertNotIn("replace-me-for-test", rendered)
                self.assertNotIn("test-connection", rendered)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_draft_beo_uses_injected_transport_and_sends_no_key(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        expected = draft(brief, client=MockModelClient())
        expected["drafter"] = "ollama"
        captured: dict = {}

        def fake_urlopen(request: Request, timeout: int = 60) -> _FakeResponse:
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["has_authorization"] = request.has_header("Authorization")
            captured["data"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse({"message": {"content": json.dumps(expected)}})

        client = OllamaCloudClient(
            connection_id="test-connection",
            model="replace-me",
            api_key="replace-me-for-test",
            urlopen=fake_urlopen,
        )
        beo = client.draft_beo(brief)
        self.assertEqual(beo["drafter"], "ollama")
        self.assertEqual(
            captured["url"],
            f"{DEFAULT_GATEWAY_BASE}/test-connection/api/chat",
        )
        self.assertFalse(captured["has_authorization"])
        self.assertEqual(captured["data"]["stream"], False)
        self.assertEqual(captured["data"]["model"], "replace-me")
        self.assertNotIn("replace-me-for-test", json.dumps(captured["data"]))
        self.assertNotIn("Authorization", json.dumps(captured))

    def test_http_error_does_not_include_key(self):
        def fake_urlopen(request: Request, timeout: int = 60):
            raise HTTPError(
                request.full_url,
                403,
                "forbidden",
                {},
                io.BytesIO(b"secret-should-not-leak"),
            )

        client = OllamaCloudClient(
            connection_id="test-connection",
            model="replace-me",
            api_key="replace-me-for-test",
            urlopen=fake_urlopen,
        )
        with self.assertRaises(RuntimeError) as raised:
            client.draft_beo({"id": "unused"})
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertNotIn("replace-me-for-test", str(raised.exception))

    def test_no_third_party_http_libraries(self):
        source = Path(__import__("beo_drafter.clients.ollama", fromlist=["OllamaCloudClient"]).__file__).read_text(
            encoding="utf-8"
        )
        self.assertIn("import urllib.request", source)
        for name in ("requests", "httpx"):
            self.assertNotIn(f"import {name}", source)
            self.assertNotIn(f"from {name}", source)


    def _model_beo(self, brief, **overrides):
        expected = draft(brief, client=MockModelClient())
        expected["drafter"] = "ollama"
        expected["overview"]["status"] = "approved"
        expected.update(overrides)
        return expected

    def test_model_output_is_forced_to_draft(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        payload = self._model_beo(brief)
        self.assertEqual(payload["overview"]["status"], "approved")

        def fake_urlopen(request: Request, timeout: int = 60) -> _FakeResponse:
            return _FakeResponse({"message": {"content": json.dumps(payload)}})

        client = OllamaCloudClient(
            connection_id="test-connection",
            model="replace-me",
            api_key="replace-me-for-test",
            urlopen=fake_urlopen,
        )
        beo = client.draft_beo(brief)
        self.assertEqual(beo["overview"]["status"], "draft")
        self.assertEqual(beo["drafter"], "ollama")

    def test_model_payload_missing_section_fails_if_validate_beo_skipped(self):
        """This test fails if draft_beo returns the payload without validate_beo."""
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        payload = self._model_beo(brief)
        del payload["notes"]
        del payload["billing"]

        def fake_urlopen(request: Request, timeout: int = 60) -> _FakeResponse:
            return _FakeResponse({"message": {"content": json.dumps(payload)}})

        client = OllamaCloudClient(
            connection_id="test-connection",
            model="replace-me",
            api_key="replace-me-for-test",
            urlopen=fake_urlopen,
        )
        with self.assertRaises(ValueError) as raised:
            client.draft_beo(brief)
        message = str(raised.exception)
        self.assertIn("missing field", message)
        self.assertTrue("notes" in message or "billing" in message)

    def test_validate_beo_rejects_wrong_section_types(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        beo = draft(brief, client=MockModelClient())
        beo["timeline"] = "not a list"
        with self.assertRaisesRegex(ValueError, "timeline must be a list"):
            validate_beo(beo)
        beo = draft(brief, client=MockModelClient())
        beo["overview"] = ["not", "an", "object"]
        with self.assertRaisesRegex(ValueError, "overview must be an object"):
            validate_beo(beo)
        approved = draft(brief, client=MockModelClient())
        approved["overview"]["status"] = "approved"
        kept = validate_beo(approved)
        self.assertEqual(kept["overview"]["status"], "approved")
        forced = validate_beo(approved, force_draft=True)
        self.assertEqual(forced["overview"]["status"], "draft")


def _never_called(request, timeout=60):
    raise AssertionError("network must not be reached")


class GatewayUrlTests(unittest.TestCase):
    """MED-4: only https://api.sprites.dev/v1/gateway/ollama/<id>/api/chat is allowed."""

    def _client(self, **kwargs):
        kwargs.setdefault("model", "replace-me")
        kwargs.setdefault("urlopen", _never_called)
        kwargs.setdefault("host", "")
        kwargs.setdefault("gateway_base", DEFAULT_GATEWAY_BASE)
        return OllamaCloudClient(**kwargs)

    def test_plain_connection_id_is_accepted(self):
        for good in ("test-connection", "conn_01", "a.b-c_D9", "a..b", "conn.v1"):
            self.assertEqual(
                self._client(connection_id=good).chat_url(),
                f"{DEFAULT_GATEWAY_BASE}/{good}/api/chat",
            )

    def test_slashy_or_odd_connection_id_is_rejected(self):
        for bad in ("../other", "foo/../../evil", "id?x=1", "id#frag", "x y", "..", ".", "a/b", "id%2F..", ""):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                self._client(connection_id=bad).chat_url()

    def test_host_override_must_be_the_sprites_gateway(self):
        ok = f"{DEFAULT_GATEWAY_BASE}/test-connection/api/chat"
        self.assertEqual(self._client(host=ok).chat_url(), ok)
        for bad in (
            "http://evil.example/chat",
            "http://api.sprites.dev/v1/gateway/ollama/test-connection/api/chat",
            "https://evil.example/v1/gateway/ollama/test-connection/api/chat",
            "https://api.sprites.dev.evil.example/v1/gateway/ollama/x/api/chat",
            "https://user@api.sprites.dev/v1/gateway/ollama/x/api/chat",
            "https://api.sprites.dev:8443/v1/gateway/ollama/x/api/chat",
            "https://api.sprites.dev/v1/gateway/ollama/x/api/chat?redirect=1",
            "https://api.sprites.dev/v1/gateway/ollama/../../other/api/chat",
            "https://api.sprites.dev/v1/other/x/api/chat",
        ):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                self._client(host=bad).chat_url()

    def test_gateway_base_override_is_checked(self):
        with self.assertRaises(RuntimeError):
            self._client(connection_id="test-connection", gateway_base="http://evil.example").chat_url()

    def test_gateway_base_rejects_dotdot_segments(self):
        # Prefix match used to accept this and emit a traversal URL.
        bad = "https://api.sprites.dev/v1/gateway/ollama/../../v1/secret"
        with self.assertRaises(RuntimeError):
            self._client(connection_id="abc", gateway_base=bad).chat_url()
        with self.assertRaises(RuntimeError):
            self._client(
                connection_id="test-connection",
                gateway_base="https://api.sprites.dev/v1/gateway/ollama/foo",
            ).chat_url()
        with self.assertRaises(RuntimeError):
            self._client(
                connection_id="test-connection",
                gateway_base="https://api.sprites.dev/v1/gateway/ollama/.",
            ).chat_url()
        # Exact prefix, including a trailing slash that normalizes, is still ok.
        url = self._client(
            connection_id="test-connection",
            gateway_base="https://api.sprites.dev/v1/gateway/ollama/",
        ).chat_url()
        self.assertEqual(url, f"{DEFAULT_GATEWAY_BASE}/test-connection/api/chat")

    def test_bad_url_fails_before_any_network_call(self):
        client = self._client(connection_id="../other")
        with self.assertRaises(RuntimeError):
            client.draft_beo({"id": "unused"})

    def test_default_transport_refuses_redirects(self):
        client = OllamaCloudClient(connection_id="test-connection", model="replace-me", host="")
        self.assertIs(client._urlopen, ollama_module._urlopen_no_redirects)
        handler = ollama_module._RefuseRedirects()
        request = Request(f"{DEFAULT_GATEWAY_BASE}/test-connection/api/chat", data=b"{}", method="POST")
        with self.assertRaises(HTTPError) as raised:
            handler.redirect_request(request, io.BytesIO(b""), 302, "Found", {}, "https://evil.example/")
        self.assertEqual(raised.exception.code, 302)
        handlers = [type(h) for h in ollama_module._OPENER.handlers]
        self.assertIn(ollama_module._RefuseRedirects, handlers)

    def test_redirect_surfaces_as_http_error_without_following(self):
        def redirecting_urlopen(request, timeout=60):
            return ollama_module._RefuseRedirects().redirect_request(
                request, io.BytesIO(b""), 307, "Temporary Redirect", {}, "https://evil.example/"
            )

        client = self._client(connection_id="test-connection", urlopen=redirecting_urlopen)
        with self.assertRaises(RuntimeError) as raised:
            client.draft_beo({"id": "unused"})
        self.assertIn("HTTP 307", str(raised.exception))

    def test_oversized_response_is_rejected_and_read_is_capped(self):
        fake = _FakeResponse(raw=b"x" * (MAX_RESPONSE_BYTES + 10))
        client = self._client(connection_id="test-connection", urlopen=lambda request, timeout=60: fake)
        with self.assertRaises(ValueError):
            client.draft_beo({"id": "unused"})
        self.assertEqual(fake.read_sizes, [MAX_RESPONSE_BYTES + 1])

    def test_url_error_does_not_chain_the_gateway_url(self):
        def failing_urlopen(request, timeout=60):
            raise URLError("connection refused")

        client = self._client(connection_id="secret-connection-id", urlopen=failing_urlopen)
        with self.assertRaises(RuntimeError) as raised:
            client.draft_beo({"id": "unused"})
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertNotIn("secret-connection-id", str(raised.exception))

