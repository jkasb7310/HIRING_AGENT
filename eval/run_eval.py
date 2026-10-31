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
    """Honest copies must not be flagged and rewording must not change the decision.
    Each lie must add a contradiction the honest copy didn't have (or, for identity, fail to match)."""
    rows, base, base_contra = [], None, set()
    honest = verify.run(crust, claims, role=role)
    on_record = {f.claim.removeprefix("Worked at ") for f in honest.findings
                 if f.status == "confirmed" and f.claim.startswith("Worked at ")}
    checkable = {i for i, e in enumerate(claims.get("experience", [])) if e.get("company") in on_record}
    for name, expected, c in variants(claims, checkable):
        rep = verify.run(crust, c, role=role)
        contra = {f.claim for f in rep.findings if f.status == "contradicted"}
        if name == "honest":
            base, base_contra = rep.decision, contra
            ok, want = rep.decision != "FLAGGED" and not contra, "not FLAGGED"
        elif name.startswith("honest"):
            ok, want = rep.decision == base, f"same ({base})"
        elif name.startswith("identity"):
            ok, want = rep.identity.get("status") != "matched" and rep.decision == "NEEDS REVIEW", "NEEDS REVIEW"
        else:
            new = contra - base_contra
            ok, want = bool(new) and rep.decision in expected, "caught: " + "/".join(sorted(expected))
        caught = sorted(contra - base_contra)
        rows.append({"case": name, "expected": want, "got": rep.decision, "pass": ok,
                     "evidence": caught[:2] or [rep.reason]})
    return rows


def print_table(rows):
    print(f"\n{'case':32} {'expected':26} {'got':14} ok")
    for r in rows:
        print(f"{r['case']:32} {r['expected'][:26]:26} {r['got']:14} {'✅' if r['pass'] else '❌'}  {r['evidence'][0][:70]}")
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
