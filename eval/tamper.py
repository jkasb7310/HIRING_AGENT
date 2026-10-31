"""Make doctored copies of a candidate's claims, one lie each, for testing.

Lies are planted only in roles the record can check (the honest run confirmed the employer):
a lie about a job that isn't on record is uncatchable by any checker, and Vouch already sends
those to review.
"""
import copy

from vouch import match as M


def _shift(value, months):
    m = M.to_month(value)
    if not isinstance(m, int):
        return value
    m += months
    return f"{m // 12}-{m % 12 + 1:02d}"


def variants(claims, checkable=None):
    """Yield (name, expected decisions, claims).

    checkable: indices into claims["experience"] whose employer is on record. None = all of them.
    """
    yield "honest", {"VERIFIED", "NEEDS REVIEW"}, copy.deepcopy(claims)
    exp = claims.get("experience") or []
    idxs = [i for i in range(len(exp)) if checkable is None or i in checkable]
    # prefer paid jobs over club roles
    idxs.sort(key=lambda i: exp[i].get("kind") == "position")
    if idxs:
        level = lambda i: M.seniority(exp[i].get("title"))
        low = min(idxs, key=lambda i: level(i) if level(i) is not None else 2)
        if (level(low) if level(low) is not None else 2) < 5:
            c = copy.deepcopy(claims)
            c["experience"][low]["title"] = "Head of Engineering"
            yield "title inflated", {"FLAGGED", "NEEDS REVIEW"}, c

        i = idxs[0]
        c = copy.deepcopy(claims)
        c["experience"][i]["start"] = _shift(c["experience"][i].get("start"), -18)
        yield "dates stretched 18 months", {"FLAGGED", "NEEDS REVIEW"}, c

        c = copy.deepcopy(claims)
        c["experience"][i]["team_size"] = 5000
        yield "impossible team size", {"FLAGGED"}, c

        # a fake job overlapping a club role is not a contradiction (students do both), so only over real jobs
        if exp[i].get("kind") != "position" and not M.SCHOOLISH.search(exp[i].get("company") or ""):
            c = copy.deepcopy(claims)
            j = c["experience"][i]
            c["experience"].insert(0, {"company": "Google", "title": j.get("title"), "start": j.get("start"),
                                       "end": j.get("end"), "team_size": None, "bullets": [], "kind": "job"})
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
    yield "honest, reworded", {"VERIFIED", "NEEDS REVIEW"}, c
