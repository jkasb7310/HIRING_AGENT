"""Offline checks: a stub Crustdata built from the documented response shape, plus the tamper suite.

    .venv/bin/python tests/test_offline.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "eval")]

from run_eval import print_table, run_suite  # noqa: E402
from vouch import parse, verify  # noqa: E402

PERSON = {
    "crustdata_person_id": 1,
    "basic_profile": {"name": "Priya Sharma", "location": {"raw": "Mumbai, Maharashtra, India"}},
    "skills": {"professional_network_skills": ["Go", "Python", "Kafka", "Payments"]},
    "experience": {
        "employment_details": {
            "current": [{"name": "Razorpay", "title": "Senior Software Engineer", "start_date": "2022-01-01",
                         "end_date": None, "crustdata_company_id": 99, "company_headcount_latest": 3200}],
            "past": [{"name": "Freshworks", "title": "Software Engineer", "start_date": "2019-07-01T00:00:00+00:00",
                      "end_date": "2021-12-01", "crustdata_company_id": 98, "company_headcount_latest": 5000}],
        },
        "years_of_experience_raw": 6,
    },
    "education": {"schools": [{"school": "Indian Institute of Technology, Bombay", "degree": "Bachelor of Technology (B.Tech.)",
                               "start_year": 2015, "end_year": 2019}]},
    "social_handles": {"professional_network_identifier": {"profile_url": "https://www.linkedin.com/in/priya-sharma-demo"}},
}
COMPANIES = {99: {"basic_info": {"name": "Razorpay"},
                  "headcount": {"timeseries": [{"date": "2022-01-01", "employee_count": 2700},
                                               {"date": "2024-01-01", "employee_count": 3100}]}}}


class StubCrust:
    def __init__(self):
        self.log = []

    def enrich_person(self, url):
        self.log.append(("/person/enrich", url, False))
        return PERSON if "priya-sharma-demo" in url else None

    def search_people(self, name, company=None, limit=5):
        self.log.append(("/person/search", name, False))
        return [PERSON] if "priya" in name.lower() else []

    def search_people_past(self, name, company, limit=5):
        return self.search_people(name, company, limit)

    def enrich_company(self, company_id=None, name=None):
        self.log.append(("/company/enrich", company_id, False))
        return COMPANIES.get(company_id)


def main():
    claims = parse.parse(parse.read_text(ROOT / "samples" / "priya_sharma.txt"), use_llm=False)
    assert claims["name"] == "Priya Sharma", claims["name"]
    assert len(claims["experience"]) == 2, claims["experience"]
    assert claims["experience"][0]["company"] == "Razorpay", claims["experience"][0]
    assert claims["experience"][0]["team_size"] == 6, claims["experience"][0]
    assert claims["education"] and "IIT" in claims["education"][0]["school"], claims["education"]
    assert claims["profile_url"].endswith("priya-sharma-demo"), claims["profile_url"]

    rep = verify.run(StubCrust(), claims, role="senior backend engineer payments Go Kafka Mumbai")
    print(rep.decision, "|", rep.reason)
    rows = run_suite(StubCrust(), claims, role="backend payments")
    print_table(rows)
    failed = [r["case"] for r in rows if not r["pass"]]
    if failed:
        sys.exit(f"FAILED: {failed}")


if __name__ == "__main__":
    main()
