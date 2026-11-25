import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beo_drafter.cli import main
from beo_drafter.drafter import draft, load_brief, write_beo
from beo_drafter.receipts import (
    GENESIS_HASH,
    append_receipt,
    make_receipt,
    read_anchor,
    read_log,
    verify_chain,
    verify_log,
)
def _beo(name: str) -> dict:
    return draft(load_brief(ROOT / "fixtures" / name))


class ReceiptChainTests(unittest.TestCase):
    def test_chain_verifies_and_detects_tamper(self):
        first = _beo("wedding.json")
        second = _beo("gala.json")
        receipt_a = make_receipt(first, previous_hash=GENESIS_HASH, action="approved", at="2027-01-01T00:00:00Z")
        receipt_b = make_receipt(
            second,
            previous_hash=receipt_a["hash"],
            action="approved",
            at="2027-01-01T00:01:00Z",
        )
        beos = {
            first["overview"]["beo_id"]: first,
            second["overview"]["beo_id"]: second,
        }
        verify_chain([receipt_a, receipt_b], beos)

        tampered = dict(receipt_b)
        tampered["note"] = "altered after signing"
        with self.assertRaises(ValueError):
            verify_chain([receipt_a, tampered], beos)

        broken_prev = dict(receipt_b)
        broken_prev["previous_hash"] = "ab" * 32
        broken_prev["hash"] = receipt_b["hash"]
        with self.assertRaises(ValueError):
            verify_chain([receipt_a, broken_prev], beos)

        altered_beos = dict(beos)
        altered_beos[first["overview"]["beo_id"]] = dict(first, notes="changed after receipt")
        with self.assertRaises(ValueError):
            verify_chain([receipt_a, receipt_b], altered_beos)

        with self.assertRaises(ValueError):
            verify_chain([receipt_b, receipt_a], beos)
        # A prefix of a valid chain still verifies mid-chain; the anchor is what
        # detects tail truncation.
        verify_chain([receipt_a], beos)

        inserted = make_receipt(first, previous_hash=receipt_a["hash"], action="approved", at="2027-01-01T00:00:30Z", note="insert")
        with self.assertRaises(ValueError):
            verify_chain([receipt_a, inserted, receipt_b], beos)

    def test_log_round_trip_and_anchor_detects_truncation(self):
        first = _beo("awards-dinner.json")
        second = _beo("offsite-lunch.json")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "receipts.jsonl"
            first_paths = write_beo(first, tmp_path, "awards-dinner")
            second_paths = write_beo(second, tmp_path, "offsite-lunch")
            append_receipt(log_path, first, action="approved", at="2027-01-02T12:00:00Z")
            append_receipt(log_path, second, action="approved", at="2027-01-02T12:01:00Z")
            verify_log(log_path, [first_paths["json"], second_paths["json"]])
            rows = read_log(log_path)
            anchor = read_anchor(log_path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["previous_hash"], GENESIS_HASH)
            self.assertEqual(anchor["count"], 2)
            self.assertEqual(anchor["tip_hash"], rows[-1]["hash"])

            truncated = "\n".join(log_path.read_text(encoding="utf-8").splitlines()[:1]) + "\n"
            log_path.write_text(truncated, encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                verify_log(log_path, [first_paths["json"], second_paths["json"]])
            self.assertIn("anchor count", str(raised.exception))

            # Mid-chain verify of the truncated log would still pass without the anchor.
            verify_chain(read_log(log_path), {first["overview"]["beo_id"]: first})

    def test_forged_append_without_anchor_update_fails(self):
        beo = _beo("wedding.json")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "receipts.jsonl"
            paths = write_beo(beo, tmp_path, "wedding")
            first = append_receipt(log_path, beo, action="approved", at="2027-01-03T00:00:00Z")
            forged = make_receipt(
                beo,
                previous_hash=first["hash"],
                action="approved",
                at="2027-01-03T00:01:00Z",
                note="forged tail",
            )
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(forged, sort_keys=True) + "\n")
            with self.assertRaises(ValueError):
                verify_log(log_path, [paths["json"]])

    def test_cli_draft_approve_updates_status_and_banner(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            receipts = tmp_path / "receipts.jsonl"
            unapproved = tmp_path / "unapproved"
            self.assertEqual(
                main(
                    [
                        "draft",
                        str(ROOT / "fixtures" / "corporate-breakfast.json"),
                        "--client",
                        "mock",
                        "--out",
                        str(unapproved),
                    ]
                ),
                0,
            )
            raw = json.loads((unapproved / "corporate-breakfast.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["overview"]["status"], "draft")
            self.assertIn("**UNAPPROVED DRAFT**", (unapproved / "corporate-breakfast.md").read_text(encoding="utf-8"))
            self.assertNotIn("**APPROVED**", (unapproved / "corporate-breakfast.md").read_text(encoding="utf-8"))

            self.assertEqual(
                main(
                    [
                        "draft",
                        str(ROOT / "fixtures" / "corporate-breakfast.json"),
                        "--client",
                        "mock",
                        "--out",
                        str(tmp_path),
                        "--approve",
                        "--receipts",
                        str(receipts),
                    ]
                ),
                0,
            )
            beo_json = tmp_path / "corporate-breakfast.json"
            data = json.loads(beo_json.read_text(encoding="utf-8"))
            self.assertEqual(data["overview"]["status"], "approved")
            approved_at = data["overview"]["approved_at"]
            self.assertRegex(approved_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            markdown = (tmp_path / "corporate-breakfast.md").read_text(encoding="utf-8")
            self.assertIn("**APPROVED**", markdown)
            self.assertNotIn("UNAPPROVED", markdown)
            self.assertIn("approved_at: " + approved_at, markdown)
            self.assertIn("[CLIENT SIGNATURE]", markdown)
            self.assertIn("[MANAGER SIGNATURE]", markdown)
            # --approve stamps status and time only; it must not invent a signer name.
            self.assertNotRegex(data["notes"], r"Client: (?!\[CLIENT SIGNATURE\])\S")
            self.assertEqual(main(["verify-receipts", str(receipts), str(beo_json)]), 0)

    def test_concurrent_appends_do_not_fork(self):
        beo = _beo("gala.json")
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "receipts.jsonl"
            barrier = threading.Barrier(16)

            def write(index: int) -> None:
                barrier.wait()
                append_receipt(
                    log_path,
                    beo,
                    action="approved",
                    note=f"writer-{index}",
                    at=f"2027-01-04T00:{index:02d}:00Z",
                )

            with ThreadPoolExecutor(max_workers=16) as pool:
                list(pool.map(write, range(16)))

            rows = read_log(log_path)
            self.assertEqual(len(rows), 16)
            previous = {row["previous_hash"] for row in rows}
            self.assertEqual(len(previous), 16, "fork: two receipts share a previous_hash")
            beos = {beo["overview"]["beo_id"]: beo}
            verify_chain(rows, beos)
            write_beo(beo, tmp, "gala")
            verify_log(log_path, [Path(tmp) / "gala.json"])
            self.assertEqual(read_anchor(log_path)["count"], 16)

    def test_verify_log_rejects_banner_mismatch(self):
        beo = _beo("wedding.json")
        beo["overview"]["status"] = "approved"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = write_beo(beo, tmp_path, "wedding")
            log_path = tmp_path / "receipts.jsonl"
            append_receipt(log_path, beo, action="approved", at="2027-01-05T00:00:00Z")
            verify_log(log_path, [paths["json"]])
            md = paths["markdown"]
            md.write_text(md.read_text(encoding="utf-8").replace("**APPROVED**", "**UNAPPROVED DRAFT**"), encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                verify_log(log_path, [paths["json"]])
            self.assertIn("banner", str(raised.exception).lower())

    def test_fcntl_fallback_warns_on_stderr(self):
        import beo_drafter.receipts as receipts
        beo = _beo("offsite-lunch.json")
        previous = receipts.fcntl
        try:
            receipts.fcntl = None
            with tempfile.TemporaryDirectory() as tmp:
                log_path = Path(tmp) / "receipts.jsonl"
                import io
                from contextlib import redirect_stderr
                buf = io.StringIO()
                with redirect_stderr(buf):
                    append_receipt(log_path, beo, action="approved", at="2027-01-06T00:00:00Z")
                self.assertIn("fcntl is unavailable", buf.getvalue())
                self.assertIn("single-writer", buf.getvalue())
                write_beo(beo, tmp, "offsite-lunch")
                verify_log(log_path, [Path(tmp) / "offsite-lunch.json"])
        finally:
            receipts.fcntl = previous


if __name__ == "__main__":
    unittest.main()
