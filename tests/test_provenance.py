import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beo_drafter.cli import _safe_error, main, public_error_message
from beo_drafter.clients.mock import bartender_count, bartender_ratio, normalize_clock, resolve_guarantee_due
from beo_drafter.clients.ollama import SYSTEM_PROMPT, OllamaCloudClient, coerce_model_beo
from beo_drafter.drafter import draft, load_brief
from beo_drafter.paste import MAX_PASTE_BYTES, apply_headcount_overrides, parse_pasted_input
from beo_drafter.provenance import STANDARD_SIGNOFF, is_guess
from beo_drafter.render import render_html, render_markdown
from beo_drafter.schema import validate_beo
from beo_drafter.server import _form_page, draft_from_request


class _FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self, size: int = -1) -> bytes:
        return self._raw if size < 0 else self._raw[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class PromptAndCoercionTests(unittest.TestCase):
    def test_system_prompt_names_types_and_skeleton(self):
        self.assertIn("timeline: a JSON array", SYSTEM_PROMPT)
        self.assertIn("dietary: a JSON array", SYSTEM_PROMPT)
        self.assertIn("notes: a JSON string", SYSTEM_PROMPT)
        self.assertIn("overview, headcount, menu, room_setup, av, billing: JSON objects", SYSTEM_PROMPT)
        self.assertIn('"timeline":[{"time":', SYSTEM_PROMPT)

    def test_timeline_dict_is_coerced_to_rows(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        expected = draft(brief)
        expected["drafter"] = "ollama"
        expected["timeline"] = {"18:00": "Dinner starts", "19:00": "First dance"}

        def fake_urlopen(request: Request, timeout: int = 60) -> _FakeResponse:
            return _FakeResponse({"message": {"content": json.dumps(expected)}})

        client = OllamaCloudClient(
            connection_id="test-connection",
            model="replace-me",
            urlopen=fake_urlopen,
        )
        beo = client.draft_beo(brief)
        self.assertIsInstance(beo["timeline"], list)
        self.assertEqual(beo["timeline"][0]["time"], "18:00")
        self.assertEqual(beo["timeline"][0]["item"], "Dinner starts")
        self.assertIn("time-to-label", " ".join(beo.get("coercions") or []))

    def test_non_dict_timeline_still_fails_validation(self):
        beo = draft(load_brief(ROOT / "fixtures" / "wedding.json"))
        beo["timeline"] = "not a list"
        with self.assertRaisesRegex(ValueError, "timeline must be a list"):
            validate_beo(beo)
        self.assertEqual(coerce_model_beo({"timeline": "not a list"})["timeline"], "not a list")

    def test_coercion_drops_unknown_keys_and_stringifies_scalar_items(self):
        beo = coerce_model_beo(
            {
                "timeline": {
                    "18:00": {"item": 80, "label": "ignored-when-item-set", "mystery": "drop me", "room": "Hall"},
                    "19:00": 21,
                }
            }
        )
        self.assertEqual(beo["timeline"][0]["item"], "80")
        self.assertEqual(beo["timeline"][0]["time"], "18:00")
        self.assertEqual(beo["timeline"][0]["room"], "Hall")
        self.assertNotIn("mystery", beo["timeline"][0])
        self.assertNotIn("label", beo["timeline"][0])
        self.assertEqual(beo["timeline"][1], {"time": "19:00", "item": "21"})

    def test_coercion_rejects_non_scalar_items(self):
        with self.assertRaisesRegex(ValueError, "timeline item must be a string"):
            coerce_model_beo({"timeline": {"18:00": ["Dinner"]}})
        with self.assertRaisesRegex(ValueError, "timeline item must be a string"):
            coerce_model_beo({"timeline": {"18:00": {"item": {"nested": True}}}})


class BarAndPackageLowTests(unittest.TestCase):
    def test_named_spirits_use_one_to_fifty(self):
        for phrase in (
            "bourbon bar",
            "whiskey bar",
            "tequila bar",
            "vodka bar",
            "gin bar",
            "rum bar",
            "liquor bar",
            "full liquor",
        ):
            self.assertEqual(bartender_ratio({"style": phrase}), 50, phrase)
        self.assertEqual(bartender_count(51, {"style": "bourbon bar"}), 2)
        self.assertEqual(bartender_count(51, {"style": "vodka bar"}), 2)
        self.assertEqual(bartender_ratio({"style": "hosted beer and wine"}), 75)

    def test_no_bar_means_zero_bartenders(self):
        self.assertEqual(bartender_count(120, {"style": "no bar"}), 0)
        self.assertEqual(bartender_count(120, {"style": "plated dinner, no bar"}), 0)

    def test_spirit_free_bar_is_not_spirits(self):
        self.assertEqual(bartender_ratio({"style": "spirit-free bar"}), 60)
        self.assertNotEqual(bartender_ratio({"style": "spirit-free bar"}), 50)
        self.assertEqual(bartender_ratio({"style": "spirit-free bourbon bar"}), 60)
        self.assertEqual(bartender_ratio({"style": "spirit-free vodka bar"}), 60)
        self.assertNotEqual(bartender_ratio({"style": "spirit-free bourbon bar"}), 50)
        self.assertEqual(
            bartender_ratio({"style": "wine service", "notes": "they asked about a vodka bar"}),
            60,
        )

    def test_no_and_without_a_named_spirit_are_not_one_to_fifty(self):
        self.assertEqual(bartender_ratio({"style": "no vodka"}), 60)
        self.assertEqual(bartender_ratio({"style": "no gin"}), 60)
        self.assertEqual(bartender_ratio({"style": "beer and wine, no vodka"}), 75)
        self.assertEqual(bartender_ratio({"style": "hosted beer and wine, without vodka"}), 75)
        self.assertNotEqual(bartender_ratio({"style": "no vodka"}), 50)
        self.assertNotEqual(bartender_ratio({"style": "beer and wine, no vodka"}), 50)
        # An affirmed spirit still wins if another spirit is excluded.
        self.assertEqual(bartender_ratio({"style": "vodka bar, no gin"}), 50)
        self.assertEqual(bartender_ratio({"style": "open bar, no vodka"}), 50)

    def test_cash_bar_in_notes_does_not_set_spirits_ratio(self):
        self.assertEqual(
            bartender_ratio({"style": "wine service", "notes": "ask accounting about a cash bar"}),
            60,
        )
        self.assertEqual(bartender_ratio({"style": "cash bar"}), 50)

    def test_whitespace_package_start_uses_the_clean_message(self):
        brief = load_brief(ROOT / "fixtures" / "conference-001b.json")
        brief = json.loads(json.dumps(brief))
        brief["package_start"] = "   "
        brief["billing"].pop("guarantee_due", None)
        with self.assertRaises(ValueError) as raised:
            resolve_guarantee_due(brief)
        self.assertIn("package_id requires package_start", str(raised.exception))
        self.assertNotIn("isoformat", str(raised.exception).lower())

    def test_event_end_match_normalizes_seconds_and_spaces(self):
        self.assertEqual(normalize_clock("21:00:00"), "21:00")
        self.assertEqual(normalize_clock(" 21:00 "), "21:00")
        brief = load_brief(ROOT / "fixtures" / "conference-001a.json")
        brief["timing"] = dict(brief["timing"])
        brief["timing"]["end"] = "21:00:00"
        brief["timing"]["milestones"] = list(brief["timing"]["milestones"])
        brief["timing"]["milestones"][-1] = dict(brief["timing"]["milestones"][-1], time="21:00 ")
        items = [row["item"] for row in draft(brief)["timeline"] if row["time"].strip() in {"21:00", "21:00:00", "21:00 "} or row["item"] == "Event end"]
        self.assertNotIn("Event end", items)


class ProvenanceAndPasteTests(unittest.TestCase):
    def test_staffing_is_a_guess_and_event_name_is_from_the_brief(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        beo = draft(brief)
        self.assertTrue(is_guess(beo, "headcount.staffing_plan"))
        self.assertEqual(beo["provenance"]["overview.event_name"]["source"], "brief")
        markdown = render_markdown(beo)
        self.assertIn("staffing_plan:", markdown)
        self.assertIn("[guess]", markdown)
        self.assertIn("staffing_plan:", markdown[markdown.index("staffing_plan:"):markdown.index("staffing_plan:") + 80] or "")
        self.assertIn("[guess]", [line for line in markdown.splitlines() if "staffing_plan:" in line][0])
        self.assertNotIn("[guess]", [line for line in markdown.splitlines() if "event_name:" in line][0])

    def test_html_marks_guesses_in_red_and_escapes_once(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        brief["overview_dummy"] = True
        beo = draft(brief)
        beo["overview"]["event_name"] = "Kim & <script>alert(1)</script> Alvarez"
        fragment = render_html(beo)
        self.assertIn('class="guess"', fragment)
        self.assertIn("staffing_plan", fragment)
        self.assertIn("&amp;", fragment)
        self.assertIn("&lt;script&gt;", fragment)
        self.assertNotIn("<script>", fragment)
        self.assertNotIn("&amp;amp;", fragment)
        self.assertIn("title=", fragment)

    def test_paste_email_and_guarantee_vegan_override(self):
        base = load_brief(ROOT / "fixtures" / "wedding.json")
        self.assertEqual(base["headcount"]["guarantee"], 120)
        email = (
            "Subject: Kim & Alvarez Reception\n"
            "Date: 2027-06-12\n"
            "Guarantee: 180\n"
            "Vegan: 10\n"
        )
        parsed = parse_pasted_input(email, base=base)
        self.assertEqual(parsed["headcount"]["guarantee"], 180)
        vegan = [item for item in parsed["dietary"] if item.get("need") == "vegan"]
        self.assertEqual(vegan[0]["count"], 10)
        result = draft_from_request(
            {"fixture": "wedding", "client": "mock", "guarantee": "180", "vegan": "10"}
        )
        self.assertEqual(result["beo"]["headcount"]["guarantee"], 180)
        joined = " ".join(result["beo"]["dietary"])
        self.assertIn("vegan", joined.lower())
        self.assertIn("10", joined)
        page = _form_page(
            markdown=result["markdown"],
            fixture="wedding",
            html_result=result["html"],
        ).decode("utf-8")
        self.assertIn('class="guess"', page)
        self.assertIn("color: #b00020", page)
        self.assertIn('name="guarantee"', page)

    def test_apply_overrides_on_a_copy(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        changed = apply_headcount_overrides(brief, guarantee=180, vegan=8)
        self.assertEqual(brief["headcount"]["guarantee"], 120)
        self.assertEqual(changed["headcount"]["guarantee"], 180)


    def test_added_signoff_is_a_guess_and_brief_notes_are_not(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        beo = draft(brief)
        self.assertEqual(beo["provenance"]["notes.copied"]["source"], "brief")
        self.assertEqual(beo["provenance"]["notes.signoff"]["source"], "default")
        self.assertTrue(is_guess(beo, "notes.signoff"))
        self.assertFalse(is_guess(beo, "notes.copied"))
        self.assertFalse(is_guess(beo, "notes"))
        self.assertIn(brief["notes"].strip(), beo["notes"])
        self.assertTrue(beo["notes"].endswith(STANDARD_SIGNOFF))
        section = render_markdown(beo).split("## Notes", 1)[1]
        brief_line = [line for line in section.splitlines() if "Synthetic sample only" in line][0]
        sign_line = [line for line in section.splitlines() if "[CLIENT SIGNATURE]" in line][0]
        self.assertNotIn("[guess]", brief_line)
        self.assertIn("[guess]", sign_line)
        fragment = render_html(beo)
        self.assertRegex(fragment, r'<span class="guess"[^>]*>[^<]*\[CLIENT SIGNATURE\]')
        self.assertNotRegex(fragment, r'<span class="guess"[^>]*>[^<]*Synthetic sample only')

    def test_notes_without_brief_text_are_all_signoff_guesses(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        brief["notes"] = ""
        beo = draft(brief)
        self.assertNotIn("notes.copied", beo["provenance"])
        self.assertEqual(beo["provenance"]["notes.signoff"]["source"], "default")
        self.assertEqual(beo["notes"], STANDARD_SIGNOFF)
        section = render_markdown(beo).split("## Notes", 1)[1]
        self.assertIn("[guess]", section)
        self.assertIn("[CLIENT SIGNATURE]", section)

    def test_brief_supplied_signature_line_stays_brief(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        brief["notes"] = "Please sign. Client: [CLIENT SIGNATURE]."
        beo = draft(brief)
        self.assertEqual(beo["notes"], brief["notes"])
        self.assertEqual(beo["provenance"]["notes.copied"]["source"], "brief")
        self.assertNotIn("notes.signoff", beo["provenance"])
        section = render_markdown(beo).split("## Notes", 1)[1]
        sign_line = [line for line in section.splitlines() if "[CLIENT SIGNATURE]" in line][0]
        self.assertNotIn("[guess]", sign_line)

    def test_guarantee_and_vegan_overrides_are_clamped(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        zero = apply_headcount_overrides(brief, guarantee=0, vegan=0)
        self.assertEqual(zero["headcount"]["guarantee"], 0)
        ceiling = apply_headcount_overrides(brief, guarantee=10000, vegan=10000)
        self.assertEqual(ceiling["headcount"]["guarantee"], 10000)
        for bad in (-1, 10001, "12.5", "abc", True, 1.5):
            with self.subTest(value=bad):
                with self.assertRaisesRegex(ValueError, "guarantee must be an integer from 0 to 10000"):
                    apply_headcount_overrides(brief, guarantee=bad)
                with self.assertRaisesRegex(ValueError, "vegan must be an integer from 0 to 10000"):
                    apply_headcount_overrides(brief, vegan=bad)

    def test_malformed_expected_values_are_rejected(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        for raw in ("Expected: -1", "Expected: 1e3", "Expected: 12.5"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "expected must be an integer from 0 to 10000"):
                    parse_pasted_input(raw, base=brief)
        ok = parse_pasted_input("Expected: 180\n", base=brief)
        self.assertEqual(ok["headcount"]["expected"], 180)

    def test_malformed_guarantee_and_vegan_values_are_rejected(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        for raw in ("Guarantee: -1", "Guarantee: 1e3", "Guarantee: 12.5"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "guarantee must be an integer from 0 to 10000"):
                    parse_pasted_input(raw, base=brief)
        for raw in ("Vegan: -1", "Vegan: 1e3", "Vegan: 12.5"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "vegan must be an integer from 0 to 10000"):
                    parse_pasted_input(raw, base=brief)
        ok = parse_pasted_input("Guarantee: 180\nVegan: 10\n", base=brief)
        self.assertEqual(ok["headcount"]["guarantee"], 180)
        vegan = [item for item in ok["dietary"] if item.get("need") == "vegan"]
        self.assertEqual(vegan[0]["count"], 10)

    def test_paste_input_has_a_64kib_cap(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        over = "Guarantee: 180\n" + ("x" * (MAX_PASTE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "at most 65536 bytes"):
            parse_pasted_input(over, base=brief)
        ok = parse_pasted_input("Guarantee: 180\n", base=brief)
        self.assertEqual(ok["headcount"]["guarantee"], 180)


class CliErrorTests(unittest.TestCase):
    def test_cli_prints_only_the_error_message(self):
        stderr = io.StringIO()
        previous = sys.stderr
        try:
            sys.stderr = stderr
            with tempfile.TemporaryDirectory() as tmp:
                code = main(["draft", str(Path(tmp) / "missing-brief.json"), "--out", tmp])
        finally:
            sys.stderr = previous
        err = stderr.getvalue()
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("File \"", err)
        self.assertNotIn("missing-brief.json", err)
        self.assertTrue(err.strip())

    def test_safe_error_scrubs_file_urls(self):
        message = _safe_error(ValueError("cannot read file:///tmp/secret-brief.json today"))
        self.assertIn("[path]", message)
        self.assertNotIn("secret-brief", message)
        self.assertNotIn("file:", message.lower())
        win = _safe_error(OSError("failed file://C:/Users/demo/secret.json"))
        self.assertIn("[path]", win)
        self.assertNotIn("secret.json", win)
        self.assertNotIn("file:", win.lower())

    def test_safe_error_scrubs_relative_home_and_env_paths(self):
        relative = _safe_error(ValueError("cannot read out/../secret-brief.json"))
        self.assertIn("[path]", relative)
        self.assertNotIn("secret-brief", relative)
        home = _safe_error(ValueError("cannot read ~/secret-brief.json"))
        self.assertIn("[path]", home)
        self.assertNotIn("secret-brief", home)
        env = _safe_error(ValueError("cannot read $HOME/secret-brief.json"))
        self.assertIn("[path]", env)
        self.assertNotIn("secret-brief", env)
        win_env = _safe_error(ValueError("cannot read %USERPROFILE%/secret-brief.json"))
        self.assertIn("[path]", win_env)
        self.assertNotIn("secret-brief", win_env)
        assigned = _safe_error(ValueError("path=/tmp/secret-brief.json"))
        self.assertIn("[path]", assigned)
        self.assertNotIn("secret-brief", assigned)
        secret = _safe_error(ValueError("OLLAMA_API_KEY=sk-live-example"))
        self.assertIn("[redacted]", secret)
        self.assertNotIn("sk-live", secret)
        useful = _safe_error(ValueError("ollama is not available over HTTP unless DEMO_ALLOW_OLLAMA=1"))
        self.assertIn("DEMO_ALLOW_OLLAMA=1", useful)
        self.assertEqual(
            public_error_message(ValueError("cannot read out/../secret-brief.json")),
            "could not draft this request",
        )
        self.assertIn("DEMO_ALLOW_OLLAMA=1", public_error_message(
            ValueError("ollama is not available over HTTP unless DEMO_ALLOW_OLLAMA=1")
        ))

    def test_cli_rejects_an_escaping_stem(self):
        stderr = io.StringIO()
        previous = sys.stderr
        brief = ROOT / "fixtures" / "wedding.json"
        try:
            sys.stderr = stderr
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "out"
                out.mkdir()
                code = main(["draft", str(brief), "--out", str(out), "--stem", "../escaped"])
                self.assertEqual(code, 1)
                self.assertFalse((Path(tmp) / "escaped.json").exists())
                self.assertFalse((out / "escaped.json").exists())
        finally:
            sys.stderr = previous
        err = stderr.getvalue()
        self.assertNotIn("Traceback", err)
        self.assertIn("output stem", err)


if __name__ == "__main__":
    unittest.main()
