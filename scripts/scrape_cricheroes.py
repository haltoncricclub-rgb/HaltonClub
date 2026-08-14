#!/usr/bin/env python3
"""
Scrapes CricHeroes team data (matches, results, top performers) for every
team listed in data/teams-config.json and writes the result to
data/cricheroes-data.json.

This uses the unofficial `cricheroes` PyPI package, which drives a headless
Chrome browser via Selenium. It is NOT an official CricHeroes integration —
CricHeroes has no public API. If CricHeroes changes their site layout, this
script may start failing and will need updating.

Run manually:
    pip install -r requirements.txt
    python scripts/scrape_cricheroes.py

In CI, this is run on a schedule by .github/workflows/update-cricheroes-data.yml
"""

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "teams-config.json"
OUTPUT_PATH = ROOT / "data" / "cricheroes-data.json"

MAX_FIXTURES_PER_TEAM = 8
MAX_LEADERBOARD_ENTRIES = 10
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 5
DELAY_BETWEEN_TEAMS_SECONDS = 3


def _patch_chrome_for_ci():
    """
    The `cricheroes` package creates its own Chrome session internally with
    no way for us to pass options in. GitHub Actions runners need
    --no-sandbox and --disable-dev-shm-usage or the Chrome renderer process
    can crash on startup (SessionNotCreatedException). We also add an
    implicit wait so a slow-loading tab doesn't immediately raise
    NoSuchElementException before the page has finished rendering.

    IMPORTANT: this patches the __init__ method directly on the
    selenium.webdriver.chrome.webdriver.WebDriver class object itself,
    rather than reassigning selenium.webdriver.Chrome. Reassigning the
    module attribute only affects code that looks up Chrome fresh off the
    module afterwards — it does NOT affect code (like the cricheroes
    package) that already imported/bound a direct reference to the class.
    Patching the class's own __init__ method affects every reference to
    that exact class object, regardless of how it was imported.
    """
    from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver

    if getattr(ChromeWebDriver, "_halton_patched", False):
        return  # only patch once per process

    _original_init = ChromeWebDriver.__init__

    def _patched_init(self, *args, **kwargs):
        options = kwargs.get("options")
        if options is not None:
            for arg in (
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--headless=new",
                "--window-size=1920,1080",
            ):
                try:
                    options.add_argument(arg)
                except Exception:
                    pass
        _original_init(self, *args, **kwargs)
        try:
            self.implicitly_wait(15)  # let slow-loading tabs appear before failing
        except Exception:
            pass

    ChromeWebDriver.__init__ = _patched_init
    ChromeWebDriver._halton_patched = True


def load_config():
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    return cfg["teams"]


def scrape_team(team_id: str, slug: str):
    """
    Returns a dict with matches, basic team stats, and leaderboard data
    (batting/bowling/fielding) for one CricHeroes team.
    Raises on failure so the caller can retry / record an error.
    """
    from cricheroes import Team  # imported lazily so config-only runs don't need selenium

    team_url = f"{team_id}/{slug}"
    team = Team(url=team_url)

    matches_raw = team.get_matches()
    matches = []
    for m in matches_raw[:MAX_FIXTURES_PER_TEAM]:
        matches.append({
            "opponent": getattr(m, "opponent", None),
            "date": getattr(m, "date", None),
            "venue": getattr(m, "venue", None),
            "result": getattr(m, "result", None),
            "status": getattr(m, "status", None),  # e.g. upcoming / live / completed
        })

    stats = []
    try:
        for s in team.get_team_stats():
            stats.append({"label": s.label, "value": s.value})
    except Exception:
        pass  # stats tab can be sparse for newer teams; don't fail the whole scrape

    leaderboard = {"batting": [], "bowling": [], "fielding": []}
    try:
        lb = team.get_leaderboard()
        for category in ("batting", "bowling", "fielding"):
            for item in lb.get(category, [])[:MAX_LEADERBOARD_ENTRIES]:
                leaderboard[category].append({
                    "player": getattr(item, "player_name", None),
                    "stat": getattr(item, "stat", None),
                })
    except Exception:
        pass  # leaderboard can be empty for teams with few scored matches

    return {
        "matches": matches,
        "stats": stats,
        "leaderboard": leaderboard,
    }


def main():
    _patch_chrome_for_ci()

    teams = load_config()
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "teams": {},
    }

    any_success = False

    for t in teams:
        team_id = t["id"]
        slug = t["slug"]
        name = t["name"]
        group = t.get("group", "unassigned")

        print(f"Scraping {name} ({team_id})...", flush=True)

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                data = scrape_team(team_id, slug)
                result["teams"][team_id] = {
                    "name": name,
                    "group": group,
                    "ok": True,
                    **data,
                }
                any_success = True
                break
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                print(f"  attempt {attempt} failed: {last_error}", file=sys.stderr)
                traceback.print_exc()
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS)
        else:
            # all attempts failed — keep last known good data if present, else mark error
            existing = {}
            if OUTPUT_PATH.exists():
                try:
                    with open(OUTPUT_PATH) as f:
                        prev = json.load(f)
                    existing = prev.get("teams", {}).get(team_id, {})
                except Exception:
                    pass
            result["teams"][team_id] = {
                "name": name,
                "group": group,
                "ok": False,
                "error": last_error,
                "matches": existing.get("matches", []),
                "stats": existing.get("stats", []),
                "leaderboard": existing.get("leaderboard", {"batting": [], "bowling": [], "fielding": []}),
            }

        time.sleep(DELAY_BETWEEN_TEAMS_SECONDS)  # ease resource pressure on the CI runner

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nWrote {OUTPUT_PATH}")
    if not any_success:
        print("WARNING: every team failed to scrape this run.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
