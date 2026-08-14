# Halton Cricket Club — CricHeroes Auto-Sync

This makes your website's Fixtures **and Leaderboards** (top runs, wickets,
fielding across all 10 club teams) update automatically from CricHeroes,
once a day, with no manual copy-pasting.

**Important caveat:** CricHeroes has no official public API. This uses an
unofficial, community-built scraper (the `cricheroes` PyPI package), which
drives a headless browser to read your team pages. It's not sanctioned by
CricHeroes and could break if they change their site — if fixtures stop
updating one day, that's the most likely reason.

Everything below is already built and configured for your 10 club teams
(Arctic Titans, Maple Mavericks, Northern Eagles, Northern Raptors, Northern
Strikers, Northern Knights, Northstar Rangers, Icefield Jets, Northern
Falcons, All Stars). The only thing left is a one-time GitHub account setup —
that part needs to be done by you personally, since it requires your own
email address and identity verification.

## One-time setup (about 10 minutes)

1. **Create a free GitHub account** at github.com — click "Sign up," use
   your club email, verify it. No payment info needed.
2. **Create a new repository**: click the "+" top-right → "New repository."
   Name it something like `halton-cricket-data`. Leave it Public. Click
   "Create repository."
3. **Upload this folder's contents**: on the new repo's page, click "Add
   file" → "Upload files," then drag in everything from this
   `cricheroes-integration` folder — the `.github` folder, `data` folder,
   `scripts` folder, and `requirements.txt` — keeping the same folder
   structure. Commit the upload.
4. **Run the scraper once manually to test it**: go to the "Actions" tab in
   your new repo → click "Update CricHeroes data" on the left → click "Run
   workflow" → "Run workflow" again to confirm. Wait 1–2 minutes, then
   refresh. A green checkmark means it worked; click into the run to see logs
   if it's red.
5. **Check the data landed**: open `data/cricheroes-data.json` in the repo —
   it should now be filled with real match and leaderboard data instead of
   being empty.
6. **Point your website at your repo**: open `index.html`, search for
   `CRICHEROES_DATA_URL`, and replace `YOUR_USERNAME/YOUR_REPO` with your
   actual GitHub username and repository name, e.g.:
   ```
   https://raw.githubusercontent.com/jsmith/halton-cricket-data/main/data/cricheroes-data.json
   ```
7. Re-upload the updated `index.html` to your web host (haltoncricketclub.ca).

From then on, the GitHub Action runs automatically every day and your site's
Fixtures and Leaderboard sections will reflect whatever it finds — no further
action needed from you.

## What you'll see on the site

- **Fixtures** (Club page): recent/upcoming matches pulled from all 10 teams
- **Leaderboards** (Club page): Top Runs, Top Wickets, Top Fielders — merged
  across all 10 teams, ranked automatically

## Adding the Academy later

Once you know which teams (or new teams) belong to the Academy, just edit
`data/teams-config.json` in the GitHub repo (click the file → pencil icon →
change `"group": "club"` to `"group": "academy"` for those teams → commit).
Nothing else needs to change — the site already knows how to route academy
data to the Academy page's Fixtures table.

## If something looks wrong

- **Tables still show sample/placeholder data** → the fetch failed silently
  (intentional, so a broken feed never breaks your site). Check the repo's
  Actions tab for a red ✗ on the latest run, and open its log for the error.
- **A team shows no matches or stats** → check that its `id` and `slug` in
  `teams-config.json` still match its CricHeroes URL exactly.
- **Leaderboard numbers look oddly sorted** → the site parses the first
  number out of each CricHeroes stat string to rank players; if CricHeroes
  changes their stat text format, this may need a small tweak.

