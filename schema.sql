pragma foreign_keys = on;

create table if not exists players (
  player_id text primary key,
  player_name text not null,
  country text,
  tour text,
  handedness text,
  birth_year integer,
  turned_pro integer,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists player_skill_snapshots (
  snapshot_id text primary key,
  player_id text not null references players(player_id),
  season integer,
  sg_total real,
  sg_t2g real,
  sg_ott real,
  sg_app real,
  sg_arg real,
  sg_putt real,
  driving_distance real,
  accuracy real,
  gir real,
  scrambling real,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists courses (
  course_id text primary key,
  course_name text not null,
  location text,
  country text,
  par integer,
  yards integer,
  architect text,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists events (
  event_id text primary key,
  event_name text not null,
  tour text,
  season integer,
  start_date text,
  end_date text,
  course_id text references courses(course_id),
  course_name text,
  status text,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists course_setups (
  setup_id text primary key,
  event_id text references events(event_id),
  course_id text references courses(course_id),
  course_name text,
  par integer,
  yards integer,
  rough text,
  green_speed text,
  fairway_width text,
  difficulty_score real,
  difficulty_bucket text,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists fields (
  field_id text primary key,
  event_id text not null references events(event_id),
  player_id text not null references players(player_id),
  player_name text,
  status text,
  seed integer,
  tee_time text,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists rounds (
  round_id text primary key,
  event_id text not null references events(event_id),
  player_id text not null references players(player_id),
  course_id text references courses(course_id),
  round_number integer,
  round_date text,
  score integer,
  to_par real,
  position text,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists strokes_gained (
  sg_id text primary key,
  round_id text references rounds(round_id),
  event_id text references events(event_id),
  player_id text references players(player_id),
  period text,
  sg_total real,
  sg_t2g real,
  sg_ott real,
  sg_app real,
  sg_arg real,
  sg_putt real,
  driving_distance real,
  accuracy real,
  gir real,
  scrambling real,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists weather_snapshots (
  weather_id text primary key,
  event_id text references events(event_id),
  course_id text references courses(course_id),
  round_number integer,
  forecast_at text,
  temperature_f real,
  wind_mph real,
  wind_direction text,
  precip_probability real,
  condition text,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists odds_snapshots (
  odds_id text primary key,
  event_id text references events(event_id),
  player_id text references players(player_id),
  player_name text,
  market text,
  book text,
  odds_american integer,
  implied_probability real,
  captured_at text,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists model_predictions (
  prediction_id text primary key,
  model_run_id text,
  event_id text references events(event_id),
  player_id text references players(player_id),
  player_name text,
  market text,
  rank integer,
  probability real,
  fair_odds_american integer,
  edge_probability real,
  projected_to_par real,
  confidence text,
  plain_english text,
  risk_flags text,
  created_at text,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists prediction_ledger (
  ledger_id text primary key,
  model_run_id text,
  event_id text references events(event_id),
  player_id text references players(player_id),
  market text,
  rank integer,
  probability real,
  odds_american integer,
  outcome_status text,
  result_label text,
  created_at text,
  source_provider text,
  source_url text,
  source_updated_at text
);

create table if not exists source_fetches (
  fetch_id text primary key,
  provider text,
  endpoint text,
  event_id text,
  model_run_id text,
  fetched_at text,
  status text,
  row_count integer,
  source_url text,
  notes text
);

create index if not exists idx_rounds_player on rounds(player_id, round_date desc);
create index if not exists idx_rounds_event on rounds(event_id, round_number);
create index if not exists idx_sg_player on strokes_gained(player_id, event_id);
create index if not exists idx_sg_round on strokes_gained(round_id);
create index if not exists idx_predictions_event on model_predictions(event_id, market, rank);
create index if not exists idx_odds_event on odds_snapshots(event_id, market, captured_at);

create view if not exists player_recent_form as
select
  p.player_id,
  p.player_name,
  count(r.round_id) as rounds,
  round(avg(r.to_par), 2) as avg_to_par,
  round(avg(sg.sg_total), 2) as avg_sg_total,
  min(r.round_date) as first_round,
  max(r.round_date) as last_round
from players p
left join rounds r on r.player_id = p.player_id
left join strokes_gained sg on sg.round_id = r.round_id
group by p.player_id, p.player_name;

create view if not exists course_difficulty as
select
  c.course_id,
  c.course_name,
  c.location,
  count(r.round_id) as rounds,
  round(avg(r.to_par), 2) as avg_to_par,
  round(avg(sg.sg_total), 2) as avg_sg_total,
  case
    when avg(r.to_par) >= 2.5 then 'brutal'
    when avg(r.to_par) >= 1.0 then 'tough'
    when avg(r.to_par) <= -1.0 then 'gettable'
    else 'balanced'
  end as difficulty_bucket
from courses c
left join rounds r on r.course_id = c.course_id
left join strokes_gained sg on sg.round_id = r.round_id
group by c.course_id, c.course_name, c.location;
