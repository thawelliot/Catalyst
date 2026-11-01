# Catalyst

A job-finding website for graduates: pick your degree, then browse current job openings in your subject or open to any degree, and track the jobs you shortlist and apply for.

Every morning a GitHub Action collects listings from the **Reed** and **Adzuna** job APIs and the **NHS Jobs** feed, removes duplicates and expired jobs, saves them to the `jobs` folder, and publishes the site to **GitHub Pages**.

Health degrees (Physiotherapy, Nursing, Midwifery and others) get an extra **NHS** option, with jobs filtered by Agenda for Change pay band. Band 5 is the usual starting band for newly qualified staff. The NHS search words live under `_nhs` in `degrees.json`.

## What's in this folder

Everything sits at the top level, so uploading to GitHub is a plain drag and drop.

| File | What it is |
| --- | --- |
| `job-finder.html` | The app itself. Edit this one. |
| `build-website.ps1` | Turns `job-finder.html` into `index.html`, the site's home page. Run it after editing. |
| `index.html` | The published home page. |
| `update_jobs.py` | Collects, de-duplicates and saves the job listings into `jobs/`. |
| `degrees.json` | Job-title phrases that count as "in my subject" for each degree. |
| `test_update_jobs.py` | Tests for the collector. |
| `.github/workflows/update-jobs.yml` | Runs the collector daily and publishes the site. |
| `jobs/` | Created by the daily update. Don't edit it by hand. |

## Setting it up (once)

1. **Get free API keys** from Reed (<https://www.reed.co.uk/developers/jobseeker>) and Adzuna (<https://developer.adzuna.com>).
2. **Create a public GitHub repository** and upload the files above. Create the workflow with **Add file → Create new file**, named `.github/workflows/update-jobs.yml`.
3. **Add three secrets** under **Settings → Secrets and variables → Actions**: `REED_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`. They are never put in the website.
4. **Settings → Pages → Source: GitHub Actions.**
5. **Actions → Update jobs and publish site → Run workflow**, ticking **Refresh every country**. It takes about 20 minutes.

The site is then live at `https://<your-username>.github.io/<repository-name>/`.

## How the listings stay current

- The UK refreshes every day at 05:00 UTC. Germany, the Netherlands, France and Spain each refresh once a week, which keeps within Adzuna's free limits.
- Each refresh rebuilds the lists from scratch, so filled or closed jobs disappear. The site also hides any job whose closing date has passed.
- The same job found on both Reed and Adzuna is shown once, labelled "via Adzuna & Reed".

## Changing the site

Edit `job-finder.html`, run `build-website.ps1`, and upload `index.html` (and any other changed files) to GitHub. The site republishes on its own.

To test the collector: `py -m unittest test_update_jobs`
