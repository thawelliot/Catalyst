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
        j = uj.from_reed(reed(1, "Graduate Analytical Chemist", "Unilever UK Ltd", "Port Sunlight"), graduate=True)
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
        self.assertFalse(uj.title_matches("Chemical Plant Operator", phrases))
        self.assertFalse(uj.title_matches("Graduate Finance Scheme - open to all STEM", phrases))
        self.assertFalse(uj.title_matches("Sales Manager - Transport", ["sport"]))


class Merging(unittest.TestCase):
    def test_same_job_on_two_sites_becomes_one(self):
        jobs = [uj.from_reed(reed(1, "Analytical Chemist", "Unilever UK Ltd", "Manchester"), False),
                uj.from_adzuna(adzuna("a1", "Analytical Chemist", "UNILEVER", "Manchester, Greater Manchester"), "GBP")]
        out = uj.merge(jobs, TODAY)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["sources"], ["Adzuna", "Reed"])
        self.assertEqual(out[0]["deadline"], "2026-11-30")  # Reed's deadline kept

    def test_estimated_salary_replaced_by_real_one(self):
        jobs = [uj.from_adzuna(adzuna("a1", "Process Chemist", "Croda", "Goole", (30000, 30000), "1"), "GBP"),
                uj.from_reed(reed(2, "Process Chemist", "Croda", "Goole", salary=(27000, 29000)), False)]
        out = uj.merge(jobs, TODAY)
        self.assertEqual((out[0]["salaryMin"], out[0]["salaryEstimated"]), (27000, False))

    def test_expired_and_broken_jobs_dropped(self):
        jobs = [uj.from_reed(reed(1, "Chemist", "A", "Leeds", deadline="31/10/2026"), False),
                uj.from_reed(reed(2, "Chemist", "B", "Leeds"), False)]
        jobs.append(dict(jobs[1], id="x", url=None, company="C"))
        out = uj.merge(jobs, TODAY)
        self.assertEqual([j["company"] for j in out], ["B"])

    def test_different_locations_stay_separate(self):
        jobs = [uj.from_reed(reed(1, "Chemist", "Croda", "Goole"), False),
                uj.from_reed(reed(2, "Chemist", "Croda", "Leek"), False)]
        self.assertEqual(len(uj.merge(jobs, TODAY)), 2)

    def test_sorted_by_deadline_then_newest(self):
        a = uj.from_reed(reed(1, "Chemist", "A", "X", deadline="20/11/2026"), False)
        b = uj.from_reed(reed(2, "Chemist", "B", "X", deadline="05/11/2026"), False)
        c = uj.from_adzuna(adzuna("c", "Chemist", "C", "X"), "GBP")
        self.assertEqual([j["company"] for j in uj.merge([a, b, c], TODAY)], ["B", "A", "C"])


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
