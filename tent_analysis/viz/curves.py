"""Supply/demand curves, signals, and bid position (Phase 2)."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from tent_analysis.analysis.curves import POSITIONS, latest_version
from tent_analysis.viz.theme import Theme, color_map
from tent_analysis.viz.timelines import _alpha, _row_titles

MARKER = dict(size=8, line=dict(width=2))


def _curve_traces(curve: pd.DataFrame, name: str, color: str, theme: Theme, opacity: float = 1.0, dash: str = 'solid',
                  showlegend: bool = True) -> list[go.Scatter]:
    """A curve as a 2px line through its price-sorted vertices with 8px markers; discontinuous vertices are hollow."""
    c = curve.sort_values(['price', 'quantity'])
    surface = theme.ink['surface']
    symbols = ['circle' if bool(cont) else 'circle-open' for cont in c['continuity']]
    line = go.Scatter(x=c['price'], y=c['quantity'], mode='lines+markers', name=name, legendgroup=name,
                      showlegend=showlegend, opacity=opacity,
                      line=dict(color=color, width=2, dash=dash),
                      marker=dict(color=color, symbol=symbols, size=MARKER['size'], line=dict(color=surface, width=2)),
                      hovertemplate='%{x:.4f} $/kWh, %{y:,.1f} kW<extra>' + name + '</extra>')
    return [line]


def curve_figure(curve: pd.DataFrame, history: pd.DataFrame | None = None, marginal_price: float | None = None,
                 balance: dict | None = None, name: str | None = None, color: str | None = None,
                 theme: Theme | None = None, title: str | None = None) -> go.Figure:
    """One supply/demand curve: price on x, quantity on y. Earlier versions (from `history`, same curve id) are drawn
    faded behind the latest; the cleared marginal price is a vertical rule and the balance point a marker."""
    theme = theme or Theme()
    color = color or theme.categorical[0]
    name = name or (str(curve['owner_name'].iloc[0]) if not curve.empty else 'curve')
    fig = go.Figure()
    if history is not None and not history.empty:
        versions = sorted(history['record_time'].unique())
        earlier = versions[:-1]
        for i, when in enumerate(earlier):
            v = history[history['record_time'] == when]
            fig.add_traces(_curve_traces(v, f'{name} (revision {i + 1})', color, theme,
                                         opacity=0.15 + 0.45 * (i + 1) / max(1, len(earlier)), showlegend=False))
    if not curve.empty:
        fig.add_traces(_curve_traces(curve, name, color, theme))
    if marginal_price is not None:
        fig.add_vline(x=marginal_price, line=dict(color=theme.ink['secondary'], width=1, dash='dot'),
                      annotation_text=f'cleared {marginal_price:.4f}', annotation_position='top',
                      annotation_font=dict(size=10, color=theme.ink['secondary']))
    if balance and balance.get('quantity') is not None:
        fig.add_trace(go.Scatter(x=[balance['price']], y=[balance['quantity']], mode='markers', name='Balance point',
                                 marker=dict(symbol='diamond', size=11, color=theme.ink['primary'],
                                             line=dict(color=theme.ink['surface'], width=2)),
                                 hovertemplate='balance %{x:.4f} $/kWh, %{y:,.1f} kW<extra></extra>'))
    fig.add_hline(y=0, line=dict(color=theme.ink['axis'], width=1))
    fig.update_xaxes(title_text='Price [$/kWh]')
    fig.update_yaxes(title_text='Quantity [kW] (supply +, demand -)')
    theme.apply(fig, title=title or f'Supply/demand curve: {name}', height=420)
    fig.update_layout(hovermode='closest', showlegend=len(fig.data) > 1)
    return fig


def curve_multiples(curves: pd.DataFrame, owner: str, prices: pd.DataFrame | None = None, max_panels: int = 12,
                    columns: int = 3, colors: dict | None = None, theme: Theme | None = None) -> go.Figure:
    """Small multiples of one owner's curve across delivery intervals, shared axes, cleared price marked in each."""
    theme = theme or Theme()
    df = latest_version(curves[curves['owner_name'] == owner])
    starts = sorted(df['interval_start'].unique())[:max_panels]
    rows = max(1, -(-len(starts) // columns))
    color = (colors or color_map(curves['owner_name'], theme)).get(owner, theme.categorical[0])
    fig = make_subplots(rows=rows, cols=columns, shared_xaxes=True, shared_yaxes=True, horizontal_spacing=0.04,
                        vertical_spacing=0.08, subplot_titles=[pd.Timestamp(s).strftime('%b %d %H:%M') for s in starts])
    for i, start in enumerate(starts):
        r, c = divmod(i, columns)
        part = df[df['interval_start'] == start]
        for trace in _curve_traces(part, owner, color, theme, showlegend=(i == 0)):
            fig.add_trace(trace, row=r + 1, col=c + 1)
        if prices is not None and not prices.empty:
            p = prices[prices['interval_start'] == start]
            if not p.empty:
                fig.add_vline(x=float(p['price'].iloc[-1]), line=dict(color=theme.ink['secondary'], width=1, dash='dot'),
                              row=r + 1, col=c + 1)
    fig.update_annotations(font=dict(size=11, color=theme.ink['secondary']))
    fig.update_xaxes(title_text='$/kWh', row=rows, col=1)
    fig.update_yaxes(title_text='kW', col=1)
    theme.apply(fig, title=f'{owner}: curves by delivery interval', height=max(360, 230 * rows + 80))
    fig.update_layout(hovermode='closest', showlegend=False, margin=dict(t=80))
    return fig


def signal_comparison(signals: pd.DataFrame, neighbor: str, interval_start, theme: Theme | None = None,
                      threshold: float | None = None) -> go.Figure:
    """Sent (solid) versus received (dashed) signal curves for one neighbor and interval."""
    theme = theme or Theme()
    df = signals[(signals['neighbor_name'] == neighbor) & (signals['interval_start'] == interval_start)]
    fig = go.Figure()
    colors = {'sent': theme.categorical[0], 'received': theme.categorical[1]}
    for direction, part in df.groupby('direction'):
        newest = part[part['record_time'] == part['record_time'].max()]
        fig.add_traces(_curve_traces(newest, direction.capitalize(), colors.get(direction, theme.ink['muted']), theme,
                                     dash='solid' if direction == 'sent' else 'dash'))
    fig.add_hline(y=0, line=dict(color=theme.ink['axis'], width=1))
    if threshold is not None:
        fig.add_annotation(xref='paper', yref='paper', x=1, y=1, xanchor='right', yanchor='bottom', showarrow=False,
                           text=f'convergence threshold {threshold:g} kW', font=dict(size=10, color=theme.ink['muted']))
    fig.update_xaxes(title_text='Price [$/kWh]')
    fig.update_yaxes(title_text='Quantity [kW]')
    theme.apply(fig, title=f'{neighbor}: sent vs received, {pd.Timestamp(interval_start):%b %d %H:%M}', height=400)
    fig.update_layout(hovermode='closest')
    return fig


def bid_position_figure(power_with_position: pd.DataFrame, theme: Theme | None = None, title: str | None = None) -> go.Figure:
    """Scheduled power per owner over time, points coloured by where the schedule sits within the owner's own curve
    (ordered: below, lower, midpoint, upper, above) on the diverging scale: blue below, grey midpoint, red above."""
    theme = theme or Theme()
    low, mid, high = theme.diverging
    scale = {'below': low, 'lower': _alpha(low, 0.55), 'midpoint': mid if theme.mode == 'dark' else '#b9b7ae',
             'upper': _alpha(high, 0.55), 'above': high}
    has_curve = power_with_position['position'].notna()
    owners = sorted(power_with_position.loc[has_curve, 'owner_name'].unique()) or sorted(power_with_position['owner_name'].unique())
    fig = make_subplots(rows=len(owners), cols=1, shared_xaxes=True, vertical_spacing=0.06)
    _row_titles(fig, owners, theme)
    for i, owner in enumerate(owners, start=1):
        part = power_with_position[power_with_position['owner_name'] == owner].sort_values('interval_start')
        # The whole schedule as a quiet baseline; only intervals with an archived curve get a position colour.
        fig.add_trace(go.Scatter(x=part['interval_start'], y=part['power_kw'], mode='lines', showlegend=False,
                                 line=dict(color=theme.ink['axis'], width=1, shape='hv'), hoverinfo='skip'), row=i, col=1)
        for position in POSITIONS:
            pts = part[part['position'].astype(object) == position]
            if pts.empty:
                continue
            fig.add_trace(go.Scatter(x=pts['interval_start'], y=pts['power_kw'], mode='markers', name=position,
                                     legendgroup=position, showlegend=(i == 1),
                                     marker=dict(color=scale[position], size=8, line=dict(color=theme.ink['surface'], width=2)),
                                     hovertemplate='%{y:,.1f} kW, ' + position + '<extra>' + owner + '</extra>'),
                          row=i, col=1)
        fig.update_yaxes(title_text='kW', row=i, col=1)
    fig.update_xaxes(title_text='Delivery interval start', row=len(owners), col=1)
    theme.apply(fig, title=title or 'Schedule position within own curve', height=max(360, 200 * len(owners) + 100))
    fig.update_layout(hovermode='closest')
    return fig
