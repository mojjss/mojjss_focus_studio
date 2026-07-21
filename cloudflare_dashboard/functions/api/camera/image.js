import { hasReadAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

function decodeBase64(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export async function onRequestGet({ request, env }) {
  if (!hasReadAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Invalid dashboard key." }, 401);
  }
  const requestId = new URL(request.url).searchParams.get("request_id") || "";
  const row = await env.DB.prepare(
    `SELECT f.image_base64,f.captured_at
     FROM camera_frames f
     JOIN camera_requests r ON r.request_id=f.request_id
     WHERE f.request_id=? AND r.status='ready'`
  ).bind(requestId).first();
  if (!row) return jsonResponse({ ok: false, error: "Photo is not ready." }, 404);
  return new Response(decodeBase64(row.image_base64), {
    status: 200,
    headers: {
      "Content-Type": "image/jpeg",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "Content-Disposition": "inline; filename=focus-studio-photo.jpg",
      "X-Captured-At": row.captured_at || "",
    },
  });
}
