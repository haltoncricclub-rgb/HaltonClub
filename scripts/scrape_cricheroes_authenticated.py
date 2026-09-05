#!/usr/bin/env python3
"""
Scrapes CricHeroes team data using a REAL, manually-authenticated browser
session, instead of the fully-automated anonymous approach that kept
getting stuck on CricHeroes' Cloudflare bot-check.

WHY THIS IS DIFFERENT FROM scrape_cricheroes.py:
That script launched a fresh, anonymous, headless browser every single run.
CricHeroes' Cloudflare protection detects Selenium/ChromeDriver's automation
fingerprint deeply enough that JS-level spoofing (hiding navigator.webdriver
etc.) wasn't enough to get past it — confirmed by identical failures across
GitHub Actions, and two different home-network runs.

This script instead:
  1. Opens a REAL, VISIBLE Chrome window (not headless).
  2. Pauses and asks a human to log into CricHeroes normally in that window
     — including solving any Cloudflare/CAPTCHA challenge by hand. A real
     person doing this, at human speed, is exactly what Cloudflare is
     designed to trust.
  3. Saves the resulting session cookies to a LOCAL file (never committed
     to git — see .gitignore) so future runs can often skip the manual
     login step, as long as the session hasn't expired.
  4. Reuses that authenticated session to visit each team's /matches and
     /leaderboard pages directly (bypassing the old cricheroes package
     entirely, which we now know has a broken/outdated tab-click routine).
  5. Parses whatever the real, rendered page contains using flexible
     text-pattern matching based on what we confirmed the real page shows
     (league name, ground/date/format, team names, status, "Match
     scheduled at ..." line) — since we don't have exact CSS class names,
     this is deliberately resilient rather than exact, and also saves the
     raw text blocks it found so we can tighten the parsing once we see
     real output.

THIS SCRIPT REQUIRES A HUMAN AT THE KEYBOARD. It is NOT meant to run
unattended on a schedule (Task Scheduler) the way the old script was — you
run it manually when you want fresh data, log in when/if asked, and let it
finish.

Run manually:
    pip install -r requirements.txt
    python scripts/scrape_cricheroes_authenticated.py
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "teams-config.json"
OUTPUT_PATH = ROOT / "data" / "cricheroes-data.json"
COOKIES_PATH = ROOT / "data" / ".cricheroes-session-cookies.json"  # LOCAL ONLY — see .gitignore

LOGIN_URL = "https://cricheroes.com/"
MAX_MATCHES_PER_TEAM = 60  # generous safety cap, not a real per-team limit — a full season shouldn't hit this
MAX_LEADERBOARD_ENTRIES = 10
PAGE_LOAD_PAUSE_SECONDS = 4  # let the page finish rendering after navigation

# CricHeroes' Batting/Bowling/Fielding tabs have a season/year filter that
# defaults to something other than the current season (confirmed: it was
# silently including a previous season's stats for at least one team,
# quietly inflating that team's numbers). Update this every year.
CURRENT_SEASON_YEAR = "2026"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    return cfg["teams"]


def build_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    # Deliberately NOT headless — a real visible window is part of why
    # this approach should be treated more like a real browser.
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"},
        )
    except Exception:
        pass
    return driver


def load_saved_cookies(driver):
    if not COOKIES_PATH.exists():
        return False
    try:
        with open(COOKIES_PATH) as f:
            cookies = json.load(f)
        driver.get(LOGIN_URL)
        time.sleep(2)
        for c in cookies:
            c.pop("sameSite", None)  # selenium is picky about this field's values
            try:
                driver.add_cookie(c)
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"Could not load saved session ({e}) — will log in fresh.")
        return False


def save_cookies(driver):
    try:
        COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(COOKIES_PATH, "w") as f:
            json.dump(driver.get_cookies(), f)
        print(f"Session saved to {COOKIES_PATH} (kept local, not committed to git).")
    except Exception as e:
        print(f"Could not save session: {e}")


def check_logged_in(driver) -> bool:
    """
    Heuristic: after loading the homepage, look for something that only
    appears when logged in (vs. a 'Sign in' button for anonymous visitors).
    This is a best-effort check — if it's wrong, the manual login prompt
    just shows up again, which is harmless.
    """
    try:
        page = driver.page_source.lower()
        if "sign in" in page and "sign out" not in page and "log out" not in page:
            return False
        return True
    except Exception:
        return False


def ensure_logged_in(driver):
    used_saved_session = load_saved_cookies(driver)
    if used_saved_session:
        driver.get(LOGIN_URL)
        time.sleep(3)
        if check_logged_in(driver):
            print("Reused saved session — already logged in, no manual step needed.")
            return

    print("\n" + "=" * 60)
    print("MANUAL LOGIN NEEDED")
    print("A Chrome window has opened. Please:")
    print("  1. Log into your CricHeroes account in that window.")
    print("  2. Solve any 'verify you are human' challenge if shown.")
    print("  3. Once you see your CricHeroes account/dashboard, come back")
    print("     here and press Enter.")
    print("=" * 60 + "\n")
    driver.get(LOGIN_URL)
    input("Press Enter once you're logged in... ")
    save_cookies(driver)


def extract_text_blocks(html: str):
    """
    Pull visible text out of the page in a structure-agnostic way, then
    group it into "match card" style chunks using the patterns we
    confirmed from a real screenshot of the Matches tab (league name,
    ground/date/format line, team names, status badge, 'Match scheduled
    at ...' line). This is intentionally resilient rather than exact,
    since we don't have CricHeroes' real CSS/class names to target.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    text = soup.get_text("\n", strip=True)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return lines


def parse_matches_from_lines(lines):
    """
    Parses the real CricHeroes match-card text pattern, confirmed from
    live scraped output across 8 different teams. The pattern is:

        <League Name>
        <Venue>, <City> (<Region>), <DD-Mon-YY>, <NN> Ov.,
        [<Round/Stage name>]              <- optional, e.g. "League Matches",
                                              "Round One", "Semi Final 2";
                                              absent for "Individual Match"
        upcoming | past | live
        <Team A>
        [(<overs>)]                       <- present only once scores exist
        <Team B>
        [(<overs>)]
        ---- then exactly one of ----
        Match scheduled at / <datetime>              (upcoming matches)
        <Winning Team> / won by / <margin>            (completed matches)
        Abandoned ( / <reason> / )                    (abandoned, one style)
        Abandoned / (<reason>)                         (abandoned, other style)

    Deliberately tolerant: if a block doesn't fully match this shape, we
    still return whatever fields we could confidently identify rather than
    dropping the match entirely.
    """
    venue_re = re.compile(r".+,\s*\d{1,2}-[A-Za-z]{3}-\d{2,4},\s*\d+\s*Ov\.?,?\s*$", re.IGNORECASE)
    score_re = re.compile(r"^\(\d+(\.\d+)?\)$")
    status_tokens = {"upcoming", "past", "live"}

    matches = []
    i = 0
    n = len(lines)

    while i < n and len(matches) < MAX_MATCHES_PER_TEAM:
        if venue_re.match(lines[i]):
            league = lines[i - 1] if i > 0 else None
            venue_line = lines[i]
            j = i + 1

            # optional round/stage name line(s) before the status token
            round_name = None
            while j < n and lines[j] not in status_tokens:
                round_name = lines[j]
                j += 1
                if j - i > 4:  # safety valve, shouldn't normally happen
                    break

            if j >= n or lines[j] not in status_tokens:
                i += 1
                continue  # not a match block after all, keep scanning

            status = lines[j]
            j += 1

            team_a = lines[j] if j < n else None
            j += 1
            score_a = None
            if j < n and score_re.match(lines[j]):
                score_a = lines[j]
                j += 1

            team_b = lines[j] if j < n else None
            j += 1
            score_b = None
            if j < n and score_re.match(lines[j]):
                score_b = lines[j]
                j += 1

            scheduled_at = None
            result_text = None

            if j < n and lines[j].lower().startswith("match scheduled at"):
                scheduled_at = lines[j + 1] if j + 1 < n else None
                j += 2
            elif j < n and lines[j].lower().startswith("abandoned"):
                if lines[j].strip() == "Abandoned (" and j + 2 < n:
                    result_text = f"Abandoned ({lines[j+1]})"
                    j += 3
                elif j + 1 < n:
                    result_text = f"Abandoned {lines[j+1]}"
                    j += 2
                else:
                    result_text = "Abandoned"
                    j += 1
            elif j + 1 < n and lines[j + 1].lower() == "won by":
                winner = lines[j]
                margin = lines[j + 2] if j + 2 < n else ""
                result_text = f"{winner} won by {margin}".strip()
                j += 3

            matches.append({
                "league": league,
                "venue_line": venue_line,
                "round": round_name,
                "status": status,
                "team_a": team_a,
                "score_a": score_a,
                "team_b": team_b,
                "score_b": score_b,
                "scheduled_at": scheduled_at,
                "result": result_text,
            })
            i = j
        else:
            i += 1

    return matches


def parse_batting_leaderboard(lines):
    """
    Parses the real batting leaderboard pattern, confirmed from live
    output: each player is a fixed 11-line block —
        <Player Name>
        Inn: N
        Runs: N
        Avg: N.NN
        SR: N.NN
        <rank number, e.g. "01">
        Hs: N
        N/O: N
        4s: N
        6s: N
        100s: N
    """
    entries = []
    i = 0
    n = len(lines)

    while i < n:
        if lines[i].startswith("Inn:") and i > 0:
            name = lines[i - 1]
            block = lines[i : i + 10]

            def get(prefix):
                for b in block:
                    if b.startswith(prefix):
                        return b.split(":", 1)[1].strip()
                return None

            entries.append({
                "player": name,
                "innings": get("Inn:"),
                "runs": get("Runs:"),
                "average": get("Avg:"),
                "strike_rate": get("SR:"),
                "highest_score": get("Hs:"),
                "not_outs": get("N/O:"),
                "fours": get("4s:"),
                "sixes": get("6s:"),
                "hundreds": get("100s:"),
            })
            i += 10
        else:
            i += 1

    return entries[:MAX_LEADERBOARD_ENTRIES]


def parse_bowling_leaderboard(lines):
    """
    Parses the real bowling leaderboard pattern, confirmed from live
    output: each player is a fixed 9-line block after their name —
        <Player Name>
        Inn: N
        W: N
        Eco: N.NN
        Avg: N.NN
        Dots:                  <- no value ever shown for this field
        <rank number>
        Maiden: N
        HW: N
        SR: N.NN
    """
    entries = []
    i = 0
    n = len(lines)

    while i < n:
        if lines[i].startswith("Inn:") and i > 0:
            name = lines[i - 1]
            block = lines[i : i + 9]

            def get(prefix):
                for b in block:
                    if b.startswith(prefix):
                        return b.split(":", 1)[1].strip()
                return None

            entries.append({
                "player": name,
                "innings": get("Inn:"),
                "wickets": get("W:"),
                "economy": get("Eco:"),
                "average": get("Avg:"),
                "maidens": get("Maiden:"),
                "best": get("HW:"),
                "strike_rate": get("SR:"),
            })
            i += 9
        else:
            i += 1

    return entries[:MAX_LEADERBOARD_ENTRIES]


def parse_fielding_leaderboard(lines):
    """
    Parses the real fielding leaderboard pattern, confirmed from live
    output: each player is a fixed 9-line block after their name —
        <Player Name>
        Mat: N
        Dismissal: N
        Catches: N
        R/O: N
        <rank number>
        C&B: N
        C.B: N
        St.: N
        Asst.R/O: N
    """
    entries = []
    i = 0
    n = len(lines)

    while i < n:
        if lines[i].startswith("Mat:") and i > 0:
            name = lines[i - 1]
            block = lines[i : i + 9]

            def get(prefix):
                for b in block:
                    if b.startswith(prefix):
                        return b.split(":", 1)[1].strip()
                return None

            entries.append({
                "player": name,
                "matches": get("Mat:"),
                "dismissals": get("Dismissal:"),
                "catches": get("Catches:"),
                "run_outs": get("R/O:"),
                "catch_and_bowled": get("C&B:"),
                "caught_behind": get("C.B:"),
                "stumpings": get("St.:"),
                "assisted_run_outs": get("Asst.R/O:"),
            })
            i += 9
        else:
            i += 1

    return entries[:MAX_LEADERBOARD_ENTRIES]


def normalize_for_website(team_name, matches, batting, bowling, fielding):
    """
    Converts the detailed parsed data above into the simpler shape the
    website's JavaScript already expects (date/opponent/result rows for
    fixtures, player/team/stat rows for the leaderboard) — so index.html
    doesn't need to change, only the data feeding it gets more accurate.
    """
    website_matches = []
    for m in matches:
        # whichever team isn't "us" is the opponent, best-effort by
        # checking which name contains "HCC" (our club's teams are all
        # named starting with HCC on CricHeroes)
        a, b = m.get("team_a") or "", m.get("team_b") or ""
        if "hcc" in a.lower() and "hcc" not in b.lower():
            opponent = b
        elif "hcc" in b.lower() and "hcc" not in a.lower():
            opponent = a
        else:
            opponent = b or a  # fallback

        date_part = None
        vline = m.get("venue_line") or ""
        date_match = re.search(r"\d{1,2}-[A-Za-z]{3}-\d{2,4}", vline)
        if date_match:
            date_part = date_match.group(0)

        if m.get("status") == "upcoming":
            result_display = f"Scheduled: {m.get('scheduled_at')}" if m.get("scheduled_at") else "Upcoming"
        else:
            result_display = m.get("result") or "Result unavailable"

        website_matches.append({
            "date": date_part,
            "opponent": opponent,
            "result": result_display,
            "status": m.get("status"),
        })

    website_batting = [
        {
            "player": b["player"],
            "team": team_name,
            "stat": f"{b.get('runs','-')} runs (Avg {b.get('average','-')}, SR {b.get('strike_rate','-')})",
        }
        for b in batting
    ]

    website_bowling = [
        {
            "player": b["player"],
            "team": team_name,
            "stat": f"{b.get('wickets','-')} wkts (Econ {b.get('economy','-')}, Avg {b.get('average','-')})",
        }
        for b in bowling
    ]

    website_fielding = [
        {
            "player": f["player"],
            "team": team_name,
            "stat": f"{f.get('catches','-')} catches, {f.get('dismissals','-')} dismissals",
        }
        for f in fielding
    ]

    return website_matches, website_batting, website_bowling, website_fielding


def click_leaderboard_tab(driver, tab_label: str) -> bool:
    """
    Clicks the BAT/BOWL/FIELD tab on the leaderboard page. We don't know
    CricHeroes' real element IDs/classes, so this tries a few reasonably
    robust ways to find something clickable containing the tab's exact
    text, and falls back to a JS click if a normal click is blocked by an
    overlay. Returns True if a click was attempted successfully.
    """
    from selenium.webdriver.common.by import By

    xpaths = [
        f"//*[normalize-space(text())='{tab_label}']",
        f"//*[contains(text(), '{tab_label}')]",
    ]
    for xp in xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xp)
            for el in elements:
                if el.is_displayed():
                    try:
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    return True
        except Exception:
            continue
    return False


def select_current_season(driver, year_text=CURRENT_SEASON_YEAR):
    """
    Sets the year filter on the leaderboard page to the current season.
    Without this, CricHeroes silently shows stats mixed across every year
    the team has ever played (confirmed real-world impact: one team's
    numbers were inflated by a previous season's stats).

    Confirmed real UI flow (via screenshot of the live page):
        1. A filter icon (top-right of the tab bar) opens a "Filter" modal
        2. That modal has its own tabs: Overs / Ball Type / Match Type /
           Year / Tournaments / Tournament Category
        3. The Year tab shows a checkbox per year (2026, 2025, 2024, ...)
        4. Checking a year and clicking "Apply" applies the filter

    This is deliberately staged and VERIFIED at each step — an earlier
    version of this function searched the whole page for anything
    containing "2026" and ended up clicking the wrong element, which
    caused a blank-page failure. This version only proceeds past opening
    the filter if the actual "Filter" modal heading is confirmed on
    screen, and stops cleanly (rather than guessing further) if any step
    doesn't find what it expects.

    Returns (success: bool, options_seen: list[str]) — options_seen is
    logged either way so a run's output shows exactly what years were
    actually on offer, letting us verify (or fix) this on the next run
    rather than silently trusting it worked.
    """
    from selenium.webdriver.common.by import By

    def find_visible(xpath):
        for el in driver.find_elements(By.XPATH, xpath):
            if el.is_displayed():
                return el
        return None

    def click(el):
        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)

    # Step 1: open the filter modal via its icon. Confirmed via inspecting
    # the real page: it's an <img alt="filter icon" ...> — a stable,
    # semantic attribute rather than an auto-generated CSS-module class
    # name, so this should be reliable. Kept as a list (with the icon
    # match first) in case CricHeroes changes this, plus the same
    # verification step regardless — only trust a click if the actual
    # "Filter" modal heading appears afterward.
    candidate_xpaths = [
        "//img[@alt='filter icon']",
        "//*[contains(translate(@class,'FILTER','filter'),'filter')]",
        "//*[contains(translate(@aria-label,'FILTER','filter'),'filter')]",
        "//*[contains(translate(@title,'FILTER','filter'),'filter')]",
    ]
    opened = False
    for xp in candidate_xpaths:
        el = find_visible(xp)
        if el:
            click(el)
            time.sleep(1)
            if find_visible("//*[normalize-space(text())='Filter']"):
                opened = True
                break
    if not opened:
        return False, []

    # Step 2: switch to the Year tab within the modal.
    year_tab = find_visible("//*[normalize-space(text())='YEAR' or normalize-space(text())='Year']")
    if year_tab:
        click(year_tab)
        time.sleep(1)

    # Collect the years actually on offer, for diagnostics either way.
    lines = extract_text_blocks(driver.page_source)
    options_seen = [ln for ln in lines if re.match(r"^(19|20)\d{2}$", ln)]

    # Step 3: check the target year — but only click it if it isn't
    # already checked. This matters because we call this function again
    # after switching tabs (BAT -> BOWL -> FIELD) in case the filter
    # resets between them. If it DOESN'T reset and is still checked from
    # before, a plain click would toggle it back OFF right before we read
    # that tab's data — silently reverting to unfiltered/all-time stats.
    # That's a real, confirmed-plausible explanation for inflated numbers
    # appearing on later tabs (Fielding) after Batting looked correct.
    from selenium.webdriver.common.by import By as _By

    target = find_visible(f"//*[normalize-space(text())='{year_text}']")
    if not target:
        return False, options_seen

    already_checked = False
    try:
        # Look for a real checkbox input near this year's label/row —
        # try a few reasonable DOM relationships since we don't know the
        # exact nesting.
        checkbox_xpaths = [
            f"//*[normalize-space(text())='{year_text}']/preceding-sibling::input[@type='checkbox'][1]",
            f"//*[normalize-space(text())='{year_text}']/following-sibling::input[@type='checkbox'][1]",
            f"//*[normalize-space(text())='{year_text}']/ancestor::*[self::div or self::label][1]//input[@type='checkbox']",
        ]
        for xp in checkbox_xpaths:
            boxes = driver.find_elements(_By.XPATH, xp)
            if boxes:
                already_checked = boxes[0].is_selected()
                break
    except Exception:
        pass

    if not already_checked:
        click(target)
        time.sleep(0.5)
        print(f"    (year filter: '{year_text}' was not yet checked, clicked it)")
    else:
        print(f"    (year filter: '{year_text}' was already checked, skipped re-clicking)")

    # Step 4: click Apply.
    apply_btn = find_visible("//*[normalize-space(text())='Apply']")
    if not apply_btn:
        return False, options_seen
    click(apply_btn)
    time.sleep(2)

    return True, options_seen


def click_load_more_repeatedly(driver, max_clicks=15):
    """
    CricHeroes' Matches tab only renders a handful of matches by default,
    with a "Load More" control to reveal the rest. Without this, we were
    silently missing real matches (confirmed: a team with 12 real matches
    only showed 8). Clicks it repeatedly until it's gone, stops appearing,
    or the safety cap is hit — whichever comes first.
    """
    from selenium.webdriver.common.by import By

    clicks = 0
    while clicks < max_clicks:
        found = False
        for xp in ["//*[normalize-space(text())='Load More']", "//*[normalize-space(text())='LOAD MORE']"]:
            for el in driver.find_elements(By.XPATH, xp):
                if el.is_displayed():
                    try:
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    time.sleep(2)
                    clicks += 1
                    found = True
                    break
            if found:
                break
        if not found:
            break
    return clicks


def scrape_team(driver, team: dict):
    team_id = team["id"]
    slug = team["slug"].strip().replace(" ", "-")
    base_url = f"https://cricheroes.com/team-profile/{team_id}/{slug}"

    result = {
        "matches_detailed": [],
        "matches": [],  # website-ready shape
        "leaderboard_detailed": {"batting": []},
        "leaderboard": {"batting": [], "bowling": [], "fielding": []},  # website-ready shape
    }

    matches_url = f"{base_url}/matches"
    print(f"  visiting {matches_url}")
    driver.get(matches_url)
    time.sleep(PAGE_LOAD_PAUSE_SECONDS)

    matches_season_ok, matches_season_options = select_current_season(driver)
    if matches_season_ok:
        print(f"  applied {CURRENT_SEASON_YEAR} filter on Matches tab (saw options: {matches_season_options})")
    else:
        print(f"  WARNING: could not apply {CURRENT_SEASON_YEAR} filter on Matches tab"
              f" — match list may include other seasons! (saw options: {matches_season_options})")

    load_more_clicks = click_load_more_repeatedly(driver)
    if load_more_clicks:
        print(f"  clicked 'Load More' {load_more_clicks} time(s) to reveal additional matches")

    lines = extract_text_blocks(driver.page_source)
    matches_detailed = parse_matches_from_lines(lines)
    result["matches_detailed"] = matches_detailed
    result["matches_season_filter_applied"] = matches_season_ok

    leaderboard_url = f"{base_url}/leaderboard"
    print(f"  visiting {leaderboard_url}")
    batting, bowling, fielding = [], [], []
    try:
        driver.get(leaderboard_url)
        time.sleep(PAGE_LOAD_PAUSE_SECONDS)

        season_ok, season_options = select_current_season(driver)
        result["season_filter_applied"] = season_ok
        result["season_filter_options_seen"] = season_options
        if season_ok:
            print(f"  applied {CURRENT_SEASON_YEAR} season filter (saw options: {season_options})")
        else:
            print(f"  WARNING: could not find/select a {CURRENT_SEASON_YEAR} season filter"
                  f" — stats may include other seasons! (saw options: {season_options})")

        # BAT tab is the default view — capture it first.
        lb_lines = extract_text_blocks(driver.page_source)
        batting = parse_batting_leaderboard(lb_lines)
        result["leaderboard_detailed"]["batting"] = batting

        if click_leaderboard_tab(driver, "BOWL"):
            time.sleep(2)
            select_current_season(driver)  # re-apply in case switching tabs reset the filter
            bowl_lines = extract_text_blocks(driver.page_source)
            bowling = parse_bowling_leaderboard(bowl_lines)
            result["leaderboard_detailed"]["bowling"] = bowling
        else:
            result["leaderboard_detailed"]["bowling_click_failed"] = True

        if click_leaderboard_tab(driver, "FIELD"):
            time.sleep(2)
            select_current_season(driver)
            field_lines = extract_text_blocks(driver.page_source)
            fielding = parse_fielding_leaderboard(field_lines)
            result["leaderboard_detailed"]["fielding"] = fielding
        else:
            result["leaderboard_detailed"]["fielding_click_failed"] = True

    except Exception as e:

        result["leaderboard_error"] = str(e)

    website_matches, website_batting, website_bowling, website_fielding = normalize_for_website(
        team["name"], matches_detailed, batting, bowling, fielding
    )
    result["matches"] = website_matches
    result["leaderboard"]["batting"] = website_batting
    result["leaderboard"]["bowling"] = website_bowling
    result["leaderboard"]["fielding"] = website_fielding

    return result


def main():
    teams = load_config()
    driver = build_driver()

    # Load whatever was there from the last run — if a team fails this time,
    # we fall back to its last known-good data instead of overwriting good
    # data with an empty/failed result. This is exactly the bug that caused
    # a bad run to wipe out working stats on the live site.
    previous = {}
    if OUTPUT_PATH.exists():
        try:
            with open(OUTPUT_PATH) as f:
                previous = json.load(f).get("teams", {})
        except Exception:
            pass

    try:
        ensure_logged_in(driver)

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "authenticated-manual",
            "teams": {},
        }

        for t in teams:
            print(f"\nScraping {t['name']} ({t['id']})...")
            try:
                data = scrape_team(driver, t)
                is_suspiciously_empty = (
                    len(data.get("matches", [])) == 0
                    and len(data.get("leaderboard", {}).get("batting", [])) == 0
                )

                if is_suspiciously_empty:
                    # This "succeeded" (no exception) but returned nothing —
                    # confirmed real-world cause: a stale/degraded reused
                    # login session serving broken pages for every team,
                    # not a genuine "this team has no data" case. Treat it
                    # the same as a real failure so it can't silently wipe
                    # out good data with zeros.
                    print(f"  WARNING: scrape 'succeeded' but returned 0 matches and 0 batting "
                          f"entries — this usually means the session is stale, not that there's "
                          f"genuinely no data. Treating as a failure for {t['name']}.")
                    raise RuntimeError("suspiciously empty result (0 matches, 0 batting entries) "
                                        "— likely a stale session rather than real data")

                result["teams"][t["id"]] = {
                    "name": t["name"],
                    "group": t.get("group", "unassigned"),
                    "ok": True,
                    **data,
                }
                print(f"  found {len(data['matches'])} match(es), {len(data['leaderboard']['batting'])} batting leaderboard entries")
            except Exception as e:
                print(f"  failed: {e}", file=sys.stderr)
                prev_team = previous.get(t["id"])
                if prev_team and prev_team.get("ok"):
                    print(f"  this run failed — keeping last known-good data for {t['name']} instead of wiping it out.")
                    result["teams"][t["id"]] = {
                        **prev_team,
                        "stale": True,
                        "stale_reason": str(e),
                    }
                else:
                    result["teams"][t["id"]] = {
                        "name": t["name"],
                        "group": t.get("group", "unassigned"),
                        "ok": False,
                        "error": str(e),
                    }
            time.sleep(2)

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nWrote {OUTPUT_PATH}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()