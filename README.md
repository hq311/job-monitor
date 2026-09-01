# Singapore Actuarial Job Monitor

A targeted monitor for Singapore job listings on MyCareersFuture. It includes roles whose titles contain `Actuary` or `Actuarial`, plus every opening from Zurich Insurance Company Ltd (Singapore Branch), Chubb Asia Pacific Pte. Ltd., and Munich Re. It is not a complete index of all Singapore jobs, and promoted/recommended cards are excluded.

## Run

Requires Python 3.9 or newer.

```bash
python3 monitor.py
```

Then open `dashboard.html` in the repository folder. Run the same command later to discover new jobs and update existing ones.

Live dashboard: [https://hq311.github.io/job-monitor/](https://hq311.github.io/job-monitor/)

The dashboard includes company, inclusion-reason, employment-type, location, status, salary, and new-posting filters; sortable columns with a sticky header; CSV export; a salary trend chart calculated from the currently visible active postings; and a **New** badge for jobs posted within the last seven days. Watched companies use shorter display names on the dashboard while exact registered names remain in the stored data and archived job details.

The header separates **Last checked** (the latest successful live fetch) from **Job data last changed** (the latest material listing change, excluding routine `last_seen_at` refreshes). If a newer live update attempt fails, the dashboard displays a warning while retaining the last good dataset. The small **Run log** button (top right) expands to show the five latest monitor invocations. Its run-source labels distinguish **GitHub Actions · Scheduled**, **GitHub Actions · Manual**, **Local script**, and older records whose origin is unknown. Future Actions entries also retain the GitHub run link and, for manual dispatches, the initiating actor. Successful live checks say whether job data changed; dashboard-only rebuilds retain the latest saved totals but are explicitly labeled **Data unchanged**. The full append-only JSON-lines history is stored at `logs/run_history.jsonl`.

Configuration lives in `config.json`. The tracker searches `Actuary`, `Actuarial`, and each watched company; deduplicates results by source UUID; and retains jobs matching the configured actuarial title stem or an exact watched-company name. Company matches are retained regardless of title.

Each MyCareersFuture request gets one additional attempt after a short delay when it fails because of a timeout, connection problem, rate limit, or server error. Generated dashboard and detail files are rewritten only when their contents changed.

Each job records and displays all matching criteria. For example, a Zurich actuarial posting can carry both title and company tags.

Each dashboard job title opens a local archived detail page. The archive retains key fields, skills, and the captured description even after the live source page disappears. A separate `Live` link opens MyCareersFuture while the posting is available.

## Lifecycle rules

- A source job UUID is stored only once.
- A seen job remains `active`, and its `last_seen_at` is updated.
- A posting whose advertised expiry date has passed is marked `expired` immediately, including when the site reports that applications have closed.
- An absent job is marked `expired` after a complete successful search.
- Failed or incomplete searches never modify lifecycle status.
- Expired jobs remain in the JSON history.

The data is stored in `data/jobs.json`. Each successful update is written through a validated temporary file, and the prior dataset is kept as `data/jobs.json.backup`.

## Automation

`.github/workflows/job-monitor.yml` runs the monitor every other day at about 10:00 AM Singapore time and can also be started manually from the repository's **Actions** tab. It commits updated job data, archived details, the dashboard, and the run history back to `main`; GitHub Pages then publishes the dashboard update.

## Scope

This prototype makes low-frequency, read-only requests to MyCareersFuture's current job-search endpoint. It stores only listing metadata needed by the monitor, not full descriptions or applicant information. Confirm the site's permission and terms before operating or distributing it beyond personal testing.
