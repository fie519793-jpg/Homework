#!/usr/bin/env python3
"""
Homework Dashboard builder.

Pulls:
  - Canvas: current grades per course, assignments due in the next N days
  - Todoist: tasks due today or overdue

Then writes a self-contained dashboard.html you can open any time.

Run this manually once to test, then schedule it (cron / Task Scheduler)
to run every morning. Each run overwrites dashboard.html with fresh data.
"""

import json
import sys
import datetime
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"
OUTPUT_PATH = HERE / "dashboard.html"


def load_config():
    """Load from config.json if present (local runs), otherwise from
    environment variables (used by GitHub Actions)."""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())

    import os

    canvas_url = os.environ.get("CANVAS_URL")
    canvas_token = os.environ.get("CANVAS_TOKEN")
    todoist_token = os.environ.get("TODOIST_TOKEN")
    days_ahead = int(os.environ.get("DAYS_AHEAD", "3"))

    if not canvas_url or not canvas_token:
        sys.exit(
            "No config.json found and CANVAS_URL/CANVAS_TOKEN environment "
            "variables are not set. Set up config.json for local runs, or "
            "set these as GitHub Actions secrets for cloud runs."
        )

    return {
        "canvas_url": canvas_url,
        "canvas_token": canvas_token,
        "todoist_token": todoist_token,
        "days_ahead": days_ahead,
    }


def http_get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP error calling {url}: {e.code} {e.reason}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"Error calling {url}: {e}", file=sys.stderr)
        return []


def get_canvas_courses(base_url, headers):
    """Active courses with current grade included."""
    url = (
        f"{base_url}/api/v1/courses"
        "?enrollment_state=active&include[]=total_scores&per_page=100"
    )
    courses = http_get_json(url, headers)
    result = []
    for c in courses:
        if not isinstance(c, dict) or "id" not in c:
            continue
        enrollments = c.get("enrollments") or []
        grade = None
        score = None
        for e in enrollments:
            if e.get("type") == "student":
                grade = e.get("computed_current_grade")
                score = e.get("computed_current_score")
                break
        result.append(
            {
                "id": c["id"],
                "name": c.get("name", "Untitled course"),
                "grade": grade,
                "score": score,
            }
        )
    return result


def get_canvas_assignments(base_url, headers, course_id, days_ahead):
    """Assignments due between now and days_ahead from now for one course."""
    now = datetime.datetime.utcnow()
    end = now + datetime.timedelta(days=days_ahead)
    url = (
        f"{base_url}/api/v1/courses/{course_id}/assignments"
        f"?bucket=upcoming&order_by=due_at&per_page=100"
    )
    assignments = http_get_json(url, headers)
    result = []
    for a in assignments:
        if not isinstance(a, dict):
            continue
        due_at = a.get("due_at")
        if not due_at:
            continue
        due_dt = datetime.datetime.strptime(due_at, "%Y-%m-%dT%H:%M:%SZ")
        if due_dt <= end:
            result.append(
                {
                    "name": a.get("name", "Untitled assignment"),
                    "due_at": due_at,
                    "html_url": a.get("html_url", ""),
                    "points_possible": a.get("points_possible"),
                    "has_submitted": (a.get("submission") or {}).get(
                        "workflow_state"
                    )
                    == "submitted",
                }
            )
    return result


def get_canvas_data(config):
    base_url = config["canvas_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {config['canvas_token']}"}
    days_ahead = config.get("days_ahead", 3)

    courses = get_canvas_courses(base_url, headers)
    for course in courses:
        course["assignments"] = get_canvas_assignments(
            base_url, headers, course["id"], days_ahead
        )
    return courses


def get_todoist_tasks(config):
    token = config.get("todoist_token")
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    # "overdue | today" filter query, per Todoist REST API v2 filter syntax
    url = "https://api.todoist.com/rest/v2/tasks?filter=" + urllib.parse_quote(
        "overdue | today"
    )
    tasks = http_get_json(url, headers)
    result = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        result.append(
            {
                "content": t.get("content", ""),
                "due": (t.get("due") or {}).get("date", ""),
                "priority": t.get("priority", 1),
                "url": t.get("url", ""),
            }
        )
    return result


def build_html(courses, todos, generated_at):
    def esc(s):
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    course_blocks = []
    for c in courses:
        grade_str = c["grade"] or (
            f"{c['score']}%" if c["score"] is not None else "—"
        )
        assignment_rows = ""
        if c["assignments"]:
            for a in c["assignments"]:
                due = a["due_at"][:10] if a["due_at"] else ""
                status = "✅ submitted" if a["has_submitted"] else "⬜ not submitted"
                assignment_rows += (
                    f'<li><a href="{esc(a["html_url"])}" target="_blank">'
                    f'{esc(a["name"])}</a> — due {esc(due)} — {status}</li>'
                )
        else:
            assignment_rows = "<li class='muted'>Nothing due soon 🎉</li>"

        course_blocks.append(
            f"""
        <div class="card">
          <div class="card-header">
            <h3>{esc(c['name'])}</h3>
            <span class="grade">{esc(grade_str)}</span>
          </div>
          <ul>{assignment_rows}</ul>
        </div>"""
        )

    todo_rows = ""
    if todos:
        for t in sorted(todos, key=lambda x: -x["priority"]):
            due_suffix = f" — due {esc(t['due'])}" if t["due"] else ""
            todo_rows += (
                f'<li><a href="{esc(t["url"])}" target="_blank">'
                f'{esc(t["content"])}</a>{due_suffix}</li>'
            )
    else:
        todo_rows = "<li class='muted'>No personal to-dos today.</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Homework Dashboard</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#f6f7fb; margin:0; padding:24px; color:#1f2430; }}
  h1 {{ margin-bottom:4px; }}
  .timestamp {{ color:#6b7280; font-size:0.85rem; margin-bottom:24px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; margin-bottom:32px; }}
  .card {{ background:white; border-radius:12px; padding:16px 20px; box-shadow:0 1px 3px rgba(0,0,0,0.08); }}
  .card-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .card-header h3 {{ margin:0; font-size:1.05rem; }}
  .grade {{ font-weight:700; color:#4f46e5; }}
  ul {{ padding-left:18px; margin:0; }}
  li {{ margin-bottom:6px; font-size:0.92rem; }}
  a {{ color:#4f46e5; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .muted {{ color:#9ca3af; list-style:none; margin-left:-18px; }}
  section h2 {{ border-bottom:2px solid #e5e7eb; padding-bottom:6px; }}
</style>
</head>
<body>
  <h1>📚 Homework Dashboard</h1>
  <div class="timestamp">Last updated: {esc(generated_at)}</div>

  <section>
    <h2>Today's To-Dos</h2>
    <div class="card"><ul>{todo_rows}</ul></div>
  </section>

  <section style="margin-top:32px;">
    <h2>Classes, Grades &amp; Upcoming Work</h2>
    <div class="grid">
      {''.join(course_blocks)}
    </div>
  </section>
</body>
</html>"""


def main():
    config = load_config()
    print("Fetching Canvas data...")
    courses = get_canvas_data(config)
    print(f"Found {len(courses)} active courses.")

    print("Fetching Todoist tasks...")
    todos = get_todoist_tasks(config)
    print(f"Found {len(todos)} tasks due today/overdue.")

    generated_at = datetime.datetime.now().strftime("%A, %B %d %Y %I:%M %p")
    html = build_html(courses, todos, generated_at)
    OUTPUT_PATH.write_text(html)
    print(f"Dashboard written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
