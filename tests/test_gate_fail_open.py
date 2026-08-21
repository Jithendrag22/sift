"""The fail-open regression. This bug shipped once; it must never ship again.

A shallow clone (`actions/checkout@v4` default `fetch-depth: 1`) does not contain
the pull request's base SHA. The gate used to treat that as "no change" and exit 0,
passing every pull request on the default CI configuration.
"""

import tempfile
import unittest
from pathlib import Path

from _sift import commit_all, git, git_init, run_cli

import budget


BIG = "x" * 9000  # ~2,500 always-on tokens, far over the 500-token ratchet


class ShallowCloneFixture:
    """A real origin repo with two commits, plus a depth-1 clone of it."""

    def __init__(self, stack):
        td = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.origin = git_init(td / "origin")
        (self.origin / "CLAUDE.md").write_text("# base config\n")
        (self.origin / "sift.budget.json").write_text(
            '{"always_on_tokens": 5000, "max_increase_tokens": 500}\n')
        self.base_sha = commit_all(self.origin, "base")
        (self.origin / "CLAUDE.md").write_text("# base config\n" + BIG + "\n")
        self.head_sha = commit_all(self.origin, "head: a very large CLAUDE.md")

        self.shallow = td / "shallow"
        git("clone", "-q", "--depth=1", f"file://{self.origin}", str(self.shallow), cwd=td)

    def cut_off_from_origin(self):
        """Make the base SHA genuinely unfetchable, as it is in a real CI clone."""
        git("remote", "remove", "origin", cwd=self.shallow)


class TestBaseRefUnavailable(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.fx = ShallowCloneFixture(self.stack)

    def test_base_sha_is_genuinely_absent_from_the_shallow_clone(self):
        """The fixture is honest: the base commit really is missing from the clone."""
        r = git("cat-file", "-e", self.fx.base_sha + "^{commit}",
                cwd=self.fx.shallow, check=False)
        self.assertNotEqual(r.returncode, 0,
                            "fixture is broken — the shallow clone already has the base SHA")

    def test_measure_ref_raises_when_the_base_ref_cannot_be_resolved(self):
        """measure_ref raises BaseUnavailable rather than returning None on a missing base."""
        self.fx.cut_off_from_origin()
        with self.assertRaises(budget.BaseUnavailable):
            budget.measure_ref(self.fx.shallow, self.fx.base_sha)

    def test_check_exits_2_not_0_when_the_base_is_missing(self):
        """`sift check --base` exits 2 on a shallow clone — never 0, which would fail open."""
        self.fx.cut_off_from_origin()
        rc, out, err = run_cli("check", str(self.fx.shallow), "--base", self.fx.base_sha)
        self.assertEqual(rc, 2, f"gate failed open: rc={rc}\nstdout={out}\nstderr={err}")
        self.assertIn("fetch-depth", err, "the error must tell the user how to fix their checkout")

    def test_comment_also_exits_2_when_the_base_is_missing(self):
        """`sift comment` refuses to render a diff it cannot compute."""
        self.fx.cut_off_from_origin()
        rc, out, err = run_cli("comment", str(self.fx.shallow), "--base", self.fx.base_sha)
        self.assertEqual(rc, 2, f"rendered a comment without a baseline: {out}")

    def test_unknown_ref_that_never_existed_also_exits_2(self):
        """A base ref that does not exist anywhere is an error, not a silent pass."""
        rc, out, err = run_cli("check", str(self.fx.shallow), "--base", "deadbeef" * 5)
        self.assertEqual(rc, 2, f"unknown ref did not error: rc={rc} {out} {err}")

    def test_the_same_change_fails_loudly_when_the_base_is_reachable(self):
        """With the base present the ratchet fires: exit 1, so exit 2 is the honest contrast."""
        git("fetch", "-q", "--depth=1", "origin", self.fx.base_sha, cwd=self.fx.shallow)
        rc, out, err = run_cli("check", str(self.fx.shallow), "--base", self.fx.base_sha)
        self.assertEqual(rc, 1, f"expected a ratchet failure, got rc={rc}\n{out}{err}")
        self.assertIn("always-on tokens", out)

    def test_check_without_a_base_still_works_on_a_shallow_clone(self):
        """Absolute-ceiling checking needs no baseline, so it must not be broken by the fix."""
        self.fx.cut_off_from_origin()
        rc, out, err = run_cli("check", str(self.fx.shallow))
        self.assertIn(rc, (0, 1), f"rc={rc} {err}")
        self.assertNotIn("error:", err)


if __name__ == "__main__":
    unittest.main()
