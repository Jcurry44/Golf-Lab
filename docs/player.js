const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

let summary = null;
let playerCards = [];
let filterRows = [];
let activePlayerId = "";
let activeGradeKey = "sg_total";
let activeDetail = null;
let activeProfile = null;
let activeView = "overview";

const staticMode = location.protocol === "file:" || location.hostname.endsWith("github.io");
const BUILD_VERSION = "20260621-drilldowns";

function versionedPath(path) {
  return `${path}${path.includes("?") ? "&" : "?"}v=${BUILD_VERSION}`;
}

function staticApiPath(path) {
  const url = new URL(path, location.origin);
  const endpoint = url.pathname.replace(/^\/api\/?/, "");
  if (endpoint === "player") {
    return versionedPath(`api/players/${encodeURIComponent(url.searchParams.get("id") || "")}.json`);
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

function roundScoreLabel(row) {
  const score = Number(row?.score);
  if (!Number.isFinite(score)) return "--";
  return score >= 55 && score <= 95 ? fmt(score) : `${fmt(score)} pts`;
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

function weightedAverage(rows, field, weightField = "rounds") {
  let numerator = 0;
  let denominator = 0;
  const fallbackValues = [];
  for (const row of rows) {
    const value = Number(row[field]);
    if (!Number.isFinite(value)) continue;
    fallbackValues.push(value);
    const weight = Number(row[weightField]);
    if (Number.isFinite(weight) && weight > 0) {
      numerator += value * weight;
      denominator += weight;
    }
  }
  if (denominator) return numerator / denominator;
  return fallbackValues.length ? fallbackValues.reduce((sum, value) => sum + value, 0) / fallbackValues.length : null;
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
    scoring_rounds: firstNumber(season.scoring_rounds, player.scoring_rounds),
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
    ["avg_sg_total", "rounds"],
    ["sg_t2g", "rounds"],
    ["sg_ott", "rounds"],
    ["sg_app", "rounds"],
    ["sg_arg", "rounds"],
    ["sg_putt", "rounds"],
    ["driving_distance", "rounds"],
    ["accuracy", "rounds"],
    ["gir", "rounds"],
    ["scrambling", "rounds"],
    ["scoring_average", "scoring_rounds"],
    ["avg_to_par", "rounds"],
  ];
  for (const [field, weightField] of numericFields) {
    profile[field] = weightedAverage(rows, field, weightField);
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
  profile.scoring_rounds = rows.reduce((sum, row) => sum + (Number(row.scoring_rounds) || 0), 0);
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
  if (kind === "volume") return numeric >= 120 ? "is-good" : numeric >= 40 ? "is-watch" : "is-bad";
  if (kind === "score") return numeric <= 70 ? "is-good" : numeric <= 71.5 ? "is-watch" : "is-bad";
  if (kind === "distance") return numeric >= 305 ? "is-good" : numeric >= 292 ? "is-watch" : "is-bad";
  if (kind === "percent") return numeric >= 0.68 ? "is-good" : numeric >= 0.6 ? "is-watch" : "is-bad";
  return numeric >= 0.75 ? "is-good" : numeric >= 0 ? "is-watch" : "is-bad";
}

function meterWidth(value, kind = "sg") {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  if (kind === "volume") return Math.max(0, Math.min(100, (numeric / 240) * 100));
  if (kind === "distance") return Math.max(0, Math.min(100, ((numeric - 270) / 55) * 100));
  if (kind === "percent") return Math.max(0, Math.min(100, ((numeric - 0.48) / 0.28) * 100));
  if (kind === "score") return Math.max(0, Math.min(100, ((73 - numeric) / 5) * 100));
  return Math.max(0, Math.min(100, ((numeric + 1.6) / 3.8) * 100));
}

function scorecardStats(profile) {
  return [
    { key: "sg_total", label: "SG Total", value: profile.avg_sg_total, formatter: signed, kind: "sg", note: "overall performance" },
    { key: "sg_t2g", label: "Tee to Green", value: profile.sg_t2g, formatter: signed, kind: "sg", note: "ball-striking base" },
    { key: "sg_ott", label: "Off Tee", value: profile.sg_ott, formatter: signed, kind: "sg", note: "driver value" },
    { key: "sg_app", label: "Approach", value: profile.sg_app, formatter: signed, kind: "sg", note: "iron control" },
    { key: "sg_arg", label: "Around Green", value: profile.sg_arg, formatter: signed, kind: "sg", note: "miss recovery" },
    { key: "sg_putt", label: "Putting", value: profile.sg_putt, formatter: signed, kind: "sg", note: "green conversion" },
    { key: "driving_distance", label: "Distance", value: profile.driving_distance, formatter: (v) => Number.isFinite(Number(v)) ? `${fmt(v, 1)} yd` : "--", kind: "distance", note: "measured driving" },
    { key: "accuracy", label: "Fairways", value: profile.accuracy, formatter: pctDecimal, kind: "percent", note: "accuracy" },
    { key: "gir", label: "GIR", value: profile.gir, formatter: pctDecimal, kind: "percent", note: "greens in regulation" },
    { key: "scrambling", label: "Scramble", value: profile.scrambling, formatter: pctDecimal, kind: "percent", note: "miss recovery" },
    { key: "scoring_average", label: "Scoring", value: profile.scoring_average, formatter: (v) => Number.isFinite(Number(v)) ? fmt(v, 2) : "--", kind: "score", note: `${fmt(profile.scoring_rounds)} trusted scoring rounds` },
    { key: "scorecards", label: "Scorecards", value: profile.rounds, formatter: (v) => fmt(v), kind: "volume", note: "imported round sample" },
  ];
}

function statTile(stat, explanation, active) {
  const { key, label, value, formatter, kind, note } = stat;
  const rendered = formatter(value);
  const cls = gradeClass(value, kind);
  const width = meterWidth(value, kind);
  return `
    <button type="button" class="score-tile ${cls} ${active ? "is-active" : ""}" data-grade-key="${escapeHtml(key)}" data-drill="stat" data-drill-key="${escapeHtml(key)}" aria-pressed="${active ? "true" : "false"}">
      <div><span>${escapeHtml(label)}</span><strong>${escapeHtml(rendered)}</strong></div>
      <div class="stat-meter"><i style="width:${width}%"></i></div>
      <small>${escapeHtml(explanation?.headline || note)}</small>
    </button>
  `;
}

function insight(label, value, note = "", tone = "", key = "") {
  return `
    <button type="button" class="insight-card ${tone}" data-drill="projection" data-drill-key="${escapeHtml(key || label)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(note)}</small>
    </button>
  `;
}

function trustProfile(detail, profile) {
  const coverage = detail.coverage || {};
  const richSeasons = (detail.seasons?.rows || []).filter((row) =>
    row.avg_sg_total !== null || row.driving_distance !== null || row.gir !== null
  ).length;
  const trustedScoring = Boolean(coverage.hasTrustedScoring)
    || ((Number(profile.scoring_rounds) || 0) >= 20 && Number(profile.scoring_average) >= 60 && Number(profile.scoring_average) <= 80);
  const checks = [
    Boolean(coverage.hasRoundScorecards),
    richSeasons >= 3,
    Boolean(coverage.hasDrivingDistance),
    Boolean(coverage.hasGir),
    trustedScoring,
    (detail.majorProfile?.summary?.rounds || 0) >= 8,
    (detail.courseDna?.toughRounds || 0) >= 8,
  ];
  const loaded = checks.filter(Boolean).length;
  return {
    loaded,
    total: checks.length,
    label: loaded >= 6 ? "Premium trust" : loaded >= 4 ? "Strong profile" : loaded >= 2 ? "Usable profile" : "Coverage watch",
    note: `${loaded}/${checks.length} core lanes loaded`,
  };
}

function profileEdge(profile) {
  const candidates = [
    ["Approach", profile.sg_app, signed(profile.sg_app)],
    ["Tee to green", profile.sg_t2g, signed(profile.sg_t2g)],
    ["Off tee", profile.sg_ott, signed(profile.sg_ott)],
    ["Putting", profile.sg_putt, signed(profile.sg_putt)],
    ["Distance", profile.driving_distance, profile.driving_distance ? `${fmt(profile.driving_distance, 1)} yd` : "--"],
    ["GIR", profile.gir, pctDecimal(profile.gir)],
  ].filter(([, value]) => Number.isFinite(Number(value)));
  if (!candidates.length) return { label: "Skill edge", value: "--", note: "rich stat lane pending" };
  const ranked = candidates.sort((a, b) => {
    const av = a[0] === "Distance" ? (Number(a[1]) - 290) / 20 : a[0] === "GIR" ? (Number(a[1]) - 0.62) * 10 : Number(a[1]);
    const bv = b[0] === "Distance" ? (Number(b[1]) - 290) / 20 : b[0] === "GIR" ? (Number(b[1]) - 0.62) * 10 : Number(b[1]);
    return bv - av;
  })[0];
  return { label: ranked[0], value: ranked[2], note: "best loaded skill lane" };
}

function snapshotCard(label, value, note, tone = "", key = "") {
  return `
    <button type="button" class="snapshot-card ${tone}" data-drill="snapshot" data-drill-key="${escapeHtml(key || label)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(note)}</small>
    </button>
  `;
}

function renderSnapshot(detail, profile) {
  const trend = detail.recentVsBaseline || {};
  const major = detail.majorProfile?.summary || {};
  const bestCourse = detail.bestCourses?.rows?.[0];
  const trust = trustProfile(detail, profile);
  const edge = profileEdge(profile);
  $("#pcSnapshot").innerHTML = [
    snapshotCard(
      "Recent form",
      trend.trend_label || "Trend pending",
      `${signed(trend.recent_sg)} recent SG vs ${signed(trend.baseline_sg)} baseline`,
      Number(trend.sg_delta) >= 0.25 ? "is-good" : Number(trend.sg_delta) <= -0.25 ? "is-watch" : "",
      "recent"
    ),
    snapshotCard(edge.label, edge.value, edge.note, "is-good", "edge"),
    snapshotCard(
      "Major profile",
      major.rounds ? signed(major.avg_to_par) : "--",
      major.rounds ? `${fmt(major.rounds)} major rounds | ${signed(major.avg_sg)} SG` : "major sample pending",
      major.rounds >= 8 ? "is-good" : "is-watch",
      "major"
    ),
    snapshotCard(
      "Tough courses",
      detail.courseDna?.toughRounds ? signed(detail.courseDna.toughAvgToPar) : "--",
      detail.courseDna?.toughRounds ? `${fmt(detail.courseDna.toughRounds)} tough rounds` : "difficulty sample pending",
      detail.courseDna?.toughRounds >= 8 ? "is-good" : "is-watch",
      "tough"
    ),
    snapshotCard(
      "Best course",
      bestCourse?.course || "--",
      bestCourse ? `${signed(bestCourse.avg_to_par)} avg | ${fmt(bestCourse.rounds)} rounds` : "repeat-course history pending",
      "",
      "best"
    ),
    snapshotCard("Data trust", trust.label, trust.note, trust.loaded >= 6 ? "is-good" : trust.loaded >= 4 ? "" : "is-watch", "trust"),
  ].join("");
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
  if (detail.courseDna?.headline) {
    bullets.push(["Course DNA", detail.courseDna.headline]);
  }
  if (detail.recentVsBaseline?.trend_label) {
    const trend = detail.recentVsBaseline;
    bullets.push(["Form trend", `${trend.trend_label}: ${signed(trend.recent_sg)} recent SG vs ${signed(trend.baseline_sg)} baseline SG.`]);
  }
  if (detail.bestCourses?.rows?.[0]) {
    const best = detail.bestCourses.rows[0];
    bullets.push(["Course fit receipt", `${best.course}: ${signed(best.avg_to_par)} average to par across ${fmt(best.rounds)} tracked rounds.`]);
  }
  if (model.risk_flags) bullets.push(["Risk flag", model.risk_flags]);
  $("#pcExplain").innerHTML = bullets.slice(0, 5).map(([label, text], index) => `
    <button type="button" data-drill="explain" data-index="${index}">
      <strong>${escapeHtml(label)}</strong><span>${escapeHtml(text)}</span>
    </button>
  `).join("");
}

function renderProjection(detail, row) {
  const model = detail.model || row || {};
  const probability = firstNumber(model.probability, row?.probability);
  const edge = firstNumber(model.edge_probability, row?.edge_probability);
  $("#projectionTitle").textContent = eventName();
  $("#projectionBadge").textContent = model.confidence || row?.confidence || "Coverage watch";
  $("#projectionGrid").innerHTML = [
    insight("Model rank", model.rank ? `#${fmt(model.rank)}` : "--", "winner market", "", "rank"),
    insight("Win probability", probability !== null ? pct(probability * 100, 1) : "--", "model implied", "", "probability"),
    insight("Projected to par", signed(firstNumber(model.projected_to_par, row?.projected_to_par)), "event finish", "", "projected"),
    insight("Fair odds", moneyOdds(model.fair_odds_american), "model price", "", "odds"),
    insight("Market edge", edge !== null ? `${signed(edge * 100, 1)} pts` : "--", "model minus market", edge > 0 ? "is-good" : "", "edge"),
    insight("Confidence", model.confidence || row?.confidence || "Watch", "sample and source quality", "", "confidence"),
  ].join("");
}

function renderGradeReceipt(detail, profile) {
  const explanations = detail.gradeExplanations || {};
  const explanation = explanations[activeGradeKey] || explanations.sg_total || {};
  const stat = scorecardStats(profile).find((item) => item.key === activeGradeKey) || scorecardStats(profile)[0];
  const recent = detail.recentVsBaseline || {};
  const value = stat.formatter(stat.value);
  $("#gradeReceipt").innerHTML = `
    <div class="grade-receipt-main">
      <span>${escapeHtml(explanation.label || stat.label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(explanation.headline || stat.note)}</small>
    </div>
    <div class="grade-receipt-copy">
      <p>${escapeHtml(explanation.body || "This grade is pending more loaded player context.")}</p>
      <div class="grade-receipt-meta">
        <span><b>${escapeHtml(signed(recent.recent_sg))}</b><small>last ${fmt(recent.recent_rounds)} rounds SG</small></span>
        <span><b>${escapeHtml(signed(recent.baseline_sg))}</b><small>full sample SG</small></span>
        <span><b>${escapeHtml(recent.trend_label || "Trend pending")}</b><small>${escapeHtml(explanation.source || "Golf Lab warehouse")}</small></span>
      </div>
    </div>
  `;
}

function renderScorecard(profile, detail) {
  $("#scorecardBadge").textContent = profile.seasonLabel || (profile.season ? `${profile.season} season` : "Latest profile");
  const explanations = detail.gradeExplanations || {};
  $("#scorecardGrid").innerHTML = scorecardStats(profile)
    .map((stat) => statTile(stat, explanations[stat.key], stat.key === activeGradeKey))
    .join("");
  renderGradeReceipt(detail, profile);
}

function renderDifficultySplits(detail) {
  const rows = detail.difficultySplits?.rows || [];
  const dna = detail.courseDna || {};
  $("#courseDnaBadge").textContent = rows.length ? `${rows.length} course lanes` : "Course lanes pending";
  $("#courseDnaHero").innerHTML = `
    <div>
      <span>${escapeHtml(dna.headline || "Course profile pending")}</span>
      <p>${escapeHtml(dna.body || "Course difficulty splits will populate as rounds are loaded.")}</p>
    </div>
    <div class="dna-kpis">
      <span><b>${fmt(dna.toughRounds)}</b><small>tough rounds</small></span>
      <span><b>${signed(dna.toughAvgToPar)}</b><small>tough to par</small></span>
      <span><b>${fmt(dna.gettableRounds)}</b><small>gettable rounds</small></span>
    </div>
  `;
  if (!rows.length) {
    $("#difficultySplits").innerHTML = `<div class="empty">No course difficulty splits loaded yet.</div>`;
    return;
  }
  $("#difficultySplits").innerHTML = rows.map((row, index) => `
    <button type="button" class="split-card ${escapeHtml(row.bucket || "balanced")}" data-drill="split" data-index="${index}">
      <div>
        <span class="difficulty ${escapeHtml(row.bucket || "balanced")}">${escapeHtml(row.bucket || "balanced")}</span>
        <strong>${signed(row.avg_to_par)}</strong>
        <small>average to par</small>
      </div>
      <div class="split-stats">
        <span><b>${fmt(row.rounds)}</b><small>rounds</small></span>
        <span><b>${signed(row.avg_sg)}</b><small>SG</small></span>
        <span><b>${pctDecimal(row.par_or_better_rate)}</b><small>par or better</small></span>
      </div>
    </button>
  `).join("");
}

function renderMajorProfile(detail) {
  const rows = detail.majorProfile?.rows || [];
  const summaryRow = detail.majorProfile?.summary || {};
  $("#majorBadge").textContent = summaryRow.rounds ? `${fmt(summaryRow.rounds)} major rounds` : "Major sample pending";
  $("#majorSummary").innerHTML = `
    <span>${escapeHtml(summaryRow.headline || "Major profile pending")}</span>
    <p>${escapeHtml(summaryRow.body || "No loaded major scorecards are tied to this player yet.")}</p>
    <div class="major-kpis">
      <span><b>${signed(summaryRow.avg_to_par)}</b><small>avg to par</small></span>
      <span><b>${signed(summaryRow.avg_sg)}</b><small>avg SG</small></span>
      <span><b>${fmt(summaryRow.events)}</b><small>events</small></span>
    </div>
  `;
  if (!rows.length) {
    $("#majorGrid").innerHTML = `<div class="empty">Major scorecards will appear here when loaded.</div>`;
    return;
  }
  $("#majorGrid").innerHTML = rows.map((row, index) => `
    <button type="button" class="major-card" data-drill="major" data-index="${index}">
      <span>${escapeHtml(row.major || "Major")}</span>
      <strong>${signed(row.avg_to_par)}</strong>
      <small>${fmt(row.rounds)} rounds | ${fmt(row.events)} events | ${signed(row.avg_sg)} SG</small>
    </button>
  `).join("");
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
  $("#seasonBars").innerHTML = rows.map((row, index) => {
    const value = firstNumber(row.avg_sg_total, row.avg_to_par ? -row.avg_to_par : null);
    const width = value === null ? 0 : Math.min(100, (Math.abs(value) / max) * 100);
    const label = row.avg_sg_total !== null ? `${signed(row.avg_sg_total)} SG` : `${signed(row.avg_to_par)} to par`;
    const tone = value === null ? "is-missing" : value >= 0 ? "is-good" : "is-bad";
    return `
      <button type="button" class="season-row" data-drill="season" data-index="${index}">
        <strong>${escapeHtml(row.season)}</strong>
        <div class="season-meter ${tone}"><i style="width:${width}%"></i></div>
        <span>${escapeHtml(label)}</span>
        <small>${fmt(row.rounds)} rounds | ${row.driving_distance ? `${fmt(row.driving_distance, 1)} yd` : "distance --"} | GIR ${pctDecimal(row.gir)}</small>
      </button>
    `;
  }).join("");
}

function renderCourseLens(target, rows, emptyText, sourceKey) {
  $(target).innerHTML = (rows || []).map((row, index) => `
    <button type="button" class="course-row" data-drill="course" data-source="${escapeHtml(sourceKey)}" data-index="${index}">
      <div>
        <strong>${escapeHtml(row.course || "Course")}</strong>
        <span>${fmt(row.rounds)} rounds</span>
      </div>
      <div>
        <b>${signed(row.avg_to_par)}</b>
        <small>${signed(row.avg_sg)} SG</small>
      </div>
    </button>
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
        ${rows.map((row, index) => `
          <tr data-drill="round" data-index="${index}" tabindex="0">
            <td>${escapeHtml(row.round_date || "")}</td>
            <td>${escapeHtml(row.event_name || "")}</td>
            <td>${escapeHtml(row.course || "")}</td>
            <td>${escapeHtml(row.round_number || "")}</td>
            <td>${escapeHtml(roundScoreLabel(row))}</td>
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
    ["Trusted scoring avg", coverage.hasTrustedScoring, `${fmt(detail.player?.scoring_rounds)} stroke-play scoring rounds`],
    ["Rich season profile", richSeasons.length >= 3, `${fmt(richSeasons.length)} public stat seasons loaded`],
    ["Strokes gained", coverage.hasStrokesGained, "model baseline"],
    ["Driving distance", coverage.hasDrivingDistance, "public PGA stat lane"],
    ["Fairway accuracy", coverage.hasAccuracy, "public PGA stat lane"],
    ["GIR", coverage.hasGir, "public PGA stat lane"],
    ["Scrambling", coverage.hasScrambling, "public PGA stat lane"],
    ["Course DNA", (detail.difficultySplits?.rows || []).length > 0, `${fmt(detail.courseDna?.toughRounds)} tough-course rounds`],
    ["Major scorecards", (detail.majorProfile?.summary?.rounds || 0) > 0, `${fmt(detail.majorProfile?.summary?.rounds)} loaded major rounds`],
  ];
  $("#receiptGrid").innerHTML = `
    <div class="coverage-list">
      ${coverageRows.map(([label, ok, note]) => `
        <button type="button" data-drill="receipt" data-drill-key="${escapeHtml(label)}" class="${ok ? "is-good" : "is-watch"}"><strong>${escapeHtml(label)}</strong><span>${ok ? "Loaded" : "Coverage watch"}</span><small>${escapeHtml(note)}</small></button>
      `).join("")}
    </div>
    <button type="button" class="receipt-note" data-drill="receipt" data-drill-key="Latest source touch">
      <strong>Latest source touch</strong>
      <span>${escapeHtml(latest.provider || "source pending")}</span>
      <small>${escapeHtml([latest.endpoint, latest.fetched_at].filter(Boolean).join(" | "))}</small>
    </button>
    <button type="button" class="receipt-note" data-drill="receipt" data-drill-key="Honest limitation">
      <strong>Honest limitation</strong>
      <span>Distance, GIR, fairway, and scrambling coverage is strongest in recent PGA public stat seasons.</span>
      <small>Blank fields are treated as missing data, not zero performance.</small>
    </button>
  `;
}

function valueForStatKey(row, stat) {
  if (!row || !stat) return "--";
  if (stat.key === "scorecards") return fmt(row.rounds);
  const value = stat.key === "sg_total" ? firstNumber(row.avg_sg_total, row.sg_total) : firstNumber(row[stat.key]);
  return stat.formatter(value);
}

function evidenceHtml(items = []) {
  const rows = items.filter((item) => item && item.label);
  if (!rows.length) return "";
  return rows.map((item) => `
    <div>
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value ?? "--")}</strong>
      <small>${escapeHtml(item.note || "")}</small>
    </div>
  `).join("");
}

function openDrilldown(payload) {
  if (!payload) return;
  $("#drillEyebrow").textContent = payload.eyebrow || "Receipt";
  $("#drillTitle").textContent = payload.title || "Detail";
  $("#drillLead").innerHTML = `
    <strong>${escapeHtml(payload.value ?? "--")}</strong>
    <span>${escapeHtml(payload.note || "")}</span>
  `;
  $("#drillBody").innerHTML = (payload.body || [])
    .filter(Boolean)
    .map((line) => `<p>${escapeHtml(line)}</p>`)
    .join("");
  $("#drillEvidence").innerHTML = evidenceHtml(payload.evidence);
  $("#drillOverlay").hidden = false;
  $("#drillDrawer").hidden = false;
  document.body.classList.add("has-drill-open");
  $("#drillClose").focus({ preventScroll: true });
}

function closeDrilldown() {
  $("#drillOverlay").hidden = true;
  $("#drillDrawer").hidden = true;
  document.body.classList.remove("has-drill-open");
}

function projectionDrill(key) {
  const model = activeDetail?.model || playerRow(activePlayerId) || {};
  const probability = firstNumber(model.probability);
  const edge = firstNumber(model.edge_probability);
  const projected = firstNumber(model.projected_to_par);
  const map = {
    rank: ["Model rank", model.rank ? `#${fmt(model.rank)}` : "--", "Outright winner-market rank for the selected event."],
    probability: ["Win probability", probability !== null ? pct(probability * 100, 1) : "--", "Outright win chance from the saved winner-market model row."],
    projected: ["Projected to par", signed(projected), "Projected tournament finish score when available."],
    odds: ["Fair odds", moneyOdds(model.fair_odds_american), "American odds implied by the model probability."],
    edge: ["Market edge", edge !== null ? `${signed(edge * 100, 1)} pts` : "--", "Model probability minus the available market price."],
    confidence: ["Confidence", model.confidence || "Watch", "Sample depth and source coverage label."],
  };
  const [title, value, note] = map[key] || map.rank;
  return {
    eyebrow: eventName(),
    title,
    value,
    note,
    body: [
      model.plain_english,
      "This player-card headline uses only the winner market. Cut, top-10, and top-20 probabilities stay out of the win slot unless they are explicitly labeled.",
    ],
    evidence: [
      { label: "Market", value: model.market || "winner", note: "selected model row" },
      { label: "Rank", value: model.rank ? `#${fmt(model.rank)}` : "--", note: "event board" },
      { label: "Probability", value: probability !== null ? pct(probability * 100, 1) : "--", note: "winner market" },
      { label: "Fair odds", value: moneyOdds(model.fair_odds_american), note: "model price" },
      { label: "Edge", value: edge !== null ? `${signed(edge * 100, 1)} pts` : "--", note: "vs market" },
      { label: "Confidence", value: model.confidence || "Watch", note: "coverage label" },
    ],
  };
}

function statDrill(key) {
  const stats = scorecardStats(activeProfile || {});
  const stat = stats.find((item) => item.key === key) || stats[0];
  const explanation = activeDetail?.gradeExplanations?.[stat.key] || {};
  const recent = activeDetail?.recentVsBaseline || {};
  const seasons = (activeDetail?.seasons?.rows || []).slice(0, 5);
  return {
    eyebrow: "Player scorecard",
    title: explanation.label || stat.label,
    value: stat.formatter(stat.value),
    note: explanation.headline || stat.note,
    body: [
      explanation.body || "This lane is built from the loaded player profile and will sharpen as more source rows are added.",
      explanation.source ? `Source lane: ${explanation.source}.` : "",
    ],
    evidence: [
      { label: "Profile window", value: activeProfile?.seasonLabel || "Latest profile", note: activeProfile?.profileSeasons || "career blend" },
      { label: "Round sample", value: fmt(activeProfile?.rounds), note: "imported scorecards" },
      { label: "Recent SG", value: signed(recent.recent_sg), note: `last ${fmt(recent.recent_rounds)} rounds` },
      { label: "Baseline SG", value: signed(recent.baseline_sg), note: "full sample" },
      ...seasons.map((row) => ({
        label: `${row.season}`,
        value: valueForStatKey(row, stat),
        note: `${fmt(row.rounds)} rounds`,
      })),
    ],
  };
}

function snapshotDrill(key) {
  const detail = activeDetail || {};
  const profile = activeProfile || {};
  const trend = detail.recentVsBaseline || {};
  const major = detail.majorProfile?.summary || {};
  const best = detail.bestCourses?.rows?.[0] || {};
  const trust = trustProfile(detail, profile);
  const edge = profileEdge(profile);
  const payloads = {
    recent: {
      title: "Recent form",
      value: trend.trend_label || "Trend pending",
      note: `${signed(trend.recent_sg)} recent SG vs ${signed(trend.baseline_sg)} baseline`,
      body: ["Recent form compares the most recent loaded rounds against the player baseline, so it is a form signal rather than a career grade."],
      evidence: [
        { label: "Recent rounds", value: fmt(trend.recent_rounds), note: "loaded sample" },
        { label: "Recent SG", value: signed(trend.recent_sg), note: "latest window" },
        { label: "Baseline SG", value: signed(trend.baseline_sg), note: "full sample" },
        { label: "Delta", value: signed(trend.sg_delta), note: "recent minus baseline" },
      ],
    },
    edge: {
      title: edge.label,
      value: edge.value,
      note: edge.note,
      body: ["The skill edge is the strongest loaded lane after normalizing SG, distance, and GIR into comparable signals."],
      evidence: scorecardStats(profile).slice(0, 10).map((stat) => ({ label: stat.label, value: stat.formatter(stat.value), note: stat.note })),
    },
    major: {
      title: "Major profile",
      value: major.rounds ? signed(major.avg_to_par) : "--",
      note: major.rounds ? `${fmt(major.rounds)} major rounds | ${signed(major.avg_sg)} SG` : "major sample pending",
      body: [major.body || "Major form uses loaded Masters, PGA Championship, U.S. Open, and Open Championship scorecards."],
      evidence: (detail.majorProfile?.rows || []).map((row) => ({ label: row.major, value: signed(row.avg_to_par), note: `${fmt(row.rounds)} rounds | ${signed(row.avg_sg)} SG` })),
    },
    tough: {
      title: "Tough-course profile",
      value: detail.courseDna?.toughRounds ? signed(detail.courseDna.toughAvgToPar) : "--",
      note: detail.courseDna?.toughRounds ? `${fmt(detail.courseDna.toughRounds)} tough rounds` : "difficulty sample pending",
      body: [detail.courseDna?.body || "Course DNA separates brutal, tough, balanced, and gettable setups when course difficulty metadata is loaded."],
      evidence: (detail.difficultySplits?.rows || []).map((row) => ({ label: row.bucket, value: signed(row.avg_to_par), note: `${fmt(row.rounds)} rounds | ${signed(row.avg_sg)} SG` })),
    },
    best: {
      title: "Best loaded course",
      value: best.course || "--",
      note: best.course ? `${signed(best.avg_to_par)} average | ${fmt(best.rounds)} rounds` : "repeat-course history pending",
      body: ["Best and worst courses are repeat-course scorecard receipts. They should be read as loaded-history signals, not permanent course labels."],
      evidence: (detail.bestCourses?.rows || []).slice(0, 6).map((row) => ({ label: row.course, value: signed(row.avg_to_par), note: `${fmt(row.rounds)} rounds | ${signed(row.avg_sg)} SG` })),
    },
    trust: {
      title: "Data trust",
      value: trust.label,
      note: trust.note,
      body: ["The trust label checks scorecards, rich stat seasons, distance, GIR, scoring, major history, and tough-course sample depth."],
      evidence: [
        { label: "Round scorecards", value: detail.coverage?.hasRoundScorecards ? "Loaded" : "Watch", note: `${fmt(detail.player?.rounds)} tracked rounds` },
        { label: "Rich seasons", value: fmt(profile.richSeasonCount), note: profile.profileSeasons || "recent stat seasons" },
        { label: "GIR", value: detail.coverage?.hasGir ? "Loaded" : "Watch", note: pctDecimal(profile.gir) },
        { label: "Distance", value: detail.coverage?.hasDrivingDistance ? "Loaded" : "Watch", note: profile.driving_distance ? `${fmt(profile.driving_distance, 1)} yd` : "--" },
        { label: "Major sample", value: fmt(major.rounds), note: "loaded major rounds" },
      ],
    },
  };
  return { eyebrow: "Snapshot", ...(payloads[key] || payloads.recent) };
}

function splitDrill(index) {
  const row = (activeDetail?.difficultySplits?.rows || [])[index] || {};
  return {
    eyebrow: "Course DNA",
    title: `${row.bucket || "Course"} setup`,
    value: signed(row.avg_to_par),
    note: `${fmt(row.rounds)} rounds | ${signed(row.avg_sg)} SG`,
    body: ["This bucket groups loaded rounds by course difficulty so the card can separate tough-course grinders from players who feast on easier setups."],
    evidence: [
      { label: "Rounds", value: fmt(row.rounds), note: "bucket sample" },
      { label: "Average to par", value: signed(row.avg_to_par), note: "scoring result" },
      { label: "Average SG", value: signed(row.avg_sg), note: "field-adjusted lane" },
      { label: "Par or better", value: pctDecimal(row.par_or_better_rate), note: "round rate" },
    ],
  };
}

function seasonDrill(index) {
  const row = (activeDetail?.seasons?.rows || [])[index] || {};
  return {
    eyebrow: "Season profile",
    title: `${row.season || "Season"} form`,
    value: row.avg_sg_total !== null ? signed(row.avg_sg_total) : signed(row.avg_to_par),
    note: `${fmt(row.rounds)} rounds | ${fmt(row.scoring_rounds)} trusted scoring rounds`,
    body: ["Season rows blend loaded scorecards with public PGA stat lanes when available."],
    evidence: [
      { label: "Scoring average", value: row.scoring_average ? fmt(row.scoring_average, 2) : "--", note: "stroke-play only" },
      { label: "SG total", value: signed(row.avg_sg_total), note: "season profile" },
      { label: "Distance", value: row.driving_distance ? `${fmt(row.driving_distance, 1)} yd` : "--", note: "public stat lane" },
      { label: "Fairways", value: pctDecimal(row.accuracy), note: "accuracy" },
      { label: "GIR", value: pctDecimal(row.gir), note: "greens in regulation" },
      { label: "Scrambling", value: pctDecimal(row.scrambling), note: "miss recovery" },
    ],
  };
}

function courseDrill(source, index) {
  const rows = activeDetail?.[source]?.rows || [];
  const row = rows[index] || {};
  return {
    eyebrow: source === "worstCourses" ? "Stress point" : "Course fit",
    title: row.course || "Course",
    value: signed(row.avg_to_par),
    note: `${fmt(row.rounds)} rounds | ${signed(row.avg_sg)} SG`,
    body: ["This is a repeat-course scorecard receipt. It is useful for fit context, but it should be weighted by sample size."],
    evidence: [
      { label: "Rounds", value: fmt(row.rounds), note: "loaded at course" },
      { label: "Average to par", value: signed(row.avg_to_par), note: "course history" },
      { label: "Average SG", value: signed(row.avg_sg), note: "field-adjusted estimate" },
    ],
  };
}

function majorDrill(index) {
  const row = (activeDetail?.majorProfile?.rows || [])[index] || {};
  return {
    eyebrow: "Major profile",
    title: row.major || "Major",
    value: signed(row.avg_to_par),
    note: `${fmt(row.rounds)} rounds | ${fmt(row.events)} events | ${signed(row.avg_sg)} SG`,
    body: ["Major buckets isolate championship-course performance so the model read does not overlean on regular PGA setups."],
    evidence: [
      { label: "Rounds", value: fmt(row.rounds), note: "loaded major rounds" },
      { label: "Events", value: fmt(row.events), note: "starts represented" },
      { label: "Average to par", value: signed(row.avg_to_par), note: "scoring result" },
      { label: "Average SG", value: signed(row.avg_sg), note: "field-adjusted lane" },
    ],
  };
}

function roundDrill(index) {
  const row = (activeDetail?.rounds?.rows || [])[index] || {};
  return {
    eyebrow: "Round scorecard",
    title: row.event_name || "Round",
    value: roundScoreLabel(row),
    note: `${row.course || "Course"} | Round ${row.round_number || "--"}`,
    body: ["A single-round receipt is the rawest layer in the card. It feeds scoring average, recent form, course history, and course-difficulty splits."],
    evidence: [
      { label: "Date", value: row.round_date || "--", note: "round date" },
      { label: "Course", value: row.course || "--", note: row.event_name || "" },
      { label: "Round", value: row.round_number || "--", note: "tournament round" },
      { label: "Score", value: roundScoreLabel(row), note: "raw card" },
      { label: "To par", value: signed(row.to_par, 0), note: "round result" },
      { label: "SG total", value: signed(row.sg_total), note: "loaded estimate" },
    ],
  };
}

function receiptDrill(key) {
  const coverage = activeDetail?.coverage || {};
  const latest = summary?.latestFetch || {};
  return {
    eyebrow: "Data receipt",
    title: key || "Coverage",
    value: key === "Latest source touch" ? (latest.provider || "source pending") : key === "Honest limitation" ? "Caveat" : "Coverage lane",
    note: key === "Latest source touch" ? [latest.endpoint, latest.fetched_at].filter(Boolean).join(" | ") : "Loaded means usable evidence exists in the local Golf Lab export.",
    body: [
      key === "Honest limitation"
        ? "Distance, GIR, fairway, and scrambling are strongest in recent public PGA stat seasons. Blank fields are missing data, not zero performance."
        : "Coverage receipts explain whether the visible scorecard number is backed by scorecards, public stat lanes, model rows, or source metadata.",
    ],
    evidence: [
      { label: "Scorecards", value: coverage.hasRoundScorecards ? "Loaded" : "Watch", note: `${fmt(activeDetail?.player?.rounds)} tracked rounds` },
      { label: "Trusted scoring", value: coverage.hasTrustedScoring ? "Loaded" : "Watch", note: `${fmt(activeDetail?.player?.scoring_rounds)} stroke-play rounds` },
      { label: "Strokes gained", value: coverage.hasStrokesGained ? "Loaded" : "Watch", note: "model baseline" },
      { label: "Distance", value: coverage.hasDrivingDistance ? "Loaded" : "Watch", note: "public stat lane" },
      { label: "GIR", value: coverage.hasGir ? "Loaded" : "Watch", note: "public stat lane" },
      { label: "Latest touch", value: latest.provider || "--", note: latest.fetched_at || "" },
    ],
  };
}

function explainDrill(element) {
  const title = element?.querySelector("strong")?.textContent || "Model read";
  const text = element?.querySelector("span")?.textContent || "";
  return {
    eyebrow: "Plain-English reasoning",
    title,
    value: "Why it matters",
    note: "Golf Lab turns the raw model and scorecard layers into a human-readable read.",
    body: [text],
    evidence: [
      { label: "Player", value: activeDetail?.player?.player_name || "--", note: activeProfile?.seasonLabel || "" },
      { label: "Model market", value: activeDetail?.model?.market || "winner", note: eventName() },
      { label: "Profile SG", value: signed(activeProfile?.avg_sg_total), note: "rich profile" },
      { label: "Scorecards", value: fmt(activeDetail?.player?.rounds), note: "loaded rounds" },
    ],
  };
}

function openDrilldownFromElement(element) {
  const type = element.dataset.drill;
  const key = element.dataset.drillKey;
  const index = Number(element.dataset.index);
  const source = element.dataset.source;
  const payload = {
    projection: () => projectionDrill(key),
    stat: () => statDrill(key),
    snapshot: () => snapshotDrill(key),
    split: () => splitDrill(index),
    season: () => seasonDrill(index),
    course: () => courseDrill(source, index),
    major: () => majorDrill(index),
    round: () => roundDrill(index),
    receipt: () => receiptDrill(key),
    explain: () => explainDrill(element),
  }[type]?.();
  openDrilldown(payload);
}

function renderSectionView() {
  const views = {
    overview: ["projection", "scorecard", "grade-details"],
    scorecard: ["scorecard", "grade-details", "seasons"],
    course: ["course-dna", "courses", "course-stress", "majors"],
    rounds: ["rounds"],
    data: ["receipts"],
    all: ["projection", "scorecard", "grade-details", "course-dna", "seasons", "courses", "course-stress", "majors", "rounds", "receipts"],
  };
  const active = new Set(views[activeView] || views.overview);
  $$("#pcSectionNav [data-player-view]").forEach((button) => {
    const selected = button.dataset.playerView === activeView;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  $$(".pc-grid > section").forEach((section) => {
    section.hidden = !active.has(section.id);
  });
}

function renderPlayer(detail) {
  const row = playerRow(detail.player.player_id);
  const profile = richProfile(detail);
  activeDetail = detail;
  activeProfile = profile;
  if (!detail.gradeExplanations?.[activeGradeKey]) activeGradeKey = "sg_total";
  $("#emptyState").hidden = true;
  $("#playerCard").hidden = false;
  renderHero(detail, row, profile);
  $("#pcVerdict").textContent = verdictText(detail, row, profile);
  renderSnapshot(detail, profile);
  renderExplain(detail, row, profile);
  renderProjection(detail, row);
  renderScorecard(profile, detail);
  renderDifficultySplits(detail);
  renderSeasons(detail);
  renderCourseLens("#bestCourses", detail.bestCourses?.rows || [], "Need more repeat-course history.", "bestCourses");
  renderCourseLens("#worstCourses", detail.worstCourses?.rows || [], "Need more repeat-course history.", "worstCourses");
  renderMajorProfile(detail);
  renderRounds(detail);
  renderReceipts(detail);
  renderSectionView();
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
  $("#drillClose").addEventListener("click", closeDrilldown);
  $("#drillOverlay").addEventListener("click", closeDrilldown);
  document.addEventListener("click", (event) => {
    const view = event.target.closest("[data-player-view]");
    if (view) {
      activeView = view.dataset.playerView || "overview";
      renderSectionView();
      return;
    }
    const pick = event.target.closest("[data-pick-player]");
    if (pick) {
      loadPlayer(pick.dataset.pickPlayer);
      $("#typeahead").hidden = true;
      return;
    }
    const grade = event.target.closest("[data-grade-key]");
    if (grade && activeDetail && activeProfile) {
      activeGradeKey = grade.dataset.gradeKey;
      renderScorecard(activeProfile, activeDetail);
      openDrilldownFromElement(grade);
      return;
    }
    const drill = event.target.closest("[data-drill]");
    if (drill && activeDetail && activeProfile) {
      openDrilldownFromElement(drill);
      return;
    }
    if (!event.target.closest(".player-search-box")) $("#typeahead").hidden = true;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#drillDrawer").hidden) closeDrilldown();
    const row = event.target.closest?.("[data-drill='round']");
    if (row && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      openDrilldownFromElement(row);
    }
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
