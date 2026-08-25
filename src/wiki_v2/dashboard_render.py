"""Dashboard rendering helpers: dark-theme CSS, badges, time selector."""

CSS = """
:root {
  --bg: #0d1117;
  --card: #161b22;
  --border: #30363d;
  --text: #c9d1d9;
  --muted: #8b949e;
  --accent: #58a6ff;
  --green: #4caf50;
  --yellow: #ff9800;
  --red: #f44336;
}
body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 24px; }
.section { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-bottom: 16px; }
.section h2 { color: var(--accent); }
.badge { display:inline-block; padding: 2px 10px; border-radius: 12px; color: #fff; font-weight: 600; }
"""


def badge(api_state: str) -> str:
    """Render a colored badge for an API state.

    normal → green, degraded → yellow, offline → red, unknown → grey.
    """
    colors = {
        "normal": "#4caf50",
        "degraded": "#ff9800",
        "offline": "#f44336",
        "unknown": "#9e9e9e",
    }
    c = colors.get(api_state, "#9e9e9e")
    return f'<span class="badge" style="background:{c}">{api_state}</span>'


def time_selector_html(current: str = "1w") -> str:
    """Render a <select> for time-range selection (NO page reload).

    Options: 1w (Неделя/Week), 3d (3 дня/3 days), 1d (День/Day), 1h (Час/Hour).

    On change it fetches /api/charts?range=<value> and swaps the chart SVG
    blocks in place via data-chart containers — so the page does NOT reload
    and does NOT scroll/jump to the top. The choice is stored in localStorage
    (wiki_dash_range) and re-applied on load (charts are re-fetched for the
    restored range, so the select and the charts always agree).
    Option labels are bilingual (.bi spans toggled by body[data-lang]).
    """
    opts = [
        ("1w", "Неделя", "Week"),
        ("3d", "3 дня", "3 days"),
        ("1d", "День", "Day"),
        ("1h", "Час", "Hour"),
    ]
    parts = [
        '<select id="time-range" onchange="dashSetRange(this.value)">'
    ]
    for value, ru_label, en_label in opts:
        sel = " selected" if value == current else ""
        # <option> content is rendered by the OS and ignores CSS display:none,
        # so the language is switched by applyLang() rewriting textContent
        # from data-ru/data-en attributes (see JS_LANG).
        parts.append(
            f'<option value="{value}"{sel} data-ru="{ru_label}" data-en="{en_label}">{ru_label}</option>'
        )
    parts.append("</select>")
    parts.append(
        '<script>'
        "window.__dashRange='1w';"
        "function dashSetRange(r){"
        "window.__dashRange=r;"
        "localStorage.setItem('wiki_dash_range',r);"
        "var sel=document.getElementById('time-range');"
        "if(sel)sel.value=r;"
        "fetch('/api/charts?range='+encodeURIComponent(r),{cache:'no-store'})"
        ".then(function(res){if(!res.ok)throw 0;return res.json();})"
        ".then(function(d){"
        "if(!d)return;"
        "var map={'inject_relevance':'dash-inject-relevance','extraction':'dash-extraction','embed_combined':'dash-embed-combined','latency':'dash-latency'};"
        "for(var k in map){var el=document.getElementById(map[k]);"
        "if(el&&d[k]!=null)el.innerHTML=d[k];}"
        "})"
        ".catch(function(){});"
        "}"
        "(function(){"
        "var url=new URL(location.href);"
        "var init=url.searchParams.get('range')||localStorage.getItem('wiki_dash_range')||'1w';"
        "if(['1w','3d','1d','1h'].indexOf(init)>=0){var sel=document.getElementById('time-range');"
        "if(sel)sel.value=init;dashSetRange(init);}"
        "})();"
        "</script>"
    )
    return "\n".join(parts)


def range_to_bucket(range: str) -> str:
    """Map a time-range string to an aggregation bucket step.

    1w → day, 3d → hour, 1d → hour, 1h → minute.
    Falls back to "day" for unknown values.
    """
    return {
        "1w": "day",
        "3d": "hour",
        "1d": "hour",
        "1h": "minute",
    }.get(range, "day")


def range_seconds(range: str) -> int:
    """Duration in seconds for a time-range selector value."""
    return {
        "1w": 7 * 86400,
        "3d": 3 * 86400,
        "1d": 86400,
        "1h": 3600,
    }.get(range, 7 * 86400)


def _auto_y_max(values: list[float], pad_factor: float = 0.15) -> float:
    """Compute a sensible max for the Y axis given data values.

    Returns a small positive default for empty/all-zero data so a chart
    renders instead of being silently flattened to zero.
    """
    if not values:
        return 1.0
    top = max(values)
    if top <= 0:
        return 1.0
    return top + top * pad_factor
