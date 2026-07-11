'use strict';

const $ = (id) => document.getElementById(id);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const apiHeaders = {'X-TARS-Client': 'dashboard'};
const state = {
  dashboard: null,
  settings: null,
  secrets: null,
  health: null,
  missions: [],
  activity: [],
  filter: 'all',
  search: '',
  currentView: localStorage.getItem('tarsView') || 'control',
  sessionTokens: Number(sessionStorage.getItem('tarsSessionTokens') || 0),
  chatBusy: false,
  listening: false,
  lastDashboardSignature: '',
};

let conversationId = localStorage.getItem('tarsConversationId') || makeId('conv');
localStorage.setItem('tarsConversationId', conversationId);
let recognition = null;

const STATUS_LABELS = {
  queued: 'EN COLA', awaiting_approval: 'APROBACIÓN', running: 'EJECUTANDO',
  completed: 'COMPLETADA', failed: 'FALLÓ', stopped: 'DETENIDA',
  blocked: 'BLOQUEADA', rejected: 'RECHAZADA',
};
const VIEW_META = {
  control: ['LIVE', 'Centro de control'], missions: ['OPERATIONS', 'Misiones'],
  agents: ['CREW', 'Agentes'], outputs: ['DELIVERABLES', 'Resultados'],
  settings: ['CONFIG', 'Ajustes'],
};

function makeId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function api(path, options = {}) {
  const headers = {...(options.headers || {}), ...apiHeaders};
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {...options, headers});
  let body = {};
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function escapeText(value) { return String(value ?? ''); }
function formatTime(timestamp) {
  if (!timestamp) return '—';
  return new Intl.DateTimeFormat('es-AR', {hour: '2-digit', minute: '2-digit'}).format(new Date(timestamp * 1000));
}
function formatDate(timestamp) {
  if (!timestamp) return '—';
  return new Intl.DateTimeFormat('es-AR', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'}).format(new Date(timestamp * 1000));
}
function timeAgo(timestamp) {
  if (!timestamp) return 'ahora';
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - timestamp));
  if (seconds < 15) return 'ahora';
  if (seconds < 60) return `hace ${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `hace ${hours}h`;
  return formatDate(timestamp);
}
function truncate(text, length = 120) {
  const value = escapeText(text).replace(/\s+/g, ' ').trim();
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}
function statusLabel(status) { return STATUS_LABELS[status] || escapeText(status).toUpperCase(); }
function currentModel() { return $('chatModel').value.trim(); }
function providerModelKey(provider) { return provider === 'google' ? 'google_model' : 'openai_model'; }
function providerLabel(provider) { return provider === 'openai' ? 'OpenAI' : 'Gemini'; }

function toast(title, message = '', kind = 'info', duration = 4200) {
  const node = document.createElement('article');
  node.className = `toast ${kind}`;
  const dot = document.createElement('i');
  const copy = document.createElement('div');
  const strong = document.createElement('strong'); strong.textContent = title;
  const p = document.createElement('p'); p.textContent = message;
  copy.append(strong, p); node.append(dot, copy); $('toastRegion').appendChild(node);
  setTimeout(() => node.remove(), duration);
}

function setInlineStatus(text, kind = '') {
  const el = $('settingsStatus');
  el.textContent = text;
  el.className = `inline-status ${kind}`;
}

function setTarsState(mode, title, subtitle) {
  const visual = $('tarsVisual');
  visual.className = `tars-visual ${mode}`;
  const badge = $('tarsStateBadge');
  badge.className = `state-badge ${mode === 'thinking' || mode === 'speaking' ? 'working' : mode}`;
  badge.textContent = mode.toUpperCase();
  $('stageTitle').textContent = title;
  $('stageSubtitle').textContent = subtitle;
}

function setView(view, persist = true) {
  if (!VIEW_META[view]) view = 'control';
  state.currentView = view;
  if (persist) localStorage.setItem('tarsView', view);
  $$('.view').forEach((node) => node.classList.toggle('active', node.dataset.viewPanel === view));
  $$('.nav-item').forEach((node) => node.classList.toggle('active', node.dataset.view === view));
  $('viewEyebrow').textContent = VIEW_META[view][0];
  $('viewTitle').textContent = VIEW_META[view][1];
  $('sidebar').classList.remove('mobile-open');
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function addMessage(role, text, timestamp = Date.now() / 1000) {
  const fragment = $('messageTemplate').content.cloneNode(true);
  const article = fragment.querySelector('.message');
  article.classList.add(role);
  const avatar = fragment.querySelector('.message-avatar');
  const name = fragment.querySelector('.message-meta strong');
  const time = fragment.querySelector('time');
  const body = fragment.querySelector('p');
  if (role === 'user') { avatar.textContent = 'E'; name.textContent = 'VOS'; }
  else if (role === 'error') { avatar.textContent = '!'; name.textContent = 'SISTEMA'; }
  else { avatar.textContent = 'T'; name.textContent = 'TARS'; }
  time.textContent = timeAgo(timestamp);
  body.textContent = text;
  $('chatLog').appendChild(fragment);
  $('chatLog').scrollTop = $('chatLog').scrollHeight;
}

async function loadConversation() {
  try {
    const messages = await api(`/api/messages?conversation_id=${encodeURIComponent(conversationId)}&limit=100`);
    if (!messages.length) return;
    $('chatLog').innerHTML = '';
    for (const message of messages) addMessage(message.role === 'user' ? 'user' : 'assistant', message.content, message.created_at);
  } catch (error) {
    console.warn('No se pudo cargar el historial', error);
  }
}

function clearConversationUI() {
  $('chatLog').innerHTML = '';
  addMessage('assistant', 'Nueva conversación iniciada. Los historiales anteriores permanecen guardados localmente.');
}

function tokenCountFromUsage(usage) {
  if (!usage || typeof usage !== 'object') return 0;
  return Number(usage.total_tokens || usage.totalTokenCount || usage.total_tokens_used ||
    (Number(usage.input_tokens || usage.promptTokenCount || 0) + Number(usage.output_tokens || usage.candidatesTokenCount || 0))) || 0;
}

function updateSessionTokens(usage) {
  const amount = tokenCountFromUsage(usage);
  if (amount > 0) {
    state.sessionTokens += amount;
    sessionStorage.setItem('tarsSessionTokens', String(state.sessionTokens));
  }
  $('metricTokens').textContent = state.sessionTokens ? state.sessionTokens.toLocaleString('es-AR') : '—';
  $('metricTokensSub').textContent = state.sessionTokens ? 'Tokens reportados en esta pestaña' : 'El proveedor aún no informó uso';
}

function dashboardSignature(data) {
  const missions = (data.missions || []).map((m) => `${m.id}:${m.status}:${m.updated_at}`).join('|');
  return `${missions}::${data.health?.executor?.active_mission_id || ''}::${data.settings?.provider || ''}`;
}

async function loadDashboard({silent = false} = {}) {
  try {
    const data = await api('/api/dashboard');
    const changed = dashboardSignature(data) !== state.lastDashboardSignature;
    state.dashboard = data;
    state.settings = data.settings;
    state.secrets = data.secrets;
    state.health = data.health;
    state.missions = data.missions || [];
    state.activity = data.activity || [];
    state.lastDashboardSignature = dashboardSignature(data);
    renderSystem();
    if (changed) {
      renderMissions();
      renderApprovals();
      renderActivity();
      renderOutputs();
      renderAgents();
    }
    if (!silent) populateSettings();
  } catch (error) {
    $('sidebarDot').className = 'status-dot error';
    $('metricSystem').textContent = 'OFFLINE';
    $('metricSystemDot').className = 'status-dot error';
    $('metricSystemSub').textContent = error.message;
    if (!silent) toast('No se pudo conectar con TARS', error.message, 'error', 7000);
  }
}

function renderSystem() {
  const {health, settings, secrets, stats} = state.dashboard;
  $('sidebarDot').className = 'status-dot online';
  $('sidebarVersion').textContent = `v${health.version}`;
  $('sidebarAddress').textContent = `${location.hostname}:${location.port || 80}`;
  $('metricSystem').textContent = 'ONLINE';
  $('metricSystemDot').className = 'status-dot online';
  $('metricSystemSub').textContent = `Python ${health.python} · servidor privado`;
  $('topProvider').textContent = providerLabel(settings.provider);
  $('composerProvider').textContent = providerLabel(settings.provider).toUpperCase();
  $('stageHumor').textContent = `${settings.humor}%`;
  $('navMissionBadge').textContent = stats.active + stats.awaiting_approval;
  $('navOutputBadge').textContent = stats.completed;
  $('metricApprovals').textContent = stats.awaiting_approval;
  $('approvalCount').textContent = stats.awaiting_approval;
  $('approvalCount').classList.toggle('has-items', stats.awaiting_approval > 0);
  $('filterAll').textContent = stats.all;
  $('filterActive').textContent = stats.active;
  $('filterApproval').textContent = stats.awaiting_approval;
  $('filterCompleted').textContent = stats.completed;
  $('filterFailed').textContent = stats.failed + stats.blocked;
  $('queueSummary').textContent = `${stats.queued} en cola · ${stats.running} ejecutando`;
  updateSessionTokens(null);

  const running = state.missions.find((mission) => mission.status === 'running');
  const executorActive = health.executor?.active_mission_id;
  if (running || executorActive) {
    const mission = running || state.missions.find((item) => item.id === executorActive);
    $('metricActive').textContent = mission ? mission.worker : 'ACTIVA';
    $('metricActiveSub').textContent = mission ? truncate(mission.title, 42) : 'Proceso en ejecución';
    $('topExecutor').textContent = mission ? `${mission.worker} trabajando` : 'Trabajando';
    $('executorDot').className = 'status-dot online';
    if (!state.chatBusy) setTarsState('working', `${mission?.worker || 'Agente'} ejecutando`, mission?.title || 'Misión activa');
  } else {
    $('metricActive').textContent = 'NINGUNA';
    $('metricActiveSub').textContent = stats.queued ? `${stats.queued} esperando ejecución` : 'Cola preparada';
    $('topExecutor').textContent = settings.auto_execute ? 'En espera' : 'Pausado';
    $('executorDot').className = settings.auto_execute ? 'status-dot online' : 'status-dot warning';
    if (!state.chatBusy && !state.listening && !window.speechSynthesis?.speaking) setTarsState('online', 'TARS operativo', 'Esperando una misión.');
  }

  const activeProviderHasKey = Boolean(secrets[settings.provider]);
  $('globalBanner').classList.toggle('hidden', activeProviderHasKey);
  if (!activeProviderHasKey) {
    $('bannerTitle').textContent = 'API key pendiente';
    $('bannerText').textContent = `Configurá ${providerLabel(settings.provider)} para habilitar conversación y misiones.`;
  }

  renderIntegrations();
}

function renderIntegrations() {
  if (!state.health) return;
  setIntegrationState('browserUseState', state.health.browser_use_installed, 'LISTO', 'NO INSTALADO');
  setIntegrationState('uiTarsState', state.health.ui_tars_installed, 'INSTALADO', 'NO CONECTADO');
  const voiceReady = Boolean(window.speechSynthesis || state.health.native_voice_available);
  setIntegrationState('voiceIntegrationState', voiceReady, 'LISTA', 'NO DISPONIBLE');
  const keychain = state.health.key_storage === 'keychain';
  setIntegrationState('keychainState', keychain, 'KEYCHAIN', state.health.key_storage?.toUpperCase() || 'LOCAL');
}
function setIntegrationState(id, ready, readyText, missingText) {
  const el = $(id); el.textContent = ready ? readyText : missingText;
  el.className = `integration-state ${ready ? 'ready' : 'missing'}`;
}

function agentStatus(worker) {
  const running = state.missions.find((mission) => mission.worker === worker && mission.status === 'running');
  const queued = state.missions.find((mission) => mission.worker === worker && mission.status === 'queued');
  const approval = state.missions.find((mission) => mission.worker === worker && mission.status === 'awaiting_approval');
  if (running) return ['WORKING', 'working'];
  if (approval) return ['WAITING', 'error'];
  if (queued) return ['QUEUED', 'standby'];
  return worker === 'TARS' ? ['ONLINE', 'online'] : ['STANDBY', 'standby'];
}

function renderAgents() {
  for (const worker of ['TARS', 'CASE', 'KIPP']) {
    const [label, cls] = agentStatus(worker);
    const roster = document.querySelector(`.agent-row[data-agent="${worker}"] .agent-state`);
    if (roster) { roster.textContent = label; roster.className = `agent-state ${cls}`; }
    const detail = $(`agent${worker[0]}${worker.slice(1).toLowerCase()}Status`);
    if (detail) { detail.textContent = label; detail.className = `state-badge ${cls}`; }
  }
  if (state.settings) $('agentTarsModel').textContent = state.settings[providerModelKey(state.settings.provider)] || 'Sin modelo';
  if (state.health) $('agentCaseIntegration').textContent = state.health.browser_use_installed ? 'INSTALADO' : 'FALTA INSTALAR';
}

function missionMatchesFilter(mission) {
  if (state.filter === 'active') return ['queued', 'running'].includes(mission.status);
  if (state.filter === 'failed') return ['failed', 'blocked', 'rejected', 'stopped'].includes(mission.status);
  if (state.filter !== 'all') return mission.status === state.filter;
  return true;
}
function missionMatchesSearch(mission) {
  if (!state.search) return true;
  const text = `${mission.title} ${mission.description} ${mission.worker} ${mission.model} ${mission.status}`.toLowerCase();
  return text.includes(state.search.toLowerCase());
}

function createActionButton(label, action, mission, kind = '') {
  const button = document.createElement('button');
  button.type = 'button'; button.textContent = label; button.className = `mini-button ${kind}`;
  button.addEventListener('click', async (event) => {
    event.stopPropagation(); button.disabled = true;
    try {
      await api(`/api/missions/${mission.id}/${action}`, {method: 'POST', body: '{}'});
      toast('Misión actualizada', `${mission.title}: ${label.toLowerCase()}.`, action === 'approve' ? 'success' : 'info');
      await loadDashboard({silent: true});
    } catch (error) { toast('No se pudo actualizar', error.message, 'error'); }
    finally { button.disabled = false; }
  });
  return button;
}

function buildMissionRow(mission, compact = false) {
  const fragment = $('missionRowTemplate').content.cloneNode(true);
  const row = fragment.querySelector('.mission-row');
  row.dataset.status = mission.status;
  const avatar = row.querySelector('.mission-agent'); avatar.textContent = mission.worker[0]; avatar.classList.add(mission.worker.toLowerCase());
  row.querySelector('.mission-title-line strong').textContent = mission.title;
  const pill = row.querySelector('.status-pill'); pill.textContent = statusLabel(mission.status); pill.classList.add(mission.status);
  row.querySelector('.mission-main p').textContent = mission.description;
  row.querySelector('.mission-meta').textContent = `${mission.worker} · ${mission.provider}/${mission.model} · riesgo ${mission.risk}`;
  row.querySelector('.mission-progress span').textContent = timeAgo(mission.updated_at);
  const actions = row.querySelector('.mission-actions');
  if (mission.status === 'awaiting_approval') actions.append(createActionButton('Aprobar', 'approve', mission, 'approve'), createActionButton('Rechazar', 'reject', mission, 'reject'));
  if (['queued', 'running'].includes(mission.status)) actions.append(createActionButton('Detener', 'stop', mission, 'stop'));
  if (['failed', 'stopped'].includes(mission.status)) actions.append(createActionButton('Reintentar', 'retry', mission));
  const detail = document.createElement('button'); detail.type = 'button'; detail.textContent = compact ? 'Ver' : 'Detalle'; detail.className = 'mini-button';
  detail.addEventListener('click', (event) => { event.stopPropagation(); openMissionDetail(mission.id); }); actions.append(detail);
  row.addEventListener('click', () => openMissionDetail(mission.id));
  return fragment;
}

function renderMissions() {
  const board = $('missionBoard'); board.innerHTML = '';
  const filtered = state.missions.filter(missionMatchesFilter).filter(missionMatchesSearch);
  if (!filtered.length) {
    const empty = document.createElement('div'); empty.className = 'empty-board';
    empty.innerHTML = '<strong>No hay misiones en esta vista.</strong><span>Creá una misión o cambiá los filtros.</span>';
    board.appendChild(empty);
  } else filtered.forEach((mission) => board.appendChild(buildMissionRow(mission)));

  const preview = $('missionPreview'); preview.innerHTML = '';
  const recent = state.missions.slice(0, 6);
  if (!recent.length) {
    const empty = document.createElement('div'); empty.className = 'empty-state-small'; empty.textContent = 'Todavía no hay misiones. La primera puede ser una prueba segura de navegación.'; preview.appendChild(empty);
  } else recent.forEach((mission) => preview.appendChild(buildMissionRow(mission, true)));
}

function renderApprovals() {
  const root = $('approvalList'); root.innerHTML = '';
  const approvals = state.missions.filter((mission) => mission.status === 'awaiting_approval');
  if (!approvals.length) { root.className = 'approval-list empty-state-small'; root.textContent = 'No hay acciones pendientes.'; return; }
  root.className = 'approval-list';
  for (const mission of approvals.slice(0, 5)) {
    const item = document.createElement('article'); item.className = 'approval-item';
    const title = document.createElement('strong'); title.textContent = mission.title;
    const p = document.createElement('p'); p.textContent = mission.risk_reason || `Riesgo ${mission.risk}`;
    const actions = document.createElement('div'); actions.className = 'approval-actions';
    actions.append(createActionButton('Aprobar', 'approve', mission, 'approve'), createActionButton('Rechazar', 'reject', mission, 'reject'));
    item.append(title, p, actions); root.appendChild(item);
  }
}

function renderActivity() {
  const root = $('activityFeed'); root.innerHTML = '';
  if (!state.activity.length) { root.className = 'activity-feed empty-state-small'; root.textContent = 'Todavía no hay actividad.'; return; }
  root.className = 'activity-feed';
  for (const event of state.activity.slice(0, 10)) {
    const fragment = $('activityTemplate').content.cloneNode(true);
    const item = fragment.querySelector('.activity-item'); item.dataset.kind = event.kind;
    item.querySelector('strong').textContent = `${event.worker} · ${event.title}`;
    item.querySelector('p').textContent = truncate(event.detail, 96);
    item.querySelector('time').textContent = timeAgo(event.created_at);
    item.addEventListener('click', () => openMissionDetail(event.mission_id));
    root.appendChild(fragment);
  }
}

function renderOutputs() {
  const completed = state.missions.filter((mission) => mission.status === 'completed');
  const failed = state.missions.filter((mission) => ['failed', 'blocked'].includes(mission.status));
  $('outputCompleted').textContent = completed.length;
  $('outputFailed').textContent = failed.length;
  $('outputApps').textContent = '0';
  const root = $('outputGrid'); root.innerHTML = '';
  const outputs = state.missions.filter((mission) => ['completed', 'failed', 'blocked'].includes(mission.status));
  if (!outputs.length) {
    const empty = document.createElement('div'); empty.className = 'empty-board';
    empty.innerHTML = '<strong>No hay resultados todavía.</strong><span>Las misiones completadas aparecerán acá con su informe.</span>';
    root.appendChild(empty); return;
  }
  for (const mission of outputs) {
    const fragment = $('outputTemplate').content.cloneNode(true);
    const card = fragment.querySelector('.output-card');
    const avatar = card.querySelector('.output-agent'); avatar.textContent = mission.worker[0]; avatar.classList.add(mission.worker.toLowerCase());
    const pill = card.querySelector('.status-pill'); pill.textContent = statusLabel(mission.status); pill.classList.add(mission.status);
    card.querySelector('h3').textContent = mission.title;
    card.querySelector('p').textContent = mission.result || mission.error || mission.risk_reason || 'Sin contenido.';
    card.querySelector('.output-meta').textContent = `${mission.worker} · ${formatDate(mission.completed_at || mission.updated_at)}`;
    card.querySelector('.output-open').addEventListener('click', () => openMissionDetail(mission.id));
    root.appendChild(fragment);
  }
}

async function openMissionDetail(missionId) {
  try {
    const [mission, events] = await Promise.all([
      api(`/api/missions/${missionId}`), api(`/api/missions/${missionId}/events`),
    ]);
    $('detailWorker').textContent = `${mission.worker} / ${mission.tool}`;
    $('detailTitle').textContent = mission.title;
    const body = $('detailBody'); body.innerHTML = '';
    const grid = document.createElement('div'); grid.className = 'detail-grid';
    const fields = [
      ['Estado', statusLabel(mission.status)], ['Riesgo', mission.risk.toUpperCase()],
      ['Modelo', `${mission.provider}/${mission.model}`], ['Creada', formatDate(mission.created_at)],
      ['Inicio', formatDate(mission.started_at)], ['Final', formatDate(mission.completed_at)],
    ];
    for (const [label, value] of fields) {
      const field = document.createElement('div'); field.className = 'detail-field';
      const span = document.createElement('span'); span.textContent = label;
      const strong = document.createElement('strong'); strong.textContent = value;
      field.append(span, strong); grid.appendChild(field);
    }
    body.appendChild(grid);
    body.appendChild(detailSection('Objetivo', mission.description));
    if (mission.result || mission.error) body.appendChild(detailSection(mission.error ? 'Error' : 'Resultado', mission.error || mission.result));
    body.appendChild(detailSection('Evaluación de riesgo', mission.risk_reason));
    const timelineSection = document.createElement('section'); timelineSection.className = 'detail-section';
    const h3 = document.createElement('h3'); h3.textContent = 'Línea de actividad';
    const timeline = document.createElement('div'); timeline.className = 'event-timeline';
    for (const event of events) {
      const row = document.createElement('article'); row.className = 'event-row';
      const dot = document.createElement('i'); const copy = document.createElement('div');
      const strong = document.createElement('strong'); strong.textContent = escapeText(event.kind).toUpperCase();
      const p = document.createElement('p'); p.textContent = event.detail;
      const time = document.createElement('time'); time.textContent = formatDate(event.created_at);
      copy.append(strong, p); row.append(dot, copy, time); timeline.appendChild(row);
    }
    timelineSection.append(h3, timeline); body.appendChild(timelineSection);
    $('detailDialog').showModal();
  } catch (error) { toast('No se pudo abrir la misión', error.message, 'error'); }
}
function detailSection(title, content) {
  const section = document.createElement('section'); section.className = 'detail-section';
  const h3 = document.createElement('h3'); h3.textContent = title;
  const div = document.createElement('div'); div.className = 'detail-content'; div.textContent = content || '—';
  section.append(h3, div); return section;
}

function populateSettings() {
  if (!state.settings) return;
  const settings = state.settings;
  $('provider').value = settings.provider;
  $('chatModel').value = settings[providerModelKey(settings.provider)] || '';
  $('browserModel').value = settings.browser_model || '';
  $('browserMaxSteps').value = settings.browser_max_steps || 18;
  $('humor').value = settings.humor; $('humorValue').textContent = `${settings.humor}%`;
  $('personality').value = settings.personality || '';
  $('autoSpeak').checked = Boolean(settings.auto_speak);
  $('autoExecute').checked = Boolean(settings.auto_execute);
  $('browserEnabled').checked = Boolean(settings.browser_enabled);
  $('allowedDomains').value = (settings.allowed_domains || []).join(', ');
  $('stageHumor').textContent = `${settings.humor}%`;
  updateProviderKeyStatus();
}
function updateProviderKeyStatus() {
  if (!state.secrets || !state.settings) return;
  const provider = $('provider').value;
  const configured = Boolean(state.secrets[provider]);
  $('providerKeyDot').className = `status-dot ${configured ? 'online' : 'warning'}`;
  $('providerKeyTitle').textContent = `${providerLabel(provider)} API key`;
  $('providerKeyText').textContent = configured ? `Configurada en ${state.secrets.storage}` : 'No configurada';
}

async function saveSettings() {
  if (!state.settings) return;
  const provider = $('provider').value;
  const payload = {
    provider,
    [providerModelKey(provider)]: currentModel(),
    browser_model: $('browserModel').value.trim(),
    browser_max_steps: Number($('browserMaxSteps').value),
    humor: Number($('humor').value),
    personality: $('personality').value.trim(),
    auto_speak: $('autoSpeak').checked,
    auto_execute: $('autoExecute').checked,
    browser_enabled: $('browserEnabled').checked,
    allowed_domains: $('allowedDomains').value.split(',').map((value) => value.trim()).filter(Boolean),
  };
  try {
    const data = await api('/api/settings', {method: 'POST', body: JSON.stringify(payload)});
    state.settings = data.settings; setInlineStatus('Ajustes guardados.', 'ok');
    toast('Configuración guardada', 'Los cambios ya están activos.', 'success');
    await loadDashboard({silent: true});
  } catch (error) { setInlineStatus(error.message, 'error'); toast('No se pudo guardar', error.message, 'error'); }
}

function renderModels(models) {
  const box = $('modelsBox'); box.innerHTML = ''; box.classList.remove('hidden');
  if (!models.length) { box.textContent = 'No se encontraron modelos compatibles.'; return; }
  for (const model of models) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = model;
    button.addEventListener('click', () => {
      $('chatModel').value = model; $('browserModel').value = model;
      setInlineStatus(`Modelo seleccionado: ${model}. Guardá los cambios.`, 'ok');
    });
    box.appendChild(button);
  }
}

async function dispatchPrefixedMission(message) {
  const match = message.match(/^(CASE|KIPP)\s*[:,\-]?\s+(.+)$/is);
  if (!match) return false;
  const worker = match[1].toUpperCase(); const description = match[2].trim();
  const model = worker === 'CASE' ? $('browserModel').value.trim() : currentModel();
  const mission = await api('/api/missions', {method: 'POST', body: JSON.stringify({
    title: `${worker}: ${description.slice(0, 72)}`, description, worker, risk: 'low',
    provider: $('provider').value, model,
  })});
  addMessage('assistant', `${worker} recibió la misión. Estado: ${statusLabel(mission.status).toLowerCase()}.`);
  toast(`${worker} despachado`, mission.title, mission.status === 'blocked' ? 'error' : 'success');
  await loadDashboard({silent: true}); return true;
}

async function sendChat(message) {
  if (state.chatBusy || !message.trim()) return;
  addMessage('user', message); $('chatInput').value = ''; resizeComposer();
  state.chatBusy = true; setTarsState('thinking', 'Procesando', 'Analizando tu orden y verificando permisos.');
  try {
    if (await dispatchPrefixedMission(message)) return;
    const data = await api('/api/chat', {method: 'POST', body: JSON.stringify({
      message, conversation_id: conversationId, provider: $('provider').value, model: currentModel(),
    })});
    addMessage('assistant', data.text); updateSessionTokens(data.usage);
    if ($('autoSpeak').checked) speak(data.text);
  } catch (error) {
    addMessage('error', error.message); toast('TARS no pudo responder', error.message, 'error', 6500);
    setTarsState('error', 'Error de comunicación', error.message);
  } finally {
    state.chatBusy = false;
    if (!window.speechSynthesis?.speaking) setTimeout(() => {
      if (!state.missions.some((m) => m.status === 'running')) setTarsState('online', 'TARS operativo', 'Esperando una misión.');
    }, 650);
  }
}

function populateVoices() {
  if (!('speechSynthesis' in window)) return;
  const voices = speechSynthesis.getVoices(); const selected = localStorage.getItem('tarsVoice');
  const select = $('voiceSelect'); select.innerHTML = '';
  const preferred = voices.filter((voice) => /^es|^en/i.test(voice.lang));
  for (const voice of (preferred.length ? preferred : voices)) {
    const option = document.createElement('option'); option.value = voice.name; option.textContent = `${voice.name} · ${voice.lang}`;
    if (voice.name === selected) option.selected = true; select.appendChild(option);
  }
  if (!select.options.length) { const option = document.createElement('option'); option.textContent = 'Voz predeterminada'; select.appendChild(option); }
}

async function speak(text) {
  if (!text) return;
  if (!('speechSynthesis' in window)) {
    try { await api('/api/voice/say', {method: 'POST', body: JSON.stringify({text, rate: Math.round(Number($('voiceRate').value) * 190)})}); }
    catch (error) { toast('Voz no disponible', error.message, 'error'); }
    return;
  }
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text.slice(0, 5000));
  utterance.lang = 'es-AR';
  utterance.rate = Number($('voiceRate').value) || 0.95;
  utterance.pitch = 0.91;
  const voice = speechSynthesis.getVoices().find((item) => item.name === $('voiceSelect').value);
  if (voice) utterance.voice = voice;
  utterance.onstart = () => setTarsState('speaking', 'Transmitiendo', 'TARS está respondiendo por voz.');
  utterance.onend = () => { if (!state.chatBusy) setTarsState('online', 'TARS operativo', 'Esperando una misión.'); };
  utterance.onerror = () => setTarsState('online', 'TARS operativo', 'La salida de voz fue interrumpida.');
  speechSynthesis.speak(utterance);
}

function initRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    $('micButton').disabled = true; $('voiceStatus').textContent = 'Micrófono no compatible; usá Chrome';
    return;
  }
  recognition = new Recognition(); recognition.lang = 'es-AR'; recognition.interimResults = true; recognition.continuous = false;
  recognition.onstart = () => {
    state.listening = true; $('micButton').classList.add('listening'); $('voiceStatus').textContent = 'Escuchando… decí “detener” para emergencia';
    setTarsState('thinking', 'Escuchando', 'Capturando tu instrucción.');
  };
  recognition.onresult = (event) => {
    let finalText = ''; let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const text = event.results[i][0].transcript;
      if (event.results[i].isFinal) finalText += text; else interim += text;
    }
    $('chatInput').value = finalText || interim; resizeComposer();
    if (finalText && /\b(detener|pará todo|para todo)\b/i.test(finalText)) {
      stopAll(); $('chatInput').value = ''; return;
    }
    if (finalText && $('handsFree').checked) setTimeout(() => $('chatForm').requestSubmit(), 220);
  };
  recognition.onerror = (event) => { $('voiceStatus').textContent = `Micrófono: ${event.error}`; toast('Error de micrófono', event.error, 'error'); };
  recognition.onend = () => {
    state.listening = false; $('micButton').classList.remove('listening'); $('voiceStatus').textContent = 'Micrófono listo';
    if (!state.chatBusy) setTarsState('online', 'TARS operativo', 'Esperando una misión.');
  };
}

function resizeComposer() {
  const input = $('chatInput'); input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 130)}px`;
}

async function stopAll() {
  try {
    const data = await api('/api/stop-all', {method: 'POST', body: '{}'});
    window.speechSynthesis?.cancel(); if (state.listening) recognition?.stop();
    addMessage('assistant', `Detención global ejecutada. Trabajos detenidos: ${data.stopped.length}.`);
    toast('Detención global ejecutada', `${data.stopped.length} trabajos detenidos.`, 'success');
    setTarsState('online', 'Sistema detenido', 'No quedan misiones activas.');
    await loadDashboard({silent: true});
  } catch (error) { toast('Falló la detención global', error.message, 'error'); }
}

async function createMission(event) {
  event.preventDefault();
  const button = $('createMissionButton'); button.disabled = true;
  try {
    const worker = $('worker').value;
    const payload = {
      title: $('missionTitle').value.trim(), description: $('missionDescription').value.trim(),
      worker, risk: $('risk').value, provider: $('provider').value,
      model: worker === 'CASE' ? $('browserModel').value.trim() : currentModel(),
    };
    const mission = await api('/api/missions', {method: 'POST', body: JSON.stringify(payload)});
    $('missionForm').reset(); $('missionDialog').close();
    toast('Misión creada', `${mission.worker} · ${statusLabel(mission.status)}`, mission.status === 'blocked' ? 'error' : 'success');
    addMessage('assistant', `Misión “${mission.title}” creada. Estado: ${statusLabel(mission.status).toLowerCase()}.`);
    await loadDashboard({silent: true}); setView('missions');
  } catch (error) { toast('No se pudo crear la misión', error.message, 'error'); }
  finally { button.disabled = false; }
}

function openMissionDialog() {
  if (!state.settings) return;
  $('risk').value = 'low'; $('worker').value = 'CASE'; $('missionDialog').showModal();
  setTimeout(() => $('missionTitle').focus(), 50);
}

function bindEvents() {
  $$('.nav-item').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));
  $$('[data-go-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.goView)));
  $$('[data-open-mission]').forEach((button) => button.addEventListener('click', openMissionDialog));
  $('openMission').addEventListener('click', openMissionDialog);
  $('mobileMenu').addEventListener('click', () => $('sidebar').classList.toggle('mobile-open'));
  $('sidebarCollapse').addEventListener('click', () => $('sidebar').closest('.app-shell').classList.toggle('sidebar-collapsed'));
  document.addEventListener('click', (event) => { if (innerWidth <= 760 && !event.target.closest('#sidebar') && !event.target.closest('#mobileMenu')) $('sidebar').classList.remove('mobile-open'); });

  $('chatForm').addEventListener('submit', (event) => { event.preventDefault(); const value = $('chatInput').value.trim(); if (value) sendChat(value); });
  $('chatInput').addEventListener('input', resizeComposer);
  $('chatInput').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('chatForm').requestSubmit(); } });
  $$('.quick-commands button').forEach((button) => button.addEventListener('click', () => { $('chatInput').value = button.dataset.command; resizeComposer(); $('chatInput').focus(); }));
  $('newConversation').addEventListener('click', () => { conversationId = makeId('conv'); localStorage.setItem('tarsConversationId', conversationId); clearConversationUI(); toast('Nueva conversación', 'Se creó una sesión local separada.', 'success'); });
  $('screenShare').addEventListener('click', () => {
    if (state.health?.ui_tars_installed) toast('UI-TARS detectado', 'El puente de control de escritorio se conectará en la próxima fase.', 'info', 6000);
    else toast('Control de pantalla no conectado', 'Instalá UI-TARS desde Agentes. La navegación web con CASE funciona por separado.', 'info', 6000);
    setView('agents');
  });

  $('missionForm').addEventListener('submit', createMission);
  $$('.close-dialog').forEach((button) => button.addEventListener('click', () => $('missionDialog').close()));
  $$('.close-detail').forEach((button) => button.addEventListener('click', () => $('detailDialog').close()));
  $('missionDialog').addEventListener('click', (event) => { if (event.target === $('missionDialog')) $('missionDialog').close(); });
  $('detailDialog').addEventListener('click', (event) => { if (event.target === $('detailDialog')) $('detailDialog').close(); });

  $('missionFilters').addEventListener('click', (event) => {
    const button = event.target.closest('[data-filter]'); if (!button) return;
    state.filter = button.dataset.filter; $$('#missionFilters button').forEach((item) => item.classList.toggle('active', item === button)); renderMissions();
  });
  $('missionSearch').addEventListener('input', (event) => { state.search = event.target.value.trim(); renderMissions(); });

  $('provider').addEventListener('change', () => {
    if (!state.settings) return;
    const oldProvider = state.settings.provider;
    state.settings[providerModelKey(oldProvider)] = currentModel();
    const provider = $('provider').value;
    state.settings.provider = provider;
    $('chatModel').value = state.settings[providerModelKey(provider)] || '';
    updateProviderKeyStatus(); $('composerProvider').textContent = providerLabel(provider).toUpperCase();
  });
  $('humor').addEventListener('input', () => { $('humorValue').textContent = `${$('humor').value}%`; $('stageHumor').textContent = `${$('humor').value}%`; });
  $('saveAllSettings').addEventListener('click', saveSettings);
  $('saveKey').addEventListener('click', async () => {
    try {
      const provider = $('provider').value; const key = $('apiKey').value.trim();
      if (!key) throw new Error('Pegá una API key antes de guardar.');
      await api(`/api/secrets/${provider}/save`, {method: 'POST', body: JSON.stringify({api_key: key})});
      $('apiKey').value = ''; setInlineStatus(`Clave de ${providerLabel(provider)} guardada.`, 'ok');
      toast('Clave guardada', 'La credencial no volverá a mostrarse.', 'success'); await loadDashboard({silent: true}); populateSettings();
    } catch (error) { setInlineStatus(error.message, 'error'); }
  });
  $('deleteKey').addEventListener('click', async () => {
    const provider = $('provider').value;
    if (!confirm(`¿Borrar la clave guardada de ${providerLabel(provider)}?`)) return;
    try { await api(`/api/secrets/${provider}/delete`, {method: 'POST', body: '{}'}); toast('Clave eliminada', providerLabel(provider), 'success'); await loadDashboard({silent: true}); populateSettings(); }
    catch (error) { toast('No se pudo borrar', error.message, 'error'); }
  });
  $('testProvider').addEventListener('click', async () => {
    const button = $('testProvider'); button.disabled = true; setInlineStatus('Probando conexión…');
    try { const provider = $('provider').value; const data = await api(`/api/providers/${provider}/test`, {method: 'POST', body: '{}'}); setInlineStatus(`Conexión correcta · ${data.model_count} modelos.`, 'ok'); toast('Proveedor conectado', `${data.model_count} modelos disponibles.`, 'success'); }
    catch (error) { setInlineStatus(error.message, 'error'); }
    finally { button.disabled = false; }
  });
  $('loadModels').addEventListener('click', async () => {
    const box = $('modelsBox'); box.classList.remove('hidden'); box.textContent = 'Consultando modelos…';
    try { const data = await api(`/api/providers/${$('provider').value}/models`); renderModels(data.models || []); }
    catch (error) { box.textContent = error.message; }
  });

  $('voiceRate').value = localStorage.getItem('tarsVoiceRate') || '0.95';
  $('voiceRateValue').textContent = `${$('voiceRate').value}×`;
  $('voiceRate').addEventListener('input', () => { localStorage.setItem('tarsVoiceRate', $('voiceRate').value); $('voiceRateValue').textContent = `${$('voiceRate').value}×`; });
  $('voiceSelect').addEventListener('change', () => localStorage.setItem('tarsVoice', $('voiceSelect').value));
  $('handsFree').checked = localStorage.getItem('tarsHandsFree') === 'true';
  $('handsFree').addEventListener('change', () => localStorage.setItem('tarsHandsFree', String($('handsFree').checked)));
  $('testVoice').addEventListener('click', () => speak('TARS operativo. Voz configurada. El calendario sigue bajo observación.'));
  $('micButton').addEventListener('click', () => {
    if (!recognition) return;
    try { if (state.listening) recognition.stop(); else recognition.start(); }
    catch (error) { toast('Micrófono', error.message, 'error'); }
  });

  $('stopAll').addEventListener('click', stopAll);
  $('bannerAction').addEventListener('click', () => setView('settings'));
  window.addEventListener('online', () => toast('Conexión restaurada', 'Las APIs remotas vuelven a estar disponibles.', 'success'));
  window.addEventListener('offline', () => toast('Sin conexión a Internet', 'El servidor local sigue activo, pero los modelos remotos no responderán.', 'error', 7000));
}

async function init() {
  bindEvents(); setView(state.currentView, false); initRecognition();
  if ('speechSynthesis' in window) { populateVoices(); speechSynthesis.onvoiceschanged = populateVoices; }
  await loadDashboard(); await loadConversation();
  renderMissions(); renderApprovals(); renderActivity(); renderOutputs(); renderAgents();
  setInterval(() => loadDashboard({silent: true}), 2500);
}

init().catch((error) => {
  toast('Error de inicio', error.message, 'error', 9000);
  setTarsState('error', 'Error de inicio', error.message);
});
