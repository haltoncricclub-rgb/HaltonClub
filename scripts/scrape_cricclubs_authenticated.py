#!/usr/bin/env python3
"""
Scrapes CricClubs team pages (both Club/adult and Academy/youth) using a
REAL browser session, the same approach that worked reliably for
CricHeroes — rather than the older cloudscraper-based approach, which was
never actually confirmed working against the real site.

CricClubs pages appear to sit behind the same kind of Cloudflare
"Performing security verification" check we saw on CricHeroes. This
script opens a real, visible Chrome window; if that check appears, it
pauses so you can wait for it to clear (usually just a few seconds,
sometimes needs a click) — no CricClubs login/account should be needed
for public team pages, unlike CricHeroes' tournament leaderboards.

This is a first pass: since we don't yet know the exact page structure
CricClubs uses for team info (fixtures, results, player stats), this
script captures RAW visible text so real parsing can be written next —
the same proven approach used for CricHeroes.

Run manually:
    python scripts/scrape_cricclubs_authenticated.py
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "cricclubs-config.json"
OUTPUT_PATH = ROOT / "data" / "cricclubs-data.json"
COOKIES_PATH = ROOT / "data" / ".cricclubs-session-cookies.json"  # LOCAL ONLY — see .gitignore

PAGE_LOAD_PAUSE_SECONDS = 4


def load_config():
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    return cfg["teams"]


def build_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
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

    # Some CricClubs league pages carry heavy/low-quality ad scripts (seen
    # first-hand crashing the browser on the Mississauga league page) —
    # block common ad/tracker domains outright so that content never loads
    # and can't take the browser down with it.
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setBlockedURLs", {
            "urls": [
                "*doubleclick.net*", "*googlesyndication*", "*googletagservices*",
                "*google-analytics*", "*googletagmanager*", "*adnxs.com*",
                "*duel.com*", "*taboola*", "*outbrain*", "*ads.*", "*/ads/*",
                "*adsystem*", "*popads*", "*propellerads*", "*revcontent*",
            ]
        })
    except Exception:
        pass

    return driver


def dismiss_ad_blocker_popup(driver):
    """
    Some CricClubs pages show a 'Looks like your ad blocker is on' modal
    that covers the content. Click through it if present.
    """
    from selenium.webdriver.common.by import By

    for text in ("Continue without supporting us", "DISABLE", "Close"):
        try:
            elements = driver.find_elements(By.XPATH, f"//*[contains(text(), '{text}')]")
            for el in elements:
                if el.is_displayed():
                    try:
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    time.sleep(1)
                    return True
        except Exception:
            continue
    return False


def load_saved_cookies(driver, domain_url):
    if not COOKIES_PATH.exists():
        return False
    try:
        with open(COOKIES_PATH) as f:
            cookies = json.load(f)
        driver.get(domain_url)
        time.sleep(2)
        for c in cookies:
            c.pop("sameSite", None)
            try:
                driver.add_cookie(c)
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"Could not load saved cookies ({e}) — continuing without them.")
        return False


def save_cookies(driver):
    try:
        COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(COOKIES_PATH, "w") as f:
            json.dump(driver.get_cookies(), f)
        print(f"Session cookies saved to {COOKIES_PATH} (kept local, not committed to git).")
    except Exception as e:
        print(f"Could not save cookies: {e}")


def looks_like_challenge_page(html: str) -> bool:
    lowered = html.lower()
    return "performing security verification" in lowered or "just a moment" in lowered


def extract_text_blocks(html: str):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return [ln for ln in text.split("\n") if ln.strip()]


def click_tab(driver, tab_label: str) -> bool:
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


def parse_tournament_summary_widget(lines, header, value_label):
    """
    Parses a CricClubs tournament homepage summary widget, e.g.:
        Batting / Player / Runs / <Name (ABBR)> / <value> / ... / View More
    Only the top ~5 entries are ever shown on this widget (full data lives
    behind "View More", not scraped here).
    """
    import re
    try:
        start = lines.index(header)
    except ValueError:
        return []
    i = start + 1
    if i < len(lines) and lines[i] == "Player":
        i += 1
    if i < len(lines) and lines[i] == value_label:
        i += 1
    entries = []
    while i + 1 < len(lines) and lines[i] != "View More":
        name_raw, value = lines[i], lines[i + 1]
        m = re.match(r"^(.*)\((\w+)\)\s*$", name_raw)
        if m:
            entries.append({"player": m.group(1).strip(), "team_abbr": m.group(2), "value": value})
        i += 2
    return entries


def parse_cricclubs_batting(lines):
    COLS = 18
    try:
        start = lines.index("4's") + 1
    except ValueError:
        return []
    entries, i = [], start
    while i + COLS <= len(lines):
        row = lines[i:i + COLS]
        if not row[0].isdigit():
            break
        entries.append({
            "rank": row[0], "player": row[1], "team": row[2],
            "matches": row[3], "innings": row[4], "not_outs": row[5],
            "runs": row[6], "balls": row[7], "average": row[8], "strike_rate": row[9],
            "highest_score": row[10], "hundreds": row[11], "seventy_fives": row[12],
            "fifties": row[13], "twenty_fives": row[14], "ducks": row[15],
            "sixes": row[16], "fours": row[17],
        })
        i += COLS
    return entries


def parse_cricclubs_bowling(lines):
    COLS = 19
    try:
        start = lines.index("Nb") + 1
    except ValueError:
        return []
    entries, i = [], start
    while i + COLS <= len(lines):
        row = lines[i:i + COLS]
        if not row[0].isdigit():
            break
        entries.append({
            "rank": row[0], "player": row[1], "team": row[2],
            "matches": row[3], "innings": row[4], "overs": row[5], "runs": row[6],
            "wickets": row[7], "best": row[8], "maidens": row[9], "dots": row[10],
            "economy": row[11], "average": row[12], "strike_rate": row[13],
            "hat_tricks": row[14], "four_wkts": row[15], "five_wkts": row[16],
            "wides": row[17], "no_balls": row[18],
        })
        i += COLS
    return entries


def parse_cricclubs_fielding(lines):
    COLS = 9
    try:
        start = lines.index("Total") + 1
    except ValueError:
        return []
    entries, i = [], start
    while i + COLS <= len(lines):
        row = lines[i:i + COLS]
        if not row[0].isdigit():
            break
        entries.append({
            "rank": row[0], "player": row[1], "team": row[2],
            "catches": row[3], "wk_catches": row[4], "direct_run_outs": row[5],
            "indirect_run_outs": row[6], "stumpings": row[7], "total": row[8],
        })
        i += COLS
    return entries


def parse_cricclubs_results(lines):
    """
    Repeating blocks anchored on the match type label (usually "League"):
        <type> / day / "Mon Year" / score1 / overs1 / score2 / overs2 /
        competition / teamA / "v" / teamB / result_text /
        ["Ball By Ball"] / "Scorecard"
    "Ball By Ball" is only present for completed (non-abandoned) matches.

    CricClubs team pages appear to also render a second, differently
    structured copy of similar match text further down the page (seen in
    real output — garbled dates, "CSV"/"Are you sure want to Lock" text
    mixed in, likely a hidden/alternate-layout widget in the DOM). This
    parser scans the whole page defensively, then keeps only rows that
    unambiguously look like real matches — clean "D Mon YYYY" dates and a
    genuine result phrase — and de-duplicates by (date, team_a, team_b).
    """
    import re

    matches = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i] in ("League", "T20", "ODI", "T10") and i + 12 < n:
            match_type = lines[i]
            day, mon_year = lines[i + 1], lines[i + 2]
            score1, overs1 = lines[i + 3], lines[i + 4]
            score2, overs2 = lines[i + 5], lines[i + 6]
            competition = lines[i + 7]
            team_a = lines[i + 8]
            team_b = lines[i + 10]  # lines[i+9] is the literal "v"
            result_text = lines[i + 11]
            j = i + 12
            if j < n and lines[j] == "Ball By Ball":
                j += 1
            if j < n and lines[j] == "Scorecard":
                j += 1
            matches.append({
                "type": match_type, "date": f"{day} {mon_year}",
                "competition": competition,
                "team_a": team_a, "score_a": score1, "overs_a": overs1,
                "team_b": team_b, "score_b": score2, "overs_b": overs2,
                "result": result_text,
            })
            i = j
        else:
            i += 1

    date_re = re.compile(r"^\d{1,2} [A-Za-z]{3,} \d{4}$")

    def looks_real(m):
        if not date_re.match(m["date"]):
            return False
        r = m["result"].lower()
        return any(kw in r for kw in ("won by", "abandoned", "no result", "tied", "drawn"))

    seen, clean = set(), []
    for m in matches:
        if not looks_real(m):
            continue
        key = (m["date"], m["team_a"], m["team_b"])
        if key in seen:
            continue
        seen.add(key)
        clean.append(m)
    return clean


# ---------------------------------------------------------------------
# A NEWER CricClubs template (confirmed on the Canada Unity Cup site as
# of Sep 2026) uses completely different tables from the older
# viewTeam.do-style pages the parsers above were built for:
#   - Batting/Bowling/Fielding tables have a different column set/order
#     (e.g. a "Points" column, no 25s/75s buckets) and the header row is
#     rendered twice before the real data.
#   - Bowling/Fielding rows sometimes carry an extra short (2-6 letter,
#     ALL CAPS) initials/abbreviation chip immediately before the player
#     name — inconsistently, only on some rows — which breaks any parser
#     that assumes a fixed column count. Confirmed real example: row
#     "1,AB,Atharav Bansal,Halton Cricket Academy,..." (chip present) vs
#     "2,Yuvraj Sharma,Halton Cricket Academy,..." (no chip) on the same
#     table. These parsers detect and skip that chip explicitly rather
#     than assuming a fixed offset.
#   - Match/results cards are also rendered twice per match (a compact
#     summary card, then a fuller "detailed" card containing a literal
#     "vs" between the two team names) — anchoring on that literal "vs"
#     avoids needing to tell the two copies apart.
# These are tried as a FALLBACK only when the older parser above finds
# nothing, so the older/working team pages are unaffected.
# ---------------------------------------------------------------------

_NEW_ABBR_RE = re.compile(r"^[A-Z]{2,6}$")


def _find_last_header_end(lines, header):
    """Returns the index right after the LAST occurrence of `header` (a
    list of consecutive expected tokens) in `lines`, or None if not found.
    The new template renders the header row twice before the real data,
    so we want the end of the second copy."""
    n, h = len(lines), len(header)
    last_end = None
    for i in range(n - h + 1):
        if lines[i:i + h] == header:
            last_end = i + h
    return last_end


def _parse_new_style_stat_table(lines, header, stat_field_names):
    """Generic parser for the new template's Batting/Bowling/Fielding
    tables: <rank> [ABBR]? <player> <team> <stat1> <stat2> ... repeated.
    `stat_field_names` gives the names for exactly the stat columns that
    follow rank/player/team, in the same left-to-right order as `header`
    (i.e. header[3:])."""
    import re as _re

    start = _find_last_header_end(lines, header)
    if start is None:
        return []
    n_stats = len(stat_field_names)
    entries = []
    i, n = start, len(lines)
    while i < n and lines[i].isdigit():
        rank = lines[i]
        i += 1
        if i < n and _NEW_ABBR_RE.match(lines[i]):
            i += 1  # skip the optional initials/abbreviation chip
        if i >= n:
            break
        player = lines[i]
        i += 1
        if i >= n:
            break
        team = lines[i]
        i += 1
        if i + n_stats > n:
            break
        stats = lines[i:i + n_stats]
        i += n_stats
        entry = {"rank": rank, "player": player, "team": team}
        entry.update(zip(stat_field_names, stats))
        entries.append(entry)
    return entries


def parse_new_style_batting(lines):
    header = ["#", "Player", "Team", "Mat", "Inns", "NO", "Runs", "Balls",
              "4s", "6s", "50s", "100s", "HS", "SR", "Avg", "Points"]
    fields = ["matches", "innings", "not_outs", "runs", "balls",
              "fours", "sixes", "fifties", "hundreds",
              "highest_score", "strike_rate", "average", "points"]
    return _parse_new_style_stat_table(lines, header, fields)


def parse_new_style_bowling(lines):
    header = ["#", "Player", "Team", "Mat", "Inns", "Overs", "Mdns", "Runs",
              "Wkts", "BBF", "Hat", "Econ", "Avg", "SR", "5W", "4W",
              "Dot Balls", "Wides", "No Balls", "Points"]
    fields = ["matches", "innings", "overs", "maidens", "runs", "wickets",
              "best", "hat_tricks", "economy", "average", "strike_rate",
              "five_wkts", "four_wkts", "dots", "wides", "no_balls", "points"]
    return _parse_new_style_stat_table(lines, header, fields)


def parse_new_style_fielding(lines):
    header = ["#", "Player", "Team", "Mat", "Catches", "WK Catches",
              "Direct RO", "Indirect RO", "Stumpings", "Total", "Points"]
    fields = ["matches", "catches", "wk_catches", "direct_run_outs",
              "indirect_run_outs", "stumpings", "total", "points"]
    return _parse_new_style_stat_table(lines, header, fields)


_NEW_DATE_RE = re.compile(
    r"^[A-Za-z]+, [A-Za-z]+ \d{1,2}, \d{4} \d{1,2}:\d{2} (AM|PM)$"
)
_NEW_SCORE_RE = re.compile(r"^\d+/\d+$")
_NEW_OVERS_RE = re.compile(r"^\(\d+(\.\d+)?/\d+\)$")
_NEW_RESULT_RE = re.compile(
    r"won by|abandoned|no result|tied|drawn", re.IGNORECASE
)


def parse_new_style_results(lines):
    """
    Anchored on the literal "vs" that appears exactly once per match, in
    the fuller "detailed" card (the earlier compact summary card for the
    same match has no "vs"). Confirmed real layout around it:
        <competition> <venue> [ABBR]? <team_a> <score_a> <overs_a>
        [ABBR]? "vs" [ABBR]? <team_b> <score_b> <overs_b>
        <venue (repeated)> <result text> "Scorecard" ...
    The optional ABBR chip can appear on either side of "vs" and isn't
    always present (confirmed: present for one team in one match, absent
    in another) — walking outward from "vs" and skipping it when seen
    avoids needing a fixed offset.
    """
    n = len(lines)
    matches = []
    for i, ln in enumerate(lines):
        if ln != "vs":
            continue

        # walk backward: [ABBR]? overs_a score_a team_a
        j = i - 1
        if j >= 0 and _NEW_ABBR_RE.match(lines[j]):
            j -= 1
        overs_a = lines[j] if j >= 0 and _NEW_OVERS_RE.match(lines[j]) else None
        if overs_a is not None:
            j -= 1
        score_a = lines[j] if j >= 0 and _NEW_SCORE_RE.match(lines[j]) else None
        if score_a is not None:
            j -= 1
        team_a = lines[j] if j >= 0 else None
        venue = lines[j - 1] if j - 1 >= 0 else None

        # walk forward: [ABBR]? team_b score_b overs_b
        k = i + 1
        if k < n and _NEW_ABBR_RE.match(lines[k]):
            k += 1
        team_b = lines[k] if k < n else None
        k += 1
        score_b = lines[k] if k < n and _NEW_SCORE_RE.match(lines[k]) else None
        if score_b is not None:
            k += 1
        overs_b = lines[k] if k < n and _NEW_OVERS_RE.match(lines[k]) else None
        if overs_b is not None:
            k += 1

        result = None
        for r in range(k, min(k + 6, n)):
            if _NEW_RESULT_RE.search(lines[r]):
                result = lines[r]
                break

        date = None
        for d in range(i, max(i - 25, -1), -1):
            if _NEW_DATE_RE.match(lines[d]):
                date = lines[d]
                break

        if team_a and team_b and date:
            matches.append({
                "date": date, "venue": venue,
                "team_a": team_a, "score_a": score_a, "overs_a": overs_a,
                "team_b": team_b, "score_b": score_b, "overs_b": overs_b,
                "result": result,
            })

    seen, deduped = set(), []
    for m in matches:
        key = (m["date"], m["team_a"], m["team_b"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(m)
    return deduped


def is_tournament_summary_page(lines) -> bool:
    return "Points Table" in lines and "Batting" in lines


def scrape_tournament_summary_page(lines, our_team_abbr: str):
    batting = parse_tournament_summary_widget(lines, "Batting", "Runs")
    bowling = parse_tournament_summary_widget(lines, "Bowling", "Wkts")
    ranking = parse_tournament_summary_widget(lines, "Ranking", "Points")

    keep = lambda entries: [e for e in entries if e["team_abbr"].upper() == our_team_abbr.upper()]
    return {
        "page_type": "tournament_summary",
        "batting": keep(batting),
        "bowling": keep(bowling),
        "ranking": keep(ranking),
    }


def scrape_team(driver, team: dict):
    url = team["url"]
    print(f"  visiting {url}")
    driver.get(url)
    time.sleep(PAGE_LOAD_PAUSE_SECONDS)

    if looks_like_challenge_page(driver.page_source):
        print("\n" + "=" * 60)
        print("SECURITY CHECK DETECTED on this page.")
        print("Please wait for it to clear in the Chrome window (usually a")
        print("few seconds), solving any 'verify you are human' step if shown.")
        print("=" * 60)
        input("Press Enter once the real page has loaded... ")
        save_cookies(driver)
        time.sleep(2)

    if dismiss_ad_blocker_popup(driver):
        print("  dismissed an ad-blocker popup on this page")

    lines = extract_text_blocks(driver.page_source)

    if is_tournament_summary_page(lines):
        # Tournament-homepage-style page (e.g. Canada Unity Cup) — has real,
        # parseable summary widgets, but no per-team tabs to click.
        our_abbr = team.get("abbr")
        if not our_abbr:
            return {"page_type": "tournament_summary", "raw_lines_preview": lines[:150],
                     "note": "Add an 'abbr' field (e.g. 'HCA') to this team's config entry to filter results."}
        return scrape_tournament_summary_page(lines, our_abbr)

    # Otherwise: a team roster page with its own tabs (Results/Batting/Bowling/Fielding).
    result = {"page_type": "team_page", "matches": [], "batting": [], "bowling": [], "fielding": []}

    if click_tab(driver, "Results"):
        time.sleep(2)
        rlines = extract_text_blocks(driver.page_source)
        result["matches"] = parse_cricclubs_results(rlines)
        if not result["matches"]:
            result["matches"] = parse_new_style_results(rlines)
            result["matches_parser"] = "new_style" if result["matches"] else None
        if not result["matches"]:
            # Neither parser found anything — a layout neither one knows
            # about. Save what's actually there instead of guessing blind.
            result["matches_raw_preview"] = rlines[:220]
    else:
        result["results_click_failed"] = True

    if click_tab(driver, "Batting"):
        time.sleep(2)
        blines = extract_text_blocks(driver.page_source)
        result["batting"] = parse_cricclubs_batting(blines)
        if not result["batting"]:
            result["batting"] = parse_new_style_batting(blines)
        if not result["batting"]:
            result["batting_raw_preview"] = blines[:220]
    else:
        result["batting_click_failed"] = True

    if click_tab(driver, "Bowling"):
        time.sleep(2)
        bowl_lines = extract_text_blocks(driver.page_source)
        result["bowling"] = parse_cricclubs_bowling(bowl_lines)
        if not result["bowling"]:
            result["bowling"] = parse_new_style_bowling(bowl_lines)
        if not result["bowling"]:
            result["bowling_raw_preview"] = bowl_lines[:220]
    else:
        result["bowling_click_failed"] = True

    if click_tab(driver, "Fielding"):
        time.sleep(2)
        field_lines = extract_text_blocks(driver.page_source)
        result["fielding"] = parse_cricclubs_fielding(field_lines)
        if not result["fielding"]:
            result["fielding"] = parse_new_style_fielding(field_lines)
        if not result["fielding"]:
            result["fielding_raw_preview"] = field_lines[:220]
    else:
        result["fielding_click_failed"] = True

    # If every single tab click failed, this page's layout doesn't match
    # what we're looking for at all (different CricClubs skin for this
    # tournament host, a "no matches yet" state that hides the tabs
    # entirely, a popup blocking clicks, etc). Rather than guessing, save
    # what the page actually contains so it can be inspected without
    # needing to be at the keyboard when it happens again.
    if (
        result.get("results_click_failed")
        and result.get("batting_click_failed")
        and result.get("bowling_click_failed")
        and result.get("fielding_click_failed")
    ):
        result["all_tabs_click_failed"] = True
        result["raw_lines_preview"] = extract_text_blocks(driver.page_source)[:220]

    return result


def main():
    teams = load_config()
    driver = build_driver()

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "authenticated-browser-raw-capture",
        "teams": {},
    }

    for t in teams:
        print(f"\nScraping {t['name']} ({t['key']})...")
        last_error = None

        for attempt in (1, 2):  # second attempt gets a fresh browser if the first one died
            try:
                data = scrape_team(driver, t)
                result["teams"][t["key"]] = {
                    "name": t["name"], "group": t.get("group", "unassigned"),
                    "url": t["url"], "ok": True, **data,
                }
                print(f"  captured data (page type: {data.get('page_type', 'unknown')})")
                last_error = None
                break
            except Exception as e:
                last_error = e
                print(f"  attempt {attempt} failed: {e}")
                if attempt == 1:
                    print("  browser session may have crashed — restarting it and retrying this team...")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = build_driver()
                    time.sleep(2)

        if last_error is not None:
            result["teams"][t["key"]] = {
                "name": t["name"], "group": t.get("group", "unassigned"),
                "url": t["url"], "ok": False, "error": str(last_error),
            }

        time.sleep(2)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nWrote {OUTPUT_PATH}")

    try:
        driver.quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()