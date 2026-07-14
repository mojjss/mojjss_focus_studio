import { getAccessRole } from "../_lib/auth.js";
import { jsonResponse } from "../_lib/response.js";
import { materializeTimer, settleExpiredTimer, timerIsActive } from "../_lib/cloud_state.js";

function dateText(compact) {
  const value = String(compact || "").replace(/-/g, "");
  return value.length === 8
    ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
    : "";
}

function cloudSessionToRecent(item) {
  const date = dateText(item.local_date) || String(item.ended_at_utc || "").slice(0, 10);
  const started = new Date(item.started_at_utc);
  return {
    sync_id: item.id,
    date,
    display_date: date,
    weekday: "",
    start: Number.isFinite(started.getTime())
      ? started.toISOString().slice(11, 16)
      : "",
    task: item.task,
    category: item.category,
    mode: item.mode,
    minutes: Number(item.minutes) || 0,
    planned_minutes: Number(item.planned_minutes) || 0,
    counts_toward_focus: Boolean(item.counts_toward_focus),
    notes: item.notes || "",
    source: item.source || "cloud",
    started_at_utc: item.started_at_utc,
  };
}

function mergedRecent(snapshotRecent, cloudRows) {
  const map = new Map();
  for (const item of Array.isArray(snapshotRecent) ? snapshotRecent : []) {
    const key = String(item.sync_id || `desktop:${item.id || ""}:${item.date || ""}:${item.start || ""}`);
    map.set(key, { ...item });
  }
  for (const row of cloudRows || []) {
    const converted = cloudSessionToRecent(row);
    map.set(String(row.id), converted);
  }
  return [...map.values()].sort((left, right) => {
    const a = Date.parse(left.started_at_utc || `${left.date || "1970-01-01"}T${left.start || "00:00"}:00Z`) || 0;
    const b = Date.parse(right.started_at_utc || `${right.date || "1970-01-01"}T${right.start || "00:00"}:00Z`) || 0;
    return a - b;
  });
}

function addDays(dateValue, amount) {
  const date = new Date(`${dateValue}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + amount);
  return date.toISOString().slice(0, 10);
}

function monthBounds(today) {
  const start = `${today.slice(0, 7)}-01`;
  const date = new Date(`${start}T00:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + 1);
  date.setUTCDate(0);
  return [start, date.toISOString().slice(0, 10)];
}

function summaryForRange(items, start, end) {
  let focusMinutes = 0;
  let otherMinutes = 0;
  let sessions = 0;
  for (const item of items) {
    if (!item.date || item.date < start || item.date > end) continue;
    if (!["Focus", "Flow", "Productive"].includes(item.mode)) continue;
    sessions += 1;
    const minutes = Math.max(0, Number(item.minutes) || 0);
    if (item.counts_toward_focus) focusMinutes += minutes;
    else otherMinutes += minutes;
  }
  return {
    focus_minutes: focusMinutes,
    focus_sessions: items.filter((item) => item.date >= start && item.date <= end && item.counts_toward_focus).length,
    other_productive_minutes: otherMinutes,
    total_productive_minutes: focusMinutes + otherMinutes,
    total_productive_sessions: sessions,
  };
}

export async function onRequestGet(context) {
  console.log(
  await context.env.DB
    .prepare("SELECT name FROM sqlite_master WHERE type='table'")
    .all()
);
  const { request, env } = context;
  const role = getAccessRole(request, env);
  if (!role) {
    return jsonResponse(
      { ok: false, error: "Invalid dashboard key." },
      401,
      { "WWW-Authenticate": 'Bearer realm="Focus Dashboard"' },
    );
  }

  const [snapshotRow, timerRow, scheduleResult, sessionsResult] = await Promise.all([
    env.DB.prepare(
      "SELECT payload, received_at, device_updated_at FROM dashboard_snapshot WHERE id=1"
    ).first(),
    settleExpiredTimer(env),
    env.DB.prepare(
      "SELECT * FROM cloud_schedule ORDER BY date,start,title"
    ).all(),
    env.DB.prepare(
      "SELECT * FROM cloud_sessions ORDER BY ended_at_utc DESC LIMIT 750"
    ).all(),
  ]);

  let payload = {};
  if (snapshotRow) {
    try {
      payload = JSON.parse(snapshotRow.payload);
    } catch {
      return jsonResponse({ ok: false, error: "Stored dashboard data is invalid." }, 500);
    }
  }

  const receivedMs = snapshotRow ? Date.parse(snapshotRow.received_at) : NaN;
  const ageSeconds = Number.isFinite(receivedMs)
    ? Math.max(0, Math.floor((Date.now() - receivedMs) / 1000))
    : null;
  const desktopOnline = ageSeconds !== null && ageSeconds <= 180;
  const cloudTimer = materializeTimer(timerRow);
  const snapshotTimer = payload.timer || {};
  const cloudTimerNewer = Date.parse(cloudTimer.updated_at || "") > Date.parse(snapshotTimer.updated_at || "");
  if (timerIsActive(timerRow) || cloudTimerNewer || !snapshotRow) {
    payload.timer = {
      ...cloudTimer,
      status: cloudTimer.status === "running"
        ? "Running from cloud"
        : cloudTimer.status === "paused"
          ? "Paused from cloud"
          : cloudTimer.status,
    };
  }

  const allScheduleRows = scheduleResult.results || [];
  const scheduleRows = allScheduleRows.filter((item) => !item.deleted);
  if (allScheduleRows.length || !payload.schedule_events) {
    payload.schedule_events = scheduleRows;
    const today = payload.calendar?.today || new Date().toISOString().slice(0, 10);
    payload.schedule = scheduleRows.filter((item) => item.date === today);
  }

  const recent = mergedRecent(payload.recent, sessionsResult.results || []);
  payload.recent = recent;

  const today = payload.calendar?.today || new Date().toISOString().slice(0, 10);
  const weekday = new Date(`${today}T00:00:00Z`).getUTCDay();
  const mondayOffset = weekday === 0 ? -6 : 1 - weekday;
  const weekStart = payload.calendar?.week_start || addDays(today, mondayOffset);
  const weekEnd = payload.calendar?.week_end || addDays(weekStart, 6);
  const [monthStart, monthEnd] = monthBounds(today);
  payload.calendar = {
    today,
    week_start: weekStart,
    week_end: weekEnd,
    month_start: payload.calendar?.month_start || monthStart,
    month_end: payload.calendar?.month_end || monthEnd,
  };
  payload.periods = {
    today: summaryForRange(recent, today, today),
    week: summaryForRange(recent, weekStart, weekEnd),
    month: summaryForRange(recent, payload.calendar.month_start, payload.calendar.month_end),
  };
  payload.today = payload.periods.today;

  payload.cloud = {
    desktop_online: desktopOnline,
    received_at: snapshotRow?.received_at || null,
    device_updated_at: snapshotRow?.device_updated_at || null,
    age_seconds: ageSeconds,
    timer_source: payload.timer?.source || "desktop",
  };
  payload.access = { role };
  payload.has_data = Boolean(
    snapshotRow || timerRow || scheduleRows.length || recent.length
  );
  payload.ok = true;

  if (!payload.camera) payload.camera = { enabled: false, private_url: "" };
  if (!payload.pixela) payload.pixela = {};
  if (!payload.timezone) payload.timezone = cloudTimer.timezone || "UTC";

  if (role === "viewer") {
    payload.recent = recent.slice(-30).map((item) => {
      const copy = { ...item };
      delete copy.notes;
      delete copy.source;
      return copy;
    });
  } else {
    payload.recent = recent.slice(-250);
  }

  return jsonResponse(payload);
}
