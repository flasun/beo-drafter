"""Deterministic offline drafter. No network, no model calls."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from beo_drafter.checks import conflict_flags
from beo_drafter.provenance import STANDARD_SIGNOFF, attach_mock_provenance


def service_ratio(style: str = "", event_type: str = "") -> int:
    """Servers per guests: cocktail/buffet/family/brunch 1:20, plated 1:16."""
    text = f"{style} {event_type}".lower()
    if "cocktail" in text:
        return 20
    if "plated" in text:
        return 16
    if any(word in text for word in ("buffet", "stations", "family-style", "brunch", "boxed")):
        return 20
    if "passed" in text:
        return 12
    return 16


def is_mixed_buffet_and_plated(style: str = "", event_type: str = "") -> bool:
    text = f"{style} {event_type}".lower()
    has_plated = "plated" in text
    has_buffet = any(word in text for word in ("buffet", "stations", "family-style", "brunch", "boxed"))
    return has_plated and has_buffet


def mixed_plated_label(style: str = "", event_type: str = "") -> str:
    text = f"{style} {event_type}".lower()
    if "dinner" in text:
        return "dinner"
    if "lunch" in text:
        return "lunch"
    return "plated"


def _bar_intent_text(bar: dict[str, Any] | None) -> str:
    """Style, inclusions, and signature only — not free-text notes."""
    if not bar:
        return ""
    return " ".join(
        str(bar.get(key) or "")
        for key in ("style", "inclusions", "signature_drink")
    ).lower()



_SPIRIT_WORD = r"bourbon|whiskey|whisky|tequila|vodka|gin|rum|liquor"


def _affirmed_named_spirit(text: str) -> bool:
    """True when a spirit word appears and is not under no/without/<spirit>-free."""
    for match in re.finditer(rf"\b({_SPIRIT_WORD})\b(?!-?free)", text):
        prefix = text[: match.start()]
        if re.search(r"(?:^|[\s,;/])(?:no|without)\s+$", prefix):
            continue
        return True
    return False


def bartender_ratio(bar: dict[str, Any] | None) -> int:
    """1:50 with spirits or a signature cocktail, 1:75 beer and wine only, else 1:60.

    Any spirits bar is 1:50: bourbon, whiskey/whisky, tequila, vodka, gin, rum,
    and a plain or full liquor bar. Only beer and wine stays 1:75. A spirit-free
    (or spirits-free) phrasing is a negation and applies to all of those spirit
    words. "no <spirit>" and "without <spirit>" also leave that spirit uncounted,
    so "no vodka" or "beer and wine, no vodka" is not 1:50. Bar-type phrases
    (full/hosted/cash/open bar) are read from style, inclusions, and
    signature_drink only, not from notes.
    """
    if not bar:
        return 60
    text = _bar_intent_text(bar)
    has_signature = bool(bar.get("signature_drink")) or any(
        phrase in text
        for phrase in ("signature cocktail", "signature drink", "signature pour")
    )
    negated_spirits = bool(
        re.search(
            r"spirit-?free|spirits-?free|\bno spirits\b|spirits not included|"
            r"not include spirits|no .*spirits",
            text,
        )
    )
    has_spirits = bool(re.search(r"\bspirits?\b(?!-?free)", text))
    has_named_spirit = _affirmed_named_spirit(text)
    has_spirits_style = any(
        phrase in text for phrase in ("full bar", "hosted bar", "cash bar", "open bar")
    )
    if has_signature or (
        (has_spirits or has_named_spirit or has_spirits_style) and not negated_spirits
    ):
        return 50
    if re.search(r"beer and wine|beer, wine", text):
        return 75
    return 60


def bartender_count(guarantee: int, bar: dict[str, Any] | None) -> int:
    """Bartenders come from the bar plan, not a headcount threshold."""
    if not bar:
        return 0
    raw = bar.get("bartenders")
    if raw not in (None, ""):
        return max(0, int(raw))
    if re.search(r"\bno bar\b", _bar_intent_text(bar)):
        return 0
    ratio = bartender_ratio(bar)
    return max(1, (guarantee + ratio - 1) // ratio)


def has_carving_station(style: str = "", menu: dict[str, Any] | None = None) -> bool:
    parts = [style]
    if isinstance(menu, dict):
        parts.append(str(menu.get("style") or ""))
        parts.append(str(menu.get("service_notes") or ""))
        for course in menu.get("courses") or []:
            if isinstance(course, dict):
                parts.append(str(course.get("course") or ""))
                items = course.get("items") or course.get("dishes") or []
                if isinstance(items, list):
                    parts.extend(str(item) for item in items)
                else:
                    parts.append(str(items))
            else:
                parts.append(str(course))
    return "carv" in " ".join(parts).lower()


def derive_guarantee_due(event_date: str, policy: str) -> str:
    """Noon, N hours or days before the event date, from the guarantee policy phrase."""
    text = (policy or "").lower()
    base = date.fromisoformat(str(event_date))
    match = re.search(r"(\d+)\s*-\s*hour", text)
    if match:
        days = int(match.group(1)) // 24
        return f"{(base - timedelta(days=days)).isoformat()} 12:00"
    match = re.search(r"(\d+)\s*-\s*day", text)
    if match:
        days = int(match.group(1))
        return f"{(base - timedelta(days=days)).isoformat()} 12:00"
    return ""


def resolve_guarantee_due(brief: dict[str, Any]) -> str:
    """Explicit billing.guarantee_due wins; else derive from package start or event date.

    A packaged brief must include package_start (or package_start_date) or an
    explicit billing.guarantee_due. A package_start after this brief's event
    date is rejected (this brief's date is the earliest date the drafter has).
    """
    billing = brief.get("billing") if isinstance(brief.get("billing"), dict) else {}
    explicit = billing.get("guarantee_due") or brief.get("guarantee_due")
    if isinstance(explicit, str):
        explicit = explicit.strip()
    start_raw = brief.get("package_start") or brief.get("package_start_date")
    start = str(start_raw).strip() if start_raw not in (None, "") else ""
    event_date = str(brief.get("date") or "").strip()
    if start and event_date:
        if date.fromisoformat(start) > date.fromisoformat(event_date):
            raise ValueError(
                f"package_start {start} is after the event date {event_date}"
            )
    if explicit not in (None, ""):
        return str(explicit)
    if brief.get("package_id"):
        if not start:
            raise ValueError(
                "package_id requires package_start or an explicit billing.guarantee_due"
            )
        base = start
    else:
        base = event_date
    if not base:
        return ""
    return derive_guarantee_due(base, str(billing.get("guarantee_policy") or ""))


def compute_staffing(
    headcount: dict[str, Any],
    style: str = "",
    event_type: str = "",
    bar: dict[str, Any] | None = None,
    menu: dict[str, Any] | None = None,
) -> dict[str, Any]:
    guarantee = int(headcount["guarantee"])
    captains = 1 if guarantee < 80 else 2
    bartenders = bartender_count(guarantee, bar)
    carving = 1 if has_carving_station(style, menu) else 0
    mixed = is_mixed_buffet_and_plated(style, event_type)
    if mixed:
        daytime_ratio = 20
        plated_ratio = 16
        daytime_servers = max(1, (guarantee + daytime_ratio - 1) // daytime_ratio)
        plated_servers = max(1, (guarantee + plated_ratio - 1) // plated_ratio)
        servers = max(daytime_servers, plated_servers)
        ratio = plated_ratio
        plated_name = mixed_plated_label(style, event_type)
        server_parts = [
            f"daytime (buffet) {daytime_servers} server(s) at a 1:{daytime_ratio} planning ratio",
            f"{plated_name} (plated) {plated_servers} server(s) at a 1:{plated_ratio} planning ratio",
        ]
        meal_servers = daytime_servers + plated_servers
    else:
        ratio = service_ratio(style, event_type)
        servers = max(1, (guarantee + ratio - 1) // ratio)
        daytime_servers = servers
        plated_servers = 0
        server_parts = [f"{servers} server(s) at a 1:{ratio} planning ratio"]
        meal_servers = servers
    computed_meals = meal_servers + captains + bartenders + carving + 2
    if "staff_meals" not in headcount or headcount["staff_meals"] in (None, ""):
        staff_meals = computed_meals
        meal_note = ""
    else:
        staff_meals = int(headcount["staff_meals"])
        meal_note = ""
        if staff_meals != computed_meals:
            meal_note = (
                f" Staff-meal count is taken from the brief ({staff_meals}); "
                f"computed floor-team meals are {computed_meals}."
            )
    parts = [f"{captains} captain(s)", *server_parts]
    if bartenders:
        parts.append(f"{bartenders} bartender(s) from the bar plan")
    else:
        parts.append("no bartenders (no bar plan)")
    if carving:
        parts.append(f"{carving} carving attendant")
    parts.append(f"{staff_meals} staff meals")
    return {
        "servers": servers,
        "captains": captains,
        "bartenders": bartenders,
        "carving_attendants": carving,
        "staff_meals": staff_meals,
        "ratio": ratio,
        "split": mixed,
        "daytime_servers": daytime_servers if mixed else 0,
        "plated_servers": plated_servers,
        "staffing_plan": (
            "Plan " + ", ".join(parts) + ". Adjust on the floor if the guarantee changes."
            + meal_note
        ),
    }


def _dietary_is_severe(item: Any) -> bool:
    if isinstance(item, str):
        text = item
    elif isinstance(item, dict):
        text = " ".join(str(item.get(k, "")) for k in ("need", "name", "note"))
    else:
        return False
    return "severe" in text.lower()


def _format_dietary_item(item: Any) -> str | None:
    if isinstance(item, str):
        line = item
    elif isinstance(item, dict):
        count = item.get("count", "")
        need = item.get("need") or item.get("name") or "unspecified need"
        note = item.get("note")
        prefix = f"{count} " if count != "" else ""
        line = f"{prefix}{need}".strip()
        if note:
            line = f"{line} ({note})"
    else:
        return None
    if _dietary_is_severe(item) and not line.upper().startswith("**SEVERE ALLERGY**"):
        line = f"**SEVERE ALLERGY** {line}"
    return line


def _dietary_lines(dietary: list[Any]) -> list[str]:
    severe = [item for item in dietary if _dietary_is_severe(item)]
    rest = [item for item in dietary if not _dietary_is_severe(item)]
    lines: list[str] = []
    for item in severe + rest:
        line = _format_dietary_item(item)
        if line:
            lines.append(line)
    return lines or ["None listed in the brief; confirm with the client 72 hours out."]


def _course_lines(courses: Any) -> list[str]:
    lines: list[str] = []
    for course in courses or []:
        if isinstance(course, str):
            lines.append(course)
            continue
        if not isinstance(course, dict):
            continue
        title = course.get("course") or course.get("name") or "Course"
        items = course.get("items") or course.get("dishes") or []
        if isinstance(items, list):
            dish = ", ".join(str(x) for x in items)
        else:
            dish = str(items)
        lines.append(f"{title}: {dish}" if dish else str(title))
    return lines


def normalize_clock(value: Any) -> str:
    """HH:MM, ignoring trailing spaces and an optional :SS."""
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return text


def _minutes(value: Any) -> int:
    parts = str(value).split(":")
    return int(parts[0]) * 60 + int(parts[1])


def timeline_sort_key(load_in: Any, row: dict[str, Any]) -> tuple[int, str]:
    """Sort by minutes after load-in; times earlier than load-in wrap to the next day."""
    origin = _minutes(load_in)
    offset = _minutes(row["time"]) - origin
    if offset < 0:
        offset += 24 * 60
    return (offset, str(row["item"]))


def _day_offset(load_in: Any, clock: Any) -> int:
    """0 if the clock is at or after load-in, else the next calendar day."""
    return 0 if _minutes(clock) >= _minutes(load_in) else 1


def _copy_room_function(source: dict[str, Any], dest: dict[str, Any]) -> dict[str, Any]:
    for key in ("function", "room"):
        value = source.get(key)
        if value not in (None, ""):
            dest[key] = str(value)
    return dest


def stamp_timeline_dates(timeline: list[dict[str, Any]], event_date: str, load_in: Any) -> list[dict[str, Any]]:
    base = date.fromisoformat(str(event_date))
    stamped: list[dict[str, Any]] = []
    for row in timeline:
        offset = _day_offset(load_in, row["time"])
        stamped.append(
            _copy_room_function(
                row,
                {
                    "time": str(row["time"]),
                    "item": str(row["item"]),
                    "day_offset": offset,
                    "date": (base + timedelta(days=offset)).isoformat(),
                },
            )
        )
    return stamped


def build_timeline(timing: dict[str, Any], event_date: str) -> list[dict[str, Any]]:
    extra = timing.get("milestones") or []
    extra_rows = [
        row
        for row in extra
        if isinstance(extra, list) and isinstance(row, dict) and "time" in row and "item" in row
    ]
    end_time = normalize_clock(timing["end"])
    has_end_milestone = any(normalize_clock(row["time"]) == end_time for row in extra_rows)
    timeline = [
        {"time": timing["load_in"], "item": str(timing.get("load_in_item") or "Load-in and room set")},
        {"time": timing["guest_arrival"], "item": "Guest arrival"},
        {"time": timing["start"], "item": "Event start"},
    ]
    if not has_end_milestone:
        timeline.append({"time": timing["end"], "item": "Event end"})
    timeline.append(
        {"time": timing["load_out"], "item": str(timing.get("load_out_item") or "Load-out")}
    )
    for row in extra_rows:
        timeline.append(
            _copy_room_function(row, {"time": str(row["time"]), "item": str(row["item"])})
        )
    load_in = timing["load_in"]
    timeline.sort(key=lambda row: timeline_sort_key(load_in, row))
    return stamp_timeline_dates(timeline, event_date, load_in)


class MockModelClient:
    name = "mock"

    def draft_beo(self, brief: dict[str, Any]) -> dict[str, Any]:
        client = brief["client"]
        venue = brief["venue"]
        timing = brief["timing"]
        headcount = brief["headcount"]
        menu = brief["menu"]
        room = brief["room_setup"]
        av = brief["av"]
        billing = brief["billing"]
        menu_bar = menu.get("bar") if isinstance(menu.get("bar"), dict) else None
        staffing = compute_staffing(
            headcount,
            style=str(menu.get("style") or ""),
            event_type=str(brief.get("event_type") or ""),
            bar=menu_bar,
            menu=menu if isinstance(menu, dict) else None,
        )

        overview = {
            "beo_id": f"BEO-{brief['id']}",
            "event_name": brief["event_name"],
            "event_type": brief["event_type"],
            "date": brief["date"],
            "client_organization": client["organization"],
            "client_contact": client["contact_name"],
            "client_role": client.get("contact_role", ""),
            "venue_name": venue["name"],
            "venue_room": venue["room"],
            "venue_address": venue.get("address", ""),
            "status": "draft",
        }
        if brief.get("package_id"):
            overview["package_id"] = str(brief["package_id"])
        menu_out = {
            "style": menu["style"],
            "courses": _course_lines(menu.get("courses")),
            "service_notes": menu.get("service_notes", ""),
        }
        if menu_bar:
            menu_out["bar"] = dict(menu_bar)
        due = resolve_guarantee_due(brief)
        billing_out = {
            "charge_to": billing.get("charge_to", ""),
            "guarantee_policy": billing.get("guarantee_policy", ""),
        }
        if due:
            billing_out["guarantee_due"] = due
        billing_out["tax_or_service"] = billing.get("tax_or_service", "")
        billing_out["notes"] = billing.get("notes", "")
        notes = str(brief.get("notes") or "")
        if "[CLIENT SIGNATURE]" not in notes:
            notes = (notes.rstrip() + "\n\n" + STANDARD_SIGNOFF) if notes.strip() else STANDARD_SIGNOFF
        beo = {
            "overview": overview,
            "timeline": build_timeline(timing, brief["date"]),
            "headcount": {
                "guarantee": headcount["guarantee"],
                "expected": headcount["expected"],
                "staff_meals": staffing["staff_meals"],
                "staffing_plan": staffing["staffing_plan"],
                "conflicts": conflict_flags(brief),
            },
            "menu": menu_out,
            "dietary": _dietary_lines(brief["dietary"]),
            "room_setup": {
                "style": room.get("style", ""),
                "tables": room.get("tables", ""),
                "seating": room.get("seating", ""),
                "other": room.get("other", ""),
                "seat_capacity": room.get("seat_capacity", ""),
            },
            "av": {
                "items": list(av.get("items") or []),
                "notes": av.get("notes", ""),
            },
            "billing": billing_out,
            "notes": notes,
            "source_brief_id": brief["id"],
            "drafter": "mock",
        }
        return attach_mock_provenance(brief, beo)
