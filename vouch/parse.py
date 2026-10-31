"""Résumé (PDF / text) -> structured claims.

Uses Claude when ANTHROPIC_API_KEY is set; otherwise a heuristic parser that handles the
common "Title, Company, Mon YYYY - Mon YYYY" layouts. Either way the output has the same shape:

{"name", "profile_url", "location", "experience": [{"company", "title", "start", "end",
 "team_size", "bullets"}], "education": [{"school", "degree", "field", "start_year", "end_year"}],
 "skills": [...]}
"""
import json
import os
import re
from pathlib import Path

import requests

DATE = r"(?:[A-Za-z]{3,9}\.?\s*['’`]?\s*\d{2,4}|\d{1,2}/\d{4}|\d{4}-\d{2}|\d{4})"
RANGE = re.compile(rf"(?P<s>{DATE})\s*(?:-|–|—|to|until)\s*(?P<e>{DATE}|present|current|now|till date|ongoing)",
                   re.I)
PROFILE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_\-%]+/?", re.I)
TEAM = re.compile(r"(?:team of|managed|managing|led|leading|mentored|built a team of|grew (?:the |a )?team (?:to|of))"
                  r"\s+(?:a\s+)?(?:team\s+of\s+)?(\d{1,4})\+?\s*(?:engineers|people|members|developers|reports|"
                  r"designers|analysts|person|member)?", re.I)
SECTION = re.compile(r"^\s*(work experience|professional experience|experience|internships?|employment(?: history)?|"
                     r"education|academic (?:details|qualifications?|background)|academics?|"
                     r"skills|technical skills|key skills|projects|key projects|academic projects|course projects|"
                     r"research(?: work| experience| projects)?|positions? of responsibility|"
                     r"scholastic achievements?|achievements|extra-?curricular activities|certifications|"
                     r"publications|awards|honou?rs|(?:professional |career )?summary|objective|"
                     r"(?:professional )?profile|about(?: me)?|relevant courses?|languages|interests|hobbies)\s*:?\s*$", re.I)
TITLE_WORDS = re.compile(r"\b(engineer|developer|manager|intern|lead|director|founder|analyst|scientist|head|vp|"
                         r"president|cto|ceo|coo|designer|consultant|sde|architect|associate|officer|specialist|"
                         r"researcher|programmer|partner|trainee|principal|staff)\b", re.I)
DEGREE = re.compile(r"\b(b\.?\s?tech|m\.?\s?tech|b\.?\s?e\.?|m\.?\s?e\.?|b\.?\s?s\.?c?|m\.?\s?s\.?c?|b\.?a\.?|m\.?a\.?|"
                    r"mba|ph\.?\s?d|bachelor[a-z']*|master[a-z']*|dual degree|diploma|b\.?com|bca|mca)\b", re.I)
SCHOOL_WORDS = re.compile(r"\b(university|institute|college|school|iit|nit|iiit|bits|iisc|academy|polytechnic)\b", re.I)


def read_text(path):
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        import logging
        from pypdf import PdfReader
        logging.getLogger("pypdf").setLevel(logging.ERROR)  # silence "wrong pointing object" noise
        reader = PdfReader(str(p))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        # "LinkedIn" is often just hyperlinked text; the URL lives in the link annotations
        links = []
        for page in reader.pages:
            for a in page.get("/Annots") or []:
                try:
                    uri = a.get_object().get("/A", {}).get("/URI")
                except Exception:
                    uri = None
                if uri:
                    links.append(str(uri))
        return text + ("\n\nLINKS\n" + "\n".join(links) if links else "")
    return p.read_text(errors="ignore")


def parse(text, use_llm=True):
    claims = None
    if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            claims = parse_llm(text)
            claims["_parser"] = "claude"
        except Exception as e:  # fall back rather than fail the whole check
            claims = None
            print(f"[vouch] LLM parse failed ({e}); using heuristic parser")
    if claims is None:
        claims = parse_heuristic(text)
        claims["_parser"] = "heuristic"
    m = PROFILE.search(text)
    if m and not claims.get("profile_url"):
        claims["profile_url"] = m.group(0)
    if claims.get("profile_url") and not claims["profile_url"].startswith("http"):
        claims["profile_url"] = "https://www." + claims["profile_url"].split("www.")[-1]
    return claims


# --- LLM -------------------------------------------------------------------

LLM_PROMPT = """Extract the claims from this résumé as JSON only, no prose. Schema:
{"name": str, "profile_url": str|null, "location": str|null,
 "experience": [{"company": str, "title": str, "start": "YYYY-MM"|"YYYY", "end": "YYYY-MM"|"YYYY"|"present",
                 "team_size": int|null, "bullets": [str], "kind": "job"|"position"}],
 "education": [{"school": str, "degree": str|null, "field": str|null, "start_year": int|null, "end_year": int|null}],
 "skills": [str]}
Include positions of responsibility (student clubs, fests, TA roles) with kind "position"; paid jobs and
internships are kind "job". Skip projects. Copy titles and company names exactly as written. team_size = the largest number of people the candidate
says they led or managed in that role, else null. Do not infer anything that is not written.

Résumé:
"""


def parse_llm(text):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": os.environ.get("VOUCH_MODEL", "claude-sonnet-5-5"), "max_tokens": 4000,
              "messages": [{"role": "user", "content": LLM_PROMPT + text[:30000]}]},
        timeout=90,
    )
    r.raise_for_status()
    out = "".join(b.get("text", "") for b in r.json()["content"])
    out = out[out.find("{"): out.rfind("}") + 1]
    data = json.loads(out)
    data.setdefault("experience", [])
    data.setdefault("education", [])
    data.setdefault("skills", [])
    return data


# --- heuristic -------------------------------------------------------------

def _split_title_company(rest):
    rest = re.sub(r"[()\[\]]", " ", rest).strip(" ,|–—-:\t")
    if rest.count("|") == 1:
        # "Intern, Talent Acquisition | Deltabotix Pvt Ltd": the bar separates title from company
        left, right = (s.strip(" ,–—-:\t") for s in rest.split("|"))
        if left and right:
            return (right, left) if TITLE_WORDS.search(right) and not TITLE_WORDS.search(left) else (left, right)
    parts = [p.strip(" ,|–—-:\t") for p in re.split(r"\s+(?:at|@)\s+|\s*[|–—]\s*|\s+-\s+|,\s*", rest) if p.strip(" ,|–—-:\t")]
    if not parts:
        return None, None
    if len(parts) == 1:
        return (parts[0], None) if TITLE_WORDS.search(parts[0]) else (None, parts[0])
    titled = [p for p in parts if TITLE_WORDS.search(p)]
    title = titled[0] if titled else parts[0]
    company = next((p for p in parts if p != title and not re.fullmatch(r"(remote|hybrid|onsite|[A-Z][a-z]+, ?[A-Z]{2,})", p)), None)
    return title, company


def _looks_like_name(line):
    words = line.split()
    return (1 < len(words) <= 5 and not SECTION.match(line) and not re.search(r"[\d@|/:•]", line)
            and not TITLE_WORDS.search(line) and all(w[0].isupper() for w in words if w[0].isalpha()))


def parse_heuristic(text):
    lines = [re.sub(r"[ \t ]+", " ", l).strip() for l in text.splitlines()]
    nonblank = [l for l in lines if l]
    name = next((l for l in nonblank[:3] if _looks_like_name(l)), None)
    claims = {"name": name, "profile_url": None, "location": None,
              "experience": [], "education": [], "skills": []}
    section = None
    pending = None  # line with title/company but dates on the next line
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        sec = SECTION.match(line)
        if sec:
            word = sec.group(1).lower()
            section = ("education" if word.startswith("education") or word in ("academics", "academic") or
                       (word.startswith("academic") and "project" not in word) else
                       "skills" if "skill" in word else
                       "experience" if word.startswith(("work", "professional", "experience", "employment", "intern"))
                       else "positions" if word.startswith("position")
                       else "other")
            pending = None
            continue
        if section == "skills":
            if re.match(r"(relevant )?(courses?|coursework)\b", line, re.I):
                continue
            claims["skills"] += [s.strip(" •-*") for s in re.split(r"[,;|•]", line.split(":", 1)[-1]) if s.strip(" •-*")]
            continue
        rng = RANGE.search(line)
        if section == "education" or (section is None and (DEGREE.search(line) or SCHOOL_WORDS.search(line)) and not TITLE_WORDS.search(line)):
            if SCHOOL_WORDS.search(line) or DEGREE.search(line):
                years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", line)]
                deg = DEGREE.search(line)
                school = re.sub(RANGE, "", line)
                school = re.sub(r"\b(19\d{2}|20\d{2})\b|\b(graduated|graduating|expected|class of|cgpa.*|gpa.*)\b", "",
                                school, flags=re.I)
                parts = [p.strip(" ,|–—-:()") for p in re.split(r"[,|–—]|\s-\s", school) if p.strip(" ,|–—-:()")]
                sch = next((p for p in parts if SCHOOL_WORDS.search(p)), None)
                if sch or deg:
                    if not sch and claims["education"] and not claims["education"][-1].get("degree"):
                        claims["education"][-1]["degree"] = deg.group(0)
                        continue
                    claims["education"].append({
                        "school": sch or parts[0], "degree": deg.group(0) if deg else None,
                        "field": None,
                        "start_year": years[0] if len(years) > 1 else None,
                        "end_year": years[-1] if years else None,
                    })
            continue
        if section in ("experience", "positions", None):
            if rng:
                rest = (line[:rng.start()] + " " + line[rng.end():]).strip()
                title, company = _split_title_company(rest)
                if (not title or not company) and pending:
                    t2, c2 = _split_title_company(pending)
                    title, company = title or t2, company or c2
                if title or company:
                    claims["experience"].append({"company": company, "title": title, "start": rng.group("s"),
                                                 "end": rng.group("e"), "team_size": None, "bullets": [],
                                                 "kind": "position" if section == "positions" else "job"})
                pending = None
            elif claims["experience"] and re.match(r"^[•\-*▪◦●]", line):
                job = claims["experience"][-1]
                job["bullets"].append(line.lstrip("•-*▪◦● "))
                t = TEAM.search(line)
                if t:
                    job["team_size"] = max(job["team_size"] or 0, int(t.group(1)))
            elif section in ("experience", "positions") and (TITLE_WORDS.search(line) or len(line) < 60):
                if pending and not TITLE_WORDS.search(pending) and TITLE_WORDS.search(line):
                    pending = f"{line}, {pending}"
                else:
                    pending = line
            elif claims["experience"]:
                t = TEAM.search(line)
                if t:
                    job = claims["experience"][-1]
                    job["team_size"] = max(job["team_size"] or 0, int(t.group(1)))
    return claims
