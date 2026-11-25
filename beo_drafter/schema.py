"""Brief and BEO shapes. Keep these local and synthetic; do not import private product schemas."""

from __future__ import annotations

from typing import Any

from beo_drafter import REQUIRED_SECTIONS

_DICT_SECTIONS = ("overview", "headcount", "menu", "room_setup", "av", "billing")


def _require(data: dict[str, Any], *keys: str, label: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise ValueError(f"{label} missing field(s): {', '.join(missing)}")


def validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(brief, dict):
        raise ValueError("brief must be a JSON object")
    _require(
        brief,
        "id",
        "event_type",
        "event_name",
        "client",
        "venue",
        "date",
        "timing",
        "headcount",
        "menu",
        "dietary",
        "room_setup",
        "av",
        "billing",
        label="brief",
    )
    _require(brief["client"], "organization", "contact_name", label="brief.client")
    _require(brief["venue"], "name", "room", label="brief.venue")
    _require(
        brief["timing"],
        "load_in",
        "guest_arrival",
        "start",
        "end",
        "load_out",
        label="brief.timing",
    )
    _require(brief["headcount"], "guarantee", "expected", label="brief.headcount")
    _require(brief["menu"], "style", "courses", label="brief.menu")
    if not isinstance(brief["dietary"], list):
        raise ValueError("brief.dietary must be a list")
    return brief


def validate_beo(beo: dict[str, Any], *, force_draft: bool = False) -> dict[str, Any]:
    """Validate BEO shape. With force_draft=True, overview.status is always draft.

    The --approve / receipt path stays on the CLI and runs after this
    and then calls validate_beo without force_draft so the banner can be
    **APPROVED**.
    """
    if not isinstance(beo, dict):
        raise ValueError("BEO must be a JSON object")
    _require(beo, *REQUIRED_SECTIONS, label="BEO")
    for key in _DICT_SECTIONS:
        if not isinstance(beo[key], dict):
            raise ValueError(f"BEO.{key} must be an object")
    if not isinstance(beo["timeline"], list):
        raise ValueError("BEO.timeline must be a list")
    if not isinstance(beo["dietary"], list):
        raise ValueError("BEO.dietary must be a list")
    if not isinstance(beo["notes"], str):
        raise ValueError("BEO.notes must be a string")
    if force_draft:
        beo["overview"]["status"] = "draft"
    return beo
