@echo off
REM ============================================================
REM  Halton Cricket - run scrapers from this computer, then
REM  push the updated data to GitHub so the website picks it up.
REM
REM  Run manually once to test, then point Windows Task Scheduler
REM  at this file for a daily automatic run.
REM  See WINDOWS_SETUP.md for full setup instructions.
REM ============================================================

REM --- EDIT THIS: path to your local clone of the GitHub repo ---
set REPO_DIR=C:\Users\YOUR_USERNAME\halton-cricket-data

echo.
echo ===== Halton Cricket data update =====
echo Repo: %REPO_DIR%
echo.

cd /d "%REPO_DIR%"
if errorlevel 1 (
    echo ERROR: Could not find that folder. Edit REPO_DIR at the top of this file.
    pause
    exit /b 1
)

echo Pulling latest changes...
git pull
if errorlevel 1 (
    echo ERROR: git pull failed. Check your internet connection and git setup.
    pause
    exit /b 1
)

echo.
echo Running CricHeroes scraper (Club)...
python scripts\scrape_cricheroes.py
echo (non-zero exit code above is OK - we still commit whatever data we got)

echo.
echo Running CricClubs scraper (Academy)...
python scripts\scrape_cricclubs.py
echo (non-zero exit code above is OK - we still commit whatever data we got)

echo.
echo Committing and pushing results...
git add data\cricheroes-data.json data\cricclubs-data.json
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Update cricket data [from local Windows run]"
    git push
    echo Done - data pushed to GitHub.
) else (
    echo No changes to commit this run.
)

echo.
echo ===== Finished =====
pause
