"""
sift.audit — measure what an agent setup actually costs.

The central idea
----------------
Not all context is paid for at the same time. A Claude Code setup has two very
different kinds of cost, and almost nobody separates them:

  ALWAYS-ON   Loaded into every single session before you type a word.
              Your CLAUDE.md. The *description* line of every installed skill
              (the model must see all of them to know which to trigger). Every
              subagent description. Every MCP tool schema. This is a standing
              tax on every request you ever make.

  ON-DEMAND   Loaded only when something fires. The *body* of a skill. The body
              of a slash command. Cost you pay occasionally and deliberately.

The always-on number is the one that matters and the one nobody knows. A skill
with a 2,000-word body and a tight 20-word description is nearly free until you
need it. A skill with a rambling 200-word description is charging you rent in
every session forever, whether it fires or not.

Everything here is computed from files on disk. Nothing is asserted.

Token estimates
---------------
Token counts are ESTIMATES from a character heuristic, not a real tokenizer.
We do not ship a tokenizer because it would mean a dependency and a download,
and the decisions this tool drives (delete / tighten / keep) do not change at
±15% precision. Every estimate is labelled as such in the output. See
`estimate_tokens` for the heuristic and its known biases.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# --------------------------------------------------------------------------
# token estimation
# --------------------------------------------------------------------------

# Rationale for 3.6 rather than the usual 4.0: prose in English averages close
# to 4 chars/token, but these files are markdown dense with punctuation, code
# fences, YAML keys and bullet markers, all of which tokenize worse than prose.
# Measured against a handful of hand-counted samples, 3.6 was closer. It is
# still an estimate and is reported as one.
CHARS_PER_TOKEN = 3.6


def estimate_tokens(text: str) -> int:
    """Estimate token count from characters.

    Biased low for CJK text and high for long runs of whitespace. Do not use
    this for billing; use it for ranking, which is what it is for.
    """
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN + 0.5)


# --------------------------------------------------------------------------
# frontmatter parsing (no yaml dependency)
# --------------------------------------------------------------------------

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def split_frontmatter(text: str) -> tuple[dict, str, bool]:
    """Return (frontmatter_dict, body, ok).

    Deliberately a *small* YAML subset: top-level `key: value` pairs, plus
    folded/literal blocks introduced by `>` or `|`. That covers essentially
    every SKILL.md in the wild. `ok` is False when there is no frontmatter at
    all, which is itself a finding worth reporting.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text, False

    raw, body = m.group(1), text[m.end():]
    data: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []
    block = False

    def flush() -> None:
        nonlocal key, buf, block
        if key is not None:
            data[key] = "\n".join(buf).strip() if block else " ".join(buf).strip()
        key, buf, block = None, [], False

    for line in raw.split("\n"):
        if not line.strip():
            if block:
                buf.append("")
            continue
        # A whole-line YAML comment is not part of any value. Without this it is
        # appended to the preceding key and inflates that artifact's always-on
        # character count. Inline `#` is deliberately left alone: it is legitimate
        # content in descriptions ("C#", "issue #12").
        if not block and line.lstrip().startswith("#"):
            continue
        # a new top-level key starts at column 0 and looks like `word:`
        km = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if km and not line.startswith((" ", "\t")):
            flush()
            key = km.group(1)
            val = km.group(2).strip()
            if val in (">", "|", ">-", "|-", ">+", "|+"):
                block = True
            elif val:
                buf = [_unquote(val)]
            else:
                buf = []
        elif key is not None:
            buf.append(line.strip())
    flush()
    return data, body, True


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


# --------------------------------------------------------------------------
# artifact model
# --------------------------------------------------------------------------

ALWAYS_ON = "always-on"
ON_DEMAND = "on-demand"


@dataclass
class Artifact:
    """One thing in a setup that costs context."""

    kind: str                 # skill | claude-md | subagent | command | mcp-tool | hook
    name: str
    path: str
    always_on_chars: int = 0
    on_demand_chars: int = 0
    issues: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def always_on_tokens(self) -> int:
        return estimate_tokens("x" * self.always_on_chars)

    @property
    def on_demand_tokens(self) -> int:
        return estimate_tokens("x" * self.on_demand_chars)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["always_on_tokens"] = self.always_on_tokens
        d["on_demand_tokens"] = self.on_demand_tokens
        return d


# --------------------------------------------------------------------------
# quality checks on a skill description
# --------------------------------------------------------------------------

# The official Agent Skills guidance is that `description` is the *only* thing
# the model sees when deciding whether to load a skill. It therefore has to do
# two jobs at once: say what the skill does, and say when to reach for it.
# These checks encode that, and each one names the cost of failing it.

_TRIGGER_HINTS = (
    "use when", "use this when", "trigger", "whenever", "if the user",
    "when the user", "call this", "reach for", "invoke when", "apply when",
)

# Descriptions above this are paying rent in every session for little gain.
DESC_TOKEN_BUDGET = 120
# Below this a description almost never carries enough to route correctly.
DESC_TOKEN_FLOOR = 8


def check_description(desc: str) -> list[str]:
    issues: list[str] = []
    if not desc.strip():
        issues.append("no description — the model has nothing to route on; this skill will effectively never fire")
        return issues

    toks = estimate_tokens(desc)
    if toks > DESC_TOKEN_BUDGET:
        issues.append(
            f"description is ~{toks} tokens (budget {DESC_TOKEN_BUDGET}) — this is always-on cost in every session"
        )
    if toks < DESC_TOKEN_FLOOR:
        issues.append(f"description is ~{toks} tokens — too thin to route on reliably")

    low = desc.lower()
    if not any(h in low for h in _TRIGGER_HINTS):
        issues.append("no explicit trigger condition (\"use when…\") — routing is left to inference")
    if '"' not in desc and "'" not in desc and "use when" not in low:
        pass  # quoted user phrases are a bonus, not a requirement; no issue raised
    if re.search(r"\b(various|etc\.?|and more|many things|anything)\b", low):
        issues.append("vague scope language (\"etc\", \"and more\") — widens triggering, causes false fires")
    return issues


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def scan_skills(root: Path) -> list[Artifact]:
    out: list[Artifact] = []
    base = root / "skills"
    if not base.is_dir():
        return out
    for sk in sorted(base.rglob("SKILL.md")):
        text = _read(sk)
        fm, body, ok = split_frontmatter(text)
        name = fm.get("name") or sk.parent.name
        desc = fm.get("description", "")
        a = Artifact(
            kind="skill",
            name=name,
            path=str(sk),
            always_on_chars=len(desc),
            on_demand_chars=len(body),
            meta={"description": desc, "frontmatter_ok": ok},
        )
        if not ok:
            a.issues.append("no YAML frontmatter — may not load at all")
        a.issues.extend(check_description(desc))
        # bundled resources are a real cost multiplier when the skill does fire
        extras = [p for p in sk.parent.rglob("*") if p.is_file() and p != sk]
        if extras:
            a.meta["bundled_files"] = len(extras)
            a.meta["bundled_bytes"] = sum(p.stat().st_size for p in extras)
        out.append(a)
    return out


def scan_claude_md(root: Path, extra: Iterable[Path] = ()) -> list[Artifact]:
    out: list[Artifact] = []
    for p in [root / "CLAUDE.md", *extra]:
        if p and Path(p).is_file():
            text = _read(Path(p))
            a = Artifact(
                kind="claude-md",
                name=str(Path(p).name),
                path=str(p),
                always_on_chars=len(text),
                meta={"lines": text.count("\n") + 1},
            )
            toks = a.always_on_tokens
            # Anthropic cut their own system prompt by 80% and the model improved.
            # There is no official number for a user CLAUDE.md, so this threshold is
            # ours, chosen to flag files that have clearly become filing cabinets.
            if toks > 1500:
                a.issues.append(
                    f"~{toks} tokens loaded into every session — strong candidate for the 6-month delete-and-see test"
                )
            elif toks > 700:
                a.issues.append(f"~{toks} tokens always-on — worth an audit pass")
            out.append(a)
    return out


def scan_subagents(root: Path) -> list[Artifact]:
    """Subagent descriptions are listed to the model, so they are always-on."""
    out: list[Artifact] = []
    base = root / "agents"
    if not base.is_dir():
        return out
    for p in sorted(base.glob("*.md")):
        fm, body, ok = split_frontmatter(_read(p))
        desc = fm.get("description", "")
        a = Artifact(
            kind="subagent",
            name=fm.get("name") or p.stem,
            path=str(p),
            always_on_chars=len(desc),
            on_demand_chars=len(body),
            meta={"description": desc},
        )
        a.issues.extend(check_description(desc))
        out.append(a)
    return out


def scan_commands(root: Path) -> list[Artifact]:
    out: list[Artifact] = []
    base = root / "commands"
    if not base.is_dir():
        return out
    for p in sorted(base.rglob("*.md")):
        fm, body, ok = split_frontmatter(_read(p))
        desc = fm.get("description", "")
        out.append(
            Artifact(
                kind="command",
                name=fm.get("name") or p.stem,
                path=str(p),
                # the listing shows name + description; the body loads on invocation
                always_on_chars=len(p.stem) + len(desc),
                on_demand_chars=len(body),
                meta={"description": desc},
            )
        )
    return out


def scan_settings(root: Path) -> list[Artifact]:
    """Hooks and MCP servers declared in settings.json.

    MCP tool schemas are the single most under-measured always-on cost in a
    modern setup: every tool a server exposes ships its full JSON schema into
    the system prompt. We can only see the *declaration* here, not the live
    schema, so we report the server and flag it as unmeasured rather than
    guessing a number.
    """
    out: list[Artifact] = []
    for fn in ("settings.json", "settings.local.json"):
        p = root / fn
        if not p.is_file():
            continue
        try:
            cfg = json.loads(_read(p))
        except json.JSONDecodeError:
            out.append(Artifact(kind="hook", name=fn, path=str(p), issues=["settings file is not valid JSON"]))
            continue

        hooks = cfg.get("hooks") or {}
        for event, entries in hooks.items():
            n = len(entries) if isinstance(entries, list) else 1
            out.append(
                Artifact(
                    kind="hook",
                    name=f"{event} ({n})",
                    path=str(p),
                    meta={"event": event, "count": n},
                    issues=["hook output is injected into context at runtime — cost is invisible to static analysis"],
                )
            )
        for server in (cfg.get("mcpServers") or {}):
            out.append(
                Artifact(
                    kind="mcp-server",
                    name=server,
                    path=str(p),
                    issues=["MCP tool schemas are always-on and not measurable statically — run `sift probe` to capture them"],
                )
            )
    return out


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------

@dataclass
class AuditResult:
    root: str
    artifacts: list[Artifact]

    @property
    def always_on_tokens(self) -> int:
        return sum(a.always_on_tokens for a in self.artifacts)

    @property
    def on_demand_tokens(self) -> int:
        return sum(a.on_demand_tokens for a in self.artifacts)

    def by_kind(self) -> dict[str, list[Artifact]]:
        d: dict[str, list[Artifact]] = {}
        for a in self.artifacts:
            d.setdefault(a.kind, []).append(a)
        return d

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "always_on_tokens": self.always_on_tokens,
            "on_demand_tokens": self.on_demand_tokens,
            "estimate": True,
            "chars_per_token": CHARS_PER_TOKEN,
            "artifacts": [a.to_dict() for a in self.artifacts],
        }


def audit(root: str | Path, project_claude_md: Iterable[str | Path] = ()) -> AuditResult:
    root = Path(root).expanduser()
    arts: list[Artifact] = []
    arts += scan_claude_md(root, [Path(p) for p in project_claude_md])
    arts += scan_skills(root)
    arts += scan_subagents(root)
    arts += scan_commands(root)
    arts += scan_settings(root)
    return AuditResult(root=str(root), artifacts=arts)


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "~/.claude"
    res = audit(root, argv[2:])
    d = res.to_dict()
    print(json.dumps(d, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
