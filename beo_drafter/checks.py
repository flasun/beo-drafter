"""Seating and overage checks. Flag conflicts; do not invent prices or seats."""

from __future__ import annotations

import re
from typing import Any


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def seat_capacity(room: dict[str, Any]) -> int | None:
    explicit = _int_or_none(room.get("seat_capacity"))
    if explicit is not None:
        return explicit
    text = str(room.get("tables") or "")
    total = 0
    found = False
    for match in re.finditer(r"(\d+)\s+rounds?\s+of\s+(\d+)", text, re.I):
        total += int(match.group(1)) * int(match.group(2))
        found = True
    for match in re.finditer(r"(\d+)\s+tables?\s+of\s+(\d+)", text, re.I):
        total += int(match.group(1)) * int(match.group(2))
        found = True
    for match in re.finditer(r"sweetheart table", text, re.I):
        total += 2
        found = True
    for match in re.finditer(r"(?:winners'|reserved)\s+table of (\d+)", text, re.I):
        total += int(match.group(1))
        found = True
    return total if found else None


def overage_cap_percent(billing: dict[str, Any]) -> int | None:
    explicit = _int_or_none(billing.get("overage_cap_percent"))
    if explicit is not None:
        return explicit
    text = str(billing.get("guarantee_policy") or "")
    match = re.search(r"(\d+)\s*%\s*overage cap", text, re.I)
    if match:
        return int(match.group(1))
    return None


def conflict_flags(brief: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    expected = int(brief["headcount"]["expected"])
    guarantee = int(brief["headcount"]["guarantee"])
    capacity = seat_capacity(brief.get("room_setup") or {})
    if capacity is not None and expected > capacity:
        flags.append(
            f"Expected {expected} guests exceed seat capacity of {capacity}."
        )
    cap_percent = overage_cap_percent(brief.get("billing") or {})
    if cap_percent is not None:
        cap = guarantee * cap_percent // 100
        overage = expected - guarantee
        if overage > cap:
            flags.append(
                f"Expected {expected} guests exceed the {cap_percent}% overage cap "
                f"(guarantee {guarantee}, cap {cap}, expected exceeds guarantee by {overage})."
            )
    return flags
