"""
sift.report — render an audit as a self-contained HTML page.

Why HTML and not a terminal table: the whole argument of this tool is that a
number nobody looks at changes nothing. A render they have not seen does not
exist. The page inlines everything — no CDN, no fonts, no network — so it opens
from a file:// URL on a plane.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit import AuditResult, audit, estimate_tokens  # noqa: E402

# --------------------------------------------------------------------------
# framing numbers
# --------------------------------------------------------------------------

# A working assumption, exposed rather than hidden: a developer using an agent
# heavily starts on the order of 40 sessions a week. The always-on tax is paid
# once per session, so annual cost scales with this. Shown in the UI as an
# assumption the reader can mentally re-scale, never as a hard claim.
SESSIONS_PER_WEEK = 40
WEEKS = 48

KIND_LABEL = {
    "claude-md": "CLAUDE.md",
    "skill": "Skill",
    "subagent": "Subagent",
    "command": "Command",
    "hook": "Hook",
    "mcp-server": "MCP server",
}


def _bar(always: int, demand: int) -> str:
    total = max(always + demand, 1)
    a = round(always / total * 100, 1)
    return f"""
    <div class="bar" role="img" aria-label="{a}% of measured context is always-on">
      <div class="bar-a" style="width:{a}%"></div>
    </div>
    <div class="bar-key">
      <span><i class="sw sw-a"></i>always-on {always:,} tok</span>
      <span><i class="sw sw-d"></i>on-demand {demand:,} tok</span>
    </div>"""


def render(res: AuditResult, title: str = "Context audit") -> str:
    arts = sorted(res.artifacts, key=lambda a: -a.always_on_tokens)
    always, demand = res.always_on_tokens, res.on_demand_tokens
    per_year = always * SESSIONS_PER_WEEK * WEEKS

    issues = [(a, i) for a in arts for i in a.issues]

    rows = []
    for a in arts:
        li = "".join(f"<li>{html.escape(i)}</li>" for i in a.issues)
        rows.append(f"""
        <tr class="{'has-issue' if a.issues else ''}">
          <td class="num">{a.always_on_tokens:,}</td>
          <td class="num dim">{a.on_demand_tokens:,}</td>
          <td><span class="kind k-{a.kind}">{KIND_LABEL.get(a.kind, a.kind)}</span></td>
          <td class="nm">{html.escape(a.name)}
            {f'<ul class="iss">{li}</ul>' if li else ''}
          </td>
        </tr>""")

    # the recommendation panel: only things we can actually justify from data
    recs = []
    heavy = [a for a in arts if a.kind == "skill" and a.always_on_tokens > 120]
    if heavy:
        saved = sum(a.always_on_tokens - 120 for a in heavy)
        recs.append(
            f"Tighten {len(heavy)} skill description{'s' if len(heavy) > 1 else ''} to the "
            f"120-token budget — recovers ~{saved:,} tokens from every session "
            f"(~{saved * SESSIONS_PER_WEEK * WEEKS:,}/year at {SESSIONS_PER_WEEK} sessions a week)."
        )
    cmd = [a for a in arts if a.kind == "claude-md" and a.always_on_tokens > 700]
    for a in cmd:
        recs.append(
            f"<code>{html.escape(a.name)}</code> is ~{a.always_on_tokens:,} always-on tokens. "
            "Run the delete-and-see test: move it aside for one session and find out which "
            "rules the model was already following without being told."
        )
    notrig = [a for a in arts if a.kind == "skill" and any("no explicit trigger" in i for i in a.issues)]
    if notrig:
        recs.append(
            f"{len(notrig)} skill{'s' if len(notrig) > 1 else ''} declare no trigger condition. "
            "These pay always-on rent but fire by inference, which is the worst of both — "
            "cost without reliability."
        )
    if not recs:
        recs.append("Nothing flagged. This setup is inside budget on every check.")

    rec_html = "".join(f"<li>{r}</li>" for r in recs)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{
  --bg:#fbfbfa; --panel:#fff; --ink:#1a1a19; --dim:#6b6b66; --line:#e5e4e0;
  --a:#b4532a; --d:#c9c7c0; --warn:#8a5a00; --warn-bg:#fdf6e7;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}}
:root:not([data-theme="light"]) {{ }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#161614; --panel:#1e1e1c; --ink:#eceae4; --dim:#96938b; --line:#302f2c;
    --a:#e0794a; --d:#4a4842; --warn:#e0b050; --warn-bg:#2a2415;
  }}
}}
:root[data-theme="dark"] {{
  --bg:#161614; --panel:#1e1e1c; --ink:#eceae4; --dim:#96938b; --line:#302f2c;
  --a:#e0794a; --d:#4a4842; --warn:#e0b050; --warn-bg:#2a2415;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Inter,system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}}
.wrap{{max-width:1000px;margin:0 auto;padding:48px 24px 80px}}
h1{{font-size:15px;font-weight:600;letter-spacing:.02em;margin:0 0 4px;color:var(--dim)}}
.root{{font-family:var(--mono);font-size:13px;color:var(--dim);word-break:break-all;margin-bottom:40px}}
.hero{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:32px;margin-bottom:16px}}
.big{{font-size:clamp(44px,9vw,76px);line-height:1;font-weight:600;letter-spacing:-.03em;
  font-variant-numeric:tabular-nums;color:var(--a)}}
.big small{{font-size:.3em;font-weight:500;color:var(--dim);letter-spacing:0;margin-left:.4em}}
.cap{{color:var(--dim);font-size:14px;margin-top:10px;max-width:62ch}}
.bar{{height:9px;border-radius:5px;background:var(--d);overflow:hidden;margin:26px 0 9px}}
.bar-a{{height:100%;background:var(--a)}}
.bar-key{{display:flex;gap:20px;flex-wrap:wrap;font-size:12.5px;color:var(--dim);font-variant-numeric:tabular-nums}}
.sw{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px}}
.sw-a{{background:var(--a)}} .sw-d{{background:var(--d)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:32px}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px}}
.tile b{{display:block;font-size:26px;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.02em}}
.tile span{{font-size:12.5px;color:var(--dim)}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:26px 28px;margin-bottom:32px}}
.panel h2{{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin:0 0 16px;font-weight:600}}
.panel ul{{margin:0;padding-left:20px}} .panel li{{margin-bottom:11px;max-width:74ch}}
code{{font-family:var(--mono);font-size:.9em;background:var(--bg);padding:1px 5px;border-radius:4px;border:1px solid var(--line)}}
.tbl-wrap{{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:14px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;min-width:620px}}
th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);
  font-weight:600;padding:14px 16px;border-bottom:1px solid var(--line);white-space:nowrap}}
td{{padding:11px 16px;border-bottom:1px solid var(--line);vertical-align:top}}
tr:last-child td{{border-bottom:none}}
.num{{font-variant-numeric:tabular-nums;text-align:right;font-family:var(--mono);white-space:nowrap}}
.dim{{color:var(--dim)}}
.nm{{font-family:var(--mono);font-size:12.5px}}
.kind{{font-size:11px;padding:2px 8px;border-radius:20px;border:1px solid var(--line);color:var(--dim);white-space:nowrap}}
.iss{{margin:7px 0 2px;padding-left:16px;font-family:inherit;font-size:12.5px;color:var(--warn)}}
.iss li{{margin:3px 0}}
.has-issue{{background:var(--warn-bg)}}
.foot{{margin-top:40px;font-size:12px;color:var(--dim);max-width:74ch;line-height:1.6}}
</style></head><body><div class="wrap">

<h1>Sift — context audit</h1>
<div class="root">{html.escape(res.root)}</div>

<div class="hero">
  <div class="big">{always:,}<small>tokens, every session</small></div>
  <p class="cap">This is the standing tax. It loads before you type a word, whether or not
  a single skill fires. Measured from files on disk, not estimated from behaviour.</p>
  {_bar(always, demand)}
</div>

<div class="grid">
  <div class="tile"><b>{per_year:,}</b><span>tokens/year at {SESSIONS_PER_WEEK} sessions a week</span></div>
  <div class="tile"><b>{len(arts)}</b><span>artifacts measured</span></div>
  <div class="tile"><b>{len(issues)}</b><span>issues flagged</span></div>
  <div class="tile"><b>{demand:,}</b><span>on-demand tokens, paid only when triggered</span></div>
</div>

<div class="panel">
  <h2>What to do</h2>
  <ul>{rec_html}</ul>
</div>

<div class="tbl-wrap"><table>
  <thead><tr>
    <th class="num">Always-on</th><th class="num">On-demand</th><th>Kind</th><th>Name &amp; findings</th>
  </tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table></div>

<p class="foot">Token counts are estimates from a character heuristic
(~{res.to_dict()['chars_per_token']} chars/token), not a tokenizer. They are accurate enough to
rank and to decide what to cut; they are not billing figures. Hook output and MCP tool
schemas are always-on but cannot be measured from static files — both are listed as
unmeasured rather than guessed at.</p>

</div></body></html>"""


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "~/.claude"
    out = Path(argv[2]) if len(argv) > 2 else Path("reports/audit.html")
    res = audit(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(res), encoding="utf-8")
    print(f"{out}  ({out.stat().st_size:,} bytes)  always-on ~{res.always_on_tokens:,} tok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
