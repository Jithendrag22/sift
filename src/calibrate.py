"""
sift.calibrate — ground the projection in observed sessions.

Everything else in Sift is computed from files on disk: a projection of what a
config *should* cost. This module reads what it *did* cost, from the session
logs Claude Code already writes to ~/.claude/projects/**/*.jsonl.

Three things come out of that, and none of them can be got any other way:

  1. The real cold-start prefix. The number Sift projects from repo files is
     your repository's *contribution*. The observed prefix is the whole thing,
     most of which is built into the CLI and invisible on disk. Reporting one
     as the other would be dishonest, so this command reports both.

  2. The empirical cache multiplier. The standing prefix is cache-written once
     at 2x and re-read at 0.1x per turn, so the billable cost depends entirely
     on turns per session — which varies by an order of magnitude between users.
     Measure yours rather than inheriting our median.

  3. Which skills have ever actually fired. Assistant records carry an
     `attributionSkill` field. A skill that has never appeared in it across
     thousands of calls is paying always-on rent for nothing, and that is an
     observation, not an argument. This is the single most actionable output
     Sift produces, and it needs no model call to compute.

Privacy: this reads your own session logs and reports counts, token totals and
skill names only. No message content is read into memory beyond the fields
named here, and none is written to the output.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from budget import CACHE_READ_MULTIPLIER, CACHE_WRITE_MULTIPLIER  # noqa: E402


def _sessions(root: Path):
    for p in sorted(root.rglob("*.jsonl")):
        yield p


def collect(root: str | Path = "~/.claude/projects") -> dict:
    root = Path(root).expanduser()
    if not root.is_dir():
        return {"error": f"no session directory at {root}"}

    cold_prefixes: list[int] = []      # cache_creation on the first call of a session
    turns_per_session: list[int] = []
    listed_skills: set[str] = set()
    listing_sizes: list[int] = []
    fired = Counter()
    versions = Counter()
    models = Counter()
    tot_cache_read = tot_cache_create = tot_uncached = 0
    n_calls = 0

    for path in _sessions(root):
        turns = 0
        cold: int | None = None
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue

                t = r.get("type")

                if t == "attachment":
                    a = r.get("attachment") or {}
                    if a.get("type") == "skill_listing":
                        names = a.get("names") or []
                        listed_skills.update(names)
                        if a.get("content"):
                            listing_sizes.append(len(a["content"]))
                    continue

                if t != "assistant":
                    continue

                msg = r.get("message") or {}
                u = msg.get("usage") or {}
                if not u:
                    continue
                n_calls += 1
                turns += 1
                cr = u.get("cache_read_input_tokens") or 0
                cc = u.get("cache_creation_input_tokens") or 0
                tot_cache_read += cr
                tot_cache_create += cc
                tot_uncached += u.get("input_tokens") or 0
                if cold is None and cr == 0 and cc:
                    cold = cc
                if r.get("version"):
                    versions[r["version"]] += 1
                if msg.get("model"):
                    models[msg["model"]] += 1
                sk = r.get("attributionSkill")
                if sk:
                    fired[sk] += 1

        if turns:
            turns_per_session.append(turns)
        if cold:
            cold_prefixes.append(cold)

    total_in = tot_cache_read + tot_cache_create + tot_uncached
    med_turns = int(statistics.median(turns_per_session)) if turns_per_session else 0

    # Effective multiplier implied by the observed mix, versus billing every
    # input token once at base price.
    effective = None
    if total_in:
        weighted = (tot_cache_create * CACHE_WRITE_MULTIPLIER
                    + tot_cache_read * CACHE_READ_MULTIPLIER
                    + tot_uncached * 1.0)
        # per always-on token, the cost across a median session
        effective = CACHE_WRITE_MULTIPLIER + CACHE_READ_MULTIPLIER * max(0, med_turns - 1)
        mix_ratio = weighted / total_in
    else:
        mix_ratio = None

    never = sorted(listed_skills - set(fired))
    return {
        "sessions": len(turns_per_session),
        "calls": n_calls,
        "turns_per_session": {
            "median": med_turns,
            "mean": round(statistics.mean(turns_per_session), 1) if turns_per_session else 0,
            "max": max(turns_per_session) if turns_per_session else 0,
        },
        "observed_cold_prefix_tokens": {
            "n": len(cold_prefixes),
            "median": int(statistics.median(cold_prefixes)) if cold_prefixes else None,
            "min": min(cold_prefixes) if cold_prefixes else None,
            "max": max(cold_prefixes) if cold_prefixes else None,
        },
        "input_token_mix": {
            "cache_read_pct": round(tot_cache_read / total_in * 100, 1) if total_in else None,
            "cache_creation_pct": round(tot_cache_create / total_in * 100, 1) if total_in else None,
            "uncached_pct": round(tot_uncached / total_in * 100, 1) if total_in else None,
            "total_input_tokens": total_in,
            "price_ratio_vs_single_uncached_read": round(mix_ratio, 2) if mix_ratio else None,
        },
        "cache_multiplier_for_your_median_session": round(effective, 1) if effective else None,
        "skills": {
            "listed": len(listed_skills),
            "ever_fired": len(fired),
            "never_fired": len(never),
            "top_fired": fired.most_common(10),
            "never_fired_names": never[:40],
        },
        "claude_code_versions": versions.most_common(5),
        "models": models.most_common(5),
    }


# --------------------------------------------------------------------------
# shareable summary
# --------------------------------------------------------------------------
#
# The single weakest claim this project makes is "1 of 30 skills has ever fired".
# It is true, and it is n=1 machine. Until that generalises it is an anecdote
# with a number attached.
#
# The obvious fix is telemetry. We are not doing that. The README promises no
# network calls and no telemetry, and a promise you revise the moment it becomes
# inconvenient was never a promise. So instead: emit a blob the operator can read
# in full, judge for themselves, and paste somewhere if they choose. Consent that
# requires an action is the only kind worth having.
#
# Skill *names* are excluded by default. Public skill names are harmless, but an
# internal one can leak a product, a client or an unannounced project, and we
# cannot tell which is which from here. `--names` includes them deliberately.

SHARE_NOTE = (
    "Paste this into https://github.com/Jithendrag22/sift/discussions to help test whether "
    "the fire-rate finding generalises. Read it first — it is yours."
)


def share_blob(d: dict, include_names: bool = False) -> dict:
    """A summary safe to publish: counts and distributions, no paths, no content."""
    s = d.get("skills", {}) or {}
    t = d.get("turns_per_session", {}) or {}
    c = d.get("observed_cold_prefix_tokens", {}) or {}
    m = d.get("input_token_mix", {}) or {}
    out = {
        "schema": "sift.share/1",
        "sessions": d.get("sessions"),
        "calls": d.get("calls"),
        "turns_median": t.get("median"),
        "turns_mean": t.get("mean"),
        "turns_max": t.get("max"),
        "cold_prefix_median": c.get("median"),
        "cold_prefix_min": c.get("min"),
        "cold_prefix_max": c.get("max"),
        "cache_read_pct": m.get("cache_read_pct"),
        "cache_creation_pct": m.get("cache_creation_pct"),
        "uncached_pct": m.get("uncached_pct"),
        "total_input_tokens": m.get("total_input_tokens"),
        "cache_multiplier": d.get("cache_multiplier_for_your_median_session"),
        "skills_listed": s.get("listed"),
        "skills_ever_fired": s.get("ever_fired"),
        "skills_never_fired": s.get("never_fired"),
        # a distribution, not a list — how concentrated is usage across the ones that fire
        "fire_counts": sorted((n for _, n in (s.get("top_fired") or [])), reverse=True),
        "claude_code_versions": [v for v, _ in (d.get("claude_code_versions") or [])],
    }
    if include_names:
        out["fired_names"] = [n for n, _ in (s.get("top_fired") or [])]
        out["never_fired_names"] = s.get("never_fired_names") or []
    return out


def render_text(d: dict) -> str:
    if "error" in d:
        return d["error"]
    L: list[str] = []
    ap = L.append
    ap("SIFT CALIBRATE — measured from your own session logs\n")
    ap(f"  {d['sessions']} sessions, {d['calls']:,} assistant calls")
    t = d["turns_per_session"]
    ap(f"  turns/session: median {t['median']}  mean {t['mean']}  max {t['max']}")

    c = d["observed_cold_prefix_tokens"]
    if c["median"]:
        ap(f"\n  OBSERVED cold-start prefix: median {c['median']:,} tokens "
           f"(range {c['min']:,}–{c['max']:,}, n={c['n']})")
        ap("    This is the whole standing prefix, most of it built into the CLI.")
        ap("    Sift's projected number is your repo's *contribution* to it — a subset.")

    m = d["input_token_mix"]
    if m["total_input_tokens"]:
        ap(f"\n  input mix: {m['cache_read_pct']}% cache read, "
           f"{m['cache_creation_pct']}% cache creation, {m['uncached_pct']}% uncached")
        ap(f"    across {m['total_input_tokens']:,} input tokens")
    if d["cache_multiplier_for_your_median_session"]:
        ap(f"\n  YOUR cache multiplier: x{d['cache_multiplier_for_your_median_session']} "
           f"(2.0 write + 0.1 x {t['median']-1} reads)")
        ap("    Put this in sift.budget.json as median_turns_per_session.")

    s = d["skills"]
    ap(f"\n  skills listed to the model: {s['listed']}")
    ap(f"  ever fired: {s['ever_fired']}    never fired: {s['never_fired']}")
    if s["top_fired"]:
        ap("    most used:")
        for name, n in s["top_fired"]:
            ap(f"      {n:>5}x  {name}")
    if s["never_fired_names"]:
        ap("    never fired once — paying always-on rent for nothing:")
        for name in s["never_fired_names"][:15]:
            ap(f"            {name}")
        if s["never_fired"] > 15:
            ap(f"            ... and {s['never_fired'] - 15} more")
    ap("\n  Caveat: absence of firing is evidence over the window these logs cover,")
    ap("  not proof a skill is useless. Check the window before deleting.")
    return "\n".join(L)


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "~/.claude/projects"
    d = collect(root)
    if "--share" in argv:
        print(json.dumps(share_blob(d, include_names="--names" in argv), indent=1))
        print("\n" + SHARE_NOTE)
    elif "--json" in argv:
        print(json.dumps(d, indent=1))
    else:
        print(render_text(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
