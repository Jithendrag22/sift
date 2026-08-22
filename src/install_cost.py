"""What does installing a whole skill collection cost you, every session, forever?

Installing a collection means every skill's *description* joins the always-on set.
Bodies are on-demand. This computes the standing tax per repo.
"""
import json,os,sys,re
sys.path.insert(0,os.path.dirname(__file__))
from audit import split_frontmatter, estimate_tokens, check_description

# Corpus root is an argument:  python3 src/install_cost.py /path/to/skill-repos
VAULT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
os.makedirs(OUT, exist_ok=True)
rows={}
for dirpath,dirs,files in os.walk(VAULT):
    if '.git' in dirpath.split(os.sep): continue
    if 'SKILL.md' not in files: continue
    p=os.path.join(dirpath,'SKILL.md')
    rel=os.path.relpath(p,VAULT).split(os.sep)
    if len(rel)<2: continue
    repo=f'{rel[0]}/{rel[1]}'
    try: t=open(p,encoding='utf-8',errors='replace').read()
    except OSError: continue
    fm,body,ok=split_frontmatter(t)
    d=fm.get('description','')
    r=rows.setdefault(repo,{'n':0,'always':0,'demand':0,'nodesc':0,'notrig':0,'bloated':0})
    r['n']+=1
    r['always']+=estimate_tokens(d); r['demand']+=estimate_tokens(body)
    if not d.strip(): r['nodesc']+=1
    else:
        iss=check_description(d)
        if any('no explicit trigger' in i for i in iss): r['notrig']+=1
        if any('budget' in i for i in iss): r['bloated']+=1
json.dump(rows, open(os.path.join(OUT, 'install_cost.json'), 'w'), indent=1)
tot=sum(r['always'] for r in rows.values()); n=sum(r['n'] for r in rows.values())
print(f"{'repo':<42}{'skills':>7}{'always-on':>11}{'on-demand':>11}{'no trig':>9}{'bloated':>9}")
for repo,r in sorted(rows.items(),key=lambda x:-x[1]['always'])[:20]:
    print(f"{repo:<42}{r['n']:>7}{r['always']:>11,}{r['demand']:>11,}{r['notrig']:>9}{r['bloated']:>9}")
print(f"\n{n:,} skills across {len(rows)} repos")
print(f"install ALL -> {tot:,} always-on tokens in every session")
