<div align="center">

# Sift

**A context budget for CI.** The bundle-size bot, for agent configuration.

[![tests](https://github.com/Jithendrag22/sift/actions/workflows/tests.yml/badge.svg)](https://github.com/Jithendrag22/sift/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](#requirements)
[![dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)

Your `CLAUDE.md`, your skills, your subagents load into **every session, for every
engineer, on every task**. Nobody is watching the total.

Sift puts the number on the pull request that moved it.

</div>

---

```
### ❌ Context budget — FAIL

████████████████████████████  2,534 / 1,000 always-on tokens  (253%)

+2,469 always-on tokens vs base.

That prefix is cache-written once at 2× and re-read at 0.1× on each of ~45 turns,
so it bills as +15,801 input tokens per session (×6.4).

| file                                       | kind         |  was |  now |      Δ |
|--------------------------------------------|--------------|-----:|-----:|-------:|
| CLAUDE.md                                  | instructions |   23 | 1908 | +1,885 |
| .claude/skills/changelog-writer/SKILL.md   | skill        |    0 |  118 |   +118 |
```

## Why

Agent configuration grows the way dependency trees grow: one reasonable addition at a
time, each individually defensible, nobody watching the total.

JavaScript did not solve bundle bloat by being clever. It solved it **socially** — by
putting the number on the pull request that caused it, while someone could still change
their mind. Agent configuration has no such number.

And the cost is not only money:

| source | finding |
|---|---|
| Anthropic, Tool Search Tool | Cutting always-loaded tool definitions from ~77K to ~8.7K tokens raised MCP accuracy **49% → 74%** (Opus 4) and **79.5% → 88.1%** (Opus 4.5) |
| [arXiv:2510.05381](https://arxiv.org/abs/2510.05381) | 13.9–85% degradation from context length **even when irrelevant tokens are masked out** |
| Shi et al., ICML 2023 | Irrelevant context harms *separately* from length |
| Boris Cherny, Anthropic | "Every 6 months delete your CLAUDE.md. Delete your skills. Delete your hooks. See what the model does." |

Less context is not merely cheaper. It is more accurate.

## Install

No package, no dependencies, no account.

```bash
git clone https://github.com/Jithendrag22/sift.git
cd sift && ./sift --help
```

## Use

```bash
./sift init                      # seed sift.budget.json with 25% headroom
./sift measure .                 # JSON snapshot of always-on cost
./sift check   . --base main     # verdict; exit 1 over budget, 2 if base unreachable
./sift comment . --base main     # markdown for the PR comment
./sift calibrate                 # ground it in your own session logs
```

### GitHub Action

```yaml
name: context-budget
on:
  pull_request:
    paths: ["CLAUDE.md", "AGENTS.md", ".claude/**", ".mcp.json", "sift.budget.json"]

permissions:
  contents: read
  pull-requests: write

jobs:
  budget:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }   # required — Sift needs the base commit
      - uses: Jithendrag22/sift@v1
```

Exposes `status`, `always-on-tokens` and `delta` as outputs for downstream steps.

### `sift.budget.json`

```json
{
  "always_on_tokens": 5000,
  "warn_ratio": 0.8,
  "max_increase_tokens": 500,
  "median_turns_per_session": 45,
  "sessions_per_week": 40
}
```

`max_increase_tokens` is the one that matters. Ceilings are easy to live under; the real
failure mode is a hundred small additions, none of which trip an absolute limit.

## The distinction the tool is built on

| | what it is | when you pay |
|---|---|---|
| **Always-on** | `CLAUDE.md` in full · the `description` of *every* installed skill · every subagent description | Every session, before you type a word |
| **On-demand** | The *body* of a skill or command · MCP tool schemas, which Tool Search defers until a tool is selected | Only when it fires |

A skill with a 3,000-word body and a tight 20-word description is nearly free until you
need it. A skill with a rambling 200-word description charges rent in every session
forever, whether it fires or not.

## `sift calibrate` — the part that is not a projection

Everything else here is computed from files: a projection of what your config *should*
cost. `calibrate` reads the session logs your agent already writes and reports what it
*did*.

```
turns/session: median 36  mean 93.3  max 638
OBSERVED cold-start prefix: median 16,043 tokens (range 7,868–31,702)
input mix: 95.2% cache read, 4.6% cache creation, 0.2% uncached
YOUR cache multiplier: ×5.5

skills listed to the model: 30
ever fired: 1     never fired: 29
```

**Those last two lines are the point.** Assistant records carry an `attributionSkill`
field, so whether a skill has *ever* fired is a count, not an argument.

<details>
<summary><strong>Why caching makes this worse, not better</strong></summary>

<br>

The intuitive objection is that prompt caching makes the standing prefix nearly free. The
arithmetic says the opposite. The prefix is cache-written once at **2×** base input price
(1-hour TTL) and re-read at **0.1×** on every subsequent call:

```
cost(X tokens, N turns) = X × (2 + 0.1 × (N − 1))
```

Caching cuts the per-turn price tenfold and then charges it 45 to 638 times. On one
measured machine — 14 sessions, 1,863 calls, 201.6M input tokens — 95.4% of input tokens
were cache reads and 100% of cache creation sat on the 1h/2× tier. Effective multiplier
versus billing once uncached: **4.02×**.

Raw token counts *understate* the cost. Sift reports the multiplied figure and shows its
working.

</details>

<details>
<summary><strong>Privacy</strong></summary>

<br>

`calibrate` reads your own local session logs and extracts counts, token totals and skill
names. No message content is read into memory beyond those fields, and none is written to
output. Nothing is transmitted anywhere — Sift makes no network calls, has no telemetry
and requires no account.

</details>

## What Sift does *not* do

Named honestly, because each of these is already solved by someone else:

- **Not a linter.** Frontmatter validity is 99.7% across a 10,198-file corpus. That
  problem is solved.
- **Not a quality score.** [`benchflow-ai/skillsbench`](https://github.com/benchflow-ai/skillsbench)
  measures skill efficacy by *executing tasks* in a sandbox. Execution-grounded beats
  static scoring, and they got there first. Sift measures cost, which is orthogonal.
- **Not a retrospective CLI.** [`codeburn`](https://github.com/getagentseal/codeburn)
  already computes a local always-on budget. It has no per-PR diff, no base ref and no
  Action — that gap is what Sift fills, and it is a narrow one.
- **Not a tokenizer.** Counts are character-heuristic estimates (~3.6 chars/token).
  Accurate enough to rank and to decide what to cut; not accurate enough to bill from.
  Labelled as estimates wherever they appear.

### Stated limitations

The evidence that always-on context costs *accuracy* is strong between 8k and 113k tokens.
**Nobody has demonstrated that a 2,000-token config measurably hurts.** Below roughly 10k
always-on tokens, Sift measures a real quantity whose harm is extrapolated.

Sift also measures your **repository's contribution**, not the whole prefix. Observed
cold-start prefixes run 11.5k–26k tokens, most of it built into the CLI and invisible on
disk. `calibrate` reports both so the two are never confused.

Every report the tool produces repeats both caveats. We would rather lose an argument than
win one dishonestly.

## Layout

```
sift                    CLI entry point
action.yml              composite GitHub Action  (Jithendrag22/sift@v1)
src/budget.py           the gate — measure, compare, render the PR comment
src/calibrate.py        observed prefix, cache multiplier, skill fire rates
src/audit.py            measure a live ~/.claude setup
src/dashboard.py        observed vs projected, on one page
src/report.py           self-contained HTML report, zero network
src/corpus_stats.py     corpus miner over any tree of SKILL.md files
src/install_cost.py     what installing a whole collection costs, per session
tests/                  68 tests, standard library only
```

## Requirements

Python 3.9+. Standard library only for the gate; the corpus miner uses PyYAML when
present and degrades gracefully without it. No install step.

Verified locally on 3.11 and 3.13 — 68 tests, all passing. The CI matrix covers 3.9.

## Contributing

One rule: **measure, do not assert.** Any number this tool prints must be computed and
reproducible. If it cannot be computed, say so rather than estimate confidently. A
measurement tool that is wrong is worse than no tool, because people act on it.

```bash
python3 -m unittest discover tests -v
```

## License

MIT — see [LICENSE](LICENSE).
