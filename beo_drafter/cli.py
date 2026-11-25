from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from beo_drafter.clients import get_client
from beo_drafter.drafter import draft, load_brief, write_beo
from beo_drafter.receipts import append_receipt, verify_log


def _cmd_draft(args: argparse.Namespace) -> int:
    brief = load_brief(args.brief)
    client = get_client(args.client)
    beo = draft(brief, client=client)
    if args.approve:
        if not args.receipts:
            raise SystemExit("--approve requires --receipts PATH")
        overview = beo.setdefault("overview", {})
        overview["status"] = "approved"
        overview["approved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stem = args.stem or Path(args.brief).stem
    paths = write_beo(beo, args.out, stem)
    print(paths["json"])
    print(paths["markdown"])
    if args.approve:
        receipt = append_receipt(
            args.receipts,
            beo,
            action="approved",
            note=args.receipt_note,
        )
        print(f"receipt {receipt['hash']}")
        print(f"anchor {receipt['hash']} count updated")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    verify_log(args.receipts, args.beo)
    print("receipt chain ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Draft a synthetic Banquet Event Order from a brief JSON file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    draft_p = sub.add_parser("draft", help="Draft a BEO with the selected client.")
    draft_p.add_argument("brief", help="Path to a synthetic brief JSON file.")
    draft_p.add_argument(
        "--client",
        default="mock",
        help="Model client: mock (default, offline) or ollama (Sprites connector; tests never hit the network).",
    )
    draft_p.add_argument("--out", default="out", help="Directory for .json and .md output.")
    draft_p.add_argument("--stem", default="", help="Output filename stem (default: brief file stem).")
    draft_p.add_argument(
        "--approve",
        action="store_true",
        help="Mark the BEO approved (status and approved_at only; no signature names), rewrite files, and record a receipt. Requires --receipts.",
    )
    draft_p.add_argument("--receipts", default="", help="JSONL receipt log path.")
    draft_p.add_argument("--receipt-note", default="", help="Optional note stored on the receipt.")
    draft_p.set_defaults(func=_cmd_draft)

    verify_p = sub.add_parser("verify-receipts", help="Verify a hash-chained receipt log and its anchor.")
    verify_p.add_argument("receipts", help="JSONL receipt log path.")
    verify_p.add_argument(
        "beo",
        nargs="+",
        help="One or more BEO JSON files referenced by the log.",
    )
    verify_p.set_defaults(func=_cmd_verify)

    return parser


_SECRET_ASSIGN_RE = re.compile(
    r"(?i)(?:^|[_.]?)(?:api[_-]?key|secret|token|password|passwd|authorization)$"
)


def _is_path_token(trimmed: str) -> bool:
    """True for absolute, home, relative, file URL, or $-style / %-style path tokens."""
    if not trimmed:
        return False
    lower = trimmed.lower()
    if lower.startswith("file:"):
        return True
    if trimmed.startswith("/") or trimmed in {"~", "~/"} or trimmed.startswith("~/"):
        return True
    if len(trimmed) > 2 and trimmed[1] == ":" and trimmed[2] == "/":
        return True
    if trimmed.startswith("./") or trimmed.startswith("../") or trimmed == "..":
        return True
    if "/" in trimmed and not lower.startswith(("http:", "https:")):
        return True
    if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*|\$\{[^}]+\}", trimmed):
        return True
    if re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", trimmed):
        return True
    return False


def _safe_error(exc: BaseException) -> str:
    """Message only: no traceback, no local paths, and no file:// or env-style paths."""
    text = str(exc).strip() or type(exc).__name__
    cleaned: list[str] = []
    for token in text.replace("\\", "/").split():
        trimmed = token.strip(".,;:()'\"")
        if "=" in trimmed:
            key, _, value = trimmed.partition("=")
            if _is_path_token(value) or _is_path_token(trimmed):
                cleaned.append("[path]")
                continue
            if _SECRET_ASSIGN_RE.search(key):
                cleaned.append("[redacted]")
                continue
        if _is_path_token(trimmed):
            cleaned.append("[path]")
        else:
            cleaned.append(token)
    return " ".join(cleaned)


def public_error_message(
    exc: BaseException, *, generic: str = "could not draft this request"
) -> str:
    """Keep pre-scrubbed validation text; hide anything that looks like a path."""
    if not isinstance(exc, ValueError):
        return generic
    original = str(exc).strip() or generic
    scrubbed = _safe_error(exc)
    if "[path]" in scrubbed or "[redacted]" in scrubbed:
        return generic
    return scrubbed or generic


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return args.func(args)
    except (ValueError, OSError, RuntimeError) as exc:
        print(_safe_error(exc), file=sys.stderr)
        return 1
