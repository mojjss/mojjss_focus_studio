import { jsonResponse } from "../_lib/response.js";

export function onRequestGet() {
  return jsonResponse({
    ok: true,
    service: "pixela-focus-dashboard",
    time: new Date().toISOString(),
  });
}
