"""The always-on / on-demand split, and the arithmetic built on top of it.

This is the product's central claim: a skill's *description* is a standing tax on
every session and its *body* is not. If that split is wrong, every number Sift
prints is wrong.
"""

import json
import tempfile
import unittest
from pathlib import Path

import _sift  # noqa: F401
import audit
import budget
from audit import estimate_tokens
from budget import cache_multiplier, compare, measure_repo

DESC = "Use when the user asks to frobnicate a widget."          # 45 chars
BODY = "# Frobnicator\n\n" + ("Long instructions. " * 100)        # ~1,915 chars


def write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class TestAlwaysOnSplit(unittest.TestCase):
    """A skill's description is always-on; its body is not."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        write(self.root / ".claude/skills/frob/SKILL.md",
              f"---\nname: frob\ndescription: {DESC}\n---\n{BODY}")

    def test_the_description_is_always_on_and_the_body_is_on_demand(self):
        """A skill charges rent for its description only; the body is paid on trigger."""
        m = measure_repo(self.root)
        skill = next(c for c in m["components"] if c["kind"] == "skill")
        self.assertEqual(skill["always_on"], estimate_tokens(DESC))
        self.assertEqual(skill["on_demand"], estimate_tokens(BODY))
        self.assertEqual(m["always_on_tokens"], estimate_tokens(DESC))

    def test_a_huge_body_does_not_move_the_always_on_number(self):
        """Ten times the body, identical always-on cost — the headline claim, asserted."""
        before = measure_repo(self.root)["always_on_tokens"]
        write(self.root / ".claude/skills/frob/SKILL.md",
              f"---\nname: frob\ndescription: {DESC}\n---\n{BODY * 10}")
        after = measure_repo(self.root)
        self.assertEqual(after["always_on_tokens"], before)
        self.assertGreater(after["on_demand_tokens"], 9 * estimate_tokens(BODY))

    def test_a_longer_description_does_move_it(self):
        """Rambling descriptions are the cost this tool exists to make visible."""
        before = measure_repo(self.root)["always_on_tokens"]
        write(self.root / ".claude/skills/frob/SKILL.md",
              f"---\nname: frob\ndescription: {DESC * 20}\n---\n{BODY}")
        self.assertGreater(measure_repo(self.root)["always_on_tokens"], before + 200)

    def test_root_claude_md_is_counted_whole_but_nested_ones_are_not(self):
        """CLAUDE.md loads every session; a nested one only when work touches its directory."""
        write(self.root / "CLAUDE.md", "root instructions\n" * 50)
        write(self.root / "pkg/CLAUDE.md", "nested instructions\n" * 50)
        m = measure_repo(self.root)
        kinds = {c["kind"]: c for c in m["components"]}
        self.assertGreater(kinds["instructions"]["always_on"], 0)
        self.assertEqual(kinds["instructions-nested"]["always_on"], 0)
        self.assertGreater(kinds["instructions-nested"]["on_demand"], 0)

    def test_subagent_and_command_descriptions_are_always_on(self):
        """Subagents and slash commands are listed to the model, so they are standing cost."""
        write(self.root / ".claude/agents/rev.md", f"---\nname: rev\ndescription: {DESC}\n---\n{BODY}")
        write(self.root / ".claude/commands/ship.md", f"---\ndescription: {DESC}\n---\n{BODY}")
        m = {c["kind"]: c for c in measure_repo(self.root)["components"]}
        self.assertEqual(m["subagent"]["always_on"], estimate_tokens(DESC))
        self.assertEqual(m["command"]["always_on"], estimate_tokens("ship" + DESC))
        self.assertGreater(m["subagent"]["on_demand"], 100)

    def test_mcp_servers_are_declared_as_unmeasured_rather_than_guessed(self):
        """Tool schemas are deferred by Tool Search, so we report the server and count zero."""
        write(self.root / ".mcp.json", json.dumps({"mcpServers": {"gh": {"command": "x"}}}))
        m = measure_repo(self.root)
        mcp = next(c for c in m["components"] if c["kind"] == "mcp-server")
        self.assertEqual(mcp["always_on"], 0)
        self.assertEqual(m["unmeasured_mcp_servers"], 1)
        self.assertTrue(mcp["notes"])

    def test_an_empty_repository_measures_zero_not_an_error(self):
        """Adopting Sift on a repo with no agent config prints 0, not a crash."""
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(measure_repo(td)["always_on_tokens"], 0)

    def test_git_internals_are_never_measured(self):
        """`.git` contents must not leak into the number."""
        write(self.root / ".git/CLAUDE.md", "x" * 5000)
        m = measure_repo(self.root)
        self.assertFalse([c for c in m["components"] if ".git" in c["path"]])


class TestAuditOfAHomeDirectory(unittest.TestCase):
    """audit.py measures a ~/.claude tree; the same split must hold there."""

    def test_skill_description_is_always_on_and_body_is_on_demand(self):
        """Auditing an installed setup separates standing rent from occasional cost."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root / "skills/frob/SKILL.md", f"---\nname: frob\ndescription: {DESC}\n---\n{BODY}")
            write(root / "CLAUDE.md", "global instructions\n" * 20)
            res = audit.audit(root)
            skill = next(a for a in res.artifacts if a.kind == "skill")
            self.assertEqual(skill.always_on_chars, len(DESC))
            self.assertEqual(skill.on_demand_chars, len(BODY))
            self.assertEqual(res.always_on_tokens,
                             skill.always_on_tokens
                             + next(a for a in res.artifacts if a.kind == "claude-md").always_on_tokens)

    def test_a_skill_with_no_frontmatter_is_flagged_not_silently_free(self):
        """A SKILL.md with no frontmatter costs 0 always-on, so it must be reported as broken."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write(root / "skills/broken/SKILL.md", "# no frontmatter here\n")
            skill = next(a for a in audit.audit(root).artifacts if a.kind == "skill")
            self.assertEqual(skill.always_on_chars, 0)
            self.assertTrue(any("frontmatter" in i for i in skill.issues))
            self.assertTrue(any("never fire" in i for i in skill.issues))


class TestCacheMultiplier(unittest.TestCase):
    """One always-on token is cache-written once at 2x and re-read at 0.1x per turn."""

    def test_boundary_values(self):
        """turns 0 and 1 both cost one write; 2 adds one read; 45 is the shipped median."""
        self.assertAlmostEqual(cache_multiplier(0), 2.0)
        self.assertAlmostEqual(cache_multiplier(1), 2.0)
        self.assertAlmostEqual(cache_multiplier(2), 2.1)
        self.assertAlmostEqual(cache_multiplier(45), 6.4)

    def test_negative_turns_are_clamped_rather_than_producing_a_discount(self):
        """Nonsense input cannot make always-on context look cheaper than one session."""
        self.assertAlmostEqual(cache_multiplier(-100), 2.0)

    def test_it_is_monotonic_in_turns(self):
        """More turns can never bill less; the projection cannot flatter a config."""
        vals = [cache_multiplier(n) for n in range(0, 200)]
        self.assertEqual(vals, sorted(vals))
        self.assertGreater(vals[-1], vals[1])

    def test_it_matches_the_stated_formula(self):
        """The multiplier is 2 + 0.1*(N-1) exactly — the number quoted in the PR comment."""
        for n in (1, 3, 10, 45, 132, 638):
            self.assertAlmostEqual(cache_multiplier(n), 2.0 + 0.1 * (n - 1), places=9)


def head(tokens, components=None):
    return {"always_on_tokens": tokens, "components": components or [], "unmeasured_mcp_servers": 0}


BUDGET = dict(budget.DEFAULT_BUDGET)  # limit 5000, warn 0.8, ratchet 500


class TestCompare(unittest.TestCase):
    """pass / warn / fail transitions, and the ratchet that fires under the ceiling."""

    def test_thresholds_are_exact(self):
        """Warn starts strictly above 80% of budget; fail strictly above budget."""
        self.assertEqual(compare(head(4000), None, BUDGET)["status"], "pass")   # exactly 80%
        self.assertEqual(compare(head(4001), None, BUDGET)["status"], "warn")
        self.assertEqual(compare(head(5000), None, BUDGET)["status"], "warn")  # at budget, not over
        self.assertEqual(compare(head(5001), None, BUDGET)["status"], "fail")

    def test_the_ratchet_fires_while_still_far_under_the_ceiling(self):
        """A +501 token change fails at 1,500/5,000 — a hundred small additions is the failure mode."""
        v = compare(head(1500), head(999), BUDGET)
        self.assertEqual(v["status"], "fail")
        self.assertEqual(v["delta"], 501)
        self.assertTrue(any("adds 501" in r for r in v["reasons"]))

    def test_exactly_the_ratchet_limit_passes(self):
        """+500 with a 500 limit is allowed; the boundary is not off by one."""
        v = compare(head(1500), head(1000), BUDGET)
        self.assertEqual(v["delta"], 500)
        self.assertEqual(v["status"], "pass")

    def test_a_deletion_produces_a_negative_delta_and_never_fails(self):
        """Deleting config is always allowed, and is reported as a negative delta."""
        v = compare(head(1000), head(4000), BUDGET)
        self.assertEqual(v["delta"], -3000)
        self.assertEqual(v["status"], "pass")

    def test_no_base_means_no_delta_rather_than_a_delta_of_zero(self):
        """Without a baseline the delta is None, so nobody can read it as 'no change'."""
        self.assertIsNone(compare(head(1000), None, BUDGET)["delta"])

    def test_a_deleted_component_appears_in_the_moved_table(self):
        """The comment can name the file whose deletion moved the number."""
        base = head(300, [{"kind": "skill", "name": "old", "path": "a/SKILL.md", "always_on": 300}])
        v = compare(head(0, []), base, BUDGET)
        self.assertEqual(v["moved"], [{"kind": "skill", "name": "old", "path": "a/SKILL.md",
                                       "was": 300, "now": 0, "delta": -300}])

    def test_moved_rows_are_ordered_by_absolute_impact(self):
        """The biggest cause is named first, whichever direction it moved."""
        base = head(400, [{"kind": "skill", "name": "s", "path": "s", "always_on": 400},
                          {"kind": "skill", "name": "t", "path": "t", "always_on": 10}])
        h = head(50, [{"kind": "skill", "name": "s", "path": "s", "always_on": 40},
                      {"kind": "skill", "name": "t", "path": "t", "always_on": 10}])
        v = compare(h, base, BUDGET)
        self.assertEqual([m["path"] for m in v["moved"]], ["s"])

    def test_both_reasons_are_reported_when_ceiling_and_ratchet_both_trip(self):
        """A change that is over budget *and* a big jump says so twice, not once."""
        v = compare(head(9000), head(1000), BUDGET)
        self.assertEqual(v["status"], "fail")
        self.assertEqual(len(v["reasons"]), 2)

    def test_render_comment_states_the_delta_and_labels_the_projection(self):
        """The PR comment never presents the yearly projection as an observed bill."""
        text = budget.render_comment(compare(head(1500), head(900), BUDGET), BUDGET)
        self.assertIn("+600", text)
        self.assertIn("estimates", text)
        self.assertIn("projection", text)


class TestBudgetFile(unittest.TestCase):
    def test_a_repo_budget_file_overrides_the_defaults(self):
        """sift.budget.json is what a team actually commits to; it must win."""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "sift.budget.json").write_text('{"always_on_tokens": 100}')
            b = budget.load_budget(td)
            self.assertEqual(b["always_on_tokens"], 100)
            self.assertEqual(b["max_increase_tokens"], budget.DEFAULT_BUDGET["max_increase_tokens"])

    def test_a_corrupt_budget_file_falls_back_to_defaults_rather_than_crashing(self):
        """A broken budget file must not take the CI job down with a traceback."""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "sift.budget.json").write_text("{not json")
            self.assertEqual(budget.load_budget(td), budget.DEFAULT_BUDGET)


if __name__ == "__main__":
    unittest.main()
