# Singapore Actuarial Job Monitor — Codex Handoff

Use this file as the starting context for a new Codex task. Inspect the current repository before changing anything, because the GitHub Actions workflow may have committed newer tracking data.

## Project purpose

This is a small, dependency-free Python monitor for targeted MyCareersFuture job postings. It is not intended to collect all jobs.

The tracker should:

- Store a local archive of matched postings and captured job details.
- Avoid duplicates using the MyCareersFuture source UUID.
- Retain expired postings rather than deleting them.
- Exclude promoted/recommended cards.
- Publish a static dashboard through GitHub Pages.

## Repository and deployment

- Local repository: `/Users/haiqi/Desktop/Codex/job-monitor`
- GitHub repository: `https://github.com/hq311/job-monitor`
- Public dashboard: `https://hq311.github.io/job-monitor/`
- GitHub Pages serves the `main` branch from the repository root.
- `index.html` redirects the Pages root URL to `dashboard.html`.

## What is tracked

Configured in `config.json`:

- Title keywords: `Actuary`, `actuarial` (implemented using the `actuar` title stem).
- Every posting from these exact company names, regardless of title:
  - `ZURICH INSURANCE COMPANY LTD (SINGAPORE BRANCH)`
  - `CHUBB ASIA PACIFIC PTE. LTD.`
  - `MUNICH RE`

The monitor searches each configured title/company term, deduplicates API results by UUID, then applies the local title/company matching rules. A job can match multiple criteria; these are shown as dashboard tags.

## Main files

| File | Purpose |
| --- | --- |
| `monitor.py` | Fetches MyCareersFuture data, updates lifecycle data, writes dashboard and archived detail pages. Uses Python standard library only. |
| `config.json` | Search terms, title/company criteria, page size, and request timeout. |
| `data/jobs.json` | Tracked job archive. This is committed so GitHub Actions can persist changes. |
| `dashboard.html` | Generated static dashboard at the repository root. |
| `output/details/` | Generated local archived detail pages linked from the dashboard. |
| `logs/run_history.jsonl` | Append-only monitor run history shown by the dashboard. |
| `.github/workflows/job-monitor.yml` | GitHub Actions schedule and auto-commit workflow. |
| `README.md` | User-facing setup and usage documentation. |

## Lifecycle rules

There are only two dashboard lifecycle states:

- `active`: returned by the latest successful complete search and not past its advertised expiry date.
- `expired`: no longer returned by a successful complete search, or its advertised expiry date has passed / the source reports applications closed.

Do not reintroduce a `possibly_missing` state unless the user explicitly asks for it. Failed or incomplete fetches must not expire jobs.

## Dashboard behavior

- Table filters: title/company search, company, inclusion reason, employment type, location, minimum salary, status, and jobs posted within the last 7 days.
- `New` badge: a job posted within the last 7 days; this is not based on when the monitor first found it.
- Table sorting: table headers and salary sorting dropdown.
- Table headers remain visible while scrolling through the job list.
- CSV export: exports only the currently visible table rows.
- Job titles open archived detail pages; the final `Live` link opens the current source page.
- The top run-log button expands to show the five most recent monitor invocations.
- Run-source labels explicitly distinguish `GitHub Actions · Scheduled`, `GitHub Actions · Manual`, `Local script`, and `Historical · Unknown`. New Actions entries include a direct run link; manual entries also include the initiating GitHub actor.
- Render-only entries show the latest saved totals with a clear `Data unchanged` label.
- The header shows the latest successful live data update; render-only runs do not change this timestamp.
- If the latest live fetch fails, the saved dashboard is rebuilt with a warning while the last good job data remains unchanged.
- Dashboard timestamps display the compact `SGT` label (Singapore Time).
- The salary trend chart uses all **visible** postings, including expired ones when the status filter includes them. The top summary cards remain active-posting statistics.
- Watched companies use short display names on the dashboard; matching and archived details retain exact registered names.
- Transient request failures receive one retry after the configured delay.
- Generated dashboard/detail files are written only when their content changed.

## Run commands

Run the real monitor locally:

```bash
cd /Users/haiqi/Desktop/Codex/job-monitor
python3 monitor.py
```

Rebuild dashboard/detail pages without accessing MyCareersFuture:

```bash
python3 monitor.py --render-only
```

Verify syntax:

```bash
PYTHONPYCACHEPREFIX=/tmp/job-monitor-pycache python3 -m py_compile monitor.py
```

## GitHub Actions

The workflow:

- Can run manually via **Actions → Singapore actuarial job monitor → Run workflow**.
- Schedules daily at 02:00 UTC, but a cadence step runs the monitor only every other day (about 10:00 AM Singapore time), including across month boundaries.
- Uses `actions/checkout@v5`, `actions/setup-python@v6`, and Python 3.11.
- Commits `data/jobs.json`, `dashboard.html`, `output/details`, and `logs/run_history.jsonl` only when they change.
- Uses descriptive generated commit messages with UTC timestamp and Actions run number.
- Preserves a failed monitor run in `logs/run_history.jsonl` before marking the workflow failed.

If the workflow fails with `pathspec 'logs/monitor.log' did not match any files`, do not add that file to the `git add` list; it is intentionally not part of the workflow commit.

## Working safely with Git

The Codex sandbox may be unable to create `.git/index.lock` or `.git/FETCH_HEAD` in the Desktop folder. If that happens, make code changes and ask the user to run Git commands in their own Terminal.

Before modifying code, check:

```bash
git status --short --branch
git log -3 --oneline --decorate
```

Before a local change, synchronize with GitHub:

```bash
git pull --rebase origin main
```

If GitHub Actions committed a newer tracker update and the rebase conflicts only in generated files (`dashboard.html`, `data/jobs.json`, or `logs/run_history.jsonl`), prefer the remote versions unless the user specifically wants the local generated result preserved.

## Suggested new-chat prompt

```text
Continue work on /Users/haiqi/Desktop/Codex/job-monitor.

Read CODEX_HANDOFF.md and inspect the current Git status, README, config, monitor.py, and workflow before changing anything. Preserve the targeted tracking criteria and active/expired lifecycle rules.
```
