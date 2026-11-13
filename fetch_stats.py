#!/usr/bin/env python3
"""
fetch_stats.py

Pulls the last 12 months of contribution data from GitHub's GraphQL API
and writes data/stats.json, which generate_cards.py then reads. Runs
daily from the GitHub Action, authenticated with the built-in GITHUB_TOKEN.

Needs: GH_USERNAME env var, GITHUB_TOKEN env var (both set in the workflow).
"""

import calendar
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

USERNAME = os.environ["GH_USERNAME"]
TOKEN = os.environ["GITHUB_TOKEN"]
OUT_PATH = Path(__file__).parent / "data" / "stats.json"

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks {
          contributionDays { date contributionCount }
        }
      }
    }
  }
}
"""


def fetch_calendar():
    body = json.dumps({"query": QUERY, "variables": {"login": USERNAME}}).encode()
    req = Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req) as resp:
        data = json.load(resp)
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = []
    for week in weeks:
        for d in week["contributionDays"]:
            days.append((d["date"], d["contributionCount"]))
    return days  # oldest -> newest, one entry per day


def compute_stats(days):
    monthly = defaultdict(int)
    active_days = 0
    weekday_counts = defaultdict(int)

    for date_str, count in days:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        month_key = (date.year, date.month)
        monthly[month_key] += count
        if count > 0:
            active_days += 1
            weekday_counts[date.weekday()] += count

    # last 12 calendar months, oldest first
    today = datetime.now(timezone.utc)
    month_keys = []
    y, m = today.year, today.month
    for _ in range(12):
        month_keys.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    month_keys.reverse()

    month_labels = [calendar.month_abbr[m] for (_, m) in month_keys]
    monthly_contributions = [monthly.get(k, 0) for k in month_keys]

    # current streak: consecutive days with count > 0, counting back from today
    streak = 0
    for date_str, count in reversed(days):
        if count > 0:
            streak += 1
        else:
            break

    busiest_weekday = max(weekday_counts, key=weekday_counts.get) if weekday_counts else 0
    busiest_day = calendar.day_name[busiest_weekday]

    daily_grid = [c for _, c in days[-(53 * 7):]]

    return {
        "generated_at": today.isoformat(),
        "month_labels": month_labels,
        "monthly_contributions": monthly_contributions,
        "current_streak": streak,
        "active_days": active_days,
        "busiest_day": busiest_day,
        "daily_grid": daily_grid,
    }


def main():
    days = fetch_calendar()
    stats = compute_stats(days)
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
