import { hasReadAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

export async function onRequestGet({ request, env }) {
  if (!hasReadAccess(request, env)) {
    return jsonResponse(
      { ok: false, error: "Invalid dashboard key." },
      401,
    );
  }

  /*
   * Return only opaque random request IDs. Photo timestamps, dimensions,
   * status details, and image data remain hidden until the camera password
   * proof is verified by /api/camera/recent.
   */
  const result = await env.DB.prepare(
    `SELECT request_id
       FROM camera_requests
      WHERE status = 'ready'
      ORDER BY completed_at DESC
      LIMIT 12`,
  ).all();

  return jsonResponse({
    ok: true,
    request_ids: (result.results || []).map((row) => row.request_id),
  });
}
