/* GT IPTV Player Pro - local receiver web interface */
(function () {
  "use strict";

  var script = document.currentScript;
  var scriptUrl = script ? new URL(script.src, window.location.href) : null;
  var scriptPath = scriptUrl ? scriptUrl.pathname : "/assets/app.js";
  var basePath = scriptPath.replace(/\/assets\/app\.js$/, "");
  var state = {
    clientVersion: scriptUrl ? String(scriptUrl.searchParams.get("v") || "") : "",
    youtubeResults: [],
    youtubePages: [],
    youtubePage: 1,
    youtubePagingBusy: false,
    youtubeQuery: "",
    youtubeSearchLocale: "",
    youtubeSearchSerial: 0,
    youtubeJobToken: "",
    youtubeTimer: 0,
    mediaResults: [],
    runtimeVersion: "",
    runtimeWarningForced: false,
    csrf: "",
    expiresIn: 0,
    language: "en",
    strings: {},
    sources: [],
    dashboard: null,
    automaticTest: null,
    automaticTestUnavailable: false,
    automaticTestLoading: false,
    automaticTestTimer: 0,
    subtitleProviders: [],
    subtitleResults: null,
    audioCurrent: null,
    cueTimer: 0,
    cueQuerySerial: 0,
    remoteTimer: 0,
    remoteSending: false,
    pairingTimer: 0,
    refreshTimer: 0,
    sessionTimer: 0,
    toastTimer: 0,
    page: "dashboard"
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function t(key, fallback) {
    return state.strings[key] || fallback || key;
  }

  function node(tag, className, textValue) {
    var element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    if (textValue !== undefined && textValue !== null) {
      element.textContent = String(textValue);
    }
    return element;
  }

  function append(parent) {
    for (var index = 1; index < arguments.length; index += 1) {
      if (arguments[index]) {
        parent.appendChild(arguments[index]);
      }
    }
    return parent;
  }

  function icon(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  function safeStorageGet(key) {
    try {
      return window.localStorage.getItem(key) || "";
    } catch (unusedError) {
      return "";
    }
  }

  function safeStorageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (unusedError) {
      return;
    }
  }

  function browserLanguage() {
    var languages = window.navigator.languages || [];
    return String(languages[0] || window.navigator.language || "en");
  }

  function preferredLanguage() {
    var currentBrowserLanguage = browserLanguage();
    var stored = safeStorageGet("gt-web-language");
    // Ignore language selections saved before browser-language tracking existed,
    // or when the browser language has changed since the selection was saved.
    if (stored && safeStorageGet("gt-web-browser-language") === currentBrowserLanguage) {
      return stored;
    }
    return currentBrowserLanguage;
  }

  function apiUrl(path) {
    return basePath + "/api/v1" + path;
  }

  async function api(path, options) {
    var settings = options || {};
    var headers = { "Accept": "application/json" };
    var method = String(settings.method || "GET").toUpperCase();
    if (settings.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (method !== "GET" && method !== "HEAD" && state.csrf) {
      headers["X-GT-CSRF"] = state.csrf;
    }
    var response;
    try {
      response = await window.fetch(apiUrl(path), {
        method: method,
        credentials: "same-origin",
        cache: "no-store",
        headers: headers,
        body: settings.body === undefined ? undefined : JSON.stringify(settings.body)
      });
    } catch (unusedError) {
      throw new Error(t("request_failed", "Request failed"));
    }
    var payload;
    try {
      payload = await response.json();
    } catch (unusedError) {
      payload = { ok: false, error: "request_failed", message: t("request_failed", "Request failed") };
    }
    if (!response.ok || !payload.ok) {
      var localizedError = state.strings[payload.error || ""];
      var english = /^en(?:_|$)/i.test(state.language);
      var error = new Error(localizedError || (english && payload.message) || t("request_failed", "Request failed"));
      error.code = payload.error || "request_failed";
      error.status = response.status;
      if (response.status === 401 && path !== "/bootstrap") {
        showAuth();
      }
      throw error;
    }
    return payload.data;
  }

  async function loadLanguage(requested) {
    var language = requested || preferredLanguage();
    var response = await window.fetch(apiUrl("/i18n?lang=" + encodeURIComponent(language)), {
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Accept": "application/json" }
    });
    var payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error("Language catalog unavailable");
    }
    var catalog = payload.data || {};
    state.language = catalog.language || "en";
    state.strings = catalog.strings || {};
    document.documentElement.lang = state.language.replace(/_/g, "-");
    document.documentElement.dir = catalog.direction === "rtl" ? "rtl" : "ltr";
    safeStorageSet("gt-web-language", state.language);
    safeStorageSet("gt-web-browser-language", browserLanguage());
    document.querySelectorAll("[data-i18n]").forEach(function (element) {
      var key = element.getAttribute("data-i18n");
      element.textContent = t(key, element.textContent);
    });
    document.querySelectorAll("[data-i18n-title]").forEach(function (element) {
      var key = element.getAttribute("data-i18n-title");
      var value = t(key, element.getAttribute("aria-label") || key);
      element.title = value;
      element.setAttribute("aria-label", value);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (element) {
      element.placeholder = t(element.getAttribute("data-i18n-placeholder"), element.placeholder);
    });
    fillLanguageSelect(byId("authLanguage"), catalog.languages || []);
    fillLanguageSelect(byId("languageSelect"), catalog.languages || []);
    byId("youtubeLanguage").textContent = t("youtube_automatic", "Automatic") + " · " + browserLanguage();
    updateKeyboardLabel();
    if (!byId("appView").hidden) {
      renderCurrentData();
    }
  }

  function fillLanguageSelect(select, languages) {
    var fragment = document.createDocumentFragment();
    languages.forEach(function (language) {
      var option = node("option", "", language.name || language.code);
      option.value = language.code;
      option.selected = language.code === state.language;
      fragment.appendChild(option);
    });
    select.replaceChildren(fragment);
  }

  function showAuth(message, isError) {
    window.clearInterval(state.refreshTimer);
    window.clearInterval(state.sessionTimer);
    window.clearTimeout(state.automaticTestTimer);
    window.clearInterval(state.remoteTimer);
    window.clearInterval(state.youtubeTimer);
    state.remoteTimer = 0;
    state.refreshTimer = 0;
    state.sessionTimer = 0;
    state.automaticTestTimer = 0;
    state.csrf = "";
    byId("remotePaste").value = "";
    byId("remotePaste").disabled = true;
    byId("remotePasteSend").disabled = true;
    byId("appView").hidden = true;
    byId("authView").hidden = false;
    setPairStatus(message || "", Boolean(isError));
    window.setTimeout(function () { byId("pairCode").focus(); }, 50);
  }

  function showApp(session) {
    byId("authView").hidden = true;
    byId("appView").hidden = false;
    state.csrf = session.csrf || state.csrf;
    state.expiresIn = Number(session.expires_in || 0);
    startSessionClock();
    switchPage(state.page || "dashboard");
    refreshAll(true);
    if (!state.refreshTimer) {
      state.refreshTimer = window.setInterval(function () {
        refreshAll(false);
      }, 15000);
    }
  }

  function setPairStatus(message, isError) {
    var target = byId("pairStatus");
    target.textContent = message || "";
    target.classList.toggle("error", Boolean(isError));
  }

  function setBusy(button, busy) {
    if (!button) {
      return;
    }
    button.disabled = Boolean(busy);
    button.classList.toggle("busy", Boolean(busy));
  }

  async function bootstrap() {
    try {
      await loadLanguage();
    } catch (unusedError) {
      state.language = "en";
    }
    try {
      var session = await api("/bootstrap");
      if (session.authenticated) {
        showApp(session);
      } else {
        showAuth();
      }
    } catch (error) {
      showAuth(error.message || t("request_failed", "Request failed"), true);
    }
  }

  async function submitPair(event) {
    event.preventDefault();
    var button = event.currentTarget.querySelector("button[type=submit]");
    var code = String(byId("pairCode").value || "").replace(/\D/g, "").slice(0, 6);
    byId("pairCode").value = code;
    if (code.length !== 6) {
      setPairStatus(t("pair_code", "Pairing code") + ": 6", true);
      return;
    }
    setBusy(button, true);
    window.clearInterval(state.pairingTimer);
    try {
      var result = await api("/pair", { method: "POST", body: { code: code } });
      setPairStatus(t("waiting_approval", "Waiting for approval on the receiver."), false);
      pollPairing(result.request_id, button);
    } catch (error) {
      setBusy(button, false);
      setPairStatus(error.message || t("request_failed", "Request failed"), true);
    }
  }

  function pollPairing(requestId, button) {
    var running = false;
    async function poll() {
      if (running) {
        return;
      }
      running = true;
      try {
        var result = await api("/pair/status?id=" + encodeURIComponent(requestId));
        if (result.status === "approved") {
          window.clearInterval(state.pairingTimer);
          state.pairingTimer = 0;
          state.csrf = result.csrf || "";
          byId("pairCode").value = "";
          setBusy(button, false);
          showApp(result);
        } else if (result.status === "denied") {
          window.clearInterval(state.pairingTimer);
          state.pairingTimer = 0;
          setBusy(button, false);
          setPairStatus(t("denied", "Request denied"), true);
        }
      } catch (error) {
        window.clearInterval(state.pairingTimer);
        state.pairingTimer = 0;
        setBusy(button, false);
        setPairStatus(error.message || t("request_failed", "Request failed"), true);
      } finally {
        running = false;
      }
    }
    poll();
    state.pairingTimer = window.setInterval(poll, 1500);
  }

  function startSessionClock() {
    window.clearInterval(state.sessionTimer);
    function update() {
      state.expiresIn = Math.max(0, Number(state.expiresIn || 0));
      var minutes = Math.floor(state.expiresIn / 60);
      var seconds = Math.floor(state.expiresIn % 60);
      byId("sessionText").textContent = String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
      byId("clock").textContent = new Intl.DateTimeFormat(state.language.replace(/_/g, "-"), {
        hour: "2-digit",
        minute: "2-digit"
      }).format(new Date());
      state.expiresIn -= 1;
      if (state.expiresIn < 0) {
        showAuth(t("session_closes", "Session expired"), true);
      }
    }
    update();
    state.sessionTimer = window.setInterval(update, 1000);
  }

  async function refreshSession() {
    try {
      var session = await api("/session");
      state.csrf = session.csrf || state.csrf;
      state.expiresIn = Number(session.expires_in || state.expiresIn);
    } catch (unusedError) {
      return;
    }
  }

  async function refreshAll(showErrors) {
    try {
      var dashboard = await api("/dashboard");
      state.dashboard = dashboard;
      state.sources = ((dashboard.sources || {}).items || []);
      renderDashboard(dashboard);
      if (state.page === "sources") {
        renderSources(state.sources);
      } else if (state.page === "automatic-test") {
        if (state.automaticTest) {
          renderAutomaticTest(state.automaticTest);
        }
      } else if (state.page === "downloads") {
        renderDownloads(dashboard.download_items || []);
      }
      refreshSession();
    } catch (error) {
      if (showErrors) {
        toast(error.message || t("request_failed", "Request failed"), true);
      }
    }
  }

  function renderCurrentData() {
    if (state.dashboard) {
      renderDashboard(state.dashboard);
      if (state.page === "sources") {
        renderSources(state.sources);
      } else if (state.page === "automatic-test" && state.automaticTest) {
        renderAutomaticTest(state.automaticTest);
      } else if (state.page === "downloads") {
        loadDownloads();
      } else if (state.page === "epg") {
        loadEpg();
      } else if (state.page === "favorites") {
        loadFavorites();
      } else if (state.page === "settings") {
        loadSettings();
      } else if (state.page === "subtitles") {
        loadSubtitleCurrent();
        if (state.subtitleResults) { renderSubtitleResults(state.subtitleResults); }
      } else if (state.page === "audio" && state.audioCurrent) {
        renderAudioCurrent(state.audioCurrent);
      } else if (state.page === "youtube") {
        renderYouTubeResults(state.youtubeResults);
        renderYouTubePager();
      } else if (state.page === "media") {
        renderMediaResults(state.mediaResults);
      }
    }
  }

  function emptyState(message) {
    return node("div", "empty-state", message);
  }

  function statusDot(status) {
    var safe = ["green", "yellow", "red"].indexOf(status) >= 0 ? status : "neutral";
    return node("span", "status-dot " + safe);
  }

  function sourceTypeLabel(type) {
    var values = {
      xtream: t("kind_xtream", "Xtream Codes"),
      stalker: t("kind_stalker", "Stalker / MAC"),
      m3u: t("kind_m3u", "M3U")
    };
    return values[type] || String(type || "IPTV").toUpperCase();
  }

  function sourceTypeShortLabel(type) {
    return type === "xtream" ? "XTREAM" : (type === "stalker" ? "STALKER / MAC" : String(type || "IPTV").toUpperCase());
  }

  function revisionNumber(version) {
    var match = /-r(\d+)$/i.exec(String(version || ""));
    return match ? Number(match[1]) : 0;
  }

  function updateRuntimeWarning(force) {
    if (force) {
      state.runtimeWarningForced = true;
    }
    var mismatch = Boolean(state.clientVersion && state.runtimeVersion && state.runtimeVersion !== state.clientVersion);
    byId("runtimeWarning").hidden = !(state.runtimeWarningForced || mismatch);
  }

  function handleAutomaticTestUnavailable(error) {
    if (!error || (error.status !== 404 && error.code !== "not_found")) {
      return false;
    }
    state.automaticTestUnavailable = true;
    updateRuntimeWarning(true);
    renderAutomaticTest(state.automaticTest);
    toast(t("restart_required", "The web interface was updated. Restart the Enigma2 interface and refresh this page to activate the changes."), true);
    return true;
  }

  function renderDashboard(data) {
    var receiver = data.receiver || {};
    var health = data.health || {};
    var downloads = data.downloads || {};
    var storage = data.storage || {};
    byId("receiverName").textContent = receiver.name || "Enigma2";
    byId("receiverStatus").textContent = receiver.online ? t("connected", "Connected") : t("off", "Off");
    byId("receiverStatus").className = receiver.online ? "good" : "bad";
    var healthLabels = {
      green: t("all_healthy", "All healthy"),
      yellow: t("status", "Check"),
      red: t("error", "Error"),
      neutral: t("configured", "Configured")
    };
    byId("healthStatus").textContent = healthLabels[health.status] || healthLabels.neutral;
    byId("healthStatus").className = health.status === "red" ? "bad" : (health.status === "yellow" ? "warn" : "good");
    byId("activeDownloads").textContent = String(downloads.active || 0);
    byId("storageFree").textContent = (storage.free_text || "—") + (storage.free_text ? " " + t("free_space", "free") : "");
    var total = Number(storage.total || 0);
    var free = Number(storage.free || 0);
    var usedPercent = total > 0 ? Math.max(0, Math.min(100, Math.round((1 - free / total) * 100))) : 0;
    byId("storageRing").parentElement.style.background = "conic-gradient(var(--cyan) 0 " + usedPercent + "%, #202d57 " + usedPercent + "%)";
    state.runtimeVersion = String((data.plugin || {}).version || "");
    var runtimeRevision = revisionNumber(state.runtimeVersion);
    if (runtimeRevision && runtimeRevision < 22) {
      state.automaticTestUnavailable = true;
    }
    updateRuntimeWarning(false);
    byId("sidebarVersion").textContent = (state.runtimeVersion || state.clientVersion || "").replace(/^.*-/, "").toUpperCase();
    byId("youtubeReceiver").textContent = receiver.name || "Enigma2";
    renderDashboardDownloads(data.download_items || []);
  }

  function renderDashboardDownloads(items) {
    var target = byId("dashboardDownloads");
    var active = items.filter(function (item) {
      return ["queued", "downloading", "paused", "stopping"].indexOf(item.status) >= 0;
    });
    byId("downloadCount").textContent = String(active.length);
    if (!active.length) {
      target.replaceChildren(emptyState(t("no_downloads", "No downloads")));
      return;
    }
    target.replaceChildren.apply(target, active.slice(0, 4).map(downloadRow));
  }

  function downloadRow(item, withActions) {
    var row = node("article", "download-row");
    var head = node("div", "download-head");
    append(head, statusDot(item.status === "downloading" ? "green" : (item.status === "error" ? "red" : "yellow")), node("strong", "", item.name || "Video"), node("small", "", String(item.percent || 0) + "%"));
    var track = node("div", "progress-track");
    var bar = node("i");
    bar.style.width = String(Math.max(0, Math.min(100, Number(item.percent || 0)))) + "%";
    track.appendChild(bar);
    var meta = node("div", "download-meta");
    append(meta, node("span", "", (item.downloaded_text || "0 B") + (item.total_text ? " / " + item.total_text : "")), node("span", "", item.speed_text || (item.status === "stopping" ? t("test_stopping", "Stopping") : t(item.status, item.status))));
    append(row, head, track, meta);
    if (withActions) {
      var actions = node("div", "row-actions");
      if (item.can_pause) {
        actions.appendChild(actionButton(t("pause", "Pause"), "pause", item.id));
      }
      if (item.can_resume) {
        actions.appendChild(actionButton(t("resume", "Resume"), "resume", item.id));
      }
      if (item.can_cancel) {
        actions.appendChild(actionButton(t("stop", "Stop"), "cancel", item.id, true));
      }
      if (item.can_delete) {
        actions.appendChild(actionButton(t("delete", "Delete"), "delete", item.id, true));
      }
      row.appendChild(actions);
    }
    return row;
  }

  function actionButton(label, action, identity, danger) {
    var button = node("button", "mini-button" + (danger ? " danger" : ""), label);
    button.type = "button";
    button.dataset.downloadAction = action;
    button.dataset.downloadId = identity;
    return button;
  }

  function automaticTestActive(data) {
    return data && ["running", "cancelling"].indexOf(data.state) >= 0;
  }

  function stopAutomaticTestPolling() {
    window.clearTimeout(state.automaticTestTimer);
    state.automaticTestTimer = 0;
  }

  function scheduleAutomaticTestPolling() {
    stopAutomaticTestPolling();
    if (state.page === "automatic-test" && automaticTestActive(state.automaticTest)) {
      state.automaticTestTimer = window.setTimeout(loadAutomaticTest, 800);
    }
  }

  function automaticTestStateLabel(value) {
    var values = {
      idle: t("test_ready", "Ready"),
      running: t("test_running", "Automatic test is running"),
      cancelling: t("test_stopping", "Stopping"),
      completed: t("test_completed", "Automatic test completed"),
      cancelled: t("test_cancelled", "Automatic test cancelled"),
      empty: t("no_portals", "No saved portal was found for this type")
    };
    return values[value] || values.idle;
  }

  function providerStatusLabel(value, fallbackStatus) {
    var values = {
      ONLINE: t("active", "Active"),
      ACTIVE: t("active", "Active"),
      SUCCESS: t("active", "Active"),
      OK: t("active", "Active"),
      BUSY: t("busy", "Busy"),
      STALE: t("stale", "Stale"),
      WARNING: t("warning_accounts", "Warning"),
      INVALID: t("invalid", "Invalid"),
      DISABLED: t("disabled", "Disabled"),
      EXPIRED: t("expired", "Expired"),
      ERROR: t("error", "Error"),
      FAILED: t("error", "Error"),
      OFFLINE: t("off", "Off")
    };
    var normalized = String(value || "").toUpperCase();
    if (values[normalized]) {
      return values[normalized];
    }
    if (fallbackStatus === "green") {
      return t("active", "Active");
    }
    if (fallbackStatus === "yellow") {
      return t("warning_accounts", "Warning");
    }
    if (fallbackStatus === "red") {
      return t("error", "Error");
    }
    return t("status", "Status");
  }

  function automaticTestConnections(item) {
    var active = item.active_connections;
    var maximum = item.max_connections;
    if (active === null || active === undefined) {
      return maximum === null || maximum === undefined ? "—" : "— / " + String(maximum);
    }
    return String(active) + " / " + (maximum === null || maximum === undefined ? "—" : String(maximum));
  }

  function automaticTestResultCard(item) {
    var status = ["green", "yellow", "red"].indexOf(item.status) >= 0 ? item.status : "neutral";
    var card = node("article", "test-result-card status-" + status);
    var head = node("div", "test-result-head");
    var title = node("div", "test-result-title");
    append(title,
      node("strong", "", item.name || sourceTypeLabel(item.type)),
      node("small", "", item.endpoint || sourceTypeLabel(item.type))
    );
    append(head,
      statusDot(status),
      title,
      node("span", "type-badge", sourceTypeShortLabel(item.type)),
      node("span", "test-result-status", providerStatusLabel(item.provider_status, status))
    );
    var details = node("div", "test-result-details");
    append(details,
      infoLine(t("connection_status", "Connection status"), providerStatusLabel(item.account_status || item.provider_status, status)),
      infoLine(t("expiry_date", "Expiry date"), item.expiry || "—"),
      infoLine(t("connections", "Connections"), automaticTestConnections(item)),
      infoLine(t("latency", "Latency"), Number(item.latency_ms || 0) > 0 ? String(item.latency_ms) + " ms" : "—")
    );
    append(card, head, details);
    if (item.detail) {
      card.appendChild(node("p", "test-result-message",
        item.detail === "The source could not be checked." ? t("request_failed", "Request failed") : item.detail));
    }
    return card;
  }

  function renderAutomaticTest(data) {
    data = data || { state: "idle", type: "", total: 0, completed: 0, summary: {}, results: [] };
    state.automaticTest = data;
    var jobState = String(data.state || "idle");
    var total = Math.max(0, Number(data.total || 0));
    var completed = Math.max(0, Number(data.completed || 0));
    var summary = data.summary || {};
    var percent = total > 0 ? Math.max(0, Math.min(100, Math.round(completed * 100 / total))) : 0;
    var stateBadge = byId("autoTestState");
    stateBadge.textContent = automaticTestStateLabel(jobState);
    stateBadge.className = "test-state-badge " + jobState;
    byId("autoTestType").textContent = data.type ? sourceTypeLabel(data.type) : t("choose_portal_type", "Choose a portal type");
    byId("autoTestProgressText").textContent = String(completed) + " / " + String(total);
    byId("autoTestProgressBar").style.width = String(percent) + "%";
    byId("autoTestCompleted").textContent = String(completed);
    byId("autoTestGreen").textContent = String(Number(summary.green || 0));
    byId("autoTestYellow").textContent = String(Number(summary.yellow || 0) + Number(summary.neutral || 0));
    byId("autoTestRed").textContent = String(Number(summary.red || 0));
    var current = data.current || null;
    byId("autoTestCurrentWrap").hidden = !current;
    byId("autoTestCurrent").textContent = current ? [current.name, current.endpoint].filter(Boolean).join(" — ") : "—";
    var active = automaticTestActive(data);
    document.querySelectorAll("[data-auto-test-type]").forEach(function (button) {
      button.disabled = active || state.automaticTestUnavailable;
    });
    var cancelButton = byId("cancelAutomaticTest");
    cancelButton.hidden = !active;
    cancelButton.disabled = jobState === "cancelling";
    var results = data.results || [];
    var target = byId("autoTestResults");
    if (results.length) {
      target.replaceChildren.apply(target, results.map(automaticTestResultCard));
    } else {
      var emptyMessage = jobState === "empty" ? t("no_portals", "No saved portal was found for this type") : t("no_test_results", "No automatic test has been run yet.");
      target.replaceChildren(emptyState(emptyMessage));
    }
  }

  async function loadAutomaticTest() {
    stopAutomaticTestPolling();
    if (state.automaticTestLoading) {
      return;
    }
    state.automaticTestLoading = true;
    var previousState = (state.automaticTest || {}).state;
    try {
      var result = await api("/automatic-test");
      renderAutomaticTest(result);
      if (["running", "cancelling"].indexOf(previousState) >= 0 && !automaticTestActive(result)) {
        refreshAll(false);
      }
    } catch (error) {
      if (!handleAutomaticTestUnavailable(error)) {
        toast(error.message || t("request_failed", "Request failed"), true);
      }
    } finally {
      state.automaticTestLoading = false;
      scheduleAutomaticTestPolling();
    }
  }

  async function startAutomaticTest(button) {
    stopAutomaticTestPolling();
    setBusy(button, true);
    try {
      var result = await api("/automatic-test/start", {
        method: "POST",
        body: { type: button.dataset.autoTestType }
      });
      renderAutomaticTest(result);
      toast(result.state === "empty" ? t("no_portals", "No saved portal was found for this type") : t("test_running", "Automatic test is running"), result.state === "empty");
    } catch (error) {
      if (!handleAutomaticTestUnavailable(error)) {
        toast(error.message || t("request_failed", "Request failed"), true);
      }
    } finally {
      button.classList.remove("busy");
      renderAutomaticTest(state.automaticTest);
      scheduleAutomaticTestPolling();
    }
  }

  async function cancelAutomaticTest(button) {
    stopAutomaticTestPolling();
    setBusy(button, true);
    try {
      renderAutomaticTest(await api("/automatic-test/cancel", { method: "POST", body: {} }));
    } catch (error) {
      if (!handleAutomaticTestUnavailable(error)) {
        toast(error.message || t("request_failed", "Request failed"), true);
      }
    } finally {
      button.classList.remove("busy");
      renderAutomaticTest(state.automaticTest);
      scheduleAutomaticTestPolling();
    }
  }

  async function loadSources() {
    try {
      var result = await api("/sources");
      state.sources = result.items || [];
      renderSources(state.sources);
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderSources(items) {
    var target = byId("sourcesList");
    if (!items.length) {
      target.replaceChildren(emptyState(t("no_sources", "No saved IPTV source was found")));
      return;
    }
    var cards = items.map(function (item) {
      var card = node("article", "management-card");
      var head = node("div", "management-card-head");
      var badge = node("span", "type-badge", sourceTypeLabel(item.type));
      var body = node("div");
      append(body, node("strong", "", item.name || sourceTypeLabel(item.type)), node("small", "", item.endpoint || item.label || item.masked_secret));
      append(head, badge, body, statusDot(item.status));
      var health = item.health || {};
      var details = node("div", "health-details", health.detail || item.label || t("masked_notice", "Sensitive account data is hidden."));
      var actions = node("div", "row-actions");
      actions.appendChild(sourceButton(t("test", "Test"), "test", item.id));
      actions.appendChild(sourceButton(t("edit", "Edit"), "edit", item.id));
      actions.appendChild(sourceButton(t("delete", "Delete"), "delete", item.id, true));
      append(card, head, details, actions);
      return card;
    });
    target.replaceChildren.apply(target, cards);
  }

  function sourceButton(label, action, identity, danger) {
    var button = node("button", "mini-button" + (danger ? " danger" : ""), label);
    button.type = "button";
    button.dataset.sourceAction = action;
    button.dataset.sourceId = identity;
    return button;
  }

  async function handleSourceAction(button) {
    var action = button.dataset.sourceAction;
    var identity = button.dataset.sourceId;
    var source = state.sources.find(function (item) { return item.id === identity; });
    if (!source) {
      return;
    }
    if (action === "edit") {
      openSourceModal(false, source);
      return;
    }
    if (action === "delete" && !window.confirm(t("confirm_delete", "Delete this source?"))) {
      return;
    }
    setBusy(button, true);
    try {
      if (action === "test") {
        var tested = await api("/sources/" + encodeURIComponent(identity) + "/test", { method: "POST", body: {} });
        toast(t("status", "Status") + ": " + providerStatusLabel(tested.provider_status, tested.status), tested.status === "red");
      } else if (action === "delete") {
        await api("/sources/" + encodeURIComponent(identity), { method: "DELETE", body: {} });
        toast(t("source_deleted", "Deleted"));
      }
      await loadSources();
      await refreshAll(false);
    } catch (error) {
      toast(error.message || t("request_failed", "Request failed"), true);
    } finally {
      setBusy(button, false);
    }
  }

  function openSourceModal(pasteMode, source) {
    byId("sourceForm").reset();
    byId("sourceFormError").textContent = "";
    byId("sourceId").value = source ? source.id : "";
    byId("sourceModalTitle").textContent = source ? t("edit", "Edit") : t("add_source", "Add Source");
    byId("pasteField").hidden = !pasteMode;
    byId("sourceType").disabled = Boolean(source);
    if (source) {
      byId("sourceType").value = source.type;
      byId("sourceName").value = source.name || "";
      if (source.type === "xtream") {
        byId("xtreamServer").value = source.endpoint || "";
        byId("xtreamOutput").value = source.output_format || "ts";
      } else if (source.type === "stalker") {
        byId("stalkerPortal").value = source.endpoint || "";
      } else if (source.type === "m3u") {
        byId("m3uKind").value = source.kind || "url";
        byId("m3uLocation").value = source.location || (source.kind === "file" ? "" : (source.endpoint || ""));
      }
    } else {
      byId("sourceType").value = "xtream";
    }
    updateSourceFields();
    byId("sourceModal").hidden = false;
    document.body.classList.add("modal-open");
    window.setTimeout(function () {
      (pasteMode ? byId("pasteValue") : byId("sourceName")).focus();
    }, 50);
  }

  function closeSourceModal() {
    byId("sourceModal").hidden = true;
    document.body.classList.remove("modal-open");
  }

  function updateSourceFields() {
    var type = byId("sourceType").value;
    document.querySelectorAll("[data-fields]").forEach(function (element) {
      element.hidden = element.getAttribute("data-fields") !== type;
    });
  }

  function parseSecurePaste() {
    var raw = String(byId("pasteValue").value || "").trim();
    if (!raw) {
      return;
    }
    var lines = raw.split(/\r?\n/).map(function (line) { return line.trim(); }).filter(Boolean);
    var urlText = lines[0] || "";
    var macLine = lines.find(function (line) { return /^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(line); });
    if (macLine && /^https?:\/\//i.test(urlText)) {
      byId("sourceType").value = "stalker";
      byId("stalkerPortal").value = urlText;
      byId("stalkerMac").value = macLine.toUpperCase();
      updateSourceFields();
      return;
    }
    try {
      var parsed = new URL(urlText);
      var username = parsed.searchParams.get("username");
      var password = parsed.searchParams.get("password");
      if (username && password) {
        parsed.search = "";
        parsed.hash = "";
        byId("sourceType").value = "xtream";
        byId("xtreamServer").value = parsed.toString().replace(/\/$/, "");
        byId("xtreamUser").value = username;
        byId("xtreamPassword").value = password;
        byId("xtreamOutput").value = /(?:^|[?&])output=m3u8(?:&|$)/i.test(urlText) ? "m3u8" : "ts";
        updateSourceFields();
        return;
      }
      byId("sourceType").value = "m3u";
      byId("m3uKind").value = "url";
      byId("m3uLocation").value = urlText;
      updateSourceFields();
    } catch (unusedError) {
      if (urlText.charAt(0) === "/") {
        byId("sourceType").value = "m3u";
        byId("m3uKind").value = "file";
        byId("m3uLocation").value = urlText;
        updateSourceFields();
      }
    }
  }

  function sourcePayload() {
    var type = byId("sourceType").value;
    var payload = { type: type, name: byId("sourceName").value.trim() };
    if (type === "xtream") {
      payload.server_url = byId("xtreamServer").value.trim();
      payload.username = byId("xtreamUser").value.trim();
      payload.password = byId("xtreamPassword").value;
      payload.output_format = byId("xtreamOutput").value;
    } else if (type === "stalker") {
      payload.portal_url = byId("stalkerPortal").value.trim();
      payload.mac = byId("stalkerMac").value.trim();
    } else {
      payload.kind = byId("m3uKind").value;
      payload.location = byId("m3uLocation").value.trim();
    }
    return payload;
  }

  async function saveSource(event) {
    event.preventDefault();
    parseSecurePaste();
    var identity = byId("sourceId").value;
    var submit = event.currentTarget.querySelector("button[type=submit]");
    setBusy(submit, true);
    byId("sourceFormError").textContent = "";
    try {
      await api(identity ? "/sources/" + encodeURIComponent(identity) : "/sources", {
        method: identity ? "PUT" : "POST",
        body: sourcePayload()
      });
      closeSourceModal();
      toast(t("source_saved", "Saved"));
      await loadSources();
      await refreshAll(false);
    } catch (error) {
      byId("sourceFormError").textContent = error.message || t("request_failed", "Request failed");
    } finally {
      setBusy(submit, false);
    }
  }

  async function loadDownloads() {
    try {
      var result = await api("/downloads");
      renderDownloads(result.items || []);
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderDownloads(items) {
    var target = byId("downloadsList");
    if (!items.length) {
      target.replaceChildren(emptyState(t("no_downloads", "No downloads")));
      return;
    }
    target.replaceChildren.apply(target, items.map(function (item) { return downloadRow(item, true); }));
  }

  async function handleDownloadAction(button) {
    setBusy(button, true);
    try {
      await api("/downloads/" + encodeURIComponent(button.dataset.downloadId) + "/" + button.dataset.downloadAction, { method: "POST", body: {} });
      await loadDownloads();
      await refreshAll(false);
    } catch (error) {
      toast(error.message || t("request_failed", "Request failed"), true);
    } finally {
      setBusy(button, false);
    }
  }

  async function loadEpg() {
    try {
      var result = await api("/epg");
      renderEpg(result);
      return result;
    } catch (error) {
      toast(error.message, true);
      return null;
    }
  }

  function infoLine(label, value) {
    var line = node("div", "info-line");
    append(line, node("span", "", label), node("strong", "", value));
    return line;
  }

  function renderEpg(data) {
    var target = byId("epgContent");
    var cards = [];
    var dvb = data.dvb || {};
    var dvbCard = node("article", "info-card");
    var dvbList = node("div", "info-list");
    append(dvbList,
      infoLine(t("status", "Status"), dvb.enabled ? t("on", "On") : t("off", "Off")),
      infoLine(t("last_update", "Last update"), dvb.last_success || "—"),
      infoLine(t("channels", "Channels"), String(dvb.mapped || 0)),
      infoLine(t("events", "Events"), String(dvb.events || 0))
    );
    append(dvbCard, node("h2", "", t("dvb_epg", "DVB EPG")), dvbList);
    cards.push(dvbCard);
    (data.m3u || []).forEach(function (binding) {
      var card = node("article", "info-card");
      var list = node("div", "info-list");
      append(list,
        infoLine(t("status", "Status"), binding.enabled ? t("on", "On") : t("off", "Off")),
        infoLine(t("last_update", "Last update"), binding.last_success || "—"),
        infoLine(t("channels", "Channels"), String(binding.channels || 0)),
        infoLine(t("events", "Events"), String(binding.events || 0))
      );
      var refresh = node("button", "mini-button", t("refresh", "Refresh"));
      refresh.type = "button";
      refresh.dataset.epgRefresh = binding.source_id;
      append(card, node("h2", "", binding.endpoint || t("xmltv_epg", "XMLTV EPG")), list, node("div", "row-actions"));
      card.lastChild.appendChild(refresh);
      cards.push(card);
    });
    target.replaceChildren.apply(target, cards);
  }

  async function refreshEpg(sourceId, button) {
    setBusy(button, true);
    try {
      await api("/epg/" + encodeURIComponent(sourceId) + "/refresh", { method: "POST", body: {} });
      toast(t("queued", "Queued"));
      window.setTimeout(loadEpg, 1000);
    } catch (error) {
      toast(error.message || t("request_failed", "Request failed"), true);
    } finally {
      setBusy(button, false);
    }
  }

  async function refreshAllEpg(button) {
    setBusy(button, true);
    try {
      var result = await api("/epg");
      var enabled = (result.m3u || []).filter(function (item) { return item.enabled; });
      if (!enabled.length) {
        throw new Error(t("not_configured", "Not configured"));
      }
      for (var index = 0; index < enabled.length; index += 1) {
        await api("/epg/" + encodeURIComponent(enabled[index].source_id) + "/refresh", { method: "POST", body: {} });
      }
      toast(t("queued", "Queued"));
      await loadEpg();
    } catch (error) {
      toast(error.message || t("request_failed", "Request failed"), true);
    } finally {
      setBusy(button, false);
    }
  }

  async function loadFavorites() {
    try {
      var result = await api("/favorites");
      renderFavorites(result.items || []);
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderFavorites(items) {
    var target = byId("favoritesList");
    if (!items.length) {
      target.replaceChildren(emptyState(t("no_favorites", "No favorites")));
      return;
    }
    var cards = items.map(function (item) {
      var card = node("article", "info-card");
      var list = node("div", "info-list");
      append(list,
        infoLine(t("type", "Type"), t(String(item.content_type || "").toLowerCase(), item.content_type || "—")),
        infoLine(t("year", "Year"), item.year || "—"),
        infoLine(t("rating", "Rating"), item.rating || "—")
      );
      var actions = node("div", "row-actions");
      var remove = node("button", "mini-button danger", t("delete", "Delete"));
      remove.type = "button";
      remove.dataset.favoriteDelete = item.key;
      actions.appendChild(remove);
      append(card, node("h2", "", item.name || "GT IPTV"), list, actions);
      return card;
    });
    target.replaceChildren.apply(target, cards);
  }

  async function deleteFavorite(button) {
    if (!window.confirm(t("confirm_delete", "Delete?"))) {
      return;
    }
    setBusy(button, true);
    try {
      await api("/favorites/" + encodeURIComponent(button.dataset.favoriteDelete), { method: "DELETE", body: {} });
      await loadFavorites();
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  }

  async function loadSettings() {
    try {
      byId("subtitleHttpWarning").hidden = window.location.protocol === "https:";
      renderSettings(await api("/settings"));
      renderSubtitleProviders(await api("/subtitles/providers"));
      renderTMDbSettings(await api("/settings/tmdb"));
      renderYouTubeSettings(await api("/settings/youtube"));
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderSettings(data) {
    var target = byId("settingsContent");
    var player = data.player || {};
    var weather = data.weather || {};
    var playerCard = node("article", "info-card");
    var playerList = node("div", "info-list");
    append(playerList,
      infoLine(t("live", "Live"), String(player.live_service_type || "—")),
      infoLine(t("movie", "Movie"), String(player.movie_service_type || "—")),
      infoLine(t("series", "Series"), String(player.series_service_type || "—")),
      infoLine("TMDB", player.tmdb_configured ? t("configured", "Configured") : t("not_configured", "Not configured"))
    );
    append(playerCard, node("h2", "", t("player", "Player / Codec")), playerList);
    var weatherCard = node("article", "info-card");
    var weatherList = node("div", "info-list");
    append(weatherList,
      infoLine(t("status", "Status"), weather.enabled ? t("on", "On") : t("off", "Off")),
      infoLine(t("city", "City"), weather.city || "—"),
      infoLine(t("unit", "Unit"), weather.unit || "C")
    );
    append(weatherCard, node("h2", "", t("weather", "Weather")), weatherList);
    var securityCard = node("article", "info-card");
    var securityList = node("div", "info-list");
    append(securityList,
      infoLine(t("version", "Version"), data.version || state.runtimeVersion || state.clientVersion || "—"),
      infoLine(t("network", "Network"), "LAN"),
      infoLine(t("security", "Security"), t("masked_notice", "Sensitive account data is hidden."))
    );
    append(securityCard, node("h2", "", "GT IPTV Player Pro"), securityList);
    target.replaceChildren(playerCard, weatherCard, securityCard);
  }

  function renderTMDbSettings(settings) {
    byId("tmdbStatus").textContent = settings.configured ? t("configured", "Configured") : t("not_configured", "Not configured");
    byId("tmdbTest").disabled = !settings.configured;
    byId("tmdbRemove").disabled = !settings.configured;
    byId("tmdbKey").placeholder = settings.configured ? t("subtitle_key_saved", "Key saved · enter a new key to replace it") : t("subtitle_api_key", "API key");
  }

  function renderYouTubeSettings(settings) {
    byId("youtubeResolution").value = settings.resolution || "720";
    byId("youtubeStreamMode").value = settings.mode || (settings.dash ? "auto" : "compatible");
    byId("youtubeAudioPreference").value = settings.audio_preference || "default";
    byId("youtubeLanguage").textContent = t("youtube_automatic", "Automatic") + " · " + browserLanguage();
  }

  async function saveExternalSettings(kind, payload, button) {
    setBusy(button, true);
    try {
      var updated = await api("/settings/" + kind, { method: "PUT", body: payload });
      if (kind === "tmdb") { renderTMDbSettings(updated); byId("tmdbKey").value = ""; }
      else { renderYouTubeSettings(updated); }
      toast(t("settings_saved", "Settings saved"));
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  async function testTMDb() {
    var button = byId("tmdbTest");
    setBusy(button, true);
    try { await api("/settings/tmdb/test", { method: "POST", body: {} }); toast(t("tmdb_test_ok", "TMDb connection successful")); }
    catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  function renderYouTubeResults(results) {
    state.youtubeResults = results || [];
    var cards = state.youtubeResults.map(function (entry) {
      var card = node("article", "video-result-card");
      var artwork = node("div", "video-artwork");
      var img = node("img"); img.src = entry.thumbnail; img.alt = ""; img.loading = "lazy"; img.referrerPolicy = "no-referrer";
      artwork.appendChild(img);
      if (entry.duration) { artwork.appendChild(node("span", "video-duration", entry.duration)); }
      var details = node("div", "video-card-details");
      append(details, node("h2", "", entry.title), node("p", "video-channel", entry.channel), node("small", "", entry.published));
      var button = node("button", "video-blue-button", t("play_on_tv", "Play on TV"));
      button.type = "button"; button.prepend(icon("device"));
      button.addEventListener("click", function () { playYouTube(entry, button); });
      append(card, artwork, details, button);
      return card;
    });
    byId("youtubeResults").replaceChildren.apply(byId("youtubeResults"), cards);
  }

  function renderYouTubePager() {
    var target = byId("youtubePager");
    var pages = state.youtubePages;
    var current = state.youtubePage;
    target.hidden = !pages.length || !(pages.length > 1 || pages[pages.length - 1].has_more);
    if (target.hidden) { target.replaceChildren(); return; }
    var controls = [];
    function control(label, page, disabled, active) {
      var button = node("button", "video-page-button" + (active ? " active" : ""), label);
      button.type = "button";
      button.disabled = disabled || state.youtubePagingBusy;
      if (active) { button.setAttribute("aria-current", "page"); }
      button.addEventListener("click", function () { gotoYouTubePage(page, button); });
      return button;
    }
    controls.push(control(t("previous_page", "Previous"), current - 1, current <= 1, false));
    var available = pages.length + (pages[pages.length - 1].has_more ? 1 : 0);
    var start = Math.max(1, Math.min(current - 2, available - 4));
    for (var number = start; number <= Math.min(available, start + 4); number += 1) {
      controls.push(control(String(number), number, false, number === current));
    }
    controls.push(control(t("next_page", "Next"), current + 1,
      current >= pages.length && !pages[pages.length - 1].has_more, false));
    target.replaceChildren.apply(target, controls);
  }

  async function gotoYouTubePage(page, button) {
    if (state.youtubePagingBusy || page < 1 || page > state.youtubePages.length + 1 || page === state.youtubePage) { return; }
    if (state.youtubePages[page - 1]) {
      state.youtubePage = page;
      renderYouTubeResults(state.youtubePages[page - 1].results);
      renderYouTubePager();
      byId("youtubeMessage").textContent = t("youtube_page_label", "Page") + " " + page;
      byId("youtubePager").scrollIntoView({ block: "nearest" });
      return;
    }
    var previous = state.youtubePages[state.youtubePages.length - 1];
    if (!previous || !previous.cursor) { return; }
    var serial = state.youtubeSearchSerial;
    state.youtubePagingBusy = true;
    renderYouTubePager();
    byId("youtubeMessage").textContent = t("searching", "Searching…");
    try {
      var path = "/youtube/search?q=" + encodeURIComponent(state.youtubeQuery) + "&cursor=" + encodeURIComponent(previous.cursor) + "&lang=" + encodeURIComponent(state.youtubeSearchLocale);
      var response = await api(path);
      if (serial !== state.youtubeSearchSerial) { return; }
      state.youtubePages.push(response);
      state.youtubePage = page;
      renderYouTubeResults(response.results);
      renderYouTubePager();
      byId("youtubeMessage").textContent = response.pagination_warning ? t(response.pagination_warning) : t("youtube_page_label", "Page") + " " + page;
      byId("youtubePager").scrollIntoView({ block: "nearest" });
    } catch (error) {
      if (serial === state.youtubeSearchSerial) {
        byId("youtubeMessage").textContent = error.message;
        toast(error.message, true);
      }
    } finally {
      if (serial === state.youtubeSearchSerial) {
        state.youtubePagingBusy = false;
        renderYouTubePager();
      }
    }
  }

  async function searchYouTube(event) {
    event.preventDefault();
    var query = byId("youtubeQuery").value.trim();
    var locale = browserLanguage();
    var serial = ++state.youtubeSearchSerial;
    state.youtubePagingBusy = false;
    var button = event.currentTarget.querySelector("button");
    setBusy(button, true);
    byId("youtubeMessage").textContent = t("searching", "Searching…");
    try {
      var result = await api("/youtube/search?q=" + encodeURIComponent(query) + "&lang=" + encodeURIComponent(locale));
      if (serial !== state.youtubeSearchSerial) { return; }
      state.youtubeQuery = query;
      state.youtubeSearchLocale = locale;
      state.youtubePages = [result];
      state.youtubePage = 1;
      renderYouTubeResults(result.results);
      renderYouTubePager();
      byId("youtubeMessage").textContent = result.pagination_warning ? t(result.pagination_warning) : (result.results.length ? "" : t("no_results", "No results found"));
    } catch (error) { if (serial === state.youtubeSearchSerial) { byId("youtubeMessage").textContent = error.message; toast(error.message, true); } }
    finally { setBusy(button, false); }
  }

  async function playYouTube(entry, button) {
    setBusy(button, true);
    try {
      var result = await api("/youtube/play", { method: "POST", body: { id: entry.id, title: entry.title } });
      state.youtubeJobToken = result.token;
      byId("youtubeMessage").textContent = t("youtube_resolving", "Preparing the video for TV…");
      pollYouTube();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
  }

  function clockTime(seconds) {
    var value = Math.max(0, Number(seconds) || 0);
    return Math.floor(value / 60) + ":" + String(Math.floor(value % 60)).padStart(2, "0");
  }

  async function pollYouTube() {
    if (state.page !== "youtube" || byId("appView").hidden) { return; }
    try {
      var data = await api("/youtube/status?token=" + encodeURIComponent(state.youtubeJobToken || ""));
      if (data.job && data.job.state === "failed") {
        byId("youtubeMessage").textContent = t("youtube_play_failed", "Video could not be played on this receiver. Try another video or quality setting.");
        state.youtubeJobToken = "";
      } else if (data.job && data.job.state === "playing") {
        byId("youtubeMessage").textContent = "";
      }
      byId("youtubeNow").hidden = !data.playing;
      if (data.playing) {
        byId("youtubeNowTitle").textContent = data.playing.title;
        var entry = null;
        state.youtubePages.some(function (page) {
          entry = (page.results || []).find(function (item) { return item.id === data.playing.id; });
          return Boolean(entry);
        });
        byId("youtubeNowImage").src = entry ? entry.thumbnail : "assets/logo.svg";
        var channel = entry ? entry.channel : "YouTube";
        var quality = Number(data.playing.quality) || 0;
        var requested = String(data.playing.requested_quality || "");
        byId("youtubeNowDetails").textContent = quality ? channel + " · " + t("youtube_actual_quality", "Quality") + " " + (requested && requested !== String(quality) ? requested + "p → " : "") + quality + "p" : channel;
        byId("youtubeNowTime").textContent = data.playing.length ? clockTime(data.playing.position) + " / " + clockTime(data.playing.length) : "";
        byId("youtubeProgress").style.width = data.playing.length ? Math.min(100, (data.playing.position / data.playing.length) * 100) + "%" : "0%";
      }
    } catch (unusedError) { /* The next status poll retries. */ }
  }

  async function loadMediaSources() {
    try {
      var sources = (await api("/sources")).items || [];
      var select = byId("mediaSource");
      var previous = select.value;
      select.replaceChildren.apply(select, sources.map(function (entry) {
        var option = node("option", "", entry.name); option.value = entry.id; return option;
      }));
      if (previous) { select.value = previous; }
      byId("mediaMessage").textContent = sources.length ? "" : t("no_sources", "No saved IPTV source was found");
    } catch (error) { byId("mediaMessage").textContent = error.message; }
  }

  function renderMediaResults(results) {
    state.mediaResults = results || [];
    var cards = state.mediaResults.map(function (entry) {
      var card = node("article", "video-result-card media-result-card");
      var artwork = node("div", "video-media-artwork"); artwork.appendChild(icon(entry.kind === "movie" ? "device" : "calendar"));
      var details = node("div", "video-card-details");
      append(details, node("h2", "", entry.name), node("p", "video-channel", entry.source),
        node("small", "", entry.season && entry.episode ? "S" + entry.season + " · E" + entry.episode : entry.kind === "movie" ? t("movies", "Movies") : t("series", "Series")));
      var button = node("button", "video-blue-button", entry.playable ? t("play_on_tv", "Play on TV") : t("choose_episode", "Choose episodes"));
      button.type = "button";
      button.addEventListener("click", function () { selectMedia(entry, button); });
      append(card, artwork, details, button); return card;
    });
    byId("mediaResults").replaceChildren.apply(byId("mediaResults"), cards);
  }

  async function searchMedia(event) {
    event.preventDefault();
    var button = event.currentTarget.querySelector("button");
    setBusy(button, true);
    byId("mediaMessage").textContent = t("searching", "Searching…");
    try {
      var path = "/media/search?source=" + encodeURIComponent(byId("mediaSource").value) + "&kind=" + encodeURIComponent(byId("mediaKind").value) + "&q=" + encodeURIComponent(byId("mediaQuery").value.trim());
      var data = await api(path);
      renderMediaResults(data.results);
      byId("mediaMessage").textContent = data.results.length ? "" : t("no_results", "No results found");
    } catch (error) { renderMediaResults([]); byId("mediaMessage").textContent = error.message; }
    finally { setBusy(button, false); }
  }

  async function selectMedia(entry, button) {
    setBusy(button, true);
    try {
      if (entry.playable) {
        await api("/media/play", { method: "POST", body: { token: entry.token } });
        toast(t("play_requested", "Playback requested on TV"));
      } else {
        byId("mediaMessage").textContent = t("loading_episodes", "Loading episodes…");
        var data = await api("/media/episodes?token=" + encodeURIComponent(entry.token));
        renderMediaResults(data.results);
        byId("mediaMessage").textContent = data.results.length ? entry.name : t("no_results", "No results found");
      }
    } catch (error) { toast(error.message, true); byId("mediaMessage").textContent = error.message; }
    finally { setBusy(button, false); }
  }

  function renderSubtitleProviders(providers) {
    state.subtitleProviders = providers || [];
    var cards = state.subtitleProviders.map(function (entry) {
      var card = node("article", "info-card subtitle-provider-card");
      var head = node("header");
      append(head, node("h3", "", entry.name), node("span", "subtitle-provider-badge" + (entry.configured ? "" : " missing"),
        entry.configured ? t("configured", "Configured") : t("not_configured", "Not configured")));
      card.appendChild(head);
      var enabled = node("label", "subtitle-toggle");
      var toggle = node("input");
      toggle.type = "checkbox";
      toggle.checked = Boolean(entry.enabled);
      toggle.addEventListener("change", function () { saveSubtitleProvider(entry.id, { enabled: toggle.checked }, toggle); });
      append(enabled, toggle, node("span", "", t("subtitle_enabled", "Use this provider")));
      card.appendChild(enabled);
      if (entry.needs_key) {
        var providerDocs = {
          subdl: "https://subdl.com/panel/api",
          subsource: "https://subsource.net/api-docs",
          opensubtitles: "https://opensubtitles.tawk.help/article/getting-started"
        };
        var documentation = node("a", "subtitle-doc-link", t("subtitle_get_key", "Where to get a key"));
        documentation.href = providerDocs[entry.id] || "#";
        documentation.target = "_blank";
        documentation.rel = "noopener noreferrer";
        card.appendChild(documentation);
        var label = node("label", "", t("subtitle_api_key", "API key"));
        var input = node("input");
        input.type = "password";
        input.autocomplete = "new-password";
        input.placeholder = entry.configured ? t("subtitle_key_saved", "Key saved · enter a new key to replace it") : t("subtitle_key_paste", "Paste your key here");
        label.appendChild(input);
        card.appendChild(label);
        var footer = node("footer");
        var save = node("button", "primary-button small", t("save", "Save"));
        save.type = "button";
        save.addEventListener("click", function () {
          if (input.value.trim()) {
            saveSubtitleProvider(entry.id, { key: input.value.trim() }, save);
            input.value = "";
          }
        });
        var test = node("button", "secondary-button small", t("test", "Test"));
        test.type = "button";
        test.disabled = !entry.configured;
        test.addEventListener("click", function () { testSubtitleProvider(entry.id, test); });
        var clear = node("button", "mini-button danger", t("subtitle_remove_key", "Remove key"));
        clear.type = "button";
        clear.disabled = !entry.configured;
        clear.addEventListener("click", function () { saveSubtitleProvider(entry.id, { clear: true }, clear); });
        append(footer, save, test, clear);
        card.appendChild(footer);
      } else {
        card.appendChild(node("p", "subtitle-hint", t("subtitle_keyless", "No key required. Guest limits apply.")));
      }
      return card;
    });
    byId("subtitleProviders").replaceChildren.apply(byId("subtitleProviders"), cards);
  }

  async function saveSubtitleProvider(name, payload, button) {
    setBusy(button, true);
    try {
      renderSubtitleProviders(await api("/subtitles/providers/" + encodeURIComponent(name),
        { method: "PUT", body: payload }));
      toast(t("subtitle_key_updated", "Subtitle provider updated"));
    } catch (error) {
      toast(error.message, true);
      loadSettings();
    } finally {
      setBusy(button, false);
    }
  }

  async function testSubtitleProvider(name, button) {
    setBusy(button, true);
    try {
      await api("/subtitles/providers/" + encodeURIComponent(name) + "/test", { method: "POST", body: {} });
      toast(t("subtitle_key_valid", "Provider connection succeeded"));
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  }

  async function loadSubtitleCurrent() {
    try {
      var data = await api("/subtitles/current");
      var playing = data.playing !== false;
      byId("subtitleCurrent").textContent = playing ? (data.title +
        (data.content_type === "series" ? " · S" + String(data.season).padStart(2, "0") + "E" + String(data.episode).padStart(2, "0") :
          (data.year ? " (" + data.year + ")" : ""))) : t("vod_not_playing", "Play a movie or episode on the receiver first.");
      byId("subtitleTitle").value = playing ? data.title : "";
      byId("subtitleSearchButton").disabled = !playing;
      byId("subtitleSearchHint").textContent = playing && !data.can_load ?
        t("subssupport_required", "Install SubsSupport or SubsSupport Pro to load external subtitles.") : "";
      renderSubtitleSync(data);
      if (!playing) {
        state.subtitleResults = null;
        byId("subtitleResults").replaceChildren();
        byId("subtitleProviderStatus").replaceChildren();
      }
    } catch (error) {
      byId("subtitleSearchButton").disabled = true;
      byId("subtitleCurrent").textContent = t("receiver_unavailable", "Receiver unavailable");
      byId("subtitleSyncPanel").hidden = true;
    }
  }

  function formatSubtitleTime(milliseconds) {
    if (milliseconds === null || milliseconds === undefined) { return ""; }
    var seconds = Math.floor(Number(milliseconds) / 1000);
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor(seconds / 60) % 60;
    var remainder = seconds % 60;
    var pair = function (value) { return String(value).padStart(2, "0"); };
    return hours ? hours + ":" + pair(minutes) + ":" + pair(remainder) :
      pair(Math.floor(seconds / 60)) + ":" + pair(remainder);
  }

  function renderSubtitleSync(data) {
    var available = Boolean(data.sync && data.sync.available);
    byId("subtitleSyncPanel").hidden = !available;
    if (!available) {
      byId("subtitleCueResults").replaceChildren();
      return;
    }
    var position = formatSubtitleTime(data.position_ms);
    byId("subtitlePosition").textContent = position || "—";
    if (position && !byId("subtitleMovieTime").value) { byId("subtitleMovieTime").value = position; }
    var offset = Number(data.sync.offset_ms || 0) / 1000;
    byId("subtitleOffset").textContent = (offset > 0 ? "+" : "") + offset.toFixed(1) + " s";
  }

  async function refreshSubtitlePosition() {
    try {
      var data = await api("/subtitles/current");
      renderSubtitleSync(data);
      var position = formatSubtitleTime(data.position_ms);
      if (!position) { throw new Error(t("subtitle_sync_position_missing", "Movie position is unavailable.")); }
      byId("subtitleMovieTime").value = position;
      await findSubtitleCues();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function findSubtitleCues() {
    var serial = ++state.cueQuerySerial;
    if (byId("subtitleSyncPanel").hidden) { return; }
    try {
      var result = await api("/subtitles/cues?q=" + encodeURIComponent(byId("subtitleCueQuery").value.trim()));
      if (serial !== state.cueQuerySerial || byId("subtitleSyncPanel").hidden) { return; }
      var entries = (result.items || []).map(function (item) {
        var button = node("button", "subtitle-cue-button", item.time + " · " + item.text);
        button.type = "button";
        button.addEventListener("click", function () {
          byId("subtitleFileTime").value = item.time;
        });
        return button;
      });
      byId("subtitleCueResults").replaceChildren.apply(byId("subtitleCueResults"), entries);
    } catch (unusedError) {
      if (serial === state.cueQuerySerial) { byId("subtitleCueResults").replaceChildren(); }
    }
  }

  async function submitSubtitleSync(event) {
    event.preventDefault();
    var button = byId("subtitleSyncButton");
    setBusy(button, true);
    try {
      await api("/subtitles/sync", { method: "POST", body: {
        film_time: byId("subtitleMovieTime").value.trim(),
        subtitle_time: byId("subtitleFileTime").value.trim()
      } });
      await loadSubtitleCurrent();
      toast(t("subtitle_sync_saved", "Subtitle timing adjusted"));
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  }

  async function adjustSubtitleSync(payload, button) {
    setBusy(button, true);
    try {
      await api("/subtitles/sync", { method: "POST", body: payload });
      await loadSubtitleCurrent();
      toast(t("subtitle_sync_saved", "Subtitle timing adjusted"));
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  }

  async function searchSubtitles(event) {
    event.preventDefault();
    var button = byId("subtitleSearchButton");
    setBusy(button, true);
    byId("subtitleSearchHint").textContent = t("subtitle_searching", "Searching providers…");
    try {
      state.subtitleResults = await api("/subtitles/search", { method: "POST",
        body: { language: byId("subtitleLanguage").value, title: byId("subtitleTitle").value } });
      renderSubtitleResults(state.subtitleResults);
    } catch (error) {
      toast(error.message, true);
    } finally {
      byId("subtitleSearchHint").textContent = "";
      setBusy(button, false);
    }
  }

  function renderSubtitleResults(data) {
    var status = (data.providers || []).map(function (entry) {
      return node("span", "", (entry.provider || "") + " · " +
        (entry.status === "ok" ? String(entry.count) : t(entry.status, "Unavailable")));
    });
    if (!status.length) {
      status.push(node("span", "", t("subtitle_no_providers", "Enable a subtitle provider in Settings.")));
    }
    byId("subtitleProviderStatus").replaceChildren.apply(byId("subtitleProviderStatus"), status);
    var items = (data.items || []).map(function (item) {
      var row = node("article", "subtitle-result");
      var detail = node("div");
      append(detail, node("strong", "", item.release || item.title),
        node("small", "", item.provider + " · " + item.language));
      var button = node("button", "secondary-button small", t("subtitle_use", "Download and use"));
      button.type = "button";
      button.disabled = !data.can_load;
      button.addEventListener("click", function () { applySubtitle(item.id, button); });
      append(row, detail, button);
      return row;
    });
    byId("subtitleResults").replaceChildren.apply(byId("subtitleResults"), items.length ? items : [
      emptyState(t("subtitle_no_results", "No matching subtitles found. Check the language and provider status."))
    ]);
  }

  async function applySubtitle(id, button) {
    setBusy(button, true);
    try {
      await api("/subtitles/apply", { method: "POST", body: { id: id } });
      toast(t("subtitle_loaded", "Subtitle loaded on the receiver"));
      byId("subtitleMovieTime").value = "";
      byId("subtitleFileTime").value = "";
      byId("subtitleCueQuery").value = "";
      await loadSubtitleCurrent();
      findSubtitleCues();
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  }

  async function loadAudioCurrent() {
    try {
      state.audioCurrent = await api("/audio/current");
      if (state.page === "audio") { renderAudioCurrent(state.audioCurrent); }
    } catch (error) {
      state.audioCurrent = null;
      byId("audioCurrentTitle").textContent = "—";
      byId("audioTrackList").replaceChildren();
      byId("audioStatus").textContent = error.message;
    }
  }

  function renderAudioCurrent(data) {
    var tracks = data.tracks || [];
    byId("audioCurrentTitle").textContent = data.playing ? data.title :
      t("vod_not_playing", "Play a movie or episode on the receiver first.");
    var message = "";
    if (data.playing && !tracks.length) {
      message = t("audio_no_tracks", "Audio tracks are not ready. Refresh after playback starts.");
    } else if (data.playing && tracks.length === 1) {
      message = t("audio_one_track", "This video has only one audio language.");
    } else if (data.playing && !data.selectable) {
      message = t("audio_selection_unavailable", "Audio selection is unavailable on this receiver.");
    }
    if (data.truncated) {
      message = t("audio_truncated", "Only the first 64 audio tracks are shown.");
    }
    byId("audioStatus").textContent = message;
    var cards = tracks.map(function (track) {
      var row = node("article", "audio-track-row" + (track.selected ? " selected" : ""));
      var label = node("strong", "", track.name);
      var button = node("button", "secondary-button small",
        track.selected ? t("audio_active", "Playing") : t("audio_select", "Use this audio"));
      button.type = "button";
      button.disabled = track.selected || !data.selectable || !data.token;
      button.addEventListener("click", function () { selectAudioTrack(track.index, data.token, button); });
      append(row, label, button);
      return row;
    });
    byId("audioTrackList").replaceChildren.apply(byId("audioTrackList"), cards);
  }

  async function selectAudioTrack(index, token, button) {
    setBusy(button, true);
    try {
      await api("/audio/select", { method: "POST", body: { index: index, token: token } });
      await new Promise(function (resolve) { window.setTimeout(resolve, 650); });
      await loadAudioCurrent();
      var active = state.audioCurrent && (state.audioCurrent.tracks || []).some(function (track) {
        return track.index === index && track.selected;
      });
      toast(active ? t("audio_selected", "Audio language changed") :
        t("audio_switch_pending", "Selection requested. Refresh to check the active audio."));
    } catch (error) {
      toast(error.message, true);
      if (error.code === "audio_tracks_changed") { loadAudioCurrent(); }
    } finally {
      setBusy(button, false);
    }
  }

  function switchPage(page) {
    stopAutomaticTestPolling();
    window.clearInterval(state.remoteTimer);
    window.clearInterval(state.youtubeTimer);
    state.remoteTimer = 0;
    state.youtubeTimer = 0;
    state.page = page;
    document.body.classList.toggle("youtube-active", page === "youtube");
    document.querySelectorAll("[data-page-panel]").forEach(function (panel) {
      panel.classList.toggle("active", panel.getAttribute("data-page-panel") === page);
    });
    document.querySelectorAll(".nav-button").forEach(function (button) {
      button.classList.toggle("active", button.dataset.page === page);
    });
    byId("sidebar").classList.remove("open");
    if (page === "sources") {
      loadSources();
    } else if (page === "automatic-test") {
      loadAutomaticTest();
    } else if (page === "epg") {
      loadEpg();
    } else if (page === "downloads") {
      loadDownloads();
    } else if (page === "favorites") {
      loadFavorites();
    } else if (page === "settings") {
      loadSettings();
    } else if (page === "subtitles") {
      loadSubtitleCurrent().then(findSubtitleCues);
    } else if (page === "audio") {
      loadAudioCurrent();
    } else if (page === "youtube") {
      pollYouTube();
      state.youtubeTimer = window.setInterval(pollYouTube, 3500);
    } else if (page === "media") {
      loadMediaSources();
    } else if (page === "remote") {
      pollKeyboard();
      state.remoteTimer = window.setInterval(pollKeyboard, 2500);
    } else {
      refreshAll(false);
    }
  }

  function updateKeyboardLabel() {
    var target = byId("remoteKeyboardStatus");
    var open = target.classList.contains("open");
    target.textContent = t("tv_keyboard", "TV keyboard") + " · " +
      (open ? t("on", "On") : t("off", "Off"));
  }

  async function pollKeyboard() {
    if (state.page !== "remote" || byId("appView").hidden) { return; }
    try {
      var data = await api("/remote/status");
      var open = Boolean(data.keyboard_open);
      byId("remoteKeyboardStatus").classList.toggle("open", open);
      byId("remotePaste").disabled = !open;
      byId("remotePasteSend").disabled = !open;
      updateKeyboardLabel();
    } catch (error) {
      byId("remotePaste").disabled = true;
      byId("remotePasteSend").disabled = true;
      byId("remoteKeyboardStatus").classList.remove("open");
      updateKeyboardLabel();
    }
  }

  async function pressRemote(button) {
    if (button.disabled) { return; }
    setBusy(button, true);
    try {
      await api("/remote/key", { method: "POST", body: { key: button.dataset.remoteKey } });
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  }

  async function sendToTv() {
    if (state.remoteSending || byId("remotePaste").disabled) { return; }
    var field = byId("remotePaste");
    var value = field.value;
    if (!value) { return; }
    state.remoteSending = true;
    setBusy(byId("remotePasteSend"), true);
    try {
      await api("/remote/paste", { method: "POST", body: { text: value } });
      field.value = "";
      toast(t("send_to_tv", "Send to TV"));
    } catch (error) {
      toast(error.message, true);
      if (error.code === "keyboard_closed") { pollKeyboard(); }
    } finally {
      state.remoteSending = false;
      setBusy(byId("remotePasteSend"), false);
      byId("remotePasteSend").disabled = !byId("remoteKeyboardStatus").classList.contains("open");
    }
  }

  function toast(message, isError) {
    var target = byId("toast");
    target.textContent = message || t("request_failed", "Request failed");
    target.classList.toggle("error", Boolean(isError));
    target.hidden = false;
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(function () { target.hidden = true; }, 3600);
  }

  async function logout() {
    try {
      await api("/logout", { method: "POST", body: {} });
    } catch (unusedError) {
      return showAuth();
    }
    showAuth();
  }

  function bindEvents() {
    byId("youtubeSearchForm").addEventListener("submit", searchYouTube);
    byId("mediaSearchForm").addEventListener("submit", searchMedia);
    byId("tmdbSave").addEventListener("click", function () {
      if (byId("tmdbKey").value.trim()) { saveExternalSettings("tmdb", { key: byId("tmdbKey").value.trim() }, byId("tmdbSave")); }
    });
    byId("tmdbRemove").addEventListener("click", function () { saveExternalSettings("tmdb", { remove: true }, byId("tmdbRemove")); });
    byId("tmdbTest").addEventListener("click", testTMDb);
    byId("youtubeSettingsSave").addEventListener("click", function () {
      var payload = { resolution: byId("youtubeResolution").value, mode: byId("youtubeStreamMode").value, audio_preference: byId("youtubeAudioPreference").value };
      saveExternalSettings("youtube", payload, byId("youtubeSettingsSave"));
    });
    byId("subtitleSearchForm").addEventListener("submit", searchSubtitles);
    byId("audioRefresh").addEventListener("click", loadAudioCurrent);
    byId("subtitleSyncForm").addEventListener("submit", submitSubtitleSync);
    byId("subtitlePositionRefresh").addEventListener("click", refreshSubtitlePosition);
    byId("subtitleSyncReset").addEventListener("click", function (event) {
      adjustSubtitleSync({ reset: true }, event.currentTarget);
    });
    document.querySelectorAll("[data-subtitle-step]").forEach(function (button) {
      button.addEventListener("click", function () {
        adjustSubtitleSync({ delta_ms: Number(button.dataset.subtitleStep) }, button);
      });
    });
    byId("subtitleCueQuery").addEventListener("input", function () {
      window.clearTimeout(state.cueTimer);
      state.cueTimer = window.setTimeout(findSubtitleCues, 300);
    });
    document.querySelectorAll("[data-remote-key]").forEach(function (button) {
      button.addEventListener("click", function () { pressRemote(button); });
    });
    byId("remotePasteSend").addEventListener("click", sendToTv);
    byId("remotePaste").addEventListener("paste", function () {
      window.setTimeout(sendToTv, 0);
    });
    byId("remotePaste").addEventListener("input", function (event) {
      if (event.inputType === "insertFromPaste") { window.setTimeout(sendToTv, 0); }
    });
    byId("pairForm").addEventListener("submit", submitPair);
    byId("pairCode").addEventListener("input", function (event) {
      var digits = event.target.value.replace(/\D/g, "").slice(0, 6);
      event.target.value = digits;
    });
    [byId("authLanguage"), byId("languageSelect")].forEach(function (select) {
      select.addEventListener("change", function (event) {
        loadLanguage(event.target.value).catch(function () {
          toast(t("request_failed", "Request failed"), true);
        });
      });
    });
    document.querySelectorAll(".nav-button").forEach(function (button) {
      button.addEventListener("click", function () { switchPage(button.dataset.page); });
    });
    byId("menuButton").addEventListener("click", function () { byId("sidebar").classList.toggle("open"); });
    byId("logoutButton").addEventListener("click", logout);
    document.querySelectorAll("[data-auto-test-type]").forEach(function (button) {
      button.addEventListener("click", function () { startAutomaticTest(button); });
    });
    byId("cancelAutomaticTest").addEventListener("click", function () { cancelAutomaticTest(byId("cancelAutomaticTest")); });
    byId("quickAdd").addEventListener("click", function () { openSourceModal(false); });
    byId("addSourceButton").addEventListener("click", function () { openSourceModal(false); });
    byId("quickPaste").addEventListener("click", function () { openSourceModal(true); });
    byId("quickEpg").addEventListener("click", function () { refreshAllEpg(byId("quickEpg")); });
    byId("refreshEpgButton").addEventListener("click", function () { refreshAllEpg(byId("refreshEpgButton")); });
    byId("refreshDownloadsButton").addEventListener("click", loadDownloads);
    byId("closeSourceModal").addEventListener("click", closeSourceModal);
    byId("cancelSource").addEventListener("click", closeSourceModal);
    byId("sourceModal").addEventListener("click", function (event) {
      if (event.target === byId("sourceModal")) {
        closeSourceModal();
      }
    });
    byId("sourceType").addEventListener("change", updateSourceFields);
    byId("pasteValue").addEventListener("input", parseSecurePaste);
    byId("sourceForm").addEventListener("submit", saveSource);
    byId("sourcesList").addEventListener("click", function (event) {
      var button = event.target.closest("[data-source-action]");
      if (button) {
        handleSourceAction(button);
      }
    });
    byId("downloadsList").addEventListener("click", function (event) {
      var button = event.target.closest("[data-download-action]");
      if (button) {
        handleDownloadAction(button);
      }
    });
    byId("epgContent").addEventListener("click", function (event) {
      var button = event.target.closest("[data-epg-refresh]");
      if (button) {
        refreshEpg(button.dataset.epgRefresh, button);
      }
    });
    byId("favoritesList").addEventListener("click", function (event) {
      var button = event.target.closest("[data-favorite-delete]");
      if (button) {
        deleteFavorite(button);
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !byId("sourceModal").hidden) {
        closeSourceModal();
      }
    });
  }

  bindEvents();
  bootstrap();
}());
