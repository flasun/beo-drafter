"""Ollama Cloud client for the Sprites connector gateway.

The Sprite calls the gateway with a connection ID only. The gateway attaches
the stored key; this client never sends, logs, or returns an API key.
Tests inject a urlopen stand-in and must not reach the network.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from beo_drafter.schema import validate_beo

DEFAULT_GATEWAY_BASE = "https://api.sprites.dev/v1/gateway/ollama"
GATEWAY_HOST = "api.sprites.dev"
GATEWAY_PATH_PREFIX = "/v1/gateway/ollama"
# A connection ID is one plain path segment: no slashes, query, or fragment.
CONNECTION_ID_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")
# A BEO reply is a few KiB; cap the read well above that.
MAX_RESPONSE_BYTES = 1024 * 1024

SYSTEM_PROMPT = """You draft a Banquet Event Order from a synthetic event brief.
Reply with a single JSON object only, no markdown fences.

Types (required top-level keys):
- overview, headcount, menu, room_setup, av, billing: JSON objects
- timeline: a JSON array of row objects, each with "time" (string, HH:MM) and "item" (string). Optional: "date" (YYYY-MM-DD), "day_offset" (integer), "room" (string), "function" (string). Do not use an object that maps times to labels.
- dietary: a JSON array of strings
- notes: a JSON string (not an object or array)
- source_brief_id: string, drafter: string

Compact skeleton (fill with brief values; keep this shape):
{"overview":{"beo_id":"string","event_name":"string","event_type":"string","date":"YYYY-MM-DD","client_organization":"string","client_contact":"string","client_role":"string","venue_name":"string","venue_room":"string","venue_address":"string","status":"draft"},"timeline":[{"time":"18:00","item":"string","date":"YYYY-MM-DD","day_offset":0}],"headcount":{"guarantee":0,"expected":0,"staff_meals":0,"staffing_plan":"string","conflicts":[]},"menu":{"style":"string","courses":["string"],"service_notes":"string"},"dietary":["string"],"room_setup":{"style":"string","tables":"string","seating":"string","other":"string","seat_capacity":0},"av":{"items":["string"],"notes":"string"},"billing":{"charge_to":"string","guarantee_policy":"string","guarantee_due":"string","tax_or_service":"string","notes":"string"},"notes":"string","source_brief_id":"string","drafter":"ollama"}

Use only menu dishes that appear in the brief. Do not invent dishes.
Set overview.status to draft, drafter to ollama, and source_brief_id to the brief id.
"""


_TIMELINE_ROW_KEYS = ("time", "item", "date", "day_offset", "room", "function")


def _stringify_label(value: Any) -> str:
    """Return a string label, or raise if the value is not a scalar."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or value is None:
        raise ValueError("timeline item must be a string")
    if isinstance(value, (int, float)):
        return str(value)
    raise ValueError("timeline item must be a string")


def coerce_model_beo(beo: dict[str, Any]) -> dict[str, Any]:
    """Narrow, safe shape fixes for known live-model mistakes.

    Decision: if timeline is a JSON object mapping a clock time to a label
    (or to a row dict), convert it to a list of {time, item} rows. That was
    the live smoke failure (BEO.timeline must be a list). Coerced rows keep
    only the known timeline keys. Scalar non-string items are stringified;
    lists, objects, and booleans are rejected. Other wrong types are left
    for validate_beo to reject.
    """
    timeline = beo.get("timeline")
    if isinstance(timeline, dict):
        rows: list[dict[str, Any]] = []
        for key, value in timeline.items():
            if isinstance(value, dict):
                row = {k: value[k] for k in _TIMELINE_ROW_KEYS if k in value}
                row.setdefault("time", str(key))
                if "item" not in row:
                    row["item"] = _stringify_label(value.get("label") or value.get("name") or key)
                else:
                    row["item"] = _stringify_label(row["item"])
                if not isinstance(row["time"], str):
                    row["time"] = _stringify_label(row["time"])
                rows.append(row)
            else:
                rows.append({"time": str(key), "item": _stringify_label(value)})
        beo["timeline"] = rows
        log = list(beo.get("coercions") or [])
        log.append("timeline object of time-to-label entries became a list of {time, item} rows")
        beo["coercions"] = log
    return beo

UrlOpen = Callable[..., Any]


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Treat any 30x as an error so the brief is never re-sent to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(_RefuseRedirects())


def _urlopen_no_redirects(request: urllib.request.Request, timeout: int = 60) -> Any:
    return _OPENER.open(request, timeout=timeout)


def _check_gateway_url(url: str, *, full_chat_url: bool) -> str:
    """Allow only https://api.sprites.dev under the Ollama gateway prefix."""
    parts = urllib.parse.urlsplit(url)
    # Reject '.' / '..' as path segments before normalizing so a prefix match
    # cannot hide traversal in a GATEWAY_BASE override. Dots inside a
    # connection_id (a..b, conn.v1) are a single segment and stay allowed.
    raw_segments = [seg for seg in parts.path.split("/") if seg]
    if any(seg in {".", ".."} for seg in raw_segments):
        path_ok = False
    elif full_chat_url:
        segments = parts.path[len(GATEWAY_PATH_PREFIX) :].split("/")
        # Expect exactly ["", connection_id, "api", "chat"].
        path_ok = (
            posixpath.normpath(parts.path).startswith(GATEWAY_PATH_PREFIX)
            and len(segments) == 4
            and segments[0] == ""
            and bool(CONNECTION_ID_RE.match(segments[1]))
            and segments[2:] == ["api", "chat"]
        )
    else:
        path_ok = posixpath.normpath(parts.path) == GATEWAY_PATH_PREFIX
    if (
        parts.scheme != "https"
        or parts.hostname != GATEWAY_HOST
        or parts.port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not path_ok
    ):
        raise RuntimeError(
            "Ollama gateway URL must be https://api.sprites.dev"
            f"{GATEWAY_PATH_PREFIX}/<connection id>/api/chat"
        )
    return url


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("Ollama response was not a JSON object") from None
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Ollama response JSON must be an object")
    return data


def _message_content(payload: dict[str, Any]) -> str:
    message = payload.get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
    raise ValueError("Ollama response did not include message content")


class OllamaCloudClient:
    name = "ollama"

    def __init__(
        self,
        host: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        connection_id: str | None = None,
        gateway_base: str | None = None,
        urlopen: UrlOpen | None = None,
    ) -> None:
        self.host = host if host is not None else os.environ.get("OLLAMA_HOST", "")
        # Read only so tests can confirm it stays out of repr/str/requests.
        # Never sent on the connector path and must not be set on a Sprite.
        self.api_key = api_key if api_key is not None else os.environ.get("OLLAMA_API_KEY", "")
        self.model = model if model is not None else os.environ.get("OLLAMA_MODEL", "")
        self.connection_id = (
            connection_id
            if connection_id is not None
            else os.environ.get("OLLAMA_CONNECTION_ID", "")
        )
        self.gateway_base = (
            gateway_base
            if gateway_base is not None
            else os.environ.get("OLLAMA_GATEWAY_BASE", DEFAULT_GATEWAY_BASE)
        ).rstrip("/")
        self._urlopen = urlopen or _urlopen_no_redirects

    def __repr__(self) -> str:
        return (
            f"OllamaCloudClient(host={self.host!r}, "
            f"api_key_set={bool(self.api_key)}, "
            f"connection_id_set={bool(self.connection_id)}, "
            f"model={self.model!r})"
        )

    def __str__(self) -> str:
        return repr(self)

    def chat_url(self) -> str:
        if self.host:
            return _check_gateway_url(self.host.rstrip("/"), full_chat_url=True)
        if not self.connection_id:
            raise RuntimeError(
                "Set OLLAMA_CONNECTION_ID for the Sprites Ollama connector, "
                "or OLLAMA_HOST to the full gateway chat URL. The app does "
                "not send an API key; the gateway attaches it."
            )
        if not CONNECTION_ID_RE.match(self.connection_id):
            raise RuntimeError("OLLAMA_CONNECTION_ID must be letters, digits, '.', '_' or '-' only")
        base = _check_gateway_url(self.gateway_base, full_chat_url=False)
        return f"{base}/{self.connection_id}/api/chat"

    def _build_request(self, brief: dict[str, Any]) -> urllib.request.Request:
        if not self.model:
            raise RuntimeError("OLLAMA_MODEL is not set")
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(brief, sort_keys=True)},
            ],
        }
        return urllib.request.Request(
            self.chat_url(),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def draft_beo(self, brief: dict[str, Any]) -> dict[str, Any]:
        request = self._build_request(brief)
        # Intentionally no Authorization header. The Sprites gateway injects the key.
        try:
            with self._urlopen(request, timeout=60) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Ollama gateway HTTP {exc.code}") from None
        except urllib.error.URLError:
            # from None: the cause would carry the gateway URL and connection ID.
            raise RuntimeError("Ollama gateway request failed") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError(f"Ollama gateway response is larger than {MAX_RESPONSE_BYTES} bytes")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Ollama gateway returned non-JSON") from exc
        beo = coerce_model_beo(_extract_json_object(_message_content(payload)))
        if not beo.get("drafter"):
            beo["drafter"] = "ollama"
        if not beo.get("source_brief_id") and brief.get("id"):
            beo["source_brief_id"] = brief["id"]
        return validate_beo(beo, force_draft=True)
