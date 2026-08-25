"""Wiki Memory v3 — dashboard JavaScript scripts."""
from __future__ import annotations

# Language switch for the small bilingual captions (.hint spans).
JS_LANG = """
function __bi(ru, en) {
  return '<span class="bi"><span class="ru">' + ru + '</span><span class="en">' + en + '</span></span>';
}
function applyLang(lang) {
  var l = (lang === 'en') ? 'en' : 'ru';
  document.body.setAttribute('data-lang', l);
  var b = document.getElementById('lang-btn');
  if (b) {
    b.textContent = (l === 'ru') ? 'EN' : 'RU';
    b.title = (l === 'ru')
      ? 'Показать подписи на английском'
      : 'Show captions in Russian';
  }
  var sel = document.getElementById('time-range');
  if (sel) {
    for (var i = 0; i < sel.options.length; i++) {
      var o = sel.options[i];
      var t = o.getAttribute('data-' + l);
      if (t) o.textContent = t;
    }
  }
  var keyInput = document.getElementById('cfg-ex-key');
  if (keyInput) {
    var ph = keyInput.getAttribute('data-ph-' + l);
    if (ph) keyInput.placeholder = ph;
  }
  try { localStorage.setItem('wiki_dash_lang', l); } catch (e) {}
}
function toggleLang() {
  var cur = document.body.getAttribute('data-lang') === 'en' ? 'en' : 'ru';
  applyLang(cur === 'en' ? 'ru' : 'en');
}
(function () {
  var saved = 'ru';
  try { saved = localStorage.getItem('wiki_dash_lang') || 'ru'; } catch (e) {}
  applyLang(saved);
})();
"""

# Autorefresh reload keeps the scroll position across location.reload().
JS_AUTOREFRESH = """
var __arTimer = null;
function __reloadKeepScroll() {
  var pm = document.getElementById('page-memory');
  if (pm && !pm.hidden) return;
  try { sessionStorage.setItem('wiki3_scrollY', String(window.scrollY)); } catch (e) {}
  location.reload();
}
(function () {
  try {
    var y = sessionStorage.getItem('wiki3_scrollY');
    if (y != null) {
      sessionStorage.removeItem('wiki3_scrollY');
      window.addEventListener('load', function () {
        window.scrollTo(0, parseInt(y, 10) || 0);
      });
    }
  } catch (e) {}
})();
function __arApply(on) {
  var btn = document.getElementById('autorefresh-btn');
  var st = document.getElementById('autorefresh-status');
  var bi = function(ru, enTxt) {
    return '<span class="bi"><span class="ru">' + ru + '</span><span class="en">' + enTxt + '</span></span>';
  };
  if (on) {
    btn.innerHTML = '\\u23f8 ' + bi('Автообновление 60с: ВКЛ', 'Autorefresh 60s: ON');
    btn.style.background = '#79A05E';
    if (st) st.innerHTML = bi('автообновление вкл (каждые 60с)', 'autorefresh on (every 60s)');
  } else {
    btn.innerHTML = '\\u25b6 ' + bi('Автообновление 60с: ВЫКЛ', 'Autorefresh 60s: OFF');
    btn.style.background = '#9A9184';
    if (st) st.innerHTML = bi('автообновление выкл', 'autorefresh off');
  }
}
function toggleAutorefresh() {
  if (__arTimer) {
    clearInterval(__arTimer);
    __arTimer = null;
    try { sessionStorage.removeItem('wiki3_autorefresh'); } catch(e) {}
    __arApply(false);
  } else {
    __arTimer = setInterval(__reloadKeepScroll, 60000);
    try { sessionStorage.setItem('wiki3_autorefresh', '1'); } catch(e) {}
    __arApply(true);
  }
}
try {
  if (sessionStorage.getItem('wiki3_autorefresh') === '1') {
    __arTimer = setInterval(__reloadKeepScroll, 60000);
    __arApply(true);
  } else {
    __arApply(false);
  }
} catch(e) {}
"""

# Live metrics poll: /api/status returns a NESTED object, so each data-dash
# key maps to its path inside the JSON. Values are written ONLY when they
# actually changed, to avoid needless DOM churn (scroll jumps).
JS_POLL = """
var DASH_PATHS = {
  api_state: ['health', 'api_state'],
  last_indexed: ['health', 'last_indexed_at'],
  db_size_mb: ['health', 'db_size_mb'],
  hit_rate: ['effectiveness', 'hit_rate'],
  coverage: ['effectiveness', 'coverage'],
  pages: ['database', 'pages'],
  sessions: ['database', 'sessions'],
  orphans: ['database', 'orphans'],
  chunks: ['database', 'chunks'],
  vectors: ['database', 'vectors'],
  embed_calls: ['api', 'embed_calls'],
  embed_errors: ['api', 'embed_errors'],
  cache_hit_rate: ['api', 'cache_hit_rate'],
  chat_api_errors: ['api', 'chat_errors'],
  search_fallback: ['api', 'search_fallback'],
  embed_errors_24h: ['health', 'api_errors_24h']
};
function __dashFmt(key, value) {
  if (value === null || value === undefined) return null;
  var en = document.body.getAttribute('data-lang') === 'en';
  if (key === 'db_size_mb') return Number(value).toFixed(2) + (en ? ' MB' : ' МБ');
  if (key === 'hit_rate' || key === 'coverage' || key === 'cache_hit_rate') {
    return (Number(value) * 100).toFixed(1) + '%';
  }
  if (key === 'last_indexed') {
    var d = new Date(Number(value) * 1000);
    function two(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + '-' + two(d.getMonth() + 1) + '-' + two(d.getDate())
      + ' ' + two(d.getHours()) + ':' + two(d.getMinutes()) + (en ? ' (local)' : ' (мест.)');
  }
  if (typeof value === 'number') {
    return String(value % 1 === 0 ? Math.floor(value) : value);
  }
  return String(value);
}
if (location.protocol === "http:") {
  setInterval(function() {
    fetch('/api/status')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        if (!d || typeof d !== 'object' || d.error) return;
        for (var k in DASH_PATHS) {
          var path = DASH_PATHS[k];
          var cur = d;
          for (var i = 0; i < path.length; i++) {
            if (cur && typeof cur === 'object' && (path[i] in cur)) cur = cur[path[i]];
            else { cur = undefined; break; }
          }
          if (cur === undefined) continue;
          var el = document.querySelector('[data-dash="' + k + '"]');
          var txt = __dashFmt(k, cur);
          if (el && txt !== null && el.textContent !== txt) el.textContent = txt;
        }
      })
      .catch(function() { });
  }, 5000);
}
// Inject block: rebuild the DOM only when the content really changed,
// otherwise every poll would destroy/recreate nodes above the viewport
// and break scroll anchoring (page jumped to top while scrolling).
var __injectLastHtml = null;
function refreshInject() {
  var box = document.getElementById('inject-content');
  if (!box) return;
  fetch('/api/injects')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var html;
      if (!d || typeof d !== 'object' || !d.inject) {
        html = "<p style='color:#8b949e'>" + __bi('Ещё нет инжектов (wiki_injects.jsonl пуст).', 'No injects yet (wiki_injects.jsonl is empty).') + "</p>";
      } else {
        var q = (d.query || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        var inj = (d.inject || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        var hits = d.hits != null ? d.hits : 0;
        var iso = d.iso || '';
        html = "<div style='background:#161b22;border:1px solid #30363d;border-radius:6px;"
          + "padding:10px 12px;margin-bottom:12px'>"
          + "<div style='color:#8b949e;font-size:0.75em;text-transform:uppercase;letter-spacing:0.05em'>"
          + __bi('Запрос пользователя', 'User query') + "</div>"
          + "<div style='color:#f0f6fc;font-size:1.05em;margin-top:4px;white-space:pre-wrap;word-break:break-word'>"
          + q + "</div>"
          + "<div style='color:#8b949e;font-size:0.8em;margin-top:6px'>" + __bi('хитов', 'hits') + ": " + hits
          + (iso ? " · " + iso : "") + "</div>"
          + "</div>"
          + "<div style='color:#8b949e;font-size:0.75em;text-transform:uppercase;letter-spacing:0.05em'>"
          + __bi('Попало в память', 'Injected into memory') + "</div>"
          + "<pre style='background:#17140F;border:1px solid #30363d;border-radius:6px;"
          + "padding:12px;overflow:auto;max-height:400px;color:#EDE6D8;white-space:pre-wrap;word-break:break-word;margin-top:6px'>"
          + inj + "</pre>";
      }
      if (html !== __injectLastHtml) {
        __injectLastHtml = html;
        box.innerHTML = html;
      }
    })
    .catch(function() { });
}
setInterval(refreshInject, 5000);
refreshInject();
"""

JS_CONTROL = """
var __extLastSig = null;
function extRunningUI(on) {
  var live = document.getElementById('ext-live');
  if (live) live.style.display = on ? 'inline-flex' : 'none';
  var dot = document.getElementById('ext-dot');
  if (dot) dot.classList.toggle('on', !!on);
  var bar = document.getElementById('ext-bar');
  if (bar) bar.classList.toggle('on', !!on);
}
function controlExtraction(action, mode) {
  var st = document.getElementById('ext-status');
  if (st) st.innerHTML = __bi('Запускаю…', 'Starting…');
  extRunningUI(true);
  var limit = null;
  var inp = document.getElementById('ext-limit');
  if (inp && inp.value !== '') {
    var n = parseInt(inp.value, 10);
    if (!isNaN(n) && n > 0) limit = n;
  }
  fetch('/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: action, mode: mode || 'normal', limit: limit })
  }).then(function(r) { return r.json(); })
    .then(function(d) {
      if (d && d.ok) {
        if (st && d.pid) {
          st.innerHTML = __bi('Запущено (pid ' + d.pid + ')', 'Started (pid ' + d.pid + ')');
          setTimeout(refreshExtraction, 1200);
        } else {
          refreshExtraction();
        }
      }
      else if (st && d && d.error) st.innerHTML = __bi('Ошибка: ' + d.error, 'Error: ' + d.error);
      else if (st) st.innerHTML = __bi('Ошибка', 'Error');
      if (!(d && d.ok)) extRunningUI(false);
    })
    .catch(function() { if (st) st.innerHTML = __bi('Ошибка сети', 'Network error'); extRunningUI(false); });
}
function refreshExtraction() {
  fetch('/api/control').then(function(r) { return r.json(); })
    .then(function(d) {
      var running = d && d.status && d.status.running;
      var p = (d && d.progress) || {};
      var sig = (running ? 'R' : 'I') + '|' + (p.done || 0) + '/' + (p.total || 0);
      if (sig === __extLastSig) return;
      __extLastSig = sig;
      var st = document.getElementById('ext-status');
      var pr = document.getElementById('ext-progress');
      if (st) st.innerHTML = running ? __bi('Идёт', 'Running') : __bi('Остановлена', 'Stopped');
      if (pr) pr.textContent = (p.done || 0) + '/' + (p.total || 0)
        + ' (' + Number(p.pct || 0).toFixed(1) + '%)';
      extRunningUI(running);
      var fill = document.getElementById('ext-bar-fill');
      if (fill) fill.style.width = Number(p.pct || 0).toFixed(1) + '%';
      var btnStart = document.getElementById('btn-ext-start');
      var btnFull = document.getElementById('btn-ext-full');
      var btnStop = document.getElementById('btn-ext-stop');
      if (btnStart) btnStart.style.display = running ? 'none' : '';
      if (btnFull) btnFull.style.display = running ? 'none' : '';
      if (btnStop) btnStop.style.display = running ? '' : 'none';
    })
    .catch(function(){});
}
setInterval(refreshExtraction, 3000);
refreshExtraction();
function saveConfig(ev, section) {
  ev.preventDefault();
  var body;
  if (section === 'extract') {
    body = {
      section: 'extract',
      url: document.getElementById('cfg-ex-url').value,
      model: document.getElementById('cfg-ex-model').value,
      key: document.getElementById('cfg-ex-key').value || undefined
    };
  } else {
    body = {
      section: 'embed',
      backend: document.getElementById('cfg-emb-backend').value,
      url: document.getElementById('cfg-emb-url').value,
      model: document.getElementById('cfg-emb-model').value
    };
  }
  fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }).then(function(r) { return r.json(); })
    .then(function(d) {
      var en = document.body.getAttribute('data-lang') === 'en';
      if (d && d.ok) {
        alert(en
          ? (d.requires_reindex ? 'Saved. Full re-index required!' : 'Saved')
          : (d.requires_reindex ? 'Сохранено. Требуется пере-индексация!' : 'Сохранено'));
        __reloadKeepScroll();
      } else {
        alert(en ? 'Error: ' + ((d && d.error) || 'unknown') : 'Ошибка: ' + ((d && d.error) || 'неизвестно'));
      }
    })
    .catch(function() { alert(document.body.getAttribute('data-lang') === 'en' ? 'Network error' : 'Ошибка сети'); });
}
"""
