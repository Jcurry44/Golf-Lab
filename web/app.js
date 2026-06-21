const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let currentView = "overview";
let summary = null;
let eventBoard = null;
let playerPayload = null;
let coursePayload = null;
let modelPayload = null;
let healthPayload = null;

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

function signed(value, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return `${numeric > 0 ? "+" : ""}${fmt(numeric, digits)}`;
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
  $("#railProof").textContent = `${fmt(counts.rounds)} rounds | ${fmt(counts.model_predictions)} model rows`;
  setStatus(summary.readiness || "ready", summary.readiness === "model-ready" ? "good" : "watch");

  const event = summary.selectedEvent || {};
  $("#eventCard").innerHTML = `
    <p class="eyebrow">Current modeled event</p>
    <h2>${escapeHtml(event.event_name || "No event loaded")}</h2>
    <div class="event-meta">
      <span>${escapeHtml(event.course_name || "Course pending")}</span>
      <span>${escapeHtml([event.start_date, event.end_date].filter(Boolean).join(" to "))}</span>
    </div>
    <div class="event-kpis">
      ${metric("Players", counts.players)}
      ${metric("Rounds", counts.rounds)}
      ${metric("Odds", counts.odds_snapshots)}
      ${metric("Sources", counts.source_fetches)}
    </div>
  `;

  $("#metricGrid").innerHTML = [
    ["Players", counts.players, "profile rows"],
    ["Events", counts.events, "schedule"],
    ["Scorecards", counts.rounds, "round-level"],
    ["SG Rows", counts.strokes_gained, "derived model"],
    ["Markets", counts.odds_snapshots, "odds snapshots"],
    ["Predictions", counts.model_predictions, "owned model"],
  ].map(([label, value, note]) => `
    <article class="metric-card">
      <span>${escapeHtml(label)}</span>
      <strong>${fmt(value)}</strong>
      <small>${escapeHtml(note)}</small>
    </article>
  `).join("");
}

function metric(label, value, note = "") {
  return `<div><span>${escapeHtml(label)}</span><strong>${fmt(value)}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ""}</div>`;
}

function renderEvent() {
  const event = eventBoard.event || {};
  const weather = eventBoard.weather || [];
  const avgWind = weather.length
    ? weather.reduce((sum, row) => sum + (Number(row.wind_mph) || 0), 0) / weather.length
    : null;
  $("#setupSignal").textContent = event.course_name
    ? `${event.course_name} ${avgWind ? `| ${fmt(avgWind)} mph wind` : ""}`
    : "Waiting for event data";
  $("#setupCopy").textContent = event.location
    ? `${event.location}. Course, field, weather, odds, and model context come from the SQLite warehouse.`
    : "Import the PGA warehouse to populate the live event desk.";
}

function renderPlayers(limit = currentView === "players" ? 48 : 8) {
  const rows = (playerPayload.rows || []).slice(0, limit);
  $("#playerCards").innerHTML = rows.map((row) => `
    <button type="button" class="player-card" data-player-id="${escapeHtml(row.player_id)}">
      <div class="rank">#${escapeHtml(row.rank || "--")}</div>
      <div>
        <h3>${escapeHtml(row.player_name)}</h3>
        <p>${escapeHtml(row.country || "PGA")} | ${fmt(row.rounds)} tracked rounds</p>
      </div>
      <div class="card-stats">
        ${metric("Win", pct(Number(row.probability || 0) * 100))}
        ${metric("Proj", signed(row.projected_to_par))}
        ${metric("SG", signed(row.avg_sg_total))}
      </div>
      <p class="plain">${escapeHtml(row.plain_english || "Model explanation pending.")}</p>
    </button>
  `).join("") || empty("No player cards yet.");
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
        <button type="button" class="model-row" data-player-id="${escapeHtml(row.player_id)}">
          <span class="model-rank">${escapeHtml(row.rank || "--")}</span>
          <strong>${escapeHtml(row.player_name)}</strong>
          <span>${pct(row.probability_pct)}</span>
          <span>${signed(row.projected_to_par)}</span>
          <em>${escapeHtml(row.confidence || "Watch")}</em>
          <p>${escapeHtml(row.plain_english || "Reasoning pending.")}</p>
        </button>
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
    const player = event.target.closest("[data-player-id]");
    if (player) openPlayer(player.dataset.playerId).catch(showError);
    const course = event.target.closest("[data-course-id]");
    if (course) openCourse(course.dataset.courseId).catch(showError);
  });
  $("#drawerClose").addEventListener("click", closeDrawer);
  $("#drawerBackdrop").addEventListener("click", closeDrawer);
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
    [summary, eventBoard, playerPayload, coursePayload, modelPayload, healthPayload] = await Promise.all([
      api("/api/summary"),
      api("/api/event"),
      api("/api/player-cards?limit=80"),
      api("/api/course-cards?limit=60"),
      api("/api/model-board?limit=80"),
      api("/api/warehouse-health"),
    ]);
    renderSummary();
    renderEvent();
    renderPlayers();
    renderCourses();
    renderModel();
    renderHealth();
    setView("overview");
  } catch (error) {
    showError(error);
    $("#eventCard").innerHTML = empty("Database not ready. Run python golf_lab_import.py --seed-starter or import the PGA warehouse.");
  }
}

boot();
