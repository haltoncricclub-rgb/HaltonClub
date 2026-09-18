  // Note: navigation between the landing page, /club/ and /academy/ is now
  // handled by real <a href> links in each page's HTML (see index.html,
  // club/index.html, academy/index.html) — no JS view-toggling needed here.

  document.querySelectorAll('.burger').forEach(btn => {
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.getAttribute('data-burger'));
      target.classList.toggle('open');
    });
  });
  document.querySelectorAll('nav.links a, nav.links button.nav-cta-link').forEach(a => {
    a.addEventListener('click', () => a.closest('nav.links').classList.remove('open'));
  });

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if(entry.isIntersecting){
        entry.target.classList.add('in');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.15 });
  document.querySelectorAll('.reveal').forEach(el => observer.observe(el));

  // ================= CricHeroes live data =================
  // Points at the JSON file published by the GitHub Action scraper.
  const CRICHEROES_DATA_URL = 'https://raw.githubusercontent.com/haltoncricclub-rgb/HaltonClub/main/data/cricheroes-data.json';
  const CRICHEROES_TOURNAMENTS_DATA_URL = 'https://raw.githubusercontent.com/haltoncricclub-rgb/HaltonClub/main/data/cricheroes-tournaments-data.json';
  const CRICCLUBS_DATA_URL = 'https://raw.githubusercontent.com/haltoncricclub-rgb/HaltonClub/main/data/cricclubs-data.json';
  const PLAYER_PHOTOS_URL = 'https://raw.githubusercontent.com/haltoncricclub-rgb/HaltonClub/main/data/player-photos.json';
  const PLAYER_OVERRIDES_URL = 'https://raw.githubusercontent.com/haltoncricclub-rgb/HaltonClub/main/data/player-overrides.json';
  const PLAYER_PHOTOS_BASE = 'https://raw.githubusercontent.com/haltoncricclub-rgb/HaltonClub/main/data/photos/';

  let playerPhotos = {}; // normalized name -> filename, populated on load

  // Populated once when data loads — used by the player-profile modal so a
  // player can be found regardless of which league filter is currently active.
  const playerSearchIndex = {
    club: { batting: [], bowling: [], fielding: [] },
    academy: { batting: [], bowling: [], fielding: [] },
  };

  function escapeHtml(str){
    const d = document.createElement('div');
    d.textContent = str == null ? '' : String(str);
    return d.innerHTML;
  }

  function renderFixtureRows(tbodyId, matches){
    const tbody = document.getElementById(tbodyId);
    if (!tbody || !matches || matches.length === 0) return false;
    tbody.innerHTML = matches.map(m => `
      <tr>
        <td class="day">${escapeHtml(m.date || 'TBD')}</td>
        <td>${escapeHtml(m.opponent || 'Fixture TBD')}</td>
        <td>${escapeHtml(m.team || '')}</td>
        <td>${escapeHtml(m.result || m.status || 'Upcoming')}</td>
      </tr>
    `).join('');
    return true;
  }

  function leadingNumber(str){
    if (str == null) return -Infinity;
    const match = String(str).match(/-?\d+(\.\d+)?/);
    return match ? parseFloat(match[0]) : -Infinity;
  }

  function renderLeaderboardRows(tbodyId, entries, showTeam, realm){
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return false;
    if (!entries || entries.length === 0){
      tbody.innerHTML = `<tr><td colspan="${showTeam ? 3 : 2}" class="lb-empty">No data yet</td></tr>`;
      return false;
    }
    tbody.innerHTML = entries.map(e => `
      <tr>
        <td><button class="player-link" data-player="${escapeHtml(e.player || '')}" data-group-key="${escapeHtml(e.groupKey || '')}" data-realm="${realm || ''}">${playerAvatarHtml(e.player, 'small')}${escapeHtml(e.player || 'Unknown')}</button></td>
        ${showTeam ? `<td>${escapeHtml(e.team || '')}</td>` : ''}
        <td>${escapeHtml(e.stat || '')}</td>
      </tr>
    `).join('');
    return true;
  }

  function setNote(noteId, text){
    const note = document.getElementById(noteId);
    if (note) note.textContent = text;
  }

  function normTeamName(s){
    return (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  function isSameTeam(a, b){
    const na = normTeamName(a), nb = normTeamName(b);
    return !!na && !!nb && na === nb;
  }

  function getTeamLeagues(team){
    const set = new Set();
    (team.matches_detailed || []).forEach(m => { if (m.league) set.add(m.league); });
    return set;
  }

  function computeSeasonRecord(clubTeams, leagueFilter){
    let played = 0, won = 0, lost = 0, abandoned = 0;
    clubTeams.forEach(team => {
      (team.matches_detailed || []).forEach(m => {
        if (leagueFilter !== 'all' && m.league !== leagueFilter) return;
        if (m.status !== 'past') return;
        played++;
        const resultLower = (m.result || '').toLowerCase();
        if (resultLower.includes('abandoned')) { abandoned++; return; }
        if (!m.result) return;
        const winnerName = m.result.split(' won by')[0].trim();
        if (isSameTeam(winnerName, team.name)) won++;
        else lost++;
      });
    });
    return { played, won, lost, abandoned };
  }

  function renderSeasonRecord(rec){
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('record-played', rec.played);
    set('record-won', rec.won);
    set('record-lost', rec.lost);
    set('record-abandoned', rec.abandoned);
  }

  function toNum(v){
    const n = parseFloat(v);
    return isNaN(n) ? 0 : n;
  }

  // ---------- Same-name player disambiguation ----------
  // Two real people can share an identical name (e.g. two different
  // "Jaspreet Singh"s across different teams) — without this, the site
  // would wrongly merge their stats into one person. This lets an admin
  // flag known collisions in data/player-overrides.json so specific
  // (name, team) combinations are kept as a separate identity instead of
  // being folded into the default group for that name.
  let playerOverrideSplits = []; // [{ name, team }, ...]

  async function loadPlayerOverrides(){
    try {
      const res = await fetch(PLAYER_OVERRIDES_URL, { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      playerOverrideSplits = Array.isArray(data.splits) ? data.splits : [];
    } catch (err) {
      // no overrides file yet — that's fine, nobody's flagged a collision
    }
  }

  function getPlayerGroupKey(name, team){
    const nameKey = normTeamName(name);
    const teamKey = normTeamName(team);
    const isSplit = playerOverrideSplits.some(o =>
      normTeamName(o.name) === nameKey && normTeamName(o.team) === teamKey
    );
    return isSplit ? `${nameKey}::${teamKey}` : nameKey;
  }

  function getPlayerDisplayName(name, team, groupKey){
    // Split-out identities get their team name appended so two rows both
    // reading "Jaspreet Singh" aren't confusingly identical on screen.
    return groupKey.includes('::') ? `${name} (${team})` : name;
  }

  function mergeBattingEntries(rawEntries){
    // Combines a player's stats across every team they play for into one
    // row, rather than listing the same person once per team — unless
    // player-overrides.json flags that name+team as a genuinely different
    // person, in which case it's kept separate (see getPlayerGroupKey).
    const map = new Map();
    rawEntries.forEach(e => {
      const key = getPlayerGroupKey(e.player, e.team);
      if (!normTeamName(e.player)) return;
      if (!map.has(key)) {
        map.set(key, {
          player: e.player, groupKey: key, teams: new Set(),
          innings: 0, runs: 0, notOuts: 0, fours: 0, sixes: 0, hundreds: 0, highest: 0,
          srWeightedSum: 0, srWeight: 0,
        });
      }
      const agg = map.get(key);
      agg.teams.add(e.team);
      const inn = toNum(e.innings);
      agg.innings += inn;
      agg.runs += toNum(e.runs);
      agg.notOuts += toNum(e.not_outs);
      agg.fours += toNum(e.fours);
      agg.sixes += toNum(e.sixes);
      agg.hundreds += toNum(e.hundreds);
      agg.highest = Math.max(agg.highest, toNum(e.highest_score));
      const sr = toNum(e.strike_rate);
      if (inn && sr) { agg.srWeightedSum += sr * inn; agg.srWeight += inn; }
    });
    return Array.from(map.values()).map(agg => {
      const dismissals = Math.max(agg.innings - agg.notOuts, 0);
      const avg = dismissals > 0 ? (agg.runs / dismissals).toFixed(2) : String(agg.runs);
      const sr = agg.srWeight > 0 ? (agg.srWeightedSum / agg.srWeight).toFixed(2) : '-';
      const team = Array.from(agg.teams).join(', ');
      return {
        player: getPlayerDisplayName(agg.player, team, agg.groupKey),
        groupKey: agg.groupKey,
        team,
        stat: `${agg.runs} runs (Avg ${avg}, SR ${sr})`,
      };
    });
  }

  function mergeBowlingEntries(rawEntries){
    const map = new Map();
    rawEntries.forEach(e => {
      const key = getPlayerGroupKey(e.player, e.team);
      if (!normTeamName(e.player)) return;
      if (!map.has(key)) {
        map.set(key, {
          player: e.player, groupKey: key, teams: new Set(),
          innings: 0, wickets: 0, maidens: 0, best: 0,
          ecoWeightedSum: 0, avgWeightedSum: 0, weight: 0,
        });
      }
      const agg = map.get(key);
      agg.teams.add(e.team);
      const inn = toNum(e.innings);
      agg.innings += inn;
      agg.wickets += toNum(e.wickets);
      agg.maidens += toNum(e.maidens);
      agg.best = Math.max(agg.best, toNum(e.best));
      const eco = toNum(e.economy), avg = toNum(e.average);
      if (inn) {
        agg.ecoWeightedSum += eco * inn;
        agg.avgWeightedSum += avg * inn;
        agg.weight += inn;
      }
    });
    return Array.from(map.values()).map(agg => {
      const eco = agg.weight > 0 ? (agg.ecoWeightedSum / agg.weight).toFixed(2) : '-';
      const avg = agg.weight > 0 ? (agg.avgWeightedSum / agg.weight).toFixed(2) : '-';
      const team = Array.from(agg.teams).join(', ');
      return {
        player: getPlayerDisplayName(agg.player, team, agg.groupKey),
        groupKey: agg.groupKey,
        team,
        stat: `${agg.wickets} wkts (Econ ${eco}, Avg ${avg})`,
      };
    });
  }

  function mergeFieldingEntries(rawEntries){
    const map = new Map();
    rawEntries.forEach(e => {
      const key = getPlayerGroupKey(e.player, e.team);
      if (!normTeamName(e.player)) return;
      if (!map.has(key)) {
        map.set(key, { player: e.player, groupKey: key, teams: new Set(), catches: 0, dismissals: 0 });
      }
      const agg = map.get(key);
      agg.teams.add(e.team);
      agg.catches += toNum(e.catches);
      agg.dismissals += toNum(e.dismissals);
    });
    return Array.from(map.values()).map(agg => {
      const team = Array.from(agg.teams).join(', ');
      return {
        player: getPlayerDisplayName(agg.player, team, agg.groupKey),
        groupKey: agg.groupKey,
        team,
        stat: `${agg.catches} catches, ${agg.dismissals} dismissals`,
      };
    });
  }

  function renderTopPerformers(prefix, leagueFilter, battingList, bowlingList, fieldingList){
    // "Season" prefix only applies to the all-leagues view — a specific
    // league is already unambiguous from the active chip right below.
    const scopePrefix = leagueFilter === 'all' ? 'Season ' : '';
    const showTeam = leagueFilter !== 'all'; // in "All Leagues" a merged player's team list can be long/redundant

    const setCard = (kind, list, kindLabel) => {
      const card = document.getElementById(`${prefix}-top-${kind}`);
      if (!card) return;
      if (!list || !list.length) { card.style.display = 'none'; return; }
      const top = list[0];
      const badge = document.getElementById(`${prefix}-top-${kind}-badge`);
      if (badge) badge.textContent = `${scopePrefix}Top ${kindLabel}`;
      const avatar = document.getElementById(`${prefix}-top-${kind}-avatar`);
      if (avatar) avatar.innerHTML = playerAvatarHtml(top.player, 'large');
      document.getElementById(`${prefix}-top-${kind}-name`).textContent = top.player;
      document.getElementById(`${prefix}-top-${kind}-stat`).textContent = `${showTeam && top.team ? top.team + ' \u00b7 ' : ''}${top.stat}`;
      card.style.display = '';
    };

    setCard('batsman', battingList, 'Batsman');
    setCard('bowler', bowlingList, 'Bowler');
    setCard('fielder', fieldingList, 'Fielder');
  }

  function renderLeaderboardsForLeague(clubTeams, leagueFilter, tournamentsByName){
    let rawBatting = [], rawBowling = [], rawFielding = [];
    let usedRealLeagueData = false;

    const tournament = leagueFilter !== 'all' ? tournamentsByName?.get(leagueFilter) : null;

    if (tournament) {
      // Real per-league data: already filtered to HCC players only by the scraper.
      rawBatting = tournament.batting || [];
      rawBowling = tournament.bowling || [];
      rawFielding = tournament.fielding || [];
      usedRealLeagueData = true;
    } else {
      // Fallback: no tournament-specific data available for this league (or "All Leagues"
      // is selected), so approximate using each team's full-season combined stats.
      clubTeams.forEach(team => {
        if (leagueFilter !== 'all') {
          const leagues = getTeamLeagues(team);
          if (!leagues.has(leagueFilter)) return; // team doesn't play in this league — excluded
        }
        (team.leaderboard_detailed?.batting || []).forEach(e => rawBatting.push({ ...e, team: team.name }));
        (team.leaderboard_detailed?.bowling || []).forEach(e => rawBowling.push({ ...e, team: team.name }));
        (team.leaderboard_detailed?.fielding || []).forEach(e => rawFielding.push({ ...e, team: team.name }));
      });
    }

    const clubBatting = mergeBattingEntries(rawBatting);
    const clubBowling = mergeBowlingEntries(rawBowling);
    const clubFielding = mergeFieldingEntries(rawFielding);

    clubBatting.sort((a, b) => leadingNumber(b.stat) - leadingNumber(a.stat));
    clubBowling.sort((a, b) => leadingNumber(b.stat) - leadingNumber(a.stat));
    clubFielding.sort((a, b) => leadingNumber(b.stat) - leadingNumber(a.stat));

    renderTopPerformers('club', leagueFilter, clubBatting, clubBowling, clubFielding);

    const showTeam = leagueFilter !== 'all';
    document.querySelectorAll('.lb-team-col').forEach(el => { el.style.display = showTeam ? '' : 'none'; });

    renderLeaderboardRows('club-leaderboard-batting', clubBatting.slice(0, 10), showTeam, 'club');
    renderLeaderboardRows('club-leaderboard-bowling', clubBowling.slice(0, 10), showTeam, 'club');
    renderLeaderboardRows('club-leaderboard-fielding', clubFielding.slice(0, 10), showTeam, 'club');

    renderSeasonRecord(computeSeasonRecord(clubTeams, leagueFilter));

    const lbNote = document.getElementById('club-leaderboard-note');
    if (lbNote) {
      const label = leagueFilter === 'all' ? 'all club teams' : leagueFilter;
      if (!clubBatting.length) {
        lbNote.textContent = `No leaderboard data for ${label} yet.`;
      } else if (usedRealLeagueData) {
        lbNote.textContent = `${label} only · CricHeroes tournament leaderboard`;
      } else {
        lbNote.textContent = `Combined across ${label} (full-season team totals — per-league split unavailable) · CricHeroes`;
      }
    }
  }

  function buildLeagueChips(clubTeams, tournamentsByName){
    const row = document.getElementById('league-chip-row');
    if (!row) return;

    const leagues = new Set();
    clubTeams.forEach(team => getTeamLeagues(team).forEach(l => leagues.add(l)));

    row.innerHTML = '';
    const makeChip = (label, value, active) => {
      const btn = document.createElement('button');
      btn.className = 'league-chip' + (active ? ' active' : '');
      btn.textContent = label;
      btn.dataset.league = value;
      btn.addEventListener('click', () => {
        row.querySelectorAll('.league-chip').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        renderLeaderboardsForLeague(clubTeams, value, tournamentsByName);
      });
      return btn;
    };

    row.appendChild(makeChip('All Leagues', 'all', true));
    Array.from(leagues).sort().forEach(l => row.appendChild(makeChip(l, l, false)));
  }

  async function fetchTournamentsByName(){
    // Tournament-level data is optional — the site works fine without it
    // (falls back to team-level approximation), so failures here are silent.
    const map = new Map();
    try {
      const res = await fetch(CRICHEROES_TOURNAMENTS_DATA_URL, { cache: 'no-store' });
      if (!res.ok) return map;
      const data = await res.json();
      Object.values(data.tournaments || {}).forEach(t => {
        if (t.name) map.set(t.name, t);
      });
    } catch (err) {
      console.warn('Tournament-level CricHeroes data unavailable — using team-level totals per league.', err);
    }
    return map;
  }

  function normalizeCricClubsMatch(m){
    // CricClubs match shape -> the same {date, opponent, result, status}
    // shape used everywhere else on the site.
    const weAreA = /hcc|halton/i.test(m.team_a || '');
    const opponent = weAreA ? m.team_b : m.team_a;
    return {
      date: m.date,
      opponent: opponent || m.team_b || m.team_a,
      result: m.result,
      status: 'past', // CricClubs "Results" tab only lists completed/decided matches
    };
  }

  async function fetchCricClubsData(){
    // Optional data source — the site works fine without it.
    try {
      const res = await fetch(CRICCLUBS_DATA_URL, { cache: 'no-store' });
      if (!res.ok) return null;
      return await res.json();
    } catch (err) {
      console.warn('CricClubs data unavailable.', err);
      return null;
    }
  }

  async function loadPlayerPhotos(){
    // Optional — players without a submitted photo just show initials instead.
    try {
      const res = await fetch(PLAYER_PHOTOS_URL, { cache: 'no-store' });
      if (!res.ok) return;
      playerPhotos = await res.json();
    } catch (err) {
      // no photo file yet — that's fine, everyone just shows initials
    }
  }

  function playerInitials(name){
    return (name || '?').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();
  }

  function playerAvatarHtml(name, size){
    const key = normTeamName(name);
    const filename = playerPhotos[key];
    if (filename){
      const cls = size === 'large' ? 'player-avatar-large' : 'player-avatar';
      return `<img class="${cls}" src="${PLAYER_PHOTOS_BASE}${encodeURIComponent(filename)}" alt="${escapeHtml(name)}" loading="lazy">`;
    }
    const cls = size === 'large' ? 'player-avatar-large-placeholder' : 'player-avatar-placeholder';
    return `<span class="${cls}">${escapeHtml(playerInitials(name))}</span>`;
  }

  function renderAcademyLeaderboardForLeague(academyTeams, leagueFilter){
    let rawBatting = [], rawBowling = [], rawFielding = [];
    const label = leagueFilter === 'all'
      ? `CricClubs (${academyTeams.map(t => t.shortName).join(' & ')})`
      : `CricClubs (${leagueFilter})`;

    academyTeams.forEach(team => {
      if (leagueFilter !== 'all' && team.shortName !== leagueFilter) return;
      (team.leaderboard_detailed.batting || []).forEach(e => rawBatting.push({ ...e, team: team.name }));
      (team.leaderboard_detailed.bowling || []).forEach(e => rawBowling.push({ ...e, team: team.name }));
      (team.leaderboard_detailed.fielding || []).forEach(e => rawFielding.push({ ...e, team: team.name }));
    });

    const batting = mergeBattingEntries(rawBatting).sort((a, b) => leadingNumber(b.stat) - leadingNumber(a.stat));
    const bowling = mergeBowlingEntries(rawBowling).sort((a, b) => leadingNumber(b.stat) - leadingNumber(a.stat));
    const fielding = mergeFieldingEntries(rawFielding).sort((a, b) => leadingNumber(b.stat) - leadingNumber(a.stat));

    renderTopPerformers('academy', leagueFilter, batting, bowling, fielding);

    const battingOk = renderLeaderboardRows('academy-leaderboard-batting', batting.slice(0, 10), false, 'academy');
    const bowlingOk = renderLeaderboardRows('academy-leaderboard-bowling', bowling.slice(0, 10), false, 'academy');
    const fieldingOk = renderLeaderboardRows('academy-leaderboard-fielding', fielding.slice(0, 10), false, 'academy');

    const note = document.getElementById('academy-leaderboard-note');
    if (note) {
      note.textContent = (battingOk || bowlingOk || fieldingOk) ? label : 'No leaderboard data yet.';
    }
  }

  function buildAcademyLeagueChips(academyTeams){
    const row = document.getElementById('academy-league-chip-row');
    if (!row) return;

    row.innerHTML = '';
    const makeChip = (label, value, active) => {
      const btn = document.createElement('button');
      btn.className = 'league-chip' + (active ? ' active' : '');
      btn.textContent = label;
      btn.addEventListener('click', () => {
        row.querySelectorAll('.league-chip').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        renderAcademyLeaderboardForLeague(academyTeams, value);
      });
      return btn;
    };

    row.appendChild(makeChip('All Leagues', 'all', true));
    academyTeams.forEach(team => row.appendChild(makeChip(team.shortName, team.shortName, false)));
  }

  function parseFlexibleDate(str){
    if (!str) return null;
    // "DD-Mon-YY" e.g. "30-Aug-26" (CricHeroes)
    let m = str.match(/^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$/);
    if (m){
      const year = m[3].length === 2 ? '20' + m[3] : m[3];
      const d = new Date(`${m[2]} ${m[1]}, ${year}`);
      if (!isNaN(d)) return d;
    }
    // "D Mon YYYY" e.g. "15 Aug 2026" (CricClubs)
    m = str.match(/^(\d{1,2}) ([A-Za-z]{3,}) (\d{4})$/);
    if (m){
      const d = new Date(`${m[2]} ${m[1]}, ${m[3]}`);
      if (!isNaN(d)) return d;
    }
    return null;
  }

  function daysBetween(a, b){
    const oneDay = 24 * 60 * 60 * 1000;
    const aMid = new Date(a.getFullYear(), a.getMonth(), a.getDate());
    const bMid = new Date(b.getFullYear(), b.getMonth(), b.getDate());
    return Math.round((bMid - aMid) / oneDay);
  }

  function renderMatchDaySpotlight(prefix, matches){
    const card = document.getElementById(`${prefix}-matchday`);
    if (!card) return;

    const today = new Date();
    let best = null, bestKind = null;

    matches.forEach(m => {
      const d = parseFlexibleDate(m.date);
      if (!d) return;
      const diff = daysBetween(d, today); // >0 = in the past, 0 = today, <0 = future

      if (diff === 0){
        best = m; bestKind = 'live';
      } else if (bestKind !== 'live' && diff > 0 && diff <= 3){
        if (!best || diff < daysBetween(parseFlexibleDate(best.date), today)){
          best = m; bestKind = 'recent';
        }
      }
    });

    if (!best){
      card.style.display = 'none';
      return;
    }

    const badge = document.getElementById(`${prefix}-matchday-badge`);
    const teamsEl = document.getElementById(`${prefix}-matchday-teams`);
    const detailEl = document.getElementById(`${prefix}-matchday-detail`);

    if (bestKind === 'live'){
      badge.className = 'matchday-badge live';
      badge.innerHTML = '<span class="matchday-dot"></span> Match Day';
      card.classList.add('is-live');
      detailEl.textContent = `${best.date} \u00b7 Follow live scoring on CricHeroes or CricClubs for ball-by-ball updates.`;
    } else {
      badge.className = 'matchday-badge recent';
      badge.textContent = 'Recent Result';
      card.classList.remove('is-live');
      detailEl.textContent = `${best.date}${best.result ? ' \u00b7 ' + best.result : ''}`;
    }
    teamsEl.textContent = `${best.team || ''}${best.opponent ? ' vs ' + best.opponent : ''}`;
    card.style.display = '';
  }

  async function loadCricHeroesData(){
    try {
      const res = await fetch(CRICHEROES_DATA_URL, { cache: 'no-store' });
      if (!res.ok) throw new Error('Fetch failed: ' + res.status);
      const data = await res.json();
      const tournamentsByName = await fetchTournamentsByName();
      const cricclubsData = await fetchCricClubsData();
      await loadPlayerPhotos();
      await loadPlayerOverrides();

      const clubMatches = [];
      const academyMatches = [];
      const clubTeams = [];

      Object.values(data.teams || {}).forEach(team => {
        if (!team.ok) return;

        if (Array.isArray(team.matches)) {
          const tagged = team.matches.map(m => ({ ...m, team: team.name }));
          if (team.group === 'club') clubMatches.push(...tagged);
          else if (team.group === 'academy') academyMatches.push(...tagged);
        }

        if (team.group === 'club') clubTeams.push(team);
      });

      // Fold in CricClubs teams: their batting/bowling/wickets fields already
      // match CricHeroes' naming closely enough to flow through the same
      // merge/leaderboard pipeline, and their "Results" tab matches slot
      // straight into the existing fixtures tables. Each academy team page
      // (TDCA, Canada Unity Cup, etc.) is kept as its own separate source so
      // the league chips can filter to exactly one, or merge all of them.
      const academyTeams = [];

      if (cricclubsData && cricclubsData.teams) {
        Object.values(cricclubsData.teams).forEach((team, idx) => {
          if (!team.ok || team.page_type !== 'team_page') return;

          const tagged = (team.matches || []).map(m => ({
            ...normalizeCricClubsMatch(m), team: team.name,
          }));
          if (team.group === 'club') clubMatches.push(...tagged);
          else if (team.group === 'academy') academyMatches.push(...tagged);

          const fielding = (team.fielding || []).map(f => ({ ...f, dismissals: f.total }));
          const leaderboard_detailed = { batting: team.batting || [], bowling: team.bowling || [], fielding };

          if (team.group === 'club') {
            clubTeams.push({
              name: team.name,
              leaderboard_detailed,
              matches_detailed: (team.matches || []).map(m => ({ ...m, league: m.competition, status: 'past' })),
            });
          } else if (team.group === 'academy') {
            // Short label for the league chip — pull whatever's in parentheses
            // in the team name (e.g. "... (TDCA)" -> "TDCA"), else fall back
            // to the full name.
            const parenMatch = team.name.match(/\(([^)]+)\)\s*$/);
            academyTeams.push({
              name: team.name,
              shortName: parenMatch ? parenMatch[1] : team.name,
              leaderboard_detailed,
            });
          }
        });
      }

      const updated = data.generated_at
        ? new Date(data.generated_at).toLocaleString('en-CA', { dateStyle: 'medium', timeStyle: 'short' })
        : null;

      const clubOk = renderFixtureRows('club-fixtures-tbody', clubMatches.slice(0, 8));
      setNote('club-fixtures-note', clubOk
        ? `Live data from CricHeroes & CricClubs${updated ? ' · updated ' + updated : ''}`
        : 'No live data yet for Club teams — showing sample fixtures.');

      const academyOk = renderFixtureRows('academy-fixtures-tbody', academyMatches.slice(0, 8));
      setNote('academy-fixtures-note', academyOk
        ? `Live data from CricClubs${updated ? ' · updated ' + updated : ''}`
        : 'No live data yet for Academy teams — showing sample fixtures.');

      renderMatchDaySpotlight('club', clubMatches);
      renderMatchDaySpotlight('academy', academyMatches);

  function buildPlayerSearchIndex(clubTeams, academyTeams){
    clubTeams.forEach(team => {
      (team.leaderboard_detailed?.batting || []).forEach(e => playerSearchIndex.club.batting.push({ ...e, team: team.name }));
      (team.leaderboard_detailed?.bowling || []).forEach(e => playerSearchIndex.club.bowling.push({ ...e, team: team.name }));
      (team.leaderboard_detailed?.fielding || []).forEach(e => playerSearchIndex.club.fielding.push({ ...e, team: team.name }));
    });
    academyTeams.forEach(team => {
      (team.leaderboard_detailed?.batting || []).forEach(e => playerSearchIndex.academy.batting.push({ ...e, team: team.name }));
      (team.leaderboard_detailed?.bowling || []).forEach(e => playerSearchIndex.academy.bowling.push({ ...e, team: team.name }));
      (team.leaderboard_detailed?.fielding || []).forEach(e => playerSearchIndex.academy.fielding.push({ ...e, team: team.name }));
    });
  }

  function statBox(val, lbl){
    return `<div class="player-stat-box"><div class="val">${escapeHtml(String(val))}</div><div class="lbl">${escapeHtml(lbl)}</div></div>`;
  }

  function openPlayerModal(playerName, realm, groupKey){
    // Fall back to plain-name matching if no group key is available (e.g.
    // an older cached page), but prefer the group key so two different
    // people sharing a name (flagged in player-overrides.json) don't get
    // merged together in the profile view either.
    const key = groupKey || normTeamName(playerName);
    const pool = playerSearchIndex[realm] || playerSearchIndex.club;

    const matchesKey = (e) => getPlayerGroupKey(e.player, e.team) === key;
    const battingRaw = pool.batting.filter(matchesKey);
    const bowlingRaw = pool.bowling.filter(matchesKey);
    const fieldingRaw = pool.fielding.filter(matchesKey);

    const teams = new Set([...battingRaw, ...bowlingRaw, ...fieldingRaw].map(e => e.team));

    document.getElementById('player-modal-name').textContent = playerName;
    document.getElementById('player-modal-teams').textContent = Array.from(teams).join(' · ') || '\u2014';
    document.getElementById('player-modal-avatar').innerHTML = playerAvatarHtml(playerName, 'large');

    // Batting: sum + recompute average/SR properly rather than just display one row's stat string.
    const battingSection = document.getElementById('player-modal-batting-section');
    const battingGrid = document.getElementById('player-modal-batting-grid');
    if (battingRaw.length){
      let runs = 0, innings = 0, notOuts = 0, highest = 0, srSum = 0, srWeight = 0;
      battingRaw.forEach(e => {
        const inn = toNum(e.innings);
        runs += toNum(e.runs);
        innings += inn;
        notOuts += toNum(e.not_outs);
        highest = Math.max(highest, toNum(e.highest_score));
        const sr = toNum(e.strike_rate);
        if (inn && sr) { srSum += sr * inn; srWeight += inn; }
      });
      const dismissals = Math.max(innings - notOuts, 0);
      const avg = dismissals > 0 ? (runs / dismissals).toFixed(2) : String(runs);
      const sr = srWeight > 0 ? (srSum / srWeight).toFixed(2) : '-';
      battingGrid.innerHTML = statBox(runs, 'Runs') + statBox(innings, 'Innings') + statBox(avg, 'Average')
        + statBox(sr, 'Strike Rate') + statBox(highest, 'High Score') + statBox(notOuts, 'Not Outs');
      battingSection.style.display = '';
    } else {
      battingSection.style.display = 'none';
    }

    const bowlingSection = document.getElementById('player-modal-bowling-section');
    const bowlingGrid = document.getElementById('player-modal-bowling-grid');
    if (bowlingRaw.length){
      let wickets = 0, innings = 0, ecoSum = 0, avgSum = 0, weight = 0;
      bowlingRaw.forEach(e => {
        const inn = toNum(e.innings);
        wickets += toNum(e.wickets);
        innings += inn;
        const eco = toNum(e.economy), avg = toNum(e.average);
        if (inn) { ecoSum += eco * inn; avgSum += avg * inn; weight += inn; }
      });
      const eco = weight > 0 ? (ecoSum / weight).toFixed(2) : '-';
      const avg = weight > 0 ? (avgSum / weight).toFixed(2) : '-';
      bowlingGrid.innerHTML = statBox(wickets, 'Wickets') + statBox(innings, 'Innings') + statBox(avg, 'Average') + statBox(eco, 'Economy');
      bowlingSection.style.display = '';
    } else {
      bowlingSection.style.display = 'none';
    }

    const fieldingSection = document.getElementById('player-modal-fielding-section');
    const fieldingGrid = document.getElementById('player-modal-fielding-grid');
    if (fieldingRaw.length){
      let catches = 0, dismissals = 0;
      fieldingRaw.forEach(e => { catches += toNum(e.catches); dismissals += toNum(e.dismissals); });
      fieldingGrid.innerHTML = statBox(catches, 'Catches') + statBox(dismissals, 'Dismissals');
      fieldingSection.style.display = '';
    } else {
      fieldingSection.style.display = 'none';
    }

    document.getElementById('player-modal-overlay').classList.add('open');
  }

  function closePlayerModal(){
    document.getElementById('player-modal-overlay').classList.remove('open');
  }

  document.addEventListener('click', (evt) => {
    const toggle = evt.target.closest('.news-toggle');
    if (toggle){
      const card = toggle.closest('.news-card');
      const full = card.querySelector('.news-full');
      const isHidden = full.hasAttribute('hidden');
      if (isHidden){
        full.removeAttribute('hidden');
        toggle.textContent = 'Show less \u2191';
      } else {
        full.setAttribute('hidden', '');
        toggle.textContent = 'Read full story \u2192';
      }
    }
  });

  document.addEventListener('click', (evt) => {
    const btn = evt.target.closest('.player-link');
    if (btn) openPlayerModal(btn.dataset.player, btn.dataset.realm, btn.dataset.groupKey);
  });
  document.getElementById('player-modal-close')?.addEventListener('click', closePlayerModal);
  document.getElementById('player-modal-overlay')?.addEventListener('click', (evt) => {
    if (evt.target.id === 'player-modal-overlay') closePlayerModal();
  });
  document.addEventListener('keydown', (evt) => {
    if (evt.key === 'Escape') closePlayerModal();
  });

  buildLeagueChips(clubTeams, tournamentsByName);
      renderLeaderboardsForLeague(clubTeams, 'all', tournamentsByName);

      buildAcademyLeagueChips(academyTeams);
      renderAcademyLeaderboardForLeague(academyTeams, 'all');

      buildPlayerSearchIndex(clubTeams, academyTeams);

    } catch (err) {
      console.warn('CricHeroes data unavailable, showing sample fixtures.', err);
      const lbNote = document.getElementById('club-leaderboard-note');
      if (lbNote) lbNote.textContent = 'Live stats unavailable right now.';
      const academyLbNote = document.getElementById('academy-leaderboard-note');
      if (academyLbNote) academyLbNote.textContent = 'Live stats unavailable right now.';
      // Sample rows already in the HTML stay as-is — no action needed.
    }
  }

  loadCricHeroesData();
