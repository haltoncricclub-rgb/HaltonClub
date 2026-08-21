#!/usr/bin/env python3
"""
Scrapes CricHeroes TOURNAMENT leaderboard pages (not team pages) to get
genuine per-league stats — e.g. only matches within "2026 HAMMER T15
BLAST", not a team's whole-season combined record.

Each tournament leaderboard includes players from every team in that
competition, not just Halton's — this script filters the results down to
just players on teams whose name contains "HCC" before saving.

Reuses the same login session as scrape_cricheroes_authenticated.py — if
you've already logged in with that script, this one should skip the
manual login step automatically (same saved cookies file).

Run manually:
    python scripts/scrape_cricheroes_tournaments.py
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "tournaments-config.json"
OUTPUT_PATH = ROOT / "data" / "cricheroes-tournaments-data.json"
COOKIES_PATH = ROOT / "data" / ".cricheroes-session-cookies.json"  # shared with the other script, LOCAL ONLY

LOGIN_URL = "https://cricheroes.com/"
PAGE_LOAD_PAUSE_SECONDS = 4


def load_config():
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    return [t for t in cfg["tournaments"] if t.get("id") and t.get("slug")]


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
            c.pop("sameSite", None)
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
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return [ln for ln in text.split("\n") if ln.strip()]


def parse_tournament_batting(lines):
    """
    Player / (Team) / Inn / Runs / Avg / SR / rank / Hs / N/O / 4s / 6s / 100s
    """
    entries = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].startswith("Inn:") and i >= 2 and lines[i - 1].startswith("(") and lines[i - 1].endswith(")"):
            player, team = lines[i - 2], lines[i - 1][1:-1]
            block = lines[i : i + 10]

            def get(prefix):
                for b in block:
                    if b.startswith(prefix):
                        return b.split(":", 1)[1].strip()
                return None

            entries.append({
                "player": player, "team": team,
                "innings": get("Inn:"), "runs": get("Runs:"), "average": get("Avg:"),
                "strike_rate": get("SR:"), "highest_score": get("Hs:"), "not_outs": get("N/O:"),
                "fours": get("4s:"), "sixes": get("6s:"), "hundreds": get("100s:"),
            })
            i += 10
        else:
            i += 1
    return entries


def parse_tournament_bowling(lines):
    """
    Player / (Team) / Inn / W / Eco / Avg / Dots / rank / Maiden / HW / SR
    """
    entries = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].startswith("Inn:") and i >= 2 and lines[i - 1].startswith("(") and lines[i - 1].endswith(")"):
            player, team = lines[i - 2], lines[i - 1][1:-1]
            block = lines[i : i + 9]

            def get(prefix):
                for b in block:
                    if b.startswith(prefix):
                        return b.split(":", 1)[1].strip()
                return None

            entries.append({
                "player": player, "team": team,
                "innings": get("Inn:"), "wickets": get("W:"), "economy": get("Eco:"),
                "average": get("Avg:"), "maidens": get("Maiden:"), "best": get("HW:"),
                "strike_rate": get("SR:"),
            })
            i += 9
        else:
            i += 1
    return entries


def parse_tournament_fielding(lines):
    """
    Player / (Team) / Mat / Dismissal / Catches / R/O / rank / C&B / C.B / St. / Asst.R/O
    """
    entries = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].startswith("Mat:") and i >= 2 and lines[i - 1].startswith("(") and lines[i - 1].endswith(")"):
            player, team = lines[i - 2], lines[i - 1][1:-1]
            block = lines[i : i + 9]

            def get(prefix):
                for b in block:
                    if b.startswith(prefix):
                        return b.split(":", 1)[1].strip()
                return None

            entries.append({
                "player": player, "team": team,
                "matches": get("Mat:"), "dismissals": get("Dismissal:"), "catches": get("Catches:"),
                "run_outs": get("R/O:"), "catch_and_bowled": get("C&B:"), "caught_behind": get("C.B:"),
                "stumpings": get("St.:"), "assisted_run_outs": get("Asst.R/O:"),
            })
            i += 9
        else:
            i += 1
    return entries


def is_hcc_team(team_name: str) -> bool:
    return "hcc" in (team_name or "").lower()


def click_leaderboard_tab(driver, tab_label: str) -> bool:
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


def scrape_tournament(driver, tournament: dict):
    url = f"https://cricheroes.com/tournament/{tournament['id']}/{tournament['slug']}/leaderboard"
    print(f"  visiting {url}")
    driver.get(url)
    time.sleep(PAGE_LOAD_PAUSE_SECONDS)

    result = {"batting": [], "bowling": [], "fielding": []}

    bat_lines = extract_text_blocks(driver.page_source)
    all_batting = parse_tournament_batting(bat_lines)
    result["batting"] = [e for e in all_batting if is_hcc_team(e["team"])]
    if not all_batting:
        result["batting_raw_preview"] = bat_lines[:40]  # parsing found nothing — keep raw text to debug

    if click_leaderboard_tab(driver, "BOWL"):
        time.sleep(2)
        bowl_lines = extract_text_blocks(driver.page_source)
        all_bowling = parse_tournament_bowling(bowl_lines)
        result["bowling"] = [e for e in all_bowling if is_hcc_team(e["team"])]
        if not all_bowling:
            result["bowling_raw_preview"] = bowl_lines[:40]
    else:
        result["bowling_click_failed"] = True

    if click_leaderboard_tab(driver, "FIELD"):
        time.sleep(2)
        field_lines = extract_text_blocks(driver.page_source)
        all_fielding = parse_tournament_fielding(field_lines)
        result["fielding"] = [e for e in all_fielding if is_hcc_team(e["team"])]
        if not all_fielding:
            result["fielding_raw_preview"] = field_lines[:40]
    else:
        result["fielding_click_failed"] = True

    return result


def main():
    tournaments = load_config()
    if not tournaments:
        print("No tournaments configured yet — fill in data/tournaments-config.json first.")
        return

    driver = build_driver()
    try:
        ensure_logged_in(driver)

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "authenticated-manual-tournament",
            "tournaments": {},
        }

        for t in tournaments:
            print(f"\nScraping tournament: {t['name']} ({t['id']})...")
            try:
                data = scrape_tournament(driver, t)
                result["tournaments"][t["id"]] = {"name": t["name"], "ok": True, **data}
            except Exception as e:
                print(f"  failed: {e}")
                result["tournaments"][t["id"]] = {"name": t["name"], "ok": False, "error": str(e)}
            time.sleep(2)

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nWrote {OUTPUT_PATH}")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()