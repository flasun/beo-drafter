"""Attach and look up per-field provenance for the demo trace-to-sentence view."""

from __future__ import annotations

from typing import Any

BRIEF = "brief"
COMPUTED = "computed"
DEFAULT = "default"
GUESSES = frozenset({COMPUTED, DEFAULT})

STANDARD_SIGNOFF = (
    "Revision [REVISION]. Prepared by [PREPARED BY]. Printed [PRINTED DATE]. "
    "This BEO supersedes all previous versions. Please sign and return. "
    "Client: [CLIENT SIGNATURE]. Manager: [MANAGER SIGNATURE]."
)


def notes_segments(beo: dict[str, Any], notes: str | None = None) -> list[tuple[str, str]]:
    """Split notes into (path, text) so brief copy and added sign-off are traced apart."""
    text = notes if notes is not None else str(beo.get("notes") or "")
    copied = lookup(beo, "notes.copied")
    signoff = lookup(beo, "notes.signoff")
    if signoff and text.endswith(STANDARD_SIGNOFF):
        prefix = text[: -len(STANDARD_SIGNOFF)].rstrip()
        parts: list[tuple[str, str]] = []
        if prefix:
            parts.append(("notes.copied", prefix))
        parts.append(("notes.signoff", STANDARD_SIGNOFF))
        return parts
    if copied and not signoff:
        return [("notes.copied", text)]
    if signoff and not copied:
        return [("notes.signoff", text)]
    if text:
        return [("notes", text)]
    return []



def mark(
    provenance: dict[str, dict[str, str]],
    path: str,
    source: str,
    ref: str = "",
    text: str = "",
) -> None:
    entry: dict[str, str] = {"source": source}
    if ref:
        entry["ref"] = ref
    if text:
        entry["text"] = text
    provenance[path] = entry


def lookup(beo: dict[str, Any], path: str) -> dict[str, str] | None:
    data = beo.get("provenance")
    if not isinstance(data, dict):
        return None
    item = data.get(path)
    return item if isinstance(item, dict) else None


def is_guess(beo: dict[str, Any], path: str) -> bool:
    item = lookup(beo, path)
    return bool(item and item.get("source") in GUESSES)


def source_label(entry: dict[str, str] | None) -> str:
    if not entry:
        return ""
    source = entry.get("source") or ""
    ref = entry.get("ref") or ""
    text = entry.get("text") or ""
    parts = [source]
    if ref:
        parts.append(ref)
    if text:
        parts.append(text)
    return " — ".join(parts)


def _brief_text(*parts: Any) -> str:
    return " / ".join(str(part) for part in parts if part not in (None, ""))


def attach_mock_provenance(brief: dict[str, Any], beo: dict[str, Any]) -> dict[str, Any]:
    """Record whether each mock BEO field came from the brief, a formula, or a default."""
    prov: dict[str, dict[str, str]] = {}
    overview = beo.get("overview") or {}
    for key in (
        "beo_id",
        "event_name",
        "event_type",
        "date",
        "client_organization",
        "client_contact",
        "client_role",
        "venue_name",
        "venue_room",
        "venue_address",
        "package_id",
    ):
        if key in overview and overview[key] not in (None, ""):
            mark(prov, f"overview.{key}", BRIEF, f"brief/{key}", _brief_text(overview[key]))
    if overview.get("status"):
        mark(prov, "overview.status", DEFAULT, "draft status unless --approve is used")

    timing = brief.get("timing") or {}
    default_items = {
        "Load-in and room set",
        "Guest arrival",
        "Event start",
        "Event end",
        "Load-out",
    }
    for index, row in enumerate(beo.get("timeline") or []):
        if not isinstance(row, dict):
            continue
        mark(prov, f"timeline[{index}].time", BRIEF, "brief.timing", _brief_text(row.get("time")))
        if row.get("date"):
            mark(prov, f"timeline[{index}].date", COMPUTED, "event date plus overnight offset")
        item = str(row.get("item") or "")
        if item in default_items or (
            item == str(timing.get("load_out_item") or "") and timing.get("load_out_item")
        ) or (
            item == str(timing.get("load_in_item") or "") and timing.get("load_in_item")
        ):
            if timing.get("load_out_item") and item == str(timing["load_out_item"]):
                mark(prov, f"timeline[{index}].item", BRIEF, "brief.timing.load_out_item", item)
            elif timing.get("load_in_item") and item == str(timing["load_in_item"]):
                mark(prov, f"timeline[{index}].item", BRIEF, "brief.timing.load_in_item", item)
            else:
                mark(prov, f"timeline[{index}].item", DEFAULT, "standard service beat label", item)
        else:
            mark(prov, f"timeline[{index}].item", BRIEF, "brief.timing.milestones", item)
        if row.get("room"):
            mark(prov, f"timeline[{index}].room", BRIEF, "brief.timing.milestones", str(row["room"]))
        if row.get("function"):
            mark(prov, f"timeline[{index}].function", BRIEF, "brief.timing.milestones", str(row["function"]))

    headcount = brief.get("headcount") or {}
    mark(prov, "headcount.guarantee", BRIEF, "brief.headcount.guarantee", _brief_text(headcount.get("guarantee")))
    mark(prov, "headcount.expected", BRIEF, "brief.headcount.expected", _brief_text(headcount.get("expected")))
    if "staff_meals" in headcount and headcount["staff_meals"] not in (None, ""):
        mark(
            prov,
            "headcount.staff_meals",
            BRIEF,
            "brief.headcount.staff_meals",
            _brief_text(headcount.get("staff_meals")),
        )
    else:
        mark(prov, "headcount.staff_meals", COMPUTED, "floor-team meal formula")
    mark(prov, "headcount.staffing_plan", COMPUTED, "compute_staffing from guarantee, style, and bar plan")
    mark(prov, "headcount.conflicts", COMPUTED, "conflict_flags for seat capacity and overage")

    menu = brief.get("menu") or {}
    mark(prov, "menu.style", BRIEF, "brief.menu.style", _brief_text(menu.get("style")))
    mark(prov, "menu.courses", BRIEF, "brief.menu.courses")
    if menu.get("service_notes"):
        mark(prov, "menu.service_notes", BRIEF, "brief.menu.service_notes", str(menu["service_notes"]))
    if menu.get("bar"):
        mark(prov, "menu.bar", BRIEF, "brief.menu.bar")

    for index, line in enumerate(beo.get("dietary") or []):
        mark(prov, f"dietary[{index}]", BRIEF, "brief.dietary", str(line))

    room = brief.get("room_setup") or {}
    for key in ("style", "tables", "seating", "other", "seat_capacity"):
        if room.get(key) not in (None, ""):
            mark(prov, f"room_setup.{key}", BRIEF, f"brief.room_setup.{key}", _brief_text(room.get(key)))

    av = brief.get("av") or {}
    if av.get("items"):
        mark(prov, "av.items", BRIEF, "brief.av.items")
    if av.get("notes"):
        mark(prov, "av.notes", BRIEF, "brief.av.notes", str(av["notes"]))

    billing = brief.get("billing") or {}
    for key in ("charge_to", "guarantee_policy", "tax_or_service", "notes"):
        if billing.get(key) not in (None, ""):
            mark(prov, f"billing.{key}", BRIEF, f"brief.billing.{key}", _brief_text(billing.get(key)))
    if billing.get("guarantee_due"):
        mark(prov, "billing.guarantee_due", BRIEF, "brief.billing.guarantee_due", str(billing["guarantee_due"]))
    elif (beo.get("billing") or {}).get("guarantee_due"):
        mark(prov, "billing.guarantee_due", COMPUTED, "noon, N hours or days before the event or package start")

    brief_notes = str(brief.get("notes") or "").strip()
    beo_notes = str(beo.get("notes") or "")
    added_signoff = (
        "[CLIENT SIGNATURE]" not in str(brief.get("notes") or "")
        and beo_notes.endswith(STANDARD_SIGNOFF)
    )
    if brief_notes:
        mark(prov, "notes.copied", BRIEF, "brief.notes", brief_notes[:200])
    if added_signoff:
        mark(prov, "notes.signoff", DEFAULT, "standard sign-off placeholders", STANDARD_SIGNOFF)

    beo["provenance"] = prov
    return beo
