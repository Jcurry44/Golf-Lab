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
let seasonProfiles = new Map();
let careerProfiles = new Map();
let playerFilters = {
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
  strength: "any",
  sort: "model",
};

const staticMode = location.protocol === "file:" || location.hostname.endsWith("github.io");

function staticApiPath(path) {
  const url = new URL(path, location.origin);
  const endpoint = url.pathname.replace(/^\/api\/?/, "");
  if (endpoint === "player") {
    return `api/players/${encodeURIComponent(url.searchParams.get("id") || "")}.json`;
  }
  if (endpoint === "course") {
    return `api/courses/${encodeURIComponent(url.searchParams.get("id") || "")}.json`;
  }
  return `api/${endpoint}.json`;
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

function signed(value, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return `${numeric > 0 ? "+" : ""}${fmt(numeric, digits)}`;
}

function numeric(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function filterNumber(key) {
  const value = numeric(playerFilters[key]);
  return value === null ? null : value;
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

function setView(view) {
  currentView = view;
  $$(".nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  $$("[data-panel]").forEach((panel) => {
    const views = panel.dataset.panel.split(/\s+/);
    panel.hidden = !views.includes(view);
  });
}

function renderSummary() {
  const counts = summary.counts || {};
  $("#railProof").textContent = `${fmt(counts.players)} players | ${fmt(counts.rounds)} rounds`;
  setStatus(summary.readiness || "ready", summary.readiness === "model-ready" ? "good" : "watch");

  const event = summary.selectedEvent || {};
  $("#topEyebrow").textContent = `${event.event_name || "Modeled event"} | ${summary.readiness || "loading"}`;
  $("#heroTitle").textContent = "Tour intelligence. Player scorecards first.";
  $("#heroSubtitle").textContent = `${fmt(counts.players)} player profiles | ${fmt(counts.fields)} current-event entries | ${fmt(counts.rounds)} scorecards | ${fmt(counts.strokes_gained)} SG rows.`;
  $("#eventCard").innerHTML = `
    <p class="eyebrow">Event context</p>
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
  if (!rounds) return;
  target.rounds = (target.rounds || 0) + rounds;
  for (const field of fields) {
    const value = numeric(row[field]);
    if (value === null) continue;
    target[`${field}_weighted`] = (target[`${field}_weighted`] || 0) + value * rounds;
    target[`${field}_rounds`] = (target[`${field}_rounds`] || 0) + rounds;
  }
  if (!target.last_round || String(row.last_round || "") > target.last_round) {
    target.last_round = row.last_round;
  }
}

function buildProfileMaps() {
  seasonProfiles = new Map();
  const career = new Map();
  const weightedFields = ["scoring_average", "avg_to_par", "avg_sg_total", "sg_t2g", "sg_ott", "sg_app", "sg_arg", "sg_putt"];
  for (const row of filterPayload?.rows || []) {
    seasonProfiles.set(`${row.player_id}|${row.season}`, row);
    if (!career.has(row.player_id)) career.set(row.player_id, { player_id: row.player_id, season: "all" });
    weightedMerge(career.get(row.player_id), row, weightedFields);
  }
  careerProfiles = new Map();
  for (const [playerId, row] of career.entries()) {
    const profile = { player_id: playerId, season: "all", rounds: row.rounds || 0, last_round: row.last_round };
    for (const field of weightedFields) {
      const rounds = row[`${field}_rounds`] || 0;
      profile[field] = rounds ? Number((row[`${field}_weighted`] / rounds).toFixed(2)) : null;
    }
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
    if (playerFilters.sort === "distance") return compareDesc(a, b, "driving_distance") || a.row.player_name.localeCompare(b.row.player_name);
    if (playerFilters.sort === "gir") return compareDesc(a, b, "gir") || a.row.player_name.localeCompare(b.row.player_name);
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
  if (!passesNumericCeiling(profile, "avg_to_par", "maxToPar")) return false;
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

function renderFeaturedPlayer(row = (playerPayload.rows || [])[0], profile = row ? profileForRow(row) : null) {
  if (!row) {
    $("#featuredPlayer").innerHTML = empty("No player scorecard loaded yet.");
    return;
  }
  const seasonLabel = playerFilters.season === "all" ? "Career profile" : `${playerFilters.season} profile`;
  $("#featuredPlayer").innerHTML = `
    <div class="featured-inner">
      <div>
        <span class="featured-rank">${escapeHtml(row.rank ? `Model rank #${row.rank}` : seasonLabel)}</span>
        <h2>${escapeHtml(row.player_name)}</h2>
        <p class="featured-copy">${escapeHtml(row.plain_english || "Model explanation pending.")}</p>
        <a class="featured-link" href="./player.html?id=${encodeURIComponent(row.player_id)}">Open full scorecard</a>
      </div>
      <div class="featured-metrics">
        ${metric("Win probability", pct(Number(row.probability || 0) * 100))}
        ${metric("Profile SG", signed(profile?.avg_sg_total))}
        ${metric("Tracked rounds", profile?.rounds)}
      </div>
    </div>
  `;
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

function renderPlayers(limit = currentView === "players" ? playerVisibleLimit : 8) {
  const allRows = playerPayload.rows || [];
  const decoratedRows = sortDecoratedPlayers(allRows
    .filter(playerMatchesSearch)
    .map((row) => ({ row, profile: profileForRow(row) }))
    .filter(({ row, profile }) => playerPassesFilters(row, profile)));
  const rows = decoratedRows.slice(0, limit);
  if (rows[0]) {
    renderFeaturedPlayer(rows[0].row, rows[0].profile);
  } else {
    renderFeaturedPlayer(null, null);
  }
  const count = $("#playerCount");
  if (count) {
    const label = playerFilters.season === "all" ? "all seasons" : `${playerFilters.season} season`;
    count.textContent = `${fmt(decoratedRows.length)} of ${fmt(allRows.length)} player cards | ${label}`;
  }
  $("#playerCards").innerHTML = rows.map(({ row, profile }) => `
    <a class="player-card" href="./player.html?id=${encodeURIComponent(row.player_id)}">
      <div class="player-card-head">
        <div class="rank">${row.rank ? `#${escapeHtml(row.rank)}` : "DB"}</div>
        <div>
          <h3>${escapeHtml(row.player_name)}</h3>
          <p class="player-card-meta">${escapeHtml(row.country || "PGA")} | ${fmt(profile.rounds)} tracked rounds${playerFilters.season === "all" ? "" : ` | ${escapeHtml(playerFilters.season)}`}</p>
        </div>
        <div class="status-pill" data-tone="${confidenceTone(row.confidence)}">${escapeHtml(row.modeled ? "Model" : (row.confidence || "watch"))}</div>
      </div>
      <div class="card-stats">
        ${metric("Win", pct(Number(row.probability || 0) * 100))}
        ${metric("SG", signed(profile.avg_sg_total))}
        ${metric("Avg", signed(profile.avg_to_par))}
      </div>
      <p class="plain">${escapeHtml(row.plain_english || "Model explanation pending.")}</p>
      <div class="scorecard-footer">
        <span>Drive <strong>${profile.driving_distance ? `${fmt(profile.driving_distance, 1)} yd` : "--"}</strong></span>
        <span>GIR <strong>${pctDecimal(profile.gir)}</strong></span>
        <span>Score <strong>${profile.scoring_average ? fmt(profile.scoring_average, 2) : "--"}</strong></span>
        <span>Scramble <strong>${pctDecimal(profile.scrambling)}</strong></span>
      </div>
    </a>
  `).join("") || empty("No player cards match those filters.");
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

function renderModel(limit = currentView === "model" ? 40 : 10) {
  const rows = (modelPayload.rows || []).slice(0, limit);
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
  $("#warehouseHealth").innerHTML = `
    <div class="health-band ${blockers.length ? "watch" : "good"}">
      <strong>${escapeHtml(healthPayload.grade || "setup")}</strong>
      <span>${blockers.length ? blockers.join(" | ") : "Warehouse has the minimum model-ready lanes."}</span>
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
      ${detail.rounds.rows.map((row) => `<div class="mini-row"><strong>${escapeHtml(row.event_name || row.course)}</strong><span>R${escapeHtml(row.round_number)} ${escapeHtml(row.score)} (${signed(row.to_par, 0)})</span></div>`).join("")}
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
    setView(button.dataset.view);
    renderPlayers();
    renderCourses();
    renderModel();
  }));
  $$("[data-view-jump]").forEach((button) => button.addEventListener("click", () => {
    setView(button.dataset.viewJump);
    renderPlayers();
    renderCourses();
    renderModel();
  }));
  document.addEventListener("click", (event) => {
    const showMore = event.target.closest("[data-show-more-players]");
    if (showMore) {
      playerVisibleLimit += 48;
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
      renderPlayers();
    });
  }
  $$("[data-player-filter]").forEach((control) => {
    control.addEventListener("input", () => {
      playerFilters[control.dataset.playerFilter] = control.value;
      playerVisibleLimit = 48;
      renderPlayers();
    });
    control.addEventListener("change", () => {
      playerFilters[control.dataset.playerFilter] = control.value;
      playerVisibleLimit = 48;
      renderPlayers();
    });
  });
  const reset = $("#playerFilterReset");
  if (reset) {
    reset.addEventListener("click", () => {
      playerFilters = {
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
        strength: "any",
        sort: "model",
      };
      $$("[data-player-filter]").forEach((control) => {
        control.value = playerFilters[control.dataset.playerFilter] ?? "";
      });
      playerVisibleLimit = 48;
      renderPlayers();
    });
  }
}

function viewFromHash() {
  const hash = location.hash.replace("#", "");
  if (["players", "model", "courses", "overview", "data"].includes(hash)) return hash;
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
