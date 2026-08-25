"""Tests for wiki_v2.dashboard_charts and wiki_v2.dashboard_render — pure functions."""
import sys
from pathlib import Path

# scripts root (scripts/wiki_v2/tests/ → parent.parent = scripts/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wiki_v2 import dashboard_charts as charts
from wiki_v2 import dashboard_render as render

# ── svg_line ─────────────────────────────────────────────────────────────────

class TestSvgLine:
    """svg_line(data, x_key, y_key, w, h, y_min, y_max) → SVG polyline."""

    def test_non_empty_returns_polyline(self):
        data = [{"date": 0, "value": 0.2}, {"date": 1, "value": 0.7}]
        result = charts.svg_line(data)
        assert "<polyline" in result
        assert "Нет данных" not in result

    def test_empty_returns_no_data(self):
        result = charts.svg_line([])
        assert "Нет данных" in result

    def test_custom_keys(self):
        data = [{"x": 10, "y": 0.5}, {"x": 20, "y": 0.9}]
        result = charts.svg_line(data, x_key="x", y_key="y")
        assert "<polyline" in result


# ── svg_area ─────────────────────────────────────────────────────────────────

class TestSvgArea:
    """svg_area(data, x_key, y_key, w, h) → SVG polygon."""

    def test_non_empty_returns_polygon(self):
        data = [{"date": 0, "value": 0.3}, {"date": 1, "value": 0.8}]
        result = charts.svg_area(data)
        assert "<polygon" in result
        assert "Нет данных" not in result

    def test_empty_returns_no_data(self):
        result = charts.svg_area([])
        assert "Нет данных" in result


# ── svg_stacked ──────────────────────────────────────────────────────────────

class TestSvgStacked:
    """svg_stacked(data, series, w, h) → stacked SVG polygon."""

    def test_non_empty_returns_polygon(self):
        data = [
            {"date": 0, "a": 1, "b": 2},
            {"date": 1, "a": 3, "b": 1},
        ]
        result = charts.svg_stacked(data, series=["a", "b"])
        assert "<polygon" in result
        assert "Нет данных" not in result

    def test_empty_data_returns_no_data(self):
        result = charts.svg_stacked([], series=["a"])
        assert "Нет данных" in result

    def test_empty_series_returns_no_data(self):
        result = charts.svg_stacked([{"date": 0, "a": 1}], series=[])
        assert "Нет данных" in result


# ── svg_sparkline ────────────────────────────────────────────────────────────

class TestSvgSparkline:
    """svg_sparkline(values, w, h) → mini polyline SVG."""

    def test_non_empty_returns_polyline(self):
        result = charts.svg_sparkline([1, 2, 3])
        assert "<polyline" in result

    def test_empty_returns_empty_string(self):
        result = charts.svg_sparkline([])
        assert result == ""

    def test_single_value(self):
        result = charts.svg_sparkline([5])
        assert "<polyline" in result


# ── svg_donut ────────────────────────────────────────────────────────────────

class TestSvgDonut:
    """svg_donut(parts, w, h) → donut chart SVG."""

    def test_two_parts_returns_path(self):
        parts = [
            {"label": "A", "value": 3, "color": "#4a90d9"},
            {"label": "B", "value": 1, "color": "#e67e22"},
        ]
        result = charts.svg_donut(parts)
        assert "<path" in result
        assert "Нет данных" not in result

    def test_empty_parts_returns_no_data(self):
        result = charts.svg_donut([])
        assert "Нет данных" in result

    def test_zero_values_returns_no_data(self):
        parts = [{"label": "X", "value": 0, "color": "#000"}]
        result = charts.svg_donut(parts)
        assert "Нет данных" in result


# ── tag_cloud ────────────────────────────────────────────────────────────────

class TestTagCloud:
    """tag_cloud(tags, max_size, min_size) → HTML spans."""

    def test_non_empty_contains_font_size(self):
        tags = [("python", 10), ("rust", 5)]
        result = charts.tag_cloud(tags)
        assert "font-size" in result
        assert "Нет данных" not in result

    def test_empty_returns_empty_string(self):
        result = charts.tag_cloud([])
        assert result == ""


# ── progress_bar ─────────────────────────────────────────────────────────────

class TestProgressBar:
    """progress_bar(pct, label, w) → HTML progress bar."""

    def test_half_width(self):
        result = charts.progress_bar(0.5)
        assert "width:50%" in result

    def test_clamp_over_1(self):
        result = charts.progress_bar(1.5)
        assert "width:100%" in result

    def test_zero_width(self):
        result = charts.progress_bar(0)
        assert "width:0%" in result

    def test_negative_clamped(self):
        result = charts.progress_bar(-0.5)
        assert "width:0%" in result

    def test_with_label(self):
        result = charts.progress_bar(0.75, label="Done")
        assert "75%" in result
        assert "Done" in result


# ── svg_multi & chart_legend ─────────────────────────────────────────────────

class TestSvgMulti:
    """svg_multi(series, w, h, dots) → multi-series chart SVG."""

    def test_two_series_returns_polylines(self):
        series = [
            {"points": [{"ts": 10, "value": 1}, {"ts": 20, "value": 2}], "color": "#C9973B"},
            {"points": [{"ts": 10, "value": 3}, {"ts": 20, "value": 4}], "color": "#C25B43"},
        ]
        result = charts.svg_multi(series)
        assert result.count("<polyline") == 2
        assert "Нет данных" not in result

    def test_single_series_ok(self):
        series = [{"points": [{"ts": 10, "value": 1}], "color": "#C9973B"}]
        result = charts.svg_multi(series)
        assert "<svg" in result

    def test_empty_returns_no_data(self):
        result = charts.svg_multi([])
        assert "Нет данных" in result

    def test_dots_returns_circles(self):
        series = [{"points": [{"ts": 10, "value": 1}, {"ts": 20, "value": 2}], "color": "#C9973B"}]
        result = charts.svg_multi(series, dots=True)
        assert "<circle" in result
        assert "<polyline" not in result

    def test_all_zeros_no_crash(self):
        series = [{"points": [{"ts": 10, "value": 0}, {"ts": 20, "value": 0}], "color": "#C9973B"}]
        result = charts.svg_multi(series)
        assert "<svg" in result


class TestChartLegend:
    """chart_legend(items) → HTML legend."""

    def test_renders_labels_and_colors(self):
        items = [
            {"label": "Test 1", "color": "#C9973B"},
            {"label": "Test 2", "color": "#C25B43"},
        ]
        result = charts.chart_legend(items)
        assert "Test 1" in result
        assert "Test 2" in result
        assert "#C9973B" in result
        assert "chart-legend" in result

    def test_empty_returns_empty_string(self):
        result = charts.chart_legend([])
        assert result == ""


# ── CSS constant ─────────────────────────────────────────────────────────────

class TestCSS:
    """dashboard_render.CSS — dark-theme stylesheet constant."""

    def test_contains_css_variables(self):
        assert "--bg" in render.CSS

    def test_contains_section_class(self):
        assert ".section" in render.CSS


# ── badge ────────────────────────────────────────────────────────────────────

class TestBadge:
    """badge(api_state) → colored <span>."""

    def test_normal(self):
        result = render.badge("normal")
        assert "4caf50" in result

    def test_degraded(self):
        result = render.badge("degraded")
        assert "ff9800" in result

    def test_offline(self):
        result = render.badge("offline")
        assert "f44336" in result

    def test_unknown(self):
        result = render.badge("unknown")
        assert "9e9e9e" in result

    def test_unknown_state_fallback(self):
        result = render.badge("weird")
        assert "9e9e9e" in result

    def test_contains_span_class(self):
        result = render.badge("normal")
        assert '<span class="badge"' in result


# ── time_selector_html ───────────────────────────────────────────────────────

class TestTimeSelectorHtml:
    """time_selector_html(current) → <select> with 4 options."""

    def test_contains_russian_labels(self):
        result = render.time_selector_html()
        assert "Неделя" in result

    def test_contains_hour_label(self):
        result = render.time_selector_html()
        assert "Час" in result

    def test_default_selected(self):
        result = render.time_selector_html(current="1w")
        assert 'value="1w" selected' in result

    def test_custom_selected(self):
        result = render.time_selector_html(current="1h")
        assert 'value="1h" selected' in result
        assert 'value="1w" selected' not in result

    def test_contains_select_tag(self):
        result = render.time_selector_html()
        assert "<select" in result
        assert "</select>" in result


# ── range_to_bucket ────────────────────────────────────────────────          

class TestRangeToBucket:
    """range_to_bucket(range) → aggregation bucket string."""

    def test_one_week(self):
        assert render.range_to_bucket("1w") == "day"

    def test_one_hour(self):
        assert render.range_to_bucket("1h") == "minute"

    def test_three_days(self):
        assert render.range_to_bucket("3d") == "hour"

    def test_one_day(self):
        assert render.range_to_bucket("1d") == "hour"

    def test_unknown_fallback(self):
        assert render.range_to_bucket("xx") == "day"

    def test_empty_fallback(self):
        assert render.range_to_bucket("") == "day"
