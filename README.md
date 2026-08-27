# MyCareersFuture Job Monitor

A small, dependency-free tracker for organic MyCareersFuture jobs. It tracks postings whose titles contain `Actuary` or `actuarial`, plus every job from Zurich Insurance Company Ltd (Singapore Branch), Chubb Asia Pacific Pte. Ltd., and Munich Re. Promoted/recommended cards are excluded.

## Run

Requires Python 3.9 or newer.

```bash
python3 monitor.py
```

Then open `dashboard.html` in the repository folder. Run the same command later to discover new jobs and update existing ones.

Live dashboard: [https://hq311.github.io/job-monitor/](https://hq311.github.io/job-monitor/)

The dashboard includes company, status, salary, and new-posting filters; sortable columns; CSV export; a salary trend chart calculated from the currently visible active postings; and a **New** badge for jobs posted within the last seven days.

The small **Run log** button (top right) expands to show the five latest monitor invocations, including success/failure, execution time, fetched/tracked/new/expired counts, and any error message. The full append-only JSON-lines history is stored at `logs/run_history.jsonl`.

Configuration lives in `config.json`. The tracker searches `Actuary`, `Actuarial`, and each watched company; deduplicates results by source UUID; and retains jobs matching the configured actuarial title stem or an exact watched-company name. Company matches are retained regardless of title.

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
