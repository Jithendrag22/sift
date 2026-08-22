#!/usr/bin/env python3
"""
Sift corpus miner.

Walks the local SKILL.md vault, parses YAML frontmatter, computes token-cost
estimates, near-duplicate clusters and description-quality signals, then writes:

  1. sift/data/corpus.jsonl   -- one JSON record per SKILL.md (see SCHEMA below)
  2. a stats report on stdout (also sift/data/corpus_report.txt)

Everything here is computed from the corpus. Token counts are ESTIMATES
(len(text)/4 chars-per-token, the standard rough heuristic) -- no tokenizer
dependency is assumed.

corpus.jsonl SCHEMA (one JSON object per line, UTF-8, newline delimited)
-----------------------------------------------------------------------
  repo            str   "<category>/<repo>" e.g. "skills/anthropic-skills"
  category        str   top-level vault folder, e.g. "skills", "harnesses"
  path            str   path relative to the vault root, incl. SKILL.md
  name            str   frontmatter `name`, or "" when absent/unparseable
  description     str   frontmatter `description`, or "" when absent
  body_chars      int   characters of the body (everything after frontmatter)
  desc_chars      int   characters of the description field
  frontmatter_ok  bool  true iff YAML frontmatter parsed AND has non-empty
                        `name` and `description` (both scalars)
  fm_error        str   "" when ok, else machine-readable reason:
                        no_delimiter | unterminated | yaml_error | not_mapping |
                        missing_name | missing_description | empty_file
  has_scripts     bool  body references an external script/binary/network call
  script_kinds    list  subset of ["script_path","shell_cmd","network","tool_bin"]
  normalised_hash str   sha1 of the normalised body (lowercased, punctuation
                        and whitespace collapsed) -- exact-dup key
  simhash         str   64-bit hex SimHash of body word-5-shingles -- near-dup key
  est_desc_tokens int   round(desc_chars/4)
  est_body_tokens int   round(body_chars/4)
  has_triggers    bool  description contains an explicit trigger phrase
  trigger_kinds   list  subset of ["use_when","use_for","proactively","quoted",
                                   "when_clause","trigger_word"]
"""

import hashlib
import json
import os, sys
import re
import statistics
import sys
from collections import Counter, defaultdict

try:
    import yaml
except ImportError:
    yaml = None

# Corpus root and output directory are arguments, not constants. Point this at any
# directory tree containing SKILL.md files:
#     python3 src/corpus_stats.py /path/to/skills [output-dir]
# Defaults assume you are running from the repository root.
VAULT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
OUT_DIR = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(OUT_DIR, exist_ok=True)
OUT_JSONL = os.path.join(OUT_DIR, "corpus.jsonl")
OUT_REPORT = os.path.join(OUT_DIR, "corpus_report.txt")

# Single source of truth for the estimate. Previously this module used 4.0 while
# audit.py used 3.6, which put corpus figures and tool figures ~11% apart by
# construction — two numbers in the same product that could never agree.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit import CHARS_PER_TOKEN as CPT

# ---------------------------------------------------------------- frontmatter

FM_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def parse_frontmatter(text):
    """Return (meta_dict_or_None, body, error_code)."""
    if not text.strip():
        return None, "", "empty_file"
    m = FM_RE.match(text)
    if not m:
        if text.lstrip().startswith("---"):
            return None, text, "unterminated"
        return None, text, "no_delimiter"
    raw, body = m.group(1), text[m.end():]
    try:
        meta = yaml.safe_load(raw) if yaml else _mini_yaml(raw)
    except Exception:
        return None, body, "yaml_error"
    if not isinstance(meta, dict):
        return None, body, "not_mapping"
    return meta, body, ""


def _mini_yaml(raw):
    """Fallback flat key: value parser if PyYAML is unavailable."""
    out = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line[:1] in " \t-":
            continue
        if ":" not in line:
            raise ValueError("not flat yaml")
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


def scalar(v):
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v).strip()
    return ""  # lists/dicts in name/description are malformed for our purposes


# ------------------------------------------------------------------- triggers

TRIGGER_PATTERNS = [
    ("use_when", re.compile(r"\buse\s+(this\s+\w+\s+)?when\b", re.I)),
    ("use_for", re.compile(r"\buse\s+(this|it)?\s*(skill\s+)?(for|to)\b", re.I)),
    ("proactively", re.compile(r"\bproactively\b|\bPROACTIVELY\b")),
    ("quoted", re.compile(r"[\"'“‘][^\"'”’]{6,80}[\"'”’]")),
    ("when_clause", re.compile(r"\b(when|whenever|if the user|after the user)\b", re.I)),
    ("trigger_word", re.compile(r"\b(trigger[s]?\s+on|invoke[d]?\s+when|call\s+this\s+when|"
                                r"applies\s+when|for\s+requests\s+like)\b", re.I)),
]

# ------------------------------------------------------- external-script signals

SCRIPT_RE = re.compile(
    r"(?:^|[\s`(\"'])(?:\./|scripts?/|bin/|assets/|src/)[\w./-]+\.(?:py|sh|js|ts|rb|pl|"
    r"ps1|bat|jar|go|R)\b", re.M)
SHELL_RE = re.compile(
    r"^\s*(?:\$\s*)?(?:python3?|node|npx|uv|uvx|bash|sh|deno|bun|pip3?|go\s+run|"
    r"cargo|ruby|java|make)\s+\S", re.M)
NET_RE = re.compile(r"\b(?:curl\s+|wget\s+|https?://(?!(?:github\.com|www\.w3|schema\.org)\S*\)?$)|"
                    r"fetch\(|requests\.(?:get|post)|axios\.|urllib\.request|WebFetch|WebSearch)", re.I)
TOOLBIN_RE = re.compile(r"^\s*(?:gh|git|docker|kubectl|aws|gcloud|az|terraform|ffmpeg|"
                        r"pandoc|jq|rg|sed|awk)\s+\S", re.M)


def code_blocks_and_body(body):
    return body


def script_signals(body):
    kinds = []
    if SCRIPT_RE.search(body):
        kinds.append("script_path")
    if SHELL_RE.search(body):
        kinds.append("shell_cmd")
    if NET_RE.search(body):
        kinds.append("network")
    if TOOLBIN_RE.search(body):
        kinds.append("tool_bin")
    return kinds


# ------------------------------------------------------------- normalise/hash

WORD_RE = re.compile(r"[a-z0-9]+")


def norm_words(text):
    return WORD_RE.findall(text.lower())


def normalised_hash(body):
    return hashlib.sha1(" ".join(norm_words(body)).encode()).hexdigest()


def simhash(words, k=5, bits=64):
    """SimHash over word k-shingles. Hamming distance <= 3 ~= near-identical."""
    if len(words) < k:
        shingles = [" ".join(words)] if words else []
    else:
        shingles = [" ".join(words[i:i + k]) for i in range(len(words) - k + 1)]
    if not shingles:
        return 0
    v = [0] * bits
    for sh in set(shingles):
        h = int.from_bytes(hashlib.md5(sh.encode()).digest()[:8], "big")
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= (1 << i)
    return out


def hamming(a, b):
    return bin(a ^ b).count("1")


# ------------------------------------------------------------------- stats

def pct(sorted_vals, p):
    if not sorted_vals:
        return 0
    i = min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def dist(vals):
    s = sorted(vals)
    return {
        "n": len(s),
        "median": pct(s, 50),
        "p90": pct(s, 90),
        "p99": pct(s, 99),
        "max": s[-1] if s else 0,
        "mean": round(statistics.fmean(s), 1) if s else 0,
        "sum": sum(s),
    }


# ---------------------------------------------------------------------- main

def collect():
    records = []
    for dirpath, dirnames, filenames in os.walk(VAULT):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        if "SKILL.md" not in filenames:
            continue
        full = os.path.join(dirpath, "SKILL.md")
        rel = os.path.relpath(full, VAULT)
        parts = rel.split(os.sep)
        category = parts[0] if len(parts) > 1 else ""
        repo = "/".join(parts[:2]) if len(parts) > 2 else category
        try:
            text = open(full, encoding="utf-8", errors="replace").read()
        except OSError as e:
            print(f"skip {rel}: {e}", file=sys.stderr)
            continue

        meta, body, err = parse_frontmatter(text)
        name = desc = ""
        if meta is not None:
            name = scalar(meta.get("name"))
            desc = scalar(meta.get("description"))
            if not name:
                err = "missing_name"
            elif not desc:
                err = "missing_description"
        ok = (err == "")

        words = norm_words(body)
        kinds = script_signals(body)
        tk = [k for k, rx in TRIGGER_PATTERNS if rx.search(desc)] if desc else []

        records.append({
            "repo": repo,
            "category": category,
            "path": rel,
            "name": name,
            "description": desc,
            "body_chars": len(body),
            "desc_chars": len(desc),
            "frontmatter_ok": ok,
            "fm_error": err,
            "has_scripts": bool(kinds),
            "script_kinds": kinds,
            "normalised_hash": normalised_hash(body),
            "simhash": format(simhash(words), "016x"),
            "est_desc_tokens": round(len(desc) / CPT),
            "est_body_tokens": round(len(body) / CPT),
            "has_triggers": bool(tk),
            "trigger_kinds": tk,
        })
    records.sort(key=lambda r: r["path"])
    return records


def near_dup_clusters(records, threshold=3):
    """Union-find over SimHash banding (4 bands x 16 bits)."""
    parent = list(range(len(records)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    sims = [int(r["simhash"], 16) for r in records]
    for band in range(4):
        buckets = defaultdict(list)
        shift = band * 16
        for i, s in enumerate(sims):
            buckets[(s >> shift) & 0xFFFF].append(i)
        for idxs in buckets.values():
            if len(idxs) < 2:
                continue
            if len(idxs) > 400:          # huge bucket: link via representative
                rep = idxs[0]
                for j in idxs[1:]:
                    if hamming(sims[rep], sims[j]) <= threshold:
                        union(rep, j)
                continue
            for a in range(len(idxs)):
                for b in range(a + 1, len(idxs)):
                    if hamming(sims[idxs[a]], sims[idxs[b]]) <= threshold:
                        union(idxs[a], idxs[b])
    clusters = defaultdict(list)
    for i in range(len(records)):
        clusters[find(i)].append(i)
    return [v for v in clusters.values() if len(v) > 1]


def main():
    recs = collect()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    L = []
    def p(s=""):
        L.append(s)
        print(s)

    n = len(recs)
    ok = [r for r in recs if r["frontmatter_ok"]]
    bad = [r for r in recs if not r["frontmatter_ok"]]
    p(f"SIFT CORPUS STATS  n={n} SKILL.md files   vault={VAULT}")
    p(f"tokenizer: NONE -- all token figures are ESTIMATES at {CPT} chars/token")
    p()
    p("== Q1 frontmatter validity ==")
    p(f"valid (name+description present, YAML parses): {len(ok)} ({len(ok)/n:.1%})")
    p(f"malformed: {len(bad)} ({len(bad)/n:.1%})")
    for k, c in Counter(r["fm_error"] for r in bad).most_common():
        p(f"  {k:<20} {c:>6}  {c/n:>6.2%}")
    p("  malformed examples:")
    seen = set()
    for r in bad:
        if r["fm_error"] in seen:
            continue
        seen.add(r["fm_error"])
        p(f"    [{r['fm_error']}] {r['path']}")
    p()

    p("== Q2 token cost (ESTIMATE, chars/4) ==")
    dd = dist([r["desc_chars"] for r in ok])
    bd = dist([r["body_chars"] for r in recs])
    p("(a) description only -- the always-loaded cost")
    p(f"  chars  median {dd['median']}  p90 {dd['p90']}  p99 {dd['p99']}  max {dd['max']}  mean {dd['mean']}")
    p(f"  ~tok   median {dd['median']/CPT:.0f}  p90 {dd['p90']/CPT:.0f}  p99 {dd['p99']/CPT:.0f}  max {dd['max']/CPT:.0f}")
    p(f"  total if ALL {len(ok)} descriptions loaded: ~{dd['sum']/CPT:,.0f} tokens")
    p("(b) full body -- loads on trigger")
    p(f"  chars  median {bd['median']}  p90 {bd['p90']}  p99 {bd['p99']}  max {bd['max']}  mean {bd['mean']}")
    p(f"  ~tok   median {bd['median']/CPT:.0f}  p90 {bd['p90']/CPT:.0f}  p99 {bd['p99']/CPT:.0f}  max {bd['max']/CPT:.0f}")
    p(f"  total corpus body: ~{bd['sum']/CPT:,.0f} tokens")
    big = sorted(recs, key=lambda r: -r["body_chars"])[:5]
    for r in big:
        p(f"  heaviest: ~{r['body_chars']/CPT:>7,.0f} tok  {r['path']}")
    p()

    p("== Q3 duplication ==")
    exact = defaultdict(list)
    for r in recs:
        exact[r["normalised_hash"]].append(r)
    dup_groups = [v for v in exact.values() if len(v) > 1]
    dup_files = sum(len(v) for v in dup_groups)
    p(f"exact normalised-text duplicates: {dup_files} files in {len(dup_groups)} groups "
      f"({dup_files - len(dup_groups)} redundant copies, {(dup_files-len(dup_groups))/n:.1%} of corpus)")
    p(f"unique normalised bodies: {len(exact)}")
    clusters = near_dup_clusters(recs)
    near_files = sum(len(c) for c in clusters)
    p(f"near-duplicate (SimHash 5-shingle, hamming<=3): {near_files} files in {len(clusters)} clusters "
      f"({near_files - len(clusters)} redundant, {(near_files-len(clusters))/n:.1%})")
    p("largest near-dup clusters:")
    for c in sorted(clusters, key=len, reverse=True)[:12]:
        names = Counter(recs[i]["name"] or os.path.basename(os.path.dirname(recs[i]["path"])) for i in c)
        repos = Counter(recs[i]["repo"] for i in c)
        label = ", ".join(f"{k}" for k, _ in names.most_common(3))
        p(f"  {len(c):>4} files | {len(repos)} repos | {label}")
        p(f"       repos: {', '.join(f'{k}({v})' for k, v in repos.most_common(4))}")
    p()

    p("== Q4 description quality ==")
    with_t = [r for r in ok if r["has_triggers"]]
    p(f"descriptions with any explicit trigger phrase: {len(with_t)}/{len(ok)} ({len(with_t)/len(ok):.1%})")
    kc = Counter(k for r in ok for k in r["trigger_kinds"])
    for k, c in kc.most_common():
        p(f"  {k:<14} {c:>6}  {c/len(ok):>6.1%}")
    strong = [r for r in ok if "use_when" in r["trigger_kinds"] or "trigger_word" in r["trigger_kinds"]]
    p(f"  strong-form ('Use when' / explicit trigger): {len(strong)} ({len(strong)/len(ok):.1%})")
    quoted = [r for r in ok if "quoted" in r["trigger_kinds"]]
    p(f"  quoted user phrases: {len(quoted)} ({len(quoted)/len(ok):.1%})")
    buckets = Counter()
    for r in ok:
        d = r["desc_chars"]
        b = "<50" if d < 50 else "50-99" if d < 100 else "100-199" if d < 200 else \
            "200-399" if d < 400 else "400-799" if d < 800 else "800+"
        buckets[b] += 1
    for b in ["<50", "50-99", "100-199", "200-399", "400-799", "800+"]:
        p(f"  len {b:<8} {buckets[b]:>6}  {buckets[b]/len(ok):>6.1%}")
    p()

    p("== Q5 external scripts / binaries / network ==")
    hs = [r for r in recs if r["has_scripts"]]
    p(f"skills referencing external execution of any kind: {len(hs)} ({len(hs)/n:.1%})")
    for k, c in Counter(k for r in recs for k in r["script_kinds"]).most_common():
        p(f"  {k:<12} {c:>6}  {c/n:>6.1%}")
    p()

    p("== Q6 repo originality ==")
    # First-seen ownership by normalised hash, and cross-repo overlap.
    hash_repos = defaultdict(set)
    for r in recs:
        hash_repos[r["normalised_hash"]].add(r["repo"])
    # cluster-level (near-dup) ownership
    cl_of = {}
    for ci, c in enumerate(clusters):
        for i in c:
            cl_of[i] = ci
    key_of = []
    for i, r in enumerate(recs):
        key_of.append(("c", cl_of[i]) if i in cl_of else ("h", r["normalised_hash"]))
    key_repos = defaultdict(set)
    for i, r in enumerate(recs):
        key_repos[key_of[i]].add(r["repo"])

    rows = []
    for repo in sorted({r["repo"] for r in recs}):
        idxs = [i for i, r in enumerate(recs) if r["repo"] == repo]
        keys = {key_of[i] for i in idxs}
        uniq = sum(1 for k in keys if len(key_repos[k]) == 1)
        rows.append((repo, len(idxs), len(keys), uniq, uniq / len(keys) if keys else 0))
    rows.sort(key=lambda x: -x[1])
    p(f"{'repo':<42}{'files':>7}{'distinct':>9}{'repo-exclusive':>15}{'excl%':>8}")
    for repo, nf, nk, uq, fr in rows[:22]:
        p(f"{repo:<42}{nf:>7}{nk:>9}{uq:>15}{fr:>7.0%}")
    p()
    p("pairwise overlap between the 6 largest collections (shared distinct skills):")
    big_repos = [r[0] for r in rows[:6]]
    rk = {repo: {key_of[i] for i, r in enumerate(recs) if r["repo"] == repo} for repo in big_repos}
    for a in range(len(big_repos)):
        for b in range(a + 1, len(big_repos)):
            A, B = rk[big_repos[a]], rk[big_repos[b]]
            inter = len(A & B)
            if inter:
                p(f"  {big_repos[a]} ^ {big_repos[b]}: {inter} shared "
                  f"({inter/len(A):.0%} of first, {inter/len(B):.0%} of second, jaccard {inter/len(A|B):.2f})")

    p("== Q6b lineage: same skill NAME across repos (body may be rewritten) ==")
    byname = defaultdict(set)
    for r in recs:
        if r["name"]:
            byname[r["name"].strip().lower()].add(r["repo"])
    cross = {k: v for k, v in byname.items() if len(v) > 1}
    p(f"distinct skill names: {len(byname)}; names present in >1 repo: {len(cross)} ({len(cross)/len(byname):.1%})")
    rn = {repo: {r["name"].strip().lower() for r in recs if r["repo"] == repo and r["name"]}
          for repo in big_repos + ["harnesses/wshobson-agents"]}
    for a in range(len(big_repos)):
        for b in range(a + 1, len(big_repos)):
            A, B = rn[big_repos[a]], rn[big_repos[b]]
            if A & B:
                p(f"  {big_repos[a]} ^ {big_repos[b]}: {len(A&B)} shared names "
                  f"({len(A&B)/len(A):.0%} of first, {len(A&B)/len(B):.0%} of second)")
    A, B = rn["skills/agentic-awesome-skills"], rn["harnesses/wshobson-agents"]
    shared = A & B
    p(f"  NOTABLE: agentic-awesome-skills carries {len(shared)} of wshobson-agents' "
      f"{len(B)} names ({len(shared)/len(B):.0%}).")
    # measure how much of the BODY survived, by jaccard on 5-shingles
    body_cache = {}
    def shingles(path):
        if path not in body_cache:
            t = open(os.path.join(VAULT, path), encoding="utf-8", errors="replace").read()
            w = norm_words(t)
            body_cache[path] = set(" ".join(w[i:i+5]) for i in range(max(0, len(w) - 4)))
        return body_cache[path]
    idx = defaultdict(list)
    for r in recs:
        if r["repo"] == "skills/agentic-awesome-skills" and r["name"]:
            idx[r["name"].strip().lower()].append(r["path"])
    js = []
    for r in recs:
        if r["repo"] != "harnesses/wshobson-agents" or not r["name"]:
            continue
        nm = r["name"].strip().lower()
        if nm not in idx:
            continue
        sa = shingles(r["path"])
        js.append(max(len(sa & shingles(q)) / max(1, len(sa | shingles(q))) for q in idx[nm]))
    if js:
        js.sort()
        p(f"  body Jaccard for those {len(js)} name-matched pairs: median {pct(js,50):.2f}, "
          f">=0.5 in {sum(1 for x in js if x>=0.5)} pairs, >=0.7 in {sum(1 for x in js if x>=0.7)}")
        p("  => the name/taxonomy is inherited; most bodies were rewritten or regenerated.")
    p()
    p(f"wrote {OUT_JSONL} ({n} records)")

    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
