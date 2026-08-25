"""Wiki Memory v3 — dashboard CSS styles."""
from __future__ import annotations

CSS = """
:root {
  --bg: #17140F;
  --card: #221D15;
  --ink: #EDE6D8;
  --muted: #9A9184;
  --brass: #C9973B;
  --sage: #79A05E;
  --brick: #C25B43;
  --border: #30363d;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
  background: var(--bg);
  color: var(--ink);
  padding: 24px;
  line-height: 1.5;
  font-size: 13px;
}
h1, h2, h3, h4 {
  font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
  font-weight: 600;
  color: var(--ink);
}
h1 { font-size: 20px; }
h2 { font-size: 15px; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }
.eyebrow {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin-bottom: 4px;
}
.sticky-header {
  position: sticky;
  top: 0;
  background: var(--bg);
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  z-index: 100;
}
.night-strip-container {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 20px;
}
.night-strip {
  position: relative;
  height: 24px;
  background: #17140F;
  border-radius: 4px;
  margin: 8px 0;
  border: 1px solid var(--border);
}
.night-tick {
  position: absolute;
  top: 4px;
  width: 3px;
  height: 16px;
  border-radius: 1px;
  cursor: pointer;
  transform: translateX(-50%);
  transition: transform 0.2s ease, opacity 0.2s ease;
}
.night-tick:hover {
  transform: translateX(-50%) scaleY(1.3);
  z-index: 10;
}
@media (prefers-reduced-motion: reduce) {
  .night-tick {
    transition: none !important;
  }
  .night-tick:hover {
    transform: translateX(-50%) !important;
  }
}
.section {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 16px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}
.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 720px) {
  .grid-2 { grid-template-columns: 1fr; }
  body { padding: 12px; }
}
.metrics {
  width: 100%;
  border-collapse: collapse;
  font-family: Cascadia Mono, Consolas, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 13px;
}
.metrics td {
  padding: 8px 12px;
  border-bottom: 1px solid #2A241C;
}
.metrics tr:last-child td { border-bottom: none; }
.metrics td:first-child { color: var(--muted); width: 45%; font-family: "Segoe UI", sans-serif; }
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  font-family: Cascadia Mono, Consolas, monospace;
}
.chart-row {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.chart-container {
  flex: 1;
  min-width: 280px;
  background: var(--bg);
  border-radius: 6px;
  padding: 12px;
  border: 1px solid var(--border);
}
.chart, .chart-container svg, .chart svg { width: 100%; height: auto; display: block; }
button, select, input {
  font-family: inherit;
  font-size: 13px;
}
button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible {
  outline: 2px solid var(--brass);
  outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
  *, ::before, ::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
.footer {
  text-align: center;
  color: var(--muted);
  margin-top: 24px;
  font-size: 11px;
}
.hint {
  display: block;
  font-size: 10px;
  line-height: 1.35;
  color: var(--muted);
  font-weight: normal;
  text-transform: none;
  letter-spacing: normal;
  margin-top: 2px;
  max-width: 340px;
}
.metrics td:first-child .hint { margin-top: 1px; }
body[data-lang="en"] .hint > span.ru,
body[data-lang="en"] .bi > span.ru { display: none; }
body[data-lang="ru"] .hint > span.en,
body[data-lang="ru"] .bi > span.en,
body:not([data-lang]) .hint > span.en,
body:not([data-lang]) .bi > span.en { display: none; }
.lang-btn {
  background: transparent;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 4px 10px;
  cursor: pointer;
  font-size: 11px;
}
.lang-btn:hover { color: var(--ink); border-color: var(--muted); }
.chart-legend { display:flex; gap:14px; flex-wrap:wrap; margin:4px 0 8px; font-size:11px; color:var(--muted); }
.chart-legend-item { display:inline-flex; align-items:center; gap:5px; }
.chart-swatch { width:10px; height:10px; border-radius:2px; display:inline-block; }

/* ── Extraction running indicators (2026-08-25) ─────────────────────────── */
@keyframes extpulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(121,160,94,.55); opacity: 1; }
  50% { box-shadow: 0 0 0 6px rgba(121,160,94,0); opacity: .75; }
}
.ext-pulse-dot {
  width: 9px; height: 9px; border-radius: 50%;
  background: var(--sage); display: inline-block; flex: none;
  animation: extpulse 1.2s ease-in-out infinite;
}
#ext-live {
  display: none; align-items: center; gap: 7px;
  background: rgba(121,160,94,.12); border: 1px solid var(--sage);
  border-radius: 999px; padding: 5px 12px;
  font-size: 12px; color: var(--sage); white-space: nowrap;
  font-family: "Cascadia Mono", Consolas, monospace;
}
#ext-dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: var(--muted); display: inline-block;
  vertical-align: middle; margin-right: 7px; flex: none;
}
#ext-dot.on { background: var(--sage); animation: extpulse 1.2s ease-in-out infinite; }
.ext-bar {
  height: 8px; background: var(--bg); border: 1px solid var(--border);
  border-radius: 999px; overflow: hidden; margin-top: 10px; max-width: 420px;
}
#ext-bar-fill {
  height: 100%; width: 0%;
  background: linear-gradient(90deg, var(--sage), var(--brass));
  transition: width .6s ease;
}
.ext-bar.on #ext-bar-fill {
  background-image:
    repeating-linear-gradient(45deg, rgba(255,255,255,.18) 0 8px, transparent 8px 16px),
    linear-gradient(90deg, var(--sage), var(--brass));
  animation: extstripes 1s linear infinite;
}
@keyframes extstripes { 100% { background-position: 22.6px 0, 0 0; } }
"""
