"""wiki_v2.dashboard_charts — self-contained SVG chart functions (no CDN).

All functions are fail-open: on any error they return a "Нет данных"
placeholder SVG (sparkline returns empty string on empty input).
"""

from __future__ import annotations

from typing import Any

CHART_LINE = "#C9973B"
CHART_FILL = "rgba(201,151,59,0.15)"
CHART_GRID = "rgba(237,230,216,0.08)"
CHART_TEXT = "#9A9184"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_svg() -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40"><text x="50" y="25" text-anchor="middle" font-size="12" font-family="Cascadia Mono, Consolas, monospace" fill="{CHART_TEXT}">Нет данных</text></svg>'


def _auto_range(values: list[float]) -> tuple[float, float]:
    """Return (min, max) with a ±0.1 padding when all values are equal."""
    mn, mx = min(values), max(values)
    if mn == mx:
        pad = max(abs(mn) * 0.1, 0.1)
        return mn - pad, mx + pad
    pad = (mx - mn) * 0.05
    return mn - pad, mx + pad


def _map_point(
    x: float, y: float,
    x_min: float, x_max: float,
    y_min: float, y_max: float,
    w: int, h: int,
    pad: int = 40,
) -> tuple[float, float]:
    """Map data coordinates to SVG pixel space (with padding)."""
    x_range = x_max - x_min if x_max != x_min else 1.0
    y_range = y_max - y_min if y_max != y_min else 1.0
    px = pad + (x - x_min) / x_range * (w - 2 * pad)
    py = h - pad - (y - y_min) / y_range * (h - 2 * pad)
    return round(px, 2), round(py, 2)


def _grid_lines(y_min: float, y_max: float, w: int, h: int, pad: int = 40) -> str:
    """Return SVG <line> elements for a 4-line horizontal grid + Y labels."""
    parts: list[str] = []
    for i in range(5):
        frac = i / 4.0
        y_val = y_min + frac * (y_max - y_min)
        _, py = _map_point(0, y_val, 0, 1, y_min, y_max, w, h, pad)
        parts.append(f'<line x1="{pad}" y1="{py}" x2="{w - pad}" y2="{py}" stroke="{CHART_GRID}" stroke-width="1"/>')
        label = f"{y_val:.2f}"
        parts.append(f'<text x="{pad - 5}" y="{py + 4}" text-anchor="end" font-size="10" font-family="Cascadia Mono, Consolas, monospace" fill="{CHART_TEXT}">{label}</text>')
    return "\n".join(parts)


def _x_ticks(
    x_min: float, x_max: float,
    w: int, h: int, pad: int = 40,
    n_ticks: int = 6,
) -> str:
    """Return SVG <text> elements for evenly spaced X axis time labels.

    Ticks are spread uniformly over the [x_min, x_max] domain (NOT one per
    data point) so clustered events can no longer overlap the labels.
    """
    import datetime as _dt
    parts = ""
    if x_max <= x_min:
        x_max = x_min + 1
    fmt = "%H:%M" if (x_max - x_min) < 2 * 86400 else "%d.%m"
    n = max(n_ticks, 2)
    for i in range(n):
        frac = i / (n - 1)
        t = x_min + frac * (x_max - x_min)
        try:
            lab = _dt.datetime.fromtimestamp(t).strftime(fmt)
        except Exception:
            lab = ""
        px, _ = _map_point(t, 0, x_min, x_max, 0, 1, w, h, pad)
        parts += f'<text x="{px:.0f}" y="{h - 5}" text-anchor="middle" font-size="10" font-family="Cascadia Mono, Consolas, monospace" fill="{CHART_TEXT}">{lab}</text>'
    return parts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def svg_line(
    data: list[dict[str, Any]],
    x_key: str = "date",
    y_key: str = "value",
    w: int = 600,
    h: int = 200,
    y_min: float = 0,
    y_max: float = 1.0,
) -> str:
    """Line chart as SVG <polyline>.

    *data* is a list of dicts.  Empty data → "Нет данных".
    """
    try:
        if not data:
            return _empty_svg()

        x_vals: list[float] = []
        y_vals: list[float] = []
        for row in data:
            x = row.get(x_key, 0)
            y = float(row.get(y_key, 0))
            x_vals.append(float(x) if isinstance(x, (int, float)) else 0)
            y_vals.append(y)

        px_points = [_map_point(x, y, min(x_vals), max(x_vals), y_min, y_max, w, h) for x, y in zip(x_vals, y_vals)]
        polyline = " ".join(f"{px},{py}" for px, py in px_points)

        if len(px_points) == 1:
            polyline = f"{px_points[0][0]},{px_points[0][1]} {px_points[0][0] + 1},{px_points[0][1]}"

        grid = _grid_lines(y_min, y_max, w, h)

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            f"{grid}\n"
            f'<polyline points="{polyline}" fill="none" stroke="{CHART_LINE}" stroke-width="2" stroke-linejoin="round"/>\n'
            f"</svg>"
        )
    except Exception:
        return _empty_svg()


def svg_timeseries(
    data: list[dict[str, Any]],
    y_key: str = "value",
    w: int = 600,
    h: int = 200,
    unit: str = "",
    label: str = "",
    x_min: float | None = None,
    x_max: float | None = None,
) -> str:
    """Line chart from time-series points [{ts, value}].

    Y axis is auto-scaled to the data (never flattened to zero). The X axis
    shows human readable HH:MM / DD.MM labels spread evenly over the domain.
    When x_min/x_max are given, the X axis covers that window even if the
    data is clustered (so a global range selector visibly zooms the chart).
    Empty data → "Нет данных".
    """
    try:
        if not data:
            return _empty_svg()

        pts: list[tuple[float, float]] = []
        for row in data:
            ts = row.get("ts")
            val = float(row.get(y_key, 0))
            if isinstance(ts, (int, float)):
                pts.append((float(ts), val))
        if not pts:
            return _empty_svg()

        d_min = min(p[0] for p in pts)
        d_max = max(p[0] for p in pts)
        if d_max == d_min:
            d_max = d_min + 1
        x_lo = d_min if x_min is None else min(d_min, float(x_min))
        x_hi = d_max if x_max is None else max(d_max, float(x_max))
        if x_hi <= x_lo:
            x_hi = x_lo + 1
        y_max = _auto_range([p[1] for p in pts])[1]

        px_points = [_map_point(x, y, x_lo, x_hi, 0, y_max, w, h) for x, y in pts]
        if len(px_points) == 1:
            px_points = [px_points[0], (px_points[0][0] + 1, px_points[0][1])]
        polyline = " ".join(f"{px},{py}" for px, py in px_points)

        grid = _grid_lines(0, y_max, w, h)

        x_ticks = _x_ticks(x_lo, x_hi, w, h)

        title = (f"<title>{label}</title>" if label else "")
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">{title}\n'
            f"{grid}\n"
            f'<polyline points="{polyline}" fill="none" stroke="{CHART_LINE}" stroke-width="2" stroke-linejoin="round"/>\n'
            f"{x_ticks}\n"
            f"</svg>"
        )
    except Exception:
        return _empty_svg()


def svg_multi(
    series: list[dict[str, Any]],
    w: int = 600,
    h: int = 200,
    dots: bool = False,
    x_min: float | None = None,
    x_max: float | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
) -> str:
    """Multi-series time chart. series=[{"points":[{ts,value}], "color": "#hex"}].

    Y axis auto-scaled from 0 across ALL series. dots=True → circles r=2.5
    instead of polylines (per-event scatter). When x_min/x_max are given the
    X axis covers that window (global range selector zooms the chart even
    when events are clustered); y_min/y_max pin the Y scale (e.g. 0..1 for
    relevance scores) — the domain still expands if data exceeds it.
    Empty/all-empty → "Нет данных".
    """
    try:
        if not series:
            return _empty_svg()
        all_pts: list[tuple[float, float]] = []
        for s in series:
            pts = s.get("points", [])
            for pt in pts:
                ts = pt.get("ts") if isinstance(pt, dict) else (pt[0] if len(pt) > 0 else None)
                val = float(pt.get("value", 0) if isinstance(pt, dict) else (pt[1] if len(pt) > 1 else 0))
                if isinstance(ts, (int, float)):
                    all_pts.append((float(ts), val))
        if not all_pts:
            return _empty_svg()

        d_x_min = min(p[0] for p in all_pts)
        d_x_max = max(p[0] for p in all_pts)
        if d_x_max == d_x_min:
            d_x_max = d_x_min + 1
        x_lo = d_x_min if x_min is None else min(d_x_min, float(x_min))
        x_hi = d_x_max if x_max is None else max(d_x_max, float(x_max))
        if x_hi <= x_lo:
            x_hi = x_lo + 1
        y_vals = [p[1] for p in all_pts]
        y_hi = _auto_range(y_vals)[1] if y_max is None else max(float(y_max), max(y_vals))
        y_lo = 0.0 if y_min is None else min(float(y_min), min(y_vals))

        grid = _grid_lines(y_lo, y_hi, w, h)

        elements: list[str] = []
        for s in series:
            color = s.get("color", CHART_LINE)
            pts = s.get("points", [])
            series_pts: list[tuple[float, float]] = []
            for pt in pts:
                ts = pt.get("ts") if isinstance(pt, dict) else (pt[0] if len(pt) > 0 else None)
                val = float(pt.get("value", 0) if isinstance(pt, dict) else (pt[1] if len(pt) > 1 else 0))
                if isinstance(ts, (int, float)):
                    series_pts.append((float(ts), val))
            if not series_pts:
                continue

            if dots:
                if len(series_pts) == 1:
                    t, v = series_pts[0]
                    px, py = _map_point(t, v, x_lo, x_hi, y_lo, y_hi, w, h)
                    elements.append(f'<circle cx="{px}" cy="{py}" r="3" fill="{color}"/>')
                else:
                    for t, v in series_pts:
                        px, py = _map_point(t, v, x_lo, x_hi, y_lo, y_hi, w, h)
                        elements.append(f'<circle cx="{px}" cy="{py}" r="2.5" fill="{color}" stroke="none"/>')
            else:
                px_points = [_map_point(t, v, x_lo, x_hi, y_lo, y_hi, w, h) for t, v in series_pts]
                if len(px_points) == 1:
                    px_points = [px_points[0], (px_points[0][0] + 1, px_points[0][1])]
                polyline_str = " ".join(f"{px},{py}" for px, py in px_points)
                elements.append(f'<polyline points="{polyline_str}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>')

        x_ticks = _x_ticks(x_lo, x_hi, w, h)

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            f"{grid}\n"
            + "\n".join(elements) + "\n"
            f"{x_ticks}\n"
            f"</svg>"
        )
    except Exception:
        return _empty_svg()


def chart_legend(items: list[dict[str, str]]) -> str:
    """HTML legend: items=[{"label": ..., "color": "#hex"}]."""
    try:
        if not items:
            return ""
        import html as html_mod
        parts = ['<div class="chart-legend">']
        for item in items:
            lbl = item.get("label", "")
            if not ("<span" in lbl or "<" in lbl):
                lbl = html_mod.escape(str(lbl))
            color = item.get("color", CHART_LINE)
            parts.append(f'<span class="chart-legend-item"><span class="chart-swatch" style="background:{color}"></span>{lbl}</span>')
        parts.append('</div>')
        return "".join(parts)
    except Exception:
        return ""


def svg_area(
    data: list[dict[str, Any]],
    x_key: str = "date",
    y_key: str = "value",
    w: int = 600,
    h: int = 200,
) -> str:
    """Area chart as SVG <polygon> with semi-transparent fill.

    Empty data → "Нет данных".
    """
    try:
        if not data:
            return _empty_svg()

        x_vals: list[float] = []
        y_vals: list[float] = []
        for row in data:
            x = row.get(x_key, 0)
            y = float(row.get(y_key, 0))
            x_vals.append(float(x) if isinstance(x, (int, float)) else 0)
            y_vals.append(y)

        x_min, x_max = min(x_vals), max(x_vals)
        y_min, y_max = _auto_range(y_vals)

        top_pts = [_map_point(x, y, x_min, x_max, y_min, y_max, w, h) for x, y in zip(x_vals, y_vals)]
        bottom_pts = [(px, h - 20) for px, _ in reversed(top_pts)]

        all_pts = top_pts + bottom_pts
        points_str = " ".join(f"{px},{py}" for px, py in all_pts)

        grid = _grid_lines(y_min, y_max, w, h)

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            f"{grid}\n"
            f'<polygon points="{points_str}" fill="{CHART_FILL}" stroke="{CHART_LINE}" stroke-width="2" stroke-linejoin="round"/>\n'
            f"</svg>"
        )
    except Exception:
        return _empty_svg()


def svg_stacked(
    data: list[dict[str, Any]],
    series: list[str],
    w: int = 600,
    h: int = 200,
) -> str:
    """Stacked area chart.

    *series* is a list of dict keys to stack on top of each other.
    Empty data → "Нет данных".
    """
    try:
        if not data or not series:
            return _empty_svg()

        stacks: list[list[float]] = [list[float]() for _ in series]
        x_vals: list[float] = []

        for row in data:
            x = row.get("date", 0)
            x_vals.append(float(x) if isinstance(x, (int, float)) else 0)
            cumulative = 0.0
            for i, key in enumerate(series):
                val = float(row.get(key, 0))
                stacks[i].append(cumulative + val)
                cumulative += val

        all_y: list[float] = []
        for s in stacks:
            all_y.extend(s)
        if not all_y:
            return _empty_svg()

        x_min, x_max = min(x_vals), max(x_vals)
        y_min, y_max = _auto_range(all_y)

        colors = ["#C9973B", "#79A05E", "#C25B43", "#4a90d9", "#8e44ad", "#1abc9c"]

        parts: list[str] = []
        for i in range(len(series) - 1, -1, -1):
            top_pts = [_map_point(x, stacks[i][j], x_min, x_max, y_min, y_max, w, h) for j, x in enumerate(x_vals)]
            if i == 0:
                bottom_pts = [(px, h - 20) for px, _ in reversed(top_pts)]
                all_pts = top_pts + bottom_pts
            else:
                bottom_pts = [_map_point(x, stacks[i - 1][j], x_min, x_max, y_min, y_max, w, h) for j, x in enumerate(x_vals)]
                bottom_pts = [(px, py) for px, py in reversed(bottom_pts)]
                all_pts = top_pts + bottom_pts

            points_str = " ".join(f"{px},{py}" for px, py in all_pts)
            color = colors[i % len(colors)]
            parts.append(f'<polygon points="{points_str}" fill="{color}" fill-opacity="0.6" stroke="{color}" stroke-width="1" stroke-linejoin="round"/>')

        grid = _grid_lines(y_min, y_max, w, h)

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            f"{grid}\n"
            + "\n".join(parts) + "\n"
            "</svg>"
        )
    except Exception:
        return _empty_svg()


def svg_sparkline(
    values: list[float],
    w: int = 80,
    h: int = 24,
) -> str:
    """Tiny polyline for corner indicators.

    Empty values → empty string.
    """
    try:
        if not values:
            return ""

        y_min, y_max = _auto_range(values)
        n = len(values)

        points = [_map_point(i, values[i], 0, n - 1 if n > 1 else 1, y_min, y_max, w, h, pad=2) for i in range(n)]
        points_str = " ".join(f"{px},{py}" for px, py in points)

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
            f'<polyline points="{points_str}" fill="none" stroke="{CHART_LINE}" stroke-width="1.5" stroke-linejoin="round"/>'
            f"</svg>"
        )
    except Exception:
        return ""


def svg_donut(
    parts: list[dict],
    w: int = 200,
    h: int = 200,
) -> str:
    """Donut/pie диаграмма. parts=[{label, value, color}]. Пусто → 'Нет данных'."""
    try:
        if not parts or all(p.get("value", 0) == 0 for p in parts):
            return _empty_svg()

        total = sum(p.get("value", 0) for p in parts)
        if total == 0:
            return _empty_svg()

        cx, cy = w / 2, h / 2
        outer_r = min(w, h) / 2 - 10
        inner_r = outer_r * 0.55
        start_angle = 0.0

        arcs: list[str] = []
        for p in parts:
            val = p.get("value", 0)
            if val <= 0:
                continue
            sweep = (val / total) * 360.0
            end_angle = start_angle + sweep

            ox1 = cx + outer_r * __import__("math").cos(__import__("math").radians(start_angle))
            oy1 = cy + outer_r * __import__("math").sin(__import__("math").radians(start_angle))
            ox2 = cx + outer_r * __import__("math").cos(__import__("math").radians(end_angle))
            oy2 = cy + outer_r * __import__("math").sin(__import__("math").radians(end_angle))
            large = 1 if sweep > 180 else 0

            ix2 = cx + inner_r * __import__("math").cos(__import__("math").radians(end_angle))
            iy2 = cy + inner_r * __import__("math").sin(__import__("math").radians(end_angle))
            ix1 = cx + inner_r * __import__("math").cos(__import__("math").radians(start_angle))
            iy1 = cy + inner_r * __import__("math").sin(__import__("math").radians(start_angle))

            d = (
                f"M {ox1:.1f} {oy1:.1f}"
                f" A {outer_r:.1f} {outer_r:.1f} 0 {large} 1 {ox2:.1f} {oy2:.1f}"
                f" L {ix2:.1f} {iy2:.1f}"
                f" A {inner_r:.1f} {inner_r:.1f} 0 {large} 0 {ix1:.1f} {iy1:.1f}"
                f" Z"
            )
            color = p.get("color", CHART_LINE)
            arcs.append(f'<path d="{d}" fill="{color}"/>')
            start_angle = end_angle

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            + "\n".join(arcs) + "\n"
            "</svg>"
        )
    except Exception:
        return _empty_svg()


def tag_cloud(
    tags: list[tuple[str, int]],
    max_size: int = 28,
    min_size: int = 10,
) -> str:
    """Тег-облако: HTML тегов, размер/цвет = частота. tags=[(тег, частота)]."""
    try:
        if not tags:
            return ""

        sorted_tags = sorted(tags, key=lambda t: t[1], reverse=True)
        max_freq = max(t[1] for t in sorted_tags)

        spans: list[str] = []
        for label, freq in sorted_tags:
            if max_freq == 0:
                size = min_size
            else:
                size = min_size + int((freq / max_freq) * (max_size - min_size))
            spans.append(f'<span style="font-size:{size}px;color:var(--ink);opacity:{0.5 + 0.5 * (freq / max_freq if max_freq else 0):.2f};margin:0 4px;">{label}</span>')

        return " ".join(spans)
    except Exception:
        return ""


def progress_bar(
    pct: float,
    label: str = "",
    w: int = 300,
) -> str:
    """Прогресс-бар (CSS width). pct 0..1 (clamp). Пусто/0 → ширина 0."""
    try:
        pct = max(0.0, min(1.0, float(pct)))
        width_pct = f"{pct * 100:.0f}%"
        inner = f"{label} {pct * 100:.0f}%" if label else ""
        return (
            f'<div style="width:{w}px;background:#2A241C;border-radius:4px;overflow:hidden;font-family:Cascadia Mono,Consolas,monospace;font-size:11px;color:var(--ink);text-align:center;">'
            f'<div style="width:{width_pct};height:20px;background:{CHART_LINE};border-radius:4px;line-height:20px;">'
            f"{inner}"
            f"</div></div>"
        )
    except Exception:
        return ""
