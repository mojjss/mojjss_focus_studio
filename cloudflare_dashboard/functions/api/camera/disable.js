import { hasWriteAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

export async function onRequestPost({ request, env }) {
  if (!hasWriteAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Invalid desktop write key." }, 401);
  }
  const now = new Date().toISOString();
  await env.DB.prepare(
    `UPDATE camera_requests
     SET status='cancelled', completed_at=?, message='Camera photo mode disabled.'
     WHERE status IN ('pending','processing')`
  ).bind(now).run();
  return jsonResponse({ ok: true });
}
