"""Consensus-run figures over analysis.consensus output: how each negotiation closed along simulated time, the signals
each market exchanged, and the passes or time it took, variants side by side."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from tent_analysis.viz.theme import STATUS, Theme, color_map, text, time_axis
from tent_analysis.viz.timelines import _alpha, _row_titles

OUTCOME_LABELS = {True: 'closed in consensus', False: 'closed without consensus'}
OUTCOME_COLORS = {True: STATUS['good'], False: STATUS['critical']}


def _bar_width_ms(starts: pd.Series, groups: int) -> float:
    """An explicit bar width for a date axis: a share of the smallest spacing between markets (a lone market on a date
    axis would otherwise get no width), split between `groups` bars standing side by side."""
    unique = pd.Series(sorted(pd.to_datetime(starts).dropna().unique()))
    spacing = unique.diff().min() if len(unique) > 1 else pd.Timedelta(minutes=15)
    return spacing.total_seconds() * 1000.0 * 0.7 / max(1, groups)


def _rows(df: pd.DataFrame, key: str = 'market_series') -> list[tuple]:
    """One panel row per (actor, series) when several actors share the frame, else one per series."""
    actors = sorted(df['actor'].dropna().astype(str).unique())
    pairs = df[['actor', key]].drop_duplicates().sort_values(['actor', key])
    return [(a, s, f'{a}: {s}' if len(actors) > 1 else str(s)) for a, s in zip(pairs['actor'], pairs[key])]


def consensus_outcome_timeline(outcomes: pd.DataFrame, theme: Theme | None = None,
                               title: str = 'Consensus negotiations') -> go.Figure:
    """One bar per market along simulated time, from its negotiation start to its end, coloured by whether it closed in
    consensus (status colours, named in the legend), with a marker where full consensus was first reached. Rows are the
    market series, prefixed by the actor when several actors are present. Markets without recorded negotiation bounds
    are drawn as a marker at their close record."""
    theme = theme or Theme()
    df = outcomes.copy()
    rows = _rows(df)
    labels = {(a, s): label for a, s, label in rows}
    df['row'] = [text(labels[(a, s)]) for a, s in zip(df['actor'], df['market_series'])]
    fig = go.Figure()
    bounded = df[df['negotiation_start'].notna() & df['negotiation_end'].notna()]
    for closed in (True, False):
        part = bounded[bounded['closed_in_consensus'] == closed]
        if part.empty:
            continue
        duration_ms = ((part['negotiation_end'] - part['negotiation_start']).dt.total_seconds() * 1000.0).clip(lower=1.0)
        custom = [[text(n), str(s), str(e), int(p), int(c), int(i), '' if pd.isna(t) else f'{t.total_seconds() / 60:.1f} min']
                  for n, s, e, p, c, i, t in zip(part['market_name'], part['negotiation_start'], part['negotiation_end'],
                                                 part['passes'], part['consensus_at_close'], part['intervals'],
                                                 part['time_to_consensus'])]
        fig.add_trace(go.Bar(x=duration_ms, y=part['row'], base=part['negotiation_start'], orientation='h',
                             name=OUTCOME_LABELS[closed], legendgroup=OUTCOME_LABELS[closed],
                             marker=dict(color=OUTCOME_COLORS[closed], line=dict(color=theme.ink['surface'], width=2)),
                             customdata=custom,
                             hovertemplate='%{customdata[0]}<br>negotiation %{customdata[1]} to %{customdata[2]}<br>'
                                           '%{customdata[3]} recorded passes, %{customdata[4]} of %{customdata[5]} intervals '
                                           'in consensus at close<br>time to consensus %{customdata[6]}<extra></extra>'))
    reached = df[df['consensus_time'].notna()]
    if not reached.empty:
        fig.add_trace(go.Scatter(x=reached['consensus_time'], y=reached['row'], mode='markers', name='full consensus reached',
                                 marker=dict(symbol='diamond', size=9, color=theme.ink['primary'],
                                             line=dict(color=theme.ink['surface'], width=2)),
                                 customdata=reached['market_name'].map(text),
                                 hovertemplate='full consensus at %{x}<extra>%{customdata}</extra>'))
    unbounded = df[df['negotiation_start'].isna() | df['negotiation_end'].isna()]
    for closed in (True, False):
        part = unbounded[unbounded['closed_in_consensus'] == closed]
        if part.empty:
            continue
        fig.add_trace(go.Scatter(x=part['close_time'], y=part['row'], mode='markers', name=OUTCOME_LABELS[closed],
                                 legendgroup=OUTCOME_LABELS[closed], showlegend=part.index[0] not in bounded.index and
                                 bounded[bounded['closed_in_consensus'] == closed].empty,
                                 marker=dict(symbol='square', size=10, color=OUTCOME_COLORS[closed],
                                             line=dict(color=theme.ink['surface'], width=2)),
                                 customdata=part['market_name'].map(text),
                                 hovertemplate=OUTCOME_LABELS[closed] + ' (no negotiation bounds recorded)<extra>%{customdata}</extra>'))
    fig.update_layout(barmode='overlay', bargap=0.35)
    times = pd.concat([df['negotiation_start'].dropna(), df['close_time'].dropna()])
    fig.update_xaxes(type='date', title_text=time_axis('Simulated time', times))
    fig.update_yaxes(categoryorder='array', categoryarray=[text(label) for _, _, label in rows][::-1], title_text='', showgrid=False)
    theme.apply(fig, title=title, height=max(280, 44 * len(rows) + 180))
    fig.update_layout(hovermode='closest')
    return fig


def signal_traffic_panel(traffic: pd.DataFrame, theme: Theme | None = None, title: str = 'Signal traffic') -> go.Figure:
    """Per market (x: its delivery start), the signals sent and the signals received as two bars side by side, each
    stacked into revised signals (full hue) and acknowledgements (the same hue, lighter). Neighbors are summed; rows are
    the market series, prefixed by the actor when several."""
    theme = theme or Theme()
    rows = _rows(traffic)
    fig = make_subplots(rows=len(rows), cols=1, shared_xaxes=True, vertical_spacing=0.08)
    if len(rows) > 1:
        _row_titles(fig, [label for _, _, label in rows], theme)
    hues = {'sent': theme.categorical[0], 'received': theme.categorical[1]}
    parts = [('sent', 'sent_revised', 'sent, revised', 1.0), ('sent', 'sent_acknowledgements', 'sent, acknowledgement', 0.45),
             ('received', 'received_revised', 'received, revised', 1.0),
             ('received', 'received_acknowledgements', 'received, acknowledgement', 0.45)]
    shown = set()
    width = _bar_width_ms(traffic['delivery_start'], groups=2)
    for i, (actor, series, _) in enumerate(rows, start=1):
        part = traffic[(traffic['actor'] == actor) & (traffic['market_series'] == series)]
        per_market = part.groupby(['market_id', 'market_name', 'delivery_start'], as_index=False)[
            [column for _, column, _, _ in parts]].sum().sort_values('delivery_start')
        for direction, column, name, alpha in parts:
            if per_market[column].sum() == 0:
                continue
            color = hues[direction] if alpha == 1.0 else _alpha(hues[direction], alpha)
            fig.add_trace(go.Bar(x=per_market['delivery_start'], y=per_market[column], name=name, legendgroup=name,
                                 offsetgroup=direction, showlegend=name not in shown, width=width,
                                 marker=dict(color=color, line=dict(color=theme.ink['surface'], width=1)),
                                 customdata=per_market['market_name'].map(text),
                                 hovertemplate='%{y} ' + name + '<extra>%{customdata}</extra>'), row=i, col=1)
            shown.add(name)
        fig.update_yaxes(title_text='signals', row=i, col=1)
    fig.update_layout(barmode='stack', bargap=0.3, bargroupgap=0.05)
    fig.update_xaxes(title_text=time_axis('Market delivery start', traffic['delivery_start']), row=len(rows), col=1)
    theme.apply(fig, title=title, height=max(360, 240 * len(rows) + 120))
    return fig


def time_to_consensus(outcomes: pd.DataFrame, value: str = 'time_to_consensus', colors: dict | None = None,
                      theme: Theme | None = None, title: str | None = None) -> go.Figure:
    """Per market (x: its delivery start), the time to full consensus in minutes (`value='time_to_consensus'`) or the
    recorded passes (`value='passes'`) as bars, one colour per source so variants from several archives stand side by
    side. A market that never reached consensus has no time; it is marked with a cross at the top of its negotiation
    window (none when the window is unknown). Rows are the market series, prefixed by the actor when several."""
    theme = theme or Theme()
    sources = sorted(outcomes['source'].astype(str).unique())
    colors = colors or color_map(sources, theme)
    rows = _rows(outcomes)
    fig = make_subplots(rows=len(rows), cols=1, shared_xaxes=True, vertical_spacing=0.08)
    if len(rows) > 1:
        _row_titles(fig, [label for _, _, label in rows], theme)
    minutes = value == 'time_to_consensus'
    unit = 'min' if minutes else 'passes'
    shown = set()
    width = _bar_width_ms(outcomes['delivery_start'], groups=len(sources))
    for i, (actor, series, _) in enumerate(rows, start=1):
        part = outcomes[(outcomes['actor'] == actor) & (outcomes['market_series'] == series)]
        for source, grp in part.groupby('source', sort=True):
            grp = grp.sort_values('delivery_start')
            y = grp[value].dt.total_seconds() / 60.0 if minutes else grp[value]
            fig.add_trace(go.Bar(x=grp['delivery_start'], y=y, name=text(source), legendgroup=str(source),
                                 showlegend=source not in shown, width=width, offsetgroup=str(source),
                                 marker=dict(color=colors.get(source, theme.ink['muted']), line=dict(color=theme.ink['surface'], width=2)),
                                 customdata=grp['market_name'].map(text),
                                 hovertemplate='%{y:,.1f} ' + unit + '<extra>%{customdata}</extra>'), row=i, col=1)
            shown.add(source)
            if minutes:
                # A market that never reached consensus within a known window; one that never reached MarketLead (the
                # run ended first) has no deadline to miss and is left out.
                never = grp[grp['time_to_consensus'].isna() & grp['negotiation_window'].notna()]
                if not never.empty:
                    window = never['negotiation_window'].dt.total_seconds() / 60.0
                    fig.add_trace(go.Scatter(x=never['delivery_start'], y=window, mode='markers', name='no consensus by close',
                                             legendgroup='no consensus', showlegend='no consensus' not in shown,
                                             marker=dict(symbol='x', size=9, color=STATUS['critical'],
                                                         line=dict(color=theme.ink['surface'], width=1)),
                                             customdata=never['market_name'].map(text),
                                             hovertemplate='no consensus within the %{y:,.1f} min window<extra>%{customdata}</extra>'),
                                  row=i, col=1)
                    shown.add('no consensus')
        fig.update_yaxes(title_text='minutes to full consensus' if minutes else 'recorded passes', rangemode='tozero', row=i, col=1)
    fig.update_layout(barmode='group', bargap=0.3, bargroupgap=0.05)
    fig.update_xaxes(title_text=time_axis('Market delivery start', outcomes['delivery_start']), row=len(rows), col=1)
    theme.apply(fig, title=title or ('Time to full consensus' if minutes else 'Recorded consensus passes'),
                height=max(360, 240 * len(rows) + 120))
    if len(sources) <= 1 and 'no consensus' not in shown:
        fig.update_layout(showlegend=False)
    return fig
