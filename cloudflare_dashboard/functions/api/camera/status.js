import { hasReadAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

export async function onRequestGet({ request, env }) {
  if (!hasReadAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Invalid dashboard key." }, 401);
  }
  const url = new URL(request.url);
  const requestId = String(url.searchParams.get("request_id") || "").trim();
  const row = await env.DB.prepare(
    `SELECT r.request_id,r.status,r.requested_at,r.claimed_at,r.completed_at,r.message,
            f.captured_at,f.width,f.height,f.bytes
     FROM camera_requests r
     LEFT JOIN camera_frames f ON f.request_id=r.request_id
     WHERE r.request_id=?`
  ).bind(requestId).first();
  if (!row) return jsonResponse({ ok: false, error: "Photo request not found." }, 404);
  return jsonResponse({ ok: true, ...row });
}
