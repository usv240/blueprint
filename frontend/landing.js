/* BLUEPRINT landing page — routes into the app and pulls live numbers from /api/*.
 * Nothing is hardcoded: the stakes stats and live counts come from the backend.
 */
'use strict';

const APP = '/app.html';

function gotoApp(address) {
  const a = (address || '').trim();
  window.location.href = a.length >= 5 ? `${APP}?address=${encodeURIComponent(a)}` : APP;
}

document.addEventListener('DOMContentLoaded', () => {
  // Theme (shared key with the app)
  applyTheme(localStorage.getItem('bp-theme') || 'dark');
  document.getElementById('btn-theme')?.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    localStorage.setItem('bp-theme', next);
  });

  const yr = document.getElementById('lp-year');
  if (yr) yr.textContent = new Date().getFullYear();

  // Hero + CTA search forms → hand off to the app with the address
  document.getElementById('hero-search')?.addEventListener('submit', e => {
    e.preventDefault(); gotoApp(document.getElementById('hero-address').value);
  });
  document.getElementById('cta-search')?.addEventListener('submit', e => {
    e.preventDefault(); gotoApp(document.getElementById('cta-address').value);
  });

  // Demo chips → launch the app pre-loaded with that address
  document.querySelectorAll('.btn-preset').forEach(btn => {
    btn.addEventListener('click', () => gotoApp(btn.dataset.address));
  });

  // Smooth-scroll for in-page anchors
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', e => {
      const el = document.querySelector(a.getAttribute('href'));
      if (el) { e.preventDefault(); el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    });
  });

  setupReveal();
  loadLiveStats();
  loadStakes();
});

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById('btn-theme');
  if (btn) btn.textContent = theme === 'dark' ? 'Light' : 'Dark';
}

// Scroll-reveal via IntersectionObserver (respects reduced-motion through CSS)
function setupReveal() {
  const els = document.querySelectorAll('.reveal');
  if (!('IntersectionObserver' in window)) { els.forEach(el => el.classList.add('in')); return; }
  const io = new IntersectionObserver((entries) => {
    entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } });
  }, { threshold: 0.12 });
  els.forEach(el => io.observe(el));
}

// Live "N properties analysed" from /api/stats
async function loadLiveStats() {
  try {
    const d = await (await fetch('/api/stats')).json();
    const wrap = document.getElementById('lp-livestat');
    if (typeof d.total_analyses === 'number') {
      countTo('lp-stat-total', d.total_analyses);
      countTo('lp-stat-24h', d.analyses_24h || 0);
      const noun = document.getElementById('lp-stat-noun');
      if (noun) noun.textContent = d.total_analyses === 1 ? 'property' : 'properties';
      if (wrap) wrap.style.visibility = 'visible';
    }
  } catch (_) { /* ignore — section just stays hidden */ }
  // Gemini model name on the powered-by strip
  try {
    const h = await (await fetch('/api/health')).json();
    const g = document.getElementById('lp-gemini');
    if (g && h.gemini_model) g.textContent = `Google ADK + ${h.gemini_model}`;
  } catch (_) {}
}

// Stakes stats from /api/about (impact_stats) — data-driven, not hardcoded
async function loadStakes() {
  const grid = document.getElementById('lp-stakes');
  if (!grid) return;
  try {
    const d = await (await fetch('/api/about')).json();
    const stats = Array.isArray(d.impact_stats) ? d.impact_stats : [];
    if (!stats.length) return;
    grid.innerHTML = stats.map(s => `
      <div class="lp-stat-card">
        <div class="lp-stat-num">${esc(s.stat)}</div>
        <div class="lp-stat-label">${esc(s.label)}</div>
        <div class="lp-stat-ctx">${esc(s.context || '')}</div>
      </div>`).join('');
  } catch (_) { /* ignore */ }
}

function countTo(id, target) {
  const el = document.getElementById(id);
  if (!el) return;
  const dur = 900, t0 = performance.now();
  function tick(now) {
    const p = Math.min(1, (now - t0) / dur);
    el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3))).toLocaleString();
    if (p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
