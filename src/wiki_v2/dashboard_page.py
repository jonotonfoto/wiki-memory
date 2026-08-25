"""Wiki Memory v3 — dashboard page rendering."""
from __future__ import annotations

import datetime
import html as html_mod

from .dashboard_analysis import _ts_charts, problems
from .dashboard_charts import chart_legend
from .dashboard_data import cached_metrics, cache_hit_rate
from .dashboard_health import health_snapshot
from .dashboard_js import JS_AUTOREFRESH, JS_CONTROL, JS_LANG, JS_POLL
from .dashboard_memory import CSS_MEMORY, JS_MEMORY_SEARCH, memory_page_html
from .dashboard_render import time_selector_html
from .dashboard_sections import (
    _last_inject,
    _safe_json,
    _section_api,
    _section_components,
    _section_database,
    _section_errors,
    _section_effectiveness,
    _section_night_strip,
    _section_problems,
    bi,
    hint,
)
from .dashboard_styles import CSS
from .effectiveness import coverage, hit_rate
from .status import status as get_status


def render_dashboard(query: str = "", range_: str = "1w") -> str:
    """Return a complete self-contained HTML dashboard string."""
    s = get_status()
    hr = hit_rate()
    cov = coverage()
    from .dashboard_data import _effectiveness_rating
    rating = _effectiveness_rating(hr, cov)
    snap = cached_metrics()
    prob = problems()
    health = health_snapshot()

    from .dashboard_control import extraction_status, progress
    ext_status = extraction_status()
    ext_progress = progress()

    ex = {}
    emb = {}
    try:
        from .dashboard_control import api_config_get
        _cfg = api_config_get()
        ex = _cfg.get("extract", {})
        emb = _cfg.get("embed", {})
    except Exception:
        ex = {}
        emb = {}

    ts = _ts_charts(range_)
    time_sel = time_selector_html(range_)
    inj = _last_inject()
    if inj:
        _q = html_mod.escape(inj.get("query", ""))
        _inj = html_mod.escape(inj.get("inject", ""))
        _hits = inj.get("hits", 0)
        _ts_iso = inj.get("iso", "")
        query_block = (
            "<div style='background:var(--bg);border:1px solid var(--border);border-radius:6px;"
            "padding:10px 12px;margin-bottom:12px'>"
            "<div class='eyebrow'>" + bi("Запрос пользователя", "User query") + "</div>"
            "<div style='color:var(--ink);font-size:1.05em;margin-top:4px;white-space:pre-wrap;word-break:break-word'>"
            + _q + "</div>"
            + ("<div style='color:var(--muted);font-size:0.8em;margin-top:6px'>" + bi("хитов", "hits") + ": " + str(_hits)
               + (" · " + _ts_iso if _ts_iso else "") + "</div>")
            + "</div>"
        )
        inj_html = (
            query_block
            + "<div class='eyebrow'>" + bi("Попало в память", "Injected into memory") + "</div>"
            + "<pre style='background:var(--bg);border:1px solid var(--border);border-radius:6px;"
            + "padding:12px;overflow:auto;max-height:400px;color:var(--ink);white-space:pre-wrap;word-break:break-word;margin-top:6px'>"
            + _inj + "</pre>"
        )
    else:
        inj_html = "<p style='color:var(--muted)'>" + bi("Ещё нет инжектов (wiki_injects.jsonl пуст).", "No injects yet (wiki_injects.jsonl is empty).") + "</p>"

    overall = health.get("overall", "ok")
    overall_color = {"ok": "var(--sage)", "warn": "var(--brass)", "error": "var(--brick)"}.get(overall, "var(--muted)")
    overall_text = {
        "ok": bi("Ночь штатная", "Night OK"),
        "warn": bi("Требует внимания", "Needs attention"),
        "error": bi("Есть проблемы", "Has problems"),
    }.get(overall, bi("Неизвестно", "Unknown"))

    header_html = f"""<div class="sticky-header">
  <div>
    <h1>{bi("WIKI MEMORY · пульт", "WIKI MEMORY · console")}</h1>
    <div style="display:flex;align-items:center;gap:6px;margin-top:2px">
      <span style="width:8px;height:8px;border-radius:50%;background:{overall_color};display:inline-block"></span>
      <span style="font-family:Cascadia Mono,Consolas,monospace;font-size:12px;color:var(--ink)" title="Сводный статус всех компонентов: ок / требует внимания / есть проблемы">{overall_text}</span>
      <span style="color:var(--muted);font-size:11px;margin-left:8px">{bi("Сгенерировано", "Generated")} {datetime.datetime.now().strftime("%H:%M")} · <span id="autorefresh-status" style="color:var(--brass)">{bi("автообновление выкл", "autorefresh off")}</span></span>
    </div>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    <div class="dash-tabs" role="tablist">
      <button type="button" id="tab-console" class="dash-tab on" onclick="showConsole()">{bi("Пульт", "Console")}</button>
      <button type="button" id="tab-memory" class="dash-tab" onclick="showMemory()">{bi("Поиск памяти", "Memory search")}</button>
    </div>
    <span id="ext-live" title="Экстракция фактов выполняется прямо сейчас" style="{'display:inline-flex' if ext_status.get('running') else ''}">
      <span class="ext-pulse-dot"></span>
      <span class="bi"><span class="ru">Экстракция идёт</span><span class="en">Extraction running</span></span>
    </span>
    <button id="lang-btn" type="button" onclick="toggleLang()" class="lang-btn">EN</button>
    <button id="autorefresh-btn" type="button" onclick="toggleAutorefresh()" style="background:var(--muted);color:#fff;border:none;border-radius:6px;padding:6px 12px;cursor:pointer;font-size:12px">▶ <span class="bi"><span class="ru">Автообновление 60с: ВЫКЛ</span><span class="en">Autorefresh 60s: OFF</span></span></button>
    {time_sel}
  </div>
</div>"""

    escaped_query = query

    body_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wiki Memory v3 — Пульт</title>
<style>
{CSS}
{CSS_MEMORY}
</style>
</head>
<body>
{header_html}
<div id="page-console">
{escaped_query and '<p style="color:var(--muted);margin-bottom:16px">' + bi("Поиск", "Search") + ': ' + html_mod.escape(escaped_query) + '</p>' or ''}
{_section_night_strip(health)}
{_section_components(health)}
<div class="grid-2">
  <div>{_section_effectiveness(hr, cov, rating)}</div>
  <div>{_section_errors(health)}</div>
</div>
<div class="section">
  <h2>{bi("Графики", "Charts")}</h2>
  {hint("Два верхних графика — живое состояние системы: слева релевантность того, что реально ушло в память (точка=поиск, шкала 0–1, 0=ничего не вставлено — чем ниже точка, тем слабее хит), справа работа экстракции. Ниже — API-нагрузка. Пустой график = не было событий за период.",
        "Top two charts are live system health: left — relevance of what actually got injected into memory (dot=search, 0–1 scale, 0=nothing injected — the lower the dot, the weaker the hit), right — extraction activity. Below — API load. Empty chart means no events in the period.")}
  <div class="chart-row" style="margin-top:12px">
    <div class="chart-container">
      <h3 style="font-size:13px;color:var(--muted);margin-bottom:4px">{bi("Попадание в память (релевантность)", "Memory hit relevance")}</h3>
      {chart_legend([{"label": bi("релевантность топ-чанка (шкала 0–1) · 0 = не вставлено", "top-chunk score (0–1 scale) · 0 = not injected"), "color": "#C9973B"}])}
      <div id="dash-inject-relevance">{ts.get("inject_relevance", "<p class='muted'>Нет данных</p>")}</div>
    </div>
    <div class="chart-container">
      <h3 style="font-size:13px;color:var(--muted);margin-bottom:4px">{bi("Экстракция (страниц за период)", "Extraction (pages per bucket)")}</h3>
      {chart_legend([{"label": bi("извлечено (ok)", "extracted (ok)"), "color": "#79A05E"},{"label": bi("fallback", "fallback"), "color": "#C25B43"}])}
      <div id="dash-extraction">{ts.get("extraction", "<p class='muted'>Нет данных</p>")}</div>
    </div>
  </div>
  <div class="chart-row" style="margin-top:16px">
    <div class="chart-container">
      <h3 style="font-size:13px;color:var(--muted);margin-bottom:4px">{bi("Эмбеддинги: вызовы и ошибки", "Embeddings: calls & errors")}</h3>
      {chart_legend([{"label": bi("вызовы", "calls"), "color": "#C9973B"},{"label": bi("ошибки", "errors"), "color": "#C25B43"}])}
      <div id="dash-embed-combined">{ts.get("embed_combined", "<p class='muted'>Нет данных</p>")}</div>
    </div>
    <div class="chart-container">
      <h3 style="font-size:13px;color:var(--muted);margin-bottom:4px">{bi("Задержка поиска (мс)", "Search latency (ms)")}</h3>
      <div id="dash-latency">{ts.get("latency", "<p class='muted'>Нет данных</p>")}</div>
    </div>
  </div>
</div>
<div class="grid-2">
  <div>{_section_problems(prob)}</div>
  <div>{_section_database(s)}</div>
</div>
<div class="grid-2">
  <div>{_section_api(snap)}</div>
  <div>
    <div class="section" style="margin-bottom:0">
      <h2>{bi("Экстракция", "Extraction")}</h2>
      {hint("Извлечение фактов из сессий LLM (экстрактором). Прогресс: обработано/всего страниц. Кнопки запускают/останавливают процесс вручную.",
            "LLM fact extraction from sessions. Progress: done/total pages. Buttons start/stop the process manually.")}
      <p style="font-family:Cascadia Mono,Consolas,monospace;display:flex;align-items:center;flex-wrap:wrap">
        <span id="ext-dot" class="{'on' if ext_status.get('running') else ''}"></span>
        <span id="ext-status">{bi('Идёт', 'Running') if ext_status.get('running') else bi('Остановлена', 'Stopped')}</span> · {bi("Прогресс", "Progress")}: <span id="ext-progress">{ext_progress.get('done',0)}/{ext_progress.get('total',0)} ({ext_progress.get('pct',0):.1f}%)</span>
      </p>
      <div class="ext-bar{' on' if ext_status.get('running') else ''}" id="ext-bar"><div id="ext-bar-fill" style="width:{ext_progress.get('pct',0):.1f}%"></div></div>
      <div style="display:flex;gap:8px;margin-top:12px;align-items:center;flex-wrap:wrap">
        <label style="display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:12px">{bi("Страниц за запуск", "Pages per run")}: <input type="number" id="ext-limit" min="1" max="100000" value="5" style="width:70px;background:var(--bg);color:var(--ink);border:1px solid var(--border);padding:4px 8px;border-radius:4px;font-family:Cascadia Mono,Consolas,monospace"></label>
        <button type="button" id="btn-ext-start" onclick="controlExtraction('start','normal')" style="background:var(--sage);color:#fff;border:none;border-radius:6px;padding:6px 14px;cursor:pointer">▶ {bi("Запустить", "Start")}</button>
        <button type="button" id="btn-ext-full" onclick="controlExtraction('start','full')" style="background:var(--brass);color:#fff;border:none;border-radius:6px;padding:6px 14px;cursor:pointer">▶ {bi("Полная пере-экстракция", "Full re-extraction")}</button>
        <button type="button" id="btn-ext-stop" onclick="controlExtraction('stop')" style="background:var(--brick);color:#fff;border:none;border-radius:6px;padding:6px 14px;cursor:pointer;display:none">⏹ {bi("Остановить", "Stop")}</button>
      </div>
    </div>
  </div>
</div>
<div class="section">
  <h2>{bi("Инжект wiki (последний запрос)", "Wiki inject (latest query)")}</h2>
  {hint("Что wiki 3 последняя вставила в память агента: запрос пользователя и текст, попавший в контекст. Обновляется без перезагрузки, DOM меняется только при новых данных.",
        "What wiki v3 last injected into the agent memory: the user query and the text placed into context. Live-updated; the DOM changes only when data actually changes.")}
  <details id="inject-acc">
    <summary style="cursor:pointer;color:var(--brass);user-select:none">▸ {bi("Смотреть, что wiki 3 инжектировала в память", "See what wiki v3 injected into memory")}</summary>
    <div style="margin-top:12px" id="inject-content">
      {inj_html}
    </div>
  </details>
</div>
<div class="section">
  <h2>{bi("Endpoint (экстрактор и эмбеддинги)", "Endpoint (extractor & embeddings)")}</h2>
  {hint("Адреса LLM-серверов, которые ядро wiki использует для экстракции фактов и эмбеддингов. Сохраняется в endpoints.yaml; смена backend эмбеддингов требует полной пере-индексации.",
        "LLM server addresses used by the wiki core for fact extraction and embeddings. Stored in endpoints.yaml; changing the embedding backend requires full re-indexing.")}
  <form onsubmit="saveConfig(event,'extract')">
    <h3 style="font-size:13px;color:var(--muted);margin-bottom:8px">{bi("Экстрактор (LLM)", "Extractor (LLM)")}</h3>
    <label>URL: <input type="text" id="cfg-ex-url" value="{ex.get('url','')}" size="70" style="background:var(--bg);color:var(--ink);border:1px solid var(--border);padding:4px 8px;border-radius:4px"></label><br style="margin-bottom:6px">
    <label>{bi("Модель", "Model")}: <input type="text" id="cfg-ex-model" value="{ex.get('model','')}" size="50" style="background:var(--bg);color:var(--ink);border:1px solid var(--border);padding:4px 8px;border-radius:4px"></label><br style="margin-bottom:6px">
    <label>{bi("API-ключ", "API key")}: <input type="password" id="cfg-ex-key" data-ph-ru="{'••••' if ex.get('key_set') else 'введите ключ'}" data-ph-en="{'••••' if ex.get('key_set') else 'enter key'}" placeholder="{'••••' if ex.get('key_set') else 'введите ключ'}" style="background:var(--bg);color:var(--ink);border:1px solid var(--border);padding:4px 8px;border-radius:4px"></label><br>
    <button type="submit" style="background:var(--sage);color:#fff;border:none;border-radius:6px;padding:6px 14px;cursor:pointer;margin-top:8px">{bi("Сохранить экстрактор", "Save extractor")}</button>
  </form>
  <hr style="border:none;border-top:1px solid var(--border);margin:16px 0">
  <form onsubmit="saveConfig(event,'embed')">
    <h3 style="font-size:13px;color:var(--muted);margin-bottom:8px">{bi("Эмбеддинги (indexer)", "Embeddings (indexer)")}</h3>
    <label>Backend: <select id="cfg-emb-backend" style="background:var(--bg);color:var(--ink);border:1px solid var(--border);padding:4px 8px;border-radius:4px">
      <option value="lmstudio" {'selected' if emb.get('backend')=='lmstudio' else ''}>LM Studio</option>
      <option value="nvidia" {'selected' if emb.get('backend')=='nvidia' else ''}>NVIDIA</option>
      <option value="llamaserver" {'selected' if emb.get('backend')=='llamaserver' else ''}>llama.cpp</option>
    </select></label><br style="margin-bottom:6px">
    <label>URL: <input type="text" id="cfg-emb-url" value="{emb.get('url','')}" size="70" style="background:var(--bg);color:var(--ink);border:1px solid var(--border);padding:4px 8px;border-radius:4px"></label><br style="margin-bottom:6px">
    <label>{bi("Модель", "Model")}: <input type="text" id="cfg-emb-model" value="{emb.get('model','')}" size="50" style="background:var(--bg);color:var(--ink);border:1px solid var(--border);padding:4px 8px;border-radius:4px"></label>
    <p style="color:var(--brass);font-size:12px;margin-top:6px">⚠️ {bi("Смена backend эмбеддингов требует ПОЛНОЙ пере-индексации базы.", "Changing the embedding backend requires a FULL re-index.")}</p>
    <button type="submit" style="background:var(--brass);color:#fff;border:none;border-radius:6px;padding:6px 14px;cursor:pointer;margin-top:8px">{bi("Сохранить эмбеддинги", "Save embeddings")}</button>
  </form>
</div>
</div>
<div id="page-memory" hidden>
{memory_page_html()}
</div>
<p class="footer">Wiki Memory v3 Dashboard · {bi("Пульт ночного архивариуса", "Night archivist console")}</p>
<script>
var dashboardData = {_safe_json({
    "health": health,
    "effectiveness": {
        "hit_rate": hr,
        "coverage": cov,
        "rating": rating,
    },
    "database": {
        "pages": s.get("pages", 0),
        "sessions": s.get("sessions", 0),
        "orphans": s.get("orphans", 0),
    },
    "api": {
        "calls": snap.get("embed_api_calls_total", 0),
        "errors": snap.get("embed_api_errors_total", 0),
        "cache_hit_rate": cache_hit_rate(snap),
    },
})};
</script>
<script>
{JS_AUTOREFRESH}
</script>
<script>
{JS_LANG}
</script>
<script>
{JS_POLL}
</script>
<script>
{JS_CONTROL}
</script>
<script>
{JS_MEMORY_SEARCH}
</script>
</body>
</html>"""
    return body_content
