"""`calibrate.collect` — the only part of Sift that reads what a config *did* cost.

Fixtures here are synthetic JSONL written into a temp directory. The tests never
read the user's real ~/.claude.
"""

import json
import tempfile
import unittest
from pathlib import Path

import _sift  # noqa: F401
import calibrate


def assistant(cache_read, cache_create, *, input_tokens=0, skill=None,
              version="2.1.229", model="claude-opus-5"):
    r = {
        "type": "assistant",
        "version": version,
        "message": {"model": model, "usage": {
            "input_tokens": input_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_create,
        }},
    }
    if skill:
        r["attributionSkill"] = skill
    return r


def listing(names, initial=True):
    content = "\n".join(f"- {n}: does {n} things" for n in names)
    return {"type": "attachment",
            "attachment": {"type": "skill_listing", "isInitial": initial,
                           "skillCount": len(names), "names": names, "content": content}}


def write_session(root, name, records):
    p = Path(root) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


class TestCollect(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)

        # Session A: cold prefix 12,000, 3 turns, `alpha` fires twice.
        write_session(self.root, "proj-a/s1.jsonl", [
            listing(["alpha", "beta", "gamma"]),
            {"type": "user", "message": {"content": "hi"}},
            assistant(0, 12000, skill="alpha"),
            assistant(12000, 400, skill="alpha"),
            assistant(12400, 200),
        ])
        # Session B: cold prefix 8,000, 5 turns, `beta` fires once.
        write_session(self.root, "proj-b/s2.jsonl", [
            listing(["alpha", "beta", "gamma"]),
            assistant(0, 8000),
            *[assistant(8000, 100) for _ in range(3)],
            assistant(8300, 100, skill="beta"),
        ])
        self.d = calibrate.collect(self.root)

    def test_the_cold_prefix_is_the_first_call_with_no_cache_read(self):
        """The standing prefix is read from turn 1 of each session, where cache_read == 0."""
        c = self.d["observed_cold_prefix_tokens"]
        self.assertEqual((c["n"], c["min"], c["max"], c["median"]), (2, 8000, 12000, 10000))

    def test_a_resumed_session_contributes_no_cold_prefix(self):
        """A session that starts warm has no cold start to observe, so it is not counted."""
        write_session(self.root, "proj-c/resumed.jsonl", [assistant(50000, 100), assistant(50100, 50)])
        d = calibrate.collect(self.root)
        self.assertEqual(d["observed_cold_prefix_tokens"]["n"], 2)
        self.assertEqual(d["sessions"], 3)

    def test_turns_are_counted_per_session(self):
        """Turns per session drives the cache multiplier, so miscounting inflates the bill."""
        t = self.d["turns_per_session"]
        self.assertEqual((t["median"], t["max"]), (4, 5))
        self.assertEqual(t["mean"], 4.0)
        self.assertEqual(self.d["calls"], 8)

    def test_skills_are_tallied_from_attributionSkill(self):
        """Fire counts come from the log field, not from a guess."""
        s = self.d["skills"]
        self.assertEqual(dict(s["top_fired"]), {"alpha": 2, "beta": 1})
        self.assertEqual(s["ever_fired"], 2)

    def test_the_never_fired_set_is_listed_minus_fired(self):
        """A listed skill that never appears in an attribution is paying rent for nothing."""
        s = self.d["skills"]
        self.assertEqual(s["listed"], 3)
        self.assertEqual(s["never_fired"], 1)
        self.assertEqual(s["never_fired_names"], ["gamma"])

    def test_the_cache_multiplier_is_derived_from_the_measured_median(self):
        """The multiplier reported back to the user is their own median, not our default."""
        self.assertAlmostEqual(self.d["cache_multiplier_for_your_median_session"], 2.3)

    def test_the_input_mix_percentages_sum_to_one_hundred(self):
        """The mix is the evidence for the multiplier; it has to add up."""
        m = self.d["input_token_mix"]
        self.assertAlmostEqual(m["cache_read_pct"] + m["cache_creation_pct"] + m["uncached_pct"],
                               100.0, places=1)
        self.assertEqual(m["total_input_tokens"], 12000 + 400 + 200 + 12000 + 12400
                         + 8000 + 300 + 8000 * 3 + 8300 + 100)

    def test_versions_and_models_are_tallied(self):
        """Cold prefix changes between CLI versions, so the version mix must be visible."""
        self.assertEqual(dict(self.d["claude_code_versions"]), {"2.1.229": 8})
        self.assertEqual(dict(self.d["models"]), {"claude-opus-5": 8})

    def test_a_malformed_line_is_skipped_rather_than_killing_the_run(self):
        """One truncated JSONL line must not lose the other 40,000 records."""
        p = self.root / "proj-a/s1.jsonl"
        p.write_text(p.read_text() + '{"type": "assistant", "message": {tru\n')
        d = calibrate.collect(self.root)
        self.assertEqual(d["calls"], 8)

    def test_a_missing_directory_reports_an_error_instead_of_zeroes(self):
        """No logs is 'I cannot tell you', not 'your skills never fire'."""
        d = calibrate.collect(self.root / "does-not-exist")
        self.assertIn("error", d)
        self.assertNotIn("skills", d)

    def test_render_text_survives_the_error_case_and_the_normal_case(self):
        """The text report never raises on the shapes collect can return."""
        self.assertIn("no session directory", calibrate.render_text({"error": "no session directory at x"}))
        out = calibrate.render_text(self.d)
        self.assertIn("never fired", out)
        self.assertIn("gamma", out)
        self.assertIn("Caveat", out)

    def test_no_sessions_at_all_renders_without_crashing(self):
        """An empty logs directory is a legitimate state, not a traceback."""
        with tempfile.TemporaryDirectory() as td:
            d = calibrate.collect(td)
            self.assertEqual(d["sessions"], 0)
            self.assertIsNone(d["observed_cold_prefix_tokens"]["median"])
            calibrate.render_text(d)


if __name__ == "__main__":
    unittest.main()
