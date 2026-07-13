import { hasOwnerAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";
import { nowIso, validateScheduleItem } from "../../_lib/cloud_state.js";

function denied(request, env) {
  return hasOwnerAccess(request, env)
    ? null
    : jsonResponse({ ok: false, error: "Owner key required." }, 403);
}

export async function onRequestGet(context) {
  const rejection = denied(context.request, context.env);
  if (rejection) return rejection;
  const { results = [] } = await context.env.DB.prepare(
    `SELECT * FROM cloud_schedule WHERE deleted=0 ORDER BY date,start,title`
  ).all();
  return jsonResponse({ ok: true, schedule: results });
}

export async function onRequestPost(context) {
  const { request, env } = context;
  const rejection = denied(request, env);
  if (rejection) return rejection;

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ ok: false, error: "Body must be valid JSON." }, 400);
  }
  const action = String(body?.action || "upsert").toLowerCase();
  const now = nowIso();

  if (action === "delete") {
    const id = String(body?.id || "").slice(0, 100);
    if (!id) return jsonResponse({ ok: false, error: "Missing event id." }, 400);
    const current = await env.DB.prepare(
      "SELECT revision FROM cloud_schedule WHERE id=?"
    ).bind(id).first();
    if (!current) return jsonResponse({ ok: false, error: "Event not found." }, 404);
    if (body.base_revision !== undefined && Number(body.base_revision) !== Number(current.revision)) {
      return jsonResponse({ ok: false, error: "This event changed in another client. Refresh and try again." }, 409);
    }
    await env.DB.prepare(
      `UPDATE cloud_schedule SET deleted=1, revision=revision+1,
       updated_at=?, source='web' WHERE id=?`
    ).bind(now, id).run();
  } else {
    let item;
    try {
      item = validateScheduleItem(body);
    } catch (error) {
      return jsonResponse({ ok: false, error: String(error?.message || error) }, 400);
    }
    const current = await env.DB.prepare(
      "SELECT revision FROM cloud_schedule WHERE id=?"
    ).bind(item.id).first();
    if (current && body.base_revision !== undefined && Number(body.base_revision) !== Number(current.revision)) {
      return jsonResponse({ ok: false, error: "This event changed in another client. Refresh and try again." }, 409);
    }
    await env.DB.prepare(
      `INSERT INTO cloud_schedule(
         id,date,start,end,title,category,notes,revision,deleted,updated_at,source
       ) VALUES(?,?,?,?,?,?,?,1,0,?,'web')
       ON CONFLICT(id) DO UPDATE SET
         date=excluded.date, start=excluded.start, end=excluded.end,
         title=excluded.title, category=excluded.category, notes=excluded.notes,
         revision=cloud_schedule.revision+1, deleted=0,
         updated_at=excluded.updated_at, source='web'`
    ).bind(
      item.id, item.date, item.start, item.end, item.title,
      item.category, item.notes, now,
    ).run();
  }

  const { results = [] } = await env.DB.prepare(
    `SELECT * FROM cloud_schedule WHERE deleted=0 ORDER BY date,start,title`
  ).all();
  return jsonResponse({ ok: true, schedule: results });
}

export function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: { "Allow": "GET, POST, OPTIONS", "Cache-Control": "no-store" },
  });
}
