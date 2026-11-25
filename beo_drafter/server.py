"""Tiny stdlib HTTP demo for the BEO drafter.

Binds 127.0.0.1 by default. A Sprite (or any external listener) must pass
`--bind 0.0.0.0` or set BEO_BIND. Mock is the only HTTP client unless
DEMO_ALLOW_OLLAMA=1. The --approve path and receipt writes are not offered over HTTP.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from beo_drafter.cli import public_error_message
from beo_drafter.clients import get_client
from beo_drafter.drafter import draft
from beo_drafter.paste import apply_headcount_overrides, parse_pasted_input
from beo_drafter.render import render_html, render_markdown
from beo_drafter.schema import validate_brief

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
# Fixed directory for any server-side files. Client-supplied out/receipts paths
# are ignored. The HTTP demo does not approve or write receipts.
DATA_DIR = ROOT / "data"
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8080
DEMO_ALLOW_OLLAMA_ENV = "DEMO_ALLOW_OLLAMA"
# A fixture brief is a few KiB; 64 KiB leaves room for a pasted brief.
MAX_BODY_BYTES = 64 * 1024
# Seconds a connection may sit idle mid-request before the handler drops it.
REQUEST_TIMEOUT_SECONDS = 15
# Wall-clock budget for one request, so a slow trickle cannot hold a thread.
REQUEST_DEADLINE_SECONDS = 15
# In-flight requests (all methods) before the server answers 503.
MAX_IN_FLIGHT_REQUESTS = 8
_IN_FLIGHT = threading.BoundedSemaphore(MAX_IN_FLIGHT_REQUESTS)
SECURITY_HEADERS = (
    (
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
)


def fixture_names() -> list[str]:
    return sorted(path.stem for path in FIXTURES.glob("*.json"))


def load_fixture(name: str) -> dict[str, Any]:
    stem = Path(name).stem
    path = FIXTURES / f"{stem}.json"
    if not path.is_file() or path.resolve().parent != FIXTURES.resolve():
        raise ValueError(f"unknown fixture {name!r}")
    return validate_brief(json.loads(path.read_text(encoding="utf-8")))


def ollama_allowed_over_http(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(DEMO_ALLOW_OLLAMA_ENV) == "1"


def resolve_bind(cli_host: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    """Default 127.0.0.1. An all-interfaces bind must be an explicit flag or BEO_BIND."""
    env = os.environ if environ is None else environ
    if cli_host:
        return cli_host
    return (env.get("BEO_BIND") or "").strip() or DEFAULT_BIND


def resolve_http_client(payload: dict[str, Any], environ: Mapping[str, str] | None = None) -> str:
    name = str(payload.get("client") or "mock").strip().lower()
    if name == "mock":
        return "mock"
    if name == "ollama":
        if not ollama_allowed_over_http(environ):
            raise ValueError(
                "ollama is not available over HTTP unless DEMO_ALLOW_OLLAMA=1"
            )
        return "ollama"
    raise ValueError("client must be mock or ollama")


def draft_from_request(payload: dict[str, Any]) -> dict[str, Any]:
    # Ignore approve, out, and receipts. HTTP never approves or writes files
    # to a caller-chosen path; any later write would use DATA_DIR only.
    _ = payload.get("approve")
    _ = payload.get("out")
    _ = payload.get("receipts")
    client_name = resolve_http_client(payload)
    pasted = payload.get("brief") or payload.get("paste") or payload.get("email")
    fixture_name = payload.get("fixture")
    if isinstance(pasted, dict):
        brief = validate_brief(pasted)
    elif isinstance(pasted, str) and pasted.strip():
        base = load_fixture(str(fixture_name)) if fixture_name else None
        try:
            brief = parse_pasted_input(pasted, base=base)
        except json.JSONDecodeError as exc:
            raise ValueError("pasted JSON is invalid") from exc
    elif fixture_name:
        brief = load_fixture(str(fixture_name))
    else:
        raise ValueError("provide a fixture name, a brief JSON object, or a short event email")
    guarantee = payload.get("guarantee")
    vegan = payload.get("vegan")
    brief = apply_headcount_overrides(
        brief,
        guarantee=None if guarantee in (None, "") else guarantee,
        vegan=None if vegan in (None, "") else vegan,
    )
    beo = draft(brief, client=get_client(client_name))
    return {
        "brief": brief,
        "beo": beo,
        "markdown": render_markdown(beo),
        "html": render_html(beo),
    }


def _form_page(message: str = "", markdown: str = "", fixture: str = "wedding", html_result: str = "") -> bytes:
    options = "\n".join(
        f'<option value="{html.escape(name)}"{" selected" if name == fixture else ""}>{html.escape(name)}</option>'
        for name in fixture_names()
    )
    client_options = '<option value="mock" selected>mock (offline)</option>'
    if ollama_allowed_over_http():
        client_options += (
            '\n        <option value="ollama">ollama (Sprites connector)</option>'
        )
    alert = f"<p class='msg'>{html.escape(message)}</p>" if message else ""
    # render_markdown already escapes model text. Re-escaping here turned
    # "Kim & Alvarez" into "Kim &amp;amp; Alvarez" in the page source.
    result = f"<pre>{markdown}</pre>" if markdown else ""
    trace = html_result if html_result else ""
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Synthetic BEO drafter</title>
  <style>
    body {{ font-family: sans-serif; max-width: 52rem; margin: 2rem auto; }}
    textarea {{ width: 100%; height: 12rem; }}
    pre {{ white-space: pre-wrap; background: #f6f4ef; padding: 1rem; }}
    .msg {{ color: #8a1f1f; }}
    label {{ display: block; margin-top: 0.75rem; }}
    .guess {{ color: #b00020; }}
    .beo-trace {{ background: #f6f4ef; padding: 1rem; }}
    .beo-trace ul {{ margin: 0.25rem 0 0.75rem 1.25rem; }}
    .row {{ display: flex; gap: 1rem; }}
    .row label {{ flex: 1; }}
  </style>
</head>
<body>
  <h1>Synthetic BEO drafter</h1>
  <p>Fictional briefs only. The default client is the offline mock. The --approve path and receipts are not offered over HTTP.</p>
  <p>Paste a brief JSON object or a short event email (labeled lines: Event, Date, Guarantee, Expected, Vegan, Client, Venue) over the selected fixture. Guesses (computed staffing, defaults) are shown in <span class="guess">red</span>; hover a value for its source sentence. To try the demo loop, draft the wedding fixture, then set guarantee to 180 and add a vegan count.</p>
  {alert}
  <form method="post" action="/draft">
    <label>Fixture
      <select name="fixture">{options}</select>
    </label>
    <label>Or paste a brief JSON object or a short event email
      <textarea name="brief" placeholder="Leave blank to use the selected fixture"></textarea>
    </label>
    <div class="row">
      <label>Guarantee override (optional, e.g. 180)
        <input type="text" name="guarantee" inputmode="numeric" autocomplete="off">
      </label>
      <label>Vegan count to set (optional)
        <input type="text" name="vegan" inputmode="numeric" autocomplete="off">
      </label>
    </div>
    <label>Client
      <select name="client">
        {client_options}
      </select>
    </label>
    <p><button type="submit">Draft BEO</button></p>
  </form>
  {trace}
  {result}
</body>
</html>
"""
    return page.encode("utf-8")


def _body_length(header: str | None) -> tuple[int | None, int, str]:
    """Return (length, error_status, error_message) for a POST Content-Length."""
    if header is None or not header.strip():
        # Missing, blank, or omitted: 400 and do not read the body (avoids a hang).
        return None, 400, "Content-Length is required"
    try:
        length = int(header.strip())
    except ValueError:
        return None, 400, "Content-Length must be an integer"
    if length < 0:
        return None, 400, "Content-Length must not be negative"
    if length > MAX_BODY_BYTES:
        return None, 413, f"request body is larger than {MAX_BODY_BYTES} bytes"
    return length, 0, ""


def _header_policy_error(headers) -> str | None:
    """Reject Transfer-Encoding plus Content-Length, and duplicate Content-Length."""
    transfer = headers.get("Transfer-Encoding")
    lengths = headers.get_all("Content-Length") or []
    if transfer and transfer.strip() and lengths:
        return "Transfer-Encoding and Content-Length cannot both be set"
    if len(lengths) > 1:
        return "duplicate Content-Length headers are not allowed"
    return None


class _DeadlineReader:
    """Wrap rfile so every byte is capped by the remaining request deadline.

    A single raw read(n) can wait for n bytes using only the idle socket
    timeout, which is how a 2s trickle held a thread past the wall clock
    budget. Reading one byte at a time lets _arm_deadline run between recvs.
    """

    def __init__(self, raw, handler: "DrafterHandler") -> None:
        self._raw = raw
        self._handler = handler

    def _read_byte(self) -> bytes:
        self._handler._arm_deadline()
        return self._raw.read(1)

    def read(self, size: int = -1) -> bytes:
        chunks: list[bytes] = []
        if size == 0:
            return b""
        got = 0
        while size < 0 or got < size:
            buf = self._read_byte()
            if not buf:
                break
            chunks.append(buf)
            got += 1
        return b"".join(chunks)

    def readline(self, size: int = -1) -> bytes:
        chunks: list[bytes] = []
        while size < 0 or len(chunks) < size:
            buf = self._read_byte()
            if not buf:
                break
            chunks.append(buf)
            if buf == b"\n":
                break
        return b"".join(chunks)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class DrafterHandler(BaseHTTPRequestHandler):
    # Socket timeout for every read, so a slow or stalled client cannot hold a thread.
    timeout = REQUEST_TIMEOUT_SECONDS
    # Separate from the idle timeout: a client that keeps sending slowly is still cut off.
    request_deadline = REQUEST_DEADLINE_SECONDS

    def setup(self) -> None:
        super().setup()
        self._request_started = time.monotonic()
        self.rfile = _DeadlineReader(self.rfile, self)

    def handle_one_request(self) -> None:
        self._request_started = time.monotonic()
        acquired = _IN_FLIGHT.acquire(blocking=False)
        try:
            try:
                self._arm_deadline()
            except TimeoutError as exc:
                self.log_error("Request timed out: %r", exc)
                self.close_connection = True
                return
            if not acquired:
                self.close_connection = True
                self.request_version = "HTTP/1.1"
                self.requestline = getattr(self, "requestline", "") or ""
                self._send(
                    503,
                    b'{"error": "too many concurrent requests"}\n',
                    "application/json",
                )
                return
            super().handle_one_request()
        finally:
            if acquired:
                _IN_FLIGHT.release()

    def _arm_deadline(self) -> None:
        started = getattr(self, "_request_started", time.monotonic())
        remaining = self.request_deadline - (time.monotonic() - started)
        if remaining <= 0:
            self.close_connection = True
            raise TimeoutError("request deadline exceeded")
        idle = self.timeout if self.timeout else remaining
        self.connection.settimeout(min(float(idle), remaining))

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        problem = _header_policy_error(self.headers)
        if problem:
            self.close_connection = True
            self._send(400, json.dumps({"error": problem}).encode("utf-8") + b"\n", "application/json")
            return False
        return True

    def log_message(self, format: str, *args: Any) -> None:
        # Access log only; never print request bodies (they could include briefs, not keys).
        super().log_message(format, *args)

    def version_string(self) -> str:
        return ""

    def send_response(self, code: int, message: str | None = None) -> None:
        """Send status and security headers, without a Server version banner."""
        self.log_request(code)
        self.send_response_only(code, message)
        self.send_header("Date", self.date_time_string())
        for key, value in SECURITY_HEADERS:
            self.send_header(key, value)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send(200, _form_page(), "text/html; charset=utf-8")
            return
        if path == "/health":
            self._send(200, b'{"ok": true}\n', "application/json")
            return
        if path == "/fixtures":
            body = json.dumps({"fixtures": fixture_names()}).encode("utf-8") + b"\n"
            self._send(200, body, "application/json")
            return
        self._send(404, b'{"error": "not found"}\n', "application/json")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/draft":
            self._send(404, b'{"error": "not found"}\n', "application/json")
            return
        content_type = self.headers.get("Content-Type", "")
        length, status, problem = _body_length(self.headers.get("Content-Length"))
        if length is None:
            # Never read an unbounded or oversized body; drop the connection instead.
            self.close_connection = True
            self._send(status, json.dumps({"error": problem}).encode("utf-8") + b"\n", "application/json")
            return
        raw = self.rfile.read(length) if length else b""
        try:
            if "application/json" in content_type:
                payload = json.loads(raw.decode("utf-8") or "{}")
                want_html = False
            else:
                form = {k: v[-1] for k, v in parse_qs(raw.decode("utf-8")).items()}
                payload = form
                want_html = True
            result = draft_from_request(payload)
        except Exception as exc:
            # Keep pre-scrubbed validation text; anything else is generic
            # so paths or internals never reach the caller.
            message = public_error_message(exc)
            if "application/json" in content_type:
                self._send(400, json.dumps({"error": message}).encode("utf-8") + b"\n", "application/json")
            else:
                self._send(400, _form_page(message=message), "text/html; charset=utf-8")
            return
        if want_html:
            fixture = str(payload.get("fixture") or "wedding")
            self._send(
                200,
                _form_page(
                    markdown=result["markdown"],
                    fixture=fixture,
                    html_result=result.get("html") or "",
                ),
                "text/html; charset=utf-8",
            )
            return
        body = json.dumps({"beo": result["beo"], "markdown": result["markdown"]}, indent=2).encode("utf-8") + b"\n"
        self._send(200, body, "application/json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HTTP demo for the synthetic BEO drafter.")
    parser.add_argument(
        "--bind",
        default=None,
        help="Bind address. Default is 127.0.0.1. Pass 0.0.0.0 (or set BEO_BIND) so a Sprite URL can reach port 8080.",
    )
    parser.add_argument("--port", type=int, default=None, help="Listen port (default 8080, or PORT).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    host = resolve_bind(args.bind)
    port = args.port if args.port is not None else int(os.environ.get("PORT") or DEFAULT_PORT)
    server = ThreadingHTTPServer((host, port), DrafterHandler)
    print(f"BEO drafter listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping", flush=True)
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
