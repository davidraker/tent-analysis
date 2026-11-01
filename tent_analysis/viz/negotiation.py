"""Negotiation trace, horizon heatmap, convergence panel, market timeline, consensus strip (Phase 2)."""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from tent_analysis.viz.theme import SEQUENTIAL, STATUS, Theme, color_map, text, time_axis
from tent_analysis.viz.timelines import LINE, _row_titles, add_state_bands

STATE_ORDER = ['Initial', 'Inactive', 'Active', 'Negotiation', 'MarketLead', 'DeliveryLead', 'Delivery', 'Reconcile',
               'Expired']


def negotiation_trace(history: pd.DataFrame, measurement_type: str, interval_start, state_changes: pd.DataFrame | None = None,
                      market_name: str | None = None, colors: dict | None = None, theme: Theme | None = None,
                      unit: str = '') -> go.Figure:
    """Value versus record time for one delivery interval, one line per owner: how the negotiation evolved.
    Market state bands are drawn behind when state changes and a market name are given."""
    theme = theme or Theme()
    df = history[(history['measurement_type'] == measurement_type) & (history['interval_start'] == interval_start)]
    colors = colors or color_map(df['owner_name'], theme)
    fig = go.Figure()
    for owner, part in df.groupby('owner_name', sort=True):
        part = part.sort_values('record_time')
        fig.add_trace(go.Scatter(x=part['record_time'], y=part['value'], mode='lines+markers', name=text(owner),
                                 line=dict(color=colors.get(owner, theme.ink['muted']), **LINE),
                                 marker=dict(size=8, color=colors.get(owner, theme.ink['muted']),
                                             line=dict(color=theme.ink['surface'], width=2)),
                                 hovertemplate='%{y:,.4f} ' + unit + '<extra>' + text(owner) + '</extra>'))
    if state_changes is not None and market_name:
        add_state_bands(fig, state_changes, market_name, theme)
    fig.update_xaxes(title_text=time_axis('Record time', history['record_time']))
    fig.update_yaxes(title_text=f'{measurement_type} {unit}'.strip())
    theme.apply(fig, title=f'Negotiation of {measurement_type}, interval {pd.Timestamp(interval_start):%b %d %H:%M}',
                height=400)
    if df['owner_name'].nunique() <= 1:
        fig.update_layout(showlegend=False)
    return fig


def horizon_heatmap(matrix: pd.DataFrame, theme: Theme | None = None, title: str = 'Forecast horizon', unit: str = '') -> go.Figure:
    """Rows are delivery intervals, columns record-time bins, colour the value known at that time (sequential ramp)."""
    theme = theme or Theme()
    if matrix.empty:
        return theme.apply(go.Figure(), title=title)
    fig = go.Figure(go.Heatmap(z=matrix.values, x=matrix.columns, y=matrix.index, colorscale=SEQUENTIAL,
                               colorbar=dict(title=unit, thickness=12, outlinewidth=0,
                                             tickfont=dict(color=theme.ink['muted'])),
                               hovertemplate='known at %{x}<br>interval %{y}<br>%{z:,.4f} ' + unit + '<extra></extra>',
                               xgap=1, ygap=1))
    fig.update_xaxes(title_text=time_axis('Record time', matrix.columns), showgrid=False)
    fig.update_yaxes(title_text=time_axis('Delivery interval start', matrix.index), showgrid=False)
    theme.apply(fig, title=title, height=max(360, 18 * len(matrix.index) + 140))
    fig.update_layout(hovermode='closest')
    return fig


def convergence_panel(conv: pd.DataFrame, colors: dict | None = None, theme: Theme | None = None) -> go.Figure:
    """Revisions per interval as bars (one colour per owner) over a strip of converged flags (status colours)."""
    theme = theme or Theme()
    colors = colors or color_map(conv['owner_name'], theme)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.06)
    _row_titles(fig, ['Revisions per interval', 'Converged'], theme)
    for owner, part in conv.groupby('owner_name', sort=True):
        part = part.sort_values('interval_start')
        fig.add_trace(go.Bar(x=part['interval_start'], y=part['revisions'], name=text(owner),
                             marker=dict(color=colors.get(owner, theme.ink['muted']),
                                         line=dict(color=theme.ink['surface'], width=1)),
                             hovertemplate='%{y} revisions<extra>' + text(owner) + '</extra>'), row=1, col=1)
    flags = conv[conv['converged'].notna()]
    for converged, label, color in ((True, 'converged', STATUS['good']), (False, 'not converged', STATUS['critical'])):
        pts = flags[flags['converged'].astype(bool) == converged]
        if pts.empty:
            continue
        fig.add_trace(go.Scatter(x=pts['interval_start'], y=pts['owner_name'].map(text), mode='markers', name=label,
                                 marker=dict(symbol='square', size=9, color=color,
                                             line=dict(color=theme.ink['surface'], width=1)),
                                 hovertemplate=label + '<extra>%{y}</extra>'), row=2, col=1)
    fig.update_layout(barmode='stack', bargap=0.15)
    fig.update_yaxes(title_text='revisions', row=1, col=1)
    fig.update_yaxes(showgrid=False, row=2, col=1)
    fig.update_xaxes(title_text=time_axis('Delivery interval start', conv['interval_start']), row=2, col=1)
    theme.apply(fig, title='Negotiation convergence', height=460)
    return fig


def market_timeline(segments: pd.DataFrame, theme: Theme | None = None, title: str = 'Market states') -> go.Figure:
    """Gantt of market states: one row per market, one bar per state stay, fixed colour per state."""
    theme = theme or Theme()
    palette = theme.categorical
    state_colors = {state: palette[i % len(palette)] for i, state in enumerate(STATE_ORDER)}
    fig = go.Figure()
    markets = [text(m) for m in dict.fromkeys(segments.sort_values('start')['market_name'])]
    shown = set()
    for _, seg in segments.iterrows():
        duration_ms = max((seg['end'] - seg['start']).total_seconds() * 1000.0, 1.0)
        fig.add_trace(go.Bar(x=[duration_ms], y=[text(seg['market_name'])], base=[seg['start']], orientation='h',
                             name=text(seg['state']), legendgroup=seg['state'], showlegend=seg['state'] not in shown,
                             marker=dict(color=state_colors.get(seg['state'], theme.ink['muted']),
                                         line=dict(color=theme.ink['surface'], width=2)),
                             customdata=[[text(seg['state']), str(seg['start']), str(seg['end'])]],
                             hovertemplate='%{customdata[0]}<br>%{customdata[1]} to %{customdata[2]}<extra>%{y}</extra>'))
        shown.add(seg['state'])
    fig.update_layout(barmode='overlay', bargap=0.35)
    fig.update_xaxes(type='date', title_text=time_axis('Time', segments['start']))
    fig.update_yaxes(categoryorder='array', categoryarray=markets[::-1], title_text='')
    theme.apply(fig, title=title, height=max(300, 36 * len(markets) + 160))
    fig.update_layout(hovermode='closest')
    return fig


def consensus_strip(consensus: pd.DataFrame, theme: Theme | None = None) -> go.Figure:
    """Full-consensus flag per market series and interval as a status strip."""
    theme = theme or Theme()
    fig = go.Figure()
    for flag, label, color in ((True, 'consensus', STATUS['good']), (False, 'no consensus', STATUS['critical'])):
        pts = consensus[consensus['consensus'].astype(bool) == flag]
        if pts.empty:
            continue
        fig.add_trace(go.Scatter(x=pts['interval_start'], y=pts['market_series'].map(text), mode='markers', name=label,
                                 marker=dict(symbol='square', size=10, color=color,
                                             line=dict(color=theme.ink['surface'], width=1)),
                                 hovertemplate=label + '<extra>%{y}</extra>'))
    fig.update_xaxes(title_text=time_axis('Delivery interval start', consensus['interval_start']))
    fig.update_yaxes(showgrid=False, title_text='')
    theme.apply(fig, title='Full consensus by interval', height=max(240, 40 * consensus['market_series'].nunique() + 160))
    fig.update_layout(hovermode='closest')
    return fig


def device_panel(topics: pd.DataFrame, theme: Theme | None = None, title: str = 'Device points',
                 max_rows: int = 24) -> go.Figure:
    """Historian topics as small multiples: one panel per point name (the topic's last path segment, so one unit per
    axis), one line per device (the rest of the topic path). Topics whose values are not numeric are not drawable here
    and raise; the caller filters them (Views.devices). More point names than `max_rows` raise too: split the topics."""
    theme = theme or Theme()
    df = topics.assign(point=topics['topic'].str.rsplit('/', n=1).str[-1],
                       device=topics['topic'].str.rsplit('/', n=1).str[0])
    df['number'] = pd.to_numeric(df['value'], errors='coerce')
    unusable = sorted(df.loc[df['number'].isna() & df['value'].notna(), 'topic'].unique())
    if unusable:
        raise ValueError(f'{len(unusable)} topics hold non-numeric values and cannot be drawn as lines: {unusable[:5]}')
    points = sorted(df['point'].unique())
    if len(points) > max_rows:
        raise ValueError(f'{len(points)} point names exceed max_rows={max_rows}; split the topics into several figures')
    devices = sorted(df['device'].unique())
    colors = color_map(devices, theme)
    fig = make_subplots(rows=len(points), cols=1, shared_xaxes=True, vertical_spacing=min(0.04, 0.8 / max(1, len(points) - 1)))
    _row_titles(fig, points, theme)
    shown = set()
    for i, point in enumerate(points, start=1):
        for device, part in df[df['point'] == point].groupby('device', sort=True):
            part = part.sort_values('ts')
            fig.add_trace(go.Scatter(x=part['ts'], y=part['number'], mode='lines', name=text(device), legendgroup=device,
                                     showlegend=device not in shown, line=dict(color=colors[device], width=2),
                                     hovertemplate='%{y}<extra>' + text(device) + '/' + text(point) + '</extra>'), row=i, col=1)
            shown.add(device)
    fig.update_xaxes(title_text=time_axis('Time', topics['ts']), row=len(points), col=1)
    theme.apply(fig, title=title, height=max(300, 150 * len(points) + 100))
    fig.update_layout(showlegend=len(devices) > 1, hovermode='closest')
    return fig
