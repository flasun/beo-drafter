"""Optional hash-chained receipt log with a tip-hash anchor.

Each receipt hash is sha256(previous_hash + canonical BEO JSON + record body).
Genesis previous_hash is 64 zero hex chars. No keys or PII beyond the synthetic BEO.

This is tamper-evident mid-chain and, with the anchor file, detects tail
truncation and an append that does not update the anchor. It is not a
signature with a real key: anyone who can rewrite both the log and the
anchor can produce a chain that still verifies.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

GENESIS_HASH = "0" * 64

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None  # type: ignore[assignment]


def canonical_beo(beo: dict[str, Any]) -> str:
    return json.dumps(beo, sort_keys=True, separators=(",", ":"))


def _canonical_record(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def compute_hash(previous_hash: str, beo: dict[str, Any], record: dict[str, Any]) -> str:
    material = previous_hash + canonical_beo(beo) + _canonical_record(record)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def make_receipt(
    beo: dict[str, Any],
    previous_hash: str = GENESIS_HASH,
    action: str = "approved",
    note: str = "",
    at: str | None = None,
) -> dict[str, Any]:
    record = {
        "previous_hash": previous_hash,
        "action": action,
        "beo_id": (beo.get("overview") or {}).get("beo_id", ""),
        "source_brief_id": beo.get("source_brief_id", ""),
        "note": note,
        "at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    record["hash"] = compute_hash(previous_hash, beo, record)
    return record


def read_log(path: str | Path) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def anchor_path(log_path: str | Path) -> Path:
    return Path(str(log_path) + ".anchor")


def write_anchor(log_path: str | Path, receipts: list[dict[str, Any]]) -> Path:
    path = anchor_path(log_path)
    tip = receipts[-1]["hash"] if receipts else GENESIS_HASH
    payload = {"count": len(receipts), "tip_hash": tip}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_anchor(log_path: str | Path) -> dict[str, Any]:
    path = anchor_path(log_path)
    if not path.exists():
        raise ValueError(f"missing receipt anchor file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _append_locked(
    handle: Any,
    log_path: Path,
    beo: dict[str, Any],
    action: str,
    note: str,
    at: str | None,
) -> dict[str, Any]:
    handle.seek(0)
    existing: list[dict[str, Any]] = []
    for line in handle:
        if line.strip():
            existing.append(json.loads(line))
    previous = existing[-1]["hash"] if existing else GENESIS_HASH
    receipt = make_receipt(beo, previous_hash=previous, action=action, note=note, at=at)
    handle.seek(0, 2)
    handle.write(json.dumps(receipt, sort_keys=True) + "\n")
    handle.flush()
    try:
        import os

        os.fsync(handle.fileno())
    except OSError:
        pass
    write_anchor(log_path, existing + [receipt])
    return receipt


def append_receipt(
    path: str | Path,
    beo: dict[str, Any],
    action: str = "approved",
    note: str = "",
    at: str | None = None,
) -> dict[str, Any]:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.touch()
    if fcntl is None:
        print(
            "warning: fcntl is unavailable; append_receipt is single-writer only "
            "and concurrent appends may fork the chain",
            file=sys.stderr,
        )
    with log_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            return _append_locked(handle, log_path, beo, action, note, at)
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def verify_chain(receipts: Iterable[dict[str, Any]], beos: dict[str, dict[str, Any]]) -> None:
    """Raise ValueError if the chain is broken or a BEO does not match."""
    previous = GENESIS_HASH
    for index, receipt in enumerate(receipts):
        beo_id = receipt.get("beo_id")
        if beo_id not in beos:
            raise ValueError(f"receipt {index} refers to unknown BEO {beo_id!r}")
        expected_prev = receipt.get("previous_hash")
        if expected_prev != previous:
            raise ValueError(
                f"receipt {index} previous_hash mismatch: "
                f"expected {previous}, got {expected_prev}"
            )
        expected = compute_hash(previous, beos[beo_id], receipt)
        actual = receipt.get("hash")
        if actual != expected:
            raise ValueError(f"receipt {index} hash mismatch")
        previous = actual


def verify_anchor(log_path: str | Path, receipts: list[dict[str, Any]]) -> None:
    anchor = read_anchor(log_path)
    count = anchor.get("count")
    tip = anchor.get("tip_hash")
    if count != len(receipts):
        raise ValueError(
            f"anchor count mismatch: expected {len(receipts)}, got {count}"
        )
    expected_tip = receipts[-1]["hash"] if receipts else GENESIS_HASH
    if tip != expected_tip:
        raise ValueError("anchor tip hash mismatch")


def verify_markdown_matches_json(beo: dict[str, Any], markdown_path: Path) -> None:
    if not markdown_path.exists():
        raise ValueError(f"missing Markdown companion: {markdown_path}")
    text = markdown_path.read_text(encoding="utf-8")
    status = str((beo.get("overview") or {}).get("status") or "draft").lower()
    if status == "approved":
        if "**APPROVED**" not in text or "UNAPPROVED" in text:
            raise ValueError(
                f"Markdown banner does not match JSON status {status!r} in {markdown_path}"
            )
    elif "**UNAPPROVED DRAFT**" not in text:
        raise ValueError(
            f"Markdown banner does not match JSON status {status!r} in {markdown_path}"
        )


def verify_log(log_path: str | Path, beo_paths: Iterable[str | Path]) -> None:
    beos: dict[str, dict[str, Any]] = {}
    for path in beo_paths:
        json_path = Path(path)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        beo_id = (data.get("overview") or {}).get("beo_id")
        if not beo_id:
            raise ValueError(f"no overview.beo_id in {path}")
        beos[beo_id] = data
        verify_markdown_matches_json(data, json_path.with_suffix(".md"))
    receipts = read_log(log_path)
    verify_chain(receipts, beos)
    verify_anchor(log_path, receipts)
