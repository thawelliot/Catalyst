"""Collects job listings for every degree from the Reed and Adzuna APIs.

Writes one file per country and degree to jobs/, plus pools of
"open to any degree" jobs and a meta.json the site reads for freshness.
Every run rebuilds the files it refreshes, so filled or expired jobs drop out.

Environment:
  REED_API_KEY                   free key from reed.co.uk/developers
  ADZUNA_APP_ID, ADZUNA_APP_KEY  free keys from developer.adzuna.com
  FULL_REFRESH=1                 refresh every country, not just the UK

The UK refreshes on every run. Each other country refreshes once a week (or
whenever it has no data yet) to stay inside Adzuna's free request limits.
Uses only the Python standard library.
"""
import base64
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "jobs"
DEGREES = {k: v for k, v in json.loads((ROOT / "degrees.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}

# Adzuna country code -> currency. Reed only covers the UK.
COUNTRIES = {"gb": "GBP", "de": "EUR", "nl": "EUR", "fr": "EUR", "es": "EUR"}
# Each European country refreshes once a week on its own day (Mon=0), spreading Adzuna calls out
EUROPE_DAY = {"de": 0, "nl": 1, "fr": 2, "es": 3}
OPEN_PHRASES = {"any": ["any degree", "any discipline"], "stem": ["stem degree", "any stem"]}
# A job counts as open to graduates unless its title asks for seniority
SENIOR_TITLE = re.compile(r"\b(senior|snr|sr|lead|principal|head|director|manager|chief|partner|experienced|consultant|architect|specialist|expert|leiter|leitung)\b", re.I)
REED_PAGES = 3  # up to 300 results per phrase
REED_PAUSE = 0.25
ADZUNA_PAUSE = 2.6  # seconds between calls: Adzuna allows about 25 a minute
UA = "Catalyst/1.0 (graduate job listings)"


# ---------- Normalising ----------
def clean(text):
    """Strip HTML tags and entities, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


COMPANY_SUFFIX = re.compile(r"\b(ltd|limited|plc|llp|llc|inc|gmbh|ag|bv|sas|sa|uk|group|holdings)\b\.?", re.I)


def norm(text):
    text = clean(text).lower()
    text = re.sub(r"\((m|w|f|d|x|h)(/(m|w|f|d|x|h))+\)", " ", text)  # German "(m/w/d)" gender tags
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def dedupe_key(job):
    company = re.sub(r"\s+", " ", COMPANY_SUFFIX.sub(" ", norm(job["company"]))).strip()
    city = norm(job["location"].split(",")[0])
    return f"{company}|{norm(job['title'])}|{city}"


def title_matches(title, phrases):
    t = norm(title)
    return any(re.search(r"\b" + re.escape(norm(p)) + r"\b", t) for p in phrases)


def reed_date(value):
    """Reed dates are dd/mm/yyyy."""
    try:
        return dt.datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except (TypeError, ValueError):
        return None


def money(value):
    try:
        v = float(value)
        return round(v) if v > 0 else None
    except (TypeError, ValueError):
        return None


GRAD_TITLE = re.compile(r"\b(graduate|grad|junior|jnr|entry[- ]level|trainee|apprentice|placement|intern(ship)?|assistant)\b", re.I)


def entry_level(title):
    """'Graduate Consultant' is entry level; 'Senior Chemist' and 'Lab Manager' are not."""
    return bool(GRAD_TITLE.search(title or "")) or not SENIOR_TITLE.search(title or "")


def from_reed(r):
    return {
        "id": f"reed-{r['jobId']}",
        "title": clean(r.get("jobTitle")),
        "company": clean(r.get("employerName")) or "Employer not named",
        "location": clean(r.get("locationName")),
        "salaryMin": money(r.get("minimumSalary")),
        "salaryMax": money(r.get("maximumSalary")),
        "currency": r.get("currency") or "GBP",
        "salaryEstimated": False,
        "deadline": reed_date(r.get("expirationDate")),
        "posted": reed_date(r.get("date")),
        "url": r.get("jobUrl") or f"https://www.reed.co.uk/jobs/{r['jobId']}",
        "sources": ["Reed"],
        "grad": entry_level(r.get("jobTitle")),
    }


def from_adzuna(a, currency):
    loc = a.get("location") or {}
    return {
        "id": f"adzuna-{a['id']}",
        "title": clean(a.get("title")),
        "company": clean((a.get("company") or {}).get("display_name")) or "Employer not named",
        "location": clean(loc.get("display_name")),
        "salaryMin": money(a.get("salary_min")),
        "salaryMax": money(a.get("salary_max")),
        "currency": currency,
        "salaryEstimated": str(a.get("salary_is_predicted")) == "1",
        "deadline": None,
        "posted": (a.get("created") or "")[:10] or None,
        "url": a.get("redirect_url"),
        "sources": ["Adzuna"],
        "grad": entry_level(a.get("title")),
    }


def merge(jobs, today=None):
    """Drop expired jobs and merge duplicates, keeping the fullest record."""
    today = (today or dt.date.today()).isoformat()
    pool = {}
    for job in jobs:
        if not job["title"] or not job["url"] or (job["deadline"] and job["deadline"] < today):
            continue
        key = dedupe_key(job)
        have = pool.get(key)
        if not have:
            pool[key] = dict(job, sources=list(job["sources"]))
            continue
        for field in ("deadline", "posted", "location"):
            have[field] = have[field] or job[field]
        if have["salaryMin"] is None and have["salaryMax"] is None or (have["salaryEstimated"] and not job["salaryEstimated"] and (job["salaryMin"] or job["salaryMax"])):
            have.update(salaryMin=job["salaryMin"], salaryMax=job["salaryMax"], salaryEstimated=job["salaryEstimated"])
        have["grad"] = have["grad"] or job["grad"]
        have["sources"] = sorted(set(have["sources"]) | set(job["sources"]))
    # soonest deadline first, then newest; undated jobs last within each
    return sorted(pool.values(), key=lambda j: (j["deadline"] or "9999-99-99", -int(j["posted"].replace("-", "")) if j["posted"] else 0))


# ---------- Fetching ----------
def get_json(url, headers=None, tries=3, body=None):
    """GET, or POST when a JSON body is given; retries busy or failing servers."""
    for attempt in range(tries):
        data = json.dumps(body).encode() if body is not None else None
        extra = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **extra, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            print(f"  ! {e.code} from {url.split('?')[0]}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries - 1:
                time.sleep(5)
                continue
            print(f"  ! {e} from {url.split('?')[0]}", file=sys.stderr)
            return None


def reed_search(key, keywords, **params):
    """Reed returns 100 jobs per page; read up to REED_PAGES pages."""
    auth = base64.b64encode(f"{key}:".encode()).decode()
    jobs = []
    for page in range(REED_PAGES):
        q = urllib.parse.urlencode({"keywords": keywords, "resultsToTake": 100, "resultsToSkip": page * 100, **params})
        data = get_json(f"https://www.reed.co.uk/api/1.0/search?{q}", {"Authorization": f"Basic {auth}"}) or {}
        results = data.get("results", [])
        jobs += [from_reed(r) for r in results]
        time.sleep(REED_PAUSE)
        if len(results) < 100 or len(jobs) >= data.get("totalResults", 0):
            break
    return jobs


def adzuna_search(app_id, app_key, country, **params):
    q = urllib.parse.urlencode({"app_id": app_id, "app_key": app_key, "results_per_page": 50, "content-type": "application/json", **params})
    data = get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?{q}")
    time.sleep(ADZUNA_PAUSE)
    return [from_adzuna(a, COUNTRIES[country]) for a in (data or {}).get("results", [])]


# ---------- UK postcodes ----------
# Some Reed jobs give only a postcode ("PE129YD") as the location. Swap it for the town.
POSTCODE = re.compile(r"^\s*([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\s*$", re.I)
_towns = {}


def postcode_of(location):
    m = POSTCODE.match(location or "")
    return f"{m[1]} {m[2]}".upper() if m else None


def lookup_postcodes(codes):
    """Map full UK postcodes to their town or district via postcodes.io (free, no key)."""
    todo = sorted(set(codes) - set(_towns))
    for i in range(0, len(todo), 100):
        data = get_json("https://api.postcodes.io/postcodes", body={"postcodes": todo[i:i + 100]})
        for item in (data or {}).get("result") or []:
            r = item.get("result") or {}
            _towns[item["query"].upper()] = r.get("admin_district") or r.get("parish") or r.get("region")
    return {c: _towns.get(c) for c in codes}


def place_postcodes(jobs, lookup=lookup_postcodes):
    """Replace postcode-only locations with town names, or a tidied postcode if unknown."""
    codes = {postcode_of(j["location"]) for j in jobs} - {None}
    if not codes:
        return jobs
    towns = lookup(codes)
    for j in jobs:
        pc = postcode_of(j["location"])
        if pc:
            j["location"] = towns.get(pc) or pc
    return jobs


# ---------- Building ----------
def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def collect_country(country, reed_key, adzuna):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    total = 0
    for degree, phrases in DEGREES.items():
        found = []
        if country == "gb" and reed_key:
            for phrase in phrases:
                found += reed_search(reed_key, phrase)
        if adzuna and country == "gb":
            for phrase in phrases[:2]:
                found += adzuna_search(*adzuna, country, title_only=phrase)
        elif adzuna:
            # one broad call per degree elsewhere keeps inside the free limits
            found += adzuna_search(*adzuna, country, what_or=" ".join(sorted({w for p in phrases for w in p.split()})))
        jobs = merge(place_postcodes([j for j in found if title_matches(j["title"], phrases)]))
        write(OUT / country / f"{slug(degree)}.json", {"degree": degree, "country": country, "updated": now, "jobs": jobs})
        total += len(jobs)
        print(f"  {country} {degree}: {len(jobs)}")
    for pool, phrases in OPEN_PHRASES.items():
        found = []
        for phrase in phrases:
            if country == "gb" and reed_key:
                found += reed_search(reed_key, f'"{phrase}"', graduate="true")
            if adzuna:
                found += adzuna_search(*adzuna, country, what_phrase=phrase)
        jobs = merge(place_postcodes(found))
        write(OUT / country / f"open-{pool}.json", {"pool": pool, "country": country, "updated": now, "jobs": jobs})
        total += len(jobs)
        print(f"  {country} open-{pool}: {len(jobs)}")
    return now, total


def main():
    reed_key = os.environ.get("REED_API_KEY", "").strip()
    app_id, app_key = os.environ.get("ADZUNA_APP_ID", "").strip(), os.environ.get("ADZUNA_APP_KEY", "").strip()
    adzuna = (app_id, app_key) if app_id and app_key else None
    if not reed_key and not adzuna:
        sys.exit("No API keys set. Add REED_API_KEY and/or ADZUNA_APP_ID + ADZUNA_APP_KEY.")

    meta_path = OUT / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"countries": {}}
    full = os.environ.get("FULL_REFRESH") == "1"
    weekday = dt.date.today().weekday()
    for country in COUNTRIES:
        due = country == "gb" or full or country not in meta["countries"] or EUROPE_DAY.get(country) == weekday
        if not due or (country != "gb" and not adzuna):
            continue
        print(f"Collecting {country}…")
        updated, total = collect_country(country, reed_key, adzuna)
        meta["countries"][country] = {"updated": updated, "jobs": total}
    write(meta_path, meta)
    print("Done.")


if __name__ == "__main__":
    main()
