import { getAccessRole } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

const ID_RE = /^[A-Za-z0-9_-]{16,80}$/;
const PROOF_RE = /^[A-Za-z0-9_-]{40,80}$/;

export async function onRequestPost({ request, env }) {
  const role = getAccessRole(request, env);
  if (!role) return jsonResponse({ ok: false, error: "Invalid dashboard key." }, 401);

  let body;
  try { body = await request.json(); }
  catch { return jsonResponse({ ok: false, error: "Body must be valid JSON." }, 400); }

  const requestId = String(body?.request_id || "").trim();
  const proof = String(body?.proof || "").trim();
  if (!ID_RE.test(requestId) || !PROOF_RE.test(proof)) {
    return jsonResponse({ ok: false, error: "Invalid photo request." }, 400);
  }

  const now = new Date().toISOString();
  const old = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const queued = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM camera_requests WHERE status IN ('pending','processing')"
  ).first();
  if (Number(queued?.count || 0) >= 8) {
    return jsonResponse({ ok: false, error: "The camera queue is busy. Try again shortly." }, 429);
  }

  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO camera_requests
       (request_id,status,proof,requested_by,requested_at,claimed_at,completed_at,message)
       VALUES (?, 'pending', ?, ?, ?, NULL, NULL, '')`
    ).bind(requestId, proof, role, now),
    env.DB.prepare("DELETE FROM camera_frames WHERE request_id IN (SELECT request_id FROM camera_requests WHERE requested_at < ?)").bind(old),
    env.DB.prepare("DELETE FROM camera_requests WHERE requested_at < ?").bind(old),
  ]);

  return jsonResponse({ ok: true, request_id: requestId, status: "pending" }, 202);
}
