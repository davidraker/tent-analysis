"""Run comparison: deltas of a metric against a baseline, as diverging bars (blue below, red above, grey at zero)."""
import pandas as pd
import plotly.graph_objects as go

from tent_analysis.viz.theme import Theme, text


def run_comparison(comparison: pd.DataFrame, x: str, value: str = 'delta', theme: Theme | None = None,
                   title: str | None = None, unit: str = '') -> go.Figure:
    """Bars of `value` (delta vs baseline) per `x`, one trace per variant source; colour encodes sign with the
    diverging pair so direction reads at a glance. Identity stays in the legend and hover."""
    theme = theme or Theme()
    low, mid, high = theme.diverging
    fig = go.Figure()
    for source, part in comparison.groupby('source', sort=True):
        part = part.sort_values(x)
        fig.add_trace(go.Bar(x=part[x].astype(str), y=part[value], name=text(source),
                             marker=dict(color=[high if v > 0 else low if v < 0 else mid for v in part[value]],
                                         line=dict(color=theme.ink['surface'], width=2)),
                             customdata=part[f'{value.split("_")[0]}_pct'] if f'{value}_pct' in part else None,
                             hovertemplate='%{x}: %{y:+,.2f} ' + unit + '<extra>' + text(source) + '</extra>'))
    baseline = comparison['baseline'].iloc[0] if not comparison.empty else ''
    fig.add_hline(y=0, line=dict(color=theme.ink['axis'], width=1))
    fig.update_layout(barmode='group', bargap=0.3)
    fig.update_yaxes(title_text=f'Difference from {text(baseline)} {unit}'.strip())
    fig.update_xaxes(title_text=x.replace('_', ' ').capitalize())
    theme.apply(fig, title=title or f'Change versus {baseline}', height=380)
    fig.update_layout(hovermode='x')
    return fig
