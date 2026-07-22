import { hasReadAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

const ID_RE = /^[A-Za-z0-9_-]{16,80}$/;
const PROOF_RE = /^[A-Za-z0-9_-]{40,80}$/;

function safeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string") return false;
  if (!right || left.length !== right.length) return false;

  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

function decodeBase64(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);

  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }

  return bytes;
}

export async function onRequestGet() {
  return jsonResponse(
    {
      ok: false,
      error: "Photo download requires the camera password.",
    },
    405,
    { Allow: "POST" },
  );
}

export async function onRequestPost({ request, env }) {
  if (!hasReadAccess(request, env)) {
    return jsonResponse(
      { ok: false, error: "Invalid dashboard key." },
      401,
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse(
      { ok: false, error: "Body must be valid JSON." },
      400,
    );
  }

  const requestId = String(body?.request_id || "").trim();
  const proof = String(body?.proof || "").trim();

  if (!ID_RE.test(requestId) || !PROOF_RE.test(proof)) {
    return jsonResponse(
      { ok: false, error: "Camera password proof is required." },
      401,
    );
  }

  const row = await env.DB.prepare(
    `SELECT
         f.image_base64,
         f.captured_at,
         r.proof
       FROM camera_frames f
       JOIN camera_requests r
         ON r.request_id = f.request_id
      WHERE f.request_id = ?
        AND r.status = 'ready'`,
  ).bind(requestId).first();

  if (!row) {
    return jsonResponse(
      { ok: false, error: "Photo is not ready." },
      404,
    );
  }

  if (!safeEqual(proof, String(row.proof || ""))) {
    return jsonResponse(
      { ok: false, error: "Incorrect camera password." },
      401,
    );
  }

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
