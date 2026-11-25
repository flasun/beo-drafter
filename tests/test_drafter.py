import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from beo_drafter import REQUIRED_SECTIONS
from beo_drafter.checks import conflict_flags
from beo_drafter.clients.mock import (
    MockModelClient,
    bartender_count,
    bartender_ratio,
    compute_staffing,
    derive_guarantee_due,
    resolve_guarantee_due,
    timeline_sort_key,
)
from beo_drafter.drafter import draft, load_brief, write_beo
from beo_drafter.render import SECTION_TITLES, render_markdown
from beo_drafter.schema import validate_brief

FIXTURES = sorted((ROOT / "fixtures").glob("*.json"))
EXPECTED_TYPES = {
    "wedding",
    "corporate breakfast",
    "gala",
    "awards dinner",
    "offsite lunch",
    "cocktail reception",
    "corporate day meeting",
    "awards dinner, buffet",
    "conference day 1",
    "conference day 2",
    "retirement brunch",
}

FIRE_COURSE = re.compile(r"\b(main|lunch|dinner main|boxed lunch|entr[eé]e)\b", re.I)
FIRE_COUNT = re.compile(r"\((\d+)")


def fire_list_counts(brief: dict) -> list[int]:
    """Every parenthetical (N) on a fire-list course, not only the first in each item."""
    counts: list[int] = []
    for course in brief.get("menu", {}).get("courses") or []:
        if not isinstance(course, dict):
            continue
        title = str(course.get("course") or course.get("name") or "")
        if not FIRE_COURSE.search(title):
            continue
        for item in course.get("items") or course.get("dishes") or []:
            counts.extend(int(match) for match in FIRE_COUNT.findall(str(item)))
    return counts


def _brief_dishes(brief: dict) -> list[str]:
    dishes: list[str] = []
    for course in brief["menu"]["courses"]:
        if isinstance(course, str):
            dishes.append(course)
            continue
        items = course.get("items") or course.get("dishes") or []
        if isinstance(items, list):
            dishes.extend(str(item) for item in items)
        else:
            dishes.append(str(items))
    return dishes


class FixtureDraftTests(unittest.TestCase):
    def test_fixtures_present_and_varied(self):
        self.assertEqual(len(FIXTURES), 11, "expected eleven brief fixtures")
        types = {load_brief(path)["event_type"] for path in FIXTURES}
        self.assertEqual(types, EXPECTED_TYPES)

    def test_mock_drafts_all_fixtures_with_required_sections(self):
        client = MockModelClient()
        for path in FIXTURES:
            with self.subTest(fixture=path.name):
                brief = load_brief(path)
                beo = draft(brief, client=client)
                for section in REQUIRED_SECTIONS:
                    self.assertIn(section, beo)
                    self.assertTrue(beo[section], f"{path.name} produced an empty {section}")
                markdown = render_markdown(beo)
                for section in REQUIRED_SECTIONS:
                    heading = f"## {SECTION_TITLES[section]}"
                    self.assertIn(heading, markdown)
                self.assertEqual(beo["source_brief_id"], brief["id"])
                self.assertEqual(beo["overview"]["event_name"], brief["event_name"])
                self.assertEqual(beo["headcount"]["guarantee"], brief["headcount"]["guarantee"])
                self.assertIn("staffing_plan", beo["headcount"])

    def test_mock_is_deterministic(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        client = MockModelClient()
        first = draft(brief, client=client)
        second = draft(brief, client=client)
        self.assertEqual(first, second)

    def test_write_beo_emits_json_and_markdown(self):
        brief = load_brief(ROOT / "fixtures" / "offsite-lunch.json")
        beo = draft(brief)
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_beo(beo, tmp, "offsite-lunch")
            loaded = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(loaded["overview"]["event_name"], brief["event_name"])
            text = paths["markdown"].read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Banquet Event Order:"))

    def test_invalid_brief_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_brief({"event_name": "incomplete"})

    def test_markdown_renders_lists_as_bullets_not_repr(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        markdown = render_markdown(draft(brief))
        self.assertNotIn("['", markdown)
        self.assertNotIn("courses: [", markdown)
        self.assertIn("- First: Chilled peach and tomato soup", markdown)
        self.assertIn("- wireless lavalier for welcome remarks", markdown)

    def test_markdown_escapes_model_html(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        beo = draft(brief)
        beo["overview"]["event_name"] = "<img src=x onerror=alert(1)>"
        beo["notes"] = "<script>alert(1)</script>"
        markdown = render_markdown(beo)
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_beo(beo, tmp, "escaped")
            written = paths["markdown"].read_text(encoding="utf-8")
        for text in (markdown, written):
            self.assertNotIn("<script>", text)
            self.assertNotIn("<img", text)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
            self.assertIn("&lt;img src=x onerror=alert(1)&gt;", text)
            self.assertIn("# Banquet Event Order: &lt;img src=x onerror=alert(1)&gt;", text)


    def test_write_beo_rejects_escaping_stems(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        beo = draft(brief)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            with self.assertRaisesRegex(ValueError, "output stem"):
                write_beo(beo, out, "../escaped")
            self.assertFalse((Path(tmp) / "escaped.json").exists())
            self.assertFalse((out / "escaped.json").exists())
            with self.assertRaisesRegex(ValueError, "output stem"):
                write_beo(beo, out, str(Path(tmp) / "abs-stem"))
            with self.assertRaisesRegex(ValueError, "output stem"):
                write_beo(beo, out, "..")
            with self.assertRaisesRegex(ValueError, "output stem"):
                write_beo(beo, out, ".")
            paths = write_beo(beo, out, "wedding")
            self.assertTrue(paths["json"].is_file())
            self.assertEqual(paths["json"].resolve().parent, out.resolve())
            self.assertTrue(paths["json"].resolve().is_relative_to(out.resolve()))

    def test_gala_overnight_timeline_ends_with_load_out(self):
        brief = load_brief(ROOT / "fixtures" / "gala.json")
        beo = draft(brief)
        timeline = beo["timeline"]
        self.assertEqual(timeline[0]["time"], "10:00")
        self.assertEqual(timeline[0]["item"], "Load-in and room set")
        self.assertEqual(timeline[0]["day_offset"], 0)
        self.assertEqual(timeline[0]["date"], "2027-10-09")
        self.assertEqual(timeline[-1]["time"], "01:00")
        self.assertEqual(timeline[-1]["item"], "Load-out")
        self.assertEqual(timeline[-1]["day_offset"], 1)
        self.assertEqual(timeline[-1]["date"], "2027-10-10")
        markdown = render_markdown(beo)
        first = next(line for line in markdown.splitlines() if line.startswith("- ") and "—" in line)
        self.assertTrue(first.startswith("- 2027-10-09 10:00 — Load-in"))
        self.assertIn("- 2027-10-10 01:00 (next day) — Load-out", markdown)

    def test_timelines_are_wrap_aware_not_reversed(self):
        for path in FIXTURES:
            with self.subTest(fixture=path.name):
                brief = load_brief(path)
                beo = draft(brief)
                load_in = brief["timing"]["load_in"]
                keys = [timeline_sort_key(load_in, row) for row in beo["timeline"]]
                self.assertEqual(keys, sorted(keys), "timeline is not in wrap-aware order")
                self.assertEqual(
                    beo["timeline"][0]["item"],
                    brief["timing"].get("load_in_item") or "Load-in and room set",
                )
                self.assertEqual(
                    beo["timeline"][-1]["item"],
                    brief["timing"].get("load_out_item") or "Load-out",
                )

    def test_menu_items_come_from_the_brief(self):
        for path in FIXTURES:
            with self.subTest(fixture=path.name):
                brief = load_brief(path)
                beo = draft(brief)
                allowed = _brief_dishes(brief)
                joined = " ".join(beo["menu"]["courses"])
                for dish in allowed:
                    self.assertIn(dish, joined)
                # Item strings may contain commas (allergen notes, fire-list counts).
                # Check that each brief dish is a substring of the rendered courses.
                self.assertTrue(allowed)

    def test_staffing_plan_follows_service_style_and_bar(self):
        for path in FIXTURES:
            with self.subTest(fixture=path.name):
                brief = load_brief(path)
                menu = brief["menu"]
                bar = menu.get("bar") if isinstance(menu.get("bar"), dict) else None
                expected = compute_staffing(
                    brief["headcount"],
                    style=str(menu.get("style") or ""),
                    event_type=str(brief.get("event_type") or ""),
                    bar=bar,
                    menu=menu,
                )
                beo = draft(brief)
                plan = beo["headcount"]["staffing_plan"]
                if expected["split"]:
                    self.assertIn("daytime (buffet)", plan)
                    self.assertIn("1:20 planning ratio", plan)
                    self.assertIn("1:16 planning ratio", plan)
                    self.assertIn("(plated)", plan)
                else:
                    match = re.search(
                        rf"(\d+) server\(s\) at a 1:{expected['ratio']} planning ratio",
                        plan,
                    )
                    self.assertIsNotNone(match, plan)
                    self.assertEqual(int(match.group(1)), expected["servers"])
                self.assertEqual(beo["headcount"]["staff_meals"], expected["staff_meals"])
                if bar:
                    self.assertIn("bartender(s) from the bar plan", plan)
                    self.assertEqual(expected["bartenders"], int(bar.get("bartenders") or expected["bartenders"]))
                else:
                    self.assertIn("no bartenders (no bar plan)", plan)
                    self.assertEqual(expected["bartenders"], 0)
                if expected["carving_attendants"]:
                    self.assertIn("carving attendant", plan)

    def test_omitted_staff_meals_uses_computed_count(self):
        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        brief["headcount"] = dict(brief["headcount"])
        brief["headcount"].pop("staff_meals")
        menu = brief["menu"]
        bar = menu.get("bar") if isinstance(menu.get("bar"), dict) else None
        beo = draft(brief)
        computed = compute_staffing(
            {"guarantee": 120},
            style=str(menu.get("style") or ""),
            event_type=str(brief.get("event_type") or ""),
            bar=bar,
        )
        self.assertEqual(computed["staff_meals"], 14)
        self.assertEqual(computed["bartenders"], 2)
        self.assertEqual(beo["headcount"]["staff_meals"], 14)
        self.assertIn("14 staff meals", beo["headcount"]["staffing_plan"])

    def test_dietary_needs_counts_and_notes_carry_through(self):
        for path in FIXTURES:
            with self.subTest(fixture=path.name):
                brief = load_brief(path)
                lines = draft(brief)["dietary"]
                joined = "\n".join(lines)
                for item in brief["dietary"]:
                    if isinstance(item, str):
                        self.assertIn(item, joined)
                        continue
                    self.assertIn(item["need"], joined)
                    self.assertTrue(any(str(item["count"]) in line and item["need"] in line for line in lines))
                    if item.get("note"):
                        self.assertIn(item["note"], joined)
        wedding = draft(load_brief(ROOT / "fixtures" / "wedding.json"))
        nut = "\n".join(wedding["dietary"])
        self.assertIn("nut-free kitchen protocol", nut)
        self.assertIn("severe allergy", nut)
        self.assertIn("no shared fryer", nut)
        self.assertTrue(wedding["dietary"][0].startswith("**SEVERE ALLERGY**"))

    def test_default_status_is_draft_not_approved(self):
        for path in FIXTURES:
            with self.subTest(fixture=path.name):
                beo = draft(load_brief(path))
                self.assertEqual(beo["overview"]["status"], "draft")
                markdown = render_markdown(beo)
                self.assertIn("**UNAPPROVED DRAFT**", markdown)
                self.assertNotIn("**APPROVED**", markdown)

    def test_draft_force_draft_overrides_stub_approved_status(self):
        """Fails if drafter.draft() stops calling validate_beo(..., force_draft=True)."""

        class ApprovedStub(MockModelClient):
            def draft_beo(self, brief):
                beo = super().draft_beo(brief)
                beo.setdefault("overview", {})["status"] = "approved"
                return beo

        brief = load_brief(ROOT / "fixtures" / "wedding.json")
        raw = ApprovedStub().draft_beo(brief)
        self.assertEqual(raw["overview"]["status"], "approved")
        beo = draft(brief, client=ApprovedStub())
        self.assertEqual(beo["overview"]["status"], "draft")
        self.assertIn("**UNAPPROVED DRAFT**", render_markdown(beo))

    def test_seating_and_overage_conflicts_are_flagged(self):
        wedding = draft(load_brief(ROOT / "fixtures" / "wedding.json"))
        wedding_flags = wedding["headcount"]["conflicts"]
        self.assertTrue(any("128" in flag and "122" in flag for flag in wedding_flags))
        self.assertIn("Expected 128 guests exceed seat capacity of 122.", render_markdown(wedding))

        gala = draft(load_brief(ROOT / "fixtures" / "gala.json"))
        gala_flags = gala["headcount"]["conflicts"]
        self.assertTrue(any("300" in flag and "122" not in flag for flag in gala_flags))
        self.assertIn("Expected 300 guests exceed seat capacity of 280.", "\n".join(gala_flags))
        self.assertIn("5% overage cap", "\n".join(gala_flags))
        self.assertIn("cap 14", "\n".join(gala_flags))
        markdown = render_markdown(gala)
        self.assertIn("Expected 300 guests exceed the 5% overage cap", markdown)

        lunch = draft(load_brief(ROOT / "fixtures" / "offsite-lunch.json"))
        self.assertEqual(conflict_flags(load_brief(ROOT / "fixtures" / "offsite-lunch.json")), [])
        self.assertEqual(lunch["headcount"]["conflicts"], [])

    def test_only_wedding_and_gala_have_intentional_conflicts(self):
        flagged = []
        for path in FIXTURES:
            flags = conflict_flags(load_brief(path))
            if flags:
                flagged.append(path.stem)
        self.assertEqual(sorted(flagged), ["gala", "wedding"])

    def test_entree_and_dietary_counts_add_up_for_every_fixture(self):
        for path in FIXTURES:
            with self.subTest(fixture=path.name):
                brief = load_brief(path)
                guarantee = int(brief["headcount"]["guarantee"])
                counts = fire_list_counts(brief)
                if counts:
                    self.assertEqual(sum(counts), guarantee, f"{path.name} fire list {counts}")
                dietary_total = 0
                for item in brief["dietary"]:
                    if isinstance(item, dict) and item.get("count") not in (None, ""):
                        dietary_total += int(item["count"])
                self.assertLessEqual(dietary_total, guarantee, path.name)

    def test_guarantee_due_is_derived_and_printed(self):
        wedding = draft(load_brief(ROOT / "fixtures" / "wedding.json"))
        self.assertEqual(wedding["billing"]["guarantee_due"], "2027-06-09 12:00")
        self.assertIn("guarantee_due: 2027-06-09 12:00", render_markdown(wedding))
        self.assertEqual(derive_guarantee_due("2027-10-09", "7-day guarantee, 5% overage cap"), "2027-10-02 12:00")
        self.assertEqual(derive_guarantee_due("2027-03-05", "24-hour guarantee"), "2027-03-04 12:00")
        gala = draft(load_brief(ROOT / "fixtures" / "gala.json"))
        self.assertEqual(gala["billing"]["guarantee_due"], "2027-10-02 12:00")

    def test_package_guarantee_due_uses_start_date_unless_overridden(self):
        day1 = draft(load_brief(ROOT / "fixtures" / "conference-001a.json"))
        day2 = draft(load_brief(ROOT / "fixtures" / "conference-001b.json"))
        self.assertEqual(day1["billing"]["guarantee_due"], "2027-07-08 12:00")
        self.assertEqual(day2["billing"]["guarantee_due"], "2027-07-08 12:00")
        self.assertIn("guarantee_due: 2027-07-08 12:00", render_markdown(day1))
        self.assertIn("guarantee_due: 2027-07-08 12:00", render_markdown(day2))
        overridden = load_brief(ROOT / "fixtures" / "conference-001b.json")
        overridden["billing"] = dict(overridden["billing"])
        overridden["billing"]["guarantee_due"] = "2027-01-01 12:00"
        self.assertEqual(resolve_guarantee_due(overridden), "2027-01-01 12:00")
        self.assertEqual(draft(overridden)["billing"]["guarantee_due"], "2027-01-01 12:00")

    def test_conference_package_id_is_shared_on_both_beos(self):
        day1 = draft(load_brief(ROOT / "fixtures" / "conference-001a.json"))
        day2 = draft(load_brief(ROOT / "fixtures" / "conference-001b.json"))
        self.assertEqual(day1["overview"]["package_id"], "synth-conference-001")
        self.assertEqual(day2["overview"]["package_id"], "synth-conference-001")
        self.assertNotEqual(day1["overview"]["beo_id"], day2["overview"]["beo_id"])
        self.assertIn("package_id: synth-conference-001", render_markdown(day1))

    def test_timeline_prints_room_and_function(self):
        gala = draft(load_brief(ROOT / "fixtures" / "gala.json"))
        markdown = render_markdown(gala)
        self.assertIn("Anteroom", markdown)
        self.assertIn("Reception", markdown)
        self.assertTrue(
            any(
                "18:00" in line and "Anteroom" in line and "Reception" in line
                for line in markdown.splitlines()
            ),
            markdown,
        )

    def test_awards_timeline_includes_service_beats(self):
        beo = draft(load_brief(ROOT / "fixtures" / "awards-dinner.json"))
        items = " ".join(row["item"] for row in beo["timeline"])
        for beat in ("Staff meal", "Tech check", "First-course drop", "Entrée fire", "Dessert", "Last call"):
            self.assertIn(beat, items)
        times = [row["time"] for row in beo["timeline"]]
        self.assertLess(times.index("16:00"), times.index("18:25"))
        self.assertLess(times.index("18:45"), times.index("19:30"))
        self.assertLess(times.index("20:45"), times.index("21:00"))

    def test_fire_list_sums_every_parenthetical_in_an_item(self):
        brief = {
            "headcount": {"guarantee": 80},
            "menu": {"courses": [{"course": "Main", "items": ["chicken (80) and fish (40)"]}]},
            "dietary": [],
        }
        counts = fire_list_counts(brief)
        self.assertEqual(counts, [80, 40])
        self.assertEqual(sum(counts), 120)
        self.assertNotEqual(sum(counts), int(brief["headcount"]["guarantee"]))

    def test_bartender_ratio_follows_the_bar_plan(self):
        spirits = {"style": "open bar", "inclusions": "a short spirits list"}
        self.assertEqual(bartender_ratio(spirits), 50)
        self.assertEqual(bartender_count(100, spirits), 2)
        signature = {"style": "hosted beer, wine, and a signature cocktail", "signature_drink": "Plover Spritz"}
        self.assertEqual(bartender_ratio(signature), 50)
        beer_wine = {"style": "hosted beer and wine", "inclusions": "beer and wine only; no spirits"}
        self.assertEqual(bartender_ratio(beer_wine), 75)
        self.assertEqual(bartender_count(150, beer_wine), 2)
        self.assertEqual(bartender_count(75, beer_wine), 1)
        for phrase in ("full bar", "hosted bar", "cash bar", "open bar"):
            self.assertEqual(bartender_ratio({"style": phrase}), 50, phrase)
        self.assertEqual(bartender_count(51, {"style": "open bar"}), 2)
        self.assertEqual(bartender_count(51, {"style": "hosted bar"}), 2)
        other = {"style": "wine service"}
        self.assertEqual(bartender_ratio(other), 60)
        self.assertEqual(bartender_count(60, other), 1)
        self.assertEqual(bartender_count(120, {"bartenders": 3, "style": "hosted beer and wine"}), 3)

    def test_cocktail_staffing_uses_one_to_twenty(self):
        brief = load_brief(ROOT / "fixtures" / "cocktail.json")
        beo = draft(brief)
        plan = beo["headcount"]["staffing_plan"]
        self.assertIn("1:20 planning ratio", plan)
        self.assertNotIn("1:12 planning ratio", plan)
        self.assertNotIn("1:16 planning ratio", plan)

    def test_mixed_functions_print_a_staffing_split(self):
        for name, plated in (("conference-001a.json", "dinner"), ("day-meeting.json", "lunch")):
            with self.subTest(fixture=name):
                plan = draft(load_brief(ROOT / "fixtures" / name))["headcount"]["staffing_plan"]
                self.assertIn("daytime (buffet)", plan)
                self.assertIn(f"{plated} (plated)", plan)
                self.assertIn("1:20 planning ratio", plan)
                self.assertIn("1:16 planning ratio", plan)

    def test_conference_day1_load_out_and_dinner_beats(self):
        beo = draft(load_brief(ROOT / "fixtures" / "conference-001a.json"))
        self.assertEqual(
            beo["timeline"][-1]["item"],
            "F&B and reception-furniture load-out only; room held overnight",
        )
        items = " ".join(row["item"] for row in beo["timeline"])
        self.assertIn("First-course drop", items)
        self.assertIn("Entrée fire", items)
        self.assertNotEqual(beo["timeline"][-1]["item"], "Load-out")

    def test_conference_day2_overnight_hold_matches_load_in(self):
        brief = load_brief(ROOT / "fixtures" / "conference-001b.json")
        beo = draft(brief)
        self.assertEqual(beo["timeline"][0]["time"], "06:00")
        self.assertEqual(beo["timeline"][0]["item"], "Overnight hold resumes; breakfast set in Meadow Hall")
        self.assertEqual(beo["timeline"][-1]["item"], "Full load-out, including overnight hold")
        six_am = [row["item"] for row in beo["timeline"] if row["time"] == "06:00"]
        self.assertEqual(six_am, ["Overnight hold resumes; breakfast set in Meadow Hall"])

    def test_cocktail_timeline_includes_tech_check(self):
        items = " ".join(row["item"] for row in draft(load_brief(ROOT / "fixtures" / "cocktail.json"))["timeline"])
        self.assertIn("Tech check", items)

    def test_awards_buffet_plan_includes_a_carving_attendant(self):
        plan = draft(load_brief(ROOT / "fixtures" / "awards-buffet.json"))["headcount"]["staffing_plan"]
        self.assertIn("1 carving attendant", plan)

    def test_brunch_bar_hours_are_the_remarks_not_a_ceremony(self):
        brief = load_brief(ROOT / "fixtures" / "brunch.json")
        beo = draft(brief)
        self.assertIn("11:45 remarks", brief["menu"]["bar"]["hours"])
        self.assertNotIn("ceremony", brief["menu"]["bar"]["hours"])
        self.assertIn("11:45 remarks", beo["menu"]["bar"]["hours"])

    def test_cocktail_venue_was_renamed_off_mossglen(self):
        brief = load_brief(ROOT / "fixtures" / "cocktail.json")
        self.assertEqual(brief["venue"]["name"], "Fernmoss Assembly Rooms")
        self.assertNotIn("Mossglen", json.dumps(brief))
        markdown = render_markdown(draft(brief))
        self.assertIn("Fernmoss Assembly Rooms", markdown)
        self.assertNotIn("Mossglen", markdown)

    def test_packaged_brief_requires_package_start_or_explicit_due(self):
        brief = load_brief(ROOT / "fixtures" / "conference-001b.json")
        brief = json.loads(json.dumps(brief))
        brief.pop("package_start", None)
        brief.pop("package_start_date", None)
        brief["billing"].pop("guarantee_due", None)
        with self.assertRaises(ValueError) as missing:
            resolve_guarantee_due(brief)
        self.assertIn("package_start", str(missing.exception))
        with self.assertRaises(ValueError):
            draft(brief)
        brief["package_start"] = ""
        with self.assertRaises(ValueError):
            resolve_guarantee_due(brief)

    def test_package_start_after_event_date_is_rejected(self):
        brief = load_brief(ROOT / "fixtures" / "conference-001a.json")
        brief = json.loads(json.dumps(brief))
        brief["package_start"] = "2027-07-22"
        with self.assertRaises(ValueError) as later:
            resolve_guarantee_due(brief)
        self.assertIn("after the event date", str(later.exception))
        with self.assertRaises(ValueError):
            draft(brief)

    def test_mixed_shift_staff_meals_count_both_crews(self):
        brief = load_brief(ROOT / "fixtures" / "conference-001a.json")
        brief["headcount"] = dict(brief["headcount"])
        brief["headcount"].pop("staff_meals")
        computed = compute_staffing(
            brief["headcount"],
            style=str(brief["menu"].get("style") or ""),
            event_type=str(brief.get("event_type") or ""),
            bar=brief["menu"].get("bar") if isinstance(brief["menu"].get("bar"), dict) else None,
            menu=brief["menu"],
        )
        self.assertEqual(computed["daytime_servers"], 4)
        self.assertEqual(computed["plated_servers"], 5)
        self.assertEqual(computed["staff_meals"], 15)
        beo = draft(brief)
        self.assertEqual(beo["headcount"]["staff_meals"], 15)
        self.assertIn("15 staff meals", beo["headcount"]["staffing_plan"])
        # The fixture override stays 14 and must not silently replace computed 15.
        listed = draft(load_brief(ROOT / "fixtures" / "conference-001a.json"))
        self.assertEqual(listed["headcount"]["staff_meals"], 14)
        self.assertIn("computed floor-team meals are 15", listed["headcount"]["staffing_plan"])

    def test_cocktail_staff_meals_match_the_computed_count(self):
        brief = load_brief(ROOT / "fixtures" / "cocktail.json")
        self.assertEqual(brief["headcount"]["staff_meals"], 16)
        beo = draft(brief)
        self.assertEqual(beo["headcount"]["staff_meals"], 16)
        self.assertIn("16 staff meals", beo["headcount"]["staffing_plan"])
        self.assertNotIn("computed floor-team meals", beo["headcount"]["staffing_plan"])

    def test_specific_milestone_hides_generic_event_end(self):
        for name, time, text in (
            ("conference-001a.json", "21:00", "Event end; F&B load-out only, room held overnight"),
            ("conference-001b.json", "13:00", "Event end; guests depart"),
        ):
            with self.subTest(fixture=name):
                beo = draft(load_brief(ROOT / "fixtures" / name))
                at_end = [row for row in beo["timeline"] if row["time"] == time]
                items = [row["item"] for row in at_end]
                self.assertEqual(items, [text])
                self.assertNotIn("Event end", [row["item"] for row in beo["timeline"]])

    def test_plated_mains_include_starch_vegetable_and_sauce(self):
        for name in ("wedding.json", "awards-dinner.json", "gala.json"):
            with self.subTest(fixture=name):
                brief = load_brief(ROOT / "fixtures" / name)
                mains = []
                for course in brief["menu"]["courses"]:
                    if isinstance(course, dict) and str(course.get("course") or "").lower() == "main":
                        mains.extend(str(item).lower() for item in course.get("items") or [])
                self.assertTrue(mains)
                starch = ("potato", "potatoes", "rice", "farro", "bread")
                vegetable = ("green", "carrot", "root", "vegetable", "asparagus")
                sauce = ("jus", "sauce", "oil", "vinaigrette")
                for item in mains:
                    self.assertTrue(any(word in item for word in starch), item)
                    self.assertTrue(any(word in item for word in vegetable), item)
                    self.assertTrue(any(word in item for word in sauce), item)


if __name__ == "__main__":
    unittest.main()
