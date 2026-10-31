"""Run the tamper suite against live Crustdata data.

    python eval/run_eval.py samples/resume.txt [--profile URL] [--role "..."]

Each résumé is parsed once; doctored copies reuse cached API responses, so the suite costs
about the same credits as one check.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tamper import variants  # noqa: E402
from vouch import parse, verify  # noqa: E402
from vouch.crust import Crust  # noqa: E402


def run_suite(crust, claims, role=None):
    rows = []
    for name, expected, c in variants(claims):
        rep = verify.run(crust, c, role=role)
        caught = [f.claim for f in rep.findings if f.status == "contradicted"]
        rows.append({"case": name, "expected": "/".join(sorted(expected)), "got": rep.decision,
                     "pass": rep.decision in expected, "evidence": caught[:2] or [rep.reason]})
    return rows


def print_table(rows):
    print(f"\n{'case':32} {'expected':24} {'got':14} ok")
    for r in rows:
        print(f"{r['case']:32} {r['expected']:24} {r['got']:14} {'✅' if r['pass'] else '❌'}  {r['evidence'][0][:70]}")
    passed = sum(r["pass"] for r in rows)
    print(f"\n{passed}/{len(rows)} cases passed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("resumes", nargs="+")
    ap.add_argument("--profile")
    ap.add_argument("--role")
    ap.add_argument("--out", default="eval/results.json")
    args = ap.parse_args()
    crust = Crust()
    allrows = []
    for path in args.resumes:
        p = Path(path)
        claims = json.loads(p.read_text()) if p.suffix == ".json" else parse.parse(parse.read_text(p))
        if args.profile:
            claims["profile_url"] = args.profile
        print(f"\n=== {p.name} ({claims.get('name')})")
        rows = run_suite(crust, claims, args.role)
        print_table(rows)
        allrows += [dict(r, resume=p.name) for r in rows]
    Path(args.out).write_text(json.dumps(allrows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
