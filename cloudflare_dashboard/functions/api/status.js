import { getAccessRole } from "../_lib/auth.js";
import { jsonResponse } from "../_lib/response.js";

export async function onRequestGet(context) {
  const { request, env } = context;
  const role = getAccessRole(request, env);

  if (!role) {
    return jsonResponse(
      { ok: false, error: "Invalid dashboard key." },
      401,
      { "WWW-Authenticate": 'Bearer realm="Focus Dashboard"' },
    );
  }

  const row = await env.DB.prepare(
    "SELECT payload, received_at, device_updated_at FROM dashboard_snapshot WHERE id = 1"
  ).first();

  if (!row) {
    return jsonResponse({
      ok: true,
      has_data: false,
      access: { role },
      cloud: {
        desktop_online: false,
        received_at: null,
        age_seconds: null,
      },
    });
  }

  let payload;
  try {
    payload = JSON.parse(row.payload);
  } catch {
    return jsonResponse(
      { ok: false, error: "Stored dashboard data is invalid." },
      500,
    );
  }

  const receivedMs = Date.parse(row.received_at);
  const ageSeconds = Number.isFinite(receivedMs)
    ? Math.max(0, Math.floor((Date.now() - receivedMs) / 1000))
    : null;

  payload.cloud = {
    desktop_online: ageSeconds !== null && ageSeconds <= 180,
    received_at: row.received_at,
    device_updated_at: row.device_updated_at,
    age_seconds: ageSeconds,
  };
  payload.access = { role };
  payload.has_data = true;

  const recent = Array.isArray(payload.recent) ? payload.recent : [];
  if (role === "viewer") {
    payload.recent = recent.slice(-30).map((item) => {
      const copy = { ...item };
      delete copy.notes;
      delete copy.source;
      return copy;
    });
  } else {
    payload.recent = recent.slice(-250);
  }

  return jsonResponse(payload);
}
