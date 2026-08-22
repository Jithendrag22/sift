"""The `sift` executable itself — exit codes and output shape are the CI contract."""

import json
import tempfile
import unittest
from pathlib import Path

from _sift import commit_all, git_init, run_cli


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        (self.root / "CLAUDE.md").write_text("# instructions\n" + "line\n" * 20)

    def test_measure_prints_parseable_json(self):
        """`sift measure` is machine-readable, because other tools will consume it."""
        rc, out, err = run_cli("measure", str(self.root))
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertGreater(d["always_on_tokens"], 0)
        self.assertTrue(d["estimate"], "output must declare that the numbers are estimates")

    def test_init_seeds_a_budget_with_headroom_so_adoption_does_not_fail_the_build(self):
        """`sift init` writes a budget above the current size, never below it."""
        rc, out, err = run_cli("init", str(self.root))
        self.assertEqual(rc, 0, err)
        b = json.loads((self.root / "sift.budget.json").read_text())
        current = json.loads(run_cli("measure", str(self.root))[1])["always_on_tokens"]
        self.assertGreaterEqual(b["always_on_tokens"], current)
        self.assertGreaterEqual(b["always_on_tokens"], 1000)
        self.assertEqual(run_cli("check", str(self.root))[0], 0)

    def test_check_exits_1_when_over_budget(self):
        """Over the committed ceiling fails the build; that is the whole point."""
        (self.root / "sift.budget.json").write_text('{"always_on_tokens": 1}')
        rc, out, err = run_cli("check", str(self.root))
        self.assertEqual(rc, 1)
        self.assertIn("fail", out)

    def test_check_exits_0_when_under_budget(self):
        """A green build stays green."""
        (self.root / "sift.budget.json").write_text('{"always_on_tokens": 100000}')
        self.assertEqual(run_cli("check", str(self.root))[0], 0)

    def test_a_warn_does_not_fail_the_build(self):
        """Warn is advisory; only fail blocks, so teams can adopt without pain."""
        current = json.loads(run_cli("measure", str(self.root))[1])["always_on_tokens"]
        (self.root / "sift.budget.json").write_text(
            json.dumps({"always_on_tokens": int(current * 1.1) + 1}))
        rc, out, _ = run_cli("check", str(self.root))
        self.assertEqual(rc, 0)
        self.assertIn("warn", out)

    def test_comment_renders_markdown_with_the_honesty_footnote(self):
        """Every rendered comment carries the caveat that these are estimates."""
        rc, out, err = run_cli("comment", str(self.root))
        self.assertEqual(rc, 0, err)
        self.assertIn("Context budget", out)
        self.assertIn("estimates", out)

    def test_help_exits_0_and_lists_every_command(self):
        """`sift --help` documents the commands it actually implements."""
        rc, out, _ = run_cli("--help")
        self.assertEqual(rc, 0)
        for cmd in ("measure", "check", "comment", "init", "calibrate"):
            self.assertIn(cmd, out)

    def test_calibrate_json_flag_is_not_mistaken_for_a_log_directory(self):
        """`sift calibrate DIR --json` reads DIR; the flag is not mistaken for the path."""
        rc, out, err = run_cli("calibrate", str(self.root), "--json")
        self.assertEqual(rc, 0, err)
        d = json.loads(out)
        self.assertNotIn("error", d, "the flag was treated as the log directory")
        self.assertEqual(d["sessions"], 0)

    def test_check_on_a_repo_with_no_change_against_its_own_head_passes(self):
        """Diffing a ref against itself is a zero delta, not a spurious failure."""
        git_init(self.root)
        sha = commit_all(self.root, "initial")
        rc, out, err = run_cli("check", str(self.root), "--base", sha)
        self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main()
