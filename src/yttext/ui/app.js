(() => {
  "use strict";

  const OTHER_SUMMARY_LANGUAGE = "__other__";
  const BCP47_LANGUAGE_PATTERN = /^(?=.{2,35}$)[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/;

  const elements = {
    form: document.getElementById("extractForm"),
    executeFormTab: document.getElementById("executeFormTab"),
    settingsFormTab: document.getElementById("settingsFormTab"),
    executeFormPanel: document.getElementById("executeFormPanel"),
    settingsFormPanel: document.getElementById("settingsFormPanel"),
    videoUrl: document.getElementById("videoUrl"),
    urlError: document.getElementById("urlError"),
    transcriptFormat: document.getElementById("transcriptFormat"),
    summary: document.getElementById("summaryToggle"),
    summaryOptions: document.getElementById("summaryOptions"),
    summaryLanguage: document.getElementById("summaryLanguage"),
    customSummaryLanguageBlock: document.getElementById("customSummaryLanguageBlock"),
    customSummaryLanguage: document.getElementById("customSummaryLanguage"),
    customSummaryLanguageError: document.getElementById("customSummaryLanguageError"),
    cookieBrowserBlock: document.getElementById("cookieBrowserBlock"),
    cookieBrowser: document.getElementById("cookieBrowser"),
    apiKey: document.getElementById("apiKey"),
    apiKeyLabel: document.getElementById("apiKeyLabel"),
    apiKeyNote: document.getElementById("apiKeyNote"),
    apiKeyError: document.getElementById("apiKeyError"),
    apiKeySource: document.getElementById("apiKeySource"),
    apiKeySourceLabel: document.getElementById("apiKeySourceLabel"),
    apiKeySourceTitle: document.getElementById("apiKeySourceTitle"),
    apiKeySourceDescription: document.getElementById("apiKeySourceDescription"),
    apiKeyVisibilityButton: document.getElementById("apiKeyVisibilityButton"),
    clearApiKeyButton: document.getElementById("clearApiKeyButton"),
    geminiModel: document.getElementById("geminiModel"),
    geminiModelSource: document.getElementById("geminiModelSource"),
    extractButton: document.getElementById("extractButton"),
    extractButtonLabel: document.querySelector("#extractButton .button-label"),
    runSummary: document.getElementById("runSummary"),
    submitHelp: document.getElementById("submitHelp"),
    apiStatus: document.getElementById("apiStatus"),
    versionLabel: document.getElementById("versionLabel"),
    progressPanel: document.getElementById("progressPanel"),
    progressMessage: document.getElementById("progressMessage"),
    progressPercent: document.getElementById("progressPercent"),
    progressBar: document.getElementById("progressBar"),
    errorPanel: document.getElementById("errorPanel"),
    errorMessage: document.getElementById("errorMessage"),
    errorHint: document.getElementById("errorHint"),
    resultPanel: document.getElementById("resultPanel"),
    resultTitle: document.getElementById("resultTitle"),
    resultMeta: document.getElementById("resultMeta"),
    warningBanner: document.getElementById("warningBanner"),
    summaryLimitPanel: document.getElementById("summaryLimitPanel"),
    summaryLimitHeading: document.getElementById("summaryLimitHeading"),
    summaryLimitMessage: document.getElementById("summaryLimitMessage"),
    summaryLimitNote: document.getElementById("summaryLimitNote"),
    truncateSummaryButton: document.getElementById("truncateSummaryButton"),
    transcriptTab: document.getElementById("transcriptTab"),
    transcriptFormatBadge: document.getElementById("transcriptFormatBadge"),
    summaryActionGroup: document.getElementById("summaryActionGroup"),
    summaryTab: document.getElementById("summaryTab"),
    markdownOutput: document.getElementById("markdownOutput"),
    copyTranscriptButton: document.getElementById("copyTranscriptButton"),
    saveTranscriptButton: document.getElementById("saveTranscriptButton"),
    copySummaryButton: document.getElementById("copySummaryButton"),
    saveSummaryButton: document.getElementById("saveSummaryButton"),
    combinedResultActions: document.getElementById("combinedResultActions"),
    saveBothButton: document.getElementById("saveBothButton"),
    openVideoButton: document.getElementById("openVideoButton"),
    toastRegion: document.getElementById("toastRegion"),
  };

  const state = {
    ready: false,
    busy: false,
    appInfo: {
      gemini_model: "gemini-flash-lite-latest",
      summary_language: "auto",
      summary_languages: [{ code: "auto", label: "Same as transcript" }],
      capabilities: { byok: true, server_api_key: false, browser_cookies: false },
    },
    result: null,
    summaryJob: null,
    activeFormTab: "execute",
    activeTab: "transcript",
  };

  window.App = {
    hasServerApiKey() {
      return Boolean(state.appInfo.capabilities?.server_api_key);
    },
    onProgress(payload) {
      const percent = Math.max(0, Math.min(100, Number(payload.percent) || 0));
      elements.progressMessage.textContent = payload.message || "Processing…";
      elements.progressPercent.textContent = `${percent}%`;
      elements.progressBar.style.width = `${percent}%`;
    },
  };

  elements.form.addEventListener("submit", handleSubmit);
  elements.summary.addEventListener("change", syncSummaryOptions);
  elements.transcriptFormat.addEventListener("change", updateExecutionSummary);
  elements.summaryLanguage.addEventListener("change", () => {
    syncSummaryLanguageInput();
    updateExecutionSummary();
  });
  elements.customSummaryLanguage.addEventListener("input", () => {
    clearSummaryLanguageError();
    updateExecutionSummary();
  });
  elements.cookieBrowser.addEventListener("change", updateExecutionSummary);
  elements.videoUrl.addEventListener("input", () => {
    clearUrlError();
    syncActionState();
  });
  elements.apiKey.addEventListener("input", () => {
    clearApiKeyError();
    renderApiKeyStatus();
  });
  elements.apiKeyVisibilityButton.addEventListener("click", toggleApiKeyVisibility);
  elements.clearApiKeyButton.addEventListener("click", clearApiKey);
  elements.copyTranscriptButton.addEventListener("click", () => copyResult("transcript"));
  elements.saveTranscriptButton.addEventListener("click", () =>
    saveResult("transcript", elements.saveTranscriptButton));
  elements.copySummaryButton.addEventListener("click", () => copyResult("summary"));
  elements.saveSummaryButton.addEventListener("click", () =>
    saveResult("summary", elements.saveSummaryButton));
  elements.saveBothButton.addEventListener("click", saveBothResults);
  elements.openVideoButton.addEventListener("click", openCurrentVideo);
  document.querySelectorAll("[data-summary-mode]").forEach((button) => {
    button.addEventListener("click", () => resolveLongSummary(button.dataset.summaryMode));
  });
  document.querySelectorAll("[data-form-tab]").forEach((button) => {
    button.addEventListener("click", () => switchFormTab(button.dataset.formTab));
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  switchFormTab("execute");
  syncSummaryOptions();
  updateExecutionSummary();
  syncActionState();
  initializeApi();
  window.addEventListener("pagehide", () => {
    discardPendingSummary();
    clearApiKey({ focus: false });
  });

  async function initializeApi() {
    try {
      state.appInfo = await requestJson("/api/info");
      state.ready = true;
      renderSummaryLanguages();
      elements.geminiModel.value = state.appInfo.gemini_model || "gemini-flash-lite-latest";
      elements.versionLabel.textContent = `v${state.appInfo.version || ""}`;
      elements.apiStatus.className = "status-pill status-ready";
      elements.apiStatus.innerHTML = '<span class="status-dot"></span><span>Ready</span>';
      renderWebConfiguration();
      updateExecutionSummary();
      syncActionState();
      elements.videoUrl.focus();
    } catch (error) {
      elements.apiStatus.className = "status-pill status-error";
      elements.apiStatus.innerHTML =
        '<span class="status-dot"></span><span>Connection error</span>';
      elements.submitHelp.textContent = "Reload this page.";
      showError({
        message: "Could not connect to the application server.",
        hint: error && error.message ? error.message : String(error),
      });
    }
  }

  function syncSummaryOptions() {
    const enabled = elements.summary.checked;
    elements.summaryOptions.classList.toggle("hidden", !enabled);
    elements.summaryOptions.setAttribute("aria-hidden", String(!enabled));
    elements.summary.setAttribute("aria-expanded", String(enabled));
    if (!enabled) {
      clearApiKeyError();
      clearSummaryLanguageError();
    }
    syncSummaryLanguageInput();
    updateExecutionSummary();
    syncActionState();
  }

  function renderSummaryLanguages() {
    const currentLanguage = String(state.appInfo.summary_language || "auto");
    const configuredLanguages = Array.isArray(state.appInfo.summary_languages)
      ? state.appInfo.summary_languages
      : [];
    const languages = configuredLanguages.length
      ? configuredLanguages
      : [{ code: "auto", label: "Same as transcript" }];

    elements.summaryLanguage.replaceChildren(
      ...languages.map((language) => {
        const option = document.createElement("option");
        option.value = String(language.code || "");
        option.textContent = String(language.label || language.code || "");
        return option;
      }),
    );
    const otherOption = document.createElement("option");
    otherOption.value = OTHER_SUMMARY_LANGUAGE;
    otherOption.textContent = "Other…";
    elements.summaryLanguage.appendChild(otherOption);

    const matchingOption = Array.from(elements.summaryLanguage.options).find(
      (option) => option.value.toLowerCase() === currentLanguage.toLowerCase(),
    );
    if (matchingOption && matchingOption.value !== OTHER_SUMMARY_LANGUAGE) {
      elements.summaryLanguage.value = matchingOption.value;
    } else if (currentLanguage && currentLanguage !== "auto") {
      elements.summaryLanguage.value = OTHER_SUMMARY_LANGUAGE;
      elements.customSummaryLanguage.value = currentLanguage;
    } else {
      elements.summaryLanguage.value = "auto";
    }
    syncSummaryLanguageInput();
  }

  function syncSummaryLanguageInput() {
    const custom = elements.summaryLanguage.value === OTHER_SUMMARY_LANGUAGE;
    elements.customSummaryLanguageBlock.classList.toggle("hidden", !custom);
    elements.customSummaryLanguageBlock.setAttribute("aria-hidden", String(!custom));
    elements.customSummaryLanguage.disabled = state.busy || !elements.summary.checked || !custom;
    if (!custom) clearSummaryLanguageError();
  }

  function selectedSummaryLanguage() {
    if (elements.summaryLanguage.value === OTHER_SUMMARY_LANGUAGE) {
      return elements.customSummaryLanguage.value.trim();
    }
    return elements.summaryLanguage.value || "auto";
  }

  function selectedSummaryLanguageLabel() {
    if (elements.summaryLanguage.value === OTHER_SUMMARY_LANGUAGE) {
      return elements.customSummaryLanguage.value.trim() || "Other language";
    }
    const selected = elements.summaryLanguage.options[elements.summaryLanguage.selectedIndex];
    return selected ? selected.text : "Same as transcript";
  }

  function renderWebConfiguration() {
    const model = state.appInfo.gemini_model || "gemini-flash-lite-latest";
    const browserCookies = Boolean(state.appInfo.capabilities?.browser_cookies);
    elements.cookieBrowserBlock.classList.toggle("hidden", !browserCookies);
    elements.cookieBrowser.disabled = !browserCookies;
    if (!browserCookies) elements.cookieBrowser.value = "";
    renderApiKeyStatus();
    elements.geminiModelSource.textContent = `Current setting: app default = ${model}`;
  }

  function renderApiKeyStatus() {
    const serverApiKey = window.App.hasServerApiKey();
    const enteredApiKey = Boolean(elements.apiKey.value.trim());
    const source = enteredApiKey ? "entered" : serverApiKey ? "environment" : "missing";
    const stateSignature = `${source}:${serverApiKey}`;
    if (elements.apiKeySource.dataset.state === stateSignature) return;
    elements.apiKeySource.dataset.state = stateSignature;

    if (source === "entered") {
      elements.apiKeySource.className = "config-source config-source-entered";
      elements.apiKeySourceLabel.textContent = "API key in use";
      elements.apiKeySourceTitle.textContent = "Using the key entered in this tab";
      elements.apiKeySourceDescription.textContent = serverApiKey
        ? "This key overrides the local environment key for Gemini requests. It is never saved."
        : "This key will be used only for Gemini requests. It is never saved.";
    } else if (source === "environment") {
      elements.apiKeySource.className = "config-source config-source-ready";
      elements.apiKeySourceLabel.textContent = "API key in use";
      elements.apiKeySourceTitle.textContent =
        "Using environment variable GEMINI_API_KEY";
      elements.apiKeySourceDescription.textContent =
        "The key stays on this local server. Enter another key below to override it in this tab.";
    } else {
      elements.apiKeySource.className = "config-source config-source-missing";
      elements.apiKeySourceLabel.textContent = "API key needed";
      elements.apiKeySourceTitle.textContent = "Gemini API key is not configured";
      elements.apiKeySourceDescription.textContent =
        "Enter a key below to create summaries and load available models.";
    }

    if (serverApiKey) {
      elements.apiKeyLabel.textContent = "Override with another Gemini API key (optional)";
      elements.apiKey.placeholder = "Enter another API key only if needed";
      elements.apiKeyNote.textContent =
        "Leave this blank to use the environment key. Entered values are never saved.";
    } else {
      elements.apiKeyLabel.textContent = "Gemini API key (required for summarization)";
      elements.apiKey.placeholder = "Enter a Gemini API key";
      elements.apiKeyNote.textContent =
        "The entered value stays only in this tab until you clear it, submit a summary, reload, or close the tab.";
    }
  }

  function updateExecutionSummary() {
    const format = elements.transcriptFormat.options[elements.transcriptFormat.selectedIndex].text;
    const parts = [
      `Transcript: ${format}`,
      "Captions: original language",
    ];
    if (elements.summary.checked) {
      parts.push(`Summary: ${selectedSummaryLanguageLabel()}`);
    } else {
      parts.push("Summary: off");
    }
    if (elements.cookieBrowser.value) {
      const browser = elements.cookieBrowser.options[elements.cookieBrowser.selectedIndex].text;
      parts.push(`Cookies: ${browser}`);
    }
    elements.runSummary.textContent = parts.join(" / ");
  }

  function syncActionState() {
    const hasUrl = Boolean(elements.videoUrl.value.trim());
    elements.extractButton.disabled = state.busy || !state.ready || !hasUrl;

    if (!state.ready) {
      elements.submitHelp.textContent = "Preparing the app.";
    } else if (!hasUrl) {
      elements.submitHelp.textContent = "Enter a YouTube video to get started.";
    } else if (elements.summary.checked) {
      elements.submitHelp.textContent = "A Gemini summary will be created after the transcript.";
    } else {
      elements.submitHelp.textContent = "Only the transcript will be created.";
    }

    if (!state.busy) {
      elements.extractButtonLabel.textContent = elements.summary.checked
        ? "Create transcript and summary"
        : "Create transcript";
    }
  }

  function toggleApiKeyVisibility() {
    const reveal = elements.apiKey.type === "password";
    elements.apiKey.type = reveal ? "text" : "password";
    elements.apiKeyVisibilityButton.textContent = reveal ? "Hide" : "Show";
    elements.apiKeyVisibilityButton.setAttribute("aria-pressed", String(reveal));
    elements.apiKey.focus();
  }

  function clearApiKey({ focus = true } = {}) {
    elements.apiKey.value = "";
    elements.apiKey.type = "password";
    elements.apiKeyVisibilityButton.textContent = "Show";
    elements.apiKeyVisibilityButton.setAttribute("aria-pressed", "false");
    clearApiKeyError();
    renderApiKeyStatus();
    if (focus) elements.apiKey.focus();
  }

  function switchFormTab(tab) {
    if (!["execute", "settings"].includes(tab) || state.busy) {
      return;
    }
    state.activeFormTab = tab;
    document.querySelectorAll("[data-form-tab]").forEach((button) => {
      const active = button.dataset.formTab === tab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    elements.executeFormPanel.classList.toggle("hidden", tab !== "execute");
    elements.settingsFormPanel.classList.toggle("hidden", tab !== "settings");
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!state.ready || state.busy) {
      return;
    }

    const url = elements.videoUrl.value.trim();
    if (!url) {
      switchFormTab("execute");
      setUrlError("Enter a YouTube URL or video ID.");
      return;
    }
    if (!validateSummaryLanguage()) {
      switchFormTab("settings");
      elements.customSummaryLanguage.focus();
      return;
    }
    clearApiKeyError();
    if (
      elements.summary.checked &&
      !elements.apiKey.value.trim() &&
      !window.App.hasServerApiKey()
    ) {
      setApiKeyError("Enter your Gemini API key to create a summary.");
      switchFormTab("settings");
      elements.apiKey.focus();
      return;
    }
    if (elements.summary.checked && !elements.geminiModel.value.trim()) {
      switchFormTab("settings");
      elements.geminiModel.focus();
      showToast("Enter the Gemini model ID to use for summarization.", "error");
      return;
    }

    setBusy(true);
    hideError();
    elements.resultPanel.classList.add("hidden");
    elements.progressPanel.classList.remove("hidden");
    window.App.onProgress({ percent: 2, message: "Starting…" });

    const payload = {
      url,
      transcript_format: elements.transcriptFormat.value,
      prepare_summary: elements.summary.checked,
      cookie_browser: elements.cookieBrowser.value || null,
    };

    try {
      await discardPendingSummary();
      const response = await requestJson("/api/extract", { method: "POST", body: payload });
      state.summaryJob = response.summary_job || null;
      renderResult(response.result);
      if (state.summaryJob && !state.summaryJob.requires_long_summary_choice) {
        window.App.onProgress({ percent: 72, message: "Generating the summary…" });
        await requestSummary("full");
      } else if (state.summaryJob) {
        showToast("Transcript ready. Choose how to summarize the long captions.", "success");
      } else {
        showToast("Transcript complete.", "success");
      }
    } catch (error) {
      showError(apiErrorFrom(error));
    } finally {
      elements.progressPanel.classList.add("hidden");
      setBusy(false);
    }
  }

  function setBusy(busy) {
    state.busy = busy;
    elements.extractButton.classList.toggle("is-busy", busy);
    if (busy) {
      elements.extractButtonLabel.textContent = "Processing…";
      elements.apiKey.type = "password";
      elements.apiKeyVisibilityButton.textContent = "Show";
      elements.apiKeyVisibilityButton.setAttribute("aria-pressed", "false");
    }

    [
      elements.executeFormTab,
      elements.settingsFormTab,
      elements.videoUrl,
      elements.transcriptFormat,
      elements.summary,
      elements.summaryLanguage,
      elements.customSummaryLanguage,
      elements.cookieBrowser,
      elements.apiKey,
      elements.apiKeyVisibilityButton,
      elements.clearApiKeyButton,
      elements.geminiModel,
    ].forEach((control) => {
      control.disabled = busy;
    });
    syncSummaryLanguageInput();
    document.querySelectorAll("[data-summary-mode]").forEach((button) => {
      button.disabled = busy;
    });
    syncActionState();
  }

  function renderResult(result) {
    state.result = result;
    state.activeTab = "transcript";
    elements.resultTitle.textContent = result.video_title || result.video_id;

    const duration = formatDuration(result.duration);
    const size = String(result.language || "").toLowerCase().startsWith("ja")
      ? `${Number(result.character_count || 0).toLocaleString()} characters`
      : `${Number(result.word_count || 0).toLocaleString()} words`;
    elements.resultMeta.textContent = `${duration} ・ ${result.caption_label} ・ ${size}`;

    elements.warningBanner.classList.toggle("hidden", !result.warning);
    elements.warningBanner.textContent = result.warning || "";
    const hasSummary = Boolean(result.summary);
    elements.summaryActionGroup.classList.toggle("hidden", !hasSummary);
    elements.combinedResultActions.classList.toggle("hidden", !hasSummary);
    elements.transcriptFormatBadge.textContent = String(result.transcript.format || "md")
      .toUpperCase();

    const summaryLimit = result.summary_limit;
    const summaryFailed = Boolean(
      state.summaryJob &&
      !result.summary &&
      String(result.warning || "").includes("summarization failed"),
    );
    const needsSummaryChoice = Boolean(
      (summaryLimit && summaryLimit.requires_confirmation) || summaryFailed,
    );
    elements.summaryLimitPanel.classList.toggle("hidden", !needsSummaryChoice);
    if (needsSummaryChoice) {
      const isLong = Number(result.character_count || 0) > Number(
        summaryLimit?.limit_characters || 50_000,
      );
      elements.truncateSummaryButton.classList.toggle("hidden", !isLong);
      if (isLong) {
        const source = Number(summaryLimit.source_characters || 0).toLocaleString();
        const limit = Number(summaryLimit.limit_characters || 0).toLocaleString();
        elements.summaryLimitHeading.textContent = "The captions exceed the default summary limit";
        elements.summaryLimitMessage.textContent =
          `Caption length: [${source} / ${limit} characters]. Choose how Gemini should summarize it.`;
        elements.summaryLimitNote.textContent =
          "Sending the full transcript may exceed the selected model's context limit or fail.";
      } else {
        elements.summaryLimitHeading.textContent = "The summary could not be completed";
        elements.summaryLimitMessage.textContent =
          "Review the warning, enter your API key again, and retry the summary.";
        elements.summaryLimitNote.textContent =
          "The transcript remains available, and the pending job expires automatically.";
      }
    }
    switchTab("transcript");
    elements.resultPanel.classList.remove("hidden");
    elements.resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function switchTab(tab) {
    if (!state.result || (tab === "summary" && !state.result.summary)) {
      return;
    }
    state.activeTab = tab;
    document.querySelectorAll("[data-tab]").forEach((button) => {
      const active = button.dataset.tab === tab;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const artifact = tab === "summary" ? state.result.summary : state.result.transcript;
    const content = artifact ? artifact.content : "";
    const isMarkdown = artifact && artifact.format === "md";
    elements.markdownOutput.classList.toggle("plain-output", !isMarkdown);
    if (isMarkdown) {
      elements.markdownOutput.innerHTML = window.YttextMarkdown.render(content);
    } else {
      elements.markdownOutput.textContent = content;
    }
    elements.markdownOutput.setAttribute(
      "aria-label",
      tab === "summary" ? "Summary preview" : "Transcript preview",
    );
  }

  async function resolveLongSummary(mode) {
    if (
      !state.result ||
      !state.summaryJob ||
      state.busy ||
      !["truncate", "full", "skip"].includes(mode)
    ) return;
    if (mode !== "skip" && !validateSummaryLanguage()) {
      switchFormTab("settings");
      elements.customSummaryLanguage.focus();
      showToast("Enter a valid summary language tag.", "error");
      return;
    }
    if (
      mode !== "skip" &&
      !elements.apiKey.value.trim() &&
      !window.App.hasServerApiKey()
    ) {
      setApiKeyError("Enter your Gemini API key to create a summary.");
      switchFormTab("settings");
      elements.apiKey.focus();
      return;
    }
    setBusy(true);
    hideError();
    elements.progressPanel.classList.remove("hidden");
    window.App.onProgress({ percent: 72, message: "Applying summary choice…" });
    try {
      await requestSummary(mode);
    } catch (error) {
      showError(apiErrorFrom(error));
    } finally {
      elements.progressPanel.classList.add("hidden");
      setBusy(false);
    }
  }

  async function requestSummary(mode) {
    if (!state.summaryJob) return;
    const headers = {};
    const apiKey = elements.apiKey.value.trim();
    if (mode !== "skip" && apiKey) headers["X-Gemini-Api-Key"] = apiKey;
    const pendingRequest = requestJson("/api/summarize", {
      method: "POST",
      headers,
      body: {
        job_id: state.summaryJob.id,
        mode,
        summary_language: selectedSummaryLanguage(),
        gemini_model: elements.geminiModel.value.trim(),
      },
    });
    clearApiKey({ focus: false });
    const response = await pendingRequest;
    state.summaryJob = response.summary_job || null;
    renderResult(response.result);
    if (response.result.summary) {
      switchTab("summary");
      showToast("Summary complete.", "success");
    } else if (mode === "skip") {
      showToast("Summary skipped.", "success");
    } else {
      showToast("Summary failed. Review the warning, enter the key, and retry.", "error");
    }
  }

  function saveResult(kind, button) {
    const artifact = getArtifact(kind);
    if (!artifact) return;
    button.disabled = true;
    downloadArtifact(artifact);
    button.disabled = false;
    showToast(`${kind === "summary" ? "Summary" : "Transcript"} download started.`, "success");
  }

  function saveBothResults() {
    if (!state.result || !state.result.summary) return;
    elements.saveBothButton.disabled = true;
    downloadArtifact(state.result.transcript);
    window.setTimeout(() => {
      downloadArtifact(state.result.summary);
      elements.saveBothButton.disabled = false;
    }, 150);
    showToast("Two downloads started.", "success");
  }

  function downloadArtifact(artifact) {
    const mimeTypes = {
      json: "application/json;charset=utf-8",
      md: "text/markdown;charset=utf-8",
      srt: "application/x-subrip;charset=utf-8",
      txt: "text/plain;charset=utf-8",
      vtt: "text/vtt;charset=utf-8",
    };
    const blob = new Blob([artifact.content], {
      type: mimeTypes[artifact.format] || "text/plain;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = artifact.filename;
    link.hidden = true;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
  }

  function getArtifact(kind) {
    if (!state.result || !["transcript", "summary"].includes(kind)) return null;
    return kind === "summary" ? state.result.summary : state.result.transcript;
  }

  async function copyResult(kind) {
    const artifact = getArtifact(kind);
    if (!artifact) return;
    const content = artifact.content;
    try {
      await navigator.clipboard.writeText(content);
    } catch (_error) {
      const textarea = document.createElement("textarea");
      textarea.value = content;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    const label = kind === "summary" ? "Summary" : "Transcript";
    showToast(`${label} copied to the clipboard.`, "success");
  }

  function openCurrentVideo() {
    if (!state.result?.video_url) return;
    window.open(state.result.video_url, "_blank", "noopener,noreferrer");
  }

  async function requestJson(path, { method = "GET", headers = {}, body } = {}) {
    const requestHeaders = { Accept: "application/json", ...headers };
    const options = {
      method,
      headers: requestHeaders,
      cache: "no-store",
      credentials: "omit",
    };
    if (body !== undefined) {
      requestHeaders["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

    const response = await fetch(path, options);
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      // A generic error below avoids reflecting server HTML into the page.
    }
    if (!response.ok) {
      const error = new Error(payload?.error?.message || `Request failed (${response.status}).`);
      error.apiError = payload?.error || null;
      throw error;
    }
    return payload || {};
  }

  async function discardPendingSummary() {
    const job = state.summaryJob;
    state.summaryJob = null;
    if (!job?.id) return;
    try {
      await fetch("/api/summary/discard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: job.id }),
        cache: "no-store",
        credentials: "omit",
        keepalive: true,
      });
    } catch (_error) {
      // The server-side TTL is the fallback when a page closes or a request is interrupted.
    }
  }

  function apiErrorFrom(error) {
    if (error?.apiError) return error.apiError;
    return {
      message: "Could not complete the request.",
      hint: error?.message || "Check your connection, then try again.",
    };
  }

  function showError(error) {
    elements.errorMessage.textContent = error.message || "An unexpected error occurred.";
    elements.errorHint.textContent = error.hint || "Check the settings, then try again.";
    elements.errorPanel.classList.remove("hidden");
    elements.errorPanel.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function hideError() {
    elements.errorPanel.classList.add("hidden");
  }

  function setUrlError(message) {
    elements.urlError.textContent = message;
    elements.urlError.classList.remove("hidden");
    elements.videoUrl.setAttribute("aria-invalid", "true");
    elements.videoUrl.focus();
  }

  function clearUrlError() {
    elements.urlError.classList.add("hidden");
    elements.urlError.textContent = "";
    elements.videoUrl.removeAttribute("aria-invalid");
  }

  function setApiKeyError(message) {
    elements.apiKeyError.textContent = message;
    elements.apiKeyError.classList.remove("hidden");
    elements.apiKey.setAttribute("aria-invalid", "true");
  }

  function clearApiKeyError() {
    elements.apiKeyError.classList.add("hidden");
    elements.apiKeyError.textContent = "";
    elements.apiKey.removeAttribute("aria-invalid");
  }

  function validateSummaryLanguage() {
    clearSummaryLanguageError();
    if (!elements.summary.checked || elements.summaryLanguage.value !== OTHER_SUMMARY_LANGUAGE) {
      return true;
    }
    const language = elements.customSummaryLanguage.value.trim();
    if (!BCP47_LANGUAGE_PATTERN.test(language)) {
      setSummaryLanguageError("Enter a valid BCP 47 tag, such as it, ar, or pt-PT.");
      return false;
    }
    return true;
  }

  function setSummaryLanguageError(message) {
    elements.customSummaryLanguageError.textContent = message;
    elements.customSummaryLanguageError.classList.remove("hidden");
    elements.customSummaryLanguage.setAttribute("aria-invalid", "true");
  }

  function clearSummaryLanguageError() {
    elements.customSummaryLanguageError.classList.add("hidden");
    elements.customSummaryLanguageError.textContent = "";
    elements.customSummaryLanguage.removeAttribute("aria-invalid");
  }

  function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    elements.toastRegion.appendChild(toast);
    window.setTimeout(() => toast.remove(), 3800);
  }

  function formatDuration(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    return [hours, minutes, secs].map((value) => String(value).padStart(2, "0")).join(":");
  }

})();
