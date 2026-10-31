"""Thin Crustdata client: retries on 429/5xx, on-disk cache, and a log of every call for citations."""
import hashlib
import json
import os
import time
from pathlib import Path

import requests

BASE = "https://api.crustdata.com"
API_VERSION = "2025-11-01"
ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("VOUCH_CACHE", ROOT / ".cache"))


def load_env(path=ROOT / ".env"):
    """Read KEY=value lines from .env into the environment (existing variables win)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip().removeprefix("export ").strip(), v.strip().strip("'\""))


load_env()


class CrustError(Exception):
    pass


class Crust:
    def __init__(self, key=None, retries=4, timeout=40):
        self.key = key or os.environ.get("CRUSTDATA_API_KEY") or os.environ.get("API_KEY")
        if not self.key:
            raise CrustError("CRUSTDATA_API_KEY is not set")
        self.retries = retries
        self.timeout = timeout
        self.log = []  # (endpoint, request summary, cached?) for the report

    def post(self, path, body):
        key = hashlib.sha256(json.dumps([path, body], sort_keys=True).encode()).hexdigest()[:24]
        cache_file = CACHE_DIR / f"{key}.json"
        if cache_file.exists():
            self.log.append((path, body, True))
            return json.loads(cache_file.read_text())

        headers = {
            "Authorization": f"Bearer {self.key}",
            "x-api-version": API_VERSION,
            "Content-Type": "application/json",
        }
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = requests.post(BASE + path, json=body, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                last = f"network error: {e}"
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                wait = r.headers.get("Retry-After")
                time.sleep(float(wait) if wait and wait.replace(".", "").isdigit() else 2 ** attempt + 1)
                continue
            if r.status_code == 404:
                data = None
            elif not r.ok:
                raise CrustError(f"{path} HTTP {r.status_code}: {r.text[:300]}")
            else:
                data = r.json()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data))
            self.log.append((path, body, False))
            return data
        raise CrustError(f"{path} failed after {self.retries + 1} attempts ({last})")

    # --- endpoints -------------------------------------------------------

    PERSON_FIELDS = ["basic_profile", "experience", "education", "skills", "social_handles"]

    def enrich_person(self, profile_url):
        data = self.post("/person/enrich", {
            "professional_network_profile_urls": [profile_url],
            "fields": self.PERSON_FIELDS,
        })
        if not data:
            return None
        entry = data[0]
        if entry.get("match_status") != "matched" or not entry.get("matches"):
            return None
        return entry["matches"][0]["person_data"]

    def search_people(self, name, company=None, limit=5):
        conditions = [{"field": "basic_profile.name", "type": "(.)", "value": name}]
        if company:
            conditions.append({"field": "experience.employment_details.current.company_name",
                               "type": "(.)", "value": company})
        body = {"filters": {"op": "and", "conditions": conditions} if len(conditions) > 1 else conditions[0],
                "fields": self.PERSON_FIELDS, "limit": limit}
        data = self.post("/person/search", body)
        return (data or {}).get("profiles", [])

    def search_people_past(self, name, company, limit=5):
        body = {"filters": {"op": "and", "conditions": [
            {"field": "basic_profile.name", "type": "(.)", "value": name},
            {"field": "experience.employment_details.past.company_name", "type": "(.)", "value": company},
        ]}, "fields": self.PERSON_FIELDS, "limit": limit}
        data = self.post("/person/search", body)
        return (data or {}).get("profiles", [])

    def enrich_company(self, company_id=None, name=None):
        body = {"fields": ["basic_info", "headcount"]}
        if company_id:
            body["crustdata_company_ids"] = [int(company_id)]
        elif name:
            body["names"] = [name]
        else:
            return None
        data = self.post("/company/enrich", body)
        if not data or not data[0].get("matches"):
            return None
        return data[0]["matches"][0]["company_data"]
