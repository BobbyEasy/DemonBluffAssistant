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
  for (const id of ["api-status","model-settings-badge","model-settings-form","model-provider","model-name","model-options","model-base-url-wrap","model-base-url","model-api-key","clear-model-key","model-settings-note","session-badge","new-session-form","undo-button","export-button","import-input","capture-button","detect-village-button","parse-button","capture-message","capture-status-dot","capture-preview","capture-preview-wrap","capture-lightbox","capture-lightbox-image","zoom-out-button","zoom-in-button","zoom-reset-button","zoom-label","close-lightbox-button","village-detection","village-detection-summary","village-detection-evidence","village-detection-warnings","confirm-village-button","discard-village-button","manual-form","manual-type","manual-seat-fields","manual-event-fields","manual-visible-role","event-role","board","village-stats","pending-json","pending-warnings","confidence-badge","clear-pending","export-recognition-button","confirm-pending","reanalyze-button","advice","assessments","solver-notes","toast"]) ui[id] = document.getElementById(id);
}

function bindEvents() {
  ui["new-session-form"].addEventListener("submit", createSession);
  ui["model-settings-form"].addEventListener("submit", saveModelSettings);
  ui["model-provider"].addEventListener("change", renderSelectedProvider);
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

function renderRoleOptions() {
  const options = ['<option value="">未知</option>', ...app.roles.map(role => `<option value="${escapeHtml(role.role_id)}">${escapeHtml(role.name_zh)} · ${escapeHtml(role.name_en)}</option>`)].join("");
  ui["event-role"].innerHTML = options;
  ui["manual-visible-role"].innerHTML = ['<option value="">未知</option>', ...app.roles.map(role => `<option value="${escapeHtml(role.name_en)}">${escapeHtml(role.name_zh)} · ${escapeHtml(role.name_en)}</option>`)].join("");
}

async function createSession(event) {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.currentTarget));
  for (const key of ["card_count","evil_count","minion_count","demon_count","health"]) values[key] = Number(values[key]);
  await createSessionFromConfig(values);
}

async function createSessionFromConfig(values) {
  try {
    app.session = await api("/api/sessions", { method: "POST", body: JSON.stringify(values) });
    localStorage.setItem("demon-bluff-session", app.session.session_id);
    app.analysis = null;
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
  ui["session-badge"].textContent = active ? `#${app.session.session_id.slice(0, 6)}` : "未开始";
  if (!active) return;
  const config = app.session.config;
  ui["village-stats"].innerHTML = [
    ["牌", config.card_count], ["恶徒", config.evil_count], ["爪牙", config.minion_count], ["恶魔", config.demon_count], ["生命", config.health]
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
  }
}

async function detectVillage() {
  if (!app.captureId) return;
  ui["detect-village-button"].disabled = true;
  ui["detect-village-button"].textContent = "正在识别…";
  try {
    app.villageSuggestion = await api(`/api/captures/${app.captureId}/village`, { method: "POST" });
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
  ui["village-detection-summary"].textContent = config
    ? `本地 OCR · ${config.card_count} 张牌 · ${config.evil_count} 恶徒 · ${config.minion_count} 爪牙 · ${config.demon_count} 恶魔 · ${config.health} 生命${config.deck_roles?.length ? ` · 牌组 ${config.deck_roles.length} 个角色` : ""}`
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
    const patch = await api(`/api/captures/${app.captureId}/parse?session_id=${app.session.session_id}`, { method: "POST" });
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
  const hasContent = (patch.seats?.length || 0) + (patch.events?.length || 0) > 0;
  const confidence = Math.round((patch.overall_confidence || 0) * 100);
  ui["confidence-badge"].textContent = hasContent ? `识别置信度 ${confidence}%` : "无待确认内容";
  ui["confirm-pending"].disabled = !hasContent;
  ui["export-recognition-button"].disabled = !hasRecognitionData(patch);
}

function validatePendingEditor() {
  try {
    const value = JSON.parse(ui["pending-json"].value);
    const hasContent = (value.seats?.length || 0) + (value.events?.length || 0) > 0;
    ui["confirm-pending"].disabled = !hasContent;
    ui["export-recognition-button"].disabled = !hasRecognitionData(value);
    ui["pending-json"].classList.remove("invalid");
  } catch (_) {
    ui["confirm-pending"].disabled = true;
    ui["export-recognition-button"].disabled = true;
    ui["pending-json"].classList.add("invalid");
  }
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
}

async function undo() {
  if (!app.session) return;
  try { app.session = await api(`/api/sessions/${app.session.session_id}/undo`, { method: "POST" }); app.analysis = null; renderSession(); await analyze(); }
  catch (error) { showToast(error.message, true); }
}

async function exportSession() {
  if (!app.session) return;
  try {
    const data = await api(`/api/sessions/${app.session.session_id}/export`);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    link.download = `demon-bluff-${app.session.session_id.slice(0, 8)}.json`;
    link.click(); URL.revokeObjectURL(link.href);
  } catch (error) { showToast(error.message, true); }
}

async function importSession(event) {
  const file = event.target.files[0]; if (!file) return;
  try {
    app.session = await api("/api/sessions/import", { method: "POST", body: await file.text() });
    localStorage.setItem("demon-bluff-session", app.session.session_id);
    app.analysis = null; renderSession(); await analyze(); showToast("局面已导入。", false);
  } catch (error) { showToast(error.message, true); }
  event.target.value = "";
}

function showToast(message, error) {
  ui.toast.textContent = message; ui.toast.className = `toast show ${error ? "error" : ""}`;
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => ui.toast.className = "toast", 4200);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}
