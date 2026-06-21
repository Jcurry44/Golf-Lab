const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let currentView = "players";
let summary = null;
let eventBoard = null;
let playerPayload = null;
let coursePayload = null;
let modelPayload = null;
let healthPayload = null;
let filterPayload = null;
let playerSearch = "";
let playerVisibleLimit = 48;
let rankingVisibleLimit = 10;
let rankingCategory = "index";
let seasonProfiles = new Map();
let careerProfiles = new Map();
let comparePlayerIds = [];
let playerFilters = defaultPlayerFilters();

const staticMode = location.protocol === "file:" || location.hostname.endsWith("github.io");
const BUILD_VERSION = "20260621-player-trust";
const RECENT_PROFILE_CUTOFF = "2023-01-01";

function versionedPath(path) {
  return `${path}${path.includes("?") ? "&" : "?"}v=${BUILD_VERSION}`;
}

function staticApiPath(path) {
  const url = new URL(path, location.origin);
  const endpoint = url.pathname.replace(/^\/api\/?/, "");
  if (endpoint === "player") {
    return versionedPath(`api/players/${encodeURIComponent(url.searchParams.get("id") || "")}.json`);
  }
  if (endpoint === "course") {
    return versionedPath(`api/courses/${encodeURIComponent(url.searchParams.get("id") || "")}.json`);
  }
  return versionedPath(`api/${endpoint}.json`);
}

async function api(path) {
  const target = staticMode ? staticApiPath(path) : path;
  let response = await fetch(target, { headers: { accept: "application/json" } });
  if (!response.ok && !staticMode) {
    response = await fetch(staticApiPath(path), { headers: { accept: "application/json" } });
  }
  const payload = await response.json();
  if (!response.ok || payload.error) throw new Error(payload.error || `Request failed: ${path}`);
  return payload;
}

function fmt(value, digits = 0) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return numeric.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function pct(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return `${fmt(numeric, 1)}%`;
}

function pctDecimal(value) {
  if (value === null || value === undefined || value === "") return "--";
  return pct(Number(value) * 100);
}

function roundScoreLabel(row) {
  const score = Number(row?.score);
  if (!Number.isFinite(score)) return "--";
  return score >= 55 && score <= 95 ? fmt(score) : `${fmt(score)} pts`;
}

function signed(value, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return `${numeric > 0 ? "+" : ""}${fmt(numeric, digits)}`;
}

function numeric(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function filterNumber(key) {
  const value = numeric(playerFilters[key]);
  return value === null ? null : value;
}

function defaultPlayerFilters() {
  return {
    season: "all",
    scope: "all",
    minRounds: "",
    minDistance: "",
    minGir: "",
    minFairways: "",
    minScramble: "",
    maxToPar: "",
    maxScore: "",
    minSg: "",
    minToughRounds: "",
    maxToughToPar: "",
    minMajorRounds: "",
    strength: "any",
    sort: "ranking",
  };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function setStatus(text, tone = "neutral") {
  const pill = $("#statusPill");
  pill.textContent = text;
  pill.dataset.tone = tone;
}

function viewMeta(view) {
  const event = summary?.selectedEvent || {};
  const eventName = event.event_name || "Modeled event";
  const meta = {
    players: {
      eyebrow: "PGA TOUR | PLAYER DATABASE",
      title: "PGA Tour rankings.",
      subtitle: "A world-ranking style player database built from scoring, strokes gained, distance, GIR, tough-course form, and major profile.",
    },
    majors: {
      eyebrow: "PGA TOUR | MAJOR CHAMPIONSHIPS",
      title: "Major Championship Lab.",
      subtitle: "Rank major specialists by championship scoring, strokes gained, experience, and course-proof profile.",
    },
    tournament: {
      eyebrow: `${eventName} | weekly tournament`,
      title: "Tournament prediction center.",
      subtitle: "This week's event board: projected standings, model tiers, fair prices, market edges, and plain-English reasoning.",
    },
    courses: {
      eyebrow: "PGA TOUR | COURSE DATABASE",
      title: "Course Lab.",
      subtitle: "Find the hardest setups, gettable tracks, and players whose profiles fit each course.",
    },
    data: {
      eyebrow: "Trust layer",
      title: "Data coverage audit.",
      subtitle: "Scorecards, rich public stat seasons, tough-course DNA, major profiles, source proof, and refresh status.",
    },
  };
  return meta[view] || meta.players;
}

function renderViewHeader() {
  const meta = viewMeta(currentView);
  $("#topEyebrow").textContent = meta.eyebrow;
  $("#heroTitle").textContent = meta.title;
  $("#heroSubtitle").textContent = meta.subtitle;
}

function setView(view, options = {}) {
  currentView = view;
  $$(".nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
    button.setAttribute("aria-pressed", button.dataset.view === view ? "true" : "false");
  });
  $$("[data-panel]").forEach((panel) => {
    const views = panel.dataset.panel.split(/\s+/);
    panel.hidden = !views.includes(view);
  });
  renderViewHeader();
  if (options.push) history.pushState({ view }, "", `#${view}`);
  if (options.scroll) {
    document.querySelector(".main")?.scrollIntoView({ block: "start" });
  }
}

function renderSummary() {
  const counts = summary.counts || {};
  $("#railProof").textContent = `${fmt(counts.players)} players | ${fmt(counts.rounds)} rounds`;
  setStatus("PGA Tour DB", counts.players ? "good" : "watch");
  const richProfiles = (filterPayload?.rows || []).filter((row) =>
    numeric(row.avg_sg_total) !== null || numeric(row.driving_distance) !== null || numeric(row.gir) !== null
  ).length;
  $("#heroProof").innerHTML = [
    ["Players", counts.players],
    ["Scorecards", counts.rounds],
    ["Rich profiles", richProfiles],
    ["Courses", counts.courses],
  ].map(([label, value]) => `<span><strong>${fmt(value)}</strong>${escapeHtml(label)}</span>`).join("");

  const event = summary.selectedEvent || {};
  renderViewHeader();
  $("#eventCard").innerHTML = `
    <p class="eyebrow">Weekly tournament</p>
    <h2>${escapeHtml(event.event_name || "No event loaded")}</h2>
    <div class="event-meta">
      <span>${escapeHtml(event.course_name || "Course pending")}</span>
      <span>${escapeHtml([event.start_date, event.end_date].filter(Boolean).join(" to "))}</span>
    </div>
    <div class="event-kpis">
      ${metric("Player DB", counts.players)}
      ${metric("Event field", counts.fields)}
      ${metric("Rounds", counts.rounds)}
    </div>
  `;

  $("#metricGrid").innerHTML = [
    ["Players", counts.players, "profile library"],
    ["Field", counts.fields, "active event"],
    ["Events", counts.events, "schedule"],
    ["Scorecards", counts.rounds, "round-level"],
    ["SG Rows", counts.strokes_gained, "derived model"],
    ["Markets", counts.odds_snapshots, "odds snapshots"],
  ].map(([label, value, note]) => `
    <article class="metric-card">
      <span>${escapeHtml(label)}</span>
      <strong>${fmt(value)}</strong>
      <small>${escapeHtml(note)}</small>
    </article>
  `).join("");
}

function metric(label, value, note = "") {
  const rendered = typeof value === "number" ? fmt(value) : (value ?? "--");
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(rendered)}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ""}</div>`;
}

function weightedMerge(target, row, fields) {
  const rounds = numeric(row.rounds) || 0;
  const scoringRounds = numeric(row.scoring_rounds) || 0;
  const toughRounds = numeric(row.tough_rounds) || 0;
  const gettableRounds = numeric(row.gettable_rounds) || 0;
  const majorRounds = numeric(row.major_rounds) || 0;
  if (!rounds && !scoringRounds && !toughRounds && !gettableRounds && !majorRounds) return;
  target.rounds = (target.rounds || 0) + rounds;
  for (const field of fields) {
    const value = numeric(row[field]);
    if (value === null) continue;
    let weight = rounds;
    if (field === "scoring_average") weight = scoringRounds;
    if (field.startsWith("tough_")) weight = toughRounds;
    if (field.startsWith("gettable_")) weight = gettableRounds;
    if (field.startsWith("major_")) weight = majorRounds;
    if (!weight) continue;
    target[`${field}_weighted`] = (target[`${field}_weighted`] || 0) + value * weight;
    target[`${field}_rounds`] = (target[`${field}_rounds`] || 0) + weight;
  }
  if (!target.last_round || String(row.last_round || "") > target.last_round) {
    target.last_round = row.last_round;
  }
}

function buildProfileMaps() {
  seasonProfiles = new Map();
  const career = new Map();
  const weightedFields = [
    "scoring_average",
    "avg_to_par",
    "avg_sg_total",
    "sg_t2g",
    "sg_ott",
    "sg_app",
    "sg_arg",
    "sg_putt",
    "tough_avg_to_par",
    "tough_avg_sg",
    "gettable_avg_to_par",
    "gettable_avg_sg",
    "major_avg_to_par",
    "major_avg_sg",
  ];
  for (const row of filterPayload?.rows || []) {
    seasonProfiles.set(`${row.player_id}|${row.season}`, row);
    if (!career.has(row.player_id)) career.set(row.player_id, { player_id: row.player_id, season: "all" });
    weightedMerge(career.get(row.player_id), row, weightedFields);
    const target = career.get(row.player_id);
    target.tough_rounds = (target.tough_rounds || 0) + (numeric(row.tough_rounds) || 0);
    target.gettable_rounds = (target.gettable_rounds || 0) + (numeric(row.gettable_rounds) || 0);
    target.major_rounds = (target.major_rounds || 0) + (numeric(row.major_rounds) || 0);
    target.major_events = (target.major_events || 0) + (numeric(row.major_events) || 0);
    target.scoring_rounds = (target.scoring_rounds || 0) + (numeric(row.scoring_rounds) || 0);
  }
  careerProfiles = new Map();
  for (const [playerId, row] of career.entries()) {
    const profile = { player_id: playerId, season: "all", rounds: row.rounds || 0, scoring_rounds: row.scoring_rounds || 0, last_round: row.last_round };
    for (const field of weightedFields) {
      const rounds = row[`${field}_rounds`] || 0;
      profile[field] = rounds ? Number((row[`${field}_weighted`] / rounds).toFixed(2)) : null;
    }
    profile.tough_rounds = row.tough_rounds || 0;
    profile.gettable_rounds = row.gettable_rounds || 0;
    profile.major_rounds = row.major_rounds || 0;
    profile.major_events = row.major_events || 0;
    careerProfiles.set(playerId, profile);
  }
}

function profileForRow(row) {
  if (playerFilters.season !== "all") {
    const profile = seasonProfiles.get(`${row.player_id}|${playerFilters.season}`);
    if (!profile) return null;
    return {
      ...row,
      ...profile,
      rank: row.rank,
      probability: row.probability,
      edge_probability: row.edge_probability,
      projected_to_par: row.projected_to_par,
      confidence: row.confidence,
      plain_english: row.plain_english,
      risk_flags: row.risk_flags,
      modeled: row.modeled,
    };
  }
  const profile = careerProfiles.get(row.player_id) || {};
  return {
    ...row,
    ...profile,
    driving_distance: row.driving_distance,
    accuracy: row.accuracy,
    gir: row.gir,
    scrambling: row.scrambling,
    rank: row.rank,
    probability: row.probability,
    edge_probability: row.edge_probability,
    projected_to_par: row.projected_to_par,
    confidence: row.confidence,
    plain_english: row.plain_english,
    risk_flags: row.risk_flags,
    modeled: row.modeled,
  };
}

function statValue(profile, key) {
  const value = numeric(profile?.[key]);
  return value === null ? null : value;
}

function plausibleScoringAverage(profile) {
  const value = statValue(profile, "scoring_average");
  return value !== null && value >= 60 && value <= 80;
}

function isCurrentProfile(profile) {
  const lastRound = String(profile?.last_round || "");
  return lastRound >= RECENT_PROFILE_CUTOFF
    || statValue(profile, "driving_distance") !== null
    || statValue(profile, "gir") !== null;
}

function labRankScore(row, profile) {
  const sg = statValue(profile, "avg_sg_total") ?? -4;
  const t2g = statValue(profile, "sg_t2g") ?? 0;
  const scoring = statValue(profile, "scoring_average");
  const qualifiedRounds = (numeric(profile?.rounds) || 0) >= 20;
  const scoringBonus = qualifiedRounds && scoring !== null && scoring >= 60
    ? Math.max(0, 72.5 - scoring) * 4
    : 0;
  const rounds = Math.min(numeric(profile?.rounds) || 0, 240);
  const majorRounds = Math.min(numeric(profile?.major_rounds) || 0, 60);
  const toughRounds = Math.min(numeric(profile?.tough_rounds) || 0, 80);
  const samplePenalty = qualifiedRounds ? 0 : 260;
  return (sg * 100) + (t2g * 24) + scoringBonus + (rounds / 12) + (majorRounds / 6) + (toughRounds / 10) - samplePenalty;
}

function compareLabRank(a, b) {
  const aq = (numeric(a.profile?.rounds) || 0) >= 20;
  const bq = (numeric(b.profile?.rounds) || 0) >= 20;
  if (aq !== bq) return aq ? -1 : 1;
  const av = labRankScore(a.row, a.profile);
  const bv = labRankScore(b.row, b.profile);
  if (av !== bv) return bv - av;
  return compareDesc(a, b, "avg_sg_total") || a.row.player_name.localeCompare(b.row.player_name);
}

function compareDesc(a, b, key) {
  const av = statValue(a.profile, key);
  const bv = statValue(b.profile, key);
  if (av === null && bv === null) return 0;
  if (av === null) return 1;
  if (bv === null) return -1;
  return bv - av;
}

function compareAsc(a, b, key) {
  const av = statValue(a.profile, key);
  const bv = statValue(b.profile, key);
  if (av === null && bv === null) return 0;
  if (av === null) return 1;
  if (bv === null) return -1;
  return av - bv;
}

function sortDecoratedPlayers(rows) {
  return rows.sort((a, b) => {
    if (playerFilters.sort === "ranking") return compareLabRank(a, b);
    if (playerFilters.sort === "distance") return compareDesc(a, b, "driving_distance") || a.row.player_name.localeCompare(b.row.player_name);
    if (playerFilters.sort === "gir") return compareDesc(a, b, "gir") || a.row.player_name.localeCompare(b.row.player_name);
    if (playerFilters.sort === "tough") return compareAsc(a, b, "tough_avg_to_par") || compareDesc(a, b, "tough_rounds");
    if (playerFilters.sort === "majors") return compareAsc(a, b, "major_avg_to_par") || compareDesc(a, b, "major_rounds");
    if (playerFilters.sort === "scoring") return compareAsc(a, b, "scoring_average") || compareAsc(a, b, "avg_to_par");
    if (playerFilters.sort === "sg") return compareDesc(a, b, "avg_sg_total") || compareDesc(a, b, "sg_t2g");
    if (playerFilters.sort === "name") return a.row.player_name.localeCompare(b.row.player_name);
    const ar = numeric(a.row.rank);
    const br = numeric(b.row.rank);
    if (ar !== null || br !== null) return (ar ?? 9999) - (br ?? 9999);
    return compareDesc(a, b, "avg_sg_total") || a.row.player_name.localeCompare(b.row.player_name);
  });
}

function renderEvent() {
  const event = eventBoard.event || {};
  const weather = eventBoard.weather || [];
  const avgWind = weather.length
    ? weather.reduce((sum, row) => sum + (Number(row.wind_mph) || 0), 0) / weather.length
    : null;
  if (avgWind) {
    $("#eventCard").insertAdjacentHTML("beforeend", `<p class="event-note">Weather signal: ${fmt(avgWind)} mph average wind in loaded forecast windows.</p>`);
  }
}

function renderFilterConsole() {
  const seasonSelect = $("#seasonFilter");
  if (!seasonSelect) return;
  const current = playerFilters.season;
  const seasonOptions = (filterPayload?.seasons || []).map((row) => `
    <option value="${escapeHtml(row.season)}">${escapeHtml(row.season)} (${fmt(row.players)} players)</option>
  `).join("");
  seasonSelect.innerHTML = `<option value="all">All seasons</option>${seasonOptions}`;
  seasonSelect.value = current;
}

function syncPlayerFilterControls() {
  $$("[data-player-filter]").forEach((control) => {
    control.value = playerFilters[control.dataset.playerFilter] ?? "";
  });
}

function applyMajorPlayerPreset() {
  playerFilters = {
    ...defaultPlayerFilters(),
    minMajorRounds: "8",
    sort: "majors",
  };
  rankingCategory = "majors";
  rankingVisibleLimit = 50;
  playerVisibleLimit = 48;
  syncPlayerFilterControls();
  setView("players", { push: true, scroll: true });
  renderPlayers();
}

function passesNumericFloor(profile, key, filterKey, multiplier = 1) {
  const threshold = filterNumber(filterKey);
  if (threshold === null) return true;
  const value = statValue(profile, key);
  return value !== null && value * multiplier >= threshold;
}

function passesNumericCeiling(profile, key, filterKey) {
  const threshold = filterNumber(filterKey);
  if (threshold === null) return true;
  const value = statValue(profile, key);
  return value !== null && value <= threshold;
}

function playerPassesFilters(row, profile) {
  if (!profile) return false;
  if (playerFilters.scope === "modeled" && !row.modeled) return false;
  if (playerFilters.scope === "database" && row.modeled) return false;
  if (!passesNumericFloor(profile, "rounds", "minRounds")) return false;
  if (!passesNumericFloor(profile, "driving_distance", "minDistance")) return false;
  if (!passesNumericFloor(profile, "gir", "minGir", 100)) return false;
  if (!passesNumericFloor(profile, "accuracy", "minFairways", 100)) return false;
  if (!passesNumericFloor(profile, "scrambling", "minScramble", 100)) return false;
  if (!passesNumericFloor(profile, "avg_sg_total", "minSg")) return false;
  if (!passesNumericFloor(profile, "tough_rounds", "minToughRounds")) return false;
  if (!passesNumericFloor(profile, "major_rounds", "minMajorRounds")) return false;
  if (playerFilters.sort === "majors" && !isCurrentProfile(profile)) return false;
  if (!passesNumericCeiling(profile, "avg_to_par", "maxToPar")) return false;
  if (!passesNumericCeiling(profile, "tough_avg_to_par", "maxToughToPar")) return false;
  if (!passesNumericCeiling(profile, "scoring_average", "maxScore")) return false;

  if (playerFilters.strength !== "any") {
    const value = statValue(profile, playerFilters.strength);
    if (value === null || value <= 0) return false;
  }
  return true;
}

function confidenceTone(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("thin") || text.includes("watch")) return "watch";
  if (text.includes("high")) return "good";
  return "neutral";
}

function playerById(playerId) {
  return (playerPayload?.rows || []).find((row) => row.player_id === playerId) || null;
}

function decoratedPlayer(playerId) {
  const row = playerById(playerId);
  if (!row) return null;
  return { row, profile: profileForRow(row) || row };
}

function bestBy(players, key, direction = "desc") {
  const rows = players.filter((item) => statValue(item.profile, key) !== null);
  if (!rows.length) return null;
  return rows.sort((a, b) => {
    const av = statValue(a.profile, key);
    const bv = statValue(b.profile, key);
    return direction === "asc" ? av - bv : bv - av;
  })[0];
}

function renderCompareTray() {
  const tray = $("#compareTray");
  if (!tray) return;
  const players = comparePlayerIds.map(decoratedPlayer).filter(Boolean);
  tray.hidden = !players.length;
  if (!players.length) {
    tray.innerHTML = "";
    return;
  }
  const sgLeader = bestBy(players, "avg_sg_total");
  const toughLeader = bestBy(players, "tough_avg_to_par", "asc");
  const majorLeader = bestBy(players, "major_avg_to_par", "asc");
  const insightParts = [
    sgLeader ? `${sgLeader.row.player_name} leads profile SG at ${signed(sgLeader.profile.avg_sg_total)}.` : "",
    toughLeader ? `${toughLeader.row.player_name} has the cleanest tough-course read at ${signed(toughLeader.profile.tough_avg_to_par)} to par.` : "",
    majorLeader ? `${majorLeader.row.player_name} owns the best loaded major sample at ${signed(majorLeader.profile.major_avg_to_par)} to par.` : "",
  ].filter(Boolean);
  tray.innerHTML = `
    <div class="compare-head">
      <div>
        <p class="eyebrow">Compare lab</p>
        <h3>${fmt(players.length)} selected</h3>
      </div>
      <button type="button" class="ghost-button" data-clear-compare>Clear</button>
    </div>
    <p class="compare-read">${escapeHtml(insightParts.join(" ") || "Pick another player to unlock a plain-English comparison.")}</p>
    <div class="compare-grid">
      ${players.map(({ row, profile }) => `
        <article class="compare-card">
          <button type="button" aria-label="Remove ${escapeHtml(row.player_name)}" data-remove-compare="${escapeHtml(row.player_id)}">Remove</button>
          <strong>${escapeHtml(row.player_name)}</strong>
          <span>${escapeHtml(row.country || "PGA")} | ${fmt(profile.rounds)} rounds</span>
          <div>
            ${metric("SG", signed(profile.avg_sg_total))}
            ${metric("Tough", signed(profile.tough_avg_to_par))}
            ${metric("Major", signed(profile.major_avg_to_par))}
            ${metric("Drive", profile.driving_distance ? `${fmt(profile.driving_distance, 1)} yd` : "--")}
            ${metric("GIR", pctDecimal(profile.gir))}
            ${metric("Score", profile.scoring_average ? fmt(profile.scoring_average, 2) : "--")}
          </div>
          <a href="./player.html?id=${encodeURIComponent(row.player_id)}">Open full card</a>
        </article>
      `).join("")}
    </div>
  `;
}

function toggleCompare(playerId) {
  if (comparePlayerIds.includes(playerId)) {
    comparePlayerIds = comparePlayerIds.filter((id) => id !== playerId);
  } else {
    comparePlayerIds = [...comparePlayerIds, playerId].slice(-4);
  }
  renderPlayers();
}

function playerMatchesSearch(row) {
  if (!playerSearch) return true;
  const haystack = [
    row.player_name,
    row.country,
    row.confidence,
    row.plain_english,
  ].join(" ").toLowerCase();
  return haystack.includes(playerSearch);
}

function decoratedLibraryRows() {
  return (playerPayload.rows || [])
    .filter(playerMatchesSearch)
    .map((row) => ({ row, profile: profileForRow(row) }))
    .filter(({ row, profile }) => playerPassesFilters(row, profile));
}

function decoratedCareerRows() {
  return (playerPayload?.rows || [])
    .map((row) => ({ row, profile: careerProfiles.get(row.player_id) || row }))
    .filter(({ profile }) => profile);
}

function metricRows(rows, key, direction = "desc", options = {}) {
  const minRounds = options.minRounds || 0;
  const minSampleKey = options.minSampleKey;
  const minSample = options.minSample || 0;
  return [...rows]
    .filter(({ profile }) => {
      if (statValue(profile, key) === null) return false;
      if ((numeric(profile.rounds) || 0) < minRounds) return false;
      if (minSampleKey && (numeric(profile[minSampleKey]) || 0) < minSample) return false;
      return true;
    })
    .sort((a, b) => direction === "asc"
      ? compareAsc(a, b, key) || a.row.player_name.localeCompare(b.row.player_name)
      : compareDesc(a, b, key) || a.row.player_name.localeCompare(b.row.player_name))
    .slice(0, options.limit || 5);
}

function rankingCategoryConfig(key) {
  const configs = {
    index: {
      label: "Rankings",
      eyebrow: "Golf Lab Index",
      title: "World-ranking style PGA board",
      description: "Career scorecards, rich PGA Tour stat profiles, major form, and course-difficulty splits.",
      qualify: ({ profile }) => (numeric(profile.rounds) || 0) >= 20,
      compare: compareLabRank,
      columns: ["SG", "Score", "Drive", "GIR", "Majors"],
      cells: [
        (row, profile) => signed(profile.avg_sg_total),
        (row, profile) => profile.scoring_average ? fmt(profile.scoring_average, 2) : "--",
        (row, profile) => profile.driving_distance ? fmt(profile.driving_distance, 1) : "--",
        (row, profile) => pctDecimal(profile.gir),
        (row, profile) => fmt(profile.major_rounds || 0),
      ],
    },
    sg: {
      label: "Strokes gained",
      eyebrow: "Performance",
      title: "Strokes gained leaders",
      description: "Total SG, tee-to-green strength, and scoring output for qualified players.",
      qualify: ({ profile }) => (numeric(profile.rounds) || 0) >= 20 && statValue(profile, "avg_sg_total") !== null,
      compare: (a, b) => compareDesc(a, b, "avg_sg_total") || compareDesc(a, b, "sg_t2g") || a.row.player_name.localeCompare(b.row.player_name),
      columns: ["SG", "T2G", "Score", "Rounds", "Majors"],
      cells: [
        (row, profile) => signed(profile.avg_sg_total),
        (row, profile) => signed(profile.sg_t2g),
        (row, profile) => profile.scoring_average ? fmt(profile.scoring_average, 2) : "--",
        (row, profile) => fmt(profile.rounds),
        (row, profile) => fmt(profile.major_rounds || 0),
      ],
    },
    distance: {
      label: "Distance",
      eyebrow: "Power",
      title: "Driving distance leaders",
      description: "Power rankings with fairway control and scoring context attached.",
      qualify: ({ profile }) => statValue(profile, "driving_distance") !== null,
      compare: (a, b) => compareDesc(a, b, "driving_distance") || compareDesc(a, b, "avg_sg_total") || a.row.player_name.localeCompare(b.row.player_name),
      columns: ["Distance", "Fairways", "SG", "GIR", "Rounds"],
      cells: [
        (row, profile) => `${fmt(profile.driving_distance, 1)} yd`,
        (row, profile) => pctDecimal(profile.accuracy),
        (row, profile) => signed(profile.avg_sg_total),
        (row, profile) => pctDecimal(profile.gir),
        (row, profile) => fmt(profile.rounds),
      ],
    },
    gir: {
      label: "GIR",
      eyebrow: "Iron control",
      title: "Greens in regulation leaders",
      description: "GIR rankings with approach, scoring, and distance context.",
      qualify: ({ profile }) => statValue(profile, "gir") !== null,
      compare: (a, b) => compareDesc(a, b, "gir") || compareDesc(a, b, "sg_app") || a.row.player_name.localeCompare(b.row.player_name),
      columns: ["GIR", "Approach", "Score", "Drive", "Rounds"],
      cells: [
        (row, profile) => pctDecimal(profile.gir),
        (row, profile) => signed(profile.sg_app),
        (row, profile) => profile.scoring_average ? fmt(profile.scoring_average, 2) : "--",
        (row, profile) => profile.driving_distance ? fmt(profile.driving_distance, 1) : "--",
        (row, profile) => fmt(profile.rounds),
      ],
    },
    scoring: {
      label: "Scoring",
      eyebrow: "Scoring",
      title: "Scoring average leaders",
      description: "Raw scoring average with SG and to-par context for qualified samples.",
      qualify: ({ profile }) => (numeric(profile.scoring_rounds) || 0) >= 20 && plausibleScoringAverage(profile),
      compare: (a, b) => compareAsc(a, b, "scoring_average") || compareDesc(a, b, "avg_sg_total") || a.row.player_name.localeCompare(b.row.player_name),
      columns: ["Score", "To par", "SG", "GIR", "Rounds"],
      cells: [
        (row, profile) => fmt(profile.scoring_average, 2),
        (row, profile) => signed(profile.avg_to_par),
        (row, profile) => signed(profile.avg_sg_total),
        (row, profile) => pctDecimal(profile.gir),
        (row, profile) => fmt(profile.scoring_rounds || 0),
      ],
    },
    tough: {
      label: "Tough courses",
      eyebrow: "Course DNA",
      title: "Tough-course performers",
      description: "Players who travel best to brutal and tough course setups.",
      qualify: ({ profile }) => (numeric(profile.tough_rounds) || 0) >= 8 && statValue(profile, "tough_avg_to_par") !== null,
      compare: (a, b) => compareAsc(a, b, "tough_avg_to_par") || compareDesc(a, b, "tough_rounds") || a.row.player_name.localeCompare(b.row.player_name),
      columns: ["Tough", "Tough SG", "Rounds", "Overall SG", "Majors"],
      cells: [
        (row, profile) => signed(profile.tough_avg_to_par),
        (row, profile) => signed(profile.tough_avg_sg),
        (row, profile) => fmt(profile.tough_rounds || 0),
        (row, profile) => signed(profile.avg_sg_total),
        (row, profile) => fmt(profile.major_rounds || 0),
      ],
    },
    majors: {
      label: "Majors",
      eyebrow: "Championship profile",
      title: "Major championship performers",
      description: "Current/recent profiles ranked by major scoring and SG across loaded championship rounds.",
      qualify: ({ profile }) => isCurrentProfile(profile) && (numeric(profile.major_rounds) || 0) >= 8 && statValue(profile, "major_avg_to_par") !== null,
      compare: (a, b) => compareAsc(a, b, "major_avg_to_par") || compareDesc(a, b, "major_rounds") || a.row.player_name.localeCompare(b.row.player_name),
      columns: ["Major avg", "Major SG", "Rounds", "Overall SG", "Score"],
      cells: [
        (row, profile) => signed(profile.major_avg_to_par),
        (row, profile) => signed(profile.major_avg_sg),
        (row, profile) => fmt(profile.major_rounds || 0),
        (row, profile) => signed(profile.avg_sg_total),
        (row, profile) => profile.scoring_average ? fmt(profile.scoring_average, 2) : "--",
      ],
    },
  };
  return configs[key] || configs.index;
}

function rankingCategoryButtons(activeKey) {
  return ["index", "sg", "distance", "gir", "scoring", "tough", "majors"].map((key) => {
    const config = rankingCategoryConfig(key);
    const active = key === activeKey;
    return `<button type="button" class="${active ? "is-active" : ""}" data-ranking-category="${escapeHtml(key)}" aria-pressed="${active ? "true" : "false"}">${escapeHtml(config.label)}</button>`;
  }).join("");
}

function rankingLimitButtons(activeLimit) {
  return [10, 50, 100].map((limit) => {
    const active = limit === activeLimit;
    return `<button type="button" class="${active ? "is-active" : ""}" data-ranking-limit="${limit}" aria-pressed="${active ? "true" : "false"}">Top ${limit}</button>`;
  }).join("");
}

function leaderboardList(title, subtitle, rows, valueFormatter, noteFormatter) {
  return `
    <article class="leaderboard-card">
      <div class="leaderboard-title">
        <span>${escapeHtml(subtitle)}</span>
        <strong>${escapeHtml(title)}</strong>
      </div>
      <div class="leaderboard-list">
        ${rows.map(({ row, profile }, index) => `
          <a href="./player.html?id=${encodeURIComponent(row.player_id)}">
            <b>${index + 1}</b>
            <span>
              <strong>${escapeHtml(row.player_name)}</strong>
              <small>${escapeHtml(noteFormatter(row, profile))}</small>
            </span>
            <em>${escapeHtml(valueFormatter(row, profile))}</em>
          </a>
        `).join("") || empty("No qualified players.")}
      </div>
    </article>
  `;
}

function rankingBoard(rows, totalRows, config) {
  return `
    <article class="tour-ranking-board">
      <div class="tour-board-head">
        <div>
          <p class="eyebrow">${escapeHtml(config.eyebrow)}</p>
          <h3>${escapeHtml(config.title)}</h3>
          <p>${escapeHtml(config.description)}</p>
        </div>
        <div class="board-metric">
          <span>Qualified board</span>
          <strong>${fmt(totalRows)}</strong>
          <small>Showing top ${fmt(rows.length)}</small>
        </div>
      </div>
      <div class="ranking-controls">
        <div class="ranking-pill-group" aria-label="Ranking category">
          ${rankingCategoryButtons(rankingCategory)}
        </div>
        <div class="ranking-pill-group compact" aria-label="Ranking size">
          ${rankingLimitButtons(rankingVisibleLimit)}
        </div>
      </div>
      <div class="ranking-table" role="table" aria-label="Golf Lab player rankings">
        <div class="ranking-row ranking-head" role="row">
          <span>Rank</span>
          <span>Player</span>
          ${config.columns.map((column) => `<span>${escapeHtml(column)}</span>`).join("")}
        </div>
        ${rows.map(({ row, profile }, index) => `
          <a class="ranking-row" href="./player.html?id=${encodeURIComponent(row.player_id)}" role="row">
            <b>${index + 1}</b>
            <span class="ranking-player">
              <strong>${escapeHtml(row.player_name)}</strong>
              <small>${escapeHtml(row.country || "PGA")} | ${fmt(profile.rounds)} rounds | Tough ${fmt(profile.tough_rounds || 0)}</small>
            </span>
            ${config.cells.map((formatter) => `<span>${escapeHtml(formatter(row, profile))}</span>`).join("")}
          </a>
        `).join("") || empty("No qualified players loaded. Refresh the data warehouse.")}
      </div>
    </article>
  `;
}

function renderMajorLab() {
  const target = $("#majorLab");
  if (!target) return;
  const rows = decoratedCareerRows().filter(({ profile }) =>
    isCurrentProfile(profile) && (numeric(profile.major_rounds) || 0) >= 8 && statValue(profile, "major_avg_to_par") !== null
  );
  const majorRounds = rows.reduce((sum, { profile }) => sum + (numeric(profile.major_rounds) || 0), 0);
  const bestScoring = bestBy(rows, "major_avg_to_par", "asc");
  const bestSg = bestBy(rows, "major_avg_sg", "desc");
  const mostTested = [...rows].sort((a, b) =>
    (numeric(b.profile.major_rounds) || 0) - (numeric(a.profile.major_rounds) || 0)
    || a.row.player_name.localeCompare(b.row.player_name)
  )[0];
  const majorBoards = [
    leaderboardList(
      "Major scoring",
      "Championship scoring",
      metricRows(rows, "major_avg_to_par", "asc", { minSampleKey: "major_rounds", minSample: 8, limit: 10 }),
      (row, profile) => signed(profile.major_avg_to_par),
      (row, profile) => `${fmt(profile.major_rounds)} major rounds | SG ${signed(profile.major_avg_sg)}`
    ),
    leaderboardList(
      "Major SG",
      "Model signal",
      metricRows(rows, "major_avg_sg", "desc", { minSampleKey: "major_rounds", minSample: 8, limit: 10 }),
      (row, profile) => signed(profile.major_avg_sg),
      (row, profile) => `${signed(profile.major_avg_to_par)} to par | ${fmt(profile.major_rounds)} rounds`
    ),
    leaderboardList(
      "Major experience",
      "Loaded sample",
      metricRows(rows, "major_rounds", "desc", { minSampleKey: "major_rounds", minSample: 8, limit: 10 }),
      (row, profile) => fmt(profile.major_rounds),
      (row, profile) => `${fmt(profile.major_events || 0)} major events | ${signed(profile.major_avg_to_par)} to par`
    ),
  ].join("");
  target.innerHTML = `
    <section class="major-command">
      <div>
        <p class="eyebrow">Championship database</p>
        <h3>Find who actually travels to major setups now.</h3>
        <p>Use major scoring, major SG, recency, and sample size together so the board favors current players who have performed across loaded championship rounds, not one noisy week.</p>
        <div class="major-actions">
          <button type="button" class="ghost-button" data-major-action="board">Open Top 50 major board</button>
          <button type="button" class="ghost-button" data-major-action="filter">Filter player cards</button>
        </div>
      </div>
      <div class="major-proof-grid">
        ${metric("Profiles", rows.length)}
        ${metric("Major rounds", majorRounds)}
        ${metric("Best scorer", bestScoring ? bestScoring.row.player_name : "--", bestScoring ? signed(bestScoring.profile.major_avg_to_par) : "")}
        ${metric("Best SG", bestSg ? bestSg.row.player_name : "--", bestSg ? signed(bestSg.profile.major_avg_sg) : "")}
        ${metric("Most tested", mostTested ? mostTested.row.player_name : "--", mostTested ? `${fmt(mostTested.profile.major_rounds)} rounds` : "")}
      </div>
    </section>
    <section class="leaderboard-grid compact major-leaderboards">${majorBoards}</section>
  `;
}

function renderLeaderboards() {
  const target = $("#leaderboardGrid");
  if (!target) return;
  const rows = decoratedLibraryRows();
  const activeConfig = rankingCategoryConfig(rankingCategory);
  const rankingRows = [...rows].filter(activeConfig.qualify).sort(activeConfig.compare);
  const visibleRankingRows = rankingRows.slice(0, rankingVisibleLimit);
  const compactBoards = [
    leaderboardList(
      "Strokes gained",
      "Performance",
      metricRows(rows, "avg_sg_total", "desc", { minRounds: 20 }),
      (row, profile) => signed(profile.avg_sg_total),
      (row, profile) => `${fmt(profile.rounds)} rounds | T2G ${signed(profile.sg_t2g)}`
    ),
    leaderboardList(
      "Driving distance",
      "Power",
      metricRows(rows, "driving_distance", "desc"),
      (row, profile) => `${fmt(profile.driving_distance, 1)} yd`,
      (row, profile) => `Fairways ${pctDecimal(profile.accuracy)}`
    ),
    leaderboardList(
      "GIR",
      "Iron control",
      metricRows(rows, "gir", "desc"),
      (row, profile) => pctDecimal(profile.gir),
      (row, profile) => `Approach ${signed(profile.sg_app)} | ${fmt(profile.rounds)} rounds`
    ),
    leaderboardList(
      "Tough courses",
      "Course DNA",
      metricRows(rows, "tough_avg_to_par", "asc", { minSampleKey: "tough_rounds", minSample: 8 }),
      (row, profile) => signed(profile.tough_avg_to_par),
      (row, profile) => `${fmt(profile.tough_rounds)} tough rounds | SG ${signed(profile.tough_avg_sg)}`
    ),
    leaderboardList(
      "Majors",
      "Championship profile",
      metricRows(rows, "major_avg_to_par", "asc", { minSampleKey: "major_rounds", minSample: 8 }),
      (row, profile) => signed(profile.major_avg_to_par),
      (row, profile) => `${fmt(profile.major_rounds)} major rounds | SG ${signed(profile.major_avg_sg)}`
    ),
  ].join("");
  target.innerHTML = `${rankingBoard(visibleRankingRows, rankingRows.length, activeConfig)}<div class="leaderboard-grid compact">${compactBoards}</div>`;
}

function renderPlayers(limit = currentView === "players" ? playerVisibleLimit : 8) {
  const allRows = playerPayload.rows || [];
  const decoratedRows = sortDecoratedPlayers(decoratedLibraryRows());
  const rows = decoratedRows.slice(0, limit).map((item, index) => ({ ...item, displayRank: index + 1 }));
  renderLeaderboards();
  const count = $("#playerCount");
  if (count) {
    const label = playerFilters.season === "all" ? "all seasons" : `${playerFilters.season} season`;
    count.textContent = `${fmt(decoratedRows.length)} of ${fmt(allRows.length)} player cards | ${label}`;
  }
  renderCompareTray();
  $("#playerCards").innerHTML = rows.map(({ row, profile, displayRank }) => {
    const selected = comparePlayerIds.includes(row.player_id);
    const rankLabel = playerFilters.sort === "model" && row.rank ? `#${escapeHtml(row.rank)}` : `#${fmt(displayRank)}`;
    return `
    <article class="player-card ${selected ? "is-selected" : ""}">
      <div class="player-card-head">
        <div class="rank">${rankLabel}</div>
        <div>
          <h3>${escapeHtml(row.player_name)}</h3>
          <p class="player-card-meta">${escapeHtml(row.country || "PGA")} | ${fmt(profile.rounds)} imported scorecards${playerFilters.season === "all" ? "" : ` | ${escapeHtml(playerFilters.season)}`}</p>
        </div>
        <div class="status-pill" data-tone="${confidenceTone(row.confidence)}">${escapeHtml(row.modeled ? "Tournament field" : (row.confidence || "watch"))}</div>
      </div>
      <div class="card-stats">
        ${metric("Lab rank", `#${fmt(displayRank)}`)}
        ${metric("SG", signed(profile.avg_sg_total))}
        ${metric("Scoring", profile.scoring_average ? fmt(profile.scoring_average, 2) : "--")}
      </div>
      <p class="plain">${escapeHtml(row.plain_english || "Model explanation pending.")}</p>
      <div class="scorecard-footer">
        <span>Drive <strong>${profile.driving_distance ? `${fmt(profile.driving_distance, 1)} yd` : "--"}</strong></span>
        <span>GIR <strong>${pctDecimal(profile.gir)}</strong></span>
        <span>Majors <strong>${fmt(profile.major_rounds)}</strong></span>
        <span>Score <strong>${profile.scoring_average ? fmt(profile.scoring_average, 2) : "--"}</strong></span>
      </div>
      <div class="player-card-actions">
        <a href="./player.html?id=${encodeURIComponent(row.player_id)}">Open card</a>
        <button type="button" data-compare-player="${escapeHtml(row.player_id)}">${selected ? "Selected" : "Compare"}</button>
      </div>
    </article>
  `;
  }).join("") || empty("No player cards match those filters.");
  const actions = $("#playerActions");
  if (actions) {
    actions.innerHTML = decoratedRows.length > rows.length
      ? `<button type="button" class="ghost-button" data-show-more-players>Show ${fmt(Math.min(48, decoratedRows.length - rows.length))} more</button>`
      : "";
  }
}

function renderCourses(limit = currentView === "courses" ? 36 : 6) {
  const rows = (coursePayload.rows || []).slice(0, limit);
  $("#courseCards").innerHTML = rows.map((row) => `
    <button type="button" class="course-card" data-course-id="${escapeHtml(row.course_id)}">
      <div class="difficulty ${escapeHtml(row.difficulty_bucket || "balanced")}">${escapeHtml(row.difficulty_bucket || "balanced")}</div>
      <h3>${escapeHtml(row.course_name)}</h3>
      <p>${escapeHtml(row.location || "Location pending")}</p>
      <div class="card-stats">
        ${metric("Rounds", row.rounds)}
        ${metric("Avg To Par", signed(row.avg_to_par))}
        ${metric("Yards", row.yards)}
      </div>
    </button>
  `).join("") || empty("No course cards yet.");
}

function modelTierRows() {
  const order = ["Win Core", "Contender Pool", "Longshot With Signal", "Volatility Watch"];
  const grouped = new Map(order.map((tier) => [tier, []]));
  for (const row of modelPayload.rows || []) {
    const tier = row.tier || "Volatility Watch";
    if (!grouped.has(tier)) grouped.set(tier, []);
    grouped.get(tier).push(row);
  }
  return order.map((tier) => ({ tier, rows: (grouped.get(tier) || []).slice(0, tier === "Win Core" ? 8 : 6) }))
    .filter((group) => group.rows.length);
}

function renderPredictionCenter() {
  const target = $("#predictionCenter");
  if (!target) return;
  const event = modelPayload.event || summary?.selectedEvent || {};
  const rows = modelPayload.rows || [];
  const favorite = rows[0];
  const positiveEdges = rows.filter((row) => Number(row.edge_probability) > 0).length;
  target.innerHTML = `
    <section class="prediction-hero">
      <div>
        <p class="eyebrow">Prediction center</p>
        <h3>${escapeHtml(event.event_name || "Current tournament")}</h3>
        <p>${escapeHtml(favorite?.tier_reason || "Projected standings will appear when model predictions are loaded.")}</p>
      </div>
      <div class="prediction-kpis">
        ${metric("Projected leader", favorite?.player_name || "--")}
        ${metric("Win prob", favorite ? pct(favorite.probability_pct) : "--")}
        ${metric("Positive edges", positiveEdges)}
      </div>
    </section>
    <section class="projection-board">
      <div class="projection-board-head">
        <p class="eyebrow">Projected standings</p>
        <h3>Model board with plain-English reasons</h3>
      </div>
      <div class="projection-list">
        ${rows.slice(0, 12).map((row) => `
          <a href="./player.html?id=${encodeURIComponent(row.player_id)}">
            <b>#${escapeHtml(row.rank || "--")}</b>
            <span>
              <strong>${escapeHtml(row.player_name)}</strong>
              <small>${pct(row.probability_pct)} win | ${signed(row.projected_to_par)} projected to par | ${escapeHtml(row.confidence || "Watch")}</small>
            </span>
            <em>${escapeHtml(row.tier_reason || row.plain_english || "Reasoning pending.")}</em>
          </a>
        `).join("") || empty("No projected standings loaded yet.")}
      </div>
    </section>
    <div class="tier-grid">
      ${modelTierRows().map((group) => `
        <article class="tier-card">
          <span>${escapeHtml(group.tier)}</span>
          ${group.rows.map((row) => `
            <a href="./player.html?id=${encodeURIComponent(row.player_id)}">
              <strong>${escapeHtml(row.player_name)}</strong>
              <small>#${escapeHtml(row.rank || "--")} | ${pct(row.probability_pct)} | ${signed(row.projected_to_par)} to par</small>
              <em>${escapeHtml(row.tier_reason || row.plain_english || "Reasoning pending.")}</em>
            </a>
          `).join("")}
        </article>
      `).join("")}
    </div>
  `;
}

function renderModel(limit = currentView === "tournament" ? 40 : 10) {
  const rows = (modelPayload.rows || []).slice(0, limit);
  renderPredictionCenter();
  $("#modelBoard").innerHTML = `
    <div class="model-table">
      ${rows.map((row) => `
        <a class="model-row" href="./player.html?id=${encodeURIComponent(row.player_id)}">
          <span class="model-rank">${escapeHtml(row.rank || "--")}</span>
          <strong>${escapeHtml(row.player_name)}</strong>
          <span>${pct(row.probability_pct)}</span>
          <span>${signed(row.projected_to_par)}</span>
          <em>${escapeHtml(row.confidence || "Watch")}</em>
          <p>${escapeHtml(row.plain_english || "Reasoning pending.")}</p>
        </a>
      `).join("")}
    </div>
  ` || empty("No model rows yet.");
}

function renderHealth() {
  const blockers = healthPayload.blockers || [];
  const sources = healthPayload.sources || [];
  const coverage = healthPayload.coverage || [];
  const statQuality = healthPayload.statQuality || [];
  const automation = healthPayload.automation || [];
  $("#warehouseHealth").innerHTML = `
    <div class="health-band ${blockers.length ? "watch" : "good"}">
      <strong>${escapeHtml(healthPayload.grade || "setup")}</strong>
      <span>${blockers.length ? blockers.join(" | ") : "Warehouse has the minimum model-ready lanes."}</span>
    </div>
    <div class="coverage-audit">
      ${coverage.map((row) => `
        <article class="${escapeHtml(row.status || "watch")}">
          <span>${escapeHtml(row.label)}</span>
          <strong>${fmt(row.value)}</strong>
          <small>${escapeHtml(row.note || "")}</small>
        </article>
      `).join("")}
    </div>
    <section class="stat-quality-panel">
      <div class="panel-heading">
        <div>
          <p class="eyebrow">Leaderboard contracts</p>
          <h3>Stat Quality Gates</h3>
        </div>
      </div>
      <div class="stat-quality-grid">
        ${statQuality.map((row) => `
          <article class="${escapeHtml(row.status || "watch")}">
            <span>${escapeHtml(row.label)}</span>
            <strong>${fmt(row.value)}</strong>
            <small>${escapeHtml(row.note || "")}</small>
            <em>${escapeHtml(row.contract || "")}</em>
          </article>
        `).join("") || empty("Stat quality checks will appear after the warehouse refresh.")}
      </div>
    </section>
    <div class="automation-strip">
      ${automation.map((row) => `
        <article>
          <span>${escapeHtml(row.label)}</span>
          <strong>${escapeHtml(row.status)}</strong>
          <small>${escapeHtml(row.note)}</small>
        </article>
      `).join("")}
    </div>
    <div class="source-list">
      ${sources.map((row) => `
        <div>
          <strong>${escapeHtml(row.provider || "source")}</strong>
          <span>${escapeHtml(row.endpoint || "")}</span>
          <em>${escapeHtml(row.fetched_at || "")}</em>
        </div>
      `).join("")}
    </div>
  `;
}

function empty(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

async function openPlayer(playerId) {
  const eventId = summary.selectedEvent && summary.selectedEvent.event_id ? `&event_id=${encodeURIComponent(summary.selectedEvent.event_id)}` : "";
  const detail = await api(`/api/player?id=${encodeURIComponent(playerId)}${eventId}`);
  const player = detail.player;
  $("#drawerBody").innerHTML = `
    <p class="eyebrow">Player card</p>
    <h2>${escapeHtml(player.player_name)}</h2>
    <div class="drawer-kpis">
      ${metric("Rounds", player.rounds)}
      ${metric("Avg To Par", signed(player.avg_to_par))}
      ${metric("Avg SG", signed(player.avg_sg_total))}
      ${metric("Distance", player.driving_distance ? `${fmt(player.driving_distance, 1)} yd` : "--")}
      ${metric("Fairways", pctDecimal(player.accuracy))}
      ${metric("GIR", pctDecimal(player.gir))}
    </div>
    <section>
      <h3>Plain-English Model Read</h3>
      <p>${escapeHtml((detail.model && detail.model.plain_english) || "No model read saved yet.")}</p>
      <p class="risk">${escapeHtml((detail.model && detail.model.risk_flags) || "No major risk flags saved.")}</p>
    </section>
    <section>
      <h3>Best Course Fits</h3>
      ${detail.bestCourses.rows.map((row) => `<div class="mini-row"><strong>${escapeHtml(row.course)}</strong><span>${signed(row.avg_to_par)} avg</span></div>`).join("") || empty("Need more course history.")}
    </section>
    <section>
      <h3>Recent Scorecards</h3>
      ${detail.rounds.rows.map((row) => `<div class="mini-row"><strong>${escapeHtml(row.event_name || row.course)}</strong><span>R${escapeHtml(row.round_number)} ${escapeHtml(roundScoreLabel(row))} (${signed(row.to_par, 0)})</span></div>`).join("")}
    </section>
  `;
  $("#detailDrawer").hidden = false;
}

async function openCourse(courseId) {
  const detail = await api(`/api/course?id=${encodeURIComponent(courseId)}`);
  const course = detail.course;
  $("#drawerBody").innerHTML = `
    <p class="eyebrow">Course card</p>
    <h2>${escapeHtml(course.course_name)}</h2>
    <div class="drawer-kpis">
      ${metric("Difficulty", course.difficulty_bucket || "pending")}
      ${metric("Rounds", course.rounds)}
      ${metric("Avg To Par", signed(course.avg_to_par))}
    </div>
    <section>
      <h3>Best Player Fits</h3>
      ${detail.fits.rows.map((row) => `<div class="mini-row"><strong>${escapeHtml(row.player_name)}</strong><span>${signed(row.avg_to_par)} avg | ${signed(row.avg_sg)} SG</span></div>`).join("") || empty("Need more course rounds.")}
    </section>
    <section>
      <h3>Recent Setups</h3>
      ${detail.setups.rows.map((row) => `<div class="mini-row"><strong>${escapeHtml(row.event_name || "Event")}</strong><span>${escapeHtml(row.yards || "--")} yards | ${escapeHtml(row.difficulty_bucket || "setup")}</span></div>`).join("") || empty("No setup rows yet.")}
    </section>
  `;
  $("#detailDrawer").hidden = false;
}

function bindEvents() {
  $$(".nav button").forEach((button) => button.addEventListener("click", () => {
    setView(button.dataset.view, { push: true, scroll: true });
    renderMajorLab();
    renderPlayers();
    renderCourses();
    renderModel();
  }));
  $$("[data-view-jump]").forEach((button) => button.addEventListener("click", () => {
    setView(button.dataset.viewJump, { push: true, scroll: true });
    renderMajorLab();
    renderPlayers();
    renderCourses();
    renderModel();
  }));
  document.addEventListener("click", (event) => {
    const majorAction = event.target.closest("[data-major-action]");
    if (majorAction) {
      rankingCategory = "majors";
      rankingVisibleLimit = 50;
      if (majorAction.dataset.majorAction === "filter") {
        applyMajorPlayerPreset();
      } else {
        setView("players", { push: true, scroll: true });
        renderPlayers();
      }
      return;
    }
    const showMore = event.target.closest("[data-show-more-players]");
    if (showMore) {
      playerVisibleLimit += 48;
      renderPlayers();
      return;
    }
    const rankingCategoryButton = event.target.closest("[data-ranking-category]");
    if (rankingCategoryButton) {
      rankingCategory = rankingCategoryButton.dataset.rankingCategory || "index";
      rankingVisibleLimit = 10;
      renderPlayers();
      return;
    }
    const rankingLimitButton = event.target.closest("[data-ranking-limit]");
    if (rankingLimitButton) {
      rankingVisibleLimit = Number(rankingLimitButton.dataset.rankingLimit) || 10;
      renderPlayers();
      return;
    }
    const compare = event.target.closest("[data-compare-player]");
    if (compare) {
      toggleCompare(compare.dataset.comparePlayer);
      return;
    }
    const removeCompare = event.target.closest("[data-remove-compare]");
    if (removeCompare) {
      comparePlayerIds = comparePlayerIds.filter((id) => id !== removeCompare.dataset.removeCompare);
      renderPlayers();
      return;
    }
    if (event.target.closest("[data-clear-compare]")) {
      comparePlayerIds = [];
      renderPlayers();
      return;
    }
    const course = event.target.closest("[data-course-id]");
    if (course) openCourse(course.dataset.courseId).catch(showError);
  });
  $("#drawerClose").addEventListener("click", closeDrawer);
  $("#drawerBackdrop").addEventListener("click", closeDrawer);
  const search = $("#playerSearch");
  if (search) {
    search.addEventListener("input", () => {
      playerSearch = search.value.trim().toLowerCase();
      playerVisibleLimit = 48;
      rankingVisibleLimit = 10;
      renderPlayers();
    });
  }
  $$("[data-player-filter]").forEach((control) => {
    control.addEventListener("input", () => {
      playerFilters[control.dataset.playerFilter] = control.value;
      playerVisibleLimit = 48;
      rankingVisibleLimit = 10;
      renderPlayers();
    });
    control.addEventListener("change", () => {
      playerFilters[control.dataset.playerFilter] = control.value;
      playerVisibleLimit = 48;
      rankingVisibleLimit = 10;
      renderPlayers();
    });
  });
  const reset = $("#playerFilterReset");
  if (reset) {
    reset.addEventListener("click", () => {
      playerFilters = defaultPlayerFilters();
      syncPlayerFilterControls();
      playerVisibleLimit = 48;
      rankingVisibleLimit = 10;
      rankingCategory = "index";
      renderPlayers();
    });
  }
  window.addEventListener("popstate", () => {
    setView(viewFromHash());
    renderMajorLab();
    renderPlayers();
    renderCourses();
    renderModel();
  });
}

function viewFromHash() {
  const hash = location.hash.replace("#", "");
  if (hash === "model" || hash === "event" || hash === "overview") return "tournament";
  if (["players", "majors", "tournament", "courses", "data"].includes(hash)) return hash;
  return "players";
}

function closeDrawer() {
  $("#detailDrawer").hidden = true;
}

function showError(error) {
  setStatus(error.message, "bad");
  console.error(error);
}

async function boot() {
  bindEvents();
  try {
    [summary, eventBoard, playerPayload, filterPayload, coursePayload, modelPayload, healthPayload] = await Promise.all([
      api("/api/summary"),
      api("/api/event"),
      api("/api/player-cards?limit=5000"),
      api("/api/player-filters"),
      api("/api/course-cards?limit=60"),
      api("/api/model-board?limit=80"),
      api("/api/warehouse-health"),
    ]);
    buildProfileMaps();
    renderSummary();
    renderEvent();
    renderFilterConsole();
    renderMajorLab();
    renderPlayers();
    renderCourses();
    renderModel();
    renderHealth();
    setView(viewFromHash());
  } catch (error) {
    showError(error);
    $("#eventCard").innerHTML = empty("Database not ready. Run python golf_lab_import.py --seed-starter or import the PGA warehouse.");
  }
}

boot();
