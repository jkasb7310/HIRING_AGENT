"""Verify résumé claims against Crustdata's person and company records.

All judgement here is deterministic code: the same claims and the same records always give the
same findings and the same decision, however the résumé was worded.
"""
from dataclasses import dataclass, field, asdict

from . import match as M
from .crust import CrustError

DATE_TOLERANCE = 3        # months of slack on start/end dates
CONFIRMED, CONTRADICTED, UNVERIFIED, WARNING = "confirmed", "contradicted", "unverified", "warning"


@dataclass
class Finding:
    claim: str
    status: str
    detail: str
    source: str = ""
    severity: int = 0          # 0 info, 1 minor, 2 material, 3 serious


@dataclass
class Report:
    candidate: str
    decision: str = ""
    reason: str = ""
    identity: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    fit: dict = field(default_factory=dict)
    profile_url: str = ""
    calls: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        d["findings"] = [asdict(f) if isinstance(f, Finding) else f for f in self.findings]
        return d


# --- identity --------------------------------------------------------------

def _jobs(person):
    ed = (person.get("experience") or {}).get("employment_details") or {}
    out = []
    for kind in ("current", "past"):
        for i, j in enumerate(ed.get(kind) or []):
            j = dict(j)
            j["_ref"] = f"experience.employment_details.{kind}[{i}]"
            j["_current"] = kind == "current"
            out.append(j)
    return out


def _schools(person):
    return [dict(s, _ref=f"education.schools[{i}]")
            for i, s in enumerate((person.get("education") or {}).get("schools") or [])]


def _profile_url(person):
    return (((person.get("social_handles") or {}).get("professional_network_identifier") or {})
            .get("profile_url") or "")


def score_candidate(claims, person):
    """How well a Crustdata profile matches the résumé: name, employers, schools."""
    name = M.name_sim(claims.get("name"), (person.get("basic_profile") or {}).get("name"))
    jobs = _jobs(person)
    claimed = [e.get("company") for e in claims.get("experience", []) if e.get("company")]
    emp_hits = sum(1 for c in claimed if any(M.company_sim(c, j.get("name")) >= 0.8 for j in jobs))
    schools = _schools(person)
    sch_hits = sum(1 for e in claims.get("education", [])
                   if any(M.school_sim(e.get("school"), s.get("school")) >= 0.75 for s in schools))
    score = name * 2 + emp_hits * 1.5 + sch_hits
    return score, {"name": round(name, 2), "employers_matched": emp_hits, "schools_matched": sch_hits}


def resolve(crust, claims):
    """Find the candidate's record. Returns (person, identity_info)."""
    url = claims.get("profile_url")
    if url:
        person = crust.enrich_person(url)
        if person:
            s, parts = score_candidate(claims, person)
            ok = parts["name"] >= 0.6
            return (person if ok else None), {
                "method": "profile URL on résumé (Person Enrich)", "url": url, **parts,
                "status": "matched" if ok else "mismatch",
                "note": "" if ok else f"profile name '{(person.get('basic_profile') or {}).get('name')}' "
                                      f"does not match résumé name '{claims.get('name')}'"}

    name = claims.get("name")
    if not name:
        return None, {"method": "none", "status": "not_found", "note": "no name on résumé"}

    candidates, seen = [], set()
    companies = [e.get("company") for e in claims.get("experience", []) if e.get("company")]
    queries = []
    if companies:
        queries.append(("current", companies[0]))
        queries += [("past", c) for c in companies[1:3]]
    queries.append(("name", None))
    for kind, company in queries:
        try:
            found = (crust.search_people(name, company) if kind == "current" else
                     crust.search_people_past(name, company) if kind == "past" else
                     crust.search_people(name, None, limit=10))
        except CrustError:
            continue
        for p in found:
            pid = p.get("crustdata_person_id")
            if pid not in seen:
                seen.add(pid)
                candidates.append(p)
        if candidates and kind != "name":
            break  # an employer-constrained hit is strong enough; skip the broad name search

    if not candidates:
        return None, {"method": "Person Search (name + employers)", "status": "not_found",
                      "note": f"no Crustdata profile found for '{name}'"}
    scored = sorted(((score_candidate(claims, p), p) for p in candidates), key=lambda x: -x[0][0])
    (best_s, best_parts), best = scored[0]
    runner = scored[1][0][0] if len(scored) > 1 else 0
    info = {"method": "Person Search (name + employers)", "candidates": len(candidates), **best_parts,
            "url": _profile_url(best)}
    if best_parts["name"] < 0.6 or (best_parts["employers_matched"] == 0 and best_parts["schools_matched"] == 0):
        info.update(status="ambiguous" if len(candidates) > 1 else "weak",
                    note=f"{len(candidates)} people named '{name}', none share an employer or school with the résumé")
        return None, info
    if runner and best_s - runner < 1.0:
        info.update(status="ambiguous", note=f"{len(candidates)} similar profiles; top two too close to call")
        return None, info
    info["status"] = "matched"
    return best, info


# --- claim checks ----------------------------------------------------------

def _span(job):
    return M.to_month(job.get("start") or job.get("start_date")), M.to_month(job.get("end") or job.get("end_date"))


def _overlap(a, b, now):
    (s1, e1), (s2, e2) = a, b
    if s1 is None or s2 is None:
        return 0
    e1 = now if e1 in (None, "present") else e1
    e2 = now if e2 in (None, "present") else e2
    return max(0, min(e1, e2) - max(s1, s2))


def _headcount_at(company, month):
    ts = ((company or {}).get("headcount") or {}).get("timeseries") or []
    pts = [(M.to_month(p.get("date")), p.get("employee_count")) for p in ts if p.get("employee_count")]
    pts = [(m, c) for m, c in pts if isinstance(m, int)]
    if not pts:
        return None, None
    m, c = min(pts, key=lambda x: abs(x[0] - month))
    return c, m


def check_job(claim, record_jobs, now, crust, src):
    out = []
    company, title = claim.get("company") or "?", claim.get("title") or "?"
    label = f"{title} at {company}"
    cs, ce = _span(claim)
    best, best_sim = None, 0
    for j in record_jobs:
        s = M.company_sim(company, j.get("name"))
        if s > best_sim:
            best, best_sim = j, s
    if not best or best_sim < 0.75:
        # Not on record. Is the claimed period filled by a different employer on record?
        clash = [j for j in record_jobs if j.get("employment_type") not in ("Internship", "Part-time")
                 and _overlap((cs, ce), _span(j), now) >= 6]
        if clash and cs is not None:
            j = max(clash, key=lambda j: _overlap((cs, ce), _span(j), now))
            out.append(Finding(label, CONTRADICTED,
                               f"Not on record. For that period ({M.fmt_month(cs)}–{M.fmt_month(ce)}) the record shows "
                               f"'{j.get('title')}' at {j.get('name')}.", f"{src} · {j['_ref']}", 2))
        else:
            out.append(Finding(label, UNVERIFIED, f"No job at '{company}' on record, and no conflicting job then.",
                               src, 1))
        return out, None

    ref = f"{src} · {best['_ref']}"
    rec_title = best.get("title") or ""
    out.append(Finding(f"Worked at {company}", CONFIRMED, f"On record as '{best.get('name')}'.", ref))

    # title / seniority
    cl, rl = M.seniority(title), M.seniority(rec_title)
    if cl is not None and rl is not None and cl > rl:
        gap = cl - rl
        out.append(Finding(f"Title: {title}", CONTRADICTED,
                           f"Record says '{rec_title}' ({M.LEVEL_NAMES[rl]}); résumé claims {M.LEVEL_NAMES[cl]}"
                           f" ({gap} level{'s' if gap > 1 else ''} higher).", ref, 3 if gap >= 2 else 2))
    elif M.title_sim(title, rec_title) >= 0.6 or (cl is not None and cl == rl):
        out.append(Finding(f"Title: {title}", CONFIRMED, f"Record says '{rec_title}'.", ref))
    elif cl is not None and rl is not None and cl < rl:
        out.append(Finding(f"Title: {title}", CONFIRMED, f"Record says '{rec_title}' (more senior than claimed).", ref))
    else:
        out.append(Finding(f"Title: {title}", WARNING, f"Record says '{rec_title}'; wording differs.", ref, 1))

    # dates
    rs, re_ = _span(best)
    if cs is not None and rs is not None:
        problems = []
        if rs - cs > DATE_TOLERANCE:
            problems.append(f"starts {rs - cs} months earlier than record ({M.fmt_month(cs)} vs {M.fmt_month(rs)})")
        if ce == "present" and re_ not in (None, "present"):
            problems.append(f"claims still there; record shows it ended {M.fmt_month(re_)}")
        elif isinstance(ce, int) and isinstance(re_, int) and ce - re_ > DATE_TOLERANCE:
            problems.append(f"ends {ce - re_} months later than record ({M.fmt_month(ce)} vs {M.fmt_month(re_)})")
        if problems:
            stretch = max([rs - cs if rs - cs > 0 else 0] +
                          [ce - re_ if isinstance(ce, int) and isinstance(re_, int) else 0] +
                          [12 if ce == "present" and isinstance(re_, int) else 0])
            out.append(Finding(f"Dates: {M.fmt_month(cs)}–{M.fmt_month(ce)}", CONTRADICTED,
                               "; ".join(problems) + ".", ref, 2 if stretch >= 6 else 1))
        else:
            out.append(Finding(f"Dates: {M.fmt_month(cs)}–{M.fmt_month(ce)}", CONFIRMED,
                               f"Record: {M.fmt_month(rs)}–{M.fmt_month(re_ if re_ is not None else 'present')}.", ref))

    # team size vs the company's headcount at the time
    team = claim.get("team_size")
    if team:
        cid = best.get("crustdata_company_id")
        company_rec, hc, when, csrc = None, None, None, ""
        if cid and crust:
            try:
                company_rec = crust.enrich_company(company_id=cid)
                csrc = f"Crustdata Company Enrich · company {cid} · headcount.timeseries"
            except CrustError:
                company_rec = None
        mid = cs if isinstance(cs, int) else now
        if company_rec:
            hc, when = _headcount_at(company_rec, mid)
        if hc is None and best.get("company_headcount_latest"):
            hc, when, csrc = best["company_headcount_latest"], None, f"{ref}.company_headcount_latest"
        if hc:
            at = f" in {M.fmt_month(when)}" if when else " (latest)"
            if team >= hc:
                out.append(Finding(f"Led a team of {team}", CONTRADICTED,
                                   f"{best.get('name')} had {hc} employees{at}: a team of {team} is not possible.",
                                   csrc, 3))
            elif team > 0.5 * hc and hc > 10:
                out.append(Finding(f"Led a team of {team}", WARNING,
                                   f"{best.get('name')} had {hc} employees{at}; team would be over half the company.",
                                   csrc, 1))
            else:
                out.append(Finding(f"Led a team of {team}", CONFIRMED,
                                   f"Plausible: {best.get('name')} had {hc} employees{at}.", csrc))
        else:
            out.append(Finding(f"Led a team of {team}", UNVERIFIED, "No headcount data for this company.", ref, 0))
    return out, best


def check_education(claim, schools, src):
    school = claim.get("school") or "?"
    label = " ".join(x for x in [claim.get("degree"), "from" if claim.get("degree") else "", school] if x)
    best = max(schools, key=lambda s: M.school_sim(school, s.get("school")), default=None)
    if not best or M.school_sim(school, best.get("school")) < 0.75:
        return [Finding(label, UNVERIFIED, f"'{school}' not on record.", src, 1)]
    ref = f"{src} · {best['_ref']}"
    out = [Finding(f"Studied at {school}", CONFIRMED, f"On record as '{best.get('school')}'.", ref)]
    cd, rd = (claim.get("degree") or "").lower(), (best.get("degree") or "").lower()
    if cd and rd:
        level = lambda d: (3 if any(k in d for k in ("ph", "doctor")) else
                           2 if any(k in d for k in ("m.", "master", "mtech", "m tech", "mba", "ms ", "msc", "mca", "dual"))
                           else 1)
        cl, rl = level(cd.replace(" ", "") + " "), level(rd.replace(" ", "") + " ")
        if cl > rl:
            out.append(Finding(f"Degree: {claim.get('degree')}", CONTRADICTED,
                               f"Record shows '{best.get('degree')}' at this school.", ref, 3))
        else:
            out.append(Finding(f"Degree: {claim.get('degree')}", CONFIRMED, f"Record: '{best.get('degree')}'.", ref))
    ce, re_ = claim.get("end_year"), best.get("end_year")
    if ce and re_ and abs(int(ce) - int(re_)) > 1:
        out.append(Finding(f"Graduated {ce}", CONTRADICTED, f"Record says {re_}.", ref, 1))
    return out


def check_internal(claims, now):
    """Overlapping full-time roles on the résumé itself."""
    out = []
    jobs = [j for j in claims.get("experience", []) if "intern" not in (j.get("title") or "").lower()]
    for i in range(len(jobs)):
        for k in range(i + 1, len(jobs)):
            ov = _overlap(_span(jobs[i]), _span(jobs[k]), now)
            if ov > 3:
                out.append(Finding(f"{jobs[i].get('company')} and {jobs[k].get('company')}", WARNING,
                                   f"Résumé claims both roles at once for {ov} months.", "résumé itself", 1))
    return out


# --- role fit ----------------------------------------------------------------

def fit(role, claims, person, findings):
    if not role:
        return {}
    words = {w for w in M.tokens(role) if len(w) > 2} - {
        "the", "and", "who", "with", "for", "has", "have", "need", "find", "want", "role", "our", "years",
        "worth", "call", "this", "week", "tell", "why", "each", "one", "someone", "people", "five", "candidate"}
    record_skills = [s.lower() for s in ((person or {}).get("skills") or {}).get("professional_network_skills") or []]
    record_titles = " ".join((j.get("title") or "") + " " + (j.get("description") or "") for j in _jobs(person or {})).lower()
    claimed = " ".join(claims.get("skills", []) + [" ".join(j.get("bullets", [])) + " " + (j.get("title") or "")
                                                   for j in claims.get("experience", [])]).lower()
    on_record = sorted(w for w in words if any(w in s for s in record_skills) or w in record_titles)
    claimed_only = sorted(w for w in words if w in claimed and w not in on_record)
    yrs = ((person or {}).get("experience") or {}).get("years_of_experience_raw")
    score = min(100, int(100 * (len(on_record) + 0.5 * len(claimed_only)) / max(1, min(len(words), 8))))
    return {"score": score, "on_record": on_record, "claimed_only": claimed_only,
            "years_of_experience": yrs, "keywords": sorted(words)}


# --- the whole check -------------------------------------------------------

def decide(rep):
    contra = [f for f in rep.findings if f.status == CONTRADICTED]
    serious = [f for f in contra if f.severity >= 3]
    material = [f for f in contra if f.severity >= 2]
    unver = [f for f in rep.findings if f.status == UNVERIFIED and f.severity >= 1]
    confirmed = [f for f in rep.findings if f.status == CONFIRMED]
    status = rep.identity.get("status")
    if status != "matched":
        rep.decision = "NEEDS REVIEW"
        rep.reason = f"Could not confirm identity: {rep.identity.get('note', status)}. Ask for the profile link."
    elif serious or len(material) >= 2:
        rep.decision = "FLAGGED"
        rep.reason = f"{len(contra)} claim(s) contradicted by the record, " \
                     f"including: {(serious or material)[0].claim}."
    elif material or len(unver) > max(1, len(confirmed) // 2):
        rep.decision = "NEEDS REVIEW"
        rep.reason = (f"Contradiction to clarify: {material[0].claim}." if material else
                      f"{len(unver)} claims could not be checked against the record.")
    else:
        rep.decision = "VERIFIED"
        rep.reason = f"{len(confirmed)} claims confirmed, none contradicted."
    if rep.fit:
        rep.fit["call"] = rep.decision == "VERIFIED" and rep.fit.get("score", 0) >= 40
    return rep


def run(crust, claims, role=None, now=None):
    import datetime
    t = datetime.date.today()
    now = now or t.year * 12 + t.month - 1
    rep = Report(candidate=claims.get("name") or "Unknown")
    try:
        person, ident = resolve(crust, claims)
    except CrustError as e:
        person, ident = None, {"status": "error", "note": str(e)}
        rep.errors.append(str(e))
    rep.identity = ident
    rep.findings += check_internal(claims, now)
    if person:
        url = _profile_url(person) or ident.get("url") or ""
        rep.profile_url = url
        src = f"Crustdata Person record ({url or 'id ' + str(person.get('crustdata_person_id'))})"
        jobs, schools = _jobs(person), _schools(person)
        for job in claims.get("experience", []):
            try:
                f, _ = check_job(job, jobs, now, crust, src)
            except CrustError as e:
                rep.errors.append(str(e))
                f = [Finding(f"{job.get('title')} at {job.get('company')}", UNVERIFIED, f"Lookup failed: {e}", "", 0)]
            rep.findings += f
        for edu in claims.get("education", []):
            rep.findings += check_education(edu, schools, src)
        if not jobs and not schools:
            rep.findings.append(Finding("Career history", UNVERIFIED, "Profile found but it lists no jobs or schools.",
                                        src, 1))
    rep.fit = fit(role, claims, person, rep.findings)
    rep.calls = [{"endpoint": p, "cached": c} for p, _, c in getattr(crust, "log", [])]
    return decide(rep)
