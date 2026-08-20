# Running the scrapers from your Windows computer

Why: GitHub Actions runs from well-known cloud IP ranges that CricHeroes (and
possibly CricClubs) blocks. Your home internet connection doesn't have that
problem. This guide sets your computer up to run the same scrapers on a
schedule and push the results to your GitHub repo automatically.

**Trade-off to know upfront:** this only runs when your computer is on,
awake, and connected to the internet. If your PC is off at the scheduled
time, that day's update just won't happen — no harm done, it'll catch up
next time it runs.

---

## 1. Install the tools (one-time)

1. **Python** — go to [python.org/downloads](https://www.python.org/downloads/),
   download the Windows installer, run it. **Important:** on the first
   install screen, check the box **"Add python.exe to PATH"** before
   clicking Install.
2. **Git for Windows** — go to
   [git-scm.com/download/win](https://git-scm.com/download/win), download,
   run the installer. Default options are fine for everything.
3. **Google Chrome** — you almost certainly already have this. If not,
   install it normally.

Restart your computer after installing these (makes sure PATH changes take
effect).

## 2. Get your repo onto your computer

1. Open **Command Prompt** (press Start, type `cmd`, hit Enter)
2. Pick a folder to keep it in, e.g. your Documents folder:
