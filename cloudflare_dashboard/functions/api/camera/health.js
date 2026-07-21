import { hasReadAccess, hasWriteAccess } from "../../_lib/auth.js";
import { jsonResponse } from "../../_lib/response.js";

export function onRequestGet({ request, env }) {
  if (!hasReadAccess(request, env) && !hasWriteAccess(request, env)) {
    return jsonResponse({ ok: false, error: "Invalid access key." }, 401);
  }
  return jsonResponse({
    ok: true,
    camera_api_version: 3,
    modes: ["live", "photos"],
  });
}
