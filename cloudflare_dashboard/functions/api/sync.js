import { hasWriteAccess } from "../_lib/auth.js";
import { jsonResponse } from "../_lib/response.js";
import {
  materializeTimer,
  normalizeDesktopTimer,
  normalizeSession,
  nowIso,
  readTimer,
  settleExpiredTimer,
  timerIsActive,
} from "../_lib/cloud_state.js";

const MAX_BODY_BYTES = 900_000;

function activeStatus(value) {
  return value === "running" || value === "paused";
}

function timerUpdatedMs(timer) {
  const value = Date.parse(timer?.updated_at || "");
  return Number.isFinite(value) ? value : 0;
}

async function upsertDesktopTimer(env, local, cloud) {
  if (!local || !local.session_id) return { conflict: false };
  const cloudActive = timerIsActive(cloud);
  const localActive = activeStatus(local.status);
  const sameSession = Boolean(cloud?.session_id && cloud.session_id === local.session_id);

  if (cloudActive && localActive && !sameSession) {
    return {
      conflict: true,
      message: "A different timer is active in the cloud. Both timers were preserved; choose one in the app before replacing either.",
    };
  }

  const localNewer = timerUpdatedMs(local) > timerUpdatedMs(cloud);
  const shouldWrite =
    (!cloud && local.status !== "idle") ||
    (sameSession && localNewer) ||
    (!sameSession && !cloudActive && localNewer);

  if (!shouldWrite) return { conflict: false };

  const now = nowIso();
  await env.DB.prepare(
    `INSERT INTO cloud_timer(
       id,session_id,revision,status,task,category,mode,counts_toward_focus,
       duration_seconds,elapsed_seconds,started_at,running_since,updated_at,source,timezone
     ) VALUES(1,?,1,?,?,?,?,?,?,?,?,?,?, 'desktop',?)
     ON CONFLICT(id) DO UPDATE SET
       session_id=excluded.session_id,
       revision=cloud_timer.revision+1,
       status=excluded.status,
       task=excluded.task,
       category=excluded.category,
       mode=excluded.mode,
       counts_toward_focus=excluded.counts_toward_focus,
       duration_seconds=excluded.duration_seconds,
       elapsed_seconds=excluded.elapsed_seconds,
       started_at=excluded.started_at,
       running_since=excluded.running_since,
       updated_at=excluded.updated_at,
       source='desktop', timezone=excluded.timezone`
  ).bind(
    local.session_id,
    local.status,
    local.task,
    local.category,
    local.mode,
    local.counts_toward_focus ? 1 : 0,
    local.duration_seconds,
    local.elapsed_seconds,
    local.started_at,
    local.status === "running" ? (local.running_since || now) : null,
    local.updated_at || now,
    local.timezone || "UTC",
  ).run();
  return { conflict: false };
}

async function upsertSchedules(env, rows) {
  if (!Array.isArray(rows)) return;
  const statements = [];
  for (const raw of rows.slice(0, 2000)) {
    if (!raw || !raw.id || !raw.date || !raw.start || !raw.end || !raw.title) continue;
    const updatedAt = String(raw.updated_at || nowIso());
    statements.push(
      env.DB.prepare(
        `INSERT INTO cloud_schedule(
           id,date,start,end,title,category,notes,revision,deleted,updated_at,source
         ) VALUES(?,?,?,?,?,?,?,1,?,?, 'desktop')
         ON CONFLICT(id) DO UPDATE SET
           date=excluded.date, start=excluded.start, end=excluded.end,
           title=excluded.title, category=excluded.category, notes=excluded.notes,
           revision=cloud_schedule.revision+1, deleted=excluded.deleted,
           updated_at=excluded.updated_at, source='desktop'
         WHERE julianday(excluded.updated_at) > julianday(cloud_schedule.updated_at)`
      ).bind(
        String(raw.id).slice(0, 100),
        String(raw.date).slice(0, 10),
        String(raw.start).slice(0, 5),
        String(raw.end).slice(0, 5),
        String(raw.title).slice(0, 200),
        String(raw.category || "General").slice(0, 100),
        String(raw.notes || "").slice(0, 2000),
        raw.deleted ? 1 : 0,
        updatedAt,
      )
    );
  }
  for (let index = 0; index < statements.length; index += 75) {
    await env.DB.batch(statements.slice(index, index + 75));
  }
}

async function upsertSessions(env, rows) {
  if (!Array.isArray(rows)) return;
  const statements = [];
  for (const raw of rows.slice(0, 1000)) {
    const item = normalizeSession(raw);
    if (!item) continue;
    statements.push(
      env.DB.prepare(
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
           updated_at=excluded.updated_at
         WHERE julianday(excluded.updated_at) >= julianday(cloud_sessions.updated_at)`
      ).bind(
        item.id, item.task, item.category, item.mode,
        item.counts_toward_focus ? 1 : 0,
        item.started_at_utc, item.ended_at_utc, item.local_date,
        item.planned_minutes, item.minutes, item.notes, item.source, item.updated_at,
      )
    );
  }
  for (let index = 0; index < statements.length; index += 75) {
    await env.DB.batch(statements.slice(index, index + 75));
  }
}

export async function onRequestPost(context) {
  const { request, env } = context;
  if (!hasWriteAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Invalid desktop write key." }, 401);
  }

  const length = Number(request.headers.get("Content-Length") || 0);
  if (length > MAX_BODY_BYTES) {
    return jsonResponse({ ok: false, error: "Sync payload is too large." }, 413);
  }

  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
    return jsonResponse({ ok: false, error: "Sync payload is too large." }, 413);
  }

  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    return jsonResponse({ ok: false, error: "Body must be valid JSON." }, 400);
  }

  const now = nowIso();
  const deviceId = String(body?.device_id || "desktop").slice(0, 100);
  const appVersion = String(body?.app_version || "").slice(0, 30);
  const clientSentAt = Number.isFinite(Date.parse(String(body?.client_sent_at || "")))
    ? String(body.client_sent_at)
    : "";
  const serverSince = Number.isFinite(Date.parse(String(body?.server_since || "")))
    ? String(body.server_since)
    : "";

  await upsertSchedules(env, body?.schedule);
  await upsertSessions(env, body?.sessions);

  let cloud = await settleExpiredTimer(env);
  const local = normalizeDesktopTimer(body?.timer);
  const timerResult = await upsertDesktopTimer(env, local, cloud);
  cloud = await readTimer(env);

  await env.DB.prepare(
    `INSERT INTO desktop_sync_status(device_id,last_seen_at,app_version,message)
     VALUES(?,?,?,?)
     ON CONFLICT(device_id) DO UPDATE SET
       last_seen_at=excluded.last_seen_at,
       app_version=excluded.app_version,
       message=excluded.message`
  ).bind(deviceId, now, appVersion, timerResult.message || "Synchronized").run();

  const scheduleQuery = serverSince
    ? env.DB.prepare(
        `SELECT * FROM cloud_schedule WHERE julianday(updated_at) > julianday(?) ORDER BY date,start,title`
      ).bind(serverSince)
    : env.DB.prepare(
        `SELECT * FROM cloud_schedule ORDER BY date,start,title`
      );
  const sessionQuery = serverSince
    ? env.DB.prepare(
        `SELECT * FROM cloud_sessions WHERE julianday(updated_at) > julianday(?) ORDER BY ended_at_utc DESC LIMIT 750`
      ).bind(serverSince)
    : env.DB.prepare(
        `SELECT * FROM cloud_sessions ORDER BY ended_at_utc DESC LIMIT 750`
      );
  const [scheduleResult, sessionResult] = await Promise.all([
    scheduleQuery.all(),
    sessionQuery.all(),
  ]);

  return jsonResponse({
    ok: true,
    server_time: now,
    client_sent_at: clientSentAt,
    timer: materializeTimer(cloud),
    timer_conflict: Boolean(timerResult.conflict),
    message: timerResult.message || "Synchronized",
    schedule: scheduleResult.results || [],
    sessions: sessionResult.results || [],
  });
}

export function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: { "Allow": "POST, OPTIONS", "Cache-Control": "no-store" },
  });
}
