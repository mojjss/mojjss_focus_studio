const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);

const formatSeconds = (seconds) => {
  seconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
};

const prettyMinutes = (minutes) => {
  minutes = Math.max(0, Number(minutes) || 0);
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return hours ? (remainder ? `${hours}h ${remainder}m` : `${hours}h`) : `${minutes}m`;
};

const minuteOfDay = (time) => {
  if (!time || !/^\d\d:\d\d/.test(time)) return null;
  const [hours, minutes] = time.slice(0, 5).split(":").map(Number);
  return hours * 60 + minutes;
};

const ageText = (seconds) => {
  if (seconds === null || seconds === undefined) return "unknown";
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
};

let readKey = localStorage.getItem("focusDashboardReadKey") || "";
let latest = null;
let fetchedAt = performance.now();
let graphKey = "";
let summaryPeriod = "today";
let scheduleView = "day";
const RECENT_PAGE_SIZE = 10;
let recentPage = 1;
let privateCameraViewing = false;
let privateCameraBusy = false;
let privateCameraToken = "";
let privateCameraIdentity = "";
let privateCameraHeartbeatTimer = null;
let privateCameraPageStopping = false;
let privateCameraMessagePinned = false;
let privateCameraPasswordAccepted = false;
let theme = localStorage.getItem("focusTheme") || "dark";
document.documentElement.dataset.theme = theme;

$("themeButton").onclick = () => {
  theme = theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("focusTheme", theme);
};

$("logoutButton").onclick = () => {
  localStorage.removeItem("focusDashboardReadKey");
  readKey = "";
  latest = null;
  recentPage = 1;
  stopPrivateCamera(false);
  $("login").classList.remove("hidden");
  $("connection").textContent = "Locked";
  $("connection").className = "badge offline";
};

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  readKey = $("readKey").value.trim();
  $("loginError").textContent = "Checking…";
  const success = await refresh(true);
  if (success) {
    recentPage = 1;
    localStorage.setItem("focusDashboardReadKey", readKey);
    $("login").classList.add("hidden");
    $("loginError").textContent = "";
  }
});

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function parseLocalDate(value) {
  const [year, month, day] = String(value).split("-").map(Number);
  return new Date(year, month - 1, day);
}

function eventsForDate(items, dateValue) {
  return (items || []).filter((item) => item.date === dateValue);
}

function eventMarkup(item) {
  return `<div class="calendar-event">
    <span class="event-time">${esc(item.start || "")}</span>
    ${esc(item.title || "Scheduled activity")}
  </div>`;
}

function renderDaySchedule(items) {
  const todayValue = latest?.calendar?.today || isoDate(new Date());
  const dayItems = eventsForDate(items, todayValue);
  const now = new Date();
  const current = now.getHours() * 60 + now.getMinutes();

  if (!dayItems.length) {
    $("schedule").innerHTML = '<div class="empty">No scheduled items today.</div>';
    $("nextEvent").innerHTML =
      '<span class="label">NEXT</span><b>No upcoming item</b>';
    return;
  }

  let next = null;
  $("schedule").innerHTML = dayItems.map((item) => {
    const start = minuteOfDay(item.start);
    const end = minuteOfDay(item.end);
    let className = "";
    if (start !== null && end !== null && current >= start && current < end) {
      className = " now";
    } else if (end !== null && current >= end) {
      className = " done";
    }
    if (!next && start !== null && start >= current) next = item;
    return `<div class="item${className}">
      <div class="time">${esc(item.start || "")}</div>
      <div>
        <div class="item-title">${esc(item.title || "Scheduled activity")}</div>
        <div class="small">${esc(item.category || "")}${item.recurring === "1" ? " · repeats daily" : ""}</div>
      </div>
      <div class="right small">${esc(item.end || "")}</div>
    </div>`;
  }).join("");

  $("nextEvent").innerHTML = next
    ? `<span class="label">NEXT · ${esc(next.start || "")}</span>
       <b>${esc(next.title || "Scheduled activity")}</b>
       <div class="small">${esc(next.category || "")}</div>`
    : '<span class="label">NEXT</span><b>Schedule complete for today</b>';
}

function renderWeekSchedule(items) {
  const calendar = latest?.calendar || {};
  const start = parseLocalDate(calendar.week_start || isoDate(new Date()));
  const todayValue = calendar.today || isoDate(new Date());
  const columns = [];

  for (let offset = 0; offset < 7; offset += 1) {
    const date = new Date(start);
    date.setDate(start.getDate() + offset);
    const dateValue = isoDate(date);
    const dayItems = eventsForDate(items, dateValue);
    columns.push(`<div class="week-day${dateValue === todayValue ? " today" : ""}">
      <div class="day-header">${date.toLocaleDateString(undefined, {weekday:"short", month:"short", day:"numeric"})}</div>
      ${dayItems.length ? dayItems.map(eventMarkup).join("") : '<div class="small">No events</div>'}
    </div>`);
  }

  $("nextEvent").style.display = "none";
  $("schedule").innerHTML = `<div class="week-grid">${columns.join("")}</div>`;
}

function renderMonthSchedule(items) {
  const calendar = latest?.calendar || {};
  const monthStart = parseLocalDate(calendar.month_start || isoDate(new Date()));
  const monthEnd = parseLocalDate(calendar.month_end || isoDate(new Date()));
  const todayValue = calendar.today || isoDate(new Date());
  const gridStart = new Date(monthStart);
  const mondayIndex = (gridStart.getDay() + 6) % 7;
  gridStart.setDate(gridStart.getDate() - mondayIndex);

  const weekdays = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    .map((name) => `<div class="month-weekday">${name}</div>`).join("");
  const cells = [];

  for (let offset = 0; offset < 42; offset += 1) {
    const date = new Date(gridStart);
    date.setDate(gridStart.getDate() + offset);
    const dateValue = isoDate(date);
    const dayItems = eventsForDate(items, dateValue);
    const outside = date < monthStart || date > monthEnd;
    const visible = dayItems.slice(0, 3);
    cells.push(`<div class="month-day${outside ? " outside" : ""}${dateValue === todayValue ? " today" : ""}">
      <div class="month-number">${date.getDate()}</div>
      ${visible.map(eventMarkup).join("")}
      ${dayItems.length > 3 ? `<div class="more-events">+${dayItems.length - 3} more</div>` : ""}
    </div>`);
  }

  $("nextEvent").style.display = "none";
  $("schedule").innerHTML =
    `<div class="month-grid">${weekdays}${cells.join("")}</div>`;
}

function renderSchedule() {
  if (!latest) return;
  const items = latest.schedule_events || latest.schedule || [];
  $("nextEvent").style.display = "";

  if (scheduleView === "week") {
    $("scheduleTitle").textContent = "Current week";
    renderWeekSchedule(items);
  } else if (scheduleView === "month") {
    const start = parseLocalDate(latest.calendar?.month_start || isoDate(new Date()));
    $("scheduleTitle").textContent =
      start.toLocaleDateString(undefined, {month:"long", year:"numeric"});
    renderMonthSchedule(items);
  } else {
    $("scheduleTitle").textContent = "Today's schedule";
    renderDaySchedule(items);
  }
}

function renderPeriodSummary() {
  if (!latest) return;
  const fallback = latest.today || {};
  const summary = latest.periods?.[summaryPeriod] || fallback;
  const titles = {
    today: "Today at a glance",
    week: "Current week",
    month: "Current month",
  };
  $("summaryTitle").textContent = titles[summaryPeriod];

  $("focus").textContent = prettyMinutes(summary.focus_minutes || 0);
  $("focusHours").textContent =
    `${(Number(summary.focus_minutes || 0) / 60).toFixed(1).replace(".0", "")} hours`;
  $("productive").textContent =
    prettyMinutes(summary.other_productive_minutes || 0);
  $("totalProductive").textContent =
    prettyMinutes(
      summary.total_productive_minutes ??
      ((summary.focus_minutes || 0) + (summary.other_productive_minutes || 0))
    );
  $("sessions").textContent = summary.total_productive_sessions || 0;
}
function formatRecentDate(item) {
  if (item.started_at_utc) {
    const date = new Date(item.started_at_utc);
    if (Number.isFinite(date.getTime())) {
      return {
        date: date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }),
        day: date.toLocaleDateString(undefined, { weekday: "long" }),
        clock: date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }),
      };
    }
  }
  if (item.display_date && item.weekday) {
    return { date: item.display_date, day: item.weekday, clock: item.start || "" };
  }
  if (!item.date) return { date: "Unknown date", day: "", clock: item.start || "" };
  const date = parseLocalDate(item.date);
  return {
    date: date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }),
    day: date.toLocaleDateString(undefined, { weekday: "long" }),
    clock: item.start || "",
  };
}

function paginationTokens(currentPage, totalPages) {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const pages = new Set([1, totalPages]);
  for (let page = currentPage - 2; page <= currentPage + 2; page += 1) {
    if (page > 1 && page < totalPages) pages.add(page);
  }

  const ordered = [...pages].sort((left, right) => left - right);
  const tokens = [];
  ordered.forEach((page, index) => {
    if (index && page - ordered[index - 1] > 1) tokens.push("ellipsis");
    tokens.push(page);
  });
  return tokens;
}

function renderRecentPagination(totalPages) {
  const container = $("recentPagination");
  if (!container) return;

  if (totalPages <= 1) {
    container.innerHTML = "";
    container.hidden = true;
    return;
  }

  container.hidden = false;
  const pageButtons = paginationTokens(recentPage, totalPages).map((token) => {
    if (token === "ellipsis") {
      return '<span class="page-ellipsis" aria-hidden="true">…</span>';
    }
    const current = token === recentPage;
    return `<button type="button" class="page-button${current ? " active" : ""}"
      data-page="${token}" aria-label="Go to sessions page ${token}"
      ${current ? 'aria-current="page"' : ""}>${token}</button>`;
  }).join("");

  container.innerHTML = `
    <button type="button" class="page-button page-direction" data-page="${recentPage - 1}"
      aria-label="Previous sessions page" ${recentPage === 1 ? "disabled" : ""}>‹</button>
    ${pageButtons}
    <button type="button" class="page-button page-direction" data-page="${recentPage + 1}"
      aria-label="Next sessions page" ${recentPage === totalPages ? "disabled" : ""}>›</button>`;
}

function renderRecent(items) {
  const ordered = Array.isArray(items) ? items.slice().reverse() : [];
  const totalPages = Math.max(1, Math.ceil(ordered.length / RECENT_PAGE_SIZE));
  recentPage = Math.min(Math.max(1, recentPage), totalPages);

  const startIndex = (recentPage - 1) * RECENT_PAGE_SIZE;
  const visible = ordered.slice(startIndex, startIndex + RECENT_PAGE_SIZE);
  const firstShown = ordered.length ? startIndex + 1 : 0;
  const lastShown = Math.min(startIndex + RECENT_PAGE_SIZE, ordered.length);

  $("recentCount").textContent = ordered.length
    ? `${firstShown}–${lastShown} of ${ordered.length} sessions`
    : "0 sessions";

  if (!ordered.length) {
    $("recent").innerHTML = '<div class="empty">No completed sessions yet.</div>';
    renderRecentPagination(0);
    return;
  }

  $("recent").innerHTML = visible.map((item) => {
    const when = formatRecentDate(item);
    const ownerDetails = latest?.access?.role === "owner" && item.notes
      ? `<div class="small session-notes">${esc(item.notes)}</div>`
      : "";
    return `<div class="item">
      <div class="time">
        <span class="session-date">${esc(when.date)}</span>
        <span class="session-day">${esc(when.day)}</span>
        <span class="session-clock">${esc(when.clock || item.start || "")}</span>
      </div>
      <div>
        <div class="item-title">${esc(item.task || "Untitled session")}</div>
        <div class="small">${esc(item.mode || "")} · ${esc(item.category || "")}${item.counts_toward_focus ? " · focus" : ""}</div>
        ${ownerDetails}
      </div>
      <div class="right">${esc(item.minutes || 0)}m</div>
    </div>`;
  }).join("");

  renderRecentPagination(totalPages);
}

$("recentPagination").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-page]");
  if (!button || button.disabled) return;

  const nextPage = Number(button.dataset.page);
  if (!Number.isInteger(nextPage) || nextPage < 1) return;

  recentPage = nextPage;
  renderRecent(latest?.recent || []);
  $("recent").scrollIntoView({ behavior: "smooth", block: "start" });
});

function setApiChip(name, state, text) {
  const element = $(`api${name[0].toUpperCase()}${name.slice(1)}Chip`);
  if (!element) return;
  element.classList.remove("ok", "error");
  if (state === "ok") element.classList.add("ok");
  if (state === "error") element.classList.add("error");
  element.textContent = `${name}: ${text}`;
}

function privateCameraBaseUrl() {
  return String(latest?.camera?.private_url || "").replace(/\/+$/, "");
}

function setPrivateCameraMessage(
  message,
  kind = "info",
  pinned = false,
) {
  const element = $("cameraStatus");
  element.textContent = message;
  element.classList.remove(
    "status-error",
    "status-success",
    "status-working",
  );
  if (kind === "error") element.classList.add("status-error");
  if (kind === "success") element.classList.add("status-success");
  if (kind === "working") element.classList.add("status-working");
  privateCameraMessagePinned = pinned;
}

function setPrivateCameraStep(name, state = "active") {
  const order = ["tailnet", "password", "stream"];
  const index = order.indexOf(name);
  order.forEach((step, current) => {
    const element = $(
      `cameraStep${step[0].toUpperCase()}${step.slice(1)}`
    );
    if (!element) return;
    element.classList.remove("active", "complete", "error");
    if (state === "error" && current === index) {
      element.classList.add("error");
    } else if (current < index) {
      element.classList.add("complete");
    } else if (current === index) {
      element.classList.add(
        state === "complete" ? "complete" : "active"
      );
    }
  });
}

function resetPrivateCameraSteps() {
  ["Tailnet", "Password", "Stream"].forEach((name) => {
    $(`cameraStep${name}`).classList.remove(
      "active",
      "complete",
      "error",
    );
  });
}

function typedCameraPassword() {
  return $("cameraPassword").value.trim();
}

function availableCameraPassword() {
  return (
    typedCameraPassword() ||
    sessionStorage.getItem("mojjssPrivateCameraPassword") ||
    ""
  );
}

function updatePrivatePasswordState({
  error = "",
  accepted = false,
} = {}) {
  const input = $("cameraPassword");
  const state = $("cameraPasswordState");
  const typed = typedCameraPassword();
  const saved =
    sessionStorage.getItem("mojjssPrivateCameraPassword") || "";

  input.classList.remove("input-error", "input-ready");
  state.classList.remove("empty", "ready", "error", "accepted");

  if (error) {
    input.classList.add("input-error");
    state.classList.add("error");
    state.textContent = error;
    privateCameraPasswordAccepted = false;
    return;
  }

  if (accepted || privateCameraPasswordAccepted) {
    state.classList.add("accepted");
    state.textContent =
      "Password accepted for this browser tab";
    privateCameraPasswordAccepted = true;
    return;
  }

  if (typed) {
    input.classList.add("input-ready");
    state.classList.add("ready");
    state.textContent =
      `Password received • ${typed.length} character` +
      `${typed.length === 1 ? "" : "s"}`;
    return;
  }

  if (saved) {
    state.classList.add("accepted");
    state.textContent =
      "Saved camera password is ready for this tab";
    return;
  }

  state.classList.add("empty");
  state.textContent = "No camera password entered";
}

async function privateCameraFetch(path, options = {}) {
  const base = privateCameraBaseUrl();
  if (!base) {
    throw new Error(
      "The desktop app has not published a camera URL yet."
    );
  }

  let response;
  try {
    response = await fetch(
      `${base}${path}${path.includes("?") ? "&" : "?"}cb=${Date.now()}`,
      {
        mode: "cors",
        credentials: "omit",
        cache: "no-store",
        ...options,
      },
    );
  } catch (error) {
    throw new Error(
      "Could not reach the laptop through the secure camera route. " +
      "Confirm the desktop app and Cloudflare Tunnel are online, " +
      "then press Retry."
    );
  }

  const contentType =
    (response.headers.get("Content-Type") || "").toLowerCase();
  const raw = await response.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    throw new Error(
      `The private camera returned non-JSON content ` +
      `(HTTP ${response.status}, ${contentType || "unknown type"}).`
    );
  }

  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function checkPrivateCameraConnection({
  announce = true,
} = {}) {
  setPrivateCameraStep("tailnet");
  if (announce) {
    setPrivateCameraMessage(
      "Contacting the laptop through Cloudflare Tunnel…",
      "working",
      true,
    );
  }

  const status = await privateCameraFetch("/api/status");
  setPrivateCameraStep("tailnet", "complete");
  privateCameraIdentity = status.identity || "";
  $("cameraIdentity").textContent = privateCameraIdentity
    ? `Viewer identity: ${privateCameraIdentity}`
    : "Viewer identity unavailable";

  if (!status.enabled) {
    throw new Error(
      "The desktop owner has disabled the private camera."
    );
  }
  if (!status.password_protected) {
    throw new Error(
      "The desktop camera password is not configured."
    );
  }

  if (announce) {
    setPrivateCameraMessage(
      "Secure camera route is ready.",
      "success",
      false,
    );
  }
  return status;
}

function updatePrivateCameraControls() {
  const camera = latest?.camera || {};
  const base = privateCameraBaseUrl();
  const enabled = Boolean(camera.enabled);
  const passwordPresent = Boolean(availableCameraPassword());

  $("cameraPrivateUrl").textContent = base
    ? base
    : "Private URL not configured";
  $("cameraOpenPrivateButton").disabled = !base;

  $("cameraBadge").textContent = privateCameraViewing
    ? "Live"
    : enabled
      ? (base ? "Secure route ready" : "Needs setup")
      : "Disabled";
  $("cameraBadge").className =
    privateCameraViewing || (enabled && base)
      ? "badge"
      : "badge offline";

  $("cameraButton").disabled = privateCameraBusy;
  $("cameraButton").textContent = privateCameraViewing
    ? "Stop camera"
    : privateCameraBusy
      ? "Connecting…"
      : "Connect camera";
  $("cameraButton").classList.toggle(
    "stop",
    privateCameraViewing,
  );

  updatePrivatePasswordState();

  if (
    privateCameraMessagePinned ||
    privateCameraBusy ||
    privateCameraViewing
  ) {
    return;
  }

  if (!enabled) {
    setPrivateCameraMessage(
      "Enable “Allow private camera” in the desktop app.",
    );
  } else if (!base) {
    setPrivateCameraMessage(
      "Configure the public Cloudflare Tunnel camera URL in the desktop app.",
      "error",
      true,
    );
  } else if (!passwordPresent) {
    setPrivateCameraMessage(
      "Enter the camera viewer password.",
    );
  } else {
    setPrivateCameraMessage(
      "Password detected. Press “Connect camera”.",
      "success",
    );
  }
}

async function sendPrivateCameraHeartbeat() {
  if (
    !privateCameraViewing ||
    !privateCameraToken
  ) {
    return;
  }

  try {
    await privateCameraFetch("/api/heartbeat", {
      method: "POST",
      headers: {
        "X-Camera-Token": privateCameraToken,
      },
    });
  } catch (error) {
    const message = String(error.message || error);
    setPrivateCameraMessage(message, "error", true);
    stopPrivateCamera(false, true);
  }
}

function startPrivateCameraHeartbeat() {
  if (privateCameraHeartbeatTimer !== null) {
    clearInterval(privateCameraHeartbeatTimer);
  }
  sendPrivateCameraHeartbeat();
  privateCameraHeartbeatTimer = setInterval(
    sendPrivateCameraHeartbeat,
    4000,
  );
}

function sendPrivateCameraStopKeepalive() {
  const base = privateCameraBaseUrl();
  if (!base || !privateCameraToken) return;

  fetch(
    `${base}/api/stop?token=${encodeURIComponent(privateCameraToken)}`,
    {
      method: "POST",
      mode: "cors",
      credentials: "omit",
      cache: "no-store",
      keepalive: true,
    },
  ).catch(() => {});
}

async function stopPrivateCamera(
  sendStop = true,
  preserveMessage = false,
) {
  const token = privateCameraToken;
  privateCameraViewing = false;
  privateCameraBusy = false;
  privateCameraToken = "";
  privateCameraIdentity = "";

  if (privateCameraHeartbeatTimer !== null) {
    clearInterval(privateCameraHeartbeatTimer);
    privateCameraHeartbeatTimer = null;
  }

  const stream = $("cameraStream");
  stream.removeAttribute("src");
  $("cameraViewport").classList.remove("has-stream");
  $("cameraIdentity").textContent = "No viewer session yet";

  if (sendStop && token) {
    try {
      await privateCameraFetch("/api/stop", {
        method: "POST",
        headers: {
          "X-Camera-Token": token,
        },
      });
    } catch {}
  }

  privateCameraPageStopping = false;

  if (!preserveMessage) {
    privateCameraMessagePinned = false;
    resetPrivateCameraSteps();
    updatePrivateCameraControls();
  }
}

async function startPrivateCamera() {
  if (privateCameraBusy || privateCameraViewing) return;

  const password = availableCameraPassword();
  if (!password) {
    updatePrivatePasswordState({
      error: "Password required before connecting",
    });
    setPrivateCameraMessage(
      "Enter the separate camera viewer password.",
      "error",
      true,
    );
    $("cameraPassword").focus();
    return;
  }

  privateCameraBusy = true;
  privateCameraMessagePinned = true;
  privateCameraPasswordAccepted = false;
  updatePrivateCameraControls();

  try {
    await checkPrivateCameraConnection();

    setPrivateCameraStep("password");
    setPrivateCameraMessage(
      "Secure route connected. Verifying the camera password locally…",
      "working",
      true,
    );

    const unlocked = await privateCameraFetch("/api/unlock", {
      method: "POST",
      headers: {
        "X-Camera-Password": password,
      },
    });

    privateCameraToken = unlocked.token;
    privateCameraIdentity = unlocked.identity || "";
    privateCameraViewing = true;
    privateCameraPasswordAccepted = true;

    sessionStorage.setItem(
      "mojjssPrivateCameraPassword",
      password,
    );
    $("cameraPassword").value = "";
    updatePrivatePasswordState({ accepted: true });
    setPrivateCameraStep("stream");

    const stream = $("cameraStream");
    stream.onload = () => {
      $("cameraViewport").classList.add("has-stream");
      setPrivateCameraStep("stream", "complete");
      setPrivateCameraMessage(
        "Private live camera is playing through Cloudflare Tunnel.",
        "success",
        true,
      );
    };
    stream.onerror = () => {
      setPrivateCameraStep("stream", "error");
      setPrivateCameraMessage(
        "The private stream stopped. Check Cloudflare Tunnel and the desktop camera.",
        "error",
        true,
      );
    };
    $("cameraViewport").classList.add("has-stream");
    stream.src =
      `${privateCameraBaseUrl()}/camera/stream` +
      `?token=${encodeURIComponent(privateCameraToken)}` +
      `&cb=${Date.now()}`;

    $("cameraIdentity").textContent = privateCameraIdentity
      ? `Viewer identity: ${privateCameraIdentity}`
      : "Viewer session verified";
    setPrivateCameraMessage(
      "Password accepted. Starting the private stream…",
      "working",
      true,
    );
    startPrivateCameraHeartbeat();
  } catch (error) {
    const message = String(error.message || error);
    const passwordError =
      message.toLowerCase().includes("password");

    if (passwordError) {
      sessionStorage.removeItem(
        "mojjssPrivateCameraPassword",
      );
      privateCameraPasswordAccepted = false;
      updatePrivatePasswordState({
        error: "Password rejected — check it and try again",
      });
      setPrivateCameraStep("password", "error");
      $("cameraPassword").focus();
      $("cameraPassword").select();
    } else {
      setPrivateCameraStep("tailnet", "error");
    }

    await stopPrivateCamera(false, true);
    setPrivateCameraMessage(message, "error", true);
  } finally {
    privateCameraBusy = false;
    updatePrivateCameraControls();
  }
}

function emergencyStopPrivateCamera() {
  if (
    privateCameraPageStopping ||
    !privateCameraViewing
  ) {
    return;
  }
  privateCameraPageStopping = true;
  sendPrivateCameraStopKeepalive();

  if (privateCameraHeartbeatTimer !== null) {
    clearInterval(privateCameraHeartbeatTimer);
    privateCameraHeartbeatTimer = null;
  }

  $("cameraStream").removeAttribute("src");
  privateCameraViewing = false;
  privateCameraToken = "";
}

$("cameraButton").addEventListener("click", () => {
  if (privateCameraViewing) {
    stopPrivateCamera(true, false);
  } else {
    startPrivateCamera();
  }
});

$("cameraRetryButton").addEventListener("click", async () => {
  privateCameraMessagePinned = false;
  try {
    await checkPrivateCameraConnection();
  } catch (error) {
    setPrivateCameraStep("tailnet", "error");
    setPrivateCameraMessage(
      String(error.message || error),
      "error",
      true,
    );
  }
  updatePrivateCameraControls();
});

$("cameraOpenPrivateButton").addEventListener("click", () => {
  const base = privateCameraBaseUrl();
  if (base) {
    window.open(`${base}/viewer`, "_blank", "noopener");
  }
});

$("cameraUnlockForm").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!privateCameraViewing && !privateCameraBusy) {
    startPrivateCamera();
  }
});

$("cameraPassword").addEventListener("input", () => {
  privateCameraMessagePinned = false;
  privateCameraPasswordAccepted = false;
  updatePrivatePasswordState();
  updatePrivateCameraControls();
});

$("cameraPassword").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    if (!privateCameraViewing && !privateCameraBusy) {
      startPrivateCamera();
    }
  }
});

$("cameraPasswordToggle").addEventListener("click", () => {
  const input = $("cameraPassword");
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  $("cameraPasswordToggle").textContent =
    showing ? "Show" : "Hide";
  input.focus();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    emergencyStopPrivateCamera();
  }
});

window.addEventListener("pagehide", () => {
  emergencyStopPrivateCamera();
});

for (const delay of [100, 500, 1200]) {
  setTimeout(() => {
    updatePrivatePasswordState();
    updatePrivateCameraControls();
  }, delay);
}


async function ownerApi(path, body = null) {
  const options = {
    method: body === null ? "GET" : "POST",
    cache: "no-store",
    headers: { "X-Dashboard-Key": readKey },
  };
  if (body !== null) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function ownerModeIsCountUp() {
  return ["Flow", "Productive", "Personal"].includes($("ownerMode").value);
}

function updateOwnerModeForm() {
  const mode = $("ownerMode").value;
  const countUp = ownerModeIsCountUp();
  $("ownerMinutes").disabled = countUp;
  if (["Focus", "Flow"].includes(mode)) {
    $("ownerFocusFlag").checked = true;
    $("ownerFocusFlag").disabled = true;
  } else if (["Personal", "Short Break", "Long Break"].includes(mode)) {
    $("ownerFocusFlag").checked = false;
    $("ownerFocusFlag").disabled = true;
  } else {
    $("ownerFocusFlag").disabled = false;
  }
  if (mode.includes("Break")) $("ownerCategory").value = "Break";
}

function renderOwnerScheduleList(items) {
  const element = $("ownerScheduleList");
  const ordered = (items || []).slice().sort((a, b) =>
    `${a.date} ${a.start}`.localeCompare(`${b.date} ${b.start}`)
  );
  if (!ordered.length) {
    element.innerHTML = '<div class="empty">No cloud schedule events yet.</div>';
    return;
  }
  element.innerHTML = ordered.slice(0, 100).map((item) => `
    <div class="owner-schedule-row" data-event-id="${esc(item.id)}">
      <b>${esc(item.date)}</b>
      <span>${esc(item.start)}–${esc(item.end)}</span>
      <div><strong>${esc(item.title)}</strong><div class="small">${esc(item.category || "General")}</div></div>
      <div class="schedule-actions">
        <button type="button" data-action="edit">Edit</button>
        <button type="button" class="danger" data-action="delete">Delete</button>
      </div>
    </div>`).join("");
}

function resetOwnerScheduleForm() {
  $("ownerScheduleId").value = "";
  $("ownerScheduleRevision").value = "";
  $("ownerScheduleDate").value = latest?.calendar?.today || isoDate(new Date());
  $("ownerScheduleStart").value = "09:00";
  $("ownerScheduleEnd").value = "10:00";
  $("ownerScheduleTitle").value = "";
  $("ownerScheduleCategory").value = "General";
  $("ownerScheduleNotes").value = "";
}

function renderOwnerControls(data) {
  const owner = data?.access?.role === "owner";
  $("ownerTimerCard").classList.toggle("hidden", !owner);
  $("ownerScheduleCard").classList.toggle("hidden", !owner);
  if (!owner) return;

  const timer = data.timer || {};
  const active = Boolean(timer.running);
  const paused = Boolean(timer.paused);
  $("ownerTimerState").textContent = active
    ? (paused ? "Paused" : `Running · ${timer.source || "cloud"}`)
    : "Cloud timer idle";
  $("ownerTimerState").className = active ? "badge" : "badge offline";
  $("ownerStartButton").disabled = active;
  $("ownerPauseButton").disabled = !active || paused;
  $("ownerResumeButton").disabled = !active || !paused;
  $("ownerFinishButton").disabled = !active;
  $("ownerCancelButton").disabled = !active;
  renderOwnerScheduleList(data.schedule_events || []);
  if (!$("ownerScheduleDate").value) resetOwnerScheduleForm();
}

async function runOwnerTimerAction(action) {
  $("ownerTimerMessage").textContent = `${action}…`;
  try {
    await ownerApi("/api/control/timer", { action });
    $("ownerTimerMessage").textContent = `Timer ${action} applied.`;
    await refresh(false);
  } catch (error) {
    $("ownerTimerMessage").textContent = String(error.message || error);
  }
}

$("ownerMode").addEventListener("change", updateOwnerModeForm);
updateOwnerModeForm();

$("ownerTimerForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("ownerTimerMessage").textContent = "Starting timer…";
  const countUp = ownerModeIsCountUp();
  try {
    await ownerApi("/api/control/timer", {
      action: "start",
      task: $("ownerTask").value.trim(),
      category: $("ownerCategory").value.trim(),
      mode: $("ownerMode").value,
      duration_seconds: countUp ? 0 : Number($("ownerMinutes").value) * 60,
      counts_toward_focus: $("ownerFocusFlag").checked,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    });
    $("ownerTimerMessage").textContent = "Timer started in the cloud.";
    await refresh(false);
  } catch (error) {
    $("ownerTimerMessage").textContent = String(error.message || error);
  }
});

$("ownerPauseButton").onclick = () => runOwnerTimerAction("pause");
$("ownerResumeButton").onclick = () => runOwnerTimerAction("resume");
$("ownerFinishButton").onclick = () => runOwnerTimerAction("finish");
$("ownerCancelButton").onclick = () => runOwnerTimerAction("cancel");

$("ownerScheduleNew").onclick = resetOwnerScheduleForm;
$("ownerScheduleCancelEdit").onclick = resetOwnerScheduleForm;

$("ownerScheduleForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = {
    action: "upsert",
    id: $("ownerScheduleId").value || undefined,
    base_revision: $("ownerScheduleRevision").value
      ? Number($("ownerScheduleRevision").value)
      : undefined,
    date: $("ownerScheduleDate").value,
    start: $("ownerScheduleStart").value,
    end: $("ownerScheduleEnd").value,
    title: $("ownerScheduleTitle").value.trim(),
    category: $("ownerScheduleCategory").value.trim(),
    notes: $("ownerScheduleNotes").value.trim(),
  };
  $("ownerScheduleMessage").textContent = "Saving event…";
  try {
    await ownerApi("/api/control/schedule", body);
    $("ownerScheduleMessage").textContent = "Schedule saved.";
    resetOwnerScheduleForm();
    await refresh(false);
  } catch (error) {
    $("ownerScheduleMessage").textContent = String(error.message || error);
  }
});

$("ownerScheduleList").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  const row = event.target.closest("[data-event-id]");
  if (!button || !row || !latest) return;
  const item = (latest.schedule_events || []).find((value) => value.id === row.dataset.eventId);
  if (!item) return;
  if (button.dataset.action === "edit") {
    $("ownerScheduleId").value = item.id || "";
    $("ownerScheduleRevision").value = item.revision || "";
    $("ownerScheduleDate").value = item.date || "";
    $("ownerScheduleStart").value = item.start || "";
    $("ownerScheduleEnd").value = item.end || "";
    $("ownerScheduleTitle").value = item.title || "";
    $("ownerScheduleCategory").value = item.category || "General";
    $("ownerScheduleNotes").value = item.notes || "";
    $("ownerScheduleTitle").focus();
    return;
  }
  if (!confirm(`Delete “${item.title}”?`)) return;
  try {
    await ownerApi("/api/control/schedule", {
      action: "delete", id: item.id, base_revision: item.revision,
    });
    $("ownerScheduleMessage").textContent = "Event deleted.";
    await refresh(false);
  } catch (error) {
    $("ownerScheduleMessage").textContent = String(error.message || error);
  }
});

function render(data) {
  latest = data;
  fetchedAt = performance.now();
  const role = data.access?.role === "owner" ? "owner" : "viewer";
  $("accessRole").textContent = role === "owner" ? "Owner mode" : "Viewer mode";
  $("accessRole").className = role === "owner" ? "badge role-badge owner" : "badge role-badge";
  renderOwnerControls(data);

  if (!data.has_data) {
    $("task").textContent = "No desktop snapshot yet";
    $("timerStatus").textContent = "Start the desktop app after configuring cloud publishing.";
    $("connection").textContent = "Waiting";
    $("connection").className = "badge offline";
    return;
  }

  const timer = data.timer || {};
  const summary = data.today || {};
  const pixela = data.pixela || {};
  const cloud = data.cloud || {};
  const camera = data.camera || {};

  $("connection").textContent = cloud.desktop_online ? "Desktop online" : "Desktop offline";
  $("connection").className = cloud.desktop_online ? "badge" : "badge offline";
  $("task").textContent = timer.running ? (timer.task || "Untitled activity") : "No active timer";
  $("mode").textContent = timer.mode || "Idle";
  $("category").textContent = timer.category || "—";
  $("focusFlag").textContent = timer.counts_toward_focus
    ? "Counts as focus"
    : "Not counting as focus";
  $("timerStatus").textContent = timer.running
    ? (timer.status || "Running in cloud")
    : cloud.desktop_online
      ? (timer.status || "Ready")
      : `Last desktop update ${ageText(cloud.age_seconds)}`;

  renderPeriodSummary();
  renderSchedule();
  renderRecent(data.recent || []);
  updatePrivateCameraControls();

  $("pixelaStatus").textContent = pixela.status || "Pixela status unavailable";
  const statusText = String(pixela.status || "").toLowerCase();
  $("syncDot").className =
    statusText.includes("connected") || statusText.includes("synced") || statusText.includes("up to date")
      ? "dot"
      : "dot warn";

  const newGraphKey = `${pixela.username}/${pixela.graph_id}`;
  if (pixela.username && pixela.graph_id && newGraphKey !== graphKey) {
    graphKey = newGraphKey;
    const graphBase =
      `https://pixe.la/v1/users/${encodeURIComponent(pixela.username)}/graphs/${encodeURIComponent(pixela.graph_id)}`;
    $("graph").src = `${graphBase}.svg`;
    $("openPixela").href = `${graphBase}.html`;
  }
}

function animate() {
  $("clock").textContent = new Date().toLocaleTimeString();

  if (latest?.has_data) {
    const timer = latest.timer || {};
    let seconds = Number(timer.display_seconds) || 0;

    if (timer.running && !timer.paused) {
      const delta = (performance.now() - fetchedAt) / 1000;
      const countUp = ["Flow", "Productive", "Personal"].includes(timer.mode);
      seconds = countUp ? seconds + delta : seconds - delta;
    }

    $("timer").textContent = formatSeconds(seconds);
    const duration = Math.max(1, Number(timer.duration_seconds) || 1);
    const elapsed =
      Math.max(0, Number(timer.elapsed_seconds) || 0) +
      (timer.running && !timer.paused
        ? (performance.now() - fetchedAt) / 1000
        : 0);

    $("progressBar").style.width =
      ["Flow", "Productive", "Personal"].includes(timer.mode)
        ? "100%"
        : `${Math.min(100, Math.max(0, (elapsed / duration) * 100))}%`;
  }

  requestAnimationFrame(animate);
}

async function refresh(fromLogin = false) {
  if (!readKey) {
    $("login").classList.remove("hidden");
    return false;
  }

  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    const response = await fetch("/api/status", {
      cache: "no-store",
      headers: { "X-Dashboard-Key": readKey },
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (response.status === 401) {
      if (fromLogin) $("loginError").textContent = "That dashboard key is incorrect.";
      else $("login").classList.remove("hidden");
      return false;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    render(await response.json());
    $("updated").textContent = `Cloud refreshed ${new Date().toLocaleTimeString()}`;
    updatePrivateCameraControls();
    return true;
  } catch (error) {
    $("connection").textContent = "Cloud error";
    $("connection").className = "badge offline";
    $("updated").textContent = "Could not reach the cloud API";
    if (fromLogin) $("loginError").textContent = String(error);
    return false;
  }
}


$("summaryTabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-period]");
  if (!button) return;
  summaryPeriod = button.dataset.period;
  $("summaryTabs").querySelectorAll("button").forEach((item) =>
    item.classList.toggle("active", item === button)
  );
  renderPeriodSummary();
});

$("scheduleTabs").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  scheduleView = button.dataset.view;
  $("scheduleTabs").querySelectorAll("button").forEach((item) =>
    item.classList.toggle("active", item === button)
  );
  renderSchedule();
});

if (readKey) {
  $("readKey").value = readKey;
  refresh(true).then((success) => {
    if (success) $("login").classList.add("hidden");
  });
}
setInterval(() => refresh(false), 3000);
requestAnimationFrame(animate);
