"""Time-series figures. One y axis per panel, never two scales on one axis: measures of different units get their own
row (small multiples sharing x)."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from tent_analysis.viz.theme import Theme, color_map

LINE = dict(width=2, shape='hv')


def _row_titles(fig: go.Figure, titles: list[str], theme: Theme) -> None:
    """Label each subplot row inside its own panel (top-left), leaving the space above the plot to the legend."""
    for i, title in enumerate(titles, start=1):
        domain = fig.layout[f'yaxis{"" if i == 1 else i}'].domain
        fig.add_annotation(xref='paper', yref='paper', x=0.0, y=domain[1], xanchor='left', yanchor='top',
                           text=f'<b>{title}</b>', showarrow=False, font=dict(size=11, color=theme.ink['secondary']),
                           bgcolor=_alpha(theme.ink['surface'], 0.8), borderpad=2)


def _facet_rows(df: pd.DataFrame, facet: str | None):
    if facet is None or facet not in df or df[facet].nunique() <= 1:
        return [None]
    return list(dict.fromkeys(df[facet]))


def price_timeline(prices: pd.DataFrame, revisions: pd.DataFrame | None = None, facet: str = 'source',
                   colors: dict | None = None, theme: Theme | None = None, title: str = 'Cleared price') -> go.Figure:
    """Step line of cleared price per market series over delivery interval start. If `revisions` (price_revisions
    output) is given, a translucent band shows the min/max across negotiation revisions per interval.
    Several sources/runs become stacked rows sharing the x axis."""
    theme = theme or Theme()
    colors = colors or color_map(prices['market_series'], theme)
    rows = _facet_rows(prices, facet)
    fig = make_subplots(rows=len(rows), cols=1, shared_xaxes=True, vertical_spacing=0.06)
    if rows != [None]:
        _row_titles(fig, [str(r) for r in rows], theme)
    shown = set()
    for i, row_key in enumerate(rows, start=1):
        part = prices if row_key is None else prices[prices[facet] == row_key]
        for series, grp in part.groupby('market_series', sort=True):
            grp = grp.sort_values('interval_start')
            color = colors.get(series, theme.ink['muted'])
            if revisions is not None and not revisions.empty:
                band = revisions[(revisions['market_series'] == series)]
                if row_key is not None and facet in band:
                    band = band[band[facet] == row_key]
                band = band.sort_values('interval_start')
                if not band.empty and (band['max'] > band['min']).any():
                    fig.add_trace(go.Scatter(x=pd.concat([band['interval_start'], band['interval_start'][::-1]]),
                                             y=pd.concat([band['max'], band['min'][::-1]]), fill='toself',
                                             fillcolor=_alpha(color, 0.15), line=dict(width=0), hoverinfo='skip',
                                             showlegend=False, name=f'{series} revisions'), row=i, col=1)
            fig.add_trace(go.Scatter(x=grp['interval_start'], y=grp['price'], mode='lines', name=str(series),
                                     legendgroup=str(series), showlegend=series not in shown,
                                     line=dict(color=color, **LINE),
                                     hovertemplate='%{y:.4f} $/kWh<extra>' + str(series) + '</extra>'), row=i, col=1)
            shown.add(series)
        fig.update_yaxes(title_text='$/kWh', row=i, col=1)
    fig.update_xaxes(title_text='Delivery interval start', row=len(rows), col=1)
    theme.apply(fig, title=title, height=max(320, 220 * len(rows) + 100))
    if prices['market_series'].nunique() <= 1:
        fig.update_layout(showlegend=False)
    return fig


def power_timeline(power: pd.DataFrame, net: pd.DataFrame | None = None, meters: pd.DataFrame | None = None,
                   colors: dict | None = None, theme: Theme | None = None, title: str = 'Scheduled power') -> go.Figure:
    """Scheduled power per owner as step lines in one panel (one unit, one axis), with an optional second row for
    net power and metered power. Generation is positive, load negative."""
    theme = theme or Theme()
    colors = colors or color_map(power['owner_name'], theme)
    rows = 2 if (net is not None and not net.empty) or (meters is not None and not meters.empty) else 1
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        row_heights=[0.65, 0.35] if rows == 2 else None)
    if rows == 2:
        _row_titles(fig, ['By owner', 'Net and metered'], theme)
    for owner, grp in power.groupby('owner_name', sort=True):
        grp = grp.sort_values('interval_start')
        fig.add_trace(go.Scatter(x=grp['interval_start'], y=grp['power_kw'], mode='lines', name=str(owner),
                                 line=dict(color=colors.get(owner, theme.ink['muted']), **LINE),
                                 hovertemplate='%{y:,.1f} kW<extra>' + str(owner) + '</extra>'), row=1, col=1)
    fig.add_hline(y=0, line=dict(color=theme.ink['axis'], width=1), row=1, col=1)
    if rows == 2:
        if net is not None and not net.empty:
            n = net.sort_values('interval_start')
            fig.add_trace(go.Scatter(x=n['interval_start'], y=n['net_kw'], mode='lines', name='Net scheduled',
                                     line=dict(color=theme.ink['primary'], **LINE),
                                     hovertemplate='%{y:,.1f} kW<extra>Net scheduled</extra>'), row=2, col=1)
        if meters is not None and not meters.empty:
            for meter, grp in meters.groupby('meter_name', sort=True):
                fig.add_trace(go.Scatter(x=grp['reading_time'], y=grp['value'], mode='lines', name=f'Metered: {meter}',
                                         line=dict(color=theme.ink['secondary'], width=2, dash='dot'),
                                         hovertemplate='%{y:,.1f}<extra>Metered: ' + str(meter) + '</extra>'),
                              row=2, col=1)
        fig.add_hline(y=0, line=dict(color=theme.ink['axis'], width=1), row=2, col=1)
        fig.update_yaxes(title_text='kW', row=2, col=1)
    fig.update_yaxes(title_text='kW (generation +, load -)', row=1, col=1)
    fig.update_xaxes(title_text='Delivery interval start', row=rows, col=1)
    theme.apply(fig, title=title, height=520 if rows == 2 else 380)
    return fig


def add_state_bands(fig: go.Figure, state_changes: pd.DataFrame, market_name: str, theme: Theme | None = None,
                    states: tuple = ('Negotiation', 'Delivery')) -> go.Figure:
    """Light vertical bands behind the data for selected states of one market, from its state-change rows."""
    theme = theme or Theme()
    df = state_changes[state_changes['market_name'] == market_name].sort_values('record_time')
    if df.empty:
        return fig
    times = list(df['record_time']) + [None]
    for (start, state), end in zip(zip(df['record_time'], df['state']), times[1:]):
        if state in states and end is not None:
            fig.add_vrect(x0=start, x1=end, fillcolor=_alpha(theme.ink['muted'], 0.08), line_width=0,
                          annotation_text=state, annotation_position='top left',
                          annotation_font=dict(color=theme.ink['muted'], size=10))
    return fig


def _alpha(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f'rgba({r},{g},{b},{alpha})'
