import { hasReadAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

export async function onRequestGet({ request, env }) {
  if (!hasReadAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Invalid dashboard key." }, 401);
  }
  const result = await env.DB.prepare(
    `SELECT r.request_id,r.completed_at,f.captured_at,f.width,f.height,f.bytes
     FROM camera_requests r
     JOIN camera_frames f ON f.request_id=r.request_id
     WHERE r.status='ready'
     ORDER BY r.completed_at DESC
     LIMIT 12`
  ).all();
  return jsonResponse({ ok: true, photos: result.results || [] });
}
