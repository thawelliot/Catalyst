"""Collects job listings for every degree from the Reed, Adzuna and NHS Jobs feeds.

Writes one file per country and degree to jobs/, pools of "open to any degree"
jobs, NHS jobs by pay band (jobs/gb/nhs-<degree>.json) for health degrees, and
a meta.json the site reads for freshness.
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
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "jobs"
_config = json.loads((ROOT / "degrees.json").read_text(encoding="utf-8"))
DEGREES = {k: v for k, v in _config.items() if not k.startswith("_")}
NHS_KEYWORDS = _config["_nhs"]  # degree -> NHS Jobs search word

# NHS Agenda for Change pay bands, as the NHS Jobs feed names them
NHS_BANDS = ["2", "3", "4", "5", "6", "7", "8A", "8B", "8C", "8D", "9"]
NHS_PAGES = {"5": 30}  # pages of 10 per band; Band 5 (newly qualified) gets the most
NHS_PAGES_DEFAULT = 15
NHS_PAUSE = 0.3

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


UNNAMED = "Employer not named"
# Reed's own agency posts many jobs as "Reed" without naming the client employer
REED_ITSELF = re.compile(r"^(reed|reed specialist recruitment|reed\.co\.uk)( (ltd|limited))?$", re.I)
AGENCY = re.compile(r"\b(recruitment|recruiting|recruiters?|staffing|personnel|resourcing|headhunt\w*|jobs in)\b", re.I)


def employer(name):
    """(company, agency): who is hiring, and the recruitment agency advertising it, if any."""
    name = clean(name)
    if not name:
        return UNNAMED, None
    if REED_ITSELF.match(name):
        return UNNAMED, "Reed"
    return name, (name if AGENCY.search(name) else None)


def dedupe_key(job):
    if job["company"] == UNNAMED:  # can't tell two unnamed employers apart, so never merge them
        return f"unnamed:{job['id']}"
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


SUMMARY_LENGTH = 280


def summarise(text):
    """First couple of sentences of an advert, cut at a word boundary."""
    text = clean(text)
    if len(text) <= SUMMARY_LENGTH:
        return text
    return text[:SUMMARY_LENGTH].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


GRAD_TITLE = re.compile(r"\b(graduate|grad|junior|jnr|entry[- ]level|trainee|apprentice|placement|intern(ship)?|assistant)\b", re.I)


def entry_level(title):
    """'Graduate Consultant' is entry level; 'Senior Chemist' and 'Lab Manager' are not."""
    return bool(GRAD_TITLE.search(title or "")) or not SENIOR_TITLE.search(title or "")


def from_reed(r):
    company, agency = employer(r.get("employerName"))
    return {
        "id": f"reed-{r['jobId']}",
        "title": clean(r.get("jobTitle")),
        "company": company,
        "agency": agency,
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
        "summary": summarise(r.get("jobDescription")),
    }


def from_adzuna(a, currency):
    loc = a.get("location") or {}
    company, agency = employer((a.get("company") or {}).get("display_name"))
    return {
        "id": f"adzuna-{a['id']}",
        "title": clean(a.get("title")),
        "company": company,
        "agency": agency,
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
        "summary": summarise(a.get("description")),
    }


def nhs_salary(text):
    """'£32073.00 to £39043.00' -> (32073, 39043); '£31.11' -> (31, 31); 'Negotiable' -> (None, None)."""
    nums = [money(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")]
    nums = [n for n in nums if n]
    return (min(nums), max(nums)) if nums else (None, None)


def nhs_towns(vacancy):
    """'Portsmouth, PO2 0TA' entries -> 'Portsmouth, Fareham' (town names, no repeats)."""
    towns = []
    for loc in vacancy.iter("location"):
        town = clean(loc.text).split(",")[0].strip()
        if town and town not in towns and not POSTCODE.match(town):
            towns.append(town)
    return ", ".join(towns)


def from_nhs(v, band):
    lo, hi = nhs_salary(v.findtext("salary"))
    band = band.lower()
    return {
        "id": f"nhs-{v.findtext('id')}",
        "title": clean(v.findtext("title")),
        "company": clean(v.findtext("employer")) or "NHS employer",
        "agency": employer(v.findtext("employer"))[1],
        "location": nhs_towns(v),
        "salaryMin": lo,
        "salaryMax": hi,
        "currency": "GBP",
        "salaryEstimated": False,
        "deadline": (v.findtext("closeDate") or "")[:10] or None,
        "posted": (v.findtext("postDate") or "")[:10] or None,
        "url": v.findtext("url"),
        "sources": ["NHS Jobs"],
        "grad": band in ("2", "3", "4", "5"),
        "summary": summarise(v.findtext("description")),
        "band": band,
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
        for field in ("deadline", "posted", "location", "band"):
            have[field] = have.get(field) or job.get(field)
        if have["salaryMin"] is None and have["salaryMax"] is None or (have["salaryEstimated"] and not job["salaryEstimated"] and (job["salaryMin"] or job["salaryMax"])):
            have.update(salaryMin=job["salaryMin"], salaryMax=job["salaryMax"], salaryEstimated=job["salaryEstimated"])
        have["grad"] = have["grad"] or job["grad"]
        if len(job.get("summary") or "") > len(have.get("summary") or ""):
            have["summary"] = job["summary"]
        have["sources"] = sorted(set(have["sources"]) | set(job["sources"]))
    # soonest deadline first, then newest; undated jobs last within each
    return sorted(pool.values(), key=lambda j: (j["deadline"] or "9999-99-99", -int(j["posted"].replace("-", "")) if j["posted"] else 0))


def carry_first_seen(jobs, previous, now):
    """Stamp each job with when Catalyst first listed it, keeping the date from the last run's file."""
    # files from before firstSeen existed fall back to the posting date, so old jobs don't all look new
    seen = {dedupe_key(j): j.get("firstSeen") or (j.get("posted") and j["posted"] + "T00:00:00+00:00") for j in previous}
    for j in jobs:
        j["firstSeen"] = seen.get(dedupe_key(j)) or j.get("firstSeen") or now
    return jobs


def previous_jobs(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("jobs", [])
    except (OSError, ValueError):
        return []


# ---------- Fetching ----------
def get_json(url, headers=None, tries=3, body=None, parse=json.load):
    """GET, or POST when a JSON body is given; retries busy or failing servers.
    Pass parse=parse_xml for XML feeds."""
    for attempt in range(tries):
        data = json.dumps(body).encode() if body is not None else None
        extra = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **extra, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return parse(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(10 * (attempt + 1))
                continue
            print(f"  ! {e.code} from {url.split('?')[0]}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as e:
            if attempt < tries - 1:
                time.sleep(5)
                continue
            print(f"  ! {e} from {url.split('?')[0]}", file=sys.stderr)
            return None


def parse_xml(resp):
    return ET.parse(resp).getroot()


def nhs_search(keyword, band):
    """NHS Jobs XML feed: 10 vacancies a page, filtered to one pay band."""
    jobs = []
    for page in range(1, NHS_PAGES.get(band, NHS_PAGES_DEFAULT) + 1):
        q = urllib.parse.urlencode({"keyword": keyword, "payBand": f"BAND_{band}", "page": page})
        root = get_json(f"https://www.jobs.nhs.uk/api/v1/search_xml?{q}", parse=parse_xml)
        time.sleep(NHS_PAUSE)
        if root is None:
            break
        jobs += [from_nhs(v, band) for v in root.findall("vacancyDetails")]
        if page >= int(root.findtext("totalPages") or 0):
            break
    return jobs


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
            town = r.get("admin_district") or r.get("parish") or r.get("region")
            _towns[item["query"].upper()] = town
            if town:  # the postcode also tells us the county, more reliably than a name search
                remember_county(town, "Greater London" if r.get("region") == "London"
                                else MET_COUNTIES.get(town) or r.get("admin_county") or town)
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


# ---------- Counties ----------
# Each UK job gets an "area" (its county) so a search for Cambridge can also show jobs elsewhere in
# Cambridgeshire. Town -> county answers are kept in jobs/places.json, so each town is only looked up once.
PLACES_PATH = OUT / "places.json"
PLACE_RANK = {"City": 0, "Town": 1, "Other Settlement": 2, "Suburban Area": 3, "Village": 4, "Hamlet": 5}
# postcodes.io leaves the county blank for metropolitan districts and London
MET_COUNTIES = {
    **dict.fromkeys(["Bolton", "Bury", "Manchester", "Oldham", "Rochdale", "Salford", "Stockport", "Tameside", "Trafford", "Wigan"], "Greater Manchester"),
    **dict.fromkeys(["Knowsley", "Liverpool", "Sefton", "St. Helens", "St Helens", "Wirral"], "Merseyside"),
    **dict.fromkeys(["Barnsley", "Doncaster", "Rotherham", "Sheffield"], "South Yorkshire"),
    **dict.fromkeys(["Gateshead", "Newcastle upon Tyne", "North Tyneside", "South Tyneside", "Sunderland"], "Tyne and Wear"),
    **dict.fromkeys(["Birmingham", "Coventry", "Dudley", "Sandwell", "Solihull", "Walsall", "Wolverhampton"], "West Midlands"),
    **dict.fromkeys(["Bradford", "Calderdale", "Kirklees", "Leeds", "Wakefield"], "West Yorkshire"),
}
_places = None


def best_place(results, name):
    """Exact name matches only, cities before towns before villages ('Cambridge' the city, not the hamlet)."""
    exact = [p for p in results or [] if (p.get("name_1") or "").lower() == name.lower()]
    return min(exact, key=lambda p: PLACE_RANK.get(p.get("local_type"), 9)) if exact else None


def county_of(place):
    if not place:
        return ""
    if place.get("region") == "London":
        return "Greater London"
    district = place.get("district_borough")
    county = MET_COUNTIES.get(district) or place.get("county_unitary") or district or ""
    return county.split(" - ")[-1]  # Welsh areas come as "Caerdydd - Cardiff"


MET_BY_NAME = {k.lower(): v for k, v in MET_COUNTIES.items()}


def places():
    global _places
    if _places is None:
        _places = previous_places()
    return _places


def remember_county(town, county):
    places().setdefault(town.lower(), county)


def lookup_county(town):
    """County for a town. Site names like 'Cambridge Bio Medical Campus' fall back to their first words."""
    _places = places()
    key = town.lower()
    if key not in _places:
        county = MET_BY_NAME.get(key, "")
        words = town.split()
        for name in dict.fromkeys([town, " ".join(words[:2]), words[0]]):  # full name, then first two words, then first
            if county:
                break
            q = urllib.parse.urlencode({"q": name, "limit": 20})
            data = get_json(f"https://api.postcodes.io/places?{q}") or {}
            county = county_of(best_place(data.get("result"), name))
        _places[key] = county
    return _places[key]


def previous_places():
    try:
        return json.loads(PLACES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def add_areas(jobs):
    for j in jobs:
        town = (j["location"] or "").split(",")[0].strip()
        if town and town.lower() not in ("uk", "united kingdom", "england", "remote"):
            j["area"] = lookup_county(town)
    return jobs


# ---------- Building ----------
def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def collect_nhs(degree, phrases, now):
    """Every pay band's NHS jobs for one degree, written to jobs/gb/nhs-<degree>.json."""
    keyword = NHS_KEYWORDS[degree]
    found = [j for band in NHS_BANDS for j in nhs_search(keyword, band)]
    jobs = add_areas(merge([j for j in found if title_matches(j["title"], phrases + [keyword])]))
    path = OUT / "gb" / f"nhs-{slug(degree)}.json"
    carry_first_seen(jobs, previous_jobs(path), now)
    write(path, {"degree": degree, "country": "gb", "updated": now, "jobs": jobs})
    print(f"  gb NHS {degree}: {len(jobs)}")
    return [dict(j) for j in jobs]


def collect_country(country, reed_key, adzuna):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    total = 0
    for degree, phrases in DEGREES.items():
        found = []
        if country == "gb" and reed_key:
            for phrase in phrases:
                found += reed_search(reed_key, phrase)
        # one Adzuna call per degree keeps inside its free limits
        if adzuna and country == "gb":
            found += adzuna_search(*adzuna, country, title_only=phrases[0])
        elif adzuna:
            found += adzuna_search(*adzuna, country, what_or=" ".join(sorted({w for p in phrases for w in p.split()})))
        if country == "gb" and degree in NHS_KEYWORDS:
            nhs = collect_nhs(degree, phrases, now)
            found += nhs
            total += len(nhs)
        path = OUT / country / f"{slug(degree)}.json"
        jobs = merge(place_postcodes([j for j in found if title_matches(j["title"], phrases)]))
        if country == "gb":
            add_areas(jobs)
        carry_first_seen(jobs, previous_jobs(path), now)
        write(path, {"degree": degree, "country": country, "updated": now, "jobs": jobs})
        total += len(jobs)
        print(f"  {country} {degree}: {len(jobs)}")
    for pool, phrases in OPEN_PHRASES.items():
        found = []
        for phrase in phrases:
            if country == "gb" and reed_key:
                found += reed_search(reed_key, f'"{phrase}"', graduate="true")
            if adzuna:
                found += adzuna_search(*adzuna, country, what_phrase=phrase)
        path = OUT / country / f"open-{pool}.json"
        jobs = merge(place_postcodes(found))
        if country == "gb":
            add_areas(jobs)
        carry_first_seen(jobs, previous_jobs(path), now)
        write(path, {"pool": pool, "country": country, "updated": now, "jobs": jobs})
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
    if _places is not None:
        write(PLACES_PATH, dict(sorted(_places.items())))  # the site reads this to widen town searches to the county
    print("Done.")


if __name__ == "__main__":
    main()
