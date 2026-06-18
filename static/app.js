const $ = (id) => document.getElementById(id);
let MODEL_PRESETS = [];
let LAST_BIOPSY = null;
let LAST_EVENTS = [];
let HEARTBEAT_TIMER = null;
let HEARTBEAT_IN_FLIGHT = false;
let HEARTBEAT_ENABLED = false;

function setStatus(text) { $('status').textContent = text; }
function setHeartbeatStatus(text) { if ($('heartbeatStatus')) $('heartbeatStatus').textContent = text; }
function pretty(x) { return JSON.stringify(x, null, 2); }
function compactTrace(events) {
  const keep = (events || []).slice(-80);
  const omitted = Math.max(0, (events || []).length - keep.length);
  return (omitted ? [{type: 'trace_compacted', omitted_events: omitted}] : []).concat(keep);
}
function setTraceFromEvents() {
  $('trace').textContent = pretty(compactTrace(LAST_EVENTS));
}

function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function nowStamp() { return new Date().toISOString().replace(/[:.]/g, '-'); }

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: {'Content-Type': 'application/json'},
    ...opts
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function getCachedProfile() {
  try { return JSON.parse(localStorage.getItem('soul_agent_profile') || '{}'); }
  catch { return {}; }
}

function cacheProfile() {
  const profile = {
    user_name: $('user_name').value.trim() || 'User',
    agent_name: $('agent_name').value.trim() || 'SoulAgent'
  };
  localStorage.setItem('soul_agent_profile', JSON.stringify(profile));
  return profile;
}

function renderIdentity() {
  const user = $('user_name').value.trim() || 'User';
  const agent = $('agent_name').value.trim() || 'SoulAgent';
  $('appTitle').textContent = agent;
  $('appSubtitle').textContent = `Local agent for ${user}: Soul.md + KG memory + user goals + agent goals + goal pulse + skills + todo queue + LLM key.`;
}

function providerDefaultModel(provider) {
  const preset = MODEL_PRESETS.find(p => p.provider === provider);
  if (preset) return preset.model;
  if (provider === 'anthropic') return 'claude-haiku-4-5';
  if (provider === 'ollama') return 'llama3.1:8b';
  if (provider === 'openai_compatible') return 'local-model';
  return 'gpt-5.5';
}

function renderModelPresets(provider, currentModel = '') {
  const select = $('model_preset');
  const presets = MODEL_PRESETS.filter(p => p.provider === provider);
  select.innerHTML = '<option value="">Custom / keep typed model</option>' + presets.map(p =>
    `<option value="${esc(p.model)}">${esc(p.label)} — ${esc(p.model)}</option>`
  ).join('');
  const match = presets.find(p => p.model === currentModel);
  select.value = match ? match.model : '';
}

function groupGoals(goals) {
  const groups = {user: [], shared: [], agent: []};
  for (const g of goals || []) {
    if (!groups[g.owner]) groups[g.owner] = [];
    groups[g.owner].push(g);
  }
  return groups;
}

function renderGoals(goals) {
  const groups = groupGoals(goals || []);
  const label = {user: 'User goals', shared: 'Shared / negotiated goals', agent: 'Agent self-goals'};
  $('goals').innerHTML = ['user', 'shared', 'agent'].map(owner => `
    <div class="goal-column goal-${esc(owner)}">
      <h3>${label[owner]}</h3>
      ${(groups[owner] || []).map(g => `
        <div class="goal-card status-${esc(g.status)}">
          <div class="goal-head"><strong>#${g.id} ${esc(g.title)}</strong><span class="pill">${esc(g.horizon)} · ${esc(g.priority)}</span></div>
          <div class="goal-body">${esc(g.body || '')}</div>
          <small>${esc(g.status)} ${g.due ? '· due ' + esc(g.due) : ''}${g.last_reviewed_at ? ' · reviewed ' + esc(g.last_reviewed_at) : ''}</small>
          <div class="goal-actions">
            <button class="tiny" data-goal-action="active" data-goal-id="${g.id}">active</button>
            <button class="tiny" data-goal-action="done" data-goal-id="${g.id}">done</button>
            <button class="tiny" data-goal-action="reviewed" data-goal-id="${g.id}">reviewed</button>
            <button class="tiny danger-mini" data-goal-action="dropped" data-goal-id="${g.id}">drop</button>
          </div>
        </div>
      `).join('') || '<p class="muted">No goals here yet.</p>'}
    </div>
  `).join('');
}

function renderHeartbeats(heartbeats) {
  return (heartbeats || []).map(h => `
    <div class="card"><strong>Heartbeat #${h.id} · ${esc(h.mode)}</strong><small>${esc(h.created_at)} ${h.run_id ? '· run #' + esc(h.run_id) : ''}</small><div>${esc((h.final || '').slice(0, 350))}</div></div>
  `).join('') || '<p class="muted">No heartbeats yet.</p>';
}

function renderWorkspace(files = [], preferredPath = '') {
  const select = $('workspaceFile');
  const flat = (files || []).filter(f => f.type === 'file');
  select.innerHTML = '<option value="">Select workspace file…</option>' + flat.map(f =>
    `<option value="${esc(f.path)}">${esc(f.path)} ${f.bytes != null ? '(' + esc(f.bytes) + ' bytes)' : ''}</option>`
  ).join('');
  if (preferredPath && flat.some(f => f.path === preferredPath)) select.value = preferredPath;
  else {
    const py = flat.find(f => String(f.path).toLowerCase().endsWith('.py'));
    if (py) select.value = py.path;
  }
}

function formatProgramRun(result) {
  if (!result) return 'No result.';
  const status = result.ok ? 'PASS' : 'FAIL';
  const lines = [];
  lines.push(`=== ${status}: ${result.path || ''} ===`);
  if ('returncode' in result) lines.push(`return code: ${result.returncode}`);
  if (result.error) lines.push(`error: ${result.error}`);
  lines.push('');
  lines.push('--- stdout ---');
  lines.push(result.stdout || '');
  lines.push('--- stderr ---');
  lines.push(result.stderr || '');
  return lines.join('\n');
}

function appendProgramOutput(text) {
  const box = $('programOutput');
  const current = box.textContent === 'No program run yet.' ? '' : box.textContent + '\n\n';
  box.textContent = current + text;
  box.scrollTop = box.scrollHeight;
}

function renderProgramRuns(runs = []) {
  const snippet = (runs || []).slice(0, 5).map(r => {
    const status = Number(r.ok) ? 'PASS' : 'FAIL';
    return `#${r.id} ${status} ${r.path} · ${r.created_at}\nstdout: ${(r.stdout || '').slice(0, 160)}\nstderr: ${(r.stderr || '').slice(0, 160)}`;
  }).join('\n\n');
  if (snippet && $('programOutput').textContent === 'No program run yet.') {
    $('programOutput').textContent = 'Recent program runs:\n\n' + snippet;
  }
}


function renderKgV2(kgv2) {
  if (!$('kgV2')) return;
  const inspect = kgv2?.inspect || kgv2 || {};
  const counts = inspect.counts || {};
  const nodes = inspect.nodes || kgv2?.results || [];
  const edges = inspect.edges || [];
  const hubs = inspect.hubs || [];
  $('kgV2Summary').innerHTML = `
    <div class="kg-stats">
      <span class="pill">nodes ${esc(counts.nodes || nodes.length || 0)}</span>
      <span class="pill">edges ${esc(counts.edges || edges.length || 0)}</span>
      <span class="pill">kinds ${esc(Object.keys(counts.by_kind || {}).length)}</span>
      <span class="pill">statuses ${esc(Object.keys(counts.by_status || {}).length)}</span>
    </div>
    <div class="muted">Hubs: ${(hubs || []).slice(0,5).map(h => `${esc(h.id)} (${esc(h.degree)})`).join(' · ') || 'none yet'}</div>
  `;
  $('kgV2').innerHTML = `
    <div class="kg-v2-column">
      <h3>Nodes</h3>
      ${(nodes || []).slice(0, 80).map(n => `
        <div class="kg-node status-${esc(n.status)}" data-node-id="${esc(n.id)}">
          <div class="kg-node-head"><code>${esc(n.id)}</code><span class="pill">${esc(n.kind)} · ${esc(n.status)}</span></div>
          <strong>${esc(n.title)}</strong>
          <div>${esc((n.body || n.summary || '').slice(0, 420))}</div>
          <small>hp ${esc(n.hp)} · score ${esc(n.score ?? '')} · conf ${esc(n.confidence ?? '')} · evidence +${esc((n.evidence_for || n.evidence?.for || []).length ?? n.evidence?.for ?? 0)}/-${esc((n.evidence_against || n.evidence?.against || []).length ?? n.evidence?.against ?? 0)} · ${esc(n.tags || '')}</small>
          <div class="kg-node-actions">
            <button class="tiny" data-copy-node="${esc(n.id)}">copy id</button>
            <button class="tiny" data-evidence-node="${esc(n.id)}">evidence here</button>
          </div>
        </div>
      `).join('') || '<p class="muted">No typed KG nodes yet.</p>'}
    </div>
    <div class="kg-v2-column">
      <h3>Edges</h3>
      ${(edges || []).slice(0, 80).map(e => `
        <div class="kg-edge"><code>${esc(e.src)}</code> <span class="pill">${esc(e.channel)} · ${esc(e.weight)}</span> <code>${esc(e.dst)}</code><small>${esc(e.source || '')} · ${esc(e.updated_at || e.created_at || '')}</small></div>
      `).join('') || '<p class="muted">No KG edges yet.</p>'}
      <h3>Lifecycle Events</h3>
      ${(inspect.events || []).slice(0, 20).map(ev => `
        <div class="card"><strong>${esc(ev.event_type)}</strong><small>${esc(ev.node_id)} · ${esc(ev.created_at)}</small><div>${esc(JSON.stringify(ev.detail || {}))}</div></div>
      `).join('') || '<p class="muted">No KG events yet.</p>'}
    </div>
  `;
}

async function loadState() {
  setStatus('refreshing…');
  const s = await api('/api/state');
  MODEL_PRESETS = s.model_presets || [];

  $('soul').value = s.soul_md || '';
  $('provider').value = s.settings?.provider || 'openai';
  $('model').value = s.settings?.model || providerDefaultModel($('provider').value);
  $('base_url').value = s.settings?.base_url || '';

  const hb = s.heartbeat_settings || {};
  $('heartbeatMode').value = hb.mode || 'work';
  $('heartbeatInterval').value = hb.interval_minutes || 15;
  $('heartbeatMaxSteps').value = hb.max_steps || 6;
  HEARTBEAT_ENABLED = !!hb.enabled;
  setHeartbeatStatus(HEARTBEAT_ENABLED ? 'goal pulse enabled in settings; click Start Pulse to resume this browser timer' : 'goal pulse idle');

  const cached = getCachedProfile();
  const serverProfile = s.profile || {};
  const userFromCache = cached.user_name && cached.user_name !== 'User';
  const agentFromCache = cached.agent_name && cached.agent_name !== 'SoulAgent';
  $('user_name').value = userFromCache ? cached.user_name : (serverProfile.user_name || 'User');
  $('agent_name').value = agentFromCache ? cached.agent_name : (serverProfile.agent_name || 'SoulAgent');
  renderIdentity();
  renderModelPresets($('provider').value, $('model').value);

  renderGoals(s.goals || []);

  $('todos').innerHTML = (s.todos || []).map(t => `
    <div class="card"><strong>#${t.id} ${esc(t.task)}</strong><small>${esc(t.status)} · ${esc(t.priority)} ${t.due ? '· due ' + esc(t.due) : ''}</small></div>
  `).join('') || '<p class="muted">No todos yet.</p>';

  $('kg').innerHTML = (s.kg || []).map(k => `
    <div class="card"><strong>${esc(k.subject)}</strong> — ${esc(k.predicate)} — <strong>${esc(k.object)}</strong><small>${esc(k.source)} · confidence ${esc(k.confidence)}</small></div>
  `).join('') || '<p class="muted">No triples yet.</p>';

  renderKgV2(s.kg_v2 || {});

  const cards = s.skill_cards?.skills || [];
  $('skillCards').innerHTML = cards.map(sk => `
    <div class="skill"><code>${esc(sk.id || sk.title)}</code><p><strong>${esc(sk.title || '')}</strong></p><p>${esc(sk.summary || '')}</p><small>${esc((sk.tools || []).join(', '))}</small></div>
  `).join('') || '<p class="muted">No skill cards loaded.</p>';

  $('skills').innerHTML = (s.skills || []).map(sk => `
    <div class="skill"><code>${esc(sk.name)}</code><p>${esc(sk.description)}</p></div>
  `).join('');

  $('runs').innerHTML = `
    <h3>Recent Runs</h3>
    ${(s.runs || []).map(r => `
      <div class="card"><strong>#${r.id} ${esc(r.goal)}</strong><small>${esc(r.created_at)}</small><div>${esc((r.final || '').slice(0, 400))}</div></div>
    `).join('') || '<p class="muted">No runs yet.</p>'}
    <h3>Recent Heartbeats</h3>
    ${renderHeartbeats(s.heartbeats || [])}
  `;

  $('notes').innerHTML = (s.notes || []).map(n => `
    <div class="card"><strong>${esc(n.title)}</strong><small>${esc(n.tags)} · ${esc(n.created_at)}</small><div>${esc((n.body || '').slice(0, 400))}</div></div>
  `).join('') || '<p class="muted">No notes yet.</p>';

  renderWorkspace(s.workspace || [], $('workspaceFile')?.value || '');
  renderProgramRuns(s.program_runs || []);
  setStatus('ready');
}

function payloadFromForm(goalOverride = null) {
  return {
    goal: goalOverride ?? $('goal').value.trim(),
    user_name: $('user_name').value,
    agent_name: $('agent_name').value,
    provider: $('provider').value,
    model: $('model').value,
    base_url: $('base_url').value,
    api_key: $('api_key').value,
    max_steps: Number($('max_steps').value || 6),
    temperature: Number($('temperature').value || 0.2),
    save_key: $('save_key').checked
  };
}

function heartbeatPayload() {
  return {
    ...payloadFromForm(''),
    mode: $('heartbeatMode').value || 'work',
    interval_minutes: Number($('heartbeatInterval').value || 15),
    max_steps: Number($('heartbeatMaxSteps').value || 6),
    heartbeat_note: $('heartbeatNote').value || ''
  };
}

function eventLabel(ev) {
  const type = ev.type || 'event';
  if (type === 'heartbeat_start') return `Goal pulse started: ${ev.mode || 'work'}`;
  if (type === 'heartbeat_saved') return `Goal pulse saved`;
  if (type === 'start') return `Started: ${ev.profile?.agent_name || 'agent'} for ${ev.profile?.user_name || 'user'}`;
  if (type === 'step_start') return `Step ${ev.step}: gathered context`;
  if (type === 'model_request') return `Step ${ev.step}: sent prompt to ${$('provider').value} / ${$('model').value}`;
  if (type === 'model_raw') return `Step ${ev.step}: raw model response received`;
  if (type === 'model_json') return `Step ${ev.step}: parsed model JSON`;
  if (type === 'tool_start') return `Step ${ev.step}: calling skill ${ev.tool}`;
  if (type === 'tool_result') return `Step ${ev.step}: skill ${ev.tool} returned`;
  if (type === 'program_auto_run_start') return `Step ${ev.step}: auto-running ${ev.args?.path || 'program'}`;
  if (type === 'program_auto_run_result') return `Step ${ev.step}: program output captured`;
  if (type === 'program_run_result') return `Program run output captured`;
  if (type === 'autofix_start') return `Auto-fix started for ${ev.path || 'program'}`;
  if (type === 'tool_error') return `Step ${ev.step}: skill ${ev.tool} failed`;
  if (type === 'final') return `Final answer saved`;
  if (type === 'stopped') return `Stopped at max steps`;
  if (type === 'error') return `Model step failed`;
  if (type === 'server_error') return `Server error`;
  return type;
}

function eventSummary(ev) {
  if (ev.type === 'heartbeat_start') return ev.mission || pretty(ev);
  if (ev.type === 'heartbeat_saved') return pretty(ev.heartbeat || ev);
  if (ev.type === 'step_start') return pretty(ev.context_summary || {});
  if (ev.type === 'model_request') return `Prompt size: ${ev.message_char_count || '?'} chars\n\nFull prompts are preserved in the Biopsy export.`;
  if (ev.type === 'model_raw') return String(ev.raw || '').slice(0, 3000);
  if (ev.type === 'model_json') return pretty(ev.model_json || {});
  if (ev.type === 'tool_start') return pretty({tool: ev.tool, args: ev.args});
  if (ev.type === 'tool_result') return pretty(ev.result);
  if (ev.type === 'program_auto_run_start') return pretty({args: ev.args, reason: ev.reason});
  if (ev.type === 'program_auto_run_result' || ev.type === 'program_run_result') return formatProgramRun(ev.result);
  if (ev.type === 'autofix_start') return pretty(ev);
  if (ev.type === 'tool_error') return pretty({error: ev.error, trace: ev.trace});
  if (ev.type === 'final' || ev.type === 'stopped' || ev.type === 'error' || ev.type === 'server_error') return pretty(ev);
  return pretty(ev);
}

function appendLiveEvent(ev) {
  const box = $('liveSteps');
  const div = document.createElement('div');
  div.className = `step event-${esc(ev.type || 'event')}`;
  const label = eventLabel(ev);
  const summary = eventSummary(ev);
  div.innerHTML = `
    <div class="step-head"><span class="pill">${esc(ev.type || 'event')}</span><strong>${esc(label)}</strong></div>
    <pre>${esc(summary)}</pre>
  `;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  if (ev.type === 'program_auto_run_result' || ev.type === 'program_run_result') {
    appendProgramOutput(formatProgramRun(ev.result));
  }
}

function updateBiopsyObject(extra = {}) {
  LAST_BIOPSY = {
    app_version: 'SoulAgentOS v12-stream-hygiene-controller',
    created_at_browser: new Date().toISOString(),
    current_form_without_api_key: {...payloadFromForm(), api_key: '[redacted/not exported]'},
    heartbeat_form: heartbeatPayload(),
    heartbeat_enabled_in_browser: HEARTBEAT_ENABLED,
    events: LAST_EVENTS,
    final_panel: $('final').textContent,
    trace_panel: $('trace').textContent,
    browser_location: window.location.href,
    note: 'Paste this JSON back into ChatGPT to diagnose the agent failure mode. It includes raw model output and prompts, goals and heartbeat history, but not the API key field.'
  };
  Object.assign(LAST_BIOPSY, extra);
}

async function streamEndpoint(path, payload, opts = {}) {
  const clear = opts.clear !== false;
  if (clear) {
    $('liveSteps').innerHTML = '';
    LAST_EVENTS = [];
  }
  updateBiopsyObject({events: LAST_EVENTS});
  const res = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || res.statusText);
  }
  if (!res.body) throw new Error('This browser did not expose a streaming response body.');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let done = false;
  while (!done) {
    const chunk = await reader.read();
    done = chunk.done;
    buffer += decoder.decode(chunk.value || new Uint8Array(), {stream: !done});
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      if (!line.trim()) continue;
      let ev;
      try { ev = JSON.parse(line); }
      catch (parseErr) {
        ev = {type: 'stream_parse_error', error: String(parseErr), line_preview: line.slice(0, 1200), line_chars: line.length};
      }
      LAST_EVENTS.push(ev);
      appendLiveEvent(ev);
      setTraceFromEvents();
      updateBiopsyObject();
      if (ev.final) $('final').textContent = ev.final;
      if (ev.type === 'error' || ev.type === 'server_error' || ev.type === 'tool_error' || ev.type === 'stream_parse_error') setStatus('error visible');
      else if (ev.type === 'final') setStatus('done');
      else if (ev.type === 'heartbeat_saved') setHeartbeatStatus('last beat saved at ' + new Date().toLocaleTimeString());
    }
  }
  if (buffer.trim()) {
    let ev;
    try { ev = JSON.parse(buffer); }
    catch (parseErr) { ev = {type: 'stream_parse_error', error: String(parseErr), line_preview: buffer.slice(0, 1200), line_chars: buffer.length}; }
    LAST_EVENTS.push(ev);
    appendLiveEvent(ev);
    setTraceFromEvents();
    if (ev.final) $('final').textContent = ev.final;
  }
  updateBiopsyObject();
}

async function runAgent() {
  const goal = $('goal').value.trim();
  if (!goal) return alert('Enter a goal first.');
  cacheProfile();
  await saveProfile(false).catch(() => {});

  $('runBtn').disabled = true;
  $('final').textContent = 'Running…';
  $('trace').textContent = 'Streaming trace will appear here.';
  setStatus('agent running');

  try {
    await streamEndpoint('/api/run_stream', payloadFromForm(goal), {clear: true});
    await loadState();
  } catch (e) {
    const err = {type: 'browser_error', error: e.message, stack: String(e.stack || e)};
    LAST_EVENTS.push(err);
    appendLiveEvent(err);
    $('final').textContent = 'Error: ' + e.message;
    setTraceFromEvents();
    updateBiopsyObject();
    setStatus('error');
  } finally {
    $('runBtn').disabled = false;
  }
}

async function saveProfile(showStatus = true) {
  const profile = cacheProfile();
  const data = await api('/api/profile', {method: 'POST', body: JSON.stringify(profile)});
  $('user_name').value = data.profile.user_name;
  $('agent_name').value = data.profile.agent_name;
  cacheProfile();
  renderIdentity();
  if (showStatus) setStatus('identity saved');
  return data;
}

function debouncedSaveProfile() {
  cacheProfile();
  renderIdentity();
  clearTimeout(window.__profileTimer);
  window.__profileTimer = setTimeout(() => saveProfile(false).then(() => setStatus('identity autosaved')).catch(() => setStatus('identity cached in browser')), 650);
}

async function refreshWorkspace(preferredPath = '') {
  const s = await api('/api/workspace');
  renderWorkspace(s.files || [], preferredPath);
  return s;
}

async function loadSelectedFile() {
  const path = $('workspaceFile').value;
  if (!path) return alert('Select a workspace file first.');
  const data = await api('/api/workspace_file?path=' + encodeURIComponent(path));
  $('labCode').value = data.file?.content || '';
  setStatus('loaded ' + path);
}

async function saveSelectedFile() {
  const path = $('workspaceFile').value;
  if (!path) return alert('Select a file first. To create a new file, let the agent write it or add it in workspace/ manually.');
  await api('/api/workspace_file', {method: 'POST', body: JSON.stringify({path, content: $('labCode').value})});
  await refreshWorkspace(path);
  setStatus('saved ' + path);
}

async function runSelectedProgram() {
  const path = $('workspaceFile').value;
  if (!path) return alert('Select a Python file first.');
  $('runProgramBtn').disabled = true;
  appendProgramOutput(`=== running ${path} ===`);
  try {
    const data = await api('/api/run_program', {method: 'POST', body: JSON.stringify({path, timeout_seconds: 15})});
    appendProgramOutput(formatProgramRun(data.result));
    setStatus(data.result?.ok ? 'program passed' : 'program failed; stderr captured');
    await refreshWorkspace(path);
  } catch (e) {
    appendProgramOutput('RUNNER ERROR: ' + e.message);
    setStatus('program runner error');
  } finally {
    $('runProgramBtn').disabled = false;
  }
}

async function autoFixSelectedProgram() {
  const path = $('workspaceFile').value;
  if (!path) return alert('Select a Python file first.');
  cacheProfile();
  await saveProfile(false).catch(() => {});
  $('autoFixBtn').disabled = true;
  $('final').textContent = 'Auto-fix running…';
  setStatus('auto-fix running');
  try {
    await streamEndpoint('/api/auto_debug_stream', {...payloadFromForm(''), path, timeout_seconds: 15, max_steps: Number($('max_steps').value || 6)}, {clear: true});
    await refreshWorkspace(path);
    await loadSelectedFile().catch(() => {});
    await loadState();
  } catch (e) {
    const err = {type: 'browser_error', error: e.message, stack: String(e.stack || e)};
    LAST_EVENTS.push(err);
    appendLiveEvent(err);
    $('final').textContent = 'Auto-fix error: ' + e.message;
    setTraceFromEvents();
    updateBiopsyObject();
    setStatus('auto-fix error');
  } finally {
    $('autoFixBtn').disabled = false;
  }
}


async function refreshKgV2() {
  const q = $('kgV2Search')?.value?.trim() || '';
  const kind = $('kgV2Kind')?.value || '';
  const status = $('kgV2Status')?.value || '';
  const url = `/api/kg_v2?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind)}&status=${encodeURIComponent(status)}`;
  const data = await api(url);
  if (data.mode === 'retrieve') {
    renderKgV2({inspect: data.inspect, results: data.results});
  } else {
    renderKgV2(data.inspect || {});
  }
  setStatus('typed KG refreshed');
}

async function addKgNodeV2() {
  const title = $('kgNodeTitle').value.trim();
  if (!title) return alert('Need a KG node title.');
  await api('/api/kg_node', {method:'POST', body: JSON.stringify({
    kind: $('kgNodeKind').value,
    status: $('kgNodeStatus').value,
    title,
    body: $('kgNodeBody').value.trim(),
    tags: $('kgNodeTags').value.trim(),
    source: 'manual'
  })});
  $('kgNodeTitle').value = '';
  $('kgNodeBody').value = '';
  $('kgNodeTags').value = '';
  await refreshKgV2();
}

async function addKgEdgeV2() {
  const src = $('kgEdgeSrc').value.trim(), dst = $('kgEdgeDst').value.trim();
  if (!src || !dst) return alert('Need source and target node ids.');
  await api('/api/kg_edge', {method:'POST', body: JSON.stringify({
    src, dst, channel: $('kgEdgeChannel').value, weight: Number($('kgEdgeWeight').value || 1), source: 'manual'
  })});
  $('kgEdgeSrc').value = $('kgEdgeDst').value = '';
  await refreshKgV2();
}

async function addKgEvidenceV2() {
  const node_id = $('kgEvidenceNode').value.trim();
  const evidence = $('kgEvidenceText').value.trim();
  if (!node_id || !evidence) return alert('Need node id and evidence text.');
  await api('/api/kg_evidence', {method:'POST', body: JSON.stringify({
    node_id, polarity: $('kgEvidencePolarity').value, evidence, source: 'manual'
  })});
  $('kgEvidenceText').value = '';
  await refreshKgV2();
}

async function kgLifecycleTick() {
  const data = await api('/api/kg_lifecycle', {method:'POST', body: JSON.stringify({})});
  renderKgV2(data.inspect || {});
  setStatus(`KG lifecycle: promoted ${data.result?.promoted?.length || 0}, retired ${data.result?.retired?.length || 0}`);
}

async function kgImportLegacy() {
  const data = await api('/api/kg_import_legacy', {method:'POST', body: JSON.stringify({})});
  renderKgV2(data.inspect || {});
  setStatus(`imported ${data.result?.imported || 0} legacy triples`);
}

async function saveSoul() {
  await api('/api/soul', {method: 'POST', body: JSON.stringify({soul_md: $('soul').value})});
  setStatus('Soul.md saved');
}

async function addTodo() {
  const task = $('todoTask').value.trim();
  if (!task) return;
  await api('/api/todo', {method: 'POST', body: JSON.stringify({task, priority: $('todoPriority').value})});
  $('todoTask').value = '';
  await loadState();
}

async function addGoal() {
  const title = $('goalTitle').value.trim();
  if (!title) return alert('Need a goal title.');
  await api('/api/goal', {method: 'POST', body: JSON.stringify({
    owner: $('goalOwner').value,
    horizon: $('goalHorizon').value,
    priority: $('goalPriority').value,
    title,
    body: $('goalBody').value.trim(),
    source: 'manual'
  })});
  $('goalTitle').value = '';
  $('goalBody').value = '';
  await loadState();
}

async function updateGoalStatus(id, action) {
  const payload = {id: Number(id)};
  if (action === 'reviewed') payload.mark_reviewed = true;
  else payload.status = action;
  await api('/api/goal_update', {method: 'POST', body: JSON.stringify(payload)});
  await loadState();
}

async function addKg() {
  const subject = $('kgS').value.trim(), predicate = $('kgP').value.trim(), object = $('kgO').value.trim();
  if (!subject || !predicate || !object) return alert('Need subject, predicate, object.');
  await api('/api/kg', {method: 'POST', body: JSON.stringify({subject, predicate, object})});
  $('kgS').value = $('kgP').value = $('kgO').value = '';
  await loadState();
}

async function doSearch() {
  const q = $('search').value.trim();
  if (!q) return loadState();
  const s = await api('/api/search?q=' + encodeURIComponent(q));
  $('kg').innerHTML = (s.kg || []).map(k => `
    <div class="card"><strong>${esc(k.subject)}</strong> — ${esc(k.predicate)} — <strong>${esc(k.object)}</strong><small>${esc(k.source)} · confidence ${esc(k.confidence)}</small></div>
  `).join('') || '<p class="muted">No KG hits.</p>';
}

async function saveHeartbeatSettings(enabled) {
  HEARTBEAT_ENABLED = !!enabled;
  return api('/api/heartbeat_settings', {method: 'POST', body: JSON.stringify({
    enabled: HEARTBEAT_ENABLED,
    interval_minutes: Number($('heartbeatInterval').value || 15),
    max_steps: Number($('heartbeatMaxSteps').value || 6),
    mode: $('heartbeatMode').value || 'work'
  })});
}

async function heartbeatOnce() {
  if (HEARTBEAT_IN_FLIGHT) {
    setHeartbeatStatus('goal pulse already in flight; skipping overlapping pulse');
    return;
  }
  HEARTBEAT_IN_FLIGHT = true;
  cacheProfile();
  await saveProfile(false).catch(() => {});
  $('final').textContent = 'Goal pulse running…';
  setStatus('goal pulse running');
  setHeartbeatStatus('goal pulse in flight…');
  try {
    await streamEndpoint('/api/heartbeat_stream', heartbeatPayload(), {clear: true});
    await loadState();
  } catch (e) {
    const err = {type: 'browser_error', error: e.message, stack: String(e.stack || e)};
    LAST_EVENTS.push(err);
    appendLiveEvent(err);
    $('final').textContent = 'Heartbeat error: ' + e.message;
    setTraceFromEvents();
    updateBiopsyObject();
    setStatus('goal pulse error');
    setHeartbeatStatus('goal pulse error');
  } finally {
    HEARTBEAT_IN_FLIGHT = false;
  }
}

async function startHeartbeat() {
  if (HEARTBEAT_TIMER) clearInterval(HEARTBEAT_TIMER);
  await saveHeartbeatSettings(true);
  HEARTBEAT_ENABLED = true;
  const minutes = Math.max(1, Number($('heartbeatInterval').value || 15));
  setHeartbeatStatus(`goal pulse running every ${minutes} minute(s) while this tab is open`);
  await heartbeatOnce();
  HEARTBEAT_TIMER = setInterval(() => {
    if (HEARTBEAT_ENABLED) heartbeatOnce();
  }, minutes * 60 * 1000);
}

async function stopHeartbeat() {
  HEARTBEAT_ENABLED = false;
  if (HEARTBEAT_TIMER) clearInterval(HEARTBEAT_TIMER);
  HEARTBEAT_TIMER = null;
  await saveHeartbeatSettings(false).catch(() => {});
  setHeartbeatStatus('goal pulse stopped');
  setStatus('goal pulse stopped');
}

async function buildBiopsy() {
  let server = {};
  try { server = await api('/api/biopsy/latest'); } catch (e) { server = {server_biopsy_error: e.message}; }
  updateBiopsyObject({server_snapshot: server});
  return LAST_BIOPSY || server;
}

async function downloadBiopsy() {
  const biopsy = await buildBiopsy();
  const blob = new Blob([pretty(biopsy)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `soul_agent_biopsy_${nowStamp()}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  setStatus('biopsy downloaded');
}

async function copyBiopsy() {
  const biopsy = await buildBiopsy();
  await navigator.clipboard.writeText(pretty(biopsy));
  setStatus('biopsy copied');
}

$('runBtn').addEventListener('click', runAgent);
$('refreshBtn').addEventListener('click', loadState);
$('saveProfileBtn').addEventListener('click', () => saveProfile(true));
$('saveSoulBtn').addEventListener('click', saveSoul);
$('addTodoBtn').addEventListener('click', addTodo);
$('addGoalBtn').addEventListener('click', addGoal);
$('addKgBtn').addEventListener('click', addKg);
$('heartbeatOnceBtn').addEventListener('click', heartbeatOnce);
$('heartbeatStartBtn').addEventListener('click', startHeartbeat);
$('heartbeatStopBtn').addEventListener('click', stopHeartbeat);
$('biopsyBtn').addEventListener('click', downloadBiopsy);
$('copyBiopsyBtn').addEventListener('click', copyBiopsy);
$('refreshWorkspaceBtn').addEventListener('click', () => refreshWorkspace().catch(err => alert(err.message)));
$('loadFileBtn').addEventListener('click', () => loadSelectedFile().catch(err => alert(err.message)));
$('saveFileBtn').addEventListener('click', () => saveSelectedFile().catch(err => alert(err.message)));
$('runProgramBtn').addEventListener('click', runSelectedProgram);
$('autoFixBtn').addEventListener('click', autoFixSelectedProgram);
$('clearProgramOutputBtn').addEventListener('click', () => { $('programOutput').textContent = 'No program run yet.'; });
$('workspaceFile').addEventListener('change', () => { if ($('workspaceFile').value) loadSelectedFile().catch(() => {}); });
$('search').addEventListener('input', () => { clearTimeout(window.__sTimer); window.__sTimer = setTimeout(doSearch, 250); });
$('user_name').addEventListener('input', debouncedSaveProfile);
$('agent_name').addEventListener('input', debouncedSaveProfile);
$('goals').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-goal-action]');
  if (!btn) return;
  updateGoalStatus(btn.dataset.goalId, btn.dataset.goalAction).catch(err => alert(err.message));
});
$('model_preset').addEventListener('change', () => {
  if ($('model_preset').value) $('model').value = $('model_preset').value;
});
$('provider').addEventListener('change', () => {
  const p = $('provider').value;
  $('model').value = providerDefaultModel(p);
  renderModelPresets(p, $('model').value);
  if (p === 'ollama') $('base_url').value = $('base_url').value || 'http://localhost:11434';
  if (p === 'openai_compatible') $('base_url').value = $('base_url').value || 'http://localhost:8000/v1';
});
$('model').addEventListener('input', () => renderModelPresets($('provider').value, $('model').value));


if ($('kgV2RetrieveBtn')) $('kgV2RetrieveBtn').addEventListener('click', () => refreshKgV2().catch(err => alert(err.message)));
if ($('kgNodeAddBtn')) $('kgNodeAddBtn').addEventListener('click', () => addKgNodeV2().catch(err => alert(err.message)));
if ($('kgEdgeAddBtn')) $('kgEdgeAddBtn').addEventListener('click', () => addKgEdgeV2().catch(err => alert(err.message)));
if ($('kgEvidenceAddBtn')) $('kgEvidenceAddBtn').addEventListener('click', () => addKgEvidenceV2().catch(err => alert(err.message)));
if ($('kgV2LifecycleBtn')) $('kgV2LifecycleBtn').addEventListener('click', () => kgLifecycleTick().catch(err => alert(err.message)));
if ($('kgV2ImportBtn')) $('kgV2ImportBtn').addEventListener('click', () => kgImportLegacy().catch(err => alert(err.message)));
if ($('kgV2')) $('kgV2').addEventListener('click', async (e) => {
  const copy = e.target.closest('[data-copy-node]');
  const ev = e.target.closest('[data-evidence-node]');
  if (copy) { await navigator.clipboard.writeText(copy.dataset.copyNode); setStatus('node id copied'); }
  if (ev) { $('kgEvidenceNode').value = ev.dataset.evidenceNode; $('kgEvidenceText').focus(); }
});

loadState().catch(e => { setStatus('load error'); console.error(e); });
