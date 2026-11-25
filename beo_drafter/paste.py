"""Deterministic paste/email overlay for the HTTP demo. No NLP, no network."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from beo_drafter.schema import validate_brief

MAX_PASTE_BYTES = 64 * 1024
OVERRIDE_MIN = 0
OVERRIDE_MAX = 10000

# Labeled lines the parser accepts. Anything else is ignored.
_LINE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("event_name", re.compile(r"^(?:event(?: name)?|subject)\s*:\s*(.+)$", re.I)),
    ("date", re.compile(r"^date\s*:\s*(\d{4}-\d{2}-\d{2})\s*$", re.I)),
    ("guarantee", re.compile(r"^guarantee\s*:\s*(.*)$", re.I)),
    ("expected", re.compile(r"^expected\s*:\s*(.*)$", re.I)),
    ("vegan", re.compile(r"^vegan(?: guests| count)?\s*:\s*(.*)$", re.I)),
    ("client", re.compile(r"^client\s*:\s*(.+)$", re.I)),
    ("venue", re.compile(r"^venue\s*:\s*(.+)$", re.I)),
)


def bounded_override(name: str, value: Any, lo: int = OVERRIDE_MIN, hi: int = OVERRIDE_MAX) -> int:
    """Parse guarantee or vegan as an integer in [lo, hi], or raise a clean error."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be an integer from {lo} to {hi}")
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        if not re.fullmatch(r"[+-]?\d+", text):
            raise ValueError(f"{name} must be an integer from {lo} to {hi}")
        number = int(text)
    if number < lo or number > hi:
        raise ValueError(f"{name} must be an integer from {lo} to {hi}")
    return number


def apply_headcount_overrides(
    brief: dict[str, Any],
    *,
    guarantee: Any = None,
    vegan: Any = None,
) -> dict[str, Any]:
    """Set guarantee and/or a vegan dietary count on a copy of the brief."""
    brief = copy.deepcopy(brief)
    headcount = dict(brief.get("headcount") or {})
    if guarantee not in (None, ""):
        headcount["guarantee"] = bounded_override("guarantee", guarantee)
        brief["headcount"] = headcount
    if vegan not in (None, ""):
        count = bounded_override("vegan", vegan)
        dietary = list(brief.get("dietary") or [])
        updated = False
        for index, item in enumerate(dietary):
            if isinstance(item, dict) and str(item.get("need") or "").lower() == "vegan":
                dietary[index] = dict(item, count=count)
                updated = True
                break
        if not updated:
            dietary.append({"need": "vegan", "count": count, "note": "added for this draft"})
        brief["dietary"] = dietary
    return brief


def parse_pasted_input(text: str, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse a JSON brief, or labeled email lines overlaid on an optional base brief.

    Supported email/text lines (one per line, case-insensitive):
    Event|Event name|Subject, Date (YYYY-MM-DD), Guarantee, Expected,
    Vegan|Vegan guests|Vegan count, Client, Venue.
    Other lines are ignored. JSON that starts with '{' is parsed as a full brief.
    A pasted email still overlays a selected fixture; it does not build a brief alone.
    """
    raw = text or ""
    if len(raw.encode("utf-8")) > MAX_PASTE_BYTES:
        raise ValueError(f"pasted text must be at most {MAX_PASTE_BYTES} bytes")
    raw = raw.strip()
    if not raw and base is not None:
        return validate_brief(copy.deepcopy(base))
    if not raw:
        raise ValueError("provide a fixture name, a brief JSON object, or a short event email")
    if raw.startswith("{"):
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("pasted JSON must be an object")
        return validate_brief(data)
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip().lstrip(">").strip()
        if not stripped:
            continue
        for name, pattern in _LINE_PATTERNS:
            match = pattern.match(stripped)
            if match:
                fields[name] = match.group(1).strip()
                break
    if not fields:
        raise ValueError(
            "could not parse pasted text; use brief JSON or labeled lines "
            "(Event, Date, Guarantee, Expected, Vegan, Client, Venue)"
        )
    if base is None:
        raise ValueError("select a fixture to overlay a pasted event email onto")
    brief = copy.deepcopy(base)
    if "event_name" in fields:
        brief["event_name"] = fields["event_name"]
    if "date" in fields:
        brief["date"] = fields["date"]
    if "client" in fields:
        client = dict(brief.get("client") or {})
        client["organization"] = fields["client"]
        brief["client"] = client
    if "venue" in fields:
        venue = dict(brief.get("venue") or {})
        venue["name"] = fields["venue"]
        brief["venue"] = venue
    if "expected" in fields:
        headcount = dict(brief.get("headcount") or {})
        headcount["expected"] = bounded_override("expected", fields["expected"])
        brief["headcount"] = headcount
    brief = apply_headcount_overrides(
        brief,
        guarantee=fields.get("guarantee"),
        vegan=fields.get("vegan"),
    )
    return validate_brief(brief)
