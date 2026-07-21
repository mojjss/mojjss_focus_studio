import { hasWriteAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

export async function onRequestGet({ request, env }) {
  if (!hasWriteAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Invalid desktop write key." }, 401);
  }
  const expiry = new Date(Date.now() - 3 * 60 * 1000).toISOString();
  await env.DB.prepare(
    `UPDATE camera_requests
     SET status='expired', completed_at=?, message='Request expired before capture.'
     WHERE status='pending' AND requested_at < ?`
  ).bind(new Date().toISOString(), expiry).run();

  const row = await env.DB.prepare(
    `SELECT request_id, proof, requested_at
     FROM camera_requests
     WHERE status='pending'
     ORDER BY requested_at ASC
     LIMIT 1`
  ).first();
  if (!row) return jsonResponse({ ok: true, request_id: null });

  const claimedAt = new Date().toISOString();
  const update = await env.DB.prepare(
    `UPDATE camera_requests SET status='processing', claimed_at=?
     WHERE request_id=? AND status='pending'`
  ).bind(claimedAt, row.request_id).run();
  if (!update.meta?.changes) return jsonResponse({ ok: true, request_id: null });

  return jsonResponse({
    ok: true,
    request_id: row.request_id,
    proof: row.proof,
    requested_at: row.requested_at,
  });
}
