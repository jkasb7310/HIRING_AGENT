"""vouch: check every claim on a résumé against Crustdata, then decide.

    python -m vouch resume.pdf --role "Senior backend engineer, payments, Mumbai"
    python -m vouch resume.txt --profile https://www.linkedin.com/in/someone --out report.html
"""
import argparse
import json
import sys
from pathlib import Path

from . import parse, report, verify
from .crust import Crust, CrustError


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vouch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("resume", help="résumé file (.pdf, .txt, .md) or a claims .json")
    ap.add_argument("--role", help="the role brief, for the fit score")
    ap.add_argument("--profile", help="the candidate's profile URL, if not on the résumé")
    ap.add_argument("--name", help="the candidate's name, if not on the résumé")
    ap.add_argument("--out", help="write the report here (.html, .md or .json)")
    ap.add_argument("--no-llm", action="store_true", help="use the heuristic parser even if ANTHROPIC_API_KEY is set")
    ap.add_argument("--claims-only", action="store_true", help="print the parsed claims and stop (no API calls)")
    args = ap.parse_args(argv)

    path = Path(args.resume)
    if not path.exists():
        sys.exit(f"vouch: {path} not found")
    if path.suffix.lower() == ".json":
        claims = json.loads(path.read_text())
    else:
        text = parse.read_text(path)
        if not text.strip():
            sys.exit("vouch: no text found in the résumé (scanned image?)")
        claims = parse.parse(text, use_llm=not args.no_llm)
    if args.profile:
        claims["profile_url"] = args.profile
    if args.name:
        claims["name"] = args.name

    if args.claims_only:
        print(json.dumps(claims, indent=2, ensure_ascii=False))
        return

    try:
        crust = Crust()
    except CrustError as e:
        sys.exit(f"vouch: {e}")
    rep = verify.run(crust, claims, role=args.role)
    print(report.to_text(rep))

    if args.out:
        out = Path(args.out)
        if out.suffix == ".html":
            out.write_text(report.to_html(rep))
        elif out.suffix == ".json":
            out.write_text(json.dumps({"claims": claims, "report": rep.to_dict()}, indent=2, ensure_ascii=False))
        else:
            out.write_text(report.to_markdown(rep))
        print(f"  report written to {out}")


if __name__ == "__main__":
    main()
