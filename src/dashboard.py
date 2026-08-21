"""
sift.dashboard — the one page that shows a setup as it actually is.

Merges the two halves of Sift:

  PROJECTED   from files on disk. What your config *should* cost.
  OBSERVED    from session logs. What it *did* cost, and which skills ever fired.

Keeping them visually distinct is the point. Everyone else in this space reports
one number and lets you assume it is the other. A projection presented as a
measurement is the failure mode we exist to correct, so the page labels every
figure with where it came from.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import calibrate as cal  # noqa: E402
from audit import audit  # noqa: E402
from budget import cache_multiplier  # noqa: E402


def _pill(text: str, tone: str) -> str:
    return f'<span class="pill {tone}">{html.escape(text)}</span>'


def render(a, c: dict, title: str = "Setup ledger") -> str:
    listed = c.get("skills", {}).get("listed", 0)
    fired = c.get("skills", {}).get("ever_fired", 0)
    never = c.get("skills", {}).get("never_fired", 0)
    turns = c.get("turns_per_session", {}).get("median", 0) or 45
    mult = cache_multiplier(turns)
    cold = c.get("observed_cold_prefix_tokens", {}) or {}
    mix = c.get("input_token_mix", {}) or {}

    fire_pct = (fired / listed * 100) if listed else 0
    projected = a.always_on_tokens

    # dead-weight ring: the fraction of listed skills that have never fired
    R, CIRC = 52, 2 * 3.14159 * 52
    dead_frac = (never / listed) if listed else 0
    dash = CIRC * dead_frac

    top = c.get("skills", {}).get("top_fired", [])
    never_names = c.get("skills", {}).get("never_fired_names", [])

    fired_rows = "".join(
        f'<tr><td class="nm">{html.escape(n)}</td><td class="r k">{v:,}</td></tr>'
        for n, v in top
    ) or '<tr><td colspan="2" class="dim">No skill firings recorded in this window.</td></tr>'

    never_items = "".join(f"<li>{html.escape(n)}</li>" for n in never_names[:30])
    more = f'<li class="dim">and {never - 30} more</li>' if never > 30 else ""

    art_rows = "".join(
        f'<tr><td class="nm">{html.escape(x.name)}</td>'
        f'<td>{_pill(x.kind, "n")}</td>'
        f'<td class="r">{x.always_on_tokens:,}</td>'
        f'<td class="r dim">{x.on_demand_tokens:,}</td></tr>'
        for x in sorted(a.artifacts, key=lambda z: -z.always_on_tokens)[:14]
    )

    return f"""<title>{html.escape(title)}</title>
<style>
:root{{
  --paper:#eceeea; --card:#f6f7f4; --ink:#151a16; --dim:#5c655d; --faint:#8b938c;
  --rule:#d4d9d1; --rule-hard:#b9c0b6;
  --waste:#a3402a; --waste-bg:#f3e3de; --keep:#42654c; --keep-bg:#e2eae2;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --paper:#12150f; --card:#1a1e18; --ink:#e6e9e0; --dim:#98a096; --faint:#6d756c;
  --rule:#2b2f28; --rule-hard:#3d423a;
  --waste:#e07a54; --waste-bg:#2c1a14; --keep:#7fa98a; --keep-bg:#17241a;}}}}
:root[data-theme="dark"]{{
  --paper:#12150f; --card:#1a1e18; --ink:#e6e9e0; --dim:#98a096; --faint:#6d756c;
  --rule:#2b2f28; --rule-hard:#3d423a;
  --waste:#e07a54; --waste-bg:#2c1a14; --keep:#7fa98a; --keep-bg:#17241a;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:15px;
  line-height:1.55;-webkit-font-smoothing:antialiased}}
.sheet{{max-width:1000px;margin:0 auto;padding:0 26px 84px}}
.mast{{border-bottom:2px solid var(--rule-hard);padding:40px 0 13px;display:flex;
  justify-content:space-between;align-items:baseline;gap:20px;flex-wrap:wrap}}
.mast h1{{font-family:var(--mono);font-size:12.5px;font-weight:600;letter-spacing:.18em;
  text-transform:uppercase;margin:0}}
.mast .meta{{font-family:var(--mono);font-size:11px;color:var(--faint);letter-spacing:.05em}}
h2{{font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.16em;
  text-transform:uppercase;color:var(--faint);margin:46px 0 0;padding-bottom:9px;
  border-bottom:1px solid var(--rule);display:flex;justify-content:space-between;align-items:center;gap:12px}}
.src{{font-size:9.5px;letter-spacing:.1em;padding:2px 8px;border-radius:20px;border:1px solid var(--rule-hard)}}
.src.obs{{color:var(--keep);border-color:var(--keep)}}
.src.proj{{color:var(--dim)}}
.headline{{display:grid;grid-template-columns:150px minmax(0,1fr);gap:34px;align-items:center;
  padding:40px 0 38px;border-bottom:1px solid var(--rule)}}
.ring{{position:relative;width:130px;height:130px}}
.ring svg{{transform:rotate(-90deg)}}
.ring .lab{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
  justify-content:center;font-family:var(--mono)}}
.ring .lab b{{font-size:32px;font-weight:600;letter-spacing:-.03em;color:var(--waste);line-height:1}}
.ring .lab span{{font-size:9px;letter-spacing:.1em;color:var(--faint);margin-top:5px;text-transform:uppercase}}
.headline p{{margin:0;font-size:clamp(19px,2.6vw,26px);line-height:1.35;text-wrap:balance;max-width:26ch}}
.headline .sub{{font-size:14px;color:var(--dim);margin-top:14px;max-width:52ch;line-height:1.6}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:11px;margin-top:22px}}
.tile{{background:var(--card);border:1px solid var(--rule);border-radius:3px;padding:16px 18px}}
.tile b{{display:block;font-family:var(--mono);font-size:24px;font-weight:600;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums;line-height:1.1}}
.tile span{{display:block;font-size:11.5px;color:var(--faint);margin-top:6px;line-height:1.4}}
.tile.w b{{color:var(--waste)}} .tile.k b{{color:var(--keep)}}
.split{{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:26px;margin-top:22px}}
.scroll{{overflow-x:auto;border:1px solid var(--rule);border-radius:3px;background:var(--card)}}
table{{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px;min-width:260px}}
th{{text-align:left;font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);
  font-weight:600;padding:11px 14px;border-bottom:1px solid var(--rule-hard);white-space:nowrap}}
td{{padding:8px 14px;border-bottom:1px solid var(--rule);white-space:nowrap}}
tr:last-child td{{border-bottom:none}}
.r{{text-align:right;font-variant-numeric:tabular-nums}}
.k{{color:var(--keep)}} .w{{color:var(--waste)}} .dim{{color:var(--dim)}}
.nm{{max-width:230px;overflow:hidden;text-overflow:ellipsis}}
.pill{{font-size:9.5px;padding:2px 7px;border-radius:20px;border:1px solid var(--rule-hard);color:var(--dim)}}
.dead{{background:var(--waste-bg);border:1px solid var(--waste);border-radius:3px;padding:18px 20px}}
.dead h3{{margin:0 0 4px;font-size:13px;font-family:var(--mono);letter-spacing:.06em;
  text-transform:uppercase;color:var(--waste)}}
.dead p{{margin:0 0 12px;font-size:12.5px;color:var(--dim)}}
.dead ul{{margin:0;padding-left:18px;columns:2;column-gap:22px;font-family:var(--mono);font-size:11.5px}}
.dead li{{margin-bottom:3px;break-inside:avoid}}
.note{{margin-top:44px;padding-top:20px;border-top:1px solid var(--rule);font-family:var(--mono);
  font-size:11px;color:var(--faint);line-height:1.8;max-width:80ch}}
@media(max-width:620px){{.headline{{grid-template-columns:1fr;gap:22px}}.dead ul{{columns:1}}}}
</style>
<div class="sheet">

<header class="mast">
  <h1>Setup Ledger</h1>
  <div class="meta">{html.escape(str(a.root))} · {c.get('sessions',0)} SESSIONS · {c.get('calls',0):,} CALLS</div>
</header>

<section class="headline">
  <div class="ring">
    <svg width="130" height="130" viewBox="0 0 130 130" role="img"
         aria-label="{never} of {listed} skills have never fired">
      <circle cx="65" cy="65" r="{R}" fill="none" stroke="var(--rule)" stroke-width="11"/>
      <circle cx="65" cy="65" r="{R}" fill="none" stroke="var(--waste)" stroke-width="11"
              stroke-dasharray="{dash:.1f} {CIRC:.1f}" stroke-linecap="butt"/>
    </svg>
    <div class="lab"><b>{never}</b><span>never fired</span></div>
  </div>
  <div>
    <p>{listed} skills are described to the model on every session. {fired} has ever fired.</p>
    <div class="sub">Not a projection. Assistant records carry an <code>attributionSkill</code>
    field, so this is counted from {c.get('calls',0):,} real calls across {c.get('sessions',0)}
    sessions. The other {never} pay their always-on cost and return nothing.</div>
  </div>
</section>

<h2>Observed <span class="src obs">FROM SESSION LOGS</span></h2>
<div class="tiles">
  <div class="tile"><b>{(cold.get('median') or 0):,}</b><span>median cold-start prefix, tokens
    (range {(cold.get('min') or 0):,}–{(cold.get('max') or 0):,})</span></div>
  <div class="tile"><b>{turns}</b><span>median turns per session
    (max {c.get('turns_per_session',{}).get('max',0):,})</span></div>
  <div class="tile"><b>×{mult:.1f}</b><span>your cache multiplier — 2.0 write + 0.1 per re-read</span></div>
  <div class="tile k"><b>{mix.get('cache_read_pct','—')}%</b><span>of input tokens are cache reads,
    across {(mix.get('total_input_tokens') or 0):,}</span></div>
</div>

<h2>Projected <span class="src proj">FROM FILES ON DISK</span></h2>
<div class="tiles">
  <div class="tile"><b>{projected:,}</b><span>always-on tokens Sift can see on disk</span></div>
  <div class="tile"><b>{int(projected*mult):,}</b><span>billable input tokens per session at ×{mult:.1f}</span></div>
  <div class="tile"><b>{a.on_demand_tokens:,}</b><span>on-demand — paid only when triggered</span></div>
  <div class="tile"><b>{fire_pct:.0f}%</b><span>of listed skills have ever earned their place</span></div>
</div>

<div class="split">
  <div>
    <h2 style="margin-top:26px">Earning their place <span class="src obs">OBSERVED</span></h2>
    <div class="scroll" style="margin-top:12px"><table>
      <thead><tr><th>Skill</th><th class="r">Fires</th></tr></thead>
      <tbody>{fired_rows}</tbody></table></div>
  </div>
  <div>
    <h2 style="margin-top:26px">Heaviest on disk <span class="src proj">PROJECTED</span></h2>
    <div class="scroll" style="margin-top:12px"><table>
      <thead><tr><th>Artifact</th><th>Kind</th><th class="r">On</th><th class="r">Demand</th></tr></thead>
      <tbody>{art_rows}</tbody></table></div>
  </div>
</div>

{'<div class="dead" style="margin-top:34px"><h3>Never fired once</h3><p>Listed to the model in every session across the whole log window, and never selected. Check the window covers normal use before deleting — absence of firing is evidence, not proof.</p><ul>' + never_items + more + '</ul></div>' if never_names else ''}

<p class="note">
  Projected figures are character-heuristic estimates from files, not tokenizer counts, and
  cover only what is visible on disk — the observed prefix is larger because most of it is
  built into the CLI. Observed figures are counted from your own session logs: counts, token
  totals and skill names only, never message content. The cache multiplier follows from
  Claude Code writing the standing prefix at a 1-hour TTL (2× base input price) and re-reading
  it at 0.1× on each subsequent turn.
</p>

</div>"""


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "~/.claude"
    out = Path(argv[2]) if len(argv) > 2 else Path("reports/ledger.html")
    a = audit(root)
    c = cal.collect()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(a, c), encoding="utf-8")
    print(f"{out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
