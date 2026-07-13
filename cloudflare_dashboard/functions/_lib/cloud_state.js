const COUNT_UP_MODES = new Set(["Flow", "Productive", "Personal"]);
const LOGGABLE_MODES = new Set(["Focus", "Flow", "Productive", "Personal"]);
const VALID_MODES = new Set([
  "Focus", "Flow", "Productive", "Personal", "Short Break", "Long Break",
]);
const VALID_STATUSES = new Set(["idle", "running", "paused", "completed", "canceled"]);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TIME_RE = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

export function nowIso() {
  return new Date().toISOString();
}

function parseMs(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : null;
}

export function currentElapsed(row, now = Date.now()) {
  if (!row) return 0;
  let elapsed = Math.max(0, Number(row.elapsed_seconds) || 0);
  if (row.status === "running") {
    const runningSince = parseMs(row.running_since);
    if (runningSince !== null) {
      elapsed += Math.max(0, Math.floor((now - runningSince) / 1000));
    }
  }
  return elapsed;
}

export function materializeTimer(row, now = Date.now()) {
  if (!row) {
    return {
      session_id: "",
      revision: 0,
      status: "idle",
      running: false,
      paused: false,
      task: "",
      category: "Research",
      mode: "Focus",
      counts_toward_focus: true,
      duration_seconds: 1500,
      elapsed_seconds: 0,
      remaining_seconds: 1500,
      display_seconds: 1500,
      started_at: null,
      running_since: null,
      updated_at: null,
      source: "cloud",
    };
  }

  const elapsed = currentElapsed(row, now);
  const duration = Math.max(0, Number(row.duration_seconds) || 0);
  const countUp = COUNT_UP_MODES.has(row.mode);
  const remaining = countUp ? 0 : Math.max(0, duration - elapsed);
  return {
    session_id: String(row.session_id || ""),
    revision: Number(row.revision) || 0,
    status: String(row.status || "idle"),
    running: row.status === "running" || row.status === "paused",
    paused: row.status === "paused",
    task: String(row.task || ""),
    category: String(row.category || "Research"),
    mode: VALID_MODES.has(row.mode) ? row.mode : "Focus",
    counts_toward_focus: Boolean(row.counts_toward_focus),
    duration_seconds: duration,
    elapsed_seconds: elapsed,
    remaining_seconds: remaining,
    display_seconds: countUp ? elapsed : remaining,
    started_at: row.started_at || null,
    running_since: row.running_since || null,
    updated_at: row.updated_at || null,
    source: row.source || "cloud",
    timezone: row.timezone || "UTC",
  };
}

export function validateTimerStart(input) {
  const mode = VALID_MODES.has(input?.mode) ? input.mode : "Focus";
  const countUp = COUNT_UP_MODES.has(mode);
  const rawDuration = Number(input?.duration_seconds ?? 1500);
  const duration = countUp ? 0 : Math.floor(rawDuration);
  if (!countUp && (!Number.isFinite(duration) || duration < 60 || duration > 36000)) {
    throw new Error("Duration must be between 1 and 600 minutes.");
  }
  const task = String(input?.task || "").trim() ||
    ({ Focus: "Focused work", Flow: "Focused work", Productive: "Productive task", Personal: "Personal activity" }[mode] || mode);
  const category = String(input?.category || "").trim() ||
    (mode.includes("Break") ? "Break" : mode === "Personal" ? "Personal" : "Research");
  return {
    mode,
    task: task.slice(0, 200),
    category: category.slice(0, 100),
    counts_toward_focus: mode === "Focus" || mode === "Flow"
      ? true
      : Boolean(input?.counts_toward_focus),
    duration_seconds: duration,
    timezone: String(input?.timezone || "UTC").slice(0, 100),
  };
}

export function normalizeDesktopTimer(input) {
  if (!input || typeof input !== "object") return null;
  const status = VALID_STATUSES.has(input.status)
    ? input.status
    : (input.running ? (input.paused ? "paused" : "running") : "idle");
  const mode = VALID_MODES.has(input.mode) ? input.mode : "Focus";
  const countUp = COUNT_UP_MODES.has(mode);
  return {
    session_id: String(input.session_id || "").slice(0, 100),
    revision: Math.max(0, Number(input.revision) || 0),
    status,
    task: String(input.task || "").slice(0, 200),
    category: String(input.category || "Research").slice(0, 100),
    mode,
    counts_toward_focus: Boolean(input.counts_toward_focus),
    duration_seconds: countUp ? 0 : Math.max(0, Math.floor(Number(input.duration_seconds) || 0)),
    elapsed_seconds: Math.max(0, Math.floor(Number(input.elapsed_seconds) || 0)),
    started_at: input.started_at || null,
    running_since: status === "running" ? (input.running_since || input.updated_at || nowIso()) : null,
    updated_at: input.updated_at || nowIso(),
    source: "desktop",
    timezone: String(input.timezone || "UTC").slice(0, 100),
  };
}

export function validateScheduleItem(input) {
  const eventDate = String(input?.date || "").trim();
  const start = String(input?.start || "").trim();
  const end = String(input?.end || "").trim();
  const title = String(input?.title || "").trim();
  if (!DATE_RE.test(eventDate)) throw new Error("Date must use YYYY-MM-DD.");
  if (!TIME_RE.test(start) || !TIME_RE.test(end)) throw new Error("Times must use 24-hour HH:MM.");
  if (end <= start) throw new Error("End time must be later than start time.");
  if (!title) throw new Error("Enter an event title.");
  return {
    id: String(input?.id || crypto.randomUUID()).slice(0, 100),
    date: eventDate,
    start,
    end,
    title: title.slice(0, 200),
    category: String(input?.category || "General").trim().slice(0, 100) || "General",
    notes: String(input?.notes || "").trim().slice(0, 2000),
  };
}

export function normalizeSession(input) {
  if (!input || typeof input !== "object") return null;
  const id = String(input.id || input.sync_id || "").slice(0, 128);
  if (!id) return null;
  const started = String(input.started_at_utc || "");
  const ended = String(input.ended_at_utc || "");
  if (parseMs(started) === null || parseMs(ended) === null) return null;
  return {
    id,
    task: String(input.task || "Untitled session").slice(0, 200),
    category: String(input.category || "General").slice(0, 100),
    mode: VALID_MODES.has(input.mode) ? input.mode : "Focus",
    counts_toward_focus: Boolean(input.counts_toward_focus),
    started_at_utc: started,
    ended_at_utc: ended,
    local_date: String(input.local_date || "").replace(/-/g, "").slice(0, 8),
    planned_minutes: Math.max(0, Math.floor(Number(input.planned_minutes) || 0)),
    minutes: Math.max(1, Math.floor(Number(input.minutes) || 1)),
    notes: String(input.notes || "").slice(0, 2000),
    source: String(input.source || "desktop").slice(0, 50),
    updated_at: String(input.updated_at || nowIso()),
  };
}

export async function readTimer(env) {
  return env.DB.prepare("SELECT * FROM cloud_timer WHERE id=1").first();
}

function dateInZone(iso, timeZone) {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timeZone || "UTC", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date(iso));
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}${values.month}${values.day}`;
  } catch {
    return iso.slice(0, 10).replaceAll("-", "");
  }
}

export async function insertCompletedSession(env, timer, endedAt = nowIso()) {
  if (!timer?.session_id || !LOGGABLE_MODES.has(timer.mode)) return;
  const elapsed = Math.max(1, currentElapsed(timer, Date.parse(endedAt)));
  const minutes = Math.max(1, Math.round(elapsed / 60));
  const localDate = dateInZone(endedAt, timer.timezone || "UTC");
  await env.DB.prepare(
    `INSERT INTO cloud_sessions(
       id,task,category,mode,counts_toward_focus,started_at_utc,ended_at_utc,
       local_date,planned_minutes,minutes,notes,source,updated_at
     ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
     ON CONFLICT(id) DO UPDATE SET
       task=excluded.task, category=excluded.category, mode=excluded.mode,
       counts_toward_focus=excluded.counts_toward_focus,
       started_at_utc=excluded.started_at_utc, ended_at_utc=excluded.ended_at_utc,
       local_date=excluded.local_date, planned_minutes=excluded.planned_minutes,
       minutes=excluded.minutes, notes=excluded.notes, source=excluded.source,
       updated_at=excluded.updated_at`
  ).bind(
    timer.session_id,
    timer.task || "Untitled session",
    timer.category || "General",
    timer.mode || "Focus",
    timer.counts_toward_focus ? 1 : 0,
    timer.started_at || endedAt,
    endedAt,
    localDate,
    Math.max(0, Math.round((Number(timer.duration_seconds) || 0) / 60)),
    minutes,
    "",
    timer.source || "cloud",
    endedAt,
  ).run();
}

export async function settleExpiredTimer(env) {
  const row = await readTimer(env);
  if (!row || row.status !== "running" || COUNT_UP_MODES.has(row.mode)) return row;
  const elapsed = currentElapsed(row);
  const duration = Math.max(0, Number(row.duration_seconds) || 0);
  if (!duration || elapsed < duration) return row;
  const endedAt = nowIso();
  await insertCompletedSession(env, row, endedAt);
  await env.DB.prepare(
    `UPDATE cloud_timer SET status='completed', elapsed_seconds=?, running_since=NULL,
       revision=revision+1, updated_at=? WHERE id=1`
  ).bind(duration, endedAt).run();
  return readTimer(env);
}

export function timerIsActive(timer) {
  return Boolean(timer && (timer.status === "running" || timer.status === "paused"));
}
