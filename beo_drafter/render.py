"""Render a BEO dict to Markdown and a traced HTML fragment. Section headings stay stable for tests."""

from __future__ import annotations

import html
from typing import Any

from beo_drafter import REQUIRED_SECTIONS
from beo_drafter.provenance import is_guess, lookup, notes_segments, source_label
from beo_drafter.schema import validate_beo


def _md_text(value: Any) -> str:
    """Escape model-supplied HTML so CLI .md is safe to preview."""
    return html.escape(str(value), quote=True)


def _guess_suffix(beo: dict[str, Any] | None, path: str) -> str:
    if beo is not None and is_guess(beo, path):
        return " [guess]"
    return ""


def _as_lines(value: Any, indent: str = "", beo: dict[str, Any] | None = None, path: str = "") -> list[str]:
    if value in (None, "", []):
        return [f"{indent}_None listed._"]
    if isinstance(value, str):
        if path == "notes" and beo is not None:
            parts = notes_segments(beo, value)
            if parts:
                return [
                    f"{indent}{_md_text(text)}{_guess_suffix(beo, part_path)}"
                    for part_path, text in parts
                ]
        return [f"{indent}{_md_text(value)}{_guess_suffix(beo, path)}"]
    if isinstance(value, list):
        lines: list[str] = []
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]" if path else f"[{index}]"
            if isinstance(item, dict):
                if "time" in item and "item" in item:
                    when = item["time"]
                    if item.get("date"):
                        when = f"{item['date']} {when}"
                    elif item.get("day_offset"):
                        when = f"{when} (+{item['day_offset']}d)"
                    if item.get("day_offset"):
                        when = f"{when} (next day)"
                    mid = [
                        _md_text(item[key])
                        for key in ("function", "room")
                        if item.get(key)
                    ]
                    body = " — ".join(mid + [_md_text(item["item"])])
                    suffix = _guess_suffix(beo, f"{item_path}.item") or _guess_suffix(beo, item_path)
                    lines.append(f"{indent}- {_md_text(when)} — {body}{suffix}")
                else:
                    nested = _as_lines(item, indent + "  ", beo=beo, path=item_path)
                    lines.extend(nested if any(item.values()) else [f"{indent}- _empty_"])
            else:
                lines.append(f"{indent}- {_md_text(item)}{_guess_suffix(beo, item_path)}")
        return lines or [f"{indent}_None listed._"]
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if item in (None, ""):
                continue
            if item == []:
                continue
            key_path = f"{path}.{key}" if path else str(key)
            if isinstance(item, (list, dict)):
                lines.append(f"{indent}- {_md_text(key)}:")
                lines.extend(_as_lines(item, indent + "  ", beo=beo, path=key_path))
            else:
                lines.append(
                    f"{indent}- {_md_text(key)}: {_md_text(item)}{_guess_suffix(beo, key_path)}"
                )
        return lines or [f"{indent}_None listed._"]
    return [f"{indent}{_md_text(value)}{_guess_suffix(beo, path)}"]


SECTION_TITLES = {
    "overview": "Overview",
    "timeline": "Timeline",
    "headcount": "Headcount",
    "menu": "Menu",
    "dietary": "Dietary needs",
    "room_setup": "Room setup",
    "av": "AV",
    "billing": "Billing",
    "notes": "Notes",
}


def status_banner(status: str) -> str:
    if (status or "").strip().lower() == "approved":
        return "**APPROVED**"
    return "**UNAPPROVED DRAFT**"


def render_markdown(beo: dict[str, Any]) -> str:
    beo = validate_beo(beo)
    overview = beo["overview"]
    title = _md_text(overview.get("event_name") or "Banquet Event Order")
    beo_id = overview.get("beo_id", "")
    lines = [f"# Banquet Event Order: {title}"]
    if beo_id:
        lines.append(f"_{_md_text(beo_id)}_")
    lines.append(status_banner(str(overview.get("status") or "draft")))
    lines.append("")
    for key in REQUIRED_SECTIONS:
        lines.append(f"## {SECTION_TITLES[key]}")
        lines.extend(_as_lines(beo[key], beo=beo, path=key))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _html_wrap(beo: dict[str, Any], path: str, inner: str) -> str:
    """inner is already escaped. Guesses get class=guess; source text goes in title."""
    entry = lookup(beo, path)
    title = html.escape(source_label(entry), quote=True) if entry else ""
    attrs = f' title="{title}"' if title else ""
    if is_guess(beo, path):
        return f'<span class="guess"{attrs}>{inner}</span>'
    if attrs:
        return f"<span{attrs}>{inner}</span>"
    return inner


def _html_items(value: Any, beo: dict[str, Any], path: str) -> list[str]:
    chunks: list[str] = []
    if value in (None, "", []):
        return ["<li><em>None listed.</em></li>"]
    if isinstance(value, str):
        if path == "notes":
            parts = notes_segments(beo, value)
            if parts:
                return [
                    f"<li>{_html_wrap(beo, part_path, _md_text(text))}</li>"
                    for part_path, text in parts
                ]
        return [f"<li>{_html_wrap(beo, path, _md_text(value))}</li>"]
    if isinstance(value, list):
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]"
            if isinstance(item, dict) and "time" in item and "item" in item:
                when = item["time"]
                if item.get("date"):
                    when = f"{item['date']} {when}"
                elif item.get("day_offset"):
                    when = f"{when} (+{item['day_offset']}d)"
                if item.get("day_offset"):
                    when = f"{when} (next day)"
                mid = " — ".join(
                    _md_text(item[key]) for key in ("function", "room") if item.get(key)
                )
                body = " — ".join(part for part in (mid, _md_text(item["item"])) if part)
                row_path = f"{item_path}.item"
                chunks.append(
                    f"<li>{_html_wrap(beo, row_path, _md_text(when) + ' — ' + body)}</li>"
                )
            elif isinstance(item, dict):
                chunks.extend(_html_items(item, beo, item_path))
            else:
                chunks.append(f"<li>{_html_wrap(beo, item_path, _md_text(item))}</li>")
        return chunks or ["<li><em>None listed.</em></li>"]
    if isinstance(value, dict):
        for key, item in value.items():
            if item in (None, "", []):
                continue
            key_path = f"{path}.{key}" if path else str(key)
            if isinstance(item, (list, dict)):
                chunks.append(f"<li>{_md_text(key)}:<ul>")
                chunks.extend(_html_items(item, beo, key_path))
                chunks.append("</ul></li>")
            else:
                inner = f"{_md_text(key)}: {_md_text(item)}"
                chunks.append(f"<li>{_html_wrap(beo, key_path, inner)}</li>")
        return chunks or ["<li><em>None listed.</em></li>"]
    return [f"<li>{_html_wrap(beo, path, _md_text(value))}</li>"]


def render_html(beo: dict[str, Any]) -> str:
    """Traced HTML fragment. Values are escaped once. Guesses use class=guess."""
    beo = validate_beo(beo)
    overview = beo["overview"]
    title = _html_wrap(
        beo, "overview.event_name", _md_text(overview.get("event_name") or "Banquet Event Order")
    )
    status = _html_wrap(beo, "overview.status", _md_text(status_banner(str(overview.get("status") or "draft"))))
    parts = [
        '<article class="beo-trace">',
        f"<h2>Banquet Event Order: {title}</h2>",
        f"<p>{status}</p>",
    ]
    for key in REQUIRED_SECTIONS:
        parts.append(f"<h3>{_md_text(SECTION_TITLES[key])}</h3>")
        parts.append("<ul>")
        parts.extend(_html_items(beo[key], beo, key))
        parts.append("</ul>")
    parts.append("</article>")
    return "\n".join(parts)
