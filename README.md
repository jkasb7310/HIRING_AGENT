# Vouch: background checks as software

**ContextCon 03, IIT Bombay · Theme: Hiring.** Vouch takes a résumé it has never seen and returns a
decision (**VERIFIED / NEEDS REVIEW / FLAGGED**), checking every claim against Crustdata's person and
company records and citing the record behind each finding.

```
résumé.pdf ──► claims ──► who is this? ──► check each claim ──► decision + cited report
               (parse)    (Person Enrich /   (titles, dates,      (rules, not an LLM)
                           Person Search)     team size, degrees)
```

## The service

- **Who does it today:** background-verification firms (employment and education checks, paid per
  candidate) and the recruiters and staffing agencies who pay them. They are paid when someone starts
  work, so a placement built on an inflated résumé costs them the fee.
- **Who pays:** HR teams and staffing agencies, per check. A manual check takes days of calls and emails.
- **What Vouch delivers:** the finished check, not a search tool. Every claim is ✅ confirmed,
  ❌ contradicted, 🟡 questionable or ⚠️ unverifiable, with the exact record it was compared against.

## What it does now (end to end, no human)

1. **Reads the résumé:** PDF or text. It uses Claude if `ANTHROPIC_API_KEY` is set, otherwise a
   heuristic parser for the usual "Title, Company, Mon YYYY – Mon YYYY" layouts.
2. **Works out who the candidate is:** uses the profile URL on the résumé if there is one (Person Enrich). If not, it runs Person
   Search on the name plus each claimed employer, then scores every candidate by name, employers and schools.
   If two people with the same name come out too close to call, it **refers** the case instead of guessing.
3. **Checks each claim:**
   - *Employer:* is the job on record? If not, does the record show a different employer for that period?
   - *Title:* résumé seniority vs record seniority (intern … C-level). A claim even one level higher is flagged.
   - *Dates:* start/end vs record, with 3 months' tolerance; catches "still there" when the job ended.
   - *Team size:* "led a team of 40" vs the company's headcount at that time (Company Enrich timeseries).
   - *Education:* school, degree level and graduation year.
   - *Internal:* overlapping full-time roles on the résumé itself.
4. **Decides with fixed rules,** so the same facts always give the same answer, whatever the wording:
   - any serious contradiction (title 2+ levels up, an impossible team size, an upgraded degree) → **FLAGGED**
   - one material contradiction, an unconfirmed identity, or too many claims it couldn't check → **NEEDS REVIEW**
   - otherwise → **VERIFIED**
5. **Scores fit for a role** (optional `--role`): separates skills **on record** from skills **only claimed**.
6. **Writes the report** to the terminal, Markdown, HTML or JSON, with a source on every line.

**Where it still needs a person:** NEEDS REVIEW cases, such as two people with the same name, no public profile, or a
contradiction that may have an innocent explanation (a company renamed, a contract role). Vouch says
exactly what to ask the candidate.

## How we know it works

`eval/tamper.py` makes doctored copies of a real candidate's claims, one lie each: inflated title,
dates stretched 18 months, a team size of 5000, a fake employer over a real job, a degree upgraded
to PhD, an unknown identity, and the honest résumé reworded. The honest copies must verify, and
every lie must be caught.

```bash
.venv/bin/python tests/test_offline.py                         # stub data, no credits
.venv/bin/python eval/run_eval.py samples/*.txt                # live Crustdata
```

Offline: **8/8 cases pass**. Live, on the public profile from Crustdata's docs: **7/7 pass** (the title
test is skipped for a CEO, since there is nothing above that to inflate to). The live results go in `eval/results.json`.

### What the live tests caught in our own code
- **The wrong person with a similar name.** A doctored résumé claimed a job at Google; searching "David Hsu" at
  Google returned *David Hou*, and our loose name match (0.89) accepted him. Fix: names now match token by token
  (every part must match), and we collect candidates from every employer search plus a name-only search
  before choosing. We no longer stop at the first hit, because the claimed employer may itself be the lie.
- **Team size checked at the wrong time.** "Led 300 people" at Retool was flagged because we compared it with
  headcount near the job's *start* (106). People grow teams during a job, so we now use the peak headcount
  during the claimed tenure (473). It becomes a 🟡 warning, not a false accusation.

## How to run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export CRUSTDATA_API_KEY=...          # app.crustdata.com → API Keys (or API_KEY=... in .env)
export ANTHROPIC_API_KEY=...          # optional, for better résumé parsing
.venv/bin/python -m vouch resume.pdf --role "Senior backend engineer, payments, Mumbai" --out report.html
```

Options: `--profile URL` (if it's not on the résumé), `--claims-only` (parse only, no API calls),
`--no-llm`.

## How it is built

| File | What it does |
|---|---|
| `vouch/parse.py` | résumé → claims (Claude or heuristic) |
| `vouch/crust.py` | Crustdata client: retries on 429/5xx with backoff, on-disk cache, a log of calls |
| `vouch/verify.py` | identity resolution, claim checks, decision rules |
| `vouch/match.py` | fuzzy matching: company suffixes, school aliases (IIT → Indian Institute of Technology), dates, seniority |
| `vouch/report.py` | terminal / Markdown / HTML report |
| `eval/` | tamper suite and runner |

**Crustdata endpoints:** `POST /person/enrich` (profile URL → full career history),
`POST /person/search` (name + current or past employer), `POST /company/enrich` (headcount timeseries).

**Robustness:**
- A 429 or 5xx is retried with backoff.
- A failed lookup marks that claim "unverified" rather than failing the whole check.
- A missing profile goes to review, never to "fake".
- Scanned PDFs with no text are rejected with a clear message.
- API responses are cached, so re-runs and the tamper suite cost almost nothing.

## Approaches we weighed

- **An LLM judging the résumé directly:** rejected. Its verdicts change with the wording, and it can't
  show a source for each decision. We use the LLM only to *read* the résumé; all judgement is code.
- **Verifying businesses (Trust and compliance):** rejected. It needs government registries (MCA, GST)
  that sit behind logins and CAPTCHAs; career history is exactly what Crustdata covers.
- **Exact string matching:** failed on "IIT Bombay" vs "Indian Institute of Technology, Bombay" and
  "Razorpay Software Pvt Ltd" vs "Razorpay". We added normalisation and an alias table.

## What it could do next

- **Sourcing in front of verification:** take a 3-line brief, use Person Search to find candidates, and check each one
  before a recruiter sees it.
- **Watcher:** re-check placed candidates and alert when their profile changes.
- **Reference contacts:** use Person Search for former managers at each employer, to request references.
- **Degree registries** (NAD/DigiLocker): these need consent flows, which we couldn't build in five hours.
