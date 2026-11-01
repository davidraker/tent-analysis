"""Order-book figures: orders by status per interval, and matched versus standing quantity with the match price."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from tent_analysis.analysis.orders import STATUS_ORDER
from tent_analysis.viz.theme import STATUS, Theme, text, time_axis
from tent_analysis.viz.timelines import LINE, _row_titles


def _status_colors(theme: Theme) -> dict[str, str]:
    """Fixed colour per order status: the categorical palette in STATUS_ORDER, so a status never changes hue."""
    palette = theme.categorical
    return {status: palette[i % len(palette)] for i, status in enumerate(STATUS_ORDER)}


def order_status_bars(counts: pd.DataFrame, theme: Theme | None = None, title: str = 'Orders by status') -> go.Figure:
    """Stacked bars of order counts per delivery interval, one colour per status (order_counts output). Several market
    series become stacked rows sharing the x axis."""
    theme = theme or Theme()
    colors = _status_colors(theme)
    series = list(dict.fromkeys(counts.sort_values('market_series')['market_series']))
    fig = make_subplots(rows=len(series), cols=1, shared_xaxes=True, vertical_spacing=0.08)
    if len(series) > 1:
        _row_titles(fig, [str(s) for s in series], theme)
    shown = set()
    for i, name in enumerate(series, start=1):
        part = counts[counts['market_series'] == name]
        for status in STATUS_ORDER:
            pts = part[part['status'].astype(str) == status].sort_values('interval_start')
            if pts.empty:
                continue
            fig.add_trace(go.Bar(x=pts['interval_start'], y=pts['orders'], name=status, legendgroup=status,
                                 showlegend=status not in shown,
                                 marker=dict(color=colors[status], line=dict(color=theme.ink['surface'], width=1)),
                                 hovertemplate='%{y} ' + status.lower().replace('_', ' ') + '<extra>' + text(name) + '</extra>'),
                          row=i, col=1)
            shown.add(status)
        fig.update_yaxes(title_text='orders', row=i, col=1)
    fig.update_layout(barmode='stack', bargap=0.15)
    fig.update_xaxes(title_text=time_axis('Delivery interval start', counts['interval_start']), row=len(series), col=1)
    theme.apply(fig, title=title, height=max(360, 260 * len(series) + 120))
    return fig


def matched_quantity_figure(matched: pd.DataFrame, theme: Theme | None = None,
                            title: str = 'Matched and standing quantity') -> go.Figure:
    """Three rows sharing the interval axis: matched quantity as bars (kW), standing supply and demand as step lines
    (kW, their own row since a standing book can dwarf what traded), and the quantity-weighted match price where it is
    finite ($/kWh). From matched_quantities output."""
    theme = theme or Theme()
    df = matched.sort_values('interval_start')
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.4, 0.3, 0.3], vertical_spacing=0.07)
    _row_titles(fig, ['Matched', 'Standing', 'Match price'], theme)
    for name, part in df.groupby('market_series', sort=True):
        fig.add_trace(go.Bar(x=part['interval_start'], y=part['matched_kw'], name=f'matched: {text(name)}',
                             marker=dict(color=STATUS['good'], line=dict(color=theme.ink['surface'], width=1)),
                             customdata=part['legs'],
                             hovertemplate='%{y:,.1f} kW matched in %{customdata} legs<extra>' + text(name) + '</extra>'),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=part['interval_start'], y=part['standing_supply_kw'], mode='lines', name='standing supply',
                                 line=dict(color=theme.categorical[0], **LINE),
                                 hovertemplate='%{y:,.1f} kW standing supply<extra>' + text(name) + '</extra>'), row=2, col=1)
        fig.add_trace(go.Scatter(x=part['interval_start'], y=part['standing_demand_kw'], mode='lines', name='standing demand',
                                 line=dict(color=theme.categorical[1], **LINE),
                                 hovertemplate='%{y:,.1f} kW standing demand<extra>' + text(name) + '</extra>'), row=2, col=1)
        priced = part[part['match_price'].notna()]
        if not priced.empty:
            fig.add_trace(go.Scatter(x=priced['interval_start'], y=priced['match_price'], mode='lines+markers',
                                     name=f'match price: {text(name)}', line=dict(color=theme.ink['primary'], width=2),
                                     marker=dict(size=7, color=theme.ink['primary'], line=dict(color=theme.ink['surface'], width=2)),
                                     hovertemplate='%{y:.4f} $/kWh<extra>' + text(name) + '</extra>'), row=3, col=1)
    fig.update_layout(barmode='group', bargap=0.3)
    fig.update_yaxes(title_text='kW', row=1, col=1)
    fig.update_yaxes(title_text='kW', row=2, col=1)
    fig.update_yaxes(title_text='$/kWh', rangemode='tozero', row=3, col=1)
    fig.update_xaxes(title_text=time_axis('Delivery interval start', df['interval_start']), row=3, col=1)
    theme.apply(fig, title=title, height=620)
    return fig
