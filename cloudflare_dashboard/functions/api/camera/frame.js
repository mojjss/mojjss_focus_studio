import { hasWriteAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

const ID_RE = /^[A-Za-z0-9_-]{16,80}$/;
const MAX_BASE64 = 900000;

export async function onRequestPost({ request, env }) {
  if (!hasWriteAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Invalid desktop write key." }, 401);
  }
  let body;
  try { body = await request.json(); }
  catch { return jsonResponse({ ok: false, error: "Body must be valid JSON." }, 400); }

  const requestId = String(body?.request_id || "").trim();
  if (!ID_RE.test(requestId)) {
    return jsonResponse({ ok: false, error: "Invalid request ID." }, 400);
  }
  const existing = await env.DB.prepare(
    "SELECT status FROM camera_requests WHERE request_id=?"
  ).bind(requestId).first();
  if (!existing) return jsonResponse({ ok: false, error: "Photo request not found." }, 404);

  const now = new Date().toISOString();
  const error = String(body?.error || "").trim();
  if (error) {
    await env.DB.prepare(
      "UPDATE camera_requests SET status='error', completed_at=?, message=? WHERE request_id=?"
    ).bind(now, error.slice(0, 300), requestId).run();
    return jsonResponse({ ok: true, status: "error" });
  }

  const image = String(body?.image_base64 || "").trim();
  if (!image || image.length > MAX_BASE64 || !/^[A-Za-z0-9+/=]+$/.test(image)) {
    return jsonResponse({ ok: false, error: "Invalid or oversized JPEG payload." }, 413);
  }
  const width = Math.max(1, Math.min(4096, Number(body?.width) || 0));
  const height = Math.max(1, Math.min(4096, Number(body?.height) || 0));
  const bytes = Math.max(1, Math.min(700000, Number(body?.bytes) || 0));
  const capturedAt = String(body?.captured_at || now).slice(0, 40);

  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO camera_frames
       (request_id,image_base64,captured_at,width,height,bytes)
       VALUES (?,?,?,?,?,?)
       ON CONFLICT(request_id) DO UPDATE SET
       image_base64=excluded.image_base64,
       captured_at=excluded.captured_at,
       width=excluded.width,
       height=excluded.height,
       bytes=excluded.bytes`
    ).bind(requestId, image, capturedAt, width, height, bytes),
    env.DB.prepare(
      "UPDATE camera_requests SET status='ready', completed_at=?, message='Photo captured.' WHERE request_id=?"
    ).bind(now, requestId),
  ]);
  return jsonResponse({ ok: true, status: "ready" });
}
