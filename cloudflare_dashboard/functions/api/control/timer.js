import { hasOwnerAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";
import {
  currentElapsed,
  insertCompletedSession,
  materializeTimer,
  nowIso,
  readTimer,
  settleExpiredTimer,
  timerIsActive,
  validateTimerStart,
} from "../../_lib/cloud_state.js";

async function requireOwner(request, env) {
  if (!hasOwnerAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Owner key required." }, 403);
  }
  return null;
}

export async function onRequestGet(context) {
  const denied = await requireOwner(context.request, context.env);
  if (denied) return denied;
  const row = await settleExpiredTimer(context.env);
  return jsonResponse({ ok: true, timer: materializeTimer(row) });
}

export async function onRequestPost(context) {
  const { request, env } = context;
  const denied = await requireOwner(request, env);
  if (denied) return denied;

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ ok: false, error: "Body must be valid JSON." }, 400);
  }

  const action = String(body?.action || "").toLowerCase();
  let row = await settleExpiredTimer(env);
  const now = nowIso();

  try {
    if (action === "start") {
      if (timerIsActive(row)) {
        return jsonResponse({ ok: false, error: "A timer is already active." }, 409);
      }
      const timer = validateTimerStart(body);
      const sessionId = crypto.randomUUID();
      await env.DB.prepare(
        `INSERT INTO cloud_timer(
           id,session_id,revision,status,task,category,mode,counts_toward_focus,
           duration_seconds,elapsed_seconds,started_at,running_since,updated_at,source,timezone
         ) VALUES(1,?,1,'running',?,?,?,?,?,0,?,?,?,'web',?)
         ON CONFLICT(id) DO UPDATE SET
           session_id=excluded.session_id,
           revision=cloud_timer.revision+1,
           status='running', task=excluded.task, category=excluded.category,
           mode=excluded.mode, counts_toward_focus=excluded.counts_toward_focus,
           duration_seconds=excluded.duration_seconds, elapsed_seconds=0,
           started_at=excluded.started_at, running_since=excluded.running_since,
           updated_at=excluded.updated_at, source='web', timezone=excluded.timezone`
      ).bind(
        sessionId,
        timer.task,
        timer.category,
        timer.mode,
        timer.counts_toward_focus ? 1 : 0,
        timer.duration_seconds,
        now,
        now,
        now,
        timer.timezone,
      ).run();
    } else if (action === "pause") {
      if (!row || row.status !== "running") {
        return jsonResponse({ ok: false, error: "No running timer to pause." }, 409);
      }
      const elapsed = currentElapsed(row);
      await env.DB.prepare(
        `UPDATE cloud_timer SET status='paused', elapsed_seconds=?, running_since=NULL,
         revision=revision+1, updated_at=?, source='web' WHERE id=1`
      ).bind(elapsed, now).run();
    } else if (action === "resume") {
      if (!row || row.status !== "paused") {
        return jsonResponse({ ok: false, error: "No paused timer to resume." }, 409);
      }
      await env.DB.prepare(
        `UPDATE cloud_timer SET status='running', running_since=?,
         revision=revision+1, updated_at=?, source='web' WHERE id=1`
      ).bind(now, now).run();
    } else if (action === "finish") {
      if (!timerIsActive(row)) {
        return jsonResponse({ ok: false, error: "No active timer to finish." }, 409);
      }
      const elapsed = currentElapsed(row);
      await insertCompletedSession(env, row, now);
      await env.DB.prepare(
        `UPDATE cloud_timer SET status='completed', elapsed_seconds=?, running_since=NULL,
         revision=revision+1, updated_at=?, source='web' WHERE id=1`
      ).bind(elapsed, now).run();
    } else if (action === "cancel" || action === "reset") {
      if (!row) {
        return jsonResponse({ ok: true, timer: materializeTimer(null) });
      }
      await env.DB.prepare(
        `UPDATE cloud_timer SET status='canceled', elapsed_seconds=?, running_since=NULL,
         revision=revision+1, updated_at=?, source='web' WHERE id=1`
      ).bind(currentElapsed(row), now).run();
    } else {
      return jsonResponse({ ok: false, error: "Unknown timer action." }, 400);
    }
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error?.message || error) }, 400);
  }

  row = await readTimer(env);
  return jsonResponse({ ok: true, timer: materializeTimer(row) });
}

export function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: { "Allow": "GET, POST, OPTIONS", "Cache-Control": "no-store" },
  });
}
