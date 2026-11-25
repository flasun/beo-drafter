from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from beo_drafter.clients import get_client
from beo_drafter.clients.base import ModelClient
from beo_drafter.render import render_markdown
from beo_drafter.schema import validate_beo, validate_brief


def load_brief(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_brief(data)


def draft(brief: dict[str, Any], client: ModelClient | None = None) -> dict[str, Any]:
    brief = validate_brief(brief)
    client = client or get_client("mock")
    beo = client.draft_beo(brief)
    return validate_beo(beo, force_draft=True)


STEM_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def validate_output_stem(stem: str) -> str:
    """Reject a stem that could escape the out directory."""
    if not isinstance(stem, str) or not STEM_RE.fullmatch(stem) or stem in {".", ".."}:
        raise ValueError(
            "output stem must be a 1-128 character filename of letters, digits, "
            "dot, underscore, or dash"
        )
    return stem


def write_beo(beo: dict[str, Any], out_dir: str | Path, stem: str) -> dict[str, Path]:
    stem = validate_output_stem(stem)
    out = Path(out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    json_path = (out / f"{stem}.json").resolve()
    md_path = (out / f"{stem}.md").resolve()
    if not json_path.is_relative_to(out) or not md_path.is_relative_to(out):
        raise ValueError("output path must stay under the out directory")
    json_path.write_text(json.dumps(beo, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(beo), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}
