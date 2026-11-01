# Catalyst

A job-finding website for graduates: pick your degree, then browse current job openings in your subject or open to any degree, and track the jobs you shortlist and apply for.

Every morning a GitHub Action collects listings from the **Reed** and **Adzuna** job APIs, removes duplicates and expired jobs, saves them to `website/jobs/`, and publishes the site to **GitHub Pages**.

## What's in this folder

| Path | What it is |
| --- | --- |
| `job-finder.html` | The app itself. Edit this one. |
| `build-website.ps1` | Turns `job-finder.html` into `website/index.html`. Run it after editing. |
| `website/` | The published site. `website/jobs/` is filled in by the daily update. |
| `scripts/update_jobs.py` | Collects, de-duplicates and saves the job listings. |
| `scripts/degrees.json` | Job-title phrases that count as "in my subject" for each degree. |
| `.github/workflows/update-jobs.yml` | Runs the collector daily and publishes the site. |

## Setting it up (once)

1. **Get free API keys**
   - Reed: sign up at <https://www.reed.co.uk/developers/jobseeker>. The key is shown on your account page.
   - Adzuna: register at <https://developer.adzuna.com>. You get an **Application ID** and an **Application Key**.
2. **Create a GitHub repository.** Sign up at <https://github.com>, then make a new **public** repository called `catalyst`.
3. **Upload this folder.** On the repository page choose **Add file → Upload files**, drag in everything in this folder (including the `.github` folder) and press **Commit changes**.
4. **Add the keys as secrets.** Go to **Settings → Secrets and variables → Actions → New repository secret** and add three secrets:
   `REED_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`.
   Secrets stay private: they are never put in the website.
5. **Turn on Pages.** Go to **Settings → Pages** and set **Source** to **GitHub Actions**.
6. **Run the first update.** Go to **Actions → Update jobs and publish site → Run workflow**, tick **Refresh every country**, and run it. It takes about 15 minutes.

Your site is then live at `https://<your-username>.github.io/catalyst/`.

## How the listings stay current

- The UK refreshes every day at 05:00 UTC. Germany, the Netherlands, France and Spain each refresh once a week, which keeps within Adzuna's free limits.
- Each refresh rebuilds the lists from scratch, so filled or closed jobs disappear. The site also hides any job whose closing date has passed.
- The same job found on both Reed and Adzuna is shown once, labelled "via Adzuna & Reed".

## Changing the site

Edit `job-finder.html`, run `build-website.ps1`, and upload the changed files to GitHub. The site republishes on its own.

To test the collector: `py -m unittest discover -s scripts`
