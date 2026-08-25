"""Wiki Memory v3 — закладка «Поиск по памяти» (read-only preview).

Отдельный модуль дашборда: поисковая страница + JSON-адаптер к боевому
конвейеру плагина wiki-context. Показывает ровно то, что вставилось бы в
контекст модели (<wiki-memory>), в человекочитаемом виде: решение гейта,
хиты, чанки главной с косинус-скорами, карта страниц, сырой инжект.

Строго read-only: плагин вызывается через build_preview(), который НЕ пишет
cache.json, wiki_injects.jsonl и не пишет поисковые события. Логика инжекта
живёт в плагине (единственный источник правды) — этот модуль только вызывает
и рисует. Fail-open: любая ошибка → {"error": ...} без падения сервера.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading

from .dashboard_sections import bi, hint

MAX_QUERY_LEN = 500

_PLUGIN_MODULE_NAME = "wiki_context_preview_plugin"

_plugin_lock = threading.Lock()
_plugin_mod = None


def _plugin_candidates() -> list:
    """Кандидаты пути к плагину wiki-context (первый существующий)."""
    out = []
    env = os.environ.get("WIKI_CONTEXT_PLUGIN")
    if env:
        out.append(env)
    home = os.environ.get("HERMES_HOME") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local", "hermes")
    out.append(os.path.join(home, "plugins", "wiki-context", "__init__.py"))
    out.append(os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "plugins", "wiki-context", "__init__.py")))
    return out


def _load_plugin():
    """Загрузить модуль плагина wiki-context через importlib (кэш, потокобезопасно).

    Плагин нельзя импортировать обычным способом (дефис в имени), поэтому
    spec_from_file_location. register() НЕ вызывается — только функции.
    """
    global _plugin_mod
    if _plugin_mod is not None:
        return _plugin_mod
    with _plugin_lock:
        if _plugin_mod is not None:
            return _plugin_mod
        last_err = "not found"
        for cand in _plugin_candidates():
            if cand and os.path.isfile(cand):
                try:
                    spec = importlib.util.spec_from_file_location(
                        _PLUGIN_MODULE_NAME, cand)
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[_PLUGIN_MODULE_NAME] = mod
                    spec.loader.exec_module(mod)
                    _plugin_mod = mod
                    return mod
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
        raise RuntimeError(f"wiki-context plugin unavailable ({last_err})")


def memory_preview(query: str) -> dict:
    """Предпросмотр инжекта по запросу (JSON для /api/memory-search).

    Валидация/обрезка q → build_preview() плагина (тот же поток, что боевой
    хук, но без каких-либо записей). Fail-open: ошибка → {"error": ...}.
    """
    q = (query or "").strip()
    if len(q) > MAX_QUERY_LEN:
        q = q[:MAX_QUERY_LEN]
    if not q:
        return {
            "query": "",
            "gate": {"decision": "skip", "reason": "empty"},
            "hits": [],
            "card": [],
            "main": None,
            "inject": "",
            "meta": {"duration_ms": 0.0, "top_k": None, "api_state": "",
                     "degraded": False, "warnings": []},
        }
    try:
        mod = _load_plugin()
    except Exception as e:
        return {"query": q, "error": f"plugin-unavailable: {e}"}
    try:
        data = mod.build_preview(q)
        return data if isinstance(data, dict) else {"query": q, "error": "bad-preview"}
    except Exception as e:
        return {"query": q, "error": f"{type(e).__name__}: {e}"}


def memory_page_html() -> str:
    """Разметка страницы «Поиск по памяти» (строка поиска + контейнеры)."""
    return (
        '<div class="section" id="memory-search-section">'
        + "<h2>" + bi("Поиск по памяти", "Memory search") + "</h2>"
        + hint(
            "Тот же конвейер, что у плагина wiki-context: введите запрос — увидите, "
            "что именно встанет в контекст модели (гейт, хиты, чанки, сырой инжект). "
            "Только просмотр — ничего не записывается.",
            "Same pipeline as the wiki-context plugin: type a query to see exactly "
            "what would be injected into the model context (gate, hits, chunks, raw "
            "inject). Read-only — nothing is written.")
        + '<form class="memory-search-form" onsubmit="return memorySearchSubmit(event)">'
        + '<input type="text" id="memory-q" class="memory-q" autocomplete="off" '
        + 'data-ph-ru="Найти в памяти…" data-ph-en="Search memory…" '
        + 'placeholder="Найти в памяти…">'
        + '<button type="submit" id="memory-go" class="memory-go">'
        + bi("Искать", "Search") + "</button>"
        + "</form>"
        + '<div id="memory-status" class="memory-status" style="display:none">'
        + '<span class="memory-spinner"></span>'
        + bi("Ищу…", "Searching…") + "</div>"
        + '<div id="memory-results"></div>'
        + "</div>"
    )


CSS_MEMORY = """
/* ── Memory search page (2026-08-25) ───────────────────────────────────── */
.dash-tabs { display: flex; gap: 4px; }
.dash-tab {
  background: transparent; color: var(--muted);
  border: 1px solid var(--border); border-radius: 6px;
  padding: 6px 14px; cursor: pointer; font-size: 12px;
}
.dash-tab.on { background: var(--brass); color: #fff; border-color: var(--brass); }
.dash-tab:hover:not(.on) { color: var(--ink); border-color: var(--muted); }
.memory-search-form { display: flex; gap: 8px; margin-top: 8px; }
.memory-q {
  flex: 1; background: var(--bg); color: var(--ink);
  border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 14px; font-size: 14px;
}
.memory-go {
  background: var(--sage); color: #fff; border: none; border-radius: 8px;
  padding: 8px 18px; cursor: pointer;
}
.memory-go:disabled { opacity: 0.6; cursor: wait; }
.memory-status {
  display: flex; align-items: center; gap: 8px;
  color: var(--muted); margin-top: 10px; font-size: 12px;
}
.memory-spinner {
  width: 14px; height: 14px; border: 2px solid var(--border);
  border-top-color: var(--brass); border-radius: 50%;
  display: inline-block; animation: memspin 0.8s linear infinite;
}
@keyframes memspin { to { transform: rotate(360deg); } }
.memory-gate { display: flex; align-items: center; gap: 10px; margin: 14px 0 2px; flex-wrap: wrap; }
.memory-why { color: var(--muted); font-size: 12px; }
.memory-badge {
  display: inline-block; padding: 3px 10px; border-radius: 999px;
  font-family: Cascadia Mono, Consolas, monospace;
  font-size: 11px; font-weight: 700; color: #fff;
}
.memory-badge.sage { background: var(--sage); }
.memory-badge.brass { background: var(--brass); }
.memory-badge.brick { background: var(--brick); }
.memory-card {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px 14px; margin-top: 12px;
}
.memory-card h3 { font-size: 13px; color: var(--ink); margin-bottom: 6px; }
.memory-path {
  font-family: Cascadia Mono, Consolas, monospace;
  font-size: 11px; color: var(--muted); word-break: break-all;
}
.memory-chunk {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 6px; padding: 10px 12px; margin-top: 8px;
}
.memory-chunk-score {
  font-family: Cascadia Mono, Consolas, monospace;
  font-size: 11px; color: var(--brass);
}
.memory-chunk-text {
  margin-top: 6px; white-space: pre-wrap; word-break: break-word;
  color: var(--ink); font-size: 12.5px; max-height: 240px; overflow: auto;
}
.memory-tags { color: var(--muted); font-size: 11px; margin-top: 2px; }
.memory-hits { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }
.memory-hits th {
  text-align: left; color: var(--muted); font-weight: 600;
  padding: 6px 8px; border-bottom: 1px solid var(--border);
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
}
.memory-hits td {
  padding: 6px 8px; border-bottom: 1px solid #2A241C;
  font-family: Cascadia Mono, Consolas, monospace;
}
.memory-raw pre {
  background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
  padding: 12px; overflow: auto; max-height: 400px; color: var(--ink);
  white-space: pre-wrap; word-break: break-word; margin-top: 8px;
}
.memory-copy {
  background: var(--muted); color: #fff; border: none; border-radius: 6px;
  padding: 4px 12px; cursor: pointer; font-size: 11px; margin-top: 8px;
}
.memory-note { color: var(--muted); font-size: 12px; margin-top: 10px; }
"""

JS_MEMORY_SEARCH = """
// ── Memory search page (2026-08-25) ───────────────────────────────────────
function __dashShowPage(name) {
  var mem = document.getElementById('page-memory');
  var con = document.getElementById('page-console');
  var tm = document.getElementById('tab-memory');
  var tc = document.getElementById('tab-console');
  var onMem = (name === 'memory');
  if (mem) mem.hidden = onMem ? false : true;
  if (con) con.hidden = onMem ? true : false;
  if (tm) tm.classList.toggle('on', onMem);
  if (tc) tc.classList.toggle('on', !onMem);
  try { localStorage.setItem('wiki3_page', onMem ? 'memory' : 'console'); } catch (e) {}
}
function showMemory() {
  __dashShowPage('memory');
  if (location.hash !== '#memory') {
    try { history.replaceState(null, '', '#memory'); } catch (e) {}
  }
}
function showConsole() {
  __dashShowPage('console');
  if (location.hash) {
    try { history.replaceState(null, '', location.pathname); } catch (e) {}
  }
}
(function () {
  var p = null;
  try { p = localStorage.getItem('wiki3_page'); } catch (e) {}
  if (p === 'memory' || location.hash === '#memory') {
    __dashShowPage('memory');
    var q = null;
    try { q = localStorage.getItem('wiki3_memq'); } catch (e) {}
    var inp = q ? document.getElementById('memory-q') : null;
    if (q && inp && !inp.value) {
      inp.value = q;
      var f = document.querySelector('.memory-search-form');
      if (f) f.dispatchEvent(new Event('submit', {cancelable: true}));
    }
  }
})();

function __memEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function memorySearchSubmit(ev) {
  ev.preventDefault();
  var inp = document.getElementById('memory-q');
  var q = inp ? inp.value.trim() : '';
  if (!q) return false;
  try { localStorage.setItem('wiki3_memq', q); } catch (e) {}
  var box = document.getElementById('memory-results');
  var st = document.getElementById('memory-status');
  var go = document.getElementById('memory-go');
  if (st) st.style.display = 'flex';
  if (go) go.disabled = true;
  if (box) box.innerHTML = '';
  fetch('/api/memory-search?q=' + encodeURIComponent(q))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (st) st.style.display = 'none';
      if (go) go.disabled = false;
      if (box) box.innerHTML = memoryRender(d);
    })
    .catch(function () {
      if (st) st.style.display = 'none';
      if (go) go.disabled = false;
      if (box) box.innerHTML = '<div class="memory-note" style="color:var(--brick)">'
        + __bi('Ошибка сети', 'Network error') + '</div>';
    });
  return false;
}

function memoryGateBadge(d) {
  var g = (d && d.gate) || {};
  var dec = g.decision || 'show';
  var map = {
    show:           { cls: 'sage',  ru: 'SHOW · показываем',             en: 'SHOW · injecting' },
    low_confidence: { cls: 'brass', ru: 'LOW CONFIDENCE · только карта', en: 'LOW CONFIDENCE · map only' },
    skip:           { cls: 'brick', ru: 'SKIP · ничего не вставляется',  en: 'SKIP · nothing injected' }
  };
  var m = map[dec] || { cls: 'sage', ru: String(dec).toUpperCase(), en: String(dec).toUpperCase() };
  var label = (document.body.getAttribute('data-lang') === 'en') ? m.en : m.ru;
  var why = [];
  if (g.tokens != null) why.push(__bi('|T| значимых слов', '|T| significant words') + ': ' + g.tokens);
  if (g.corpus_hits != null) why.push(__bi('в корпусе A', 'corpus A') + ': ' + g.corpus_hits);
  if (g.reason) why.push(__memEsc(g.reason));
  return '<div class="memory-gate">'
    + '<span class="memory-badge ' + m.cls + '">' + __memEsc(label) + '</span>'
    + (why.length ? '<span class="memory-why">' + why.join(' · ') + '</span>' : '')
    + '</div>';
}

function memoryMeta(d) {
  var mt = (d && d.meta) || {};
  var bits = [];
  if (mt.duration_ms != null) bits.push(__bi('длительность', 'duration') + ': ' + mt.duration_ms + ' ' + __bi('мс', 'ms'));
  if (mt.top_k != null) bits.push('top_k: ' + mt.top_k);
  if (mt.api_state) bits.push('api: ' + __memEsc(mt.api_state));
  if (mt.degraded) bits.push('<span style="color:var(--brass)">⚠ '
    + __bi('деградация (keyword-only)', 'degraded (keyword-only)') + '</span>');
  if (mt.warnings && mt.warnings.length) {
    bits.push(__bi('предупреждения', 'warnings') + ': ' + __memEsc(mt.warnings.join(', ')));
  }
  return bits.length ? '<div class="memory-note">' + bits.join(' · ') + '</div>' : '';
}

function memoryHits(d) {
  var hits = (d && d.hits) || [];
  if (!hits.length) return '';
  var rows = '';
  for (var i = 0; i < hits.length; i++) {
    var h = hits[i];
    rows += '<tr><td>' + (i + 1) + '</td>'
      + '<td>' + __memEsc(h.title || h.slug) + '</td>'
      + '<td>' + Number(h.score || 0).toFixed(4) + '</td>'
      + '<td>' + __memEsc(h.source || '') + '</td></tr>';
  }
  return '<div class="memory-card"><h3>' + __bi('Хиты поиска', 'Search hits') + '</h3>'
    + '<table class="memory-hits"><tr><th>#</th>'
    + '<th>' + __bi('страница', 'page') + '</th><th>score</th>'
    + '<th>' + __bi('источник', 'source') + '</th></tr>' + rows + '</table></div>';
}

function memoryMain(d) {
  var m = d && d.main;
  if (!m) return '';
  var h = '<div class="memory-card"><h3>' + __bi('Главная страница', 'Main page')
    + ': ' + __memEsc(m.title || m.slug) + '</h3>'
    + '<div class="memory-path">' + __memEsc(m.path || '') + '</div>';
  var chunks = m.chunks || [];
  if (chunks.length) {
    h += '<div class="memory-note">'
      + __bi('Релевантные чанки (косинус к запросу)', 'Relevant chunks (cosine to query)')
      + ':</div>';
    for (var i = 0; i < chunks.length; i++) {
      var c = chunks[i];
      h += '<div class="memory-chunk">'
        + '<span class="memory-chunk-score">#' + c.idx + ' · cos '
        + Number(c.score).toFixed(3) + '</span>'
        + '<div class="memory-chunk-text">' + __memEsc(c.text) + '</div></div>';
    }
  } else {
    h += '<div class="memory-note">' + __bi('Чанки не отобраны', 'No chunks selected')
      + (m.chunk_reason ? ' — ' + __memEsc(m.chunk_reason) : '') + '</div>';
  }
  return h + '</div>';
}

function memoryCardMap(d) {
  var card = (d && d.card) || [];
  if (!card.length) return '';
  var items = '';
  for (var i = 0; i < card.length; i++) {
    var c = card[i];
    items += '<div class="memory-chunk"><div>' + __memEsc(c.title || c.slug) + '</div>'
      + '<div class="memory-tags">'
      + (c.tags && c.tags.length
          ? __bi('теги', 'tags') + ': ' + __memEsc(c.tags.join(', '))
          : __bi('без тегов', 'no tags'))
      + '</div>'
      + '<div class="memory-path">' + __memEsc(c.path || '') + '</div></div>';
  }
  return '<div class="memory-card"><h3>'
    + __bi('Карта связанных страниц', 'Map of related pages') + '</h3>' + items + '</div>';
}

function memoryRaw(d) {
  var inj = (d && d.inject) || '';
  if (!inj) return '';
  return '<div class="memory-card memory-raw"><h3>'
    + __bi('Сырой инжект (ровно то, что получит модель)', 'Raw inject (exactly what the model gets)')
    + '</h3><pre id="memory-raw-inject">' + __memEsc(inj) + '</pre>'
    + '<button type="button" id="memory-copy-btn" class="memory-copy" onclick="memoryCopyInject()">'
    + __bi('Копировать', 'Copy') + '</button></div>';
}

function memoryRender(d) {
  if (!d || typeof d !== 'object') return '';
  if (d.error) {
    return '<div class="memory-card" style="border-color:var(--brick)"><h3 style="color:var(--brick)">'
      + __bi('Ошибка', 'Error') + '</h3><div class="memory-note">' + __memEsc(d.error) + '</div></div>';
  }
  var html = memoryGateBadge(d) + memoryMeta(d);
  var dec = (d.gate && d.gate.decision) || 'show';
  if (dec === 'skip') {
    html += '<div class="memory-note">'
      + __bi('Запрос вне домена памяти (A = 0) — &lt;wiki-memory&gt; не вставляется вовсе.',
             'Query is off-domain (A = 0) — &lt;wiki-memory&gt; is not injected at all.')
      + '</div>';
    return html;
  }
  html += memoryHits(d) + memoryMain(d) + memoryCardMap(d) + memoryRaw(d);
  if (!d.inject) {
    html += '<div class="memory-note">'
      + __bi('Ничего не вставлено (нет релевантного контекста).', 'Nothing injected (no relevant context).')
      + '</div>';
  }
  return html;
}

function memoryCopyInject() {
  var pre = document.getElementById('memory-raw-inject');
  var btn = document.getElementById('memory-copy-btn');
  if (!pre) return;
  var txt = pre.textContent;
  function ok() {
    if (btn) btn.textContent = (document.body.getAttribute('data-lang') === 'en')
      ? 'Copied ✓' : 'Скопировано ✓';
  }
  function fallback() {
    var ta = document.createElement('textarea');
    ta.value = txt;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
    ok();
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(ok, fallback);
  } else {
    fallback();
  }
}
"""
