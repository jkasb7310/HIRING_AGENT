"""Make doctored copies of a candidate's claims, one lie each, for testing."""
import copy

from vouch import match as M


def _shift(value, months):
    m = M.to_month(value)
    if not isinstance(m, int):
        return value
    m += months
    return f"{m // 12}-{m % 12 + 1:02d}"


def variants(claims):
    """Yield (name, expected decisions, claims). The honest copy should verify; each lie should not."""
    yield "honest", {"VERIFIED"}, copy.deepcopy(claims)
    jobs = claims.get("experience") or []
    if jobs:
        # inflate the most junior role so the lie is always a step up, never a step down
        level = lambda j: M.seniority(j.get("title"))
        idx = min(range(len(jobs)), key=lambda i: level(jobs[i]) if level(jobs[i]) is not None else 9)
        if (level(jobs[idx]) or 0) < 5:
            c = copy.deepcopy(claims)
            c["experience"][idx]["title"] = "Head of Engineering"
            yield "title inflated", {"FLAGGED", "NEEDS REVIEW"}, c

        c = copy.deepcopy(claims)
        c["experience"][-1]["start"] = _shift(c["experience"][-1].get("start"), -18)
        yield "dates stretched 18 months", {"FLAGGED", "NEEDS REVIEW"}, c

        c = copy.deepcopy(claims)
        c["experience"][0]["team_size"] = 5000
        yield "impossible team size", {"FLAGGED"}, c

        c = copy.deepcopy(claims)
        j = c["experience"][0]
        c["experience"].insert(0, {"company": "Google", "title": j.get("title"), "start": j.get("start"),
                                   "end": j.get("end"), "team_size": None, "bullets": []})
        yield "fake employer over a real job", {"FLAGGED", "NEEDS REVIEW"}, c
    if claims.get("education"):
        c = copy.deepcopy(claims)
        c["education"][0]["degree"] = "PhD"
        yield "degree upgraded to PhD", {"FLAGGED"}, c

    c = copy.deepcopy(claims)
    c["name"] = "Zzyzx Qwertyuiop"
    c["profile_url"] = None
    yield "identity not on record", {"NEEDS REVIEW"}, c

    # same facts, reworded: decision must not change
    c = copy.deepcopy(claims)
    for j in c.get("experience", []):
        j["company"] = (j.get("company") or "").upper() + " Pvt. Ltd."
        j["bullets"] = ["Reworded: " + b for b in j.get("bullets", [])]
    yield "honest, reworded", {"VERIFIED"}, c
