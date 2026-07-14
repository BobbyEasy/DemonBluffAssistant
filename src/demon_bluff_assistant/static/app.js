"use strict";

const ui = {};
const app = {
  config: null,
  modelSettings: null,
  roles: [],
  session: null,
  captureId: null,
  pending: { seats: [], events: [], warnings: [], overall_confidence: 0 },
  analysis: null,
  villageSuggestion: null,
  previewZoom: 1,
  chat: [],
};

const labels = {
  certain_evil: "必恶",
  lean_evil: "偏恶",
  undetermined: "未定",
  certain_good: "必善",
  reveal: "翻牌",
  execute: "处决",
  use_ability: "使用技能",
  wait: "等待",
};

document.addEventListener("DOMContentLoaded", async () => {
  bindElements();
  bindEvents();
  await bootstrap();
  setInterval(pollCapture, 1200);
});

function bindElements() {
  for (const id of ["api-status","model-settings-badge","model-settings-form","model-provider","model-name","model-options","model-base-url-wrap","model-base-url","model-api-key","clear-model-key","model-settings-note","session-badge","new-session-form","undo-button","export-button","import-input","capture-button","detect-village-button","parse-button","capture-message","capture-status-dot","vision-engine","glm-vision-form","glm-vision-api-key","clear-glm-vision-key","glm-vision-status","capture-preview","capture-preview-wrap","capture-lightbox","capture-lightbox-image","zoom-out-button","zoom-in-button","zoom-reset-button","zoom-label","close-lightbox-button","village-detection","village-detection-summary","village-detection-evidence","village-detection-warnings","confirm-village-button","discard-village-button","manual-form","manual-type","manual-seat-fields","manual-event-fields","manual-visible-role","event-role","board","village-stats","pending-json","pending-summary","pending-warnings","confidence-badge","clear-pending","export-recognition-button","confirm-pending","reanalyze-button","export-analysis-button","export-dataset-button","advice","assessments","solver-notes","workflow-status","workflow-step-1","workflow-step-2","workflow-step-3","workflow-step-4","chat-form","chat-input","chat-send-button","clear-chat-button","chat-messages","toast"]) ui[id] = document.getElementById(id);
}

function bindEvents() {
  ui["new-session-form"].addEventListener("submit", createSession);
  ui["model-settings-form"].addEventListener("submit", saveModelSettings);
  ui["model-provider"].addEventListener("change", renderSelectedProvider);
  ui["glm-vision-form"].addEventListener("submit", saveGlmVisionSettings);
  ui["vision-engine"].addEventListener("change", renderGlmVisionSettings);
  ui["capture-button"].addEventListener("click", captureNow);
  ui["parse-button"].addEventListener("click", parseCapture);
  ui["detect-village-button"].addEventListener("click", detectVillage);
  ui["confirm-village-button"].addEventListener("click", confirmDetectedVillage);
  ui["discard-village-button"].addEventListener("click", clearVillageSuggestion);
  ui["confirm-pending"].addEventListener("click", confirmPending);
  ui["clear-pending"].addEventListener("click", () => setPending(emptyPatch()));
  ui["export-recognition-button"].addEventListener("click", exportRecognition);
  ui["undo-button"].addEventListener("click", undo);
  ui["export-button"].addEventListener("click", exportSession);
  ui["import-input"].addEventListener("change", importSession);
  ui["reanalyze-button"].addEventListener("click", analyze);
  ui["export-analysis-button"].addEventListener("click", exportAnalysis);
  ui["export-dataset-button"].addEventListener("click", exportDataset);
  ui["chat-form"].addEventListener("submit", sendChatMessage);
  ui["clear-chat-button"].addEventListener("click", clearChat);
  ui["manual-type"].addEventListener("change", toggleManualType);
  ui["manual-form"].addEventListener("submit", addManualEntry);
  ui["pending-json"].addEventListener("input", validatePendingEditor);
  ui["capture-preview"].addEventListener("click", openCapturePreview);
  ui["capture-preview"].addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") openCapturePreview();
  });
  ui["zoom-out-button"].addEventListener("click", () => setPreviewZoom(app.previewZoom - 0.25));
  ui["zoom-in-button"].addEventListener("click", () => setPreviewZoom(app.previewZoom + 0.25));
  ui["zoom-reset-button"].addEventListener("click", () => setPreviewZoom(1));
  ui["close-lightbox-button"].addEventListener("click", () => ui["capture-lightbox"].close());
  ui["capture-lightbox"].addEventListener("click", event => {
    if (event.target === ui["capture-lightbox"]) ui["capture-lightbox"].close();
  });
}

function openCapturePreview() {
  if (!app.captureId || !ui["capture-preview"].src) return;
  ui["capture-lightbox-image"].src = ui["capture-preview"].src;
  setPreviewZoom(1);
  ui["capture-lightbox"].showModal();
}

function setPreviewZoom(value) {
  app.previewZoom = Math.min(4, Math.max(0.5, value));
  ui["capture-lightbox-image"].style.width = `${app.previewZoom * 100}%`;
  ui["zoom-label"].textContent = `${Math.round(app.previewZoom * 100)}%`;
}

async function bootstrap() {
  try {
    [app.config, app.modelSettings, app.roles] = await Promise.all([
      api("/api/config"),
      api("/api/model-settings"),
      api("/api/roles").then(data => data.roles),
    ]);
    renderConfig();
    renderModelSettings();
    renderRoleOptions();
    const stored = localStorage.getItem("demon-bluff-session");
    if (stored) {
      try { app.session = await api(`/api/sessions/${stored}`); }
      catch (_) { localStorage.removeItem("demon-bluff-session"); }
    }
    if (app.session) await loadChat();
    renderSession();
  } catch (error) { showToast(error.message, true); }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("json") ? response.json() : response;
}

function renderConfig() {
  const active = activeProviderStatus();
  const configured = Boolean(active?.configured);
  const label = active ? `${active.label} · ${active.model}` : "未配置策略模型";
  ui["api-status"].textContent = configured ? `${label} · 本地 OCR` : "未配置策略模型 · 本地 OCR/求解可用";
  ui["api-status"].className = "status-pill good";
}

function providerStatus(provider) {
  return app.modelSettings?.providers?.find(item => item.provider === provider) || null;
}

function activeProviderStatus() {
  return providerStatus(app.modelSettings?.active_provider);
}

function renderModelSettings() {
  const active = activeProviderStatus();
  ui["model-settings-badge"].textContent = active?.configured ? `已连接 ${active.label}` : "策略模型可选";
  ui["model-provider"].value = app.modelSettings?.active_provider || "openai";
  renderSelectedProvider();
  renderGlmVisionSettings();
}

function renderGlmVisionSettings() {
  const profile = providerStatus("zhipu");
  const selected = selectedVisionEngine();
  if (selected === "local") {
    ui["glm-vision-status"].textContent = "RapidOCR 在本机处理截图，不会上传图片。";
  } else if (profile?.configured) {
    ui["glm-vision-status"].textContent = "GLM-4.6V-Flash 已配置；识别时当前截图会发送到智谱 API。";
  } else {
    ui["glm-vision-status"].textContent = "尚未配置智谱 API Key；保存后才能使用 GLM 视觉识别。";
  }
}

function selectedVisionEngine() {
  return ui["vision-engine"]?.value === "glm" ? "glm" : "local";
}

function renderSelectedProvider() {
  const profile = providerStatus(ui["model-provider"].value);
  if (!profile) return;
  ui["model-name"].value = profile.model || "";
  ui["model-options"].innerHTML = (profile.known_models || []).map(model => `<option value="${escapeHtml(model)}"></option>`).join("");
  ui["model-base-url"].value = profile.base_url || "";
  ui["model-base-url-wrap"].classList.toggle("hidden", profile.provider !== "custom");
  ui["model-settings-note"].textContent = profile.provider === "deepseek"
    ? "DeepSeek V4 只负责确认后的文字策略；截图由本机 RapidOCR 离线识别，不消耗模型额度。"
    : profile.provider === "custom"
      ? "兼容接口仅用于确认后的文字策略。API Key 将发送到你填写的 Base URL。"
      : "OpenAI 用于文字策略；截图默认由本机 RapidOCR 离线识别。Key 使用当前 Windows 用户的 DPAPI 加密保存。";
}

async function saveModelSettings(event) {
  event.preventDefault();
  const provider = ui["model-provider"].value;
  const payload = {
    provider,
    model: ui["model-name"].value.trim(),
    api_key: ui["model-api-key"].value.trim() || null,
    base_url: provider === "custom" ? ui["model-base-url"].value.trim() : null,
    activate: true,
    clear_api_key: ui["clear-model-key"].checked,
  };
  try {
    app.modelSettings = await api("/api/model-settings", { method: "PUT", body: JSON.stringify(payload) });
    app.config = await api("/api/config");
    ui["model-api-key"].value = "";
    ui["clear-model-key"].checked = false;
    renderModelSettings();
    renderConfig();
    showToast(`已切换到 ${payload.model}。`, false);
  } catch (error) { showToast(error.message, true); }
}

async function saveGlmVisionSettings(event) {
  event.preventDefault();
  const payload = {
    provider: "zhipu",
    model: "glm-4.6v-flash",
    api_key: ui["glm-vision-api-key"].value.trim() || null,
    base_url: null,
    activate: false,
    clear_api_key: ui["clear-glm-vision-key"].checked,
  };
  try {
    app.modelSettings = await api("/api/model-settings", { method: "PUT", body: JSON.stringify(payload) });
    app.config = await api("/api/config");
    ui["glm-vision-api-key"].value = "";
    ui["clear-glm-vision-key"].checked = false;
    renderModelSettings();
    renderConfig();
    showToast(payload.clear_api_key ? "已删除智谱视觉 API Key。" : "智谱视觉接口已保存。", false);
  } catch (error) { showToast(error.message, true); }
}

function renderRoleOptions() {
  const options = ['<option value="">未知</option>', ...app.roles.map(role => `<option value="${escapeHtml(role.role_id)}">${escapeHtml(role.name_zh)} · ${escapeHtml(role.name_en)}</option>`)].join("");
  ui["event-role"].innerHTML = options;
  ui["manual-visible-role"].innerHTML = ['<option value="">未知</option>', ...app.roles.map(role => `<option value="${escapeHtml(role.name_en)}">${escapeHtml(role.name_zh)} · ${escapeHtml(role.name_en)}</option>`)].join("");
}

async function createSession(event) {
  event.preventDefault();
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    for (const key of ["card_count","health"]) values[key] = Number(values[key]);
    for (const key of ["evil_count","minion_count","demon_count"]) {
      const range = parseCountRange(values[key]);
      values[key] = range.minimum;
      values[`${key}_max`] = range.maximum;
    }
    await createSessionFromConfig(values);
  } catch (error) { showToast(error.message, true); }
}

function parseCountRange(value) {
  const match = String(value).trim().match(/^(\d{1,2})(?:\s*[-–—~至到]\s*(\d{1,2}))?$/);
  if (!match) throw new Error(`数量“${value}”格式不正确，请填写 2 或 2-4。`);
  const minimum = Number(match[1]);
  const maximum = Number(match[2] ?? match[1]);
  if (maximum < minimum) throw new Error(`数量范围“${value}”的最大值不能小于最小值。`);
  return { minimum, maximum };
}

function formatCountRange(minimum, maximum) {
  return maximum == null || minimum === maximum ? String(minimum) : `${minimum}-${maximum}`;
}

async function createSessionFromConfig(values) {
  try {
    app.session = await api("/api/sessions", { method: "POST", body: JSON.stringify(values) });
    localStorage.setItem("demon-bluff-session", app.session.session_id);
    app.analysis = null;
    app.chat = [];
    setPending(emptyPatch());
    renderSession();
    showToast("新村庄已建立。请先采集牌桌总览和牌组页。", false);
    return true;
  } catch (error) { showToast(error.message, true); return false; }
}

function renderSession() {
  const active = Boolean(app.session);
  ui["undo-button"].disabled = !active;
  ui["export-button"].disabled = !active;
  ui["reanalyze-button"].disabled = !active;
  ui["chat-input"].disabled = !active;
  ui["chat-send-button"].disabled = !active;
  ui["clear-chat-button"].disabled = !active || app.chat.length === 0;
  ui["session-badge"].textContent = active ? `#${app.session.session_id.slice(0, 6)}` : "未开始";
  renderWorkflow();
  renderChat();
  if (!active) return;
  const config = app.session.config;
  ui["village-stats"].innerHTML = [
    ["牌", config.card_count],
    ["恶徒", formatCountRange(config.evil_count, config.evil_count_max)],
    ["爪牙", formatCountRange(config.minion_count, config.minion_count_max)],
    ["恶魔", formatCountRange(config.demon_count, config.demon_count_max)],
    ["生命", config.health]
  ].map(([name,value]) => `<span class="stat">${name}<b>${value}</b></span>`).join("");
  renderBoard();
}

function renderBoard() {
  if (!app.session) return;
  const assessmentMap = new Map((app.analysis?.report?.assessments || []).map(item => [item.position, item]));
  ui.board.className = "board";
  ui.board.innerHTML = app.session.seats.map(seat => {
    const assessment = assessmentMap.get(seat.position);
    const classification = assessment?.classification || "undetermined";
    const flags = [seat.revealed ? "已翻开" : "未翻开", seat.alive ? "存活" : "死亡", seat.corrupted ? "腐化" : ""].filter(Boolean).join(" · ");
    return `<article class="seat-card ${classification}">
      <span class="seat-number">#${seat.position}</span><span class="seat-class">${labels[classification]}</span>
      <div class="seat-role">${escapeHtml(seat.visible_role || "未知角色")}</div>
      <div class="seat-meta">${escapeHtml(flags)}${assessment ? `<br>一致世界占比 ${(assessment.consistent_world_share * 100).toFixed(1)}%` : ""}${seat.claim_text ? `<br>${escapeHtml(seat.claim_text)}` : ""}</div>
    </article>`;
  }).join("");
}

async function captureNow() {
  try {
    const status = await api("/api/captures", { method: "POST" });
    acceptCaptureStatus(status);
  } catch (error) { showToast(error.message, true); }
}

async function pollCapture() {
  try { acceptCaptureStatus(await api("/api/captures/latest")); } catch (_) {}
}

function acceptCaptureStatus(status) {
  ui["capture-message"].textContent = status.message;
  ui["capture-status-dot"].className = `dot ${status.status}`;
  if (status.status === "ready" && status.capture_id && status.capture_id !== app.captureId) {
    app.captureId = status.capture_id;
    ui["capture-preview"].src = `/api/captures/${app.captureId}/image`;
    ui["capture-preview-wrap"].classList.remove("empty");
    ui["detect-village-button"].disabled = false;
    ui["parse-button"].disabled = !app.session;
    renderWorkflow();
  }
}

async function detectVillage() {
  if (!app.captureId) return;
  ui["detect-village-button"].disabled = true;
  ui["detect-village-button"].textContent = "正在识别…";
  try {
    app.villageSuggestion = await api(`/api/captures/${app.captureId}/village?engine=${selectedVisionEngine()}`, { method: "POST" });
    renderVillageSuggestion();
  } catch (error) { showToast(error.message, true); }
  finally {
    ui["detect-village-button"].textContent = "识别并创建村庄";
    ui["detect-village-button"].disabled = false;
  }
}

function renderVillageSuggestion() {
  const suggestion = app.villageSuggestion;
  ui["village-detection"].classList.remove("hidden");
  const config = suggestion?.config;
  const engine = suggestion?.recognition_engine === "rapidocr-local" ? "本地 OCR" : (suggestion?.recognition_engine || "未知引擎");
  ui["village-detection-summary"].textContent = config
    ? `${engine} · ${config.card_count} 张牌 · ${formatCountRange(config.evil_count, config.evil_count_max)} 恶徒 · ${formatCountRange(config.minion_count, config.minion_count_max)} 爪牙 · ${formatCountRange(config.demon_count, config.demon_count_max)} 恶魔 · ${config.health} 生命${config.deck_roles?.length ? ` · 牌组 ${config.deck_roles.length} 个角色` : ""}`
    : "截图中的建村信息不足，尚不能自动创建。";
  ui["village-detection-evidence"].textContent = suggestion?.raw_text?.length
    ? `识别依据：${suggestion.raw_text.join(" ｜ ")}`
    : "";
  ui["village-detection-warnings"].innerHTML = (suggestion?.warnings || []).map(item => `<div class="warning">${escapeHtml(item)}</div>`).join("");
  ui["confirm-village-button"].disabled = !config;
}

async function confirmDetectedVillage() {
  const config = app.villageSuggestion?.config;
  if (!config) return;
  if (await createSessionFromConfig(config)) clearVillageSuggestion();
}

function clearVillageSuggestion() {
  app.villageSuggestion = null;
  ui["village-detection"].classList.add("hidden");
  ui["confirm-village-button"].disabled = true;
}

async function parseCapture() {
  if (!app.session || !app.captureId) return;
  ui["parse-button"].disabled = true;
  ui["parse-button"].textContent = "正在解析…";
  try {
    const patch = await api(`/api/captures/${app.captureId}/parse?session_id=${app.session.session_id}&engine=${selectedVisionEngine()}`, { method: "POST" });
    setPending(patch);
    showToast("识别完成。请检查并修正待确认内容。", false);
  } catch (error) { showToast(error.message, true); }
  finally { ui["parse-button"].textContent = "解析这张截图"; ui["parse-button"].disabled = false; }
}

function emptyPatch() { return { seats: [], events: [], warnings: [], overall_confidence: 0 }; }

function setPending(patch) {
  app.pending = patch;
  ui["pending-json"].value = JSON.stringify(patch, null, 2);
  ui["pending-warnings"].innerHTML = (patch.warnings || []).map(item => `<div class="warning">${escapeHtml(item)}</div>`).join("");
  renderPendingSummary(patch);
  const hasContent = (patch.seats?.length || 0) + (patch.events?.length || 0) > 0;
  const confidence = Math.round((patch.overall_confidence || 0) * 100);
  ui["confidence-badge"].textContent = hasContent ? `识别置信度 ${confidence}%` : "无待确认内容";
  ui["confirm-pending"].disabled = !hasContent;
  ui["export-recognition-button"].disabled = !hasRecognitionData(patch);
  renderWorkflow();
}

function validatePendingEditor() {
  try {
    const value = JSON.parse(ui["pending-json"].value);
    app.pending = value;
    const hasContent = (value.seats?.length || 0) + (value.events?.length || 0) > 0;
    ui["confirm-pending"].disabled = !hasContent;
    ui["export-recognition-button"].disabled = !hasRecognitionData(value);
    ui["pending-json"].classList.remove("invalid");
    renderPendingSummary(value);
    renderWorkflow();
  } catch (_) {
    ui["confirm-pending"].disabled = true;
    ui["export-recognition-button"].disabled = true;
    ui["pending-json"].classList.add("invalid");
  }
}

function renderPendingSummary(patch) {
  const seats = patch?.seats || [];
  const events = patch?.events || [];
  if (!seats.length && !events.length) {
    ui["pending-summary"].className = "pending-summary empty-state";
    ui["pending-summary"].innerHTML = "<div><strong>暂无识别内容</strong><p>解析截图后先在这里核对牌号、角色和证词。</p></div>";
    return;
  }
  const seatCards = seats.map(seat => `<article><b>#${seat.position} · ${escapeHtml(seat.visible_role || "未知角色")}</b><span>${seat.revealed ? "已翻开" : "未翻开"}${seat.claim_text ? ` · ${escapeHtml(seat.claim_text)}` : ""}</span></article>`).join("");
  const eventCards = events.map(event => `<article><b>#${event.speaker_position} 证词 · ${escapeHtml(event.kind)}</b><span>${event.targets?.length ? `目标 #${event.targets.join(", #")} · ` : ""}${escapeHtml(event.raw_text || (event.value ?? "待核对"))}</span></article>`).join("");
  ui["pending-summary"].className = "pending-summary";
  ui["pending-summary"].innerHTML = seatCards + eventCards;
}

function hasRecognitionData(value) {
  return Boolean(
    (value.seats?.length || 0) +
    (value.events?.length || 0) +
    (value.warnings?.length || 0) +
    (value.raw_text?.length || 0)
  );
}

function exportRecognition() {
  let value;
  try { value = JSON.parse(ui["pending-json"].value); }
  catch (_) { return showToast("待确认 JSON 格式不正确，无法导出。", true); }
  if (!hasRecognitionData(value)) return showToast("当前没有可导出的识别结果。", true);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  link.download = `demon-bluff-recognition-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function confirmPending() {
  if (!app.session) return;
  let patch;
  try { patch = JSON.parse(ui["pending-json"].value); }
  catch (_) { return showToast("待确认 JSON 格式不正确。", true); }
  try {
    app.session = await api(`/api/sessions/${app.session.session_id}/events`, { method: "POST", body: JSON.stringify(patch) });
    setPending(emptyPatch());
    renderSession();
    await analyze();
  } catch (error) { showToast(error.message, true); }
}

function toggleManualType() {
  const eventMode = ui["manual-type"].value === "event";
  ui["manual-seat-fields"].classList.toggle("hidden", eventMode);
  ui["manual-event-fields"].classList.toggle("hidden", !eventMode);
}

function addManualEntry(event) {
  event.preventDefault();
  if (!app.session) return showToast("请先创建村庄。", true);
  const patch = emptyPatch();
  patch.overall_confidence = 1;
  if (ui["manual-type"].value === "seat") {
    patch.seats.push({
      position: Number(document.getElementById("manual-position").value),
      visible_role: ui["manual-visible-role"].value || null,
      revealed: document.getElementById("manual-revealed").checked,
      corrupted: document.getElementById("manual-corrupted").checked,
      confirmed_alignment: document.getElementById("manual-alignment").value || null,
      claim_text: document.getElementById("manual-claim").value || null,
    });
  } else {
    patch.events.push({
      speaker_position: Number(document.getElementById("event-speaker").value),
      role_id: ui["event-role"].value || null,
      phase: "day",
      kind: document.getElementById("event-kind").value,
      targets: document.getElementById("event-targets").value.split(/[,，\s]+/).filter(Boolean).map(Number),
      value: parseValue(document.getElementById("event-value").value),
      confidence: 1,
      raw_text: document.getElementById("event-raw").value || null,
    });
  }
  setPending(patch);
  document.getElementById("pending-editor").scrollIntoView({ behavior: "smooth" });
}

function parseValue(value) {
  const normalized = value.trim().toLowerCase();
  if (["true","yes","是","有"].includes(normalized)) return true;
  if (["false","no","否","无"].includes(normalized)) return false;
  if (normalized !== "" && Number.isFinite(Number(normalized))) return Number(normalized);
  return value || null;
}

async function analyze() {
  if (!app.session) return;
  ui.advice.innerHTML = "<p class=muted>正在重建一致世界并生成建议…</p>";
  try {
    app.analysis = await api(`/api/sessions/${app.session.session_id}/analysis`);
    renderAnalysis(); renderBoard();
  } catch (error) { showToast(error.message, true); }
}

function renderAnalysis() {
  const { report, advice } = app.analysis;
  ui.advice.className = "advice";
  ui.advice.innerHTML = `<div class="advice-action">${labels[advice.action_type] || advice.action_type}${advice.positions.length ? ` · #${advice.positions.join(", #")}` : ""}</div>
    <strong>${escapeHtml(advice.summary)}</strong>
    ${advice.reasoning.length ? `<ul>${advice.reasoning.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
    <p class="uncertainty">不确定性：${escapeHtml(advice.uncertainty)}</p>`;
  ui.assessments.innerHTML = report.assessments.map(item => `<div class="assessment"><b>#${item.position} · ${labels[item.classification]}</b><span>${(item.consistent_world_share * 100).toFixed(1)}%</span></div>`).join("");
  const conflict = report.conflict_event_ids.length ? [`冲突事件：${report.conflict_event_ids.join(", ")}`] : [];
  ui["solver-notes"].innerHTML = [...conflict, ...report.notes].map(note => `<p>• ${escapeHtml(note)}</p>`).join("");
  ui["export-analysis-button"].disabled = false;
  ui["export-dataset-button"].disabled = false;
  renderWorkflow();
}

async function exportAnalysis() {
  if (!app.session || !app.analysis) return;
  try {
    const data = await api(`/api/sessions/${app.session.session_id}/analysis/export`);
    downloadJson(data, `demon-bluff-analysis-${app.session.session_id.slice(0, 8)}.json`);
  } catch (error) { showToast(error.message, true); }
}

async function exportDataset() {
  try {
    const data = await api("/api/dataset/export");
    downloadJson(data, `demon-bluff-dataset-${new Date().toISOString().slice(0, 10)}.json`);
  } catch (error) { showToast(error.message, true); }
}

async function undo() {
  if (!app.session) return;
  try { app.session = await api(`/api/sessions/${app.session.session_id}/undo`, { method: "POST" }); app.analysis = null; ui["export-analysis-button"].disabled = true; renderSession(); await analyze(); }
  catch (error) { showToast(error.message, true); }
}

async function exportSession() {
  if (!app.session) return;
  try {
    const data = await api(`/api/sessions/${app.session.session_id}/export`);
    downloadJson(data, `demon-bluff-${app.session.session_id.slice(0, 8)}.json`);
  } catch (error) { showToast(error.message, true); }
}

async function importSession(event) {
  const file = event.target.files[0]; if (!file) return;
  try {
    app.session = await api("/api/sessions/import", { method: "POST", body: await file.text() });
    localStorage.setItem("demon-bluff-session", app.session.session_id);
    app.analysis = null; app.chat = []; await loadChat(); renderSession(); await analyze(); showToast("局面已导入。", false);
  } catch (error) { showToast(error.message, true); }
  event.target.value = "";
}

function downloadJson(data, filename) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function renderWorkflow() {
  const pendingCount = (app.pending?.seats?.length || 0) + (app.pending?.events?.length || 0);
  const step = !app.session ? 1 : pendingCount ? 3 : app.analysis ? 4 : 2;
  ui["workflow-status"].textContent = `第 ${step} 步`;
  for (let index = 1; index <= 4; index += 1) {
    ui[`workflow-step-${index}`].classList.toggle("active", index === step);
    ui[`workflow-step-${index}`].classList.toggle("done", index < step);
  }
}

async function loadChat() {
  if (!app.session) return;
  try {
    app.chat = (await api(`/api/sessions/${app.session.session_id}/chat`)).messages || [];
  } catch (_) { app.chat = []; }
  renderChat();
}

function renderChat() {
  const messages = app.chat || [];
  ui["clear-chat-button"].disabled = !app.session || messages.length === 0;
  if (!messages.length) {
    ui["chat-messages"].className = "chat-messages empty-state";
    ui["chat-messages"].innerHTML = "<div><strong>尚未开始讨论</strong><p>建立村庄后，可让 DeepSeek 比较候选解释、找信息量最高的下一步。</p></div>";
    return;
  }
  ui["chat-messages"].className = "chat-messages";
  ui["chat-messages"].innerHTML = messages.map(item => `<article class="chat-message ${item.role}"><b>${item.role === "user" ? "你" : "策略模型"}</b><div>${escapeHtml(item.content).replace(/\n/g, "<br>")}</div></article>`).join("");
  ui["chat-messages"].scrollTop = ui["chat-messages"].scrollHeight;
}

async function sendChatMessage(event) {
  event.preventDefault();
  if (!app.session) return;
  const message = ui["chat-input"].value.trim();
  if (!message) return;
  ui["chat-send-button"].disabled = true;
  ui["chat-send-button"].textContent = "分析中…";
  try {
    const result = await api(`/api/sessions/${app.session.session_id}/chat`, { method: "POST", body: JSON.stringify({ message }) });
    app.chat = result.messages || [];
    ui["chat-input"].value = "";
    renderChat();
  } catch (error) { showToast(error.message, true); }
  finally {
    ui["chat-send-button"].textContent = "发送追问";
    ui["chat-send-button"].disabled = !app.session;
  }
}

async function clearChat() {
  if (!app.session) return;
  try {
    await api(`/api/sessions/${app.session.session_id}/chat`, { method: "DELETE" });
    app.chat = [];
    renderChat();
  } catch (error) { showToast(error.message, true); }
}

function showToast(message, error) {
  ui.toast.textContent = message; ui.toast.className = `toast show ${error ? "error" : ""}`;
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => ui.toast.className = "toast", 4200);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}
