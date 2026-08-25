"""Wiki Memory v3 — dashboard section renderers.

Every displayed parameter carries a small bilingual caption (RU/EN)
rendered by hint(); the language switch lives in the sticky header
(see JS_LANG in dashboard_js.py).
"""
from __future__ import annotations

import datetime
import html as html_mod
import json

from . import config
from .events import _events_path
from .logging_setup import logger


def _safe_json(obj) -> str:
    """Serialize *obj* to a JSON string safe for embedding in <script>."""
    return json.dumps(obj, ensure_ascii=False, default=str)


def hint(ru: str, en: str) -> str:
    """Small bilingual caption shown under a parameter label."""
    return (
        '<div class="hint"><span class="ru">'
        + html_mod.escape(ru)
        + '</span><span class="en">'
        + html_mod.escape(en)
        + "</span></div>"
    )


def bi(ru: str, en: str) -> str:
    """Bilingual inline spans for headings (toggled by body[data-lang]).

    Wrapped in .bi so the CSS rules `.bi > span.ru/.en` (dashboard_styles)
    actually match — without the wrapper both languages render at once.
    """
    return (
        '<span class="bi"><span class="ru">'
        + html_mod.escape(ru)
        + '</span><span class="en">'
        + html_mod.escape(en)
        + "</span></span>"
    )


def _parse_events() -> list[dict]:
    """Read all events from the JSONL file (fail-open → [])."""
    path = _events_path()
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "search_event":
                        events.append(obj)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return []
    return events


def _last_inject() -> dict:
    """Return the most recent wiki inject from wiki_injects.jsonl (fail-open)."""
    try:
        path = config.WIKI_PATH / "wiki_injects.jsonl"
        if not path.exists():
            return {}
        last = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
        return last or {}
    except Exception as exc:
        logger.debug("_last_inject failed: %s", exc)
        return {}


def _count_pending_facts() -> int:
    """Return number of lines in .facts_pending.jsonl (fail-open -> 0)."""
    try:
        path = config.WIKI_PATH / ".facts_pending.jsonl"
        if not path.exists():
            return 0
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _section_night_strip(health: dict) -> str:
    """Night Strip (Лента ночи) — 24h timeline of events with cluster handling."""
    try:
        from .dashboard_health import night_strip_events
        events = night_strip_events()
    except Exception:
        events = []

    pct_buckets: dict[float, list[dict]] = {}
    for ev in events:
        p = round(ev.get("pct", 0.0), 1)
        pct_buckets.setdefault(p, []).append(ev)

    ticks_html = ""
    color_map = {
        "error": "#C25B43",
        "warn": "#C9973B",
        "index": "#79A05E",
        "watchdog": "#9A9184",
    }

    for p, group in pct_buckets.items():
        count = len(group)
        for idx, ev in enumerate(group):
            t_type = ev.get("type", "warn")
            color = color_map.get(t_type, "#C9973B")
            text = html_mod.escape(ev.get("text", ""))
            ts_dt = datetime.datetime.fromtimestamp(
                ev.get("ts", datetime.datetime.now().timestamp())
            ).strftime("%H:%M")
            tooltip = html_mod.escape(f"[{ts_dt}] {text}")

            offset_x = (idx - (count - 1) / 2.0) * 3 if count > 1 else 0
            opacity = 0.85 if count > 1 else 1.0

            ticks_html += f"""<div class="night-tick" style="left:calc({p}% + {offset_x}px);background:{color};opacity:{opacity}" title="{tooltip}"></div>\n"""

    now = datetime.datetime.now()
    marks = [now - datetime.timedelta(hours=h) for h in (24, 18, 12, 6, 0)]
    labels = "".join(f"<span>{m.strftime('%H:%M')}</span>" for m in marks)

    return f"""<div class="night-strip-container">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
  <span class="eyebrow">{bi("Лента ночи (последние 24 часа)", "Night strip (last 24 hours)")}{hint(
      "Каждая метка — событие за сутки: ошибка (красная), предупреждение (жёлтая), завершённая индексация (зелёная), сторож (серая). Наведи курсор для деталей.",
      "Each mark is a 24h event: error (red), warning (yellow), finished indexing (green), watchdog (grey). Hover for details.")}</span>
  <span style="font-family:Cascadia Mono,Consolas,monospace;font-size:11px;color:var(--muted)">{len(events)} {bi("событий", "events")}</span>
</div>
<div class="night-strip">
{ticks_html}
</div>
<div style="display:flex;justify-content:space-between;font-family:Cascadia Mono,Consolas,monospace;font-size:11px;color:var(--muted)">
{labels}
</div>
</div>"""


def _section_components(health: dict) -> str:
    """Components health section."""
    comps = health.get("components", {})
    color_map = {
        "ok": "#79A05E",
        "warn": "#C9973B",
        "error": "#C25B43",
        "unknown": "#9A9184",
    }
    comp_hints = {
        "search_api": (
            "Поиск читает индекс в процессе Hermes; дашборд проверяет сам индекс (векторы на месте).",
            "Search reads the index inside Hermes; the dashboard verifies the index itself (vectors present).",
        ),
        "embeddings": (
            "Сервер эмбеддингов: жив ли TCP-порт и сколько ошибок вызовов было за 24ч.",
            "Embedding server: is the TCP port alive and how many call errors occurred in 24h.",
        ),
        "indexer": (
            "Индексация: идёт ли сейчас (lock-файл), когда была последняя и сколько сессий ждёт в очереди.",
            "Indexer: is it running now (lock file), when it last ran, how many sessions are queued.",
        ),
        "extractor": (
            "Экстракция фактов LLM: идёт ли процесс, прогресс и есть ли последняя ошибка.",
            "LLM fact extraction: running or not, progress, last error if any.",
        ),
        "watchdog": (
            "Сторож llama-server следит за авто-загрузкой/выгрузкой сервера эмбеддингов.",
            "llama-server watchdog manages auto start/stop of the embedding server.",
        ),
    }

    cards = ""
    for name, label in [
        ("search_api", "Search API"),
        ("embeddings", "Embeddings"),
        ("indexer", "Indexer"),
        ("extractor", "Extractor"),
        ("watchdog", "Watchdog"),
    ]:
        comp = comps.get(name, {})
        st = comp.get("status", "unknown")
        dot_color = color_map.get(st, "#9A9184")
        detail = html_mod.escape(comp.get("detail", "—"))
        ru, en = comp_hints.get(name, ("", ""))
        cards += f"""
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;flex:1;min-width:180px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            <span style="width:10px;height:10px;border-radius:50%;background:{dot_color};display:inline-block"></span>
            <strong style="color:var(--ink);font-size:0.95em">{label}</strong>
            <span style="margin-left:auto;font-family:Cascadia Mono,Consolas,monospace;font-size:0.75em;color:var(--muted)">{st}</span>
          </div>
          <div style="color:var(--muted);font-size:0.85em;font-family:Cascadia Mono,Consolas,monospace">{detail}</div>
          {hint(ru, en)}
        </div>
        """

    return f"""<div class="section">
<h2>{bi("Компоненты", "Components")}</h2>
<div style="display:flex;flex-wrap:wrap;gap:12px">
{cards}
</div>
</div>"""


def _section_errors(health: dict) -> str:
    """Errors (24h) section. Counters are real 24h windows (ts-filtered)."""
    errs = health.get("errors_24h", {})
    chat_err = errs.get("chat_api_errors_24h", 0)
    search_fb = errs.get("search_fallback_total", 0) or errs.get("search_fallback_24h", 0)
    embed_err = errs.get("embed_api_errors_24h", 0)
    log_tail = errs.get("log_tail", [])

    tail_html = ""
    if log_tail:
        escaped_lines = "\n".join(html_mod.escape(line) for line in log_tail)
        tail_html = f"""<h3 style="margin-top:12px;font-size:13px;color:var(--muted)">{bi("Последние ошибки лога", "Recent log errors")}</h3>
<pre style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px;font-family:Cascadia Mono,Consolas,monospace;font-size:0.8em;max-height:220px;overflow:auto;color:var(--brick)">{escaped_lines}</pre>"""
    else:
        tail_html = f'<p style="color:var(--muted);margin-top:8px">{bi("Ошибок за сутки нет.", "No errors in the last 24h.")}</p>'

    def _row(label, ru, en, value, dash_key):
        return (
            "<tr><td>"
            + label
            + hint(ru, en)
            + f'</td><td data-dash="{dash_key}">{value}</td></tr>'
        )

    rows = (
        _row(bi("Ошибки чат API", "Chat API errors"),
             "Вызовы LLM (экстракция/поиск), упавшие с ошибкой за последние 24 часа.",
             "LLM calls (extraction/search) that failed with an error in the last 24 hours.",
             chat_err, "chat_api_errors")
        + _row(bi("Фолбэки поиска", "Search fallbacks"),
             "Поиск не смог выполнить полную многовекторную схему и ушёл по упрощённому пути (за 24ч).",
             "Search could not run the full multi-vector pipeline and fell back (last 24h).",
             search_fb, "search_fallback")
        + _row(bi("Ошибки эмбеддингов", "Embedding errors"),
             "Запросы к серверу эмбеддингов, закончившиеся ошибкой, за 24 часа.",
             "Embedding-server requests that ended with an error, last 24 hours.",
             embed_err, "embed_errors_24h")
    )

    return f"""<div class="section">
<h2>{bi("Ошибки (24ч)", "Errors (24h)")}</h2>
<table class="metrics">
{rows}
</table>
{tail_html}
</div>"""


def _section_effectiveness(hr: float, cov: float, rating: str) -> str:
    """Effectiveness section."""

    def _row(label, ru, en, value, dash_key=None):
        key_attr = f' data-dash="{dash_key}"' if dash_key else ""
        return f"<tr><td>{label}{hint(ru, en)}</td><td{key_attr}>{value}</td></tr>"

    rows = (
        _row("Hit Rate",
             "Доля поисковых запросов за всё время, которые нашли хотя бы один хит (>0). Низкий → поиск бесполезен.",
             "Share of all search queries that returned at least one hit. Low value means search is useless.",
             f"{hr:.1%}", "hit_rate")
        + _row("Coverage",
              "Доля индексированных сессий, у которых заполнен content_hash (прошли полный цикл индексации).",
              "Share of indexed sessions that have content_hash filled (completed full indexing).",
              f"{cov:.1%}", "coverage")
        + _row(bi("Оценка", "Rating"),
              "Сводная оценка: 60% Hit Rate + 40% Coverage → Отлично/Хорошо/Средне/Низкая.",
              "Combined score: 60% hit rate + 40% coverage → Excellent/Good/Medium/Low.",
              rating)
    )
    return f"""<div class="section">
<h2>{bi("Эффективность", "Effectiveness")}</h2>
<table class="metrics">
{rows}
</table>
</div>"""


def _section_database(s: dict) -> str:
    """Database section."""
    pages = s.get("pages", 0)
    sessions = s.get("sessions", 0)
    orphans = s.get("orphans", 0)
    pending_facts = _count_pending_facts()

    new_sessions = 0
    try:
        from .dashboard_data import _count_new_sessions_7d
        new_sessions = _count_new_sessions_7d()
    except Exception:
        new_sessions = 0

    db_size_mb = s.get("db_size_mb", 0)
    disk_free_gb = s.get("disk_free_gb", 0)

    def _row(label, ru, en, value, dash_key=None):
        key_attr = f' data-dash="{dash_key}"' if dash_key else ""
        return f"<tr><td>{label}{hint(ru, en)}</td><td{key_attr}>{value}</td></tr>"

    rows = (
        _row(bi("Страницы", "Pages"),
             "Вики-страницы в индексе (одна сессия может дать страницу; slug = адрес страницы).",
             "Wiki pages stored in the index (one session may produce one page; slug = page address).",
             pages, "pages")
        + _row(bi("Сессии", "Sessions"),
             "Обработанные диалоги (сессии Hermes), отмеченные в таблице sessions.",
             "Processed conversations (Hermes sessions) recorded in the sessions table.",
             sessions, "sessions")
        + _row(bi("Факты в очереди", "Pending facts"),
             "Строки в .facts_pending.jsonl — факты, ожидающие экстракции/обработки.",
             "Lines in .facts_pending.jsonl — facts waiting for extraction/processing.",
             pending_facts, "facts_pending")
        + _row(bi("Чанки", "Chunks"),
             "Кусочки страниц (chunk:N / page_chunk:N), нарезанные split_text для поиска.",
             "Page pieces (chunk:N / page_chunk:N) produced by split_text for retrieval.",
             s.get("chunks", 0), "chunks")
        + _row(bi("Векторы", "Vectors"),
             "Все эмбеддинги в БД: страницы + чанки + заголовки/теги (таблица embeddings).",
             "All embeddings in DB: pages + chunks + titles/tags (embeddings table).",
             s.get("vectors", 0), "vectors")
        + _row(bi("Новые за неделю", "New this week"),
             "Сессии, проиндексированные за последние 7 дней (по sessions.indexed_at).",
             "Sessions indexed during the last 7 days (by sessions.indexed_at).",
             new_sessions)
        + _row("Orphans",
             "Страницы без вектора в embeddings — они выпадут из семантического поиска.",
             "Pages without a vector in embeddings — invisible to semantic search.",
             orphans, "orphans")
        + _row(bi("Размер БД", "DB size"),
             "Физический размер файла индекса .index_v2.db на диске.",
             "Physical size of the .index_v2.db index file on disk.",
             f"{db_size_mb:.2f} МБ", "db_size_mb")
        + _row(bi("Диск свободно", "Disk free"),
             f"{disk_free_gb:.1f} ГБ свободно на диске с БД; ⚠️ если меньше 10% от объёма.",
             f"{disk_free_gb:.1f} GB free on the DB disk; ⚠️ when below 10% of capacity.",
             bi("⚠️ Внимание", "⚠️ Warning") if s.get("disk_warning") else "OK")
    )

    return f"""<div class="section">
<h2>{bi("База", "Database")}</h2>
<table class="metrics">
{rows}
</table>
</div>"""


def _section_api(snap: dict) -> str:
    """API section. Cache counters come from the wiki query cache
    (cache_hits_total/cache_misses_total written by search.py)."""
    calls = snap.get("embed_api_calls_total", 0)
    errors = snap.get("embed_api_errors_total", 0)
    cache_hits = int(snap.get("cache_hits_total", 0))
    cache_misses = int(snap.get("cache_misses_total", 0))

    from .dashboard_data import cache_hit_rate
    rate = cache_hit_rate(snap)

    def _row(label, ru, en, value, dash_key):
        return (
            "<tr><td>"
            + label
            + hint(ru, en)
            + f'</td><td data-dash="{dash_key}">{value}</td></tr>'
        )

    rows = (
        _row(bi("Вызовы эмбеддингов", "Embedding calls"),
             "Сколько всего запросов на эмбеддинги отправлено за всю историю (сумма inc из wiki_metrics.jsonl).",
             "Total embedding requests sent over all history (sum of inc lines in wiki_metrics.jsonl).",
             int(calls), "embed_calls")
        + _row(bi("Ошибки (всего)", "Errors (total)"),
             "Ошибки эмбеддингов за всё время; для окна 24ч смотри секцию «Ошибки».",
             "Embedding errors over all time; see the Errors section for the 24h window.",
             int(errors), "embed_errors")
        + _row(bi("Cache Hit Rate (кэш запросов)", "Cache Hit Rate (query cache)"),
             f"Доля повторных запросов, отданных из wiki-context кэша ({cache_hits} из {cache_hits + cache_misses}).",
             f"Share of repeated queries served by the wiki-context cache ({cache_hits} of {cache_hits + cache_misses}).",
             f"{rate:.1%}", "cache_hit_rate")
    )

    return f"""<div class="section">
<h2>API</h2>
<table class="metrics">
{rows}
</table>
</div>"""


def _section_problems(p: dict) -> str:
    """Проблемные зоны — главное для оператора."""
    zone_hints = {
        "not_indexed": (
            "Сессии, ещё не попавшие в индекс — будут обработаны следующим проходом cron-индексации.",
            "Sessions not yet indexed — they will be handled by the next cron indexing pass.",
        ),
        "not_extracted": (
            "Страницы с quality='fallback': экстракция провалилась, остался только сырой текст.",
            "Pages with quality='fallback': extraction failed, only raw text remains.",
        ),
        "oversized": (
            "Сессии, отложенные из-за превышения длины и ещё не обработанные индексером (обработанные записи исчезают сами).",
            "Sessions deferred due to length limits and not yet handled by the indexer (handled entries drop off automatically).",
        ),
        "junk_chunks": (
            "Страницы, чей полный текст распознан как мусор (навигация, логи) — не полезны для памяти.",
            "Pages whose full text was classified as junk (navigation, logs) — not useful memory.",
        ),
    }
    rows = ""
    for key in ("not_indexed", "not_extracted", "oversized", "junk_chunks"):
        zone = p.get(key, {})
        label = zone.get("label", key)
        label_en = zone.get("label_en", "")
        working = zone.get("working", False)
        count = zone.get("count")
        ru, en = zone_hints.get(key, ("", ""))
        label_html = bi(label, label_en) if label_en else html_mod.escape(label)
        if working and count is not None:
            badge_color = "var(--brick)" if count > 0 else "var(--sage)"
            rows += (
                f'<tr><td>{label_html}{hint(ru, en)}</td>'
                f'<td><span class="badge" style="background:{badge_color}">{count}</span></td></tr>'
            )
        else:
            rows += f"<tr><td>{label_html}{hint(ru, en)}</td><td style=\"color:var(--muted)\">—</td></tr>"
    return f"""<div class="section">
<h2>{bi("Проблемные зоны", "Problem zones")}</h2>
<table class="metrics">
{rows}
</table>
</div>"""
