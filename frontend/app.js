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
let _currentReport     = null;   // latest rendered report (for verdict refresh on debate)

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadStats();
  loadHealth();
  loadAbout();
  setInterval(loadStats, 30_000);
  applyTheme(localStorage.getItem('bp-theme') || 'light');
  const yearEl = document.getElementById('app-year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  // Deep-links from the landing page / shared URLs:
  //   ?address=<addr>      → auto-run a fresh analysis (landing search + demo chips)
  //   ?report=<hash>       → open a stored report by address_hash
  //   ?share=<id>          → open a publicly shared report by share_id
  const _params   = new URLSearchParams(location.search);
  const _address  = _params.get('address');
  const _rptHash  = _params.get('report');
  const _shareId  = _params.get('share');
  if (_shareId) {
    openSharedReport(_shareId);
  } else if (_rptHash) {
    openStoredReport(_rptHash);
  } else if (_address && _address.trim().length >= 5) {
    addressInput.value = _address.trim();
    startAnalysis(_address.trim());
  }

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

  // Analysis log drawer toggle
  document.getElementById('log-drawer-toggle')?.addEventListener('click', () => {
    const toggle = document.getElementById('log-drawer-toggle');
    const body   = document.getElementById('log-drawer-body');
    if (!toggle || !body) return;
    const open = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!open));
    body.style.display = open ? 'none' : 'flex';
  });

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

  // Elastic Intelligence dashboard
  initElasticDashboard();

  // a11y: Esc closes any open modal
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('.modal-overlay').forEach(m => {
      if (m.style.display !== 'none') hide(m);
    });
  });

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
    const geminiEl     = document.getElementById('stat-gemini');
    const geminiTextEl = document.getElementById('stat-gemini-text');
    const geminiDotEl  = document.getElementById('stat-gemini-dot');
    const pipelineEl   = document.getElementById('stat-pipeline-label');
    if (geminiTextEl && _healthData.gemini_model) {
      geminiTextEl.textContent = _healthData.gemini_model;
      if (geminiDotEl) geminiDotEl.classList.remove('degraded');
    }
    if (geminiEl && _healthData.gemini_model) {
      geminiEl.dataset.tooltip = geminiEl.dataset.tooltip || '';
      const agents = _healthData.agents ? `${_healthData.agents}-agent SequentialAgent` : '';
      geminiEl.dataset.tooltip = [
        `Model: ${_healthData.gemini_model}`,
        agents,
        'Adversarial debate: Optimist vs Pessimist',
        'SSE streaming · Google Cloud ADK',
      ].filter(Boolean).join('\n');
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
      geminiDetailEl.textContent = `${_healthData.agents}-agent SequentialAgent · Adversarial debate · SSE streaming`;
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

    const elasticTextEl = document.getElementById('stat-elastic-text');
    const elasticDotEl  = document.getElementById('stat-elastic-dot');
    const elasticEl     = document.getElementById('stat-elastic');
    if (elasticTextEl) {
      const mcpActive = d.elastic_mcp_active;
      elasticTextEl.textContent = mcpActive ? 'Elastic MCP ✓' : 'Elastic SDK';
      if (elasticDotEl) elasticDotEl.classList.toggle('degraded', !mcpActive);
      if (elasticEl) elasticEl.dataset.tooltip = [
        mcpActive ? 'Agent Builder MCP: active' : 'Agent Builder MCP: unavailable (direct SDK)',
        'RRF hybrid retrieval: BM25 + ELSER semantic',
        'Semantic reranker (.rerank-v1-elasticsearch)',
        'ES|QL: cross-reference + flip-fraud detection',
        'Percolator: proactive risk-profile alerts',
        'Geo-distance: cross-property intelligence',
        'Significant terms: flag pattern detection',
      ].join('\n');
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
    addLog(`> Starting analysis: ${evt.address || currentAddress}`, 'info');
    return;
  }

  if (type === 'step') {
    updateAgentCard(agent, message, 'running');
    addLog(`[${agent || '?'}] ${message}${detail ? ': ' + detail : ''}`, detail && detail.includes('[WARN]') ? 'warn' : 'info');
    return;
  }

  if (type === 'finding') {
    updateAgentCard(agent, summary, 'done');
    addLog(`+ ${summary}`, 'finding');
    return;
  }

  if (type === 'complete') {
    markAllDone();
    addLog('+ Analysis complete. Rendering report', 'finding');
    if (report) {
      renderReport(report);
      // Show pending pill while DebateAgent is still running
      if (!report.debate) {
        const pill = document.getElementById('verdict-debate-pending');
        if (pill) show(pill);
      }
    }
    return;
  }

  if (type === 'debate_complete') {
    if (evt.debate) {
      const pill = document.getElementById('verdict-debate-pending');
      if (pill) hide(pill);
      renderDebate(evt.debate);
      if (_currentReport) renderVerdict(_currentReport, evt.debate);
      // Reload similar properties now that the debate-adjusted risk_level is
      // written back to Elastic — previous call used the synthesis-level risk band
      if (currentAddressHash) loadSimilarProperties(currentAddressHash);
    }
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
async function openStoredReport(hash) {
  try {
    const res = await fetch(`/api/report/${encodeURIComponent(hash)}`);
    if (!res.ok) return;
    const report = await res.json();
    currentAddress = report.normalized_address || '';
    hide(sectionSearch);
    renderReport(report);
  } catch (_) { /* silently ignore — fall back to landing */ }
}

async function openSharedReport(shareId) {
  try {
    const res = await fetch(`/api/share/${encodeURIComponent(shareId)}`);
    if (!res.ok) {
      toast('Shared report not found or has expired.', 'error');
      return;
    }
    const report = await res.json();
    currentAddress = report.normalized_address || '';
    hide(sectionSearch);
    renderReport(report);
  } catch (_) {
    toast('Could not load shared report.', 'error');
  }
}

function renderReport(report) {
  // Collapse the full agent panel into a compact log drawer
  const drawer     = document.getElementById('log-drawer');
  const drawerBody = document.getElementById('log-drawer-body');
  const liveLog    = document.getElementById('live-log');
  if (drawer && drawerBody && liveLog && liveLog.children.length > 0) {
    // Clone all log lines into the drawer
    drawerBody.innerHTML = liveLog.innerHTML;
    show(drawer);
  }
  hide(agentPanel);
  show(reportPanel);

  // Track current report for action buttons + verdict refresh on debate
  _currentReport = report;
  currentAddressHash = report.address_hash || '';
  if (currentAddressHash) checkWatchState(currentAddressHash);

  // The Verdict — lead with the buyer's decision (refined later by the debate)
  renderVerdict(report, report.debate || null);

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
  const initLevel = scoreToLevel(report.buyer_risk_score) || report.risk_level || 'MEDIUM';
  riskBadge.textContent = initLevel;
  riskBadge.className = `risk-level-badge ${initLevel}`;
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
    const dedupedCount = renderTimeline(timeline);
    document.getElementById('timeline-count').textContent = `(${dedupedCount} events)`;
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
  if (report.debate) {
    renderDebate(report.debate);
    renderVerdict(report, report.debate);  // update header score to debate-adjusted value
  }

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

  // How Elastic powered THIS analysis (retrieval strategy, ES|QL, geo, percolator)
  if (report.elastic_provenance) renderElasticProvenance(report.elastic_provenance);

  // Debate fallback: if the SSE debate_complete event was missed (e.g. Gemini rate
  // limit delayed it), poll the stored Elasticsearch report once after 10s.
  if (!report.debate && currentAddressHash) {
    setTimeout(async () => {
      if (document.getElementById('debate-section')?.style.display !== 'none') return;
      try {
        const r = await fetch(`/api/report/${currentAddressHash}`);
        if (!r.ok) return;
        const stored = await r.json();
        if (stored.debate) {
          const pill = document.getElementById('verdict-debate-pending');
          if (pill) hide(pill);
          renderDebate(stored.debate);
          renderVerdict(stored, stored.debate);  // sync header to debate-adjusted score
          if (stored.buyer_risk_score != null) {
            renderGauge(stored.buyer_risk_score, true);
            _updateMapForDebate(stored.buyer_risk_score, stored.buy_recommendation);
          }
        }
      } catch (_) {}
    }, 10000);
  }

  // Reload stats after report is generated
  loadStats();
}

function renderTimeline(items) {
  const container = document.getElementById('timeline');
  container.innerHTML = '';
  // Deduplicate: collapse identical events from repeated pipeline runs.
  // Use full description so permit events with same type but different work/status
  // don't collapse into one entry. Only exact matches (same run re-indexing) dedup.
  const seen = new Set();
  const unique = items.filter(item => {
    const key = `${item.event_type}|${item.date}|${item.description || ''}|${item.source || ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  items = unique;
  // Return deduped count for the badge
  const _dedupedCount = items.length;
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
      <div class="tl-date">${esc(item.date || '-')}</div>
      <div class="tl-body">
        <div class="tl-desc">${esc(item.description || '')}</div>
        ${item.source ? `<div class="tl-source">Source: ${esc(item.source)}</div>` : ''}
        ${flags}
      </div>
      ${amount}
    `;
    container.appendChild(div);
  });
  return _dedupedCount;
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

// ── Score → risk level (mirrors backend score_bands) ─────────────────────────
function scoreToLevel(score) {
  if (score == null || isNaN(score)) return 'MEDIUM';
  if (score <= 30) return 'LOW';
  if (score <= 60) return 'MEDIUM';
  if (score <= 80) return 'HIGH';
  return 'CRITICAL';
}

// ── The Verdict banner ─────────────────────────────────────────────────────────
const _REC_META = {
  BUY:       { emoji: '', label: 'BUY' },
  NEGOTIATE: { emoji: '', label: 'NEGOTIATE' },
  AVOID:     { emoji: '', label: 'AVOID' },
};
const _STRATEGY_LABEL = {
  elastic_mcp_hybrid: 'Agent Builder MCP hybrid',
  rrf_bm25_elser:     'RRF hybrid (BM25 ⊕ ELSER)',
  semantic_reranker:  'semantic reranker',
  bm25:               'BM25 keyword',
};

function renderVerdict(report, debate) {
  const banner = document.getElementById('verdict-banner');
  if (!banner) return;

  const rec   = (debate && debate.buy_recommendation) || report.buy_recommendation || 'NEGOTIATE';
  const conf  = (debate && debate.confidence) || report.confidence || 'MEDIUM';
  const score = (debate && typeof debate.final_score === 'number')
    ? debate.final_score
    : (typeof report.buyer_risk_score === 'number' ? report.buyer_risk_score : null);
  // Always derive level from the score being displayed so header + body stay in sync
  const level = score != null ? scoreToLevel(score) : (report.risk_level || 'MEDIUM');
  const rationale =
    (debate && (debate.verdict_text || (debate.verdict && debate.verdict.verdict))) ||
    report.summary || '';

  const m = _REC_META[rec] || _REC_META.NEGOTIATE;
  banner.className = `verdict-banner ${rec}`;
  document.getElementById('verdict-emoji').textContent = m.emoji;
  document.getElementById('verdict-word').textContent = m.label;
  const scoreStr = score != null ? `Buyer Risk <strong>${score}/100</strong> · ${esc(level)}` : esc(level);
  document.getElementById('verdict-conf-line').innerHTML =
    `${scoreStr} · Confidence <strong>${esc(conf)}</strong>`;
  document.getElementById('verdict-rationale').textContent = rationale;

  // Honest stat chips — every value derives from real report data
  const chips = [];
  const flagCount = Array.isArray(report.flags) ? report.flags.length : 0;
  const tlCount   = Array.isArray(report.timeline) ? report.timeline.length : 0;
  const srcCount  = Array.isArray(report.data_sources) ? report.data_sources.length : 0;
  if (flagCount) chips.push(`<span class="verdict-chip"><strong>${flagCount}</strong> material risk${flagCount > 1 ? 's' : ''} found</span>`);
  if (tlCount)   chips.push(`<span class="verdict-chip"><strong>${tlCount}</strong> dated records</span>`);
  if (srcCount)  chips.push(`<span class="verdict-chip"><strong>${srcCount}</strong> data sources</span>`);
  const strat = report.elastic_provenance && report.elastic_provenance.retrieval_strategy;
  if (strat) chips.push(`<span class="verdict-chip chip-elastic">Retrieval: <strong>${esc(_STRATEGY_LABEL[strat] || strat)}</strong></span>`);
  document.getElementById('verdict-chips').innerHTML = chips.join('');

  show(banner);
}

// ── Debate tug-of-war: Optimist vs Pessimist move the score synthesis → final ──
function renderTug(debate) {
  const tug = document.getElementById('debate-tug');
  if (!tug || !_currentReport) return;
  const final = typeof debate.final_score === 'number' ? debate.final_score : null;
  if (final === null) return;   // nothing meaningful without a final score

  const clamp = v => Math.max(0, Math.min(100, v));
  const initial = typeof _currentReport.buyer_risk_score === 'number' ? _currentReport.buyer_risk_score : null;
  const opt = debate.optimist  && typeof debate.optimist.adjusted_score  === 'number' ? debate.optimist.adjusted_score  : null;
  const pes = debate.pessimist && typeof debate.pessimist.adjusted_score === 'number' ? debate.pessimist.adjusted_score : null;

  const setMarker = (id, v) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (v === null) { el.style.display = 'none'; return; }
    el.style.display = '';
    el.style.left = clamp(v) + '%';
  };

  document.getElementById('tug-opt-score').textContent = opt !== null ? `${opt}/100` : '-';
  document.getElementById('tug-pes-score').textContent = pes !== null ? `${pes}/100` : '-';
  setMarker('tug-marker-opt', opt);
  setMarker('tug-marker-pes', pes);
  document.getElementById('tug-final-val').textContent = final;

  const initEl = document.getElementById('tug-marker-init');
  const finalEl = document.getElementById('tug-marker-final');
  show(tug);

  // Animate: final marker starts at the synthesis score, then slides to the verdict
  if (initial !== null && initial !== final) {
    initEl.style.display = '';
    initEl.style.left = clamp(initial) + '%';
    finalEl.style.left = clamp(initial) + '%';
    void finalEl.offsetWidth;  // force reflow so the transition runs
    requestAnimationFrame(() => { finalEl.style.left = clamp(final) + '%'; });
  } else {
    initEl.style.display = 'none';
    finalEl.style.left = clamp(final) + '%';
  }

  const parts = [];
  if (initial !== null) parts.push(`SynthesisAgent proposed <strong>${initial}/100</strong>.`);
  if (opt !== null && pes !== null) parts.push(`OptimistAgent pulled toward <strong>${opt}</strong>, PessimistAgent toward <strong>${pes}</strong>.`);
  parts.push(`Confidence-adjusted verdict: <strong>${final}/100</strong>.`);
  document.getElementById('tug-caption').innerHTML = parts.join(' ');
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
    document.getElementById('optimist-score').textContent = `Suggested score: ${opt.adjusted_score ?? '-'}/100`;
    show(document.getElementById('debate-optimist'));
  }
  if (pes.argument) {
    document.getElementById('pessimist-body').textContent = pes.argument;
    document.getElementById('pessimist-score').textContent = `Suggested score: ${pes.adjusted_score ?? '-'}/100`;
    show(document.getElementById('debate-pessimist'));
  }

  // Animate gauge to the debated final score and sync the map legend
  if (finalScore !== undefined) {
    renderGauge(finalScore, true);
    _updateMapForDebate(finalScore, rec);
  }

  // Refresh the Verdict banner with the debate's final recommendation + score
  if (_currentReport) renderVerdict(_currentReport, debate);

  // Tug-of-war visualisation of how the debate moved the score
  renderTug(debate);

  // Percolator alerts arrive live with the debate result
  if (Array.isArray(debate.percolator_matches)) {
    renderPercolatorAlerts(debate.percolator_matches);
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
    { label: 'Air Quality (PM2.5)', value: n.pm25 != null ? `${n.pm25.toFixed(1)} µg/m³` : '-', ok: (n.pm25 || 0) <= 12 },
    { label: 'Superfund Proximity', value: n.superfund_proximity != null ? `${Math.round(n.superfund_proximity)}/100` : '-', ok: (n.superfund_proximity || 0) < 40 },
    { label: 'Traffic Pollution', value: n.traffic_proximity != null ? `${Math.round(n.traffic_proximity)}/100` : '-', ok: (n.traffic_proximity || 0) < 60 },
    { label: 'Schools Nearby', value: n.schools_nearby != null ? `${n.schools_nearby}` : '-', ok: (n.schools_nearby || 0) > 0 },
    { label: 'Parks Nearby', value: n.parks_nearby != null ? `${n.parks_nearby}` : '-', ok: (n.parks_nearby || 0) > 0 },
    { label: 'Transit Stops', value: n.transit_nearby != null ? `${n.transit_nearby}` : '-', ok: (n.transit_nearby || 0) > 1 },
    { label: 'Neighbourhood Score', value: n.neighborhood_score != null ? `${n.neighborhood_score}/100` : '-', ok: (n.neighborhood_score || 0) >= 60 },
  ];

  grid.innerHTML = items.map(item => `
    <div class="nbhd-card ${item.ok ? 'good' : 'warn'}">
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
let _mapMarker = null;

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
    const score  = report.buyer_risk_score ?? '-';

    // Property pin
    const icon = L.divIcon({
      className: '',
      html: `<div style="background:${color};width:22px;height:22px;border-radius:50%;border:3px solid white;box-shadow:0 2px 10px rgba(0,0,0,0.45);"></div>`,
      iconSize: [22, 22], iconAnchor: [11, 11],
    });
    _mapMarker = L.marker([lat, lng], { icon })
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
    const dist  = (typeof p.distance_km === 'number')
      ? `<div class="similar-dist">📍 ${p.distance_km} km away · geo_distance</div>` : '';
    return `
      <div class="similar-card">
        <div class="similar-score" style="color:${color}">${p.buyer_risk_score ?? '-'}<span>/100</span></div>
        <div class="similar-level" style="color:${color}">${esc(lvl)}</div>
        <div class="similar-addr">${esc(p.normalized_address || p.address_hash || '-')}</div>
        ${loc ? `<div class="similar-loc">${esc(loc)}</div>` : ''}
        ${dist}
        ${flags.map(f => `<div class="similar-flag">⚠ ${esc(f)}</div>`).join('')}
      </div>
    `;
  }).join('');

  // Surface the retrieval method (geo vs risk-band) on the badge, data-driven.
  if (badge && data.method) {
    const label = data.method === 'geo_distance' ? 'geo_distance' :
                  data.method === 'risk_level_esql' ? 'ES|QL risk band' : '';
    if (label) badge.textContent = `${props.length} found · ${label}`;
  }

  show(section);
}

// ── Update map legend + risk badge after debate adjusts the score ─────────────
function _updateMapForDebate(finalScore, buyRec) {
  const riskColors = { LOW: '#22c55e', MEDIUM: '#eab308', HIGH: '#f97316', CRITICAL: '#ef4444' };
  const recLevel = scoreToLevel(finalScore);
  const color = riskColors[recLevel] || '#64748b';
  const legendEl = document.getElementById('map-legend');
  if (legendEl) {
    const fzChip = legendEl.querySelector('.map-legend-item:last-child');
    const fzHtml = fzChip && fzChip.innerHTML.includes('FEMA') ? fzChip.outerHTML : '';
    legendEl.innerHTML = `
      <span class="map-legend-item"><span style="background:${color}" class="map-legend-dot"></span>${recLevel} risk · ${finalScore}/100 <em style="font-size:.7rem;opacity:.7">(debate-adjusted)</em></span>
      <span class="map-legend-item"><span class="map-legend-ring" style="border-color:${color}"></span>500m analysis radius</span>
      ${fzHtml}
    `;
  }
  // Update the map marker popup to reflect the debate-adjusted score
  if (_mapMarker) {
    const addr = _mapMarker.getPopup()?.getContent()?.split('<br>')[0] || '';
    _mapMarker.setPopupContent(
      `${addr}<br>Risk Score: <strong>${finalScore}/100</strong> · ${recLevel} <em style="font-size:.75em;opacity:.8">(debate-adjusted)</em>`
    );
  }
  // Also update the risk badge at the top of the report
  const badge = document.getElementById('risk-level-badge');
  if (badge) {
    badge.textContent = recLevel;
    badge.className = `risk-level-badge ${recLevel}`;
  }
}

// ── Elastic provenance (per-report: how Elastic powered this analysis) ────────
function renderPercolatorAlerts(matches) {
  const wrap = document.getElementById('elastic-alerts');
  const section = document.getElementById('elastic-prov-section');
  if (!wrap || !Array.isArray(matches) || matches.length === 0) return;
  const sevColor = { CRITICAL: 'var(--danger)', HIGH: 'var(--orange)', MEDIUM: 'var(--warn)', LOW: 'var(--text2)' };
  wrap.innerHTML = `
    <div class="el-alert-head">${matches.length} saved risk profile${matches.length > 1 ? 's' : ''} matched
      <span class="el-alert-tag">Elastic percolator</span></div>
    ${matches.map(m => `
      <div class="el-alert" style="border-left-color:${sevColor[m.severity] || 'var(--text2)'}">
        <span class="el-alert-name" style="color:${sevColor[m.severity] || 'var(--text2)'}">${esc(m.alert_name || 'Alert')}</span>
        <span class="el-alert-desc">${esc(m.description || '')}</span>
      </div>`).join('')}
  `;
  if (section) show(section);
}

const _STRATEGY_LABELS = {
  elastic_mcp_hybrid: 'Elastic Agent Builder MCP hybrid search',
  rrf_bm25_elser:     'RRF hybrid retriever (BM25 ⊕ ELSER)',
  semantic_reranker:  'ELSER + semantic reranker',
  bm25:               'BM25 keyword',
  unavailable:        'Elastic unavailable',
};

function renderElasticProvenance(prov) {
  const section = document.getElementById('elastic-prov-section');
  const grid    = document.getElementById('elastic-prov-grid');
  const esqlEl  = document.getElementById('elastic-esql-list');
  if (!section || !grid || !prov) return;

  // Percolator matches may live in provenance (report-fetch path)
  if (Array.isArray(prov.percolator_matches)) renderPercolatorAlerts(prov.percolator_matches);

  const tiles = [
    { label: 'Retrieval strategy', value: _STRATEGY_LABELS[prov.retrieval_strategy] || prov.retrieval_strategy || '-' },
    { label: 'Events retrieved',   value: prov.events_retrieved ?? '-' },
    { label: 'ES|QL queries run',  value: Array.isArray(prov.esql_queries) ? prov.esql_queries.length : (prov.esql_queries ?? 0) },
    { label: 'Geo cross-refs (50km)', value: prov.geo_nearby_count ?? 0 },
    { label: 'Significant terms',  value: prov.significant_terms_count ?? 0 },
    { label: 'MCP active',         value: prov.mcp_active ? 'Yes' : 'Direct SDK' },
  ];
  grid.innerHTML = tiles.map(t => `
    <div class="el-prov-tile">
      <div class="el-prov-val">${esc(String(t.value))}</div>
      <div class="el-prov-label">${esc(t.label)}</div>
    </div>
  `).join('');

  // ES|QL queries actually executed for this property, with row counts
  if (esqlEl && Array.isArray(prov.esql_queries) && prov.esql_queries.length) {
    esqlEl.innerHTML = `
      <div class="el-esql-head">ES|QL queries executed against Elasticsearch for this property</div>
      ${prov.esql_queries.map(q => `
        <div class="el-esql-item">
          <div class="el-esql-name">${esc(q.name || 'Query')} <span class="el-esql-rows">${q.row_count} row${q.row_count === 1 ? '' : 's'}</span></div>
          <code class="el-esql-code">${esc(q.query || '')}</code>
        </div>`).join('')}
    `;
  } else if (esqlEl) {
    esqlEl.innerHTML = '';
  }

  show(section);
}

// ── Elastic Intelligence dashboard (modal, 100% API-driven) ───────────────────
function initElasticDashboard() {
  const modal = document.getElementById('modal-elastic');
  const open  = document.getElementById('btn-elastic-nav');
  const close = document.getElementById('modal-elastic-close');
  if (!modal || !open) return;
  open.addEventListener('click', openElasticDashboard);
  close?.addEventListener('click', () => hide(modal));
  modal.addEventListener('click', e => { if (e.target === modal) hide(modal); });
}

async function openElasticDashboard() {
  const modal   = document.getElementById('modal-elastic');
  const loading = document.getElementById('el-dash-loading');
  const dash    = document.getElementById('el-dash');
  show(modal);
  show(loading); hide(dash);
  try {
    const [statusRes, insightsRes] = await Promise.all([
      fetch('/api/elastic/status'),
      fetch('/api/elastic/insights'),
    ]);
    const status   = await statusRes.json();
    const insights = insightsRes.ok ? await insightsRes.json() : { available: false };
    renderElasticStatus(status);
    renderElasticInsights(insights);
    hide(loading); show(dash);
  } catch (err) {
    if (loading) loading.textContent = 'Could not load Elastic status. Check the connection.';
  }
}

function renderElasticStatus(s) {
  // Connection status row
  const statusRow = document.getElementById('el-status-row');
  if (statusRow) {
    const mcpOk = s.elastic_mcp && s.elastic_mcp.startsWith('connected');
    statusRow.innerHTML = `
      <div class="el-status-pill ${s.elastic_connected ? 'ok' : 'off'}">${s.elastic_connected ? '● Connected' : '○ Offline'} · Elasticsearch</div>
      <div class="el-status-pill ${mcpOk ? 'ok' : 'warn'}">${mcpOk ? '● MCP connected' : '○ Direct SDK'} · Agent Builder</div>
      <div class="el-status-pill ${s.percolator_ready ? 'ok' : 'warn'}">${s.percolator_ready ? '● Active' : '○ Inactive'} · Percolator alerts</div>
    `;
  }

  // Capability cards grouped by category — live ✓/✗
  const grid = document.getElementById('el-cap-grid');
  const sub  = document.getElementById('el-cap-sub');
  const caps = s.capabilities || [];
  if (sub) sub.textContent = `${caps.filter(c => c.active).length}/${caps.length} active`;
  if (grid) {
    grid.innerHTML = caps.map(c => `
      <div class="el-cap-card ${c.active ? 'active' : 'inactive'}">
        <div class="el-cap-top">
          <span class="el-cap-dot ${c.active ? 'on' : 'off'}"></span>
          <span class="el-cap-cat">${esc(c.category || '')}</span>
        </div>
        <div class="el-cap-label">${esc(c.label || c.id)}</div>
        <div class="el-cap-detail">${esc(c.detail || '')}</div>
      </div>
    `).join('');
  }

  // Index document counts
  const idxGrid = document.getElementById('el-idx-grid');
  const counts  = s.index_document_counts || {};
  const descs   = s.indices || {};
  if (idxGrid) {
    idxGrid.innerHTML = Object.keys(descs).map(idx => {
      const n = counts[idx];
      const display = (n === undefined || n < 0) ? '-' : Number(n).toLocaleString();
      return `
        <div class="el-idx-card">
          <div class="el-idx-count">${display}</div>
          <div class="el-idx-name">${esc(idx)}</div>
          <div class="el-idx-desc">${esc(descs[idx] || '')}</div>
        </div>`;
    }).join('');
  }

  // MCP + Agent Builder tools + ES|QL queries + retrieval strategies
  const mcpEl = document.getElementById('el-mcp');
  if (mcpEl) {
    const tools  = s.elastic_mcp_tools || [];
    const abt    = s.agent_builder_tools || [];
    const esql   = s.esql_queries || [];
    const strats = s.retrieval_strategies || [];
    const chips = arr => arr.length
      ? arr.map(t => `<span class="el-chip">${esc(t)}</span>`).join('')
      : '<span class="el-chip el-chip-empty">none discovered</span>';
    mcpEl.innerHTML = `
      <div class="el-mcp-block">
        <div class="el-mcp-key">MCP endpoint</div>
        <code class="el-mcp-val">${esc(s.elastic_mcp_endpoint || 'not configured')}</code>
      </div>
      <div class="el-mcp-block"><div class="el-mcp-key">Discovered MCP tools</div><div class="el-chips">${chips(tools)}</div></div>
      <div class="el-mcp-block"><div class="el-mcp-key">Custom Agent Builder tools (provisioned)</div><div class="el-chips">${chips(abt)}</div></div>
      <div class="el-mcp-block"><div class="el-mcp-key">Retrieval strategy ladder</div><div class="el-chips">${chips(strats)}</div></div>
      <div class="el-mcp-block"><div class="el-mcp-key">ES|QL queries per analysis</div><div class="el-chips">${chips(esql)}</div></div>
      <div class="el-mcp-block">
        <div class="el-mcp-key">Inference endpoints</div>
        <div class="el-chips">
          <span class="el-chip">${esc(s.elser_inference_endpoint || '')}</span>
          <span class="el-chip">${esc(s.reranking_inference_endpoint || '')}</span>
        </div>
      </div>
    `;
  }
}

function renderElasticInsights(data) {
  const el = document.getElementById('el-insights');
  if (!el) return;
  if (!data || !data.available) {
    el.innerHTML = '<p class="el-empty">No analyses indexed yet. Run a property to populate cross-property intelligence.</p>';
    return;
  }
  const ins = data.insights || {};
  const stats = ins.score_stats || {};
  const pct   = ins.score_percentiles || {};
  const riskColors = { LOW: '#22c55e', MEDIUM: '#eab308', HIGH: '#f97316', CRITICAL: '#ef4444' };

  // Headline stat tiles
  const tiles = `
    <div class="el-ins-tiles">
      <div class="el-ins-tile"><div class="el-ins-num">${stats.count != null ? Number(stats.count).toLocaleString() : '-'}</div><div class="el-ins-lbl">Properties analysed</div></div>
      <div class="el-ins-tile"><div class="el-ins-num">${stats.avg != null ? Math.round(stats.avg) : '-'}</div><div class="el-ins-lbl">Avg risk score</div></div>
      <div class="el-ins-tile"><div class="el-ins-num">${pct['90.0'] != null ? Math.round(pct['90.0']) : (pct['90'] != null ? Math.round(pct['90']) : '-')}</div><div class="el-ins-lbl">90th pct score</div></div>
      <div class="el-ins-tile"><div class="el-ins-num">${ins.unique_counties != null ? ins.unique_counties : '-'}</div><div class="el-ins-lbl">Unique counties</div></div>
    </div>`;

  // Risk-level distribution bars
  const dist = ins.risk_level_distribution || [];
  const distMax = Math.max(1, ...dist.map(d => d.count));
  const distHtml = dist.length ? `
    <div class="el-ins-block">
      <div class="el-ins-h">Risk-level distribution <span class="el-sub">terms aggregation</span></div>
      ${dist.map(d => `
        <div class="el-bar-row">
          <span class="el-bar-key" style="color:${riskColors[d.key] || 'var(--text2)'}">${esc(d.key)}</span>
          <span class="el-bar-track"><span class="el-bar-fill" style="width:${(d.count / distMax * 100).toFixed(0)}%;background:${riskColors[d.key] || 'var(--accent)'}"></span></span>
          <span class="el-bar-val">${d.count}</span>
        </div>`).join('')}
    </div>` : '';

  // Top flags
  const flags = ins.top_flags || [];
  const flagMax = Math.max(1, ...flags.map(f => f.count));
  const flagsHtml = flags.length ? `
    <div class="el-ins-block">
      <div class="el-ins-h">Most common risk flags <span class="el-sub">terms aggregation</span></div>
      ${flags.map(f => `
        <div class="el-bar-row">
          <span class="el-bar-key el-bar-key-wide">${esc(f.key)}</span>
          <span class="el-bar-track"><span class="el-bar-fill" style="width:${(f.count / flagMax * 100).toFixed(0)}%"></span></span>
          <span class="el-bar-val">${f.count}</span>
        </div>`).join('')}
    </div>` : '';

  // Top states
  const states = ins.top_states || [];
  const statesHtml = states.length ? `
    <div class="el-ins-block">
      <div class="el-ins-h">Top states <span class="el-sub">terms aggregation</span></div>
      <div class="el-chips">${states.map(st => `<span class="el-chip">${esc(st.key)} · ${st.count}</span>`).join('')}</div>
    </div>` : '';

  // Significant terms by band
  const sig = data.significant_terms_by_band || {};
  const sigHtml = Object.keys(sig).length ? `
    <div class="el-ins-block">
      <div class="el-ins-h">Statistically distinctive flags by band <span class="el-sub">significant_terms</span></div>
      ${Object.entries(sig).map(([band, items]) => `
        <div class="el-sig-band">
          <span class="el-sig-band-name" style="color:${riskColors[band] || 'var(--text2)'}">${esc(band)}</span>
          <div class="el-chips">${items.map(i => `<span class="el-chip">${esc(i.flag)} <em>×${i.score}</em></span>`).join('')}</div>
        </div>`).join('')}
    </div>` : '';

  el.innerHTML = tiles + distHtml + flagsHtml + statesHtml + sigHtml;
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
      addQAMessage('Could not get an answer. Please try again.', 'assistant error');
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
  const drawer = document.getElementById('log-drawer');
  if (drawer) hide(drawer);
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
  btnTheme.textContent = theme === 'dark' ? 'Light' : 'Dark';
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
    toast('Share link created, valid for 90 days', 'success');
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
      toast('Added to watchlist, re-analysed every 24 h', 'success');
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
          Watching since ${item.watched_at ? new Date(item.watched_at).toLocaleDateString() : '-'}
          &nbsp;·&nbsp;
          Last checked: ${item.last_checked ? new Date(item.last_checked).toLocaleDateString() : '-'}
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
      <p><strong>Model:</strong> ${esc(_healthData.gemini_model || '-')} &nbsp;|&nbsp; <strong>Fallback:</strong> ${esc(_healthData.fallback_model || '-')}</p>
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
        ${esc(g.term)} <span class="glossary-short">· ${esc(g.short)}</span>
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
  const recEmoji    = { A: '', B: '', NEITHER: '' };
  const recLabel    = recommended === 'A'
    ? (a.normalized_address || 'Property A')
    : recommended === 'B'
    ? (b.normalized_address || 'Property B')
    : 'Neither; proceed with caution';

  const verdictEl = document.getElementById('compare-verdict');
  verdictEl.innerHTML = `
    <div class="cmp-verdict-badge" style="border-color:${recColors[recommended]}">
      <div class="cmp-verdict-emoji">${recEmoji[recommended]}</div>
      <div class="cmp-verdict-content">
        <div class="cmp-verdict-label">Our recommendation: <strong style="color:${recColors[recommended]}">${recLabel}</strong></div>
        <div class="cmp-verdict-conf">Confidence: ${esc(rec.confidence || '-')}</div>
        <div class="cmp-verdict-headline">${esc(rec.headline || '')}</div>
        <p class="cmp-verdict-rationale">${esc(rec.rationale || '')}</p>
        ${rec.deal_breaker ? `<div class="cmp-deal-breaker">Deal-breaker: ${esc(rec.deal_breaker)}</div>` : ''}
      </div>
    </div>
  `;

  renderCompareCol(document.getElementById('compare-col-a'), a, 'A', recommended, rec.a_pros, rec.a_cons);
  renderCompareCol(document.getElementById('compare-col-b'), b, 'B', recommended, rec.b_pros, rec.b_cons);

  show(document.getElementById('compare-results'));
}

function renderCompareCol(el, report, label, recommended, pros, cons) {
  const score   = typeof report.buyer_risk_score === 'number' ? report.buyer_risk_score : '-';
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
    ? `<div class="cmp-flags">${flags.map(f => `<div class="cmp-flag">${esc(f)}</div>`).join('')}</div>`
    : '';

  const nbhdScore = nbhd.neighborhood_score != null ? `${nbhd.neighborhood_score}/100` : '-';
  const debateRec = debate.buy_recommendation ? `${debate.buy_recommendation} (${debate.confidence || '?'})` : '-';

  el.innerHTML = `
    <div class="cmp-col-inner ${isWinner ? 'cmp-winner' : ''}">
      ${isWinner ? '<div class="cmp-winner-badge">Recommended</div>' : ''}
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
