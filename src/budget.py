"""
sift.budget — a context budget for CI.

The bundlesize bot, for agent configuration.

Why this exists
---------------
A repository's agent configuration — CLAUDE.md, AGENTS.md, .claude/skills,
subagents, slash commands, MCP servers — is loaded into every session, for every
engineer, on every task. It grows the way dependency trees grow: one reasonable
addition at a time, each individually defensible, nobody watching the total.

JavaScript solved this socially, not technically. Bundle-size bots did not make
bundles smaller by being clever; they made the number visible on the pull request
that caused it, at the moment someone could still change their mind. That is the
entire mechanism, and it is what agent configuration is missing.

The evidence that this is worth gating on
-----------------------------------------
Not merely cost. Anthropic's Tool Search Tool cut tool definitions from ~77K to
~8.7K always-loaded tokens and MCP accuracy rose from 49% to 74% on Opus 4, and
79.5% to 88.1% on Opus 4.5 (anthropic.com/engineering/advanced-tool-use).
Du et al. (arXiv:2510.05381) find 13.9-85% degradation
from context length *even when irrelevant tokens are masked out*, so length itself
is a cost, not just misdirected attention. Shi et al. (ICML 2023) show irrelevant
context harms separately from length.

Honest limitation, stated up front and repeated in the output: that evidence sits
at 8k-113k tokens. Nobody has demonstrated that a 2,000-token config costs
measurable accuracy. Below roughly 10k always-on tokens this tool is measuring a
real quantity whose harm is extrapolated, not proven. It says so.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit import (  # noqa: E402
    CHARS_PER_TOKEN,
    check_description,
    estimate_tokens,
    split_frontmatter,
)

BUDGET_FILE = "sift.budget.json"

DEFAULT_BUDGET = {
    "always_on_tokens": 5000,
    "warn_ratio": 0.8,
    # A PR that adds this many always-on tokens gets flagged even if the total is
    # still under budget. Ratchets matter more than absolutes: the failure mode is
    # a hundred small additions, none of which trip a ceiling.
    "max_increase_tokens": 500,
    # Turns per session. Used for the cache multiplier below. Median measured on a
    # real machine was 45; mean 132; max 638. Override per repo from your own data.
    "median_turns_per_session": 45,
    "sessions_per_week": 40,
}


# --------------------------------------------------------------------------
# the cache multiplier — why raw token counts understate the cost
# --------------------------------------------------------------------------
#
# The intuitive objection to this whole tool is "prompt caching makes the standing
# prefix nearly free, so who cares". The arithmetic says the opposite.
#
# Claude Code writes the standing prefix at a 1-hour TTL, which bills at 2x base
# input price, and then re-reads it at 0.1x on every subsequent call in that
# session. Both multipliers are Anthropic's published figures, verified against
# https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching :
#   5-minute cache write  1.25x base input
#   1-hour cache write    2.00x base input
#   cache read            0.10x base input
# So N turns cost:
#
#       X * (2 + 0.1 * (N - 1))
#
# Caching cuts the per-turn price tenfold and then charges it forty-five to six
# hundred times. Measured on a real machine (14 sessions, 1,863 assistant calls,
# 201.6M input tokens): 95.4% of input tokens were cache reads, 4.4% cache
# creation, 100% of that creation on the 1h/2x tier. Effective price per input
# token versus billing it once uncached: 4.02x.
#
# The dishonest overstatement would be tokens * full price * N. The dishonest
# understatement is the raw token count. This is the middle, and it is derived.

CACHE_WRITE_MULTIPLIER = 2.0    # 1h TTL write premium
CACHE_READ_MULTIPLIER = 0.1     # subsequent reads


def cache_multiplier(turns: int) -> float:
    """Billable input-token multiplier for one always-on token over a session."""
    turns = max(1, int(turns))
    return CACHE_WRITE_MULTIPLIER + CACHE_READ_MULTIPLIER * (turns - 1)


# --------------------------------------------------------------------------
# measuring a repository's agent configuration
# --------------------------------------------------------------------------

@dataclass
class Component:
    kind: str
    name: str
    path: str
    always_on: int = 0        # tokens
    on_demand: int = 0        # tokens
    notes: list[str] = field(default_factory=list)


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def measure_repo(root: str | Path) -> dict:
    """Measure every always-on context cost in a repository's agent config."""
    root = Path(root).expanduser().resolve()
    comps: list[Component] = []

    # 1. Root instruction files — loaded whole, every session.
    for fn in ("CLAUDE.md", "AGENTS.md", ".cursorrules", "GEMINI.md"):
        p = root / fn
        if p.is_file():
            t = _read(p)
            comps.append(Component("instructions", fn, _rel(p, root), estimate_tokens(t)))

    # Nested CLAUDE.md files load when work touches their directory. Not strictly
    # always-on, so counted separately rather than silently inflating the headline.
    for p in sorted(root.rglob("CLAUDE.md")):
        if p == root / "CLAUDE.md" or ".git" in p.parts or "node_modules" in p.parts:
            continue
        comps.append(
            Component("instructions-nested", _rel(p, root), _rel(p, root),
                      0, estimate_tokens(_read(p)),
                      ["nested — loads when work touches this directory"])
        )

    # 2. Skills. Description is always-on; body loads on trigger.
    for base in (root / ".claude" / "skills", root / "skills"):
        if not base.is_dir():
            continue
        for sk in sorted(base.rglob("SKILL.md")):
            fm, body, ok = split_frontmatter(_read(sk))
            desc = fm.get("description", "")
            c = Component("skill", fm.get("name") or sk.parent.name, _rel(sk, root),
                          estimate_tokens(desc), estimate_tokens(body))
            if not ok:
                c.notes.append("no frontmatter — may not load")
            c.notes.extend(check_description(desc))
            comps.append(c)

    # 3. Subagents. Their descriptions are listed to the model, so always-on.
    base = root / ".claude" / "agents"
    if base.is_dir():
        for p in sorted(base.rglob("*.md")):
            fm, body, _ = split_frontmatter(_read(p))
            comps.append(Component("subagent", fm.get("name") or p.stem, _rel(p, root),
                                   estimate_tokens(fm.get("description", "")),
                                   estimate_tokens(body)))

    # 4. Slash commands. Name + description listed; body on invocation.
    base = root / ".claude" / "commands"
    if base.is_dir():
        for p in sorted(base.rglob("*.md")):
            fm, body, _ = split_frontmatter(_read(p))
            comps.append(Component("command", p.stem, _rel(p, root),
                                   estimate_tokens(p.stem + fm.get("description", "")),
                                   estimate_tokens(body)))

    # 5. MCP servers.
    #
    # CORRECTED 2026-08-22 after inspecting real session JSONL. An earlier version
    # of this file claimed MCP tool schemas are always-on. They are not, by
    # default: Claude Code ships Tool Search, and sessions carry a
    # `deferred_tools_delta` attachment containing tool *names* only (~10 chars
    # each; one observed session had 466 of them). Schemas load when a tool is
    # actually selected. Anthropic solved this in the product.
    #
    # So we count a small per-tool listing cost and say plainly that the schema
    # itself is deferred. Over-reporting here would have been the same sin as
    # the ecosystem we are criticising.
    for fn in (".mcp.json", ".claude/settings.json", ".claude/settings.local.json"):
        p = root / fn
        if not p.is_file():
            continue
        try:
            cfg = json.loads(_read(p))
        except json.JSONDecodeError:
            continue
        for name in (cfg.get("mcpServers") or {}):
            comps.append(Component("mcp-server", name, fn, 0, 0,
                                   ["tools are name-listed via Tool Search; full schemas load on selection, "
                                    "not at session start — run `sift probe` to measure the listing"]))

    always = sum(c.always_on for c in comps)
    return {
        "root": str(root),
        "always_on_tokens": always,
        "on_demand_tokens": sum(c.on_demand for c in comps),
        "unmeasured_mcp_servers": sum(1 for c in comps if c.kind == "mcp-server"),
        "chars_per_token": CHARS_PER_TOKEN,
        "estimate": True,
        "components": [c.__dict__ for c in comps],
    }


# --------------------------------------------------------------------------
# budget config
# --------------------------------------------------------------------------

def load_budget(root: str | Path) -> dict:
    p = Path(root) / BUDGET_FILE
    b = dict(DEFAULT_BUDGET)
    if p.is_file():
        try:
            b.update(json.loads(_read(p)))
        except json.JSONDecodeError:
            pass
    return b


# --------------------------------------------------------------------------
# comparison and verdict
# --------------------------------------------------------------------------

def compare(head: dict, base: dict | None, budget: dict) -> dict:
    """Produce a verdict: pass / warn / fail, with the reasons."""
    a = head["always_on_tokens"]
    limit = budget["always_on_tokens"]
    warn_at = int(limit * budget.get("warn_ratio", 0.8))

    delta = None
    if base is not None:
        delta = a - base["always_on_tokens"]

    reasons: list[str] = []
    status = "pass"

    if a > limit:
        status = "fail"
        reasons.append(f"always-on context is {a:,} tokens, over the {limit:,} budget by {a - limit:,}")
    elif a > warn_at:
        status = "warn"
        reasons.append(f"always-on context is {a:,} tokens, {a / limit:.0%} of the {limit:,} budget")

    if delta is not None and delta > budget.get("max_increase_tokens", 500):
        if status != "fail":
            status = "fail"
        reasons.append(
            f"this change adds {delta:,} always-on tokens "
            f"(limit for a single change is {budget['max_increase_tokens']:,})"
        )

    # component-level diff, so the comment can name the file that caused it
    moved: list[dict] = []
    if base is not None:
        bmap = {(c["kind"], c["path"]): c for c in base["components"]}
        hmap = {(c["kind"], c["path"]): c for c in head["components"]}
        for k, c in hmap.items():
            was = bmap.get(k, {}).get("always_on", 0)
            if c["always_on"] != was:
                moved.append({"kind": c["kind"], "name": c["name"], "path": c["path"],
                              "was": was, "now": c["always_on"], "delta": c["always_on"] - was})
        for k, c in bmap.items():
            if k not in hmap and c["always_on"]:
                moved.append({"kind": c["kind"], "name": c["name"], "path": c["path"],
                              "was": c["always_on"], "now": 0, "delta": -c["always_on"]})
        moved.sort(key=lambda m: -abs(m["delta"]))

    return {
        "status": status,
        "always_on_tokens": a,
        "budget": limit,
        "delta": delta,
        "reasons": reasons,
        "moved": moved,
        "head": head,
    }


# --------------------------------------------------------------------------
# PR comment rendering
# --------------------------------------------------------------------------

_ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌"}


def render_comment(v: dict, budget: dict | None = None) -> str:
    budget = budget or DEFAULT_BUDGET
    turns = budget.get("median_turns_per_session", 45)
    spw = budget.get("sessions_per_week", 40)
    mult = cache_multiplier(turns)

    a, limit, delta = v["always_on_tokens"], v["budget"], v["delta"]
    pct = a / limit if limit else 0
    filled = min(int(pct * 28), 28)
    bar = "█" * filled + "░" * (28 - filled)

    head = f"### {_ICON[v['status']]} Context budget — {v['status'].upper()}\n\n"
    head += f"```\n{bar}  {a:,} / {limit:,} always-on tokens  ({pct:.0%})\n```\n\n"

    if delta is not None:
        sign = "+" if delta > 0 else ""
        if delta:
            billable = int(delta * mult)
            yearly = int(billable * spw * 48)
            head += (
                f"**{sign}{delta:,} always-on tokens** vs base.\n\n"
                f"That prefix is cache-written once at 2× and re-read at 0.1× on each of "
                f"~{turns} turns, so it bills as **{sign}{billable:,} input tokens per session** "
                f"(×{mult:.1f}) — {sign}{yearly:,} a year per engineer at {spw} sessions a week.\n\n"
            )
        else:
            head += "No change to always-on context.\n\n"

    if v["reasons"]:
        head += "\n".join(f"- {r}" for r in v["reasons"]) + "\n\n"

    moved = [m for m in v["moved"] if m["delta"]][:12]
    if moved:
        head += "| file | kind | was | now | Δ |\n|---|---|---:|---:|---:|\n"
        for m in moved:
            s = "+" if m["delta"] > 0 else ""
            head += f"| `{m['path']}` | {m['kind']} | {m['was']:,} | {m['now']:,} | **{s}{m['delta']:,}** |\n"
        head += "\n"

    unmeasured = v["head"].get("unmeasured_mcp_servers", 0)
    if unmeasured:
        head += (f"> {unmeasured} MCP server(s) declared. Claude Code defers tool schemas via Tool "
                 "Search, so only tool *names* are listed at session start — a small cost, and not "
                 "measurable from static files. Not included above.\n\n")

    head += ("<sub>Token counts are estimates from a character heuristic, not a tokenizer. "
             "The per-session figure is a projection from repo state, not an observed bill: "
             "always-on tokens x cache multiplier x turns. It holds model, version and effort "
             "fixed, so it is directional and free of task-variance confounders. "
             "Evidence that always-on context costs accuracy is strong above ~10k tokens "
             "([Tool Search: 77K→8.7K tokens raised MCP accuracy 49%→74%]"
             "(https://www.anthropic.com/engineering), "
             "[arXiv:2510.05381](https://arxiv.org/abs/2510.05381)) and is an extrapolation "
             "below that. Sift measures the quantity; the harm at small sizes is inferred.</sub>")
    return head


# --------------------------------------------------------------------------
# git helpers — measure the same repo at another ref
# --------------------------------------------------------------------------

class BaseUnavailable(RuntimeError):
    """The base ref could not be resolved.

    This is raised rather than returning None because the failure mode it
    guards against is the dangerous one: `actions/checkout@v4` defaults to
    `fetch-depth: 1`, so the base SHA is simply absent from the clone. Silently
    treating that as "no change" makes the ratchet pass every pull request on
    the default CI configuration — a gate that fails open is worse than no gate,
    because it is trusted.
    """


def measure_ref(root: str | Path, ref: str) -> dict | None:
    """Measure the repo as it exists at `ref`, via a temporary worktree."""
    root = Path(root).resolve()
    import tempfile

    have = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{ref}^{{commit}}"],
                          capture_output=True)
    if have.returncode != 0:
        # try to fetch it before giving up — shallow CI clones can deepen
        subprocess.run(["git", "-C", str(root), "fetch", "--depth=1", "origin", ref],
                       capture_output=True)
        have = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{ref}^{{commit}}"],
                              capture_output=True)
        if have.returncode != 0:
            raise BaseUnavailable(
                f"base ref {ref!r} is not in this clone. If this is GitHub Actions, "
                "actions/checkout defaults to fetch-depth: 1 — set `with: {fetch-depth: 0}`."
            )

    with tempfile.TemporaryDirectory() as td:
        wt = Path(td) / "wt"
        r = subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", str(wt), ref],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise BaseUnavailable(f"could not create worktree at {ref!r}: {r.stderr.strip()}")
        try:
            return measure_repo(wt)
        finally:
            subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(wt)],
                           capture_output=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

USAGE = """sift — a context budget for CI

  sift measure [path]              measure always-on context, print JSON
  sift check   [path] [--base REF] verdict against sift.budget.json; exit 1 on fail
  sift comment [path] [--base REF] render the pull-request comment markdown
  sift init    [path]              write a sift.budget.json seeded from current size
  sift calibrate [logs]            measure the real prefix, your cache multiplier and
                                   which skills have ever actually fired, from session logs
                                   --share  print a publishable summary (no names, no paths)
                                   --names  include skill names in --share, deliberately
"""


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(USAGE)
        return 0
    cmd = argv[1]
    rest = argv[2:]
    base_ref = None
    if "--base" in rest:
        i = rest.index("--base")
        base_ref = rest[i + 1] if i + 1 < len(rest) else None
        rest = rest[:i] + rest[i + 2:]
    # Flags are not paths. Without this, `sift calibrate --json` treated "--json"
    # as the log directory and reported "no session directory at --json".
    flags = [a for a in rest if a.startswith("-")]
    rest = [a for a in rest if not a.startswith("-")]
    root = Path(rest[0]).expanduser() if rest else Path.cwd()

    if cmd == "calibrate":
        import calibrate as _cal
        d = _cal.collect(rest[0] if rest else "~/.claude/projects")
        if "--share" in flags:
            print(json.dumps(_cal.share_blob(d, include_names="--names" in flags), indent=1))
            print("\n" + _cal.SHARE_NOTE)
        elif "--json" in flags:
            print(json.dumps(d, indent=1))
        else:
            print(_cal.render_text(d))
        return 0

    if cmd == "measure":
        print(json.dumps(measure_repo(root), indent=1))
        return 0

    if cmd == "init":
        head = measure_repo(root)
        a = head["always_on_tokens"]
        # Seed the budget with headroom rather than pinning to today's number,
        # so adopting the tool does not immediately fail your own build.
        b = dict(DEFAULT_BUDGET, always_on_tokens=max(1000, int(a * 1.25 / 100 + 0.5) * 100))
        (Path(root) / BUDGET_FILE).write_text(json.dumps(b, indent=2) + "\n")
        print(f"wrote {BUDGET_FILE}: budget {b['always_on_tokens']:,} (current {a:,})")
        return 0

    head = measure_repo(root)
    try:
        base = measure_ref(root, base_ref) if base_ref else None
    except BaseUnavailable as e:
        # Exit 2, never 0. A gate that cannot see the baseline must say so loudly.
        print(f"error: {e}", file=sys.stderr)
        return 2
    v = compare(head, base, load_budget(root))

    if cmd == "comment":
        print(render_comment(v, load_budget(root)))
        return 0
    if cmd == "check":
        print(f"{_ICON[v['status']]} {v['status']}: {v['always_on_tokens']:,}/{v['budget']:,} always-on tokens")
        for r in v["reasons"]:
            print(f"   - {r}")
        return 1 if v["status"] == "fail" else 0

    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
