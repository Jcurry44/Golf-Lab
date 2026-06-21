const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let summary = null;
let playerCards = [];
let filterRows = [];
let activePlayerId = "";

const staticMode = location.protocol === "file:" || location.hostname.endsWith("github.io");

function staticApiPath(path) {
  const url = new URL(path, location.origin);
  const endpoint = url.pathname.replace(/^\/api\/?/, "");
  if (endpoint === "player") {
    return `api/players/${encodeURIComponent(url.searchParams.get("id") || "")}.json`;
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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function fmt(value, digits = 0) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return numeric.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function signed(value, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return `${numeric > 0 ? "+" : ""}${fmt(numeric, digits)}`;
}

function pct(value, digits = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return `${fmt(numeric, digits)}%`;
}

function pctDecimal(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return pct(numeric * 100, 1);
}

function moneyOdds(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return numeric > 0 ? `+${Math.round(numeric)}` : `${Math.round(numeric)}`;
}

function fold(value) {
  return String(value || "").normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

function firstNumber(...values) {
  for (const value of values) {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric;
  }
  return null;
}

function hideStatus() {
  $("#status").hidden = true;
}

function showStatus(text) {
  const status = $("#status");
  status.textContent = text;
  status.hidden = false;
}

function eventName() {
  return summary?.selectedEvent?.event_name || "current event";
}

function playerRow(playerId) {
  return playerCards.find((row) => row.player_id === playerId) || null;
}

function latestProfile(detail) {
  const player = detail.player || {};
  const season = (detail.seasons?.rows || []).find((row) =>
    row.avg_sg_total !== null || row.driving_distance !== null || row.scoring_average !== null
  ) || {};
  return {
    ...player,
    ...season,
    player_id: player.player_id,
    player_name: player.player_name,
    rounds: firstNumber(season.rounds, player.rounds),
    avg_to_par: firstNumber(season.avg_to_par, player.avg_to_par),
    avg_sg_total: firstNumber(season.avg_sg_total, player.avg_sg_total),
    sg_t2g: firstNumber(season.sg_t2g, player.sg_t2g),
    sg_ott: firstNumber(season.sg_ott, player.sg_ott),
    sg_app: firstNumber(season.sg_app, player.sg_app),
    sg_arg: firstNumber(season.sg_arg, player.sg_arg),
    sg_putt: firstNumber(season.sg_putt, player.sg_putt),
    driving_distance: firstNumber(season.driving_distance, player.driving_distance),
    accuracy: firstNumber(season.accuracy, player.accuracy),
    gir: firstNumber(season.gir, player.gir),
    scrambling: firstNumber(season.scrambling, player.scrambling),
  };
}

function richProfile(detail, maxSeasons = 4) {
  const player = detail.player || {};
  const rows = (detail.seasons?.rows || [])
    .filter((row) =>
      row.avg_sg_total !== null ||
      row.driving_distance !== null ||
      row.gir !== null ||
      row.accuracy !== null ||
      row.scrambling !== null
    )
    .sort((a, b) => Number(b.season || 0) - Number(a.season || 0))
    .slice(0, maxSeasons);
  if (!rows.length) return { ...latestProfile(detail), richSeasonCount: 0, seasonLabel: "Latest available profile" };

  const profile = { ...player, richSeasonCount: rows.length };
  const numericFields = [
    "avg_sg_total",
    "sg_t2g",
    "sg_ott",
    "sg_app",
    "sg_arg",
    "sg_putt",
    "driving_distance",
    "accuracy",
    "gir",
    "scrambling",
    "scoring_average",
    "avg_to_par",
  ];
  for (const field of numericFields) {
    const values = rows.map((row) => Number(row[field])).filter(Number.isFinite);
    profile[field] = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  }
  const seasons = rows.map((row) => Number(row.season)).filter(Number.isFinite);
  const minSeason = Math.min(...seasons);
  const maxSeason = Math.max(...seasons);
  profile.season = maxSeason;
  profile.seasonLabel = minSeason === maxSeason
    ? `${maxSeason} rich profile`
    : `${minSeason}-${maxSeason} rich profile`;
  profile.profileSeasons = rows.map((row) => row.season).join(", ");
  profile.rounds = firstNumber(player.rounds, rows[0]?.rounds);
  return profile;
}

function profileForCard(row) {
  const recent = filterRows
    .filter((item) => item.player_id === row.player_id)
    .sort((a, b) => Number(b.season || 0) - Number(a.season || 0))[0] || {};
  return { ...row, ...recent, rank: row.rank, probability: row.probability };
}

function gradeClass(value, kind = "sg") {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "is-missing";
  if (kind === "score") return numeric <= 70 ? "is-good" : numeric <= 71.5 ? "is-watch" : "is-bad";
  if (kind === "distance") return numeric >= 305 ? "is-good" : numeric >= 292 ? "is-watch" : "is-bad";
  if (kind === "percent") return numeric >= 0.68 ? "is-good" : numeric >= 0.6 ? "is-watch" : "is-bad";
  return numeric >= 0.75 ? "is-good" : numeric >= 0 ? "is-watch" : "is-bad";
}

function meterWidth(value, kind = "sg") {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  if (kind === "distance") return Math.max(0, Math.min(100, ((numeric - 270) / 55) * 100));
  if (kind === "percent") return Math.max(0, Math.min(100, ((numeric - 0.48) / 0.28) * 100));
  if (kind === "score") return Math.max(0, Math.min(100, ((73 - numeric) / 5) * 100));
  return Math.max(0, Math.min(100, ((numeric + 1.6) / 3.8) * 100));
}

function statTile(label, value, formatter, kind, note) {
  const rendered = formatter(value);
  const cls = gradeClass(value, kind);
  const width = meterWidth(value, kind);
  return `
    <article class="score-tile ${cls}">
      <div><span>${escapeHtml(label)}</span><strong>${escapeHtml(rendered)}</strong></div>
      <div class="stat-meter"><i style="width:${width}%"></i></div>
      <small>${escapeHtml(note)}</small>
    </article>
  `;
}

function insight(label, value, note = "", tone = "") {
  return `
    <article class="insight-card ${tone}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(note)}</small>
    </article>
  `;
}

function renderHero(detail, row, profile) {
  const player = detail.player || {};
  const model = detail.model || row || {};
  const rank = firstNumber(model.rank, row?.rank);
  const probability = firstNumber(model.probability, row?.probability);
  const season = profile.seasonLabel || (profile.season ? `${profile.season} profile` : "career profile");
  $("#pcEyebrow").textContent = `${eventName()} | ${season}`;
  $("#pcName").textContent = player.player_name || row?.player_name || "Player";
  $("#pcHeroLabel").textContent = rank ? "Model rank" : "Profile SG";
  $("#pcHeroValue").textContent = rank ? `#${fmt(rank)}` : signed(profile.avg_sg_total);
  $("#pcHeroSub").textContent = probability
    ? `${pct(probability * 100, 1)} win probability | ${fmt(detail.player?.rounds)} imported scorecards`
    : `${fmt(detail.player?.rounds)} imported scorecards`;
  $("#pcChips").innerHTML = [
    player.country || row?.country || "PGA",
    model.confidence || row?.confidence || "coverage watch",
    profile.richSeasonCount ? `${fmt(profile.richSeasonCount)} rich stat seasons` : "rich stats pending",
    detail.player?.rounds ? `${fmt(detail.player.rounds)} imported scorecards` : "scorecard sample pending",
    profile.driving_distance ? `${fmt(profile.driving_distance, 1)} yd driving` : "distance coverage watch",
    profile.gir ? `${pctDecimal(profile.gir)} GIR` : "GIR coverage watch",
  ].filter(Boolean).map((chip) => `<span class="pc-chip">${escapeHtml(chip)}</span>`).join("");
}

function verdictText(detail, row, profile) {
  const model = detail.model || row || {};
  const rank = firstNumber(model.rank, row?.rank);
  const sg = firstNumber(profile.avg_sg_total);
  const projected = firstNumber(model.projected_to_par, row?.projected_to_par);
  const clauses = [];
  if (rank && rank <= 5) clauses.push(`top-tier model profile for ${eventName()}`);
  else if (rank && rank <= 20) clauses.push(`contender-tier model profile for ${eventName()}`);
  else if (rank) clauses.push(`needs the board to break his way from model rank #${fmt(rank)}`);
  else clauses.push("database profile is stronger than the current event model coverage");

  if (sg !== null && sg >= 1) clauses.push(`recent scoring baseline is excellent at ${signed(sg)} SG`);
  else if (sg !== null && sg >= 0) clauses.push(`recent scoring baseline is positive at ${signed(sg)} SG`);
  else if (sg !== null) clauses.push(`recent scoring baseline is under pressure at ${signed(sg)} SG`);

  if (projected !== null) clauses.push(`projected event score sits at ${signed(projected)} to par`);
  if (profile.gir !== null) clauses.push(`${pctDecimal(profile.gir)} GIR gives the approach profile a quick read`);
  return clauses.join(" | ") + ".";
}

function renderExplain(detail, row, profile) {
  const model = detail.model || row || {};
  const bullets = [];
  if (model.plain_english) bullets.push(["Model read", model.plain_english]);
  if (profile.sg_t2g !== null) bullets.push(["Tee-to-green", `${signed(profile.sg_t2g)} SG T2G anchors the ball-striking profile.`]);
  if (profile.sg_app !== null) bullets.push(["Approach", `${signed(profile.sg_app)} approach SG is the cleanest proxy for iron control.`]);
  if (profile.driving_distance !== null && profile.accuracy !== null) {
    bullets.push(["Driving blend", `${fmt(profile.driving_distance, 1)} yards with ${pctDecimal(profile.accuracy)} fairways shows the power/control tradeoff.`]);
  }
  if (detail.bestCourses?.rows?.[0]) {
    const best = detail.bestCourses.rows[0];
    bullets.push(["Course fit receipt", `${best.course}: ${signed(best.avg_to_par)} average to par across ${fmt(best.rounds)} tracked rounds.`]);
  }
  if (model.risk_flags) bullets.push(["Risk flag", model.risk_flags]);
  $("#pcExplain").innerHTML = bullets.slice(0, 5).map(([label, text]) => `
    <div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(text)}</span></div>
  `).join("");
}

function renderProjection(detail, row) {
  const model = detail.model || row || {};
  const probability = firstNumber(model.probability, row?.probability);
  const edge = firstNumber(model.edge_probability, row?.edge_probability);
  $("#projectionTitle").textContent = eventName();
  $("#projectionBadge").textContent = model.confidence || row?.confidence || "Coverage watch";
  $("#projectionGrid").innerHTML = [
    insight("Model rank", model.rank ? `#${fmt(model.rank)}` : "--", "winner market"),
    insight("Win probability", probability !== null ? pct(probability * 100, 1) : "--", "model implied"),
    insight("Projected to par", signed(firstNumber(model.projected_to_par, row?.projected_to_par)), "event finish"),
    insight("Fair odds", moneyOdds(model.fair_odds_american), "model price"),
    insight("Market edge", edge !== null ? `${signed(edge * 100, 1)} pts` : "--", "model minus market", edge > 0 ? "is-good" : ""),
    insight("Confidence", model.confidence || row?.confidence || "Watch", "sample and source quality"),
  ].join("");
}

function renderScorecard(profile) {
  $("#scorecardBadge").textContent = profile.seasonLabel || (profile.season ? `${profile.season} season` : "Latest profile");
  $("#scorecardGrid").innerHTML = [
    statTile("SG Total", profile.avg_sg_total, signed, "sg", "overall performance"),
    statTile("Tee to Green", profile.sg_t2g, signed, "sg", "ball-striking base"),
    statTile("Off Tee", profile.sg_ott, signed, "sg", "driver value"),
    statTile("Approach", profile.sg_app, signed, "sg", "iron control"),
    statTile("Around Green", profile.sg_arg, signed, "sg", "miss recovery"),
    statTile("Putting", profile.sg_putt, signed, "sg", "green conversion"),
    statTile("Distance", profile.driving_distance, (v) => Number.isFinite(Number(v)) ? `${fmt(v, 1)} yd` : "--", "distance", "measured driving"),
    statTile("Fairways", profile.accuracy, pctDecimal, "percent", "accuracy"),
    statTile("GIR", profile.gir, pctDecimal, "percent", "greens in regulation"),
    statTile("Scramble", profile.scrambling, pctDecimal, "percent", "miss recovery"),
    statTile("Scoring", profile.scoring_average, (v) => Number.isFinite(Number(v)) ? fmt(v, 2) : "--", "score", "raw average"),
    statTile("Scorecards", profile.rounds, (v) => fmt(v), "sg", "imported round sample"),
  ].join("");
}

function renderSeasons(detail) {
  const rows = detail.seasons?.rows || [];
  $("#seasonBadge").textContent = rows.length ? `${rows.length} seasons loaded` : "No season rows";
  if (!rows.length) {
    $("#seasonBars").innerHTML = `<div class="empty">No season profile rows yet.</div>`;
    return;
  }
  const values = rows.map((row) => firstNumber(row.avg_sg_total, row.avg_to_par ? -row.avg_to_par : null)).filter((value) => value !== null);
  const max = Math.max(1, ...values.map((value) => Math.abs(value)));
  $("#seasonBars").innerHTML = rows.map((row) => {
    const value = firstNumber(row.avg_sg_total, row.avg_to_par ? -row.avg_to_par : null);
    const width = value === null ? 0 : Math.min(100, (Math.abs(value) / max) * 100);
    const label = row.avg_sg_total !== null ? `${signed(row.avg_sg_total)} SG` : `${signed(row.avg_to_par)} to par`;
    const tone = value === null ? "is-missing" : value >= 0 ? "is-good" : "is-bad";
    return `
      <article class="season-row">
        <strong>${escapeHtml(row.season)}</strong>
        <div class="season-meter ${tone}"><i style="width:${width}%"></i></div>
        <span>${escapeHtml(label)}</span>
        <small>${fmt(row.rounds)} rounds | ${row.driving_distance ? `${fmt(row.driving_distance, 1)} yd` : "distance --"} | GIR ${pctDecimal(row.gir)}</small>
      </article>
    `;
  }).join("");
}

function renderCourseLens(target, rows, emptyText) {
  $(target).innerHTML = (rows || []).map((row) => `
    <article class="course-row">
      <div>
        <strong>${escapeHtml(row.course || "Course")}</strong>
        <span>${fmt(row.rounds)} rounds</span>
      </div>
      <div>
        <b>${signed(row.avg_to_par)}</b>
        <small>${signed(row.avg_sg)} SG</small>
      </div>
    </article>
  `).join("") || `<div class="empty">${escapeHtml(emptyText)}</div>`;
}

function renderRounds(detail) {
  const rows = detail.rounds?.rows || [];
  if (!rows.length) {
    $("#roundTable").innerHTML = `<div class="empty">No round scorecards loaded yet.</div>`;
    return;
  }
  $("#roundTable").innerHTML = `
    <table class="data-table">
      <thead><tr><th>Date</th><th>Event</th><th>Course</th><th>R</th><th>Score</th><th>To par</th><th>SG</th></tr></thead>
      <tbody>
        ${rows.map((row) => `
          <tr>
            <td>${escapeHtml(row.round_date || "")}</td>
            <td>${escapeHtml(row.event_name || "")}</td>
            <td>${escapeHtml(row.course || "")}</td>
            <td>${escapeHtml(row.round_number || "")}</td>
            <td>${escapeHtml(row.score || "--")}</td>
            <td>${signed(row.to_par, 0)}</td>
            <td>${signed(row.sg_total)}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderReceipts(detail) {
  const coverage = detail.coverage || {};
  const latest = summary?.latestFetch || {};
  const richSeasons = (detail.seasons?.rows || []).filter((row) =>
    row.avg_sg_total !== null || row.driving_distance !== null || row.gir !== null
  );
  const coverageRows = [
    ["Round scorecards", coverage.hasRoundScorecards, `${fmt(detail.player?.rounds)} tracked rounds`],
    ["Rich season profile", richSeasons.length >= 3, `${fmt(richSeasons.length)} public stat seasons loaded`],
    ["Strokes gained", coverage.hasStrokesGained, "model baseline"],
    ["Driving distance", coverage.hasDrivingDistance, "public PGA stat lane"],
    ["Fairway accuracy", coverage.hasAccuracy, "public PGA stat lane"],
    ["GIR", coverage.hasGir, "public PGA stat lane"],
    ["Scrambling", coverage.hasScrambling, "public PGA stat lane"],
  ];
  $("#receiptGrid").innerHTML = `
    <div class="coverage-list">
      ${coverageRows.map(([label, ok, note]) => `
        <div class="${ok ? "is-good" : "is-watch"}"><strong>${escapeHtml(label)}</strong><span>${ok ? "Loaded" : "Coverage watch"}</span><small>${escapeHtml(note)}</small></div>
      `).join("")}
    </div>
    <article class="receipt-note">
      <strong>Latest source touch</strong>
      <span>${escapeHtml(latest.provider || "source pending")}</span>
      <small>${escapeHtml([latest.endpoint, latest.fetched_at].filter(Boolean).join(" | "))}</small>
    </article>
    <article class="receipt-note">
      <strong>Honest limitation</strong>
      <span>Distance, GIR, fairway, and scrambling coverage is strongest in recent PGA public stat seasons.</span>
      <small>Blank fields are treated as missing data, not zero performance.</small>
    </article>
  `;
}

function renderPlayer(detail) {
  const row = playerRow(detail.player.player_id);
  const profile = richProfile(detail);
  $("#emptyState").hidden = true;
  $("#playerCard").hidden = false;
  renderHero(detail, row, profile);
  $("#pcVerdict").textContent = verdictText(detail, row, profile);
  renderExplain(detail, row, profile);
  renderProjection(detail, row);
  renderScorecard(profile);
  renderSeasons(detail);
  renderCourseLens("#bestCourses", detail.bestCourses?.rows || [], "Need more repeat-course history.");
  renderCourseLens("#worstCourses", detail.worstCourses?.rows || [], "Need more repeat-course history.");
  renderRounds(detail);
  renderReceipts(detail);
}

async function loadPlayer(playerId, push = true) {
  if (!playerId) return;
  activePlayerId = playerId;
  showStatus("Building player card...");
  try {
    const eventId = summary?.selectedEvent?.event_id ? `&event_id=${encodeURIComponent(summary.selectedEvent.event_id)}` : "";
    const detail = await api(`/api/player?id=${encodeURIComponent(playerId)}${eventId}`);
    hideStatus();
    renderPlayer(detail);
    const input = $("#playerSearchInput");
    input.value = detail.player?.player_name || "";
    if (push) history.pushState({ playerId }, "", `./player.html?id=${encodeURIComponent(playerId)}`);
  } catch (error) {
    showStatus(error.message);
  }
}

function matchPlayers(query) {
  const q = fold(query);
  if (!q) return [];
  return playerCards
    .map((row) => {
      const name = fold(row.player_name);
      let score = 999;
      if (name === q) score = 0;
      else if (name.startsWith(q)) score = 1;
      else if (name.includes(q)) score = 2;
      return [score, row];
    })
    .filter(([score]) => score < 999)
    .sort((a, b) => a[0] - b[0] || (Number(a[1].rank || 9999) - Number(b[1].rank || 9999)))
    .slice(0, 8)
    .map(([, row]) => row);
}

function renderTypeahead() {
  const box = $("#typeahead");
  const rows = matchPlayers($("#playerSearchInput").value);
  if (!rows.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.innerHTML = rows.map((row) => `
    <button type="button" data-pick-player="${escapeHtml(row.player_id)}">
      <span>${escapeHtml(row.player_name)}</span>
      <small>${row.rank ? `#${escapeHtml(row.rank)} model` : escapeHtml(row.country || "profile")}</small>
    </button>
  `).join("");
  box.hidden = false;
}

function renderEmpty() {
  const rows = playerCards.slice(0, 12);
  $("#emptyState").hidden = false;
  $("#emptyState").innerHTML = `
    <div class="empty-hero">
      <p class="eyebrow">Top model cards</p>
      <h2>${escapeHtml(eventName())}</h2>
      <span>${fmt(summary?.counts?.players)} players | ${fmt(summary?.counts?.rounds)} scorecards | ${fmt(summary?.counts?.strokes_gained)} SG rows</span>
    </div>
    <div class="browse-grid">
      ${rows.map((row) => {
        const profile = profileForCard(row);
        return `
          <a class="browse-card" href="./player.html?id=${encodeURIComponent(row.player_id)}" data-browse-player="${escapeHtml(row.player_id)}">
            <span>${row.rank ? `#${escapeHtml(row.rank)}` : "DB"}</span>
            <strong>${escapeHtml(row.player_name)}</strong>
            <small>${escapeHtml(row.plain_english || `${signed(profile.avg_sg_total)} SG profile`)}</small>
          </a>
        `;
      }).join("")}
    </div>
  `;
}

function findRequestedPlayer() {
  const params = new URLSearchParams(location.search);
  const id = params.get("id");
  if (id) return id;
  const name = params.get("name");
  if (!name) return "";
  const matches = matchPlayers(name);
  return matches[0]?.player_id || "";
}

function bindEvents() {
  $("#playerSearchInput").addEventListener("input", renderTypeahead);
  $("#playerSearchForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const match = matchPlayers($("#playerSearchInput").value)[0];
    if (match) loadPlayer(match.player_id);
    else showStatus("No matching player in the current Golf Lab export.");
    $("#typeahead").hidden = true;
  });
  document.addEventListener("click", (event) => {
    const pick = event.target.closest("[data-pick-player]");
    if (pick) {
      loadPlayer(pick.dataset.pickPlayer);
      $("#typeahead").hidden = true;
      return;
    }
    if (!event.target.closest(".player-search-box")) $("#typeahead").hidden = true;
  });
  window.addEventListener("popstate", () => {
    const requested = findRequestedPlayer();
    if (requested && requested !== activePlayerId) loadPlayer(requested, false);
  });
}

async function boot() {
  bindEvents();
  try {
    const [summaryPayload, cardsPayload, filtersPayload] = await Promise.all([
      api("/api/summary"),
      api("/api/player-cards?limit=5000"),
      api("/api/player-filters"),
    ]);
    summary = summaryPayload;
    playerCards = cardsPayload.rows || [];
    filterRows = filtersPayload.rows || [];
    const requested = findRequestedPlayer();
    if (requested) {
      await loadPlayer(requested, false);
    } else {
      hideStatus();
      renderEmpty();
    }
  } catch (error) {
    showStatus(error.message);
  }
}

boot();
