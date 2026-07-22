import { hasReadAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

const ID_RE = /^[A-Za-z0-9_-]{16,80}$/;
const PROOF_RE = /^[A-Za-z0-9_-]{40,80}$/;
const MAX_RECENT = 12;

function safeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string") return false;
  if (!right || left.length !== right.length) return false;

  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

export async function onRequestGet() {
  return jsonResponse(
    {
      ok: false,
      error: "Recent photos require the camera password.",
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

  const supplied = Array.isArray(body?.proofs)
    ? body.proofs.slice(0, MAX_RECENT)
    : [];

  if (!supplied.length) {
    return jsonResponse(
      { ok: false, error: "Camera password proof is required." },
      401,
    );
  }

  const proofById = new Map();
  for (const item of supplied) {
    const requestId = String(item?.request_id || "").trim();
    const proof = String(item?.proof || "").trim();

    if (!ID_RE.test(requestId) || !PROOF_RE.test(proof)) {
      return jsonResponse(
        { ok: false, error: "Invalid camera password proof." },
        400,
      );
    }

    proofById.set(requestId, proof);
  }

  const result = await env.DB.prepare(
    `SELECT
         r.request_id,
         r.proof,
         r.completed_at,
         f.captured_at,
         f.width,
         f.height,
         f.bytes
       FROM camera_requests r
       JOIN camera_frames f
         ON f.request_id = r.request_id
      WHERE r.status = 'ready'
      ORDER BY r.completed_at DESC
      LIMIT 12`,
  ).all();

  const readyRows = result.results || [];
  const photos = readyRows
    .filter((row) =>
      safeEqual(
        proofById.get(String(row.request_id)) || "",
        String(row.proof || ""),
      ),
    )
    .map((row) => ({
      request_id: row.request_id,
      completed_at: row.completed_at,
      captured_at: row.captured_at,
      width: row.width,
      height: row.height,
      bytes: row.bytes,
    }));

  if (readyRows.length && !photos.length) {
    return jsonResponse(
      { ok: false, error: "Incorrect camera password." },
      401,
    );
  }

  return jsonResponse({
    ok: true,
    photos,
  });
}
