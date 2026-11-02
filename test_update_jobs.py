"""Tests for the job collector. Run: py -m unittest test_update_jobs"""
import datetime as dt
import re
import unittest
from pathlib import Path

import update_jobs as uj

TODAY = dt.date(2026, 11, 1)


def reed(job_id, title, employer, location, deadline="30/11/2026", salary=(28000, 32000)):
    return {"jobId": job_id, "jobTitle": title, "employerName": employer, "locationName": location,
            "minimumSalary": salary[0], "maximumSalary": salary[1], "currency": "GBP",
            "expirationDate": deadline, "date": "25/10/2026", "jobUrl": f"https://www.reed.co.uk/jobs/{job_id}"}


def adzuna(job_id, title, company, location, salary=(None, None), predicted="0"):
    return {"id": job_id, "title": title, "company": {"display_name": company},
            "location": {"display_name": location}, "salary_min": salary[0], "salary_max": salary[1],
            "salary_is_predicted": predicted, "created": "2026-10-28T09:00:00Z",
            "redirect_url": f"https://www.adzuna.co.uk/jobs/land/ad/{job_id}"}


class Parsing(unittest.TestCase):
    def test_reed_record(self):
        j = uj.from_reed(reed(1, "Graduate Analytical Chemist", "Unilever UK Ltd", "Port Sunlight"))
        self.assertEqual(j["deadline"], "2026-11-30")
        self.assertEqual(j["posted"], "2026-10-25")
        self.assertEqual((j["salaryMin"], j["salaryMax"]), (28000, 32000))
        self.assertTrue(j["grad"])

    def test_adzuna_strips_highlight_tags(self):
        j = uj.from_adzuna(adzuna("a1", "<strong>Chemist</strong> &amp; Analyst", "Croda", "Goole, East Riding"), "GBP")
        self.assertEqual(j["title"], "Chemist & Analyst")
        self.assertIsNone(j["deadline"])
        self.assertIsNone(j["salaryMin"])

    def test_title_match_is_whole_word(self):
        phrases = ["chemist", "chemistry"]
        self.assertTrue(uj.title_matches("Formulation Chemist (m/w/d)", phrases))
        self.assertFalse(uj.title_matches("Chemicals Sales Executive", phrases))
        self.assertFalse(uj.title_matches("Graduate Finance Scheme - open to all STEM", phrases))
        self.assertFalse(uj.title_matches("Sales Manager - Transport", ["sport"]))


class Level(unittest.TestCase):
    def test_plain_titles_count_as_entry_level(self):
        for title in ["Analytical Chemist", "QC Analyst", "Graduate Consultant", "Junior Project Manager", "Laboratory Technician"]:
            self.assertTrue(uj.entry_level(title), title)

    def test_senior_titles_do_not(self):
        for title in ["Senior Chemist", "Lead Developer", "Laboratory Manager", "Principal Scientist", "Head of R&D"]:
            self.assertFalse(uj.entry_level(title), title)


class Merging(unittest.TestCase):
    def test_same_job_on_two_sites_becomes_one(self):
        jobs = [uj.from_reed(reed(1, "Analytical Chemist", "Unilever UK Ltd", "Manchester")),
                uj.from_adzuna(adzuna("a1", "Analytical Chemist", "UNILEVER", "Manchester, Greater Manchester"), "GBP")]
        out = uj.merge(jobs, TODAY)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["sources"], ["Adzuna", "Reed"])
        self.assertEqual(out[0]["deadline"], "2026-11-30")  # Reed's deadline kept

    def test_estimated_salary_replaced_by_real_one(self):
        jobs = [uj.from_adzuna(adzuna("a1", "Process Chemist", "Croda", "Goole", (30000, 30000), "1"), "GBP"),
                uj.from_reed(reed(2, "Process Chemist", "Croda", "Goole", salary=(27000, 29000)))]
        out = uj.merge(jobs, TODAY)
        self.assertEqual((out[0]["salaryMin"], out[0]["salaryEstimated"]), (27000, False))

    def test_expired_and_broken_jobs_dropped(self):
        jobs = [uj.from_reed(reed(1, "Chemist", "A", "Leeds", deadline="31/10/2026")),
                uj.from_reed(reed(2, "Chemist", "B", "Leeds"))]
        jobs.append(dict(jobs[1], id="x", url=None, company="C"))
        out = uj.merge(jobs, TODAY)
        self.assertEqual([j["company"] for j in out], ["B"])

    def test_different_locations_stay_separate(self):
        jobs = [uj.from_reed(reed(1, "Chemist", "Croda", "Goole")),
                uj.from_reed(reed(2, "Chemist", "Croda", "Leek"))]
        self.assertEqual(len(uj.merge(jobs, TODAY)), 2)

    def test_sorted_by_deadline_then_newest(self):
        a = uj.from_reed(reed(1, "Chemist", "A", "X", deadline="20/11/2026"))
        b = uj.from_reed(reed(2, "Chemist", "B", "X", deadline="05/11/2026"))
        c = uj.from_adzuna(adzuna("c", "Chemist", "C", "X"), "GBP")
        self.assertEqual([j["company"] for j in uj.merge([a, b, c], TODAY)], ["B", "A", "C"])


class SummariesAndNewness(unittest.TestCase):
    def test_summary_is_trimmed_at_a_word(self):
        text = "<p>We are hiring a graduate chemist to join our busy QC lab.</p> " + "Great benefits and training. " * 20
        s = uj.summarise(text)
        self.assertTrue(s.startswith("We are hiring a graduate chemist"))
        self.assertTrue(s.endswith("…"))
        self.assertLessEqual(len(s), uj.SUMMARY_LENGTH + 1)
        self.assertEqual(uj.summarise("Short advert."), "Short advert.")

    def test_longer_summary_kept_when_merging(self):
        a = dict(uj.from_reed(reed(1, "Chemist", "Croda", "Goole")), summary="Short.")
        b = dict(uj.from_adzuna(adzuna("a", "Chemist", "Croda", "Goole"), "GBP"), summary="A much longer description of the role.")
        self.assertEqual(uj.merge([a, b], TODAY)[0]["summary"], "A much longer description of the role.")

    def test_first_seen_survives_between_runs(self):
        old = [dict(uj.from_reed(reed(1, "Chemist", "Croda", "Goole")), firstSeen="2026-10-20T05:00:00+00:00")]
        new = [uj.from_adzuna(adzuna("a", "Chemist", "Croda", "Goole"), "GBP"),
               uj.from_reed(reed(2, "Chemist", "Ineos", "Runcorn"))]
        uj.carry_first_seen(new, old, "2026-11-02T05:00:00+00:00")
        self.assertEqual([j["firstSeen"] for j in new], ["2026-10-20T05:00:00+00:00", "2026-11-02T05:00:00+00:00"])


NHS_VACANCY = """<vacancyDetails><id>5695022</id><reference>C9348-26-2398</reference>
<title>Band 5 Rotational Physiotherapist</title><description>Join our friendly MSK and inpatient rotation. Preceptorship and CPD support provided for newly qualified physiotherapists.</description>
<employer>Example NHS Trust</employer><type>Permanent</type><salary>£32073.00 to £39043.00</salary>
<closeDate>2026-11-20</closeDate><postDate>2026-10-28T09:12:00.1234</postDate>
<url>https://beta.jobs.nhs.uk/candidate/jobadvert/C9348-26-2398</url>
<locations><location>Portsmouth, PO2 0TA</location><location>Fareham, PO16 0LF</location><location>Portsmouth, PO6 3LY</location></locations></vacancyDetails>"""


class NHS(unittest.TestCase):
    def test_vacancy_record(self):
        j = uj.from_nhs(uj.ET.fromstring(NHS_VACANCY), "5")
        self.assertEqual(j["id"], "nhs-5695022")
        self.assertEqual(j["band"], "5")
        self.assertTrue(j["grad"])
        self.assertEqual(j["location"], "Portsmouth, Fareham")
        self.assertEqual((j["salaryMin"], j["salaryMax"]), (32073, 39043))
        self.assertEqual((j["deadline"], j["posted"]), ("2026-11-20", "2026-10-28"))
        self.assertEqual(j["sources"], ["NHS Jobs"])

    def test_band_codes_and_seniority(self):
        self.assertEqual(uj.from_nhs(uj.ET.fromstring(NHS_VACANCY), "8A")["band"], "8a")
        self.assertFalse(uj.from_nhs(uj.ET.fromstring(NHS_VACANCY), "7")["grad"])

    def test_salary_formats(self):
        self.assertEqual(uj.nhs_salary("£31.11"), (31, 31))
        self.assertEqual(uj.nhs_salary("Negotiable"), (None, None))
        self.assertEqual(uj.nhs_salary("£24,169 - £25,524 per annum"), (24169, 25524))

    def test_band_survives_merge_with_reed_copy(self):
        nhs = uj.from_nhs(uj.ET.fromstring(NHS_VACANCY), "5")
        reed_copy = uj.from_reed(reed(9, "Band 5 Rotational Physiotherapist", "Example NHS Trust", "Portsmouth"))
        self.assertEqual(uj.merge([reed_copy, nhs], TODAY)[0]["band"], "5")

    def test_every_nhs_degree_is_a_degree(self):
        self.assertTrue(set(uj.NHS_KEYWORDS) <= set(uj.DEGREES))


class Employers(unittest.TestCase):
    def test_reed_itself_is_not_the_employer(self):
        for name in ["Reed", "REED SPECIALIST RECRUITMENT", "Reed Specialist Recruitment Ltd", "Reed.co.uk"]:
            self.assertEqual(uj.employer(name), (uj.UNNAMED, "Reed"), name)

    def test_real_companies_keep_their_name(self):
        self.assertEqual(uj.employer("Owen Reed"), ("Owen Reed", None))
        self.assertEqual(uj.employer("Breedon Group plc"), ("Breedon Group plc", None))
        self.assertEqual(uj.employer("Rise Technical Recruitment Limited"), ("Rise Technical Recruitment Limited", "Rise Technical Recruitment Limited"))
        self.assertEqual(uj.employer(""), (uj.UNNAMED, None))

    def test_unnamed_employers_never_merge(self):
        a = uj.from_reed(reed(1, "Chemist", "Reed", "Leeds"))
        b = uj.from_reed(reed(2, "Chemist", "Reed", "Leeds"))
        self.assertEqual(len(uj.merge([a, b], TODAY)), 2)


class Counties(unittest.TestCase):
    def test_city_beats_village_of_same_name(self):
        results = [
            {"name_1": "Cambridge", "local_type": "Village", "county_unitary": "Gloucestershire", "region": "South West"},
            {"name_1": "Cambridge", "local_type": "City", "county_unitary": "Cambridgeshire", "region": "Eastern"},
            {"name_1": "Cambridge Heath", "local_type": "Suburban Area", "region": "London"},
        ]
        self.assertEqual(uj.county_of(uj.best_place(results, "Cambridge")), "Cambridgeshire")

    def test_metropolitan_and_london(self):
        self.assertEqual(uj.county_of({"district_borough": "Manchester", "county_unitary": None, "region": "North West"}), "Greater Manchester")
        self.assertEqual(uj.county_of({"district_borough": "Westminster", "region": "London"}), "Greater London")
        self.assertEqual(uj.county_of(None), "")

    def test_add_areas_uses_first_town(self):
        original = uj.lookup_county
        uj.lookup_county = lambda town: {"Portsmouth": "Hampshire"}.get(town, "")
        try:
            jobs = uj.add_areas([{"location": "Portsmouth, Fareham"}, {"location": "UK"}])
        finally:
            uj.lookup_county = original
        self.assertEqual(jobs[0]["area"], "Hampshire")
        self.assertNotIn("area", jobs[1])


class Postcodes(unittest.TestCase):
    def test_spots_postcodes_only(self):
        self.assertEqual(uj.postcode_of("PE129YD"), "PE12 9YD")
        self.assertEqual(uj.postcode_of(" ts17 7bw "), "TS17 7BW")
        self.assertEqual(uj.postcode_of("EC1A 1BB"), "EC1A 1BB")
        self.assertIsNone(uj.postcode_of("Manchester"))
        self.assertIsNone(uj.postcode_of("10115 Berlin"))

    def test_replaces_with_town_or_tidies(self):
        jobs = [{"location": "PE129YD"}, {"location": "ZZ99 9ZZ"}, {"location": "Leeds"}]
        uj.place_postcodes(jobs, lookup=lambda codes: {"PE12 9YD": "South Holland"})
        self.assertEqual([j["location"] for j in jobs], ["South Holland", "ZZ99 9ZZ", "Leeds"])


class Config(unittest.TestCase):
    def test_degrees_match_the_site(self):
        page = (Path(__file__).resolve().parent / "job-finder.html").read_text(encoding="utf-8")
        block = page.split("const DEGREES = {", 1)[1].split("\n};", 1)[0]
        site = re.findall(r'^\s*"([^"]+)":', block, re.M)
        self.assertEqual(sorted(site), sorted(uj.DEGREES))


if __name__ == "__main__":
    unittest.main()
