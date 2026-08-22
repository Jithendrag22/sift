"""The --share blob must never leak anything the operator did not opt into.

This is a privacy promise made in the README, so it gets a test rather than a
comment. A comment cannot fail the build.
"""
import json
import unittest

import _sift  # noqa: F401  (puts src/ on the path)
import calibrate


def _fixture():
    return {
        "sessions": 3,
        "calls": 120,
        "turns_per_session": {"median": 40, "mean": 40.0, "max": 60},
        "observed_cold_prefix_tokens": {"n": 3, "median": 16000, "min": 8000, "max": 30000},
        "input_token_mix": {"cache_read_pct": 95.2, "cache_creation_pct": 4.6,
                            "uncached_pct": 0.2, "total_input_tokens": 1234,
                            "price_ratio_vs_single_uncached_read": 4.0},
        "cache_multiplier_for_your_median_session": 5.9,
        "skills": {
            "listed": 4, "ever_fired": 1, "never_fired": 3,
            "top_fired": [("acme-internal-billing", 12)],
            "never_fired_names": ["project-titan-launch", "client-northwind", "secret-thing"],
        },
        "claude_code_versions": [("2.1.175", 50)],
        "models": [("claude-opus-5", 50)],
    }


class TestSharePrivacy(unittest.TestCase):
    def test_no_skill_names_by_default(self):
        """Skill names can leak a product, a client or an unannounced project."""
        blob = calibrate.share_blob(_fixture())
        text = json.dumps(blob)
        for secret in ("acme-internal-billing", "project-titan-launch",
                       "client-northwind", "secret-thing"):
            self.assertNotIn(secret, text, f"{secret!r} leaked into the default share blob")

    def test_names_included_only_when_asked(self):
        """--names is a deliberate act, and it must actually work when chosen."""
        blob = calibrate.share_blob(_fixture(), include_names=True)
        text = json.dumps(blob)
        self.assertIn("acme-internal-billing", text)
        self.assertIn("project-titan-launch", text)

    def test_no_paths_or_home_directory_ever(self):
        """A filesystem path identifies a machine and often a person."""
        for names in (False, True):
            text = json.dumps(calibrate.share_blob(_fixture(), include_names=names))
            self.assertNotIn("/Users/", text)
            self.assertNotIn("/home/", text)
            self.assertNotIn("C:\\", text)

    def test_fire_counts_survive_without_names(self):
        """The distribution is the useful part, and it must not be lost with the names."""
        blob = calibrate.share_blob(_fixture())
        self.assertEqual(blob["fire_counts"], [12])
        self.assertEqual(blob["skills_listed"], 4)
        self.assertEqual(blob["skills_never_fired"], 3)

    def test_schema_is_versioned(self):
        """Aggregating submissions later requires knowing which shape each one is."""
        self.assertEqual(calibrate.share_blob(_fixture())["schema"], "sift.share/1")


if __name__ == "__main__":
    unittest.main()
