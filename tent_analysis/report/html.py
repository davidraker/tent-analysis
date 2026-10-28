"""Assemble figures into one self-contained HTML report, and build the standard Phase 1 figure set for a source."""
import html
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio

from tent_analysis.analysis import (cleared_prices, price_revisions, scheduled_power, net_power, energy_cost,
                                    aggregate_cost)
from tent_analysis.data import Source, load_measurements, load_meter_readings
from tent_analysis.viz import Theme, color_map, price_timeline, power_timeline, cost_bars


def standard_figures(source: Source, theme: Theme | None = None) -> dict[str, go.Figure]:
    """Every figure family the archive can support: overview, curves, negotiation, markets, signals, bid position,
    costs, devices. Built through the dashboard views so report and dashboard always agree."""
    from tent_analysis.app.views import Selection, Views
    views = Views(source, Selection(mode=(theme or Theme()).mode))
    figures: dict[str, go.Figure] = {}
    for page in ('overview', 'curves', 'negotiation', 'markets', 'signals', 'bids', 'costs', 'devices'):
        figures.update(getattr(views, page)())
    return figures


def build_report(figures: dict[str, go.Figure], out: str | Path, title: str = 'TENT analysis',
                 theme: Theme | None = None) -> Path:
    """Write one HTML file: a table of contents and every figure, plotly.js embedded once."""
    theme = theme or Theme()
    ink = theme.ink
    out = Path(out)
    parts = [f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>',
             f'<style>body{{background:{ink["page"]};color:{ink["primary"]};font-family:Inter,"Segoe UI",Helvetica,Arial,'
             f'sans-serif;margin:0;padding:24px 32px}} h1{{font-size:22px;font-weight:600}} h2{{font-size:16px;'
             f'font-weight:600;margin:32px 0 8px}} nav a{{color:{ink["secondary"]};margin-right:16px;text-decoration:none}}'
             f' .fig{{background:{ink["surface"]};border:1px solid {ink["grid"]};border-radius:6px;padding:8px}}</style>',
             f'</head><body><h1>{html.escape(title)}</h1><nav>']
    parts += [f'<a href="#fig{i}">{html.escape(name)}</a>' for i, name in enumerate(figures)]
    parts.append('</nav>')
    for i, (name, fig) in enumerate(figures.items()):
        parts.append(f'<h2 id="fig{i}">{html.escape(name)}</h2><div class="fig">')
        parts.append(pio.to_html(fig, full_html=False, include_plotlyjs=(i == 0), config={'displaylogo': False,
                                                                                            'responsive': True}))
        parts.append('</div>')
    parts.append('</body></html>')
    out.write_text('\n'.join(parts), encoding='utf-8')
    return out
