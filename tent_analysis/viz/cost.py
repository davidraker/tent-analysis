"""Cost bars: grouped by period (hour, weekday, month, day) with one colour per source/run, baseline first."""
import pandas as pd
import plotly.graph_objects as go

from tent_analysis.viz.theme import Theme, color_map, text


def cost_bars(aggregated: pd.DataFrame, by: str, baseline: str | None = None, colors: dict | None = None,
              theme: Theme | None = None, title: str | None = None, group: str = 'source') -> go.Figure:
    """Grouped bars of cost per period. `aggregated` is aggregate_cost() output summed over owners; bars are grouped
    by `group` (source by default, baseline first). Thin bars, a surface-coloured 2px ring separating neighbours."""
    theme = theme or Theme()
    df = aggregated.groupby([group, by], as_index=False, observed=True)['cost'].sum()
    groups = list(dict.fromkeys(df[group]))
    if baseline in groups:
        groups.remove(baseline)
        groups.insert(0, baseline)
    colors = colors or color_map(groups, theme)
    fig = go.Figure()
    for g in groups:
        part = df[df[group] == g]
        fig.add_trace(go.Bar(x=part[by].astype(str), y=part['cost'], name=text(g),
                             marker=dict(color=colors.get(g, theme.ink['muted']),
                                         line=dict(color=theme.ink['surface'], width=2)),
                             hovertemplate='%{x}: $%{y:,.2f}<extra>' + text(g) + '</extra>'))
    fig.update_layout(barmode='group', bargap=0.3, bargroupgap=0.05)
    fig.update_yaxes(title_text='Cost [$] (positive = paid)')
    fig.update_xaxes(title_text=by.capitalize())
    theme.apply(fig, title=title or f'Energy cost by {by}', height=380)
    fig.update_layout(hovermode='x')
    if len(groups) <= 1:
        fig.update_layout(showlegend=False)
    return fig
