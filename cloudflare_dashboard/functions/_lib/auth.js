function getBearer(request) {
  const value = request.headers.get("Authorization") || "";
  return value.startsWith("Bearer ") ? value.slice(7).trim() : "";
}

function safeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string") return false;
  if (!right || left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

function suppliedKey(request) {
  return (
    request.headers.get("X-Dashboard-Key") ||
    getBearer(request) ||
    ""
  ).trim();
}

export function getAccessRole(request, env) {
  const supplied = suppliedKey(request);
  if (safeEqual(supplied, env.DASHBOARD_OWNER_KEY || "")) {
    return "owner";
  }
  if (
    safeEqual(supplied, env.DASHBOARD_VIEWER_KEY || "") ||
    safeEqual(supplied, env.DASHBOARD_READ_KEY || "")
  ) {
    return "viewer";
  }
  return null;
}

export function hasReadAccess(request, env) {
  return Boolean(getAccessRole(request, env));
}

export function hasOwnerAccess(request, env) {
  return getAccessRole(request, env) === "owner";
}

export function hasWriteAccess(request, env) {
  const supplied =
    request.headers.get("X-Write-Key") ||
    getBearer(request);
  return safeEqual(supplied || "", env.DESKTOP_WRITE_KEY || "");
}
