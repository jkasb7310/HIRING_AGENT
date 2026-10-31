"""Render a verification report as terminal text, Markdown or a standalone HTML page."""
import html

ICON = {"confirmed": "✅", "contradicted": "❌", "unverified": "⚠️", "warning": "🟡"}
ORDER = {"contradicted": 0, "warning": 1, "unverified": 2, "confirmed": 3}
COLOR = {"VERIFIED": "#1a7f37", "NEEDS REVIEW": "#9a6700", "FLAGGED": "#cf222e"}


def _sorted(findings):
    return sorted(findings, key=lambda f: (ORDER.get(f.status, 9), -f.severity))


def to_markdown(rep):
    lines = [f"# Vouch report: {rep.candidate}", "",
             f"**Decision: {rep.decision}** · {rep.reason}", ""]
    idn = rep.identity
    lines += ["## Identity", f"- Status: **{idn.get('status')}** via {idn.get('method', '-')}"]
    if rep.profile_url:
        lines.append(f"- Profile: {rep.profile_url}")
    if idn.get("note"):
        lines.append(f"- Note: {idn['note']}")
    if "employers_matched" in idn:
        lines.append(f"- Name match {idn.get('name')}, employers matched {idn['employers_matched']}, "
                     f"schools matched {idn['schools_matched']}")
    counts = {}
    for f in rep.findings:
        counts[f.status] = counts.get(f.status, 0) + 1
    lines += ["", "## Claims", " · ".join(f"{ICON[k]} {counts.get(k, 0)} {k}" for k in ORDER), "",
              "| | Claim | Finding | Source |", "|---|---|---|---|"]
    for f in _sorted(rep.findings):
        lines.append(f"| {ICON.get(f.status, '')} | {f.claim} | {f.detail} | {f.source} |")
    if rep.fit:
        lines += ["", "## Fit for the role",
                  f"- Score: **{rep.fit['score']}/100** · call: **{'yes' if rep.fit.get('call') else 'no'}**",
                  f"- On record: {', '.join(rep.fit['on_record']) or '-'}",
                  f"- Only claimed on résumé: {', '.join(rep.fit['claimed_only']) or '-'}"]
        if rep.fit.get("years_of_experience"):
            lines.append(f"- Years of experience on record: {rep.fit['years_of_experience']}")
    if rep.errors:
        lines += ["", "## Errors (handled)"] + [f"- {e}" for e in rep.errors]
    if rep.calls:
        lines += ["", "## Data calls"] + [f"- `{c['endpoint']}`{' (cached)' if c['cached'] else ''}" for c in rep.calls]
    return "\n".join(lines) + "\n"


def to_text(rep):
    out = [f"\n  {rep.candidate}  →  {rep.decision}", f"  {rep.reason}", ""]
    for f in _sorted(rep.findings):
        out.append(f"  {ICON.get(f.status, ' ')} {f.claim}\n      {f.detail}\n      ↳ {f.source}")
    if rep.fit:
        out.append(f"\n  Fit {rep.fit['score']}/100 · call: {'yes' if rep.fit.get('call') else 'no'}")
    return "\n".join(out) + "\n"


def to_html(rep):
    e = html.escape
    rows = "".join(
        f"<tr class='{f.status}'><td>{ICON.get(f.status, '')}</td><td>{e(f.claim)}</td>"
        f"<td>{e(f.detail)}</td><td class='src'>{e(f.source)}</td></tr>" for f in _sorted(rep.findings))
    idn = rep.identity
    fit = ""
    if rep.fit:
        fit = (f"<h2>Fit for the role</h2><p><b>{rep.fit['score']}/100</b> · call: "
               f"<b>{'yes' if rep.fit.get('call') else 'no'}</b></p>"
               f"<p>On record: {e(', '.join(rep.fit['on_record']) or '-')}<br>"
               f"Only claimed: {e(', '.join(rep.fit['claimed_only']) or '-')}</p>")
    profile = f"<a href='{e(rep.profile_url)}'>{e(rep.profile_url)}</a>" if rep.profile_url else "-"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vouch: {e(rep.candidate)}</title><style>
:root{{--bg:#fff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--bad:#ffebe9;--warn:#fff8c5}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d1117;--fg:#e6edf3;--muted:#8d96a0;--line:#30363d;--bad:#3c1618;--warn:#3a2e05}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:980px;margin:0 auto;padding:24px 16px}}
.badge{{display:inline-block;padding:6px 14px;border-radius:6px;color:#fff;font-weight:700;background:{COLOR.get(rep.decision, '#555')}}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}}
tr.contradicted{{background:var(--bad)}}tr.warning{{background:var(--warn)}}.src{{color:var(--muted);font-size:12px;word-break:break-all}}
.muted{{color:var(--muted)}}</style></head><body>
<h1>{e(rep.candidate)}</h1><p><span class="badge">{e(rep.decision)}</span> {e(rep.reason)}</p>
<p class="muted">Identity: <b>{e(str(idn.get('status')))}</b> via {e(str(idn.get('method', '-')))}. {e(idn.get('note', ''))}<br>Profile: {profile}</p>
<h2>Claims</h2><table><tr><th></th><th>Claim</th><th>Finding</th><th>Source</th></tr>{rows}</table>{fit}
<p class="muted">{len(rep.calls)} Crustdata calls · decisions are rule-based, so the same record always gives the same answer.</p>
</body></html>"""
