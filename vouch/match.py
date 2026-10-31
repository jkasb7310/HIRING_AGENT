"""Normalisation and fuzzy matching for companies, titles, schools and dates."""
import re
from difflib import SequenceMatcher

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}

COMPANY_NOISE = {
    "pvt", "private", "ltd", "limited", "inc", "llc", "llp", "corp", "corporation", "co", "company",
    "technologies", "technology", "tech", "labs", "lab", "solutions", "software", "services", "india",
    "the", "group", "global", "systems", "plc", "gmbh", "ai", "hq",
}
SCHOOL_NOISE = {"the", "of", "university", "institute", "college", "school", "and", "&"}
SCHOOL_ALIASES = {
    "iit": "indian institute technology", "iitb": "indian institute technology bombay",
    "iisc": "indian institute science", "bits": "birla institute technology science",
    "nit": "national institute technology", "iiit": "international institute information technology",
    "mit": "massachusetts institute technology",
}


def tokens(s, noise=()):
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    return [t for t in s.split() if t and t not in noise]


def sim(a, b):
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def company_sim(a, b):
    ta, tb = tokens(a, COMPANY_NOISE), tokens(b, COMPANY_NOISE)
    if not ta or not tb:
        ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    ja, jb = " ".join(ta), " ".join(tb)
    if ja == jb:
        return 1.0
    # "Flipkart" vs "Flipkart Internet" -> containment
    if set(ta) <= set(tb) or set(tb) <= set(ta):
        return 0.9
    return sim(ja, jb)


def _expand_school(s):
    out = []
    for t in tokens(s):
        out.extend(SCHOOL_ALIASES.get(t, t).split())
    return [t for t in out if t not in SCHOOL_NOISE]


def school_sim(a, b):
    ta, tb = _expand_school(a), _expand_school(b)
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
    return max(overlap if min(len(sa), len(sb)) > 1 else 0.0, sim(" ".join(ta), " ".join(tb)))


def name_sim(a, b):
    """Token-wise: every part of the shorter name must match a part of the other (initials allowed).
    The weakest part decides, so 'David Hsu' vs 'David Hou' scores low."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    worst = 1.0
    for t in short:
        best = max((1.0 if t == u else 0.9 if (len(t) == 1 and u.startswith(t)) or (len(u) == 1 and t.startswith(u))
                    else sim(t, u)) for u in long_)
        worst = min(worst, best)
    return worst


# --- dates -----------------------------------------------------------------

PRESENT = {"present", "current", "now", "till date", "today", "ongoing"}


def to_month(value):
    """Return months since year 0 (year*12 + month-1), None for unknown, 'present' for ongoing."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) * 12
    s = str(value).strip().lower()
    if not s:
        return None
    if s in PRESENT:
        return "present"
    m = re.match(r"(\d{4})-(\d{1,2})", s)  # 2019-03, 2017-01-01T00:00:00+00:00
    if m:
        return int(m.group(1)) * 12 + int(m.group(2)) - 1
    m = re.match(r"([a-z]{3})[a-z]*\.?\s*'?(\d{2,4})", s)  # Mar 2019, March 2019, Mar '19
    if m and m.group(1) in MONTHS:
        y = int(m.group(2))
        y = y + 2000 if y < 100 else y
        return y * 12 + MONTHS[m.group(1)] - 1
    m = re.match(r"(\d{1,2})/(\d{4})", s)  # 03/2019
    if m:
        return int(m.group(2)) * 12 + int(m.group(1)) - 1
    m = re.search(r"(\d{4})", s)
    if m:
        return int(m.group(1)) * 12
    return None


def fmt_month(m):
    if m == "present":
        return "present"
    if m is None:
        return "?"
    return f"{list(MONTHS)[m % 12].title()} {m // 12}"


# --- titles ----------------------------------------------------------------

# Highest-matching rule wins, so order from most to least senior.
SENIORITY = [
    (7, r"\b(ceo|cto|cfo|coo|cpo|chief|founder|co-?founder|president)\b"),
    (6, r"\b(vp|vice president|svp|evp)\b"),
    (5, r"\b(director|head of|head,|senior manager|sr\.? manager|general manager)\b|^head\b"),
    (4, r"\b(lead|staff|principal|manager|architect|em)\b"),
    (3, r"\b(senior|sr\.?|sde[- ]?(3|iii)|sde ?3|engineer (3|iii)|l5)\b"),
    (2, r"\b(engineer|developer|analyst|scientist|consultant|designer|sde[- ]?(2|ii)|associate|specialist|programmer)\b"),
    (1, r"\b(junior|jr\.?|graduate|trainee|sde[- ]?(1|i)\b|engineer (1|i)\b|apprentice|fresher)\b"),
    (0, r"\b(intern|internship|student|research assistant)\b"),
]


def seniority(title):
    t = (title or "").lower()
    # junior/intern markers override generic "engineer"
    for level in (0, 1):
        if re.search(dict((l, p) for l, p in SENIORITY)[level], t):
            return level
    for level, pattern in SENIORITY:
        if re.search(pattern, t):
            return level
    return None


LEVEL_NAMES = {0: "intern", 1: "junior", 2: "mid-level", 3: "senior", 4: "lead/manager",
               5: "director/head", 6: "VP", 7: "C-level/founder"}


def title_sim(a, b):
    return sim(" ".join(tokens(a)), " ".join(tokens(b)))
