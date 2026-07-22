(() => {
  "use strict";

  const storageKey = "focusCameraMode";
  const passwordKey = "mojjssPrivateCameraPassword";
  const objectUrls = new Set();

  function dashboardKey() {
    return (localStorage.getItem("focusDashboardReadKey") || "").trim();
  }

  function authHeaders(json = false) {
    const headers = { "X-Dashboard-Key": dashboardKey() };
    if (json) headers["Content-Type"] = "application/json";
    return headers;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      ...options,
      headers: {
        ...authHeaders(Boolean(options.body)),
        ...(options.headers || {}),
      },
    });

    const type = response.headers.get("content-type") || "";
    const data = type.includes("application/json")
      ? await response.json()
      : null;

    if (!response.ok) {
      throw new Error(data?.error || `HTTP ${response.status}`);
    }

    return data;
  }

  function bytesToBase64Url(bytes) {
    let binary = "";
    for (const value of bytes) binary += String.fromCharCode(value);

    return btoa(binary)
      .replaceAll("+", "-")
      .replaceAll("/", "_")
      .replace(/=+$/g, "");
  }

  async function proofFor(password, requestId, saltB64, iterations) {
    const encoder = new TextEncoder();

    const passwordKeyMaterial = await crypto.subtle.importKey(
      "raw",
      encoder.encode(password),
      "PBKDF2",
      false,
      ["deriveBits"],
    );

    const salt = Uint8Array.from(
      atob(saltB64),
      (character) => character.charCodeAt(0),
    );

    const keyBits = await crypto.subtle.deriveBits(
      {
        name: "PBKDF2",
        salt,
        iterations: Number(iterations) || 100000,
        hash: "SHA-256",
      },
      passwordKeyMaterial,
      256,
    );

    const hmacKey = await crypto.subtle.importKey(
      "raw",
      keyBits,
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );

    const signature = await crypto.subtle.sign(
      "HMAC",
      hmacKey,
      encoder.encode(`focus-studio-camera-photo-v1:${requestId}`),
    );

    return bytesToBase64Url(new Uint8Array(signature));
  }

  function uuidText() {
    if (crypto.randomUUID) {
      return crypto.randomUUID().replaceAll("-", "");
    }

    return bytesToBase64Url(
      crypto.getRandomValues(new Uint8Array(24)),
    );
  }

  function sleep(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  async function waitForPhoto(requestId, statusNode) {
    const deadline = Date.now() + 90000;

    while (Date.now() < deadline) {
      const state = await api(
        `/api/camera/status?request_id=${encodeURIComponent(requestId)}`,
      );

      if (state.status === "ready") return state;

      if (["error", "expired", "cancelled"].includes(state.status)) {
        throw new Error(
          state.message || `Photo request ${state.status}.`,
        );
      }

      statusNode.textContent =
        state.status === "processing"
          ? "Taking photo…"
          : "Waiting for the desktop…";

      await sleep(1400);
    }

    throw new Error("The desktop did not return a photo in time.");
  }

  async function imageBlob(requestId, proof) {
    const response = await fetch("/api/camera/image", {
      method: "POST",
      headers: authHeaders(true),
      cache: "no-store",
      body: JSON.stringify({
        request_id: requestId,
        proof,
      }),
    });

    if (!response.ok) {
      let message = `HTTP ${response.status}`;

      try {
        message = (await response.json()).error || message;
      } catch {
        // Keep the HTTP status message.
      }

      throw new Error(message);
    }

    return response.blob();
  }

  function addPhoto(gallery, blob, metadata) {
    const url = URL.createObjectURL(blob);
    objectUrls.add(url);

    const figure = document.createElement("figure");
    figure.className = "photo-shot";

    const image = document.createElement("img");
    image.src = url;
    image.alt = "Remote camera photo";
    image.loading = "lazy";

    const caption = document.createElement("figcaption");
    const when = metadata?.captured_at
      ? new Date(metadata.captured_at).toLocaleString()
      : "Just now";

    caption.textContent =
      `${when} · ${metadata?.width || "?"}×${metadata?.height || "?"}`;

    const save = document.createElement("a");
    save.href = url;
    save.download =
      `focus-studio-${metadata?.request_id || Date.now()}.jpg`;
    save.textContent = "Save";

    caption.append(" · ", save);
    figure.append(image, caption);
    gallery.prepend(figure);
  }

  async function setup() {
    const liveButton = document.getElementById("cameraButton");
    const passwordInput = document.getElementById("cameraPassword");
    const passwordForm = document.getElementById("cameraUnlockForm");

    if (!liveButton || !passwordInput || !passwordForm) return;

    const card =
      liveButton.closest("section, article, .card")
      || liveButton.parentElement;

    if (!card || card.querySelector("[data-photo-mode-root]")) return;

    let statusPayload;

    try {
      statusPayload = await api("/api/status");
    } catch {
      return;
    }

    const camera = statusPayload?.camera || {};
    const modes = Array.isArray(camera.allowed_modes)
      ? camera.allowed_modes
      : ["live", "photos"];

    const photosAllowed =
      modes.includes("photos") && camera.photos_enabled !== false;
    const liveAllowed = modes.includes("live");

    /*
     * Keep an exact marker for the password form's original location.
     * The same form is moved into the Photos panel when Photos is selected,
     * then moved back for Live. This keeps one password input, one password,
     * and all existing app-v55.js event listeners.
     */
    const passwordHome = document.createComment(
      "focus-studio-camera-password-home",
    );
    passwordForm.parentNode.insertBefore(passwordHome, passwordForm);

    const root = document.createElement("div");
    root.dataset.photoModeRoot = "1";
    root.className = "camera-mode-extension";

    root.innerHTML = `
      <div class="camera-mode-picker" role="group" aria-label="Camera mode">
        <button type="button" data-mode="live">Live</button>
        <button type="button" data-mode="photos">Photos</button>
      </div>

      <div class="photo-mode-panel" hidden>
        <p class="photo-mode-note">
          Request one or several still photos. This uses the same camera
          password as Live mode.
        </p>

        <div data-photo-password-slot></div>

        <div class="photo-mode-actions">
          <label>
            Photos
            <select data-photo-count>
              <option value="1">1</option>
              <option value="3">3</option>
              <option value="5">5</option>
            </select>
          </label>

          <button type="button" data-take-photos>Take photo</button>
          <button type="button" data-refresh-photos>Recent</button>
        </div>

        <p class="photo-mode-status" data-photo-status>Ready.</p>
        <div class="photo-gallery" data-photo-gallery></div>
      </div>
    `;

    card.prepend(root);

    const panel = root.querySelector(".photo-mode-panel");
    const passwordSlot = root.querySelector(
      "[data-photo-password-slot]",
    );
    const gallery = root.querySelector("[data-photo-gallery]");
    const photoStatus = root.querySelector("[data-photo-status]");
    const takeButton = root.querySelector("[data-take-photos]");
    const refreshButton = root.querySelector("[data-refresh-photos]");
    const countSelect = root.querySelector("[data-photo-count]");
    const buttons = [...root.querySelectorAll("[data-mode]")];

    const liveModeButton = buttons.find(
      (button) => button.dataset.mode === "live",
    );
    const photoModeButton = buttons.find(
      (button) => button.dataset.mode === "photos",
    );

    if (liveModeButton) liveModeButton.disabled = !liveAllowed;
    if (photoModeButton) photoModeButton.disabled = !photosAllowed;

    function makePasswordFormVisible() {
      passwordForm.hidden = false;
      passwordForm.classList.remove("photo-mode-hide-live");
      passwordForm.style.removeProperty("display");
      passwordForm.style.removeProperty("visibility");
    }

    function placePasswordForm(mode) {
      makePasswordFormVisible();

      if (mode === "photos") {
        passwordSlot.appendChild(passwordForm);
      } else if (passwordHome.parentNode) {
        passwordHome.parentNode.insertBefore(
          passwordForm,
          passwordHome.nextSibling,
        );
      }

      makePasswordFormVisible();
    }

    function setMode(mode) {
      const chosen =
        mode === "photos" && photosAllowed
          ? "photos"
          : liveAllowed
            ? "live"
            : "photos";

      localStorage.setItem(storageKey, chosen);
      panel.hidden = chosen !== "photos";

      buttons.forEach((button) => {
        button.classList.toggle(
          "active",
          button.dataset.mode === chosen,
        );
      });

      const liveOnlyElements = [
        document.getElementById("cameraViewport"),
        document.getElementById("cameraIdentity"),
        document.getElementById("cameraOpenPrivateButton"),
      ];

      liveOnlyElements.forEach((node) => {
        if (node) {
          node.classList.toggle(
            "photo-mode-hide-live",
            chosen === "photos",
          );
        }
      });

      placePasswordForm(chosen);
    }

    buttons.forEach((button) => {
      button.addEventListener(
        "click",
        () => setMode(button.dataset.mode),
      );
    });

    const preferred =
      localStorage.getItem(storageKey)
      || camera.default_mode
      || (photosAllowed ? "photos" : "live");

    setMode(preferred);

    function password() {
      const value =
        passwordInput.value
        || sessionStorage.getItem(passwordKey)
        || "";

      if (value) sessionStorage.setItem(passwordKey, value);
      return value;
    }

    async function requestOne() {
      const secret = password();

      if (!secret) {
        throw new Error("Enter the camera password first.");
      }

      if (!camera.password_salt || !camera.password_iterations) {
        throw new Error(
          "The desktop has not published camera password metadata yet.",
        );
      }

      const requestId = uuidText();
      const proof = await proofFor(
        secret,
        requestId,
        camera.password_salt,
        camera.password_iterations,
      );

      await api("/api/camera/request", {
        method: "POST",
        body: JSON.stringify({
          request_id: requestId,
          proof,
        }),
      });

      const metadata = await waitForPhoto(
        requestId,
        photoStatus,
      );
      const blob = await imageBlob(requestId, proof);

      addPhoto(gallery, blob, {
        ...metadata,
        request_id: requestId,
      });
    }

    takeButton.addEventListener("click", async () => {
      takeButton.disabled = true;
      refreshButton.disabled = true;

      try {
        const total = Number(countSelect.value) || 1;

        for (let index = 0; index < total; index += 1) {
          photoStatus.textContent =
            `Photo ${index + 1} of ${total}…`;

          await requestOne();

          if (index + 1 < total) {
            await sleep(1200);
          }
        }

        photoStatus.textContent =
          `${total} photo${total === 1 ? "" : "s"} received.`;
      } catch (error) {
        photoStatus.textContent =
          error?.message || String(error);
      } finally {
        takeButton.disabled = false;
        refreshButton.disabled = false;
      }
    });

    refreshButton.addEventListener("click", async () => {
      refreshButton.disabled = true;
      takeButton.disabled = true;

      try {
        const secret = password();

        if (!secret) {
          throw new Error(
            "Enter the camera password before opening Recent photos.",
          );
        }

        if (!camera.password_salt || !camera.password_iterations) {
          throw new Error(
            "The desktop has not published camera password metadata yet.",
          );
        }

        photoStatus.textContent = "Checking camera password…";

        /*
         * The challenge reveals only opaque random request IDs. It does not
         * reveal photo metadata or image data. A password-derived proof is
         * calculated separately for every ID.
         */
        const challenge = await api("/api/camera/recent-challenge");
        const requestIds = Array.isArray(challenge.request_ids)
          ? challenge.request_ids
          : [];

        if (!requestIds.length) {
          gallery.replaceChildren();
          photoStatus.textContent = "No recent photos.";
          return;
        }

        const proofs = await Promise.all(
          requestIds.map(async (requestId) => ({
            request_id: requestId,
            proof: await proofFor(
              secret,
              requestId,
              camera.password_salt,
              camera.password_iterations,
            ),
          })),
        );

        const recent = await api("/api/camera/recent", {
          method: "POST",
          body: JSON.stringify({ proofs }),
        });

        gallery.replaceChildren();

        for (const item of recent.photos || []) {
          const itemProof = await proofFor(
            secret,
            item.request_id,
            camera.password_salt,
            camera.password_iterations,
          );
          const blob = await imageBlob(item.request_id, itemProof);
          addPhoto(gallery, blob, item);
        }

        const count = (recent.photos || []).length;
        photoStatus.textContent =
          `${count} password-protected recent photo${count === 1 ? "" : "s"}.`;
      } catch (error) {
        gallery.replaceChildren();
        photoStatus.textContent =
          error?.message || String(error);
      } finally {
        refreshButton.disabled = false;
        takeButton.disabled = false;
      }
    });
  }

  window.addEventListener("beforeunload", () => {
    objectUrls.forEach((url) => URL.revokeObjectURL(url));
  });

  let setupInProgress = false;
  let retryTimer = null;

  async function ensurePhotoMode() {
    if (document.querySelector("[data-photo-mode-root]")) {
      return true;
    }

    if (setupInProgress || !dashboardKey()) {
      return false;
    }

    setupInProgress = true;

    try {
      await setup();
    } catch (error) {
      console.warn("Photo mode setup is waiting for dashboard access.", error);
    } finally {
      setupInProgress = false;
    }

    return Boolean(
      document.querySelector("[data-photo-mode-root]"),
    );
  }

  function schedulePhotoModeSetup(delay = 0) {
    if (retryTimer !== null) {
      clearTimeout(retryTimer);
    }

    retryTimer = window.setTimeout(async () => {
      retryTimer = null;
      const ready = await ensurePhotoMode();

      /*
       * Viewer and owner keys are saved only after the dashboard login
       * request succeeds. Keep retrying so Photo mode appears even when
       * this script initially loaded before the user entered a key.
       */
      if (!ready) {
        schedulePhotoModeSetup(750);
      }
    }, delay);
  }

  function startPhotoModeBootstrap() {
    const loginForm = document.getElementById("loginForm");

    if (loginForm) {
      loginForm.addEventListener(
        "submit",
        () => schedulePhotoModeSetup(250),
        true,
      );
    }

    window.addEventListener(
      "focus",
      () => schedulePhotoModeSetup(0),
    );

    window.addEventListener("storage", (event) => {
      if (event.key === "focusDashboardReadKey") {
        schedulePhotoModeSetup(0);
      }
    });

    schedulePhotoModeSetup(0);
  }

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      startPhotoModeBootstrap,
      { once: true },
    );
  } else {
    startPhotoModeBootstrap();
  }
})();
