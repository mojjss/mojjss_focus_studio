import { hasWriteAccess } from "../_lib/auth.js";
import { jsonResponse } from "../_lib/response.js";

const MAX_BODY_BYTES = 600_000;

function validPayload(payload) {
  return (
    payload &&
    typeof payload === "object" &&
    payload.timer &&
    typeof payload.timer === "object" &&
    payload.today &&
    typeof payload.today === "object"
  );
}

export async function onRequestPost(context) {
  const { request, env } = context;

  if (!hasWriteAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Invalid desktop write key." }, 401);
  }

  const contentLength = Number(request.headers.get("Content-Length") || 0);
  if (contentLength > MAX_BODY_BYTES) {
    return jsonResponse({ ok: false, error: "Payload is too large." }, 413);
  }

  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
    return jsonResponse({ ok: false, error: "Payload is too large." }, 413);
  }

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return jsonResponse({ ok: false, error: "Body must be valid JSON." }, 400);
  }

  if (!validPayload(payload)) {
    return jsonResponse(
      { ok: false, error: "Payload must contain timer and today objects." },
      400,
    );
  }

  const receivedAt = new Date().toISOString();
  const deviceUpdatedAt =
    payload?.timer?.updated_at ||
    payload?.server_time ||
    null;

  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO dashboard_snapshot
         (id, payload, received_at, device_updated_at, schema_version)
       VALUES (1, ?, ?, ?, 1)
       ON CONFLICT(id) DO UPDATE SET
         payload = excluded.payload,
         received_at = excluded.received_at,
         device_updated_at = excluded.device_updated_at,
         schema_version = excluded.schema_version`
    ).bind(JSON.stringify(payload), receivedAt, deviceUpdatedAt),
    env.DB.prepare(
      `INSERT INTO ingest_log(received_at, status, payload_bytes, message)
       VALUES (?, 'success', ?, 'Snapshot received')`
    ).bind(receivedAt, raw.length),
    env.DB.prepare(
      `DELETE FROM ingest_log
       WHERE id NOT IN (
         SELECT id FROM ingest_log ORDER BY id DESC LIMIT 500
       )`
    ),
  ]);

  return jsonResponse({
    ok: true,
    received_at: receivedAt,
  });
}

export function onRequestOptions() {
  return new Response(null, {
    status: 204,
    headers: {
      "Allow": "POST, OPTIONS",
      "Cache-Control": "no-store",
    },
  });
}
