/* BLUEPRINT — frontend application logic
 * All data is fetched from /api/* endpoints — nothing is hardcoded.
 */

'use strict';

// ── DOM refs ──────────────────────────────────────────────────────────────────
const searchForm     = document.getElementById('search-form');
const addressInput   = document.getElementById('address-input');
const btnAnalyze     = document.getElementById('btn-analyze');
const sectionSearch  = document.getElementById('section-search');
const agentPanel     = document.getElementById('agent-panel');
const reportPanel    = document.getElementById('report-panel');
const errorPanel     = document.getElementById('error-panel');
const panelAddress   = document.getElementById('panel-address');
const liveLog        = document.getElementById('live-log');
const btnNewSearch   = document.getElementById('btn-new-search');
const btnRetry       = document.getElementById('btn-retry');
const btnHiw         = document.getElementById('btn-how');
const btnTheme       = document.getElementById('btn-theme');
const modalHiw       = document.getElementById('modal-hiw');
const modalClose     = document.getElementById('modal-close');

// ── State ─────────────────────────────────────────────────────────────────────
let currentAddress     = '';
let currentAddressHash = '';
let currentWatched     = false;
let activeEventSource  = null;
let currentFilter      = 'all';
let allTimelineItems   = [];

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadStats();
  loadHealth();
  loadAbout();
  setInterval(loadStats, 30_000);
  applyTheme(localStorage.getItem('bp-theme') || 'dark');

  // Demo preset buttons
  document.querySelectorAll('.btn-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      addressInput.value = btn.dataset.address;
      addressInput.focus();
    });
  });

  // Timeline filters
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentFilter = btn.dataset.filter;
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterTimeline();
    });
  });

  // New search / retry
  btnNewSearch.addEventListener('click', showSearch);
  btnRetry.addEventListener('click', showSearch);

  // How it works modal
  btnHiw.addEventListener('click', async () => {
    show(modalHiw);
    await _ensureHiwRendered();
  });
  modalClose.addEventListener('click', () => hide(modalHiw));
  modalHiw.addEventListener('click', e => { if (e.target === modalHiw) hide(modalHiw); });

  // HIW tab switching
  document.getElementById('hiw-tabs')?.addEventListener('click', e => {
    const btn = e.target.closest('.hiw-tab');
    if (!btn) return;
    const tab = btn.dataset.tab;
    document.querySelectorAll('.hiw-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.hiw-panel').forEach(p => hide(p));
    const panel = document.getElementById(`hiw-panel-${tab}`);
    if (panel) show(panel);
  });

  // Share button
  document.getElementById('btn-share-report').addEventListener('click', () => {
    if (currentAddressHash) shareReport(currentAddressHash);
  });

  // Watch button
  document.getElementById('btn-watch-property').addEventListener('click', () => {
    if (currentAddressHash) toggleWatch(currentAddressHash, currentAddress);
  });

  // Export button
  document.getElementById('btn-export-report').addEventListener('click', () => {
    if (currentAddressHash) exportReport(currentAddressHash);
  });

  // Share modal close
  document.getElementById('modal-share-close').addEventListener('click', () => {
    hide(document.getElementById('modal-share'));
  });
  document.getElementById('modal-share').addEventListener('click', e => {
    if (e.target === document.getElementById('modal-share')) hide(document.getElementById('modal-share'));
  });
  document.getElementById('btn-copy-link').addEventListener('click', copyShareLink);

  // Watchlist modal
  document.getElementById('btn-watchlist-nav').addEventListener('click', openWatchlist);
  document.getElementById('modal-watchlist-close').addEventListener('click', () => {
    hide(document.getElementById('modal-watchlist'));
  });
  document.getElementById('modal-watchlist').addEventListener('click', e => {
    if (e.target === document.getElementById('modal-watchlist')) hide(document.getElementById('modal-watchlist'));
  });

  // Theme
  btnTheme.addEventListener('click', toggleTheme);

  // Q&A panel
  initQA();

  // Compare modal
  initCompare();

  // Inline HIW button (inside how-section on landing page)
  document.getElementById('btn-hiw-inline')?.addEventListener('click', async () => {
    show(modalHiw);
    await _ensureHiwRendered();
  });

  // Form submit
  searchForm.addEventListener('submit', e => {
    e.preventDefault();
    const address = addressInput.value.trim();
    if (address.length < 5) return;
    startAnalysis(address);
  });
});

// ── Health + About data (cached) ──────────────────────────────────────────────
let _healthData  = null;
let _aboutData   = null;
let _aboutLoaded = false;

async function loadHealth() {
  try {
    const res = await fetch('/api/health');
    if (!res.ok) return;
    _healthData = await res.json();

    // Populate Gemini model badge
    const geminiEl = document.getElementById('stat-gemini');
    const pipelineEl = document.getElementById('stat-pipeline-label');
    if (geminiEl && _healthData.gemini_model) {
      geminiEl.textContent = _healthData.gemini_model;
    }
    if (pipelineEl && _healthData.agents) {
      pipelineEl.textContent = `${_healthData.agents}-agent ADK pipeline`;
    }
    const agentCountEl = document.getElementById('panel-agent-count');
    if (agentCountEl && _healthData.agents) {
      agentCountEl.textContent = _healthData.agents;
    }

    // Populate integration strip
    const geminiNameEl = document.getElementById('int-gemini-name');
    const geminiDetailEl = document.getElementById('int-gemini-detail');
    const elasticDetailEl = document.getElementById('int-elastic-detail');
    if (geminiNameEl && _healthData.gemini_model) {
      geminiNameEl.textContent = `Google ADK + ${_healthData.gemini_model}`;
    }
    if (geminiDetailEl && _healthData.agents) {
      const fallback = _healthData.fallback_model ? ` · Fallback: ${_healthData.fallback_model}` : '';
      geminiDetailEl.textContent = `${_healthData.agents}-agent SequentialAgent · Adversarial debate · SSE streaming${fallback}`;
    }
    if (elasticDetailEl && _healthData.elastic_capabilities) {
      elasticDetailEl.textContent = _healthData.elastic_capabilities
        .map(c => c.replace(/_/g, ' '))
        .map(c => c.charAt(0).toUpperCase() + c.slice(1))
        .join(' · ');
    }
  } catch (_) { /* silently ignore */ }
}

async function loadAbout() {
  if (_aboutLoaded) return _aboutData;
  try {
    const res = await fetch('/api/about');
    if (!res.ok) return null;
    _aboutData   = await res.json();
    _aboutLoaded = true;

    // Render educational sections
    _renderValidatedExamples(_aboutData.validated_examples || []);
    _renderWhySection(_aboutData.impact_stats || []);
    _renderHowSection(_aboutData.how_steps || [], _aboutData.debate_callout || '');
    _renderChecksGrid(_aboutData.risk_factors || []);
    _renderTrustStripInline(_aboutData.data_sources || []);
  } catch (_) {}
  return _aboutData;
}

// ── Stats ─────────────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) return;
    const d = await res.json();
    if (typeof d.total_analyses === 'number') animateStat('stat-total', d.total_analyses);
    if (typeof d.analyses_24h  === 'number') animateStat('stat-24h',   d.analyses_24h);

    const elasticEl = document.getElementById('stat-elastic');
    if (elasticEl) {
      elasticEl.textContent = d.elastic_mcp_active ? 'Elastic MCP ✓' : 'Elastic SDK';
    }
  } catch (_) { /* silently ignore */ }
}

function animateStat(id, target) {
  const el = document.getElementById(id);
  if (!el || typeof target !== 'number') return;
  const start = parseInt(el.textContent.replace(/\D/g, '')) || 0;
  const diff  = target - start;
  if (diff === 0) return;
  const dur = 600;
  const t0  = performance.now();
  const tick = (now) => {
    const pct = Math.min((now - t0) / dur, 1);
    el.textContent = Math.round(start + diff * easeOut(pct)).toLocaleString();
    if (pct < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
const easeOut = t => 1 - Math.pow(1 - t, 3);

// ── Analysis flow ─────────────────────────────────────────────────────────────
function startAnalysis(address) {
  currentAddress = address;
  if (activeEventSource) { activeEventSource.close(); activeEventSource = null; }

  // Reset UI
  resetAgentCards();
  liveLog.innerHTML = '';
  panelAddress.textContent = address;
  hide(sectionSearch);
  hide(reportPanel);
  hide(errorPanel);
  show(agentPanel);
  btnAnalyze.disabled = true;

  const url = '/api/analyze/stream?address=' + encodeURIComponent(address);
  const es = new EventSource(url);
  activeEventSource = es;

  es.onmessage = e => {
    if (e.data === '[DONE]') { es.close(); btnAnalyze.disabled = false; return; }
    try { handleEvent(JSON.parse(e.data)); } catch (_) {}
  };

  es.onerror = () => {
    es.close();
    btnAnalyze.disabled = false;
    showError('Connection lost. Please try again.');
  };
}

function handleEvent(evt) {
  const { type, agent, message, detail, count, summary, report, error } = evt;

  if (type === 'start') {
    addLog(`▶ Starting analysis: ${evt.address || currentAddress}`, 'info');
    return;
  }

  if (type === 'step') {
    updateAgentCard(agent, message, 'running');
    addLog(`[${agent || '?'}] ${message}${detail ? ' — ' + detail : ''}`, detail && detail.includes('⚠') ? 'warn' : 'info');
    return;
  }

  if (type === 'finding') {
    updateAgentCard(agent, summary, 'done');
    addLog(`✓ ${summary}`, 'finding');
    return;
  }

  if (type === 'complete') {
    markAllDone();
    addLog('✓ Analysis complete — rendering report', 'finding');
    if (report) renderReport(report);
    return;
  }

  if (type === 'debate_complete') {
    if (evt.debate) renderDebate(evt.debate);
    return;
  }

  if (type === 'error') {
    showError(error || 'An unexpected error occurred.');
    return;
  }
}

// ── Agent cards ────────────────────────────────────────────────────────────────
const AGENT_ORDER = ['GeocoderAgent', 'DeedAgent', 'PermitAgent', 'ClimateAgent', 'NeighborhoodAgent', 'SynthesisAgent', 'DebateAgent'];

function resetAgentCards() {
  AGENT_ORDER.forEach(name => {
    const card  = document.getElementById('agent-' + name);
    const badge = card?.querySelector('.agent-badge');
    const status = card?.querySelector('.agent-status');
    if (badge)  { badge.className = 'agent-badge pending'; badge.textContent = 'pending'; }
    if (status) { status.textContent = 'Waiting…'; }
    if (card)   { card.className = 'agent-card'; }
  });
}

function updateAgentCard(agent, message, state) {
  if (!agent) return;
  const card  = document.getElementById('agent-' + agent);
  if (!card) return;
  const badge  = card.querySelector('.agent-badge');
  const status = card.querySelector('.agent-status');
  if (badge)  { badge.className = `agent-badge ${state}`; badge.textContent = state; }
  if (status) { status.textContent = message || state; }
  card.className = `agent-card ${state}`;
}

function markAllDone() {
  AGENT_ORDER.forEach(name => updateAgentCard(name, 'Complete', 'done'));
}

// ── Live log ───────────────────────────────────────────────────────────────────
function addLog(text, cls = 'info') {
  const line = document.createElement('div');
  line.className = `log-line ${cls}`;
  line.textContent = text;
  liveLog.appendChild(line);
  liveLog.scrollTop = liveLog.scrollHeight;
}

// ── Report rendering ───────────────────────────────────────────────────────────
function renderReport(report) {
  hide(agentPanel);
  show(reportPanel);

  // Track current report for action buttons
  currentAddressHash = report.address_hash || '';
  if (currentAddressHash) checkWatchState(currentAddressHash);

  // Address + meta
  document.getElementById('report-address').textContent = report.normalized_address || currentAddress;
  const meta = [];
  if (report.created_at) meta.push(new Date(report.created_at).toLocaleString());
  if (report.address_hash) meta.push(`ID: ${report.address_hash}`);
  document.getElementById('report-meta').textContent = meta.join(' · ');

  // Risk score gauge — animated fill from 0 → score
  const score = typeof report.buyer_risk_score === 'number' ? report.buyer_risk_score : 50;
  document.getElementById('risk-score-value').textContent = '0';
  renderGauge(score, true);  // animated; also counts up the number
  const riskBadge = document.getElementById('risk-level-badge');
  riskBadge.textContent = report.risk_level || 'UNKNOWN';
  riskBadge.className = `risk-level-badge ${report.risk_level || 'MEDIUM'}`;
  document.getElementById('risk-summary').textContent = report.summary || '';

  // Flags
  const flags = Array.isArray(report.flags) ? report.flags : [];
  if (flags.length > 0) {
    const list = document.getElementById('flags-list');
    list.innerHTML = flags.map(f => `<li>${esc(f)}</li>`).join('');
    show(document.getElementById('flags-section'));
  }

  // Diligence questions
  const questions = Array.isArray(report.diligence_questions) ? report.diligence_questions : [];
  if (questions.length > 0) {
    const list = document.getElementById('questions-list');
    list.innerHTML = questions.map(q => `<li>${esc(q)}</li>`).join('');
    show(document.getElementById('questions-section'));
  }

  // Timeline
  const timeline = Array.isArray(report.timeline) ? report.timeline : [];
  if (timeline.length > 0) {
    allTimelineItems = timeline;
    renderTimeline(timeline);
    document.getElementById('timeline-count').textContent = `(${timeline.length} events)`;
    show(document.getElementById('timeline-section'));
  }

  // Positive signals
  const positives = Array.isArray(report.positive_signals) ? report.positive_signals : [];
  if (positives.length > 0) {
    const list = document.getElementById('positive-list');
    list.innerHTML = positives.map(p => `<li>${esc(p)}</li>`).join('');
    show(document.getElementById('positive-section'));
  }

  // Data sources
  const sources = Array.isArray(report.data_sources) ? report.data_sources : [];
  if (sources.length > 0) {
    const wrap = document.getElementById('sources-list');
    wrap.innerHTML = sources.map(s => `<span class="source-tag">${esc(s)}</span>`).join('');
    show(document.getElementById('sources-section'));
  }

  // Escape Plan
  if (Array.isArray(report.escape_plan) && report.escape_plan.length > 0) {
    renderEscapePlan(report.escape_plan);
  }

  // Neighbourhood
  renderNeighborhood(report);

  // Debate (may arrive later via debate_complete event — pre-render if already in report)
  if (report.debate) renderDebate(report.debate);

  // Score explainer (from /api/about)
  if (report.risk_level) _renderScoreExplainer(report.risk_level);

  // Provenance strip (from /api/about, cross-referenced with report.data_sources)
  if (Array.isArray(report.data_sources) && report.data_sources.length > 0) {
    _renderProvenanceStrip(report.data_sources);
  }

  // Interactive map — uses lat/lng/climate_risk now embedded in report_doc
  if (report.lat && report.lng) renderMap(report);

  // Cross-property intelligence — load similar risk profiles from Elastic memory layer
  if (currentAddressHash) loadSimilarProperties(currentAddressHash);

  // Reload stats after report is generated
  loadStats();
}

function renderTimeline(items) {
  const container = document.getElementById('timeline');
  container.innerHTML = '';
  items.forEach(item => {
    const div = document.createElement('div');
    div.className = 'timeline-item';
    div.dataset.type = item.event_type || 'unknown';

    const flags = Array.isArray(item.flags) && item.flags.length > 0
      ? `<div class="tl-flags">${item.flags.map(f => `<span class="tl-flag">${esc(f)}</span>`).join('')}</div>`
      : '';

    const amount = item.amount && item.amount > 0
      ? `<div class="tl-amount">${formatCurrency(item.amount)}</div>`
      : '';

    div.innerHTML = `
      <div class="tl-date">${esc(item.date || '—')}</div>
      <div class="tl-body">
        <div class="tl-desc">${esc(item.description || '')}</div>
        ${item.source ? `<div class="tl-source">Source: ${esc(item.source)}</div>` : ''}
        ${flags}
      </div>
      ${amount}
    `;
    container.appendChild(div);
  });
}

function filterTimeline() {
  const items = document.querySelectorAll('#timeline .timeline-item');
  items.forEach(item => {
    const type = item.dataset.type || '';
    const visible = currentFilter === 'all' || type === currentFilter ||
      (currentFilter === 'climate_flood' && type.startsWith('climate'));
    item.classList.toggle('hidden', !visible);
  });
}

// ── Debate rendering ──────────────────────────────────────────────────────────
function renderDebate(debate) {
  const section = document.getElementById('debate-section');
  if (!section) return;

  const rec = debate.buy_recommendation || 'NEGOTIATE';
  const conf = debate.confidence || 'MEDIUM';
  const verdictText = debate.verdict_text || debate.verdict?.verdict || '';
  const finalScore = debate.final_score;

  const recColor = rec === 'BUY' ? 'var(--green)' : rec === 'AVOID' ? 'var(--red)' : 'var(--yellow)';
  const recEmoji = rec === 'BUY' ? '✅' : rec === 'AVOID' ? '🚫' : '🤝';

  document.getElementById('debate-verdict').innerHTML = `
    <div class="verdict-badge" style="color:${recColor}">${recEmoji} ${rec}</div>
    <div class="verdict-conf">Confidence: <strong>${conf}</strong>${finalScore !== undefined ? ` · Final score: <strong>${finalScore}/100</strong>` : ''}</div>
    <p class="verdict-text">${esc(verdictText)}</p>
  `;

  const opt = debate.optimist || {};
  const pes = debate.pessimist || {};

  if (opt.argument) {
    document.getElementById('optimist-body').textContent = opt.argument;
    document.getElementById('optimist-score').textContent = `Suggested score: ${opt.adjusted_score ?? '—'}/100`;
    show(document.getElementById('debate-optimist'));
  }
  if (pes.argument) {
    document.getElementById('pessimist-body').textContent = pes.argument;
    document.getElementById('pessimist-score').textContent = `Suggested score: ${pes.adjusted_score ?? '—'}/100`;
    show(document.getElementById('debate-pessimist'));
  }

  // Animate gauge to the debated final score
  if (finalScore !== undefined) {
    renderGauge(finalScore, true);
  }

  show(section);
}

// ── Neighbourhood rendering ───────────────────────────────────────────────────
function renderNeighborhood(report) {
  // neighbourhood data may come from report.neighborhood or direct fields
  const n = report.neighborhood || {};
  if (!Object.keys(n).length) return;

  const grid = document.getElementById('neighborhood-grid');
  const section = document.getElementById('neighborhood-section');
  if (!grid || !section) return;

  const items = [
    { icon: '💨', label: 'Air Quality (PM2.5)', value: n.pm25 != null ? `${n.pm25.toFixed(1)} µg/m³` : '—', ok: (n.pm25 || 0) <= 12 },
    { icon: '☣', label: 'Superfund Proximity', value: n.superfund_proximity != null ? `${Math.round(n.superfund_proximity)}/100` : '—', ok: (n.superfund_proximity || 0) < 40 },
    { icon: '🚗', label: 'Traffic Pollution', value: n.traffic_proximity != null ? `${Math.round(n.traffic_proximity)}/100` : '—', ok: (n.traffic_proximity || 0) < 60 },
    { icon: '🏫', label: 'Schools Nearby', value: n.schools_nearby != null ? `${n.schools_nearby}` : '—', ok: (n.schools_nearby || 0) > 0 },
    { icon: '🌳', label: 'Parks Nearby', value: n.parks_nearby != null ? `${n.parks_nearby}` : '—', ok: (n.parks_nearby || 0) > 0 },
    { icon: '🚌', label: 'Transit Stops', value: n.transit_nearby != null ? `${n.transit_nearby}` : '—', ok: (n.transit_nearby || 0) > 1 },
    { icon: '🏘', label: 'Neighbourhood Score', value: n.neighborhood_score != null ? `${n.neighborhood_score}/100` : '—', ok: (n.neighborhood_score || 0) >= 60 },
  ];

  grid.innerHTML = items.map(item => `
    <div class="nbhd-card ${item.ok ? 'good' : 'warn'}">
      <div class="nbhd-icon">${item.icon}</div>
      <div class="nbhd-label">${item.label}</div>
      <div class="nbhd-value">${item.value}</div>
    </div>
  `).join('');

  show(section);
}

// ── Escape Plan rendering ─────────────────────────────────────────────────────
function renderEscapePlan(steps) {
  if (!Array.isArray(steps) || steps.length === 0) return;
  const list = document.getElementById('escape-list');
  const section = document.getElementById('escape-section');
  if (!list || !section) return;
  list.innerHTML = steps.map(s => `<li>${esc(s)}</li>`).join('');
  show(section);
}

// ── Neighborhood map (Leaflet.js) ────────────────────────────────────────────
let _map = null;

function renderMap(report) {
  const lat = report.lat;
  const lng = report.lng;
  const section = document.getElementById('map-section');
  const mapEl   = document.getElementById('property-map');
  if (!lat || !lng || !section || !mapEl || typeof L === 'undefined') return;

  // Tear down previous instance so re-runs don't crash
  if (_map) { _map.remove(); _map = null; }

  show(section);

  // Leaflet must be initialised after the element is visible
  requestAnimationFrame(() => {
    _map = L.map('property-map', { zoomControl: true, scrollWheelZoom: false })
            .setView([lat, lng], 15);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(_map);

    // Risk colour
    const riskColors = { LOW: '#22c55e', MEDIUM: '#eab308', HIGH: '#f97316', CRITICAL: '#ef4444' };
    const level  = report.risk_level || 'MEDIUM';
    const color  = riskColors[level] || '#64748b';
    const score  = report.buyer_risk_score ?? '—';

    // Property pin
    const icon = L.divIcon({
      className: '',
      html: `<div style="background:${color};width:22px;height:22px;border-radius:50%;border:3px solid white;box-shadow:0 2px 10px rgba(0,0,0,0.45);"></div>`,
      iconSize: [22, 22], iconAnchor: [11, 11],
    });
    L.marker([lat, lng], { icon })
     .addTo(_map)
     .bindPopup(`<strong>${esc(report.normalized_address || '')}</strong><br>Risk Score: <strong>${score}/100</strong> · ${level}`)
     .openPopup();

    // 500m analysis radius
    L.circle([lat, lng], {
      radius: 500, color, fillColor: color, fillOpacity: 0.05,
      weight: 1.5, dashArray: '5 5',
    }).addTo(_map).bindTooltip('500 m analysis radius', { permanent: false });

    // FEMA flood zone overlay
    const floodZone = (report.climate_risk || {}).flood_zone || '';
    if (floodZone && floodZone !== 'X' && floodZone !== 'UNKNOWN') {
      L.circle([lat, lng], {
        radius: 150, color: '#3b82f6', fillColor: '#3b82f6', fillOpacity: 0.12, weight: 2,
      }).addTo(_map).bindPopup(`FEMA Flood Zone: <strong>${esc(floodZone)}</strong>`);
    }

    // Map legend
    const legendEl = document.getElementById('map-legend');
    if (legendEl) {
      const fz = floodZone && floodZone !== 'X' && floodZone !== 'UNKNOWN'
        ? `<span class="map-legend-item"><span style="background:#3b82f6" class="map-legend-dot"></span>FEMA Zone ${esc(floodZone)}</span>` : '';
      legendEl.innerHTML = `
        <span class="map-legend-item"><span style="background:${color}" class="map-legend-dot"></span>${level} risk · ${score}/100</span>
        <span class="map-legend-item"><span class="map-legend-ring" style="border-color:${color}"></span>500m analysis radius</span>
        ${fz}
      `;
    }

    _map.invalidateSize();
  });
}

// ── Cross-property intelligence (Elastic memory layer) ────────────────────────
async function loadSimilarProperties(addressHash) {
  try {
    const res = await fetch(`/api/similar/${encodeURIComponent(addressHash)}`);
    if (!res.ok) return;
    const data = await res.json();
    if (!data.properties || data.properties.length === 0) return;
    renderSimilarProperties(data);
  } catch (_) { /* silently ignore — not critical */ }
}

function renderSimilarProperties(data) {
  const section = document.getElementById('similar-section');
  const grid    = document.getElementById('similar-grid');
  const badge   = document.getElementById('similar-badge');
  if (!section || !grid) return;

  const riskColors = { LOW: '#22c55e', MEDIUM: '#eab308', HIGH: '#f97316', CRITICAL: '#ef4444' };
  const props = data.properties || [];

  if (badge) badge.textContent = `${props.length} found`;

  grid.innerHTML = props.map(p => {
    const lvl   = p.risk_level || 'UNKNOWN';
    const color = riskColors[lvl] || '#64748b';
    const flags = (p.flags || []).slice(0, 2);
    const loc   = [p.county, p.state].filter(Boolean).join(', ');
    return `
      <div class="similar-card">
        <div class="similar-score" style="color:${color}">${p.buyer_risk_score ?? '—'}<span>/100</span></div>
        <div class="similar-level" style="color:${color}">${esc(lvl)}</div>
        <div class="similar-addr">${esc(p.normalized_address || p.address_hash || '—')}</div>
        ${loc ? `<div class="similar-loc">${esc(loc)}</div>` : ''}
        ${flags.map(f => `<div class="similar-flag">⚠ ${esc(f)}</div>`).join('')}
      </div>
    `;
  }).join('');

  show(section);
}

// ── Q&A panel ─────────────────────────────────────────────────────────────────
function initQA() {
  const btn   = document.getElementById('btn-qa-send');
  const input = document.getElementById('qa-input');
  if (!btn || !input) return;

  const send = async () => {
    const q = input.value.trim();
    if (!q || q.length < 3) return;
    input.value = '';
    addQAMessage(q, 'user');
    const thinkingId = addQAMessage('Thinking…', 'assistant thinking');

    try {
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address_hash: currentAddressHash, question: q }),
      });
      const data = await res.json();
      removeQAMessage(thinkingId);
      addQAMessage(data.answer || 'No answer returned.', 'assistant');
    } catch (err) {
      removeQAMessage(thinkingId);
      addQAMessage('Could not get an answer — please try again.', 'assistant error');
    }
  };

  btn.addEventListener('click', send);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });
}

let _qaMsgId = 0;
function addQAMessage(text, role) {
  const id = 'qa-msg-' + (++_qaMsgId);
  const wrap = document.getElementById('qa-messages');
  if (!wrap) return id;
  const div = document.createElement('div');
  div.id = id;
  div.className = `qa-msg qa-${role.split(' ')[0]}`;
  div.textContent = text;
  wrap.appendChild(div);
  wrap.scrollTop = wrap.scrollHeight;
  return id;
}
function removeQAMessage(id) {
  document.getElementById(id)?.remove();
}

// ── Risk gauge SVG ────────────────────────────────────────────────────────────
let _gaugeAnimFrame = null;

function renderGauge(score, animate = true) {
  const fill = document.getElementById('gauge-fill');
  const dot  = document.getElementById('gauge-dot');
  const scoreEl = document.getElementById('risk-score-value');
  if (!fill || !dot) return;

  if (_gaugeAnimFrame) { cancelAnimationFrame(_gaugeAnimFrame); _gaugeAnimFrame = null; }

  const total = 251.2;
  const targetPct = Math.max(0, Math.min(100, score)) / 100;

  const setGaugeAt = (pct) => {
    fill.style.strokeDasharray = `${total * pct} ${total}`;
    const angle = Math.PI * pct;
    dot.setAttribute('cx', (100 - 80 * Math.cos(angle)).toFixed(1));
    dot.setAttribute('cy', (100 - 80 * Math.sin(angle)).toFixed(1));
  };

  if (!animate) { setGaugeAt(targetPct); return; }

  const startScore = parseInt(scoreEl?.textContent) || 0;
  const t0 = performance.now();
  const dur = 1600;
  const ease = t => 1 - Math.pow(1 - t, 3);

  const tick = (now) => {
    const p = Math.min((now - t0) / dur, 1);
    const ep = ease(p);
    setGaugeAt(ep * targetPct);
    if (scoreEl) scoreEl.textContent = Math.round(startScore + ep * (score - startScore));
    if (p < 1) _gaugeAnimFrame = requestAnimationFrame(tick);
    else _gaugeAnimFrame = null;
  };
  _gaugeAnimFrame = requestAnimationFrame(tick);
}

// ── Utility ────────────────────────────────────────────────────────────────────
function show(el) { if (el) el.style.display = ''; }
function hide(el) { if (el) el.style.display = 'none'; }

function showSearch() {
  hide(agentPanel);
  hide(reportPanel);
  hide(errorPanel);
  show(sectionSearch);
  addressInput.focus();
}

function showError(msg) {
  hide(agentPanel);
  hide(reportPanel);
  document.getElementById('error-message').textContent = msg;
  show(errorPanel);
  show(sectionSearch);
}

function esc(str) {
  if (typeof str !== 'string') return String(str ?? '');
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatCurrency(n) {
  if (!n || isNaN(n)) return '';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n);
}

// ── Theme ─────────────────────────────────────────────────────────────────────
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  btnTheme.textContent = theme === 'dark' ? '☀' : '☾';
  localStorage.setItem('bp-theme', theme);
}
function toggleTheme() {
  applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
}

// ── Share ─────────────────────────────────────────────────────────────────────
async function shareReport(addressHash) {
  const modal = document.getElementById('modal-share');
  const input = document.getElementById('share-url-input');
  input.value = 'Generating link…';
  show(modal);
  try {
    const res = await fetch(`/api/share/${encodeURIComponent(addressHash)}`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    input.value = data.url || '';
    toast('Share link created — valid for 90 days', 'success');
  } catch (err) {
    input.value = '';
    toast('Could not create share link: ' + err.message, 'error');
  }
}

function copyShareLink() {
  const input = document.getElementById('share-url-input');
  if (!input.value || input.value.includes('Generating')) return;
  try {
    navigator.clipboard.writeText(input.value).then(() => {
      toast('Link copied to clipboard', 'success');
      document.getElementById('btn-copy-link').textContent = 'Copied!';
      setTimeout(() => { document.getElementById('btn-copy-link').textContent = 'Copy'; }, 2000);
    });
  } catch (_) {
    input.select();
    document.execCommand('copy');
    toast('Link copied', 'success');
  }
}

// ── Watch ─────────────────────────────────────────────────────────────────────
async function checkWatchState(addressHash) {
  const btn = document.getElementById('btn-watch-property');
  if (!btn) return;
  try {
    const res  = await fetch(`/api/watch/${encodeURIComponent(addressHash)}`);
    const data = await res.json();
    currentWatched = data.watched === true;
    btn.textContent = currentWatched ? 'Watching ✓' : 'Watch';
    btn.classList.toggle('btn-action-active', currentWatched);
  } catch (_) { /* silently ignore */ }
}

async function toggleWatch(addressHash, address) {
  const btn = document.getElementById('btn-watch-property');
  if (!btn) return;
  try {
    if (currentWatched) {
      const res = await fetch(`/api/watch/${encodeURIComponent(addressHash)}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      currentWatched = false;
      btn.textContent = 'Watch';
      btn.classList.remove('btn-action-active');
      toast('Removed from watchlist', 'info');
    } else {
      const res = await fetch('/api/watch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address_hash: addressHash, normalized_address: address }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      currentWatched = true;
      btn.textContent = 'Watching ✓';
      btn.classList.add('btn-action-active');
      toast('Added to watchlist — re-analysed every 24 h', 'success');
    }
  } catch (err) {
    toast('Watchlist update failed: ' + err.message, 'error');
  }
}

// ── Export ────────────────────────────────────────────────────────────────────
function exportReport(addressHash) {
  const url = `/api/export/${encodeURIComponent(addressHash)}`;
  const a   = document.createElement('a');
  a.href     = url;
  a.download = `blueprint-${addressHash.slice(0, 8)}.html`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  toast('Generating buyer brief…', 'info');
}

// ── Watchlist modal ───────────────────────────────────────────────────────────
async function openWatchlist() {
  const modal  = document.getElementById('modal-watchlist');
  const wrap   = document.getElementById('watchlist-items');
  wrap.innerHTML = '<p class="wl-loading">Loading…</p>';
  show(modal);
  try {
    const res   = await fetch('/api/watch');
    const items = await res.json();
    if (!Array.isArray(items) || items.length === 0) {
      wrap.innerHTML = '<p class="wl-empty">No properties on your watchlist yet.<br>Click "Watch" on any report to add one.</p>';
      return;
    }
    wrap.innerHTML = items.map(item => `
      <div class="wl-item">
        <div class="wl-addr">${esc(item.normalized_address || item.address_hash)}</div>
        <div class="wl-meta">
          Watching since ${item.watched_at ? new Date(item.watched_at).toLocaleDateString() : '—'}
          &nbsp;·&nbsp;
          Last checked: ${item.last_checked ? new Date(item.last_checked).toLocaleDateString() : '—'}
        </div>
        <button class="btn-ghost btn-wl-remove" data-hash="${esc(item.address_hash)}">Remove</button>
      </div>
    `).join('');

    wrap.querySelectorAll('.btn-wl-remove').forEach(btn => {
      btn.addEventListener('click', async () => {
        const hash = btn.dataset.hash;
        try {
          await fetch(`/api/watch/${encodeURIComponent(hash)}`, { method: 'DELETE' });
          btn.closest('.wl-item').remove();
          if (hash === currentAddressHash) {
            currentWatched = false;
            const watchBtn = document.getElementById('btn-watch-property');
            if (watchBtn) { watchBtn.textContent = 'Watch'; watchBtn.classList.remove('btn-action-active'); }
          }
          toast('Removed from watchlist', 'info');
          if (!wrap.querySelector('.wl-item')) {
            wrap.innerHTML = '<p class="wl-empty">Watchlist is now empty.</p>';
          }
        } catch (_) { toast('Could not remove property', 'error'); }
      });
    });
  } catch (err) {
    wrap.innerHTML = `<p class="wl-empty">Failed to load watchlist: ${esc(err.message)}</p>`;
  }
}

// ── How It Works modal rendering ──────────────────────────────────────────────
let _hiwRendered = false;

async function _ensureHiwRendered() {
  if (_hiwRendered) return;
  const loading = document.getElementById('hiw-loading');
  const about   = await loadAbout();
  if (!about) {
    if (loading) loading.textContent = 'Could not load content. Check your connection.';
    return;
  }
  hide(loading);
  _renderHiwPipeline(about);
  _renderHiwScore(about);
  _renderHiwSources(about);
  _renderHiwGlossary(about);
  // Show the first (pipeline) panel
  show(document.getElementById('hiw-panel-pipeline'));
  _hiwRendered = true;
}

function _renderHiwPipeline(about) {
  const intro = document.getElementById('hiw-pipeline-intro');
  const steps = document.getElementById('hiw-steps-dynamic');
  const footer = document.getElementById('hiw-footer-dynamic');
  if (!steps) return;

  if (intro && about.adversarial_pipeline?.description) {
    intro.textContent = about.adversarial_pipeline.description;
  }

  const agents = about.adversarial_pipeline?.agents || [];
  steps.innerHTML = agents.map((a, i) => `
    <div class="hiw-step">
      <div class="hiw-num">${i + 1}</div>
      <div class="hiw-body">
        <span class="hiw-agent-icon">${esc(a.icon || '')}</span>
        <strong>${esc(a.name)}</strong>
        ${esc(a.role)}
      </div>
    </div>
  `).join('');

  if (footer && _healthData) {
    const caps = (_healthData.elastic_capabilities || [])
      .map(c => c.replace(/_/g, ' ')).join(' · ');
    const tiers = (_healthData.data_tiers || []).join(', ');
    footer.innerHTML = `
      <p><strong>Elastic capabilities:</strong> ${esc(caps)}</p>
      <p><strong>Coverage tiers:</strong> ${esc(tiers)}</p>
      <p><strong>Model:</strong> ${esc(_healthData.gemini_model || '—')} &nbsp;|&nbsp; <strong>Fallback:</strong> ${esc(_healthData.fallback_model || '—')}</p>
    `;
  }
}

function _renderHiwScore(about) {
  const bandsEl   = document.getElementById('hiw-score-bands');
  const factorsEl = document.getElementById('hiw-factors');
  if (!bandsEl) return;

  bandsEl.innerHTML = (about.score_bands || []).map(b => `
    <div class="hiw-band" style="border-left-color:${esc(b.color)}">
      <div class="hiw-band-range" style="color:${esc(b.color)}">${esc(b.range)}</div>
      <div class="hiw-band-label" style="color:${esc(b.color)}">${esc(b.label)}</div>
      <div class="hiw-band-headline">${esc(b.headline)}</div>
      <div class="hiw-band-meaning">${esc(b.meaning)}</div>
    </div>
  `).join('');

  if (factorsEl) {
    factorsEl.innerHTML = (about.risk_factors || []).map(f => `
      <div class="hiw-factor">
        <div class="hiw-factor-icon">${esc(f.icon || '')}</div>
        <div class="hiw-factor-body">
          <div class="hiw-factor-name">${esc(f.factor)} <span class="hiw-weight weight-${f.weight.toLowerCase()}">${esc(f.weight)}</span></div>
          <div class="hiw-factor-detail">${esc(f.detail)}</div>
        </div>
      </div>
    `).join('');
  }
}

function _renderHiwSources(about) {
  const el = document.getElementById('hiw-sources-dynamic');
  if (!el) return;
  el.innerHTML = (about.data_sources || []).map(s => `
    <div class="hiw-source-card">
      <div class="hiw-source-icon">${esc(s.icon || '')}</div>
      <div class="hiw-source-body">
        <div class="hiw-source-name">${esc(s.name)}</div>
        <div class="hiw-source-coverage">Coverage: ${esc(s.coverage)} · ${esc(s.freshness)}</div>
        <div class="hiw-source-what">${esc(s.what)}</div>
        <div class="hiw-source-why"><strong>Why it matters:</strong> ${esc(s.why_it_matters)}</div>
      </div>
    </div>
  `).join('');
}

function _renderHiwGlossary(about) {
  const el = document.getElementById('hiw-glossary-dynamic');
  if (!el) return;
  el.innerHTML = (about.glossary || []).map(g => `
    <details class="glossary-item">
      <summary class="glossary-term">
        ${esc(g.term)} <span class="glossary-short">— ${esc(g.short)}</span>
      </summary>
      <div class="glossary-detail">${esc(g.detail)}</div>
    </details>
  `).join('');
}

// ── Validated examples (hero section) ─────────────────────────────────────────
function _renderValidatedExamples(examples) {
  const wrap = document.getElementById('validated-examples');
  const cards = document.getElementById('ve-cards');
  if (!wrap || !cards || !examples.length) return;

  const colors = { LOW: '#22c55e', MEDIUM: '#eab308', HIGH: '#f97316', CRITICAL: '#ef4444' };
  cards.innerHTML = examples.map(ex => `
    <div class="ve-card" data-address="${esc(ex.address)}">
      <div class="ve-card-header">
        <span class="ve-location">${esc(ex.label)}</span>
        <span class="ve-score" style="color:${colors[ex.level] || '#64748b'}">${ex.score}/100 ${ex.level}</span>
      </div>
      <div class="ve-headline">${esc(ex.headline)}</div>
      <div class="ve-finding">${esc(ex.key_finding)}</div>
      <div class="ve-outcome">&#x2714; ${esc(ex.outcome)}</div>
      <button class="btn-ghost ve-try-btn" data-address="${esc(ex.address)}">Try this address →</button>
    </div>
  `).join('');

  // Wire up "Try this address" buttons
  cards.querySelectorAll('.ve-try-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      addressInput.value = btn.dataset.address;
      addressInput.focus();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });

  show(wrap);
}

// ── Landing page educational sections (all data from /api/about) ─────────────

function _renderWhySection(stats) {
  const wrap = document.getElementById('why-stats');
  const section = document.getElementById('why-section');
  if (!wrap || !section || !stats.length) return;
  wrap.innerHTML = stats.map(s => `
    <div class="why-stat-card">
      <div class="why-stat-value">${esc(s.stat)}</div>
      <div class="why-stat-label">${esc(s.label)}</div>
      <div class="why-stat-context">${esc(s.context)}</div>
    </div>
  `).join('');
  show(section);
}

function _renderHowSection(steps, debateCallout) {
  const stepsEl = document.getElementById('how-steps-inline');
  const calloutEl = document.getElementById('debate-callout-text');
  const section = document.getElementById('how-section');
  if (!stepsEl || !section || !steps.length) return;
  stepsEl.innerHTML = steps.map(s => `
    <div class="how-step">
      <div class="how-step-num">${esc(String(s.step))}</div>
      <div class="how-step-icon">${esc(s.icon)}</div>
      <div class="how-step-body">
        <div class="how-step-title">${esc(s.title)}</div>
        <div class="how-step-detail">${esc(s.detail)}</div>
      </div>
    </div>
  `).join('');
  if (calloutEl && debateCallout) calloutEl.textContent = ' ' + debateCallout;
  show(section);
}

function _renderChecksGrid(factors) {
  const grid = document.getElementById('checks-grid');
  const section = document.getElementById('checks-section');
  if (!grid || !section || !factors.length) return;
  const weightColor = { HIGH: 'var(--danger)', MEDIUM: 'var(--warn)', LOW: 'var(--text2)' };
  grid.innerHTML = factors.map(f => `
    <div class="check-card">
      <div class="check-icon">${esc(f.icon || '')}</div>
      <div class="check-body">
        <div class="check-name">
          ${esc(f.factor)}
          <span class="check-weight" style="color:${weightColor[f.weight] || 'var(--text2)'}">
            ${esc(f.weight)}
          </span>
        </div>
        <div class="check-detail">${esc(f.detail)}</div>
      </div>
    </div>
  `).join('');
  show(section);
}

function _renderTrustStripInline(sources) {
  const strip = document.getElementById('trust-strip-inline');
  const section = document.getElementById('trust-section');
  if (!strip || !section || !sources.length) return;
  strip.innerHTML = sources.map(s => `
    <div class="trust-item" title="${esc(s.what)}">
      <span class="trust-icon">${esc(s.icon || '')}</span>
      <span class="trust-name">${esc(s.name)}</span>
    </div>
  `).join('');
  show(section);
}

// ── Score explainer (in report) ───────────────────────────────────────────────
async function _renderScoreExplainer(riskLevel) {
  const about = _aboutData || await loadAbout();
  if (!about) return;
  const band = (about.score_bands || []).find(b => b.label === riskLevel);
  if (!band) return;
  const headlineEl = document.getElementById('score-explainer-headline');
  const meaningEl  = document.getElementById('score-explainer-meaning');
  const wrapEl     = document.getElementById('score-explainer');
  if (!headlineEl || !meaningEl || !wrapEl) return;
  headlineEl.textContent = band.headline;
  meaningEl.textContent  = band.meaning;
  wrapEl.style.borderLeftColor = band.color;
  show(wrapEl);
}

// ── Data provenance strip (in report) ────────────────────────────────────────
async function _renderProvenanceStrip(dataSources) {
  if (!dataSources || dataSources.length === 0) return;
  const el = document.getElementById('provenance-strip');
  if (!el) return;

  const about = _aboutData || await loadAbout();
  const knownSources = about?.data_sources || [];

  el.innerHTML = dataSources.map(sourceName => {
    const known = knownSources.find(s =>
      sourceName.toLowerCase().includes(s.id) ||
      sourceName.toLowerCase().includes(s.name.toLowerCase().split(' ')[0].toLowerCase())
    );
    return `
      <div class="prov-item" title="${known ? esc(known.what) : ''}">
        <span class="prov-icon">${known ? esc(known.icon) : '📄'}</span>
        <span class="prov-name">${esc(sourceName)}</span>
        ${known ? `<span class="prov-freshness">${esc(known.freshness)}</span>` : ''}
      </div>
    `;
  }).join('');
}

// ── Property Comparison ────────────────────────────────────────────────────────
function initCompare() {
  const modal   = document.getElementById('modal-compare');
  const btnOpen = document.getElementById('btn-compare-nav');
  const btnClose = document.getElementById('modal-compare-close');
  const btnRun  = document.getElementById('btn-compare-run');
  const btnReset = document.getElementById('btn-compare-reset');

  if (!modal) return;

  btnOpen.addEventListener('click', () => {
    _compareReset();
    show(modal);
  });
  btnClose.addEventListener('click', () => hide(modal));
  modal.addEventListener('click', e => { if (e.target === modal) hide(modal); });
  btnRun.addEventListener('click', runCompare);
  btnReset?.addEventListener('click', _compareReset);

  document.getElementById('compare-addr-a')?.addEventListener('keydown', e => { if (e.key === 'Enter') runCompare(); });
  document.getElementById('compare-addr-b')?.addEventListener('keydown', e => { if (e.key === 'Enter') runCompare(); });
}

function _compareReset() {
  show(document.getElementById('compare-inputs'));
  hide(document.getElementById('compare-loading'));
  hide(document.getElementById('compare-results'));
  document.getElementById('compare-addr-a').value = '';
  document.getElementById('compare-addr-b').value = '';
}

async function runCompare() {
  const addrA = (document.getElementById('compare-addr-a')?.value || '').trim();
  const addrB = (document.getElementById('compare-addr-b')?.value || '').trim();
  if (addrA.length < 5 || addrB.length < 5) {
    toast('Please enter both addresses (min 5 characters each)', 'error');
    return;
  }

  hide(document.getElementById('compare-inputs'));
  show(document.getElementById('compare-loading'));
  hide(document.getElementById('compare-results'));

  try {
    const res = await fetch('/api/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address_a: addrA, address_b: addrB }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    hide(document.getElementById('compare-loading'));
    renderCompareResults(data);
  } catch (err) {
    hide(document.getElementById('compare-loading'));
    show(document.getElementById('compare-inputs'));
    toast('Comparison failed: ' + err.message, 'error');
  }
}

function renderCompareResults(data) {
  const rec = data.recommendation || {};
  const a   = data.a || {};
  const b   = data.b || {};

  const recommended = rec.recommended || 'NEITHER';
  const recColors   = { A: 'var(--success)', B: 'var(--accent)', NEITHER: 'var(--warn)' };
  const recEmoji    = { A: '🏆', B: '🏆', NEITHER: '🤝' };
  const recLabel    = recommended === 'A'
    ? (a.normalized_address || 'Property A')
    : recommended === 'B'
    ? (b.normalized_address || 'Property B')
    : 'Neither — proceed with caution';

  const verdictEl = document.getElementById('compare-verdict');
  verdictEl.innerHTML = `
    <div class="cmp-verdict-badge" style="border-color:${recColors[recommended]}">
      <div class="cmp-verdict-emoji">${recEmoji[recommended]}</div>
      <div class="cmp-verdict-content">
        <div class="cmp-verdict-label">Our recommendation: <strong style="color:${recColors[recommended]}">${recLabel}</strong></div>
        <div class="cmp-verdict-conf">Confidence: ${esc(rec.confidence || '—')}</div>
        <div class="cmp-verdict-headline">${esc(rec.headline || '')}</div>
        <p class="cmp-verdict-rationale">${esc(rec.rationale || '')}</p>
        ${rec.deal_breaker ? `<div class="cmp-deal-breaker">⚠ Deal-breaker: ${esc(rec.deal_breaker)}</div>` : ''}
      </div>
    </div>
  `;

  renderCompareCol(document.getElementById('compare-col-a'), a, 'A', recommended, rec.a_pros, rec.a_cons);
  renderCompareCol(document.getElementById('compare-col-b'), b, 'B', recommended, rec.b_pros, rec.b_cons);

  show(document.getElementById('compare-results'));
}

function renderCompareCol(el, report, label, recommended, pros, cons) {
  const score   = typeof report.buyer_risk_score === 'number' ? report.buyer_risk_score : '—';
  const level   = report.risk_level || 'UNKNOWN';
  const isWinner = recommended === label;
  const levelClass = level.toLowerCase();
  const debate  = report.debate || {};
  const nbhd    = report.neighborhood || {};

  const prosHtml = (pros || []).length
    ? `<div class="cmp-pros-cons"><strong>Pros</strong><ul>${pros.map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>`
    : '';
  const consHtml = (cons || []).length
    ? `<div class="cmp-pros-cons cmp-cons"><strong>Cons</strong><ul>${cons.map(c => `<li>${esc(c)}</li>`).join('')}</ul></div>`
    : '';

  const flags = (report.flags || []).slice(0, 3);
  const flagsHtml = flags.length
    ? `<div class="cmp-flags">${flags.map(f => `<div class="cmp-flag">⚠ ${esc(f)}</div>`).join('')}</div>`
    : '';

  const nbhdScore = nbhd.neighborhood_score != null ? `${nbhd.neighborhood_score}/100` : '—';
  const debateRec = debate.buy_recommendation ? `${debate.buy_recommendation} (${debate.confidence || '?'})` : '—';

  el.innerHTML = `
    <div class="cmp-col-inner ${isWinner ? 'cmp-winner' : ''}">
      ${isWinner ? '<div class="cmp-winner-badge">🏆 Recommended</div>' : ''}
      <div class="cmp-col-label">Property ${label}</div>
      <div class="cmp-addr">${esc(report.normalized_address || 'Unknown')}</div>
      <div class="cmp-score risk-${levelClass}">
        <span class="cmp-score-num">${score}</span>
        <span class="cmp-score-label">/100 · ${level}</span>
      </div>
      <p class="cmp-summary">${esc((report.summary || '').slice(0, 200))}…</p>
      ${flagsHtml}
      <div class="cmp-meta-row"><span class="cmp-meta-key">AI Debate</span><span class="cmp-meta-val">${esc(debateRec)}</span></div>
      <div class="cmp-meta-row"><span class="cmp-meta-key">Neighbourhood</span><span class="cmp-meta-val">${nbhdScore}</span></div>
      ${prosHtml}
      ${consHtml}
    </div>
  `;
}

// ── Toast notifications ───────────────────────────────────────────────────────
function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = message;
  container.appendChild(t);
  requestAnimationFrame(() => t.classList.add('toast-visible'));
  setTimeout(() => {
    t.classList.remove('toast-visible');
    setTimeout(() => t.remove(), 300);
  }, 3500);
}
