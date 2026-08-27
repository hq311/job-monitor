# MyCareersFuture Job Monitor

A small, dependency-free prototype that tracks organic MyCareersFuture jobs matching the title keywords `Actuary`/`actuarial` plus every job posted by Zurich Insurance Company Ltd (Singapore Branch), Chubb Asia Pacific Pte. Ltd., and Munich Re. It avoids duplicate records, records disappearing postings, and generates a static dashboard while excluding separate promoted/recommended cards.

## Run

Requires Python 3.9 or newer.

```bash
python3 monitor.py
```

Then open `dashboard.html` in the repository folder (or visit the GitHub file). Run the same command later to discover new jobs and update existing ones.

Live dashboard: [https://hq311.github.io/job-monitor/](https://hq311.github.io/job-monitor/)

The dashboard's small **Run log** button (top right) expands to show the last ten monitor invocations, including success/failure, execution time, fetched/tracked/new/missing/expired counts, and any error message. The full append-only JSON-lines history is stored at `logs/run_history.jsonl`.

Configuration lives in `config.json`. The initial setup combines `Actuary`, `Actuarial`, and the Zurich company search; deduplicates them by source UUID; and retains jobs when either the title contains the stem `actuar` or the returned company name exactly matches the configured Zurich company. Zurich jobs are retained regardless of title.

Each job records and displays all matching criteria. For example, a Zurich actuarial posting can carry both title and company tags.

Each dashboard job title opens a local archived detail page. The archive retains key fields, skills, and the captured description even after the live source page disappears. A separate `Live` link opens MyCareersFuture while the posting is available.

## Lifecycle rules

- A source job UUID is stored only once.
- A seen job remains `active`, and its `last_seen_at` is updated.
- An absent job becomes `possibly_missing`.
- After three consecutive complete successful searches, it becomes `expired`.
- Failed or incomplete searches never modify lifecycle status.
- Expired jobs remain in the JSON history.

The data is stored in `data/jobs.json`. Each successful update is written through a validated temporary file, and the prior dataset is kept as `data/jobs.json.backup`.

## Scope

This prototype makes low-frequency, read-only requests to MyCareersFuture's current job-search endpoint. It stores only listing metadata needed by the monitor, not full descriptions or applicant information. Confirm the site's permission and terms before operating or distributing it beyond personal testing.
