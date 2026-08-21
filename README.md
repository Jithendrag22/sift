# Sift

**A context budget for CI.** The bundle-size bot, for agent configuration.

```
████████████████████████████  2,534 / 1,000 always-on tokens  (253%)

+2,469 always-on tokens vs base — that is +4,740,480 tokens a year
per engineer, at 40 sessions a week.

| file                                    | kind   |  was |  now |      Δ |
|-----------------------------------------|--------|-----:|-----:|-------:|
| CLAUDE.md                               | instr. |   23 | 1908 | +1,885 |
| .claude/skills/changelog-writer/SKILL.md| skill  |    0 |  118 |   +118 |
```

## The problem

A repository's agent configuration — `CLAUDE.md`, `.claude/skills`, subagents, slash
commands, MCP servers — loads into **every session, for every engineer, on every task**.
It grows the way dependency trees grow: one reasonable addition at a time, each
individually defensible, nobody watching the total.

JavaScript did not solve bundle bloat by being clever. It solved it socially, by putting
the number on the pull request that caused it, while someone could still change their
mind. Agent configuration has no such number.

Prior art, stated accurately: GitHub code search returns 58 workflow files mentioning
`context-budget`. The closest is `kumaran-is/claude-code-onboarding` (34★), whose
`context-budget-gate.yml` gates on Claude Code's 150k-character silent-load limit and
whose author states it deliberately does **not** police size or growth. `codeburn`
(9,589★) computes an always-on budget locally but has no diff, no base ref and no Action.
So the ceiling is guarded and the retrospective is covered; the **per-pull-request ratchet
is not**. That is the gap, and it is narrower than "nobody has done this".

## Always-on vs on-demand

The distinction the tool is built on, and the one nobody makes:

| | what it is | when you pay |
|---|---|---|
| **Always-on** | `CLAUDE.md` in full; the `description` line of *every* installed skill; every subagent description; every MCP tool schema | Every session, before you type a word |
| **On-demand** | The *body* of a skill or command | Only when it fires |

A skill with a 3,000-word body and a tight 20-word description is nearly free until you
need it. A skill with a rambling 200-word description charges rent in every session
forever, whether it fires or not. Sift measures them separately because they are
economically different things.

## Why gate on it

Not just cost — accuracy.

- Anthropic's Tool Search Tool cut always-loaded tool definitions from ~77K to ~8.7K
  tokens. MCP accuracy rose **49% → 74%** on Opus 4 and **79.5% → 88.1%** on Opus 4.5.
- Du et al., [arXiv:2510.05381](https://arxiv.org/abs/2510.05381) — 13.9–85% degradation
  from context length *even when irrelevant tokens are masked out*. Length itself costs.
- Shi et al., ICML 2023 — irrelevant context harms separately from length.
- Boris Cherny (Anthropic), publicly: delete your `CLAUDE.md`, your skills and your hooks
  every six months and see what the model does unassisted. Anthropic cut 80% of the Claude
  Code system prompt for Opus 5 and Fable 5, and the model got better.

**Stated limitation.** That evidence sits at 8k–113k tokens. Nobody has demonstrated that
a 2,000-token config costs measurable accuracy. Below roughly 10k always-on tokens, Sift
is measuring a real quantity whose harm is *extrapolated*. Every report it produces says
so. We would rather lose an argument than win one dishonestly.

## Use

```bash
./sift init                          # seed sift.budget.json with 25% headroom
./sift measure .                     # JSON snapshot of always-on cost
./sift check   . --base main         # verdict; exit 1 when over budget
./sift comment . --base main         # markdown for the PR comment
./sift calibrate                     # ground it in your own session logs
```

### `sift calibrate` — the part that is not a projection

Everything else in Sift is computed from files. `calibrate` reads the session logs Claude
Code already writes to `~/.claude/projects/**/*.jsonl` and reports three things you cannot
get any other way:

```
turns/session: median 36  mean 93.2  max 638
OBSERVED cold-start prefix: median 16,043 tokens (range 7,868-31,702)
input mix: 95.2% cache read, 4.6% cache creation, 0.2% uncached
YOUR cache multiplier: x5.5

skills listed to the model: 30
ever fired: 1    never fired: 29
```

The last two lines are the point. Assistant records carry an `attributionSkill` field, so
whether a skill has *ever* fired is an observation, not an argument. Twenty-nine skills
paying always-on rent across 2,143 calls, and one of them earning it.

It reads counts, token totals and skill names. No message content.

GitHub Action:

```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- uses: your-org/sift/action@v1
  with:
    base: ${{ github.event.pull_request.base.sha }}
```

`sift.budget.json`:

```json
{
  "always_on_tokens": 5000,
  "warn_ratio": 0.8,
  "max_increase_tokens": 500
}
```

`max_increase_tokens` is the one that matters. Ceilings are easy to live under; the real
failure mode is a hundred small additions, none of which trip an absolute limit.

## What it does not do

- **It is not a linter.** Frontmatter validity is 99.7% across a 10,198-file corpus. That
  problem is solved; `hashgraph-online/skill-publish` covers what remains.
- **It does not score skill quality.** `benchflow-ai/skillsbench` measures efficacy by
  executing tasks in a sandbox. Execution-grounded beats static scoring and they got
  there first. Sift measures cost, which is orthogonal and unclaimed.
- **It does not use a tokenizer.** Counts are character-heuristic estimates (~3.6
  chars/token). Accurate enough to rank and to decide what to cut, not accurate enough to
  bill from. Labelled as estimates everywhere they appear.
- **It does not count MCP tool schemas as always-on**, because they are not. Claude Code
  ships Tool Search: sessions carry a `deferred_tools_delta` listing tool *names* only,
  and schemas load on selection. An earlier draft of this README claimed otherwise. It was
  wrong, and Anthropic solved that problem in the product.

- **It measures your repository's contribution, not the whole prefix.** Observed
  cold-start prefixes on real sessions run 11.5k–26k tokens, most of it built into the CLI
  and invisible on disk. Sift's number is the part *you control and can change in a pull
  request*, which is the only part a CI gate can act on. `sift calibrate` reports the real
  observed prefix from your own session logs so the two are never confused.

## Layout

```
sift                  CLI entry point
src/budget.py         the CI gate — measure, compare, render PR comment
src/audit.py          measure a live ~/.claude setup
src/report.py         self-contained HTML report, zero network
src/calibrate.py      observed prefix, cache multiplier and skill fire rates from logs
src/corpus_stats.py   corpus miner (10,198 SKILL.md files)
src/install_cost.py   what installing a whole collection costs, per session, forever
action/action.yml     composite GitHub Action
data/corpus.jsonl     parsed corpus, 17 fields per skill
```

Python 3.9+, standard library only for the gate. No install step.

## Status

v1. The gate works end-to-end against a real git repository and returns correct exit
codes. Not yet published to the Action marketplace.
