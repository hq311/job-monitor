#!/usr/bin/env python3
"""Small MyCareersFuture job monitor using only the Python standard library."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
JOBS_PATH = DATA_DIR / "jobs.json"
DASHBOARD_PATH = ROOT / "dashboard.html"
API_URL = "https://api.mycareersfuture.gov.sg/v2/jobs"
SGT = ZoneInfo("Asia/Singapore")
RUN_LOG_PATH = LOG_DIR / "run_history.jsonl"
RUN_STARTED_AT: str | None = None
RUN_MODE = "monitor"


def now_iso() -> str:
    return datetime.now(SGT).replace(microsecond=0).isoformat()


def elapsed_seconds(started_at: str | None, finished_at: str) -> float | None:
    if not started_at:
        return None
    try:
        return round((datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds(), 2)
    except ValueError:
        return None


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    backup = path.with_suffix(path.suffix + ".backup")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    # Prove the completed temporary file is valid before replacing the dataset.
    load_json(temporary, None)
    if path.exists():
        shutil.copy2(path, backup)
    os.replace(temporary, path)


def append_run_log(entry: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def load_run_log() -> list[dict[str, Any]]:
    if not RUN_LOG_PATH.exists():
        return []
    entries = []
    for line in RUN_LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def load_config() -> dict[str, Any]:
    config = load_json(CONFIG_PATH, {})
    required = {"search_term", "missing_checks_before_expiring", "page_size"}
    missing = required - config.keys()
    if missing:
        raise ValueError(f"Missing config values: {', '.join(sorted(missing))}")
    return config


def fetch_page(search_term: str, limit: int, page: int, timeout: int) -> dict[str, Any]:
    query = urlencode({"search": search_term, "limit": limit, "page": page})
    request = Request(
        f"{API_URL}?{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "PersonalJobMonitor/0.1 (targeted, low-frequency read-only monitor)",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_all(config: dict[str, Any]) -> list[dict[str, Any]]:
    limit = max(1, min(int(config["page_size"]), 100))
    timeout = int(config.get("request_timeout_seconds", 30))
    combined: dict[str, dict[str, Any]] = {}
    for search_term in config.get("search_terms", [config["search_term"]]):
        first = fetch_page(search_term, limit, 0, timeout)
        results = list(first.get("results", []))
        total = int(first.get("total", len(results)))
        page = 1
        while len(results) < total:
            payload = fetch_page(search_term, limit, page, timeout)
            page_results = payload.get("results", [])
            if not page_results:
                raise RuntimeError(
                    f"Search for {search_term!r} reported {total} jobs but stopped "
                    f"after {len(results)}; refusing to use incomplete results for end detection."
                )
            results.extend(page_results)
            page += 1
        for result in results:
            if result.get("uuid"):
                combined[result["uuid"]] = result
    return list(combined.values())


def title_matches(title: str, term: str, mode: str, stem: str = "") -> bool:
    if mode == "search_results":
        # The search API returns organic matches only. Unlike the visual page,
        # it does not insert the separate "Recommended" promotional cards.
        return True
    normalized_title = " ".join(title.casefold().split())
    normalized_term = " ".join(term.casefold().split())
    if mode == "stem":
        return stem.casefold() in normalized_title
    if mode == "exact":
        return normalized_title == normalized_term
    if mode == "word":
        return re.search(rf"\b{re.escape(normalized_term)}\b", normalized_title) is not None
    return normalized_term in normalized_title


def normalized_name(value: str) -> str:
    return " ".join(value.casefold().split())


def company_name(raw: dict[str, Any]) -> str:
    company = raw.get("hiringCompany") or raw.get("postedCompany") or {}
    return company.get("name") or ""


def matched_criteria(raw: dict[str, Any], config: dict[str, Any]) -> list[str]:
    title = normalized_name(raw.get("title", ""))
    matches = []
    for keyword in config.get("title_keywords", ["Actuary", "actuarial"]):
        if normalized_name(keyword) in title:
            matches.append(f"Title: {keyword}")
    source_company = normalized_name(company_name(raw))
    for watched_company in config.get("watched_companies", []):
        if source_company == normalized_name(watched_company):
            matches.append(f"Company: {watched_company}")
    return matches


def is_watched_job(raw: dict[str, Any], config: dict[str, Any]) -> bool:
    return bool(matched_criteria(raw, config))


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    lines = [" ".join(line.split()) for line in html.unescape(value).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def normalize_job(raw: dict[str, Any], observed_at: str, config: dict[str, Any]) -> dict[str, Any]:
    metadata = raw.get("metadata") or {}
    salary = raw.get("salary") or {}
    salary_type = salary.get("type") or {}
    company = raw.get("hiringCompany") or raw.get("postedCompany") or {}
    uuid = str(raw["uuid"])
    return {
        "source_job_id": uuid,
        "source_job_post_id": metadata.get("jobPostId"),
        "title": raw.get("title") or "Untitled",
        "matched_criteria": matched_criteria(raw, config),
        "company": company.get("name") or "Unknown company",
        "company_uen": company.get("uen"),
        "salary_min": salary.get("minimum"),
        "salary_max": salary.get("maximum"),
        "salary_period": salary_type.get("salaryType"),
        "employment_types": [x.get("employmentType") for x in raw.get("employmentTypes", []) if x.get("employmentType")],
        "position_levels": [x.get("position") for x in raw.get("positionLevels", []) if x.get("position")],
        "categories": [x.get("category") for x in raw.get("categories", []) if x.get("category")],
        "skills": [x.get("skill") for x in raw.get("skills", []) if x.get("skill")],
        "description": html_to_text(raw.get("description")),
        "minimum_years_experience": raw.get("minimumYearsExperience"),
        "vacancies": raw.get("numberOfVacancies"),
        "location": ((raw.get("address") or {}).get("districts") or [{}])[0].get("region"),
        "posted_at": metadata.get("newPostingDate") or metadata.get("originalPostingDate"),
        "expires_at": metadata.get("expiryDate"),
        "url": metadata.get("jobDetailsUrl") or f"https://www.mycareersfuture.gov.sg/job/{uuid}",
        "first_seen_at": observed_at,
        "last_seen_at": observed_at,
        "expired_at": None,
        "reopened_at": None,
        "status": "active",
        "missing_checks": 0,
    }


def update_jobs(
    stored: dict[str, dict[str, Any]],
    fetched: list[dict[str, Any]],
    config: dict[str, Any],
    observed_at: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    matching = {
        raw["uuid"]: normalize_job(raw, observed_at, config)
        for raw in fetched
        if raw.get("uuid") and is_watched_job(raw, config)
    }
    stats = {"fetched": len(fetched), "matching": len(matching), "new": 0, "active": 0, "missing": 0, "expired": 0, "reopened": 0}

    for job_id, current in matching.items():
        if job_id not in stored:
            stored[job_id] = current
            stats["new"] += 1
            continue
        existing = stored[job_id]
        was_expired = existing.get("status") in ("ended", "expired")
        first_seen = existing.get("first_seen_at", observed_at)
        existing.update(current)
        existing["first_seen_at"] = first_seen
        if was_expired:
            existing["reopened_at"] = observed_at
            stats["reopened"] += 1

    threshold = max(1, int(config["missing_checks_before_expiring"]))
    for job_id, job in stored.items():
        if job_id in matching or job.get("status") in ("ended", "expired"):
            continue
        job["missing_checks"] = int(job.get("missing_checks", 0)) + 1
        if job["missing_checks"] >= threshold:
            job["status"] = "expired"
            job["expired_at"] = observed_at
            stats["expired"] += 1
        else:
            job["status"] = "possibly_missing"
            stats["missing"] += 1

    stats["active"] = sum(1 for job in stored.values() if job.get("status") == "active")
    return stored, stats


def fmt_money(value: Any) -> str:
    return "—" if value is None else f"${int(value):,}"


def fmt_date(value: Any) -> str:
    if value in (None, ""):
        return "—"
    return str(value)[:10]


def fmt_datetime(value: Any) -> str:
    if value in (None, ""):
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.strftime("%-d %b %Y, %-I:%M %p Singapore time")
    except ValueError:
        return str(value)


def render_job_detail(job: dict[str, Any]) -> str:
    def text(value: Any) -> str:
        return html.escape(str(value)) if value not in (None, "", []) else "—"

    salary = f"{fmt_money(job.get('salary_min'))}–{fmt_money(job.get('salary_max'))}"
    if job.get("salary_period"):
        salary += f" {job['salary_period']}"
    facts = [
        ("Company", job.get("company")),
        ("Matched criteria", ", ".join(job.get("matched_criteria", []))),
        ("Salary", salary),
        ("Employment", ", ".join(job.get("employment_types", []))),
        ("Level", ", ".join(job.get("position_levels", []))),
        ("Category", ", ".join(job.get("categories", []))),
        ("Location", job.get("location")),
        ("Experience", f"{job['minimum_years_experience']} years minimum" if job.get("minimum_years_experience") is not None else None),
        ("Vacancies", job.get("vacancies")),
        ("Posted", job.get("posted_at")),
        ("Advertised expiry", job.get("expires_at")),
        ("First seen", fmt_date(job.get("first_seen_at"))),
        ("Last seen", fmt_date(job.get("last_seen_at"))),
        ("Confirmed expired", fmt_date(job.get("expired_at") or job.get("ended_at"))),
        ("MCF reference", job.get("source_job_post_id")),
    ]
    facts_html = "".join(f"<dt>{html.escape(label)}</dt><dd>{text(value)}</dd>" for label, value in facts)
    skills = "".join(f"<li>{html.escape(skill)}</li>" for skill in job.get("skills", [])) or "<li>—</li>"
    description = html.escape(job.get("description") or "No description was captured.").replace("\n", "<br>")
    raw_status = "expired" if job.get("status") == "ended" else job.get("status", "unknown")
    status = html.escape(raw_status.replace("_", " ").title())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{text(job.get('title'))} — Archived job</title><style>
body{{margin:0;background:#f5f7fa;color:#17202a;font:15px/1.6 system-ui,-apple-system,sans-serif}}main{{max-width:900px;margin:auto;padding:32px 20px}}a{{color:#155eef}}.back{{display:inline-block;margin-bottom:18px}}.panel{{background:#fff;border:1px solid #dce2e8;border-radius:14px;padding:24px}}h1{{line-height:1.2;margin:6px 0}}.company{{color:#637083;font-size:17px}}.status{{display:inline-block;background:#e8eefc;color:#1849a9;border-radius:999px;padding:3px 9px;font-weight:700}}dl{{display:grid;grid-template-columns:170px 1fr;border-top:1px solid #dce2e8;margin-top:22px}}dt,dd{{margin:0;padding:9px 0;border-bottom:1px solid #edf0f3}}dt{{color:#637083}}h2{{margin-top:28px}}.skills{{columns:2}}.note{{color:#637083;font-size:13px;margin-top:26px}}@media(max-width:600px){{dl{{grid-template-columns:1fr}}dd{{padding-top:0}}.skills{{columns:1}}}}
</style></head><body><main><a class="back" href="../../dashboard.html">← Back to dashboard</a><article class="panel"><span class="status">{status}</span><h1>{text(job.get('title'))}</h1><div class="company">{text(job.get('company'))}</div><p><a href="{html.escape(job.get('url',''))}" target="_blank" rel="noopener">Open current MyCareersFuture page ↗</a></p><dl>{facts_html}</dl><h2>Skills</h2><ul class="skills">{skills}</ul><h2>Captured job description</h2><p>{description}</p><p class="note">This is a local snapshot captured when the posting was available. The source page may later change or disappear.</p></article></main></body></html>"""


def write_job_details(jobs: dict[str, dict[str, Any]]) -> None:
    details_dir = OUTPUT_DIR / "details"
    details_dir.mkdir(parents=True, exist_ok=True)
    for job_id, job in jobs.items():
        (details_dir / f"{job_id}.html").write_text(render_job_detail(job), encoding="utf-8")


def render_dashboard(jobs: dict[str, dict[str, Any]], config: dict[str, Any], run: dict[str, Any], run_history: list[dict[str, Any]] | None = None) -> str:
    run_history = run_history or []
    last_successful_check = next(
        (entry.get("finished_at") for entry in reversed(run_history) if entry.get("status") == "success"),
        run.get("checked_at"),
    )
    ordered = sorted(
        jobs.values(),
        key=lambda job: (job.get("status") in ("ended", "expired"), job.get("posted_at") or ""),
        reverse=True,
    )
    rows = []
    for row_index, job in enumerate(ordered):
        status = "expired" if job.get("status") == "ended" else job.get("status", "unknown")
        salary = f"{fmt_money(job.get('salary_min'))}–{fmt_money(job.get('salary_max'))}"
        if job.get("salary_period"):
            salary += f" {job['salary_period']}"
        salary_mid = ""
        if job.get("salary_min") is not None and job.get("salary_max") is not None:
            salary_mid = (float(job["salary_min"]) + float(job["salary_max"])) / 2
        criteria_html = "".join(
            f"<span class='criteria-chip'>{html.escape(tag)}</span> "
            for tag in job.get("matched_criteria", [])
        ) or "—"
        rows.append(
            "<tr "
            f"data-status='{html.escape(status)}' data-title='{html.escape(job.get('title',''))}' data-company='{html.escape(job.get('company',''))}' "
            f"data-salary-min='{job.get('salary_min') if job.get('salary_min') is not None else ''}' "
            f"data-salary-max='{job.get('salary_max') if job.get('salary_max') is not None else ''}' "
            f"data-salary-mid='{salary_mid}' data-posted='{html.escape(job.get('posted_at') or '')}' "
            f"data-expires='{html.escape(job.get('expires_at') or '')}' data-first-seen='{html.escape(fmt_date(job.get('first_seen_at')))}' "
            f"data-last-seen='{html.escape(fmt_date(job.get('last_seen_at')))}' data-expired='{html.escape(fmt_date(job.get('expired_at') or job.get('ended_at')))}' data-index='{row_index}' "
            f"data-search='{html.escape((job.get('title','') + ' ' + job.get('company','')).casefold())}'>"
            f"<td><span class='badge {html.escape(status)}'>{html.escape(status.replace('_', ' ').title())}</span></td>"
            f"<td><a href='output/details/{html.escape(job.get('source_job_id',''))}.html'>{html.escape(job.get('title',''))}</a></td>"
            f"<td>{html.escape(job.get('company',''))}</td>"
            f"<td>{html.escape(salary)}</td>"
            f"<td>{html.escape(job.get('posted_at') or '—')}</td>"
            f"<td>{html.escape(job.get('expires_at') or '—')}</td>"
            f"<td>{html.escape(fmt_date(job.get('first_seen_at')))}</td>"
            f"<td>{html.escape(fmt_date(job.get('last_seen_at')))}</td>"
            f"<td>{html.escape(fmt_date(job.get('expired_at') or job.get('ended_at')))}</td>"
            f"<td>{criteria_html}</td>"
            f"<td><a href='{html.escape(job.get('url',''))}' target='_blank' rel='noopener'>Live ↗</a></td></tr>"
        )
    companies = sorted({job.get("company", "") for job in jobs.values() if job.get("company")}, key=str.casefold)
    company_options = "".join(f"<option value='{html.escape(company)}'>{html.escape(company)}</option>" for company in companies)
    company_summary = ", ".join(config.get("watched_companies", []))
    match_description = f'Actuarial roles + every job from {html.escape(company_summary)}'
    run_history_rows = []
    for entry in reversed(run_history[-5:]):
        entry_status = entry.get("status", "unknown")
        if entry_status == "success":
            result = (
                f"Ran {entry.get('execution_seconds', '—')}s · Fetched {entry.get('fetched', 0)} · Tracked {entry.get('matching', 0)} · "
                f"New {entry.get('new', 0)} · Missing {entry.get('missing', 0)} · "
                f"Expired {entry.get('expired', 0)}"
            )
        else:
            result = f"Ran {entry.get('execution_seconds', '—')}s · {entry.get('error', 'Unknown error')}"
        run_history_rows.append(
            f"<tr><td>{html.escape(fmt_datetime(entry.get('finished_at')))}</td>"
            f"<td><span class='run-status {html.escape(entry_status)}'>{html.escape(entry_status.title())}</span></td>"
            f"<td>{html.escape(str(result))}</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Tracker — {html.escape(config['search_term'])}</title>
<style>
:root{{--ink:#17202a;--muted:#637083;--line:#dce2e8;--bg:#f5f7fa;--card:#fff;--blue:#155eef}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,sans-serif}}
main{{max-width:1280px;margin:auto;padding:32px 20px}}h1{{margin:0;font-size:28px}}.sub{{color:var(--muted);margin:4px 0 24px}}
.criteria{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:#eef4ff;border:1px solid #b8ccff;border-radius:12px;padding:12px 14px;margin:0 0 18px;color:#173b8f}}.criteria strong{{margin-right:4px}}.criteria span{{background:white;border:1px solid #c9d8ff;border-radius:7px;padding:5px 9px}}.criteria-chip{{display:inline-block;background:#eef4ff;border:1px solid #c9d8ff;color:#173b8f;border-radius:6px;padding:2px 6px;margin:1px 2px;font-size:12px}}
.topbar{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}}.log-toggle{{width:auto;white-space:nowrap;background:white;color:#155eef;border-color:#b8ccff;padding:7px 10px;font-size:13px}}.run-log{{background:white;border:1px solid var(--line);border-radius:12px;padding:18px;margin:0 0 18px}}.run-log h2{{margin:0 0 10px;font-size:17px}}.run-log table{{white-space:normal}}.run-status{{display:inline-block;border-radius:6px;padding:2px 7px;font-weight:700;font-size:12px}}.run-status.success{{background:#dcfce7;color:#166534}}.run-status.failed{{background:#fee2e2;color:#991b1b}}
.cards{{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:12px;margin-bottom:18px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}.card b{{display:block;font-size:26px}}.card span{{color:var(--muted)}}
.controls{{display:grid;grid-template-columns:1.3fr 1.2fr .7fr .9fr .8fr;gap:10px;margin:18px 0}}input,select,button{{min-width:0;width:100%;border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:white;font:inherit}}button{{background:#155eef;color:white;border-color:#155eef;font-weight:700;cursor:pointer}}button:hover{{background:#124dcc}}
.chart{{background:white;border:1px solid var(--line);border-radius:12px;padding:18px;margin:18px 0}}.chart h2{{margin:0 0 2px;font-size:17px}}.chart p{{margin:0 0 10px;color:var(--muted)}}#salaryChart{{display:block;width:100%;height:250px}}.axis{{stroke:#cbd5e1;stroke-width:1}}.trend{{fill:none;stroke:#155eef;stroke-width:3}}.point{{fill:#155eef}}.chart-label{{fill:#637083;font-size:11px}}.no-chart{{color:var(--muted);text-align:center;padding:60px 0}}
.x-axis-label{{text-align:center;color:var(--muted);font-size:12px;margin-top:-18px}}.table-actions{{display:flex;justify-content:flex-end;margin:0 0 10px}}.table-actions button{{width:auto}}
.table-wrap{{overflow:auto;background:white;border:1px solid var(--line);border-radius:12px}}table{{width:100%;border-collapse:collapse;white-space:nowrap}}th,td{{text-align:left;padding:12px;border-bottom:1px solid var(--line)}}th{{background:#f8fafc;color:var(--muted);font-size:12px;text-transform:uppercase}}a{{color:var(--blue);font-weight:600;text-decoration:none}}a:hover{{text-decoration:underline}}
.sort-header{{all:unset;cursor:pointer;font:inherit;color:inherit;text-transform:inherit}}.sort-header:hover{{color:#155eef}}.sort-header[aria-sort="ascending"]::after{{content:" ▲"}}.sort-header[aria-sort="descending"]::after{{content:" ▼"}}
.badge{{display:inline-block;border-radius:999px;padding:3px 8px;font-size:12px;font-weight:700}}.active{{background:#dcfce7;color:#166534}}.possibly_missing{{background:#fef3c7;color:#92400e}}.expired{{background:#e5e7eb;color:#4b5563}}
.empty{{display:none;text-align:center;padding:30px;color:var(--muted)}}footer{{margin-top:16px;color:var(--muted);font-size:12px}}@media(max-width:900px){{.cards{{grid-template-columns:1fr 1fr}}.controls{{grid-template-columns:1fr 1fr}}}}@media(max-width:600px){{.controls{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="topbar"><div><h1>MyCareersFuture Job Tracker</h1><p class="sub">Promoted recommendations excluded · Last successful check: {html.escape(fmt_datetime(last_successful_check))}</p></div><button id="toggleRunLog" class="log-toggle" type="button" aria-expanded="false">Run log</button></div>
<section class="criteria"><strong>Tracking criteria</strong><span>Title keywords: Actuary, actuarial</span><span>Companies: {html.escape(company_summary)}</span></section>
<section id="runLogPanel" class="run-log" hidden><h2>Monitor run log</h2><table><thead><tr><th>Finished</th><th>Status</th><th>High-level results or error</th></tr></thead><tbody>{''.join(run_history_rows) or '<tr><td colspan="3">No runs recorded yet.</td></tr>'}</tbody></table></section>
<section class="cards"><div class="card"><b id="activeCount">0</b><span>Active postings</span></div><div class="card"><b id="age7">0</b><span>Posted ≤7 days ago</span></div><div class="card"><b id="age30">0</b><span>Posted 8–30 days ago</span></div><div class="card"><b id="age31">0</b><span>Posted 31+ days ago</span></div><div class="card"><b id="avgSalary">—</b><span>Average midpoint salary</span></div></section>
<div class="controls"><input id="search" type="search" placeholder="Search title or company"><select id="company"><option value="all">All companies</option>{company_options}</select><input id="minSalary" type="number" min="0" step="500" placeholder="Minimum salary"><select id="sort"><option value="default">Default sorting</option><option value="min-asc">Minimum salary: low to high</option><option value="min-desc">Minimum salary: high to low</option><option value="max-asc">Maximum salary: low to high</option><option value="max-desc">Maximum salary: high to low</option></select><select id="status"><option value="all">All statuses</option><option value="active" selected>Active</option><option value="possibly_missing">Possibly missing</option><option value="expired">Expired postings</option></select></div>
<section class="chart"><h2>Average salary trend</h2><p>Monthly average of (minimum salary + maximum salary) ÷ 2 for visible active postings</p><svg id="salaryChart" viewBox="0 0 760 250" role="img" aria-label="Average midpoint salary by posting month"></svg><div class="x-axis-label">Job posting month</div><div id="noChart" class="no-chart" hidden>Not enough salary data for this filter.</div></section>
<div class="table-actions"><button id="downloadCsv" type="button">Download visible CSV</button></div>
<div class="table-wrap"><table><thead><tr><th><button class="sort-header" data-sort-field="status">Status</button></th><th><button class="sort-header" data-sort-field="title">Job title</button></th><th><button class="sort-header" data-sort-field="company">Company</button></th><th><button class="sort-header" data-sort-field="salaryMin" data-sort-type="number">Salary</button></th><th><button class="sort-header" data-sort-field="posted">Posted</button></th><th><button class="sort-header" data-sort-field="expires">Advertised expiry</button></th><th><button class="sort-header" data-sort-field="firstSeen">First seen</button></th><th><button class="sort-header" data-sort-field="lastSeen">Last seen</button></th><th><button class="sort-header" data-sort-field="expired">Confirmed expired</button></th><th>Matched criteria</th><th>Source</th></tr></thead><tbody id="jobs">{''.join(rows)}</tbody></table><div id="empty" class="empty">No jobs match these filters.</div></div>
<footer>Source: MyCareersFuture. “Expired posting” means absent from {int(config['missing_checks_before_expiring'])} consecutive complete successful searches. Dates use Singapore time.</footer>
<script>
const q=document.querySelector('#search'),company=document.querySelector('#company'),minSalary=document.querySelector('#minSalary'),sort=document.querySelector('#sort'),statusFilter=document.querySelector('#status'),tbody=document.querySelector('#jobs'),rows=[...tbody.querySelectorAll('tr')],empty=document.querySelector('#empty');
let headerSort={{field:null,direction:'asc',type:'text'}};
const money=n=>n?new Intl.NumberFormat('en-SG',{{style:'currency',currency:'SGD',maximumFractionDigits:0}}).format(n):'—';
function drawTrend(activeRows){{const svg=document.querySelector('#salaryChart'),noChart=document.querySelector('#noChart'),groups={{}};for(const row of activeRows){{const month=row.dataset.posted.slice(0,7),mid=Number(row.dataset.salaryMid);if(month&&mid){{(groups[month]??=[]).push(mid)}}}}const data=Object.entries(groups).sort().map(([month,v])=>({{month,value:v.reduce((a,b)=>a+b,0)/v.length}}));svg.innerHTML='';if(!data.length){{svg.hidden=true;noChart.hidden=false;return}}svg.hidden=false;noChart.hidden=true;const W=760,H=250,L=64,R=24,T=24,B=44,max=Math.max(...data.map(d=>d.value))*1.12,min=Math.min(...data.map(d=>d.value))*0.88||0;const x=i=>data.length===1?(L+W-R)/2:L+i*(W-L-R)/(data.length-1),y=v=>T+(max-v)*(H-T-B)/(max-min||1);svg.innerHTML=`<line class="axis" x1="${{L}}" y1="${{H-B}}" x2="${{W-R}}" y2="${{H-B}}"/><line class="axis" x1="${{L}}" y1="${{T}}" x2="${{L}}" y2="${{H-B}}"/>`+data.map((d,i)=>`<text class="chart-label" text-anchor="middle" x="${{x(i)}}" y="${{H-18}}">${{d.month}}</text>`).join('')+`<text class="chart-label" text-anchor="end" x="${{L-8}}" y="${{y(max)+4}}">${{money(max)}}</text><text class="chart-label" text-anchor="end" x="${{L-8}}" y="${{y(min)+4}}">${{money(min)}}</text><polyline class="trend" points="${{data.map((d,i)=>`${{x(i)}},${{y(d.value)}}`).join(' ')}}"/>`+data.map((d,i)=>`<circle class="point" cx="${{x(i)}}" cy="${{y(d.value)}}" r="5"><title>${{d.month}}: ${{money(d.value)}}</title></circle><text class="chart-label" text-anchor="middle" x="${{x(i)}}" y="${{y(d.value)-10}}">${{money(d.value)}}</text>`).join('')}}
function applySort(){{let field,direction,type;if(sort.value!=='default'){{const parts=sort.value.split('-');field=parts[0]==='min'?'salaryMin':'salaryMax';direction=parts[1];type='number'}}else if(headerSort.field){{({{field,direction,type}}=headerSort)}}else{{field='index';direction='asc';type='number'}}const sorted=[...rows].sort((a,b)=>{{let av=a.dataset[field]??'',bv=b.dataset[field]??'';if(type==='number'){{av=Number(av||0);bv=Number(bv||0)}}else{{av=av.toLocaleLowerCase();bv=bv.toLocaleLowerCase()}}const comparison=type==='number'?av-bv:av.localeCompare(bv);return direction==='asc'?comparison:-comparison}});sorted.forEach(row=>tbody.appendChild(row))}}
function filter(){{applySort();let visible=0;const minimum=Number(minSalary.value||0);for(const row of rows){{const rowSalary=Number(row.dataset.salaryMin||0),show=(!q.value||row.dataset.search.includes(q.value.toLowerCase()))&&(company.value==='all'||row.dataset.company===company.value)&&(statusFilter.value==='all'||row.dataset.status===statusFilter.value)&&(!minimum||rowSalary>=minimum);row.hidden=!show;if(show)visible++}}empty.style.display=visible?'none':'block';const activeRows=rows.filter(r=>!r.hidden&&r.dataset.status==='active'),today=new Date();let age7=0,age30=0,age31=0;const mids=[];for(const row of activeRows){{const days=(today-new Date(row.dataset.posted+'T00:00:00'))/86400000;if(days<=7)age7++;else if(days<=30)age30++;else age31++;const mid=Number(row.dataset.salaryMid);if(mid)mids.push(mid)}}document.querySelector('#activeCount').textContent=activeRows.length;document.querySelector('#age7').textContent=age7;document.querySelector('#age30').textContent=age30;document.querySelector('#age31').textContent=age31;document.querySelector('#avgSalary').textContent=mids.length?money(mids.reduce((a,b)=>a+b,0)/mids.length):'—';drawTrend(activeRows)}}
function downloadCsv(){{const visible=[...tbody.querySelectorAll('tr')].filter(row=>!row.hidden),headers=['Status','Job','Company','Salary','Posted','Advertised expiry','First seen','Last seen','Confirmed expired','Matched criteria','Source URL'],quote=value=>'"'+String(value??'').replaceAll('"','""')+'"',lines=[headers.map(quote).join(',')];for(const row of visible){{const cells=[...row.cells],source=cells[10].querySelector('a')?.href||'';lines.push([cells[0].innerText,cells[1].innerText,cells[2].innerText,cells[3].innerText,cells[4].innerText,cells[5].innerText,cells[6].innerText,cells[7].innerText,cells[8].innerText,cells[9].innerText,source].map(quote).join(','))}}const blob=new Blob([lines.join('\\n')],{{type:'text/csv;charset=utf-8'}}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='job-tracker-filtered.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}}
[q,company,minSalary,sort,statusFilter].forEach(el=>el.addEventListener(el.tagName==='INPUT'?'input':'change',filter));for(const button of document.querySelectorAll('.sort-header')){{button.addEventListener('click',()=>{{const same=headerSort.field===button.dataset.sortField;headerSort={{field:button.dataset.sortField,direction:same&&headerSort.direction==='asc'?'desc':'asc',type:button.dataset.sortType||'text'}};sort.value='default';document.querySelectorAll('.sort-header').forEach(b=>b.removeAttribute('aria-sort'));button.setAttribute('aria-sort',headerSort.direction==='asc'?'ascending':'descending');filter()}})}}document.querySelector('#downloadCsv').addEventListener('click',downloadCsv);document.querySelector('#toggleRunLog').addEventListener('click',()=>{{const panel=document.querySelector('#runLogPanel'),button=document.querySelector('#toggleRunLog');panel.hidden=!panel.hidden;button.setAttribute('aria-expanded',String(!panel.hidden))}});filter();
</script></main></body></html>"""


def main() -> int:
    global RUN_STARTED_AT, RUN_MODE
    parser = argparse.ArgumentParser(description="Track targeted MyCareersFuture job postings.")
    parser.add_argument("--render-only", action="store_true", help="Rebuild the dashboard without accessing the website")
    args = parser.parse_args()
    RUN_STARTED_AT = now_iso()
    RUN_MODE = "render-only" if args.render_only else "monitor"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    dataset = load_json(JOBS_PATH, {"jobs": {}, "runs": []})
    checked_at = now_iso()
    if args.render_only:
        run = dataset.get("runs", [{}])[-1] if dataset.get("runs") else {"checked_at": "Never"}
    else:
        fetched = fetch_all(config)
        jobs, stats = update_jobs(dataset.get("jobs", {}), fetched, config, checked_at)
        run = {"checked_at": checked_at, "status": "success", **stats}
        dataset["jobs"] = jobs
        dataset.setdefault("runs", []).append(run)
        dataset["runs"] = dataset["runs"][-100:]
        atomic_write_json(JOBS_PATH, dataset)
    finished_at = now_iso()
    append_run_log({
        "started_at": RUN_STARTED_AT,
        "finished_at": finished_at,
        "execution_seconds": elapsed_seconds(RUN_STARTED_AT, finished_at),
        "status": "success",
        "mode": RUN_MODE,
        **run,
    })
    DASHBOARD_PATH.write_text(render_dashboard(dataset.get("jobs", {}), config, run, load_run_log()), encoding="utf-8")
    write_job_details(dataset.get("jobs", {}))
    print(f"Checked: {run.get('checked_at', 'Never')}")
    if not args.render_only:
        print(f"Fetched: {run['fetched']} | Tracked matches: {run['matching']} | New: {run['new']} | Active: {run['active']} | Possibly missing: {run['missing']} | Expired: {run['expired']}")
    print(f"Dashboard: {DASHBOARD_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        message = f"{now_iso()} ERROR {type(exc).__name__}: {exc}\n"
        with (LOG_DIR / "monitor.log").open("a", encoding="utf-8") as handle:
            handle.write(message)
        finished_at = now_iso()
        append_run_log({
            "started_at": RUN_STARTED_AT or finished_at,
            "finished_at": finished_at,
            "execution_seconds": elapsed_seconds(RUN_STARTED_AT, finished_at),
            "status": "failed",
            "mode": RUN_MODE,
            "error": f"{type(exc).__name__}: {exc}",
        })
        print(message, file=sys.stderr, end="")
        raise SystemExit(1)
