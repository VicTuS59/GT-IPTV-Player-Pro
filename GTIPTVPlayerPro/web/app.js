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
    youtubeHistory: [],
    youtubeQuality: "",
    youtubeSearchBusy: false,
    youtubePages: [],
    youtubePage: 1,
    youtubePagingBusy: false,
    youtubeQuery: "",
    youtubeSearchLocale: "",
    youtubeSearchSerial: 0,
    youtubeSearchToken: "",
    youtubePrefetch: null,
    youtubeJobToken: "",
    youtubePlayingId: "",
    youtubeTimer: 0,
    mediaResults: [],
    mediaDownloadPending: {},
    runtimeVersion: "",
    runtimeWarningForced: false,
    csrf: "",
    expiresIn: 0,
    language: "en",
    strings: {},
    runtimeTexts: {},
    languageRequestSerial: 0,
    youtubeSettings: null,
    youtubeStatus: null,
    sources: [],
    dashboard: null,
    automaticTest: null,
    automaticTestUnavailable: false,
    automaticTestLoading: false,
    automaticTestRequestSerial: 0,
    automaticTestDeleting: {},
    automaticTestTimer: 0,
    subtitleProviders: [],
    subtitleResults: null,
    subtitleContext: null,
    subtitleCurrentSerial: 0,
    subtitleSearchSerial: 0,
    subtitleApplySerial: 0,
    subtitleSearchBusySerial: 0,
    subtitleApplyBusySerial: 0,
    subtitleCanSearch: false,
    subtitleSelectedId: null,
    subtitleScroll: 0,
    subtitleFormDirty: false,
    subtitleRestoreScroll: false,
    subtitleSyncRevision: null,
    subtitlePositionSerial: 0,
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

  function serverText(value) {
    var text = String(value || "");
    var messages = state.runtimeTexts;
    if (messages[text]) { return messages[text]; }
    var templates = Object.keys(messages);
    for (var index = 0; index < templates.length; index += 1) {
      var template = templates[index];
      var parts = template.split("{}");
      if (parts.length !== 2 || !text.startsWith(parts[0]) || !text.endsWith(parts[1])) { continue; }
      var argument = text.slice(parts[0].length, parts[1] ? -parts[1].length : undefined);
      return messages[template].replace("{}", messages[argument] || argument);
    }
    return text;
  }

  function audioTrackLabel(value) {
    var label = String(value || "");
    var numbered = /^(\d+\.\s*)(.*)$/.exec(label);
    var prefix = numbered ? numbered[1] : "";
    var details = numbered ? numbered[2] : label;
    return prefix + details.split(" • ").map(serverText).join(" • ");
  }

  function translateCurrentMessages(previous) {
    var keys = Object.keys(previous).sort(function (a, b) { return previous[b].length - previous[a].length; });
    ["toast", "pairStatus", "sourceFormError", "youtubeMessage", "mediaMessage", "audioStatus", "subtitleSearchHint"].forEach(function (id) {
      var element = byId(id);
      var text = element.textContent;
      keys.some(function (key) {
        var old = previous[key];
        if (text === old || text.startsWith(old + " · ") || text.startsWith(old + " ")) {
          element.textContent = t(key) + text.slice(old.length);
          return true;
        }
        return false;
      });
    });
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
    var serial = ++state.languageRequestSerial;
    var language = requested || preferredLanguage();
    var response;
    var payload;
    try {
      response = await window.fetch(apiUrl("/i18n?lang=" + encodeURIComponent(language)), {
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json" }
      });
      payload = await response.json();
    } catch (unusedError) {
      if (serial !== state.languageRequestSerial) { return; }
      throw new Error(t("request_failed", "Request failed"));
    }
    if (serial !== state.languageRequestSerial) { return; }
    if (!response.ok || !payload.ok) {
      throw new Error(t("request_failed", "Request failed"));
    }
    var catalog = payload.data || {};
    var previous = state.strings;
    state.language = catalog.language || "en";
    state.strings = catalog.strings || {};
    state.runtimeTexts = catalog.runtime_texts || {};
    translateCurrentMessages(previous);
    document.documentElement.lang = state.language.replace(/_/g, "-");
    document.documentElement.dir = catalog.direction === "rtl" ? "rtl" : "ltr";
    safeStorageSet("gt-web-language", state.language);
    safeStorageSet("gt-web-browser-language", browserLanguage());
    document.querySelectorAll("[data-i18n]").forEach(function (element) {
      var key = element.getAttribute("data-i18n");
      if (element.id === "youtubeMessage" && Object.keys(previous).length &&
          element.textContent !== previous[key] && element.textContent !== t(key)) { return; }
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
    fillSubtitleLanguageSelect(catalog.languages || []);
    byId("youtubeLanguage").textContent = t("youtube_automatic", "Automatic") + " · " + browserLanguage();
    updateKeyboardLabel();
    if (!byId("sourceModal").hidden) {
      byId("sourceModalTitle").textContent = byId("sourceId").value ? t("edit", "Edit") : t("add_source", "Add Source");
    }
    var quality = String((state.youtubeSettings || {}).resolution || byId("youtubeResolution").value || "1440");
    byId("youtubeSearchQuality").textContent = t("youtube_actual_quality", "Quality") + " · " + youTubeQualityLabel(quality);
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

  function fillSubtitleLanguageSelect(languages) {
    var select = byId("subtitleLanguage");
    var previous = select.dataset.populated ? select.value : state.language.split("_")[0];
    var seen = {};
    var fragment = document.createDocumentFragment();
    languages.forEach(function (language) {
      var code = String(language.code || "").split("_")[0].toLowerCase();
      if (!/^[a-z]{2,3}$/.test(code) || seen[code]) { return; }
      seen[code] = true;
      var option = node("option", "", language.name || code);
      option.value = code;
      fragment.appendChild(option);
    });
    if (!Object.keys(seen).length) { return; }
    select.replaceChildren(fragment);
    select.value = seen[previous] ? previous : (seen.en ? "en" : Object.keys(seen)[0]);
    select.dataset.populated = "1";
  }

  function mobileMenuLayout() {
    return typeof window.matchMedia === "function" ?
      window.matchMedia("(max-width: 1024px)").matches : Number(window.innerWidth || 1280) <= 1024;
  }

  function setMenuOpen(open, restoreFocus) {
    var sidebar = byId("sidebar");
    var wasOpen = sidebar.classList.contains("open");
    var mobile = mobileMenuLayout();
    var visible = Boolean(open && mobile && !byId("appView").hidden);
    sidebar.classList.toggle("open", visible);
    sidebar.inert = mobile && !visible;
    byId("workspace").inert = visible;
    byId("menuBackdrop").hidden = !visible;
    byId("menuButton").setAttribute("aria-expanded", String(visible));
    document.body.classList.toggle("menu-open", visible);
    if (visible) {
      sidebar.removeAttribute("aria-hidden");
      sidebar.setAttribute("role", "dialog");
      sidebar.setAttribute("aria-modal", "true");
      if (!wasOpen) { byId("closeMenuButton").focus(); }
    } else {
      sidebar.removeAttribute("role");
      sidebar.removeAttribute("aria-modal");
      if (mobile) { sidebar.setAttribute("aria-hidden", "true"); }
      else { sidebar.removeAttribute("aria-hidden"); }
      if (wasOpen && restoreFocus !== false && !byId("appView").hidden) {
        var target = mobile ? byId("menuButton") : sidebar.querySelector(".nav-button.active");
        if (target) { target.focus(); }
      }
    }
  }

  function handleMenuKeyboard(event) {
    var sidebar = byId("sidebar");
    if (!sidebar.classList.contains("open")) { return; }
    if (event.key === "Escape") {
      event.preventDefault();
      setMenuOpen(false);
    } else if (event.key === "Tab") {
      var buttons = Array.prototype.filter.call(sidebar.querySelectorAll("button"), function (button) {
        return !button.disabled && !button.hidden;
      });
      var first = buttons[0];
      var last = buttons[buttons.length - 1];
      if (!first) { return; }
      if (event.shiftKey && (document.activeElement === first || !sidebar.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !sidebar.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  function showAuth(message, isError) {
    setMenuOpen(false, false);
    cancelYouTubeSearch();
    ++state.youtubeSearchSerial;
    state.youtubeSearchBusy = state.youtubePagingBusy = false;
    setBusy(byId("youtubeSearchForm").querySelector("button"), false);
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

  function formattedPairCode(value) {
    var digits = String(value || "").replace(/\D/g, "").slice(0, 6);
    return digits.length > 3 ? digits.slice(0, 3) + " " + digits.slice(3) : digits;
  }

  function formatPairCodeInput(event) {
    var input = event.target;
    var raw = String(input.value || "");
    var cursor = input.selectionStart;
    var digitsBefore = raw.slice(0, typeof cursor === "number" ? cursor : raw.length).replace(/\D/g, "").length;
    input.value = formattedPairCode(raw);
    if (typeof cursor === "number" && typeof input.setSelectionRange === "function") {
      var afterSeparator = digitsBefore > 3 || (digitsBefore === 3 &&
        (event.inputType === "deleteContentForward" || raw.charAt(cursor - 1) === " "));
      var position = Math.min(input.value.length, digitsBefore + (afterSeparator && input.value.length > 3 ? 1 : 0));
      input.setSelectionRange(position, position);
    }
  }

  async function submitPair(event) {
    event.preventDefault();
    var button = event.currentTarget.querySelector("button[type=submit]");
    var code = String(byId("pairCode").value || "").replace(/\D/g, "").slice(0, 6);
    byId("pairCode").value = formattedPairCode(code);
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
    }
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
    } else if (state.page === "audio" && state.audioCurrent) {
      renderAudioCurrent(state.audioCurrent);
    } else if (state.page === "youtube") {
      renderYouTubeResults(state.youtubeResults);
      renderYouTubeHistory(state.youtubeHistory);
      renderYouTubePager();
      if (state.youtubeStatus) { renderYouTubeStatus(state.youtubeStatus); }
    } else if (state.page === "media") {
      renderMediaResults(state.mediaResults);
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

  function restoreAutomaticTestAvailability() {
    if (state.automaticTestUnavailable) {
      state.automaticTestUnavailable = false;
      state.runtimeWarningForced = false;
      updateRuntimeWarning(false);
    }
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
    append(head, statusDot(item.status === "downloading" ? "green" : (item.status === "error" ? "red" : "yellow")), node("strong", "", item.name || t("video", "Video")), node("small", "", String(item.percent || 0) + "%"));
    var track = node("div", "progress-track");
    var bar = node("i");
    bar.style.width = String(Math.max(0, Math.min(100, Number(item.percent || 0)))) + "%";
    track.appendChild(bar);
    var meta = node("div", "download-meta");
    append(meta, node("span", "", (item.downloaded_text || "0 B") + (item.total_text ? " / " + item.total_text : "")), node("span", "", item.speed_text || (item.status === "stopping" ? t("test_stopping", "Stopping") : t(item.status, t("status", "Status")))));
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
    if (state.page === "automatic-test" && !byId("appView").hidden && automaticTestActive(state.automaticTest)) {
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

  function automaticTestAccountLabel(item) {
    return item.account_hint ? t(item.type === "stalker" ? "mac_address" : "username") + " · " + item.account_hint : "";
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
    if (item.account_hint) { title.appendChild(node("small", "test-result-account", automaticTestAccountLabel(item))); }
    var badges = node("div", "test-result-badges");
    append(badges,
      node("span", "type-badge", sourceTypeShortLabel(item.type)),
      node("span", "test-result-status", providerStatusLabel(item.provider_status, status))
    );
    append(head, statusDot(status), title, badges);
    var details = node("div", "test-result-details");
    append(details,
      infoLine(t("connection_status", "Connection status"), providerStatusLabel(item.account_status || item.provider_status, status)),
      infoLine(t("expiry_date", "Expiry date"), serverText(item.expiry) || "—"),
      infoLine(t("connections", "Connections"), automaticTestConnections(item)),
      infoLine(t("latency", "Latency"), Number(item.latency_ms || 0) > 0 ? String(item.latency_ms) + " ms" : "—")
    );
    append(card, head, details);
    if (item.detail) {
      card.appendChild(node("p", "test-result-message",
        serverText(item.detail)));
    }
    if (item.id) {
      var actions = node("div", "test-result-actions");
      var remove = sourceButton(t("delete", "Delete"), "delete", item.id, true);
      remove.classList.add("test-result-delete");
      remove.setAttribute("aria-label", t("delete", "Delete") + " · " + (item.name || sourceTypeLabel(item.type)));
      setBusy(remove, Boolean(state.automaticTestDeleting[item.id]));
      remove.setAttribute("aria-busy", String(Boolean(state.automaticTestDeleting[item.id])));
      actions.appendChild(remove);
      card.appendChild(actions);
    }
    return card;
  }

  function renderAutomaticTest(data) {
    data = data || { state: "idle", type: "", total: 0, completed: 0, summary: {}, results: [] };
    var previous = state.automaticTest;
    if (previous && data.instance && data.instance === previous.instance && Number(data.revision) < Number(previous.revision)) { return; }
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
    var serial = ++state.automaticTestRequestSerial;
    var previousState = (state.automaticTest || {}).state;
    try {
      var result = await api("/automatic-test");
      if (serial !== state.automaticTestRequestSerial) { return; }
      restoreAutomaticTestAvailability();
      renderAutomaticTest(result);
      if (["running", "cancelling"].indexOf(previousState) >= 0 && !automaticTestActive(result)) {
        refreshAll(false);
      }
    } catch (error) {
      if (serial !== state.automaticTestRequestSerial) { return; }
      if (!handleAutomaticTestUnavailable(error)) {
        toast(error.message || t("request_failed", "Request failed"), true);
      }
    } finally {
      state.automaticTestLoading = false;
      scheduleAutomaticTestPolling();
    }
  }

  async function deleteAutomaticTestSource(button) {
    var identity = button.dataset.sourceId;
    var item = ((state.automaticTest || {}).results || []).find(function (entry) { return entry.id === identity; });
    if (!item || state.automaticTestDeleting[identity]) { return; }
    var description = [item.name || sourceTypeLabel(item.type), item.endpoint, automaticTestAccountLabel(item)].filter(Boolean).join("\n");
    if (!window.confirm(t("confirm_delete", "Delete this item?") + "\n\n" + description)) { return; }
    state.automaticTestDeleting[identity] = true;
    setBusy(button, true);
    button.setAttribute("aria-busy", "true");
    try {
      var result = await api("/sources/" + encodeURIComponent(identity), { method: "DELETE", body: {} });
      ++state.automaticTestRequestSerial;
      state.sources = state.sources.filter(function (entry) { return entry.id !== identity; });
      if (result.automatic_test) { renderAutomaticTest(result.automatic_test); }
      else { await loadAutomaticTest(); }
      toast(t("source_deleted", "Source deleted"));
      refreshAll(false);
    } catch (error) {
      toast(error.message || t("request_failed", "Request failed"), true);
    } finally {
      delete state.automaticTestDeleting[identity];
      setBusy(button, false);
      button.setAttribute("aria-busy", "false");
      document.querySelectorAll(".test-result-delete").forEach(function (current) {
        if (current.dataset.sourceId === identity) {
          setBusy(current, false);
          current.setAttribute("aria-busy", "false");
        }
      });
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
      restoreAutomaticTestAvailability();
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
      var result = await api("/automatic-test/cancel", { method: "POST", body: {} });
      restoreAutomaticTestAvailability();
      renderAutomaticTest(result);
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
      var details = node("div", "health-details", serverText(health.detail) || item.label || t("masked_notice", "Sensitive account data is hidden."));
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

  function youTubeQualityLabel(value) {
    return value === "2160" ? "4K (2160p)" : value === "1440" ? "2K (1440p)" : value + "p";
  }

  function renderYouTubeSettings(settings) {
    state.youtubeSettings = settings || {};
    var quality = String(settings.resolution || "1440");
    state.youtubeQuality = quality;
    byId("youtubeResolution").value = quality;
    byId("youtubeAutoplay").value = settings.autoplay ? "on" : "off";
    byId("youtubeLanguage").textContent = t("youtube_automatic", "Automatic") + " · " + browserLanguage();
    byId("youtubeSearchQuality").textContent = t("youtube_actual_quality", "Quality") + " · " + youTubeQualityLabel(quality);

  }

  function renderYouTubeHistory(queries) {
    state.youtubeHistory = queries || [];
    var rows = state.youtubeHistory.map(function (query) {
      var row = node("div", "youtube-history-row");
      var search = node("button", "youtube-history-query", query);
      search.type = "button";
      search.addEventListener("click", function () {
        byId("youtubeQuery").value = query;
        byId("youtubeHistory").hidden = true;
        byId("youtubeHistoryToggle").setAttribute("aria-expanded", "false");
        byId("youtubeSearchForm").requestSubmit();
      });
      var remove = node("button", "secondary-button small", t("delete", "Delete"));
      remove.type = "button";
      remove.setAttribute("aria-label", t("delete", "Delete") + " · " + query);
      remove.addEventListener("click", function () { deleteYouTubeHistory(query, remove); });
      append(row, search, remove);
      return row;
    });
    if (!rows.length) { rows.push(node("p", "video-message", t("no_results", "No results found"))); }
    byId("youtubeHistoryList").replaceChildren.apply(byId("youtubeHistoryList"), rows);
    byId("youtubeHistoryClear").disabled = !state.youtubeHistory.length;
  }

  async function loadYouTubeHistory() {
    try { renderYouTubeHistory((await api("/youtube/history")).queries); }
    catch (error) { byId("youtubeMessage").textContent = error.message; }
  }

  async function deleteYouTubeHistory(query, button) {
    setBusy(button, true);
    try {
      var payload = query === null ? {} : { query: query };
      renderYouTubeHistory((await api("/youtube/history", { method: "DELETE", body: payload })).queries);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(button, false); }
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

  function cancelYouTubeSearch() {
    var token = state.youtubeSearchToken;
    state.youtubeSearchToken = "";
    if (token) {
      api("/youtube/search-job", { method: "DELETE", body: { token: token } }).catch(function () {});
    }
    cancelYouTubePrefetch();
  }

  function cancelYouTubePrefetch() {
    var task = state.youtubePrefetch;
    state.youtubePrefetch = null;
    if (task) {
      task.cancelled = true;
      if (task.token) {
        api("/youtube/search-job", { method: "DELETE", body: { token: task.token } }).catch(function () {});
      }
    }
  }

  async function getYouTubeSearch(query, cursor, serial, update, button, task) {
    var response = await api("/youtube/search?q=" + encodeURIComponent(query) + "&cursor=" + encodeURIComponent(cursor || "")
      + "&lang=" + encodeURIComponent(state.youtubeSearchLocale) + "&progress=1");
    var token = response.search_token || "";
    function obsolete() {
      if (serial === state.youtubeSearchSerial && !(task && task.cancelled)) { return false; }
      if (token) {
        api("/youtube/search-job", { method: "DELETE", body: { token: token } }).catch(function () {});
      }
      return true;
    }
    if (obsolete()) { return null; }
    if (task) { task.token = token; }
    else { state.youtubeSearchToken = token; }
    if (button) { setBusy(button, false); }
    try {
      while (true) {
        if (obsolete()) { return null; }
        if (response.results.length || !response.error) { update(response); }
        if (!response.pending) {
          if (response.error) { throw new Error(t(response.error, t("request_failed", "Request failed"))); }
          return response;
        }
        if (!token) { throw new Error(t("request_failed", "Request failed")); }
        await new Promise(function (resolve) { window.setTimeout(resolve, 350); });
        if (obsolete()) { return null; }
        response = await api("/youtube/search-job?token=" + encodeURIComponent(token));
      }
    } catch (error) {
      if (token) {
        api("/youtube/search-job", { method: "DELETE", body: { token: token } }).catch(function () {});
      }
      throw error;
    } finally {
      if (task) { task.token = ""; }
      else if (state.youtubeSearchToken === token) { state.youtubeSearchToken = ""; }
    }
  }

  function showYouTubePage(page, response) {
    state.youtubePages[page - 1] = response;
    state.youtubePage = page;
    renderYouTubeResults(response.results);
    renderYouTubePager();
    byId("youtubeMessage").textContent = response.pending ? t("searching", "Searching…") :
      response.pagination_warning ? t(response.pagination_warning) : t("youtube_page_label", "Page") + " " + page;
  }

  function prepareNextYouTubePage() {
    if (state.page !== "youtube" || state.youtubeSearchBusy || state.youtubePagingBusy) { return; }
    var previous = state.youtubePages[state.youtubePage - 1];
    var page = state.youtubePage + 1;
    if (!previous || previous.pending || previous.error || !previous.has_more || !previous.cursor
        || state.youtubePages[page - 1]) { return; }
    if (state.youtubePrefetch && state.youtubePrefetch.page === page) { return; }
    cancelYouTubePrefetch();
    var task = { page: page, serial: state.youtubeSearchSerial, token: "", response: null,
      promoted: false, cancelled: false, error: null, promise: null };
    state.youtubePrefetch = task;
    task.promise = getYouTubeSearch(state.youtubeQuery, previous.cursor, task.serial, function (response) {
      task.response = response;
      if (task.promoted) { showYouTubePage(page, response); }
    }, null, task).catch(function (error) {
      // A failed speculative request must leave the current page usable.
      task.error = error;
      return null;
    });
  }

  async function gotoYouTubePage(page, button) {
    if (state.youtubePagingBusy || page < 1 || page > state.youtubePages.length + 1 || page === state.youtubePage) { return; }
    if (state.youtubePages[page - 1]) {
      state.youtubePage = page;
      renderYouTubeResults(state.youtubePages[page - 1].results);
      renderYouTubePager();
      byId("youtubeMessage").textContent = t("youtube_page_label", "Page") + " " + page;
      byId("youtubePager").scrollIntoView({ block: "nearest" });
      prepareNextYouTubePage();
      return;
    }
    var previous = state.youtubePages[state.youtubePages.length - 1];
    if (!previous || !previous.cursor) { return; }
    var serial = state.youtubeSearchSerial;
    var prepared = state.youtubePrefetch;
    if (prepared && prepared.page === page && prepared.error) {
      cancelYouTubePrefetch();
      prepared = null;
    }
    state.youtubePagingBusy = true;
    renderYouTubePager();
    byId("youtubeMessage").textContent = t("searching", "Searching…");
    try {
      if (prepared && prepared.page === page) {
        prepared.promoted = true;
        if (prepared.response) { showYouTubePage(page, prepared.response); }
        await prepared.promise;
        if (state.youtubePrefetch === prepared) { state.youtubePrefetch = null; }
        if (prepared.error && serial === state.youtubeSearchSerial) { throw prepared.error; }
      } else {
        await getYouTubeSearch(state.youtubeQuery, previous.cursor, serial, function (response) {
          showYouTubePage(page, response);
        });
      }
      if (serial === state.youtubeSearchSerial) { byId("youtubePager").scrollIntoView({ block: "nearest" }); }
    } catch (error) {
      if (serial === state.youtubeSearchSerial) {
        byId("youtubeMessage").textContent = error.message;
        toast(error.message, true);
      }
    } finally {
      if (serial === state.youtubeSearchSerial) {
        state.youtubePagingBusy = false;
        renderYouTubePager();
        prepareNextYouTubePage();
      }
    }
  }

  async function searchYouTube(event) {
    event.preventDefault();
    var query = byId("youtubeQuery").value.trim();
    var locale = browserLanguage();
    var serial = ++state.youtubeSearchSerial;
    cancelYouTubeSearch();
    state.youtubePagingBusy = false;
    state.youtubeSearchBusy = true;
    state.youtubeQuery = query;
    state.youtubeSearchLocale = locale;
    state.youtubePages = [];
    renderYouTubeResults([]);
    renderYouTubePager();
    var button = event.currentTarget.querySelector("button");
    setBusy(button, true);
    byId("youtubeMessage").textContent = t("searching", "Searching…");
    try {
      await getYouTubeSearch(query, "", serial, function (result) {
        state.youtubeQuality = String((state.youtubeSettings || {}).resolution || result.quality || "1440");
        byId("youtubeSearchQuality").textContent = t("youtube_actual_quality", "Quality") + " · " + youTubeQualityLabel(state.youtubeQuality);
        state.youtubePages = [result];
        state.youtubePage = 1;
        renderYouTubeResults(result.results);
        renderYouTubePager();
        byId("youtubeMessage").textContent = result.pending ? t("searching", "Searching…") :
          result.pagination_warning ? t(result.pagination_warning) : (result.results.length ? "" : t("no_results", "No results found"));
      }, button);
    } catch (error) { if (serial === state.youtubeSearchSerial) { byId("youtubeMessage").textContent = error.message; toast(error.message, true); } }
    finally {
      if (serial === state.youtubeSearchSerial) { state.youtubeSearchBusy = false; setBusy(button, false); }
      if (serial === state.youtubeSearchSerial) { loadYouTubeHistory(); prepareNextYouTubePage(); }
    }
  }

  async function playYouTube(entry, button) {
    setBusy(button, true);
    try {
      var result = await api("/youtube/play", { method: "POST", body: { id: entry.id, title: entry.title, context: (state.youtubePages[state.youtubePage - 1] || {}).context || "" } });
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
      if (data.settings) { renderYouTubeSettings(data.settings); }
      if (!byId("youtubeHistory").hidden) { loadYouTubeHistory(); }
      state.youtubeStatus = data;
      renderYouTubeStatus(data);
    } catch (unusedError) { /* The next status poll retries. */ }
  }

  function renderYouTubeStatus(data) {
    if (data.playing && data.playing.id !== state.youtubePlayingId) {
      state.youtubePlayingId = data.playing.id;
      if (!state.youtubeSearchBusy && !data.autoplay_pending) { byId("youtubeMessage").textContent = ""; }
    }
    if (data.job && data.job.state === "failed") {
      byId("youtubeMessage").textContent = t(data.job.error || "youtube_play_failed", "Video could not be played on this receiver. Try another video or quality setting.")
        + (data.job.error === "youtube_native_player_required" ? " · ServiceApp / ExtEplayer3 (5002)" : "");
      state.youtubeJobToken = "";
    } else if (data.job && data.job.state === "playing") {
      if (!state.youtubeSearchBusy) { byId("youtubeMessage").textContent = ""; }
      state.youtubeJobToken = "";
    }
    if (data.autoplay_pending && !state.youtubeSearchBusy) {
      byId("youtubeMessage").textContent = t("youtube_resolving", "Preparing the video for TV…");
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
        node("small", "", entry.season && entry.episode ? t("season", "Season") + " " + entry.season + " · " + t("episode", "Episode") + " " + entry.episode : entry.kind === "movie" ? t("movies", "Movies") : t("series", "Series")));
      var button = node("button", "video-blue-button", entry.playable ? t("play_on_tv", "Play on TV") : t("choose_episode", "Choose episodes"));
      button.type = "button";
      button.addEventListener("click", function () { selectMedia(entry, button); });
      var actions = node("div", "media-result-actions");
      actions.appendChild(button);
      if (entry.playable) {
        var downloadButton = node("button", "video-blue-button media-download-button");
        downloadButton.type = "button";
        downloadButton.dataset.mediaToken = entry.token;
        append(downloadButton, icon("download"), node("span", "", t("download", "Download")));
        downloadButton.title = entry.downloadable ? t("download", "Download") + " · " + entry.name :
          t("download_unsupported", "This stream is not a downloadable video file.");
        downloadButton.setAttribute("aria-label", t("download", "Download") + " · " + entry.name);
        downloadButton.setAttribute("aria-busy", String(Boolean(state.mediaDownloadPending[entry.token])));
        setBusy(downloadButton, Boolean(state.mediaDownloadPending[entry.token]));
        downloadButton.disabled = !entry.downloadable || Boolean(state.mediaDownloadPending[entry.token]);
        downloadButton.addEventListener("click", function () { downloadMedia(entry, downloadButton); });
        actions.appendChild(downloadButton);
      }
      append(card, artwork, details, actions); return card;
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

  async function downloadMedia(entry, button) {
    if (!entry.downloadable || state.mediaDownloadPending[entry.token]) { return; }
    state.mediaDownloadPending[entry.token] = true;
    setBusy(button, true);
    button.setAttribute("aria-busy", "true");
    try {
      var data = await api("/media/download", { method: "POST", body: { token: entry.token } });
      toast(t("downloads", "Downloads") + " · " + t(data.status || "queued", "Queued") + " · " + entry.name);
      refreshAll(false);
    } catch (error) {
      toast(error.message, true);
      byId("mediaMessage").textContent = error.message;
    } finally {
      delete state.mediaDownloadPending[entry.token];
      setBusy(button, false);
      button.setAttribute("aria-busy", "false");
      // A language change can replace the card while its request is pending.
      document.querySelectorAll(".media-download-button").forEach(function (current) {
        if (current.dataset.mediaToken === entry.token) {
          setBusy(current, false);
          current.setAttribute("aria-busy", "false");
        }
      });
    }
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
    var serial = ++state.subtitleCurrentSerial;
    try {
      var data = await api("/subtitles/current");
      if (serial !== state.subtitleCurrentSerial) { return; }
      var playing = data.playing !== false;
      var context = playing ? (data.search_context || JSON.stringify([
        data.title, data.year, data.content_type, data.season, data.episode])) : null;
      var changed = context !== state.subtitleContext;
      if (changed) {
        state.subtitleSearchSerial += 1;
        state.subtitleApplySerial += 1;
        clearSubtitleList();
        state.subtitleContext = context;
        state.subtitleFormDirty = false;
      }
      byId("subtitleCurrent").textContent = playing ? (data.title +
        (data.content_type === "series" ? " · " + t("season", "Season") + " " + data.season + " · " + t("episode", "Episode") + " " + data.episode :
          (data.year ? " (" + data.year + ")" : ""))) : t("vod_not_playing", "Play a movie or episode on the receiver first.");
      if (changed || !byId("subtitleTitle").value) {
        byId("subtitleTitle").value = playing ? data.title : "";
      }
      var independentDisabled = data.independent_enabled === false;
      state.subtitleCanSearch = playing && !independentDisabled;
      byId("subtitleSearchButton").disabled = !state.subtitleCanSearch
        || Boolean(state.subtitleSearchBusySerial || state.subtitleApplyBusySerial);
      byId("subtitleSearchHint").textContent = playing && independentDisabled ?
        t("independent_subtitles_disabled", "Independent subtitles are turned off in settings.") :
        (playing && !data.can_load ? t("subtitle_load_failed", "The receiver could not load this subtitle.") : "");
      renderSubtitleSync(data);
      if (!playing || independentDisabled) {
        clearSubtitleList();
      } else {
        if (!state.subtitleFormDirty && data.cached_results && data.cached_results.context === context) {
          state.subtitleResults = data.cached_results;
          byId("subtitleLanguage").value = data.cached_results.language;
          byId("subtitleTitle").value = data.cached_results.title;
          var selectedId = data.cached_results.selected_id || state.subtitleSelectedId;
          state.subtitleSelectedId = (data.cached_results.items || []).some(function (item) {
            return item.id === selectedId;
          }) ? selectedId : null;
        } else if (Object.prototype.hasOwnProperty.call(data, "cached_results")) {
          clearSubtitleList();
        }
        if (state.subtitleResults && state.subtitleResults.context === context) {
          state.subtitleResults.can_load = data.can_load;
          renderSubtitleResults(state.subtitleResults);
          if (state.subtitleRestoreScroll) {
            state.subtitleRestoreScroll = false;
            var restoreContext = context;
            window.setTimeout(function () {
              if (state.page === "subtitles" && state.subtitleContext === restoreContext
                  && typeof window.scrollTo === "function") {
                window.scrollTo(0, state.subtitleScroll);
              }
            }, 0);
          }
        }
      }
    } catch (error) {
      if (serial !== state.subtitleCurrentSerial) { return; }
      state.subtitleCanSearch = false;
      byId("subtitleSearchButton").disabled = true;
      byId("subtitleCurrent").textContent = t("receiver_unavailable", "Receiver unavailable");
      byId("subtitleSyncPanel").hidden = true;
      if (state.subtitleResults) {
        state.subtitleResults.can_load = false;
        renderSubtitleResults(state.subtitleResults);
      }
    }
  }

  function clearSubtitleList() {
    state.subtitleResults = null;
    state.subtitleSelectedId = null;
    state.subtitleScroll = 0;
    byId("subtitleResults").replaceChildren();
    byId("subtitleProviderStatus").replaceChildren();
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
    var revision = available ? (data.sync.revision || null) : null;
    if (revision !== state.subtitleSyncRevision) {
      state.subtitleSyncRevision = revision;
      state.subtitlePositionSerial += 1;
      state.cueQuerySerial += 1;
      byId("subtitleMovieTime").value = "";
      byId("subtitleFileTime").value = "";
      byId("subtitleCueQuery").value = "";
      byId("subtitleCueResults").replaceChildren();
    }
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
    var serial = ++state.subtitlePositionSerial;
    var revision = state.subtitleSyncRevision;
    var context = state.subtitleContext;
    try {
      var data = await api("/subtitles/current");
      if (serial !== state.subtitlePositionSerial || revision !== state.subtitleSyncRevision
          || context !== state.subtitleContext) { return; }
      if (!data.sync || data.sync.revision !== revision || !data.sync.available
          || (data.search_context && data.search_context !== context)) {
        await loadSubtitleCurrent();
        return;
      }
      renderSubtitleSync(data);
      var position = formatSubtitleTime(data.position_ms);
      if (!position) { throw new Error(t("subtitle_sync_position_missing", "Movie position is unavailable.")); }
      byId("subtitleMovieTime").value = position;
      await findSubtitleCues();
    } catch (error) {
      if (serial === state.subtitlePositionSerial) { toast(error.message, true); }
    }
  }

  async function findSubtitleCues() {
    var serial = ++state.cueQuerySerial;
    var revision = state.subtitleSyncRevision;
    if (byId("subtitleSyncPanel").hidden) { return; }
    try {
      var result = await api("/subtitles/cues?q=" + encodeURIComponent(byId("subtitleCueQuery").value.trim())
        + "&revision=" + encodeURIComponent(revision || ""));
      if (serial !== state.cueQuerySerial || byId("subtitleSyncPanel").hidden
          || revision !== state.subtitleSyncRevision || result.revision !== revision) { return; }
      var entries = (result.items || []).map(function (item) {
        var button = node("button", "subtitle-cue-button", item.time + " · " + item.text);
        button.type = "button";
        button.addEventListener("click", function () {
          if (revision !== state.subtitleSyncRevision) { return; }
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
    var revision = state.subtitleSyncRevision;
    setBusy(button, true);
    try {
      await api("/subtitles/sync", { method: "POST", body: {
        film_time: byId("subtitleMovieTime").value.trim(),
        subtitle_time: byId("subtitleFileTime").value.trim(),
        revision: revision
      } });
      if (revision !== state.subtitleSyncRevision) { return; }
      await loadSubtitleCurrent();
      if (revision === state.subtitleSyncRevision) { toast(t("subtitle_sync_saved", "Subtitle timing adjusted")); }
    } catch (error) {
      if (revision === state.subtitleSyncRevision) { toast(error.message, true); }
    } finally {
      setBusy(button, false);
    }
  }

  async function adjustSubtitleSync(payload, button) {
    var revision = state.subtitleSyncRevision;
    setBusy(button, true);
    try {
      await api("/subtitles/sync", { method: "POST", body: Object.assign({}, payload,
        { revision: revision }) });
      if (revision !== state.subtitleSyncRevision) { return; }
      await loadSubtitleCurrent();
      if (revision === state.subtitleSyncRevision) { toast(t("subtitle_sync_saved", "Subtitle timing adjusted")); }
    } catch (error) {
      if (revision === state.subtitleSyncRevision) { toast(error.message, true); }
    } finally {
      setBusy(button, false);
    }
  }

  async function searchSubtitles(event) {
    event.preventDefault();
    var button = byId("subtitleSearchButton");
    setBusy(button, true);
    byId("subtitleSearchHint").textContent = t("subtitle_searching", "Searching providers…");
    var serial = ++state.subtitleSearchSerial;
    state.subtitleSearchBusySerial = serial;
    var context = state.subtitleContext;
    var query = { language: byId("subtitleLanguage").value,
      title: byId("subtitleTitle").value, refresh: true };
    try {
      var data = await api("/subtitles/search", { method: "POST",
        body: query });
      if (serial !== state.subtitleSearchSerial || context !== state.subtitleContext
          || query.language !== byId("subtitleLanguage").value
          || query.title !== byId("subtitleTitle").value) { return; }
      data.context = data.context || state.subtitleContext;
      state.subtitleResults = data;
      state.subtitleFormDirty = false;
      state.subtitleSelectedId = null;
      state.subtitleScroll = 0;
      await loadSubtitleCurrent();
    } catch (error) {
      if (serial === state.subtitleSearchSerial) { toast(error.message, true); }
    } finally {
      if (state.subtitleSearchBusySerial === serial) {
        state.subtitleSearchBusySerial = 0;
        byId("subtitleSearchHint").textContent = "";
        setBusy(button, false);
        button.disabled = !state.subtitleCanSearch || Boolean(state.subtitleApplyBusySerial);
      }
    }
  }

  function renderSubtitleResults(data) {
    var names = { subdl: "SubDL", opensubtitles: "OpenSubtitles.com", subsource: "SubSource",
      napiprojekt: "NapiProjekt", napisy24: "Napisy24" };
    var compatibility = {
      high: ["subtitleCompatibilityHigh", "High compatibility"],
      possible: ["subtitleCompatibilityPossible", "Possible compatibility"],
      fps_convert: ["subtitleCompatibilityConverted", "FPS will be converted"],
      unknown: ["subtitleCompatibilityUnknown", "Compatibility unknown"],
      different: ["subtitleCompatibilityDifferent", "Different version"]
    };
    var status = (data.providers || []).map(function (entry) {
      return node("span", "", (names[entry.provider] || entry.provider || "") + " · " +
        (entry.status === "ok" ? String(entry.count) : t(entry.status, t("provider_unavailable", "Source unavailable"))));
    });
    if (!status.length) {
      status.push(node("span", "", t("subtitle_no_providers", "Enable a subtitle provider in Settings.")));
    }
    byId("subtitleProviderStatus").replaceChildren.apply(byId("subtitleProviderStatus"), status);
    var items = (data.items || []).map(function (item) {
      var row = node("article", "subtitle-result");
      row.classList.toggle("is-selected", item.id === state.subtitleSelectedId);
      var detail = node("div");
      append(detail, node("strong", "", item.release || item.title),
        node("small", "", (names[item.provider] || item.provider) + " · " + item.language));
      var metadata = [];
      var fps = Number(item.subtitle_fps || item.fps || 0);
      if (Number.isFinite(fps) && fps > 0) {
        var fpsText = String(Math.round(fps * 1000) / 1000);
        var targetFps = Number(item.video_fps || 0);
        if (item.compatibility_status === "fps_convert" && Number.isFinite(targetFps) && targetFps > 0) {
          fpsText += " > " + String(Math.round(targetFps * 1000) / 1000);
        }
        metadata.push(fpsText + " " + t("subtitleFps", "FPS"));
      }
      var label = compatibility[item.compatibility_status];
      if (label) { metadata.push(t(label[0], label[1])); }
      if (item.hearing_impaired) { metadata.push(t("subtitleHearingImpaired", "Hearing-impaired subtitles")); }
      if (metadata.length) { detail.appendChild(node("small", "subtitle-result-metadata", metadata.join(" · "))); }
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
    var serial = ++state.subtitleApplySerial;
    var context = state.subtitleContext;
    state.subtitleApplyBusySerial = serial;
    byId("subtitleLanguage").disabled = true;
    byId("subtitleTitle").disabled = true;
    byId("subtitleSearchButton").disabled = true;
    setBusy(button, true);
    try {
      await api("/subtitles/apply", { method: "POST", body: { id: id } });
      if (serial !== state.subtitleApplySerial || context !== state.subtitleContext) { return; }
      state.subtitleSelectedId = id;
      state.subtitleScroll = Number(window.scrollY || window.pageYOffset || 0);
      toast(t("subtitle_loaded", "Subtitle loaded on the receiver"));
      byId("subtitleMovieTime").value = "";
      byId("subtitleFileTime").value = "";
      byId("subtitleCueQuery").value = "";
      await loadSubtitleCurrent();
      findSubtitleCues();
    } catch (error) {
      if (serial === state.subtitleApplySerial) { toast(error.message, true); }
    } finally {
      setBusy(button, false);
      if (state.subtitleApplyBusySerial === serial) {
        state.subtitleApplyBusySerial = 0;
        byId("subtitleLanguage").disabled = false;
        byId("subtitleTitle").disabled = false;
        byId("subtitleSearchButton").disabled = !state.subtitleCanSearch || Boolean(state.subtitleSearchBusySerial);
      }
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
    byId("audioCurrentTitle").textContent = data.playing ? (data.title || t("movie", "Movie")) :
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
      var label = node("strong", "", audioTrackLabel(track.name));
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
    state.subtitleRestoreScroll = page === "subtitles" && state.page !== "subtitles";
    if (state.page === "subtitles" && page !== "subtitles") {
      state.subtitleScroll = Number(window.scrollY || window.pageYOffset || 0);
    }
    if (state.page === "youtube" && page !== "youtube"
        && (state.youtubeSearchToken || state.youtubeSearchBusy || state.youtubePagingBusy || state.youtubePrefetch)) {
      cancelYouTubeSearch();
      ++state.youtubeSearchSerial;
      state.youtubeSearchBusy = state.youtubePagingBusy = false;
      state.youtubePages = [];
      setBusy(byId("youtubeSearchForm").querySelector("button"), false);
    }
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
    setMenuOpen(false);
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
      loadYouTubeHistory();
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
    byId("youtubeHistoryToggle").addEventListener("click", function () {
      var panel = byId("youtubeHistory");
      panel.hidden = !panel.hidden;
      byId("youtubeHistoryToggle").setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) { loadYouTubeHistory(); }
    });
    byId("youtubeHistoryClear").addEventListener("click", function (event) { deleteYouTubeHistory(null, event.currentTarget); });
    byId("mediaSearchForm").addEventListener("submit", searchMedia);
    byId("tmdbSave").addEventListener("click", function () {
      if (byId("tmdbKey").value.trim()) { saveExternalSettings("tmdb", { key: byId("tmdbKey").value.trim() }, byId("tmdbSave")); }
    });
    byId("tmdbRemove").addEventListener("click", function () { saveExternalSettings("tmdb", { remove: true }, byId("tmdbRemove")); });
    byId("tmdbTest").addEventListener("click", testTMDb);
    byId("youtubeSettingsSave").addEventListener("click", function () {
      var payload = { resolution: byId("youtubeResolution").value, autoplay: byId("youtubeAutoplay").value === "on" };
      saveExternalSettings("youtube", payload, byId("youtubeSettingsSave"));
    });
    byId("subtitleSearchForm").addEventListener("submit", searchSubtitles);
    byId("subtitleLanguage").addEventListener("change", function () {
      state.subtitleSearchSerial += 1;
      state.subtitleFormDirty = true;
      clearSubtitleList();
    });
    byId("subtitleTitle").addEventListener("input", function () {
      state.subtitleSearchSerial += 1;
      state.subtitleFormDirty = true;
      clearSubtitleList();
    });
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
    byId("pairCode").addEventListener("input", formatPairCodeInput);
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
    byId("menuButton").addEventListener("click", function () {
      setMenuOpen(!byId("sidebar").classList.contains("open"));
    });
    byId("closeMenuButton").addEventListener("click", function () { setMenuOpen(false); });
    byId("menuBackdrop").addEventListener("click", function () { setMenuOpen(false); });
    window.addEventListener("resize", function () {
      var sidebar = byId("sidebar");
      var activeInSidebar = sidebar.contains(document.activeElement);
      setMenuOpen(sidebar.classList.contains("open"));
      if (mobileMenuLayout() && !sidebar.classList.contains("open") && activeInSidebar) {
        byId("menuButton").focus();
      }
    });
    setMenuOpen(false, false);
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
    byId("autoTestResults").addEventListener("click", function (event) {
      var button = event.target.closest(".test-result-delete");
      if (button) { deleteAutomaticTestSource(button); }
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
        event.preventDefault();
      } else if (byId("sourceModal").hidden) {
        handleMenuKeyboard(event);
      }
    });
  }

  bindEvents();
  bootstrap();
}());
