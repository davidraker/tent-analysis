"""
Dashboard logic without Streamlit: a Selection describes what the user picked, and each view function turns a Source
plus a Selection into figures. The Streamlit script only draws widgets and calls these, so they are testable.
"""
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go

from tent_analysis.analysis import (aggregate_cost, bid_position, cleared_prices, compare_runs, consensus, convergence,
                                    energy_cost, horizon_matrix, net_curve, net_power, price_revisions,
                                    scheduled_power, signal_pairs, single_curve, state_segments)
from tent_analysis.data import (Source, load_curves, load_historian_topics, load_measurements, load_meter_readings,
                                load_signals, load_state_changes)
from tent_analysis.viz import (Theme, bid_position_figure, cleared_price_for, color_map, consensus_strip,
                               convergence_panel, cost_bars, curve_figure, curve_multiples, device_panel,
                               horizon_heatmap, market_timeline, negotiation_trace, power_timeline, price_timeline,
                               run_comparison, signal_comparison)


@dataclass
class Selection:
    series: list[str] = field(default_factory=list)     # market series names; empty = all
    owners: list[str] = field(default_factory=list)     # component names; empty = all
    start: datetime | None = None
    end: datetime | None = None
    interval_start: datetime | None = None              # the focused delivery interval (linked views)
    owner: str | None = None                            # the focused owner for curve views
    neighbor: str | None = None
    mode: str = 'light'

    @property
    def theme(self) -> Theme:
        return Theme(self.mode)


def snap_interval(picked, intervals: list) -> pd.Timestamp | None:
    """The known delivery interval nearest to a clicked x value. Plotly hands the x back as a string in the axis's
    wall-clock time (zone stripped), so a naive value is localised to the intervals' zone and an aware one converted."""
    if picked is None or not intervals:
        return None
    zone = pd.Timestamp(intervals[0]).tz
    t = pd.Timestamp(picked)
    t = t.tz_localize(zone) if t.tz is None else t.tz_convert(zone)
    return min((pd.Timestamp(i) for i in intervals), key=lambda i: abs(i - t))


class Views:
    def __init__(self, source: Source, selection: Selection):
        self.source, self.sel = source, selection
        self.theme = selection.theme
        cat = source.catalog
        # One fixed colour per plotted entity and market series, assigned once per source so pages never repaint.
        self.colors = color_map(list(cat.participants['name']) + list(cat.market_series['name']), self.theme)
        self.market_types = cat.types_under('market')

    # ------------------------------------------------------------------ data
    def _filter(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply the series and owner selections. The owner selection is scoped to participants (assets and neighbors);
        market-owned rows such as MarginalPrice stay, since every page needs the price regardless of owner."""
        if df.empty:
            return df
        if self.sel.series and 'market_series' in df:
            df = df[df['market_series'].isin(self.sel.series)]
        if self.sel.owners and 'owner_name' in df:
            df = df[df['owner_name'].isin(self.sel.owners) | df['owner_type'].isin(self.market_types)]
        return df

    def measurements(self, types, history=False) -> pd.DataFrame:
        return self._filter(load_measurements(self.source, types=types, start=self.sel.start, end=self.sel.end,
                                              history=history))

    def intervals(self) -> list:
        prices = cleared_prices(self.measurements(['MarginalPrice']))
        return sorted(prices['interval_start'].unique()) if not prices.empty else []

    # ------------------------------------------------------------------ pages
    def overview(self) -> dict[str, go.Figure]:
        latest = self.measurements(['MarginalPrice', 'ScheduledPower'])
        history = self.measurements(['MarginalPrice'], history=True)
        prices, power = cleared_prices(latest), scheduled_power(latest)
        figures = {}
        if not prices.empty:
            figures['Cleared price'] = price_timeline(prices, revisions=price_revisions(history), colors=self.colors,
                                                      theme=self.theme)
        if not power.empty:
            meters = load_meter_readings(self.source)
            figures['Scheduled power'] = power_timeline(power, net=net_power(power),
                                                        meters=meters if not meters.empty else None,
                                                        colors=self.colors, theme=self.theme)
        return figures

    def curve_owners(self) -> list[str]:
        """Owners that actually have archived curves (for the dashboard's owner picker)."""
        curves = self._filter(load_curves(self.source))
        return sorted(curves['owner_name'].dropna().unique()) if not curves.empty else []

    def curves(self) -> dict[str, go.Figure]:
        curves = self._filter(load_curves(self.source, history=True))
        if curves.empty:
            return {}
        prices = cleared_prices(self.measurements(['MarginalPrice']))
        owners = sorted(curves['owner_name'].dropna().unique())
        owner = self.sel.owner if self.sel.owner in owners else owners[0]
        mine = curves[curves['owner_name'] == owner]
        starts = sorted(mine['interval_start'].unique())
        start = self.sel.interval_start if self.sel.interval_start in set(starts) else starts[0]
        figures = {}
        # An owner may hold several curves for one interval (active vertices of an asset; a neighbor's sent and
        # received signal vertices): one measurement each, drawn with the history of that curve id only.
        at = mine[mine['interval_start'] == start]
        for measurement_id in at.sort_values(['measurement_type', 'direction']).drop_duplicates('measurement_id')['measurement_id']:
            rows = mine[mine['measurement_id'] == measurement_id]
            latest = single_curve(rows[rows['interval_start'] == start]).sort_values(['price', 'quantity'])
            own = rows[rows['curve_id'] == latest['curve_id'].iloc[0]]
            kind = str(latest['measurement_type'].iloc[0])
            if pd.notna(latest['direction'].iloc[0]):
                kind += f" ({latest['direction'].iloc[0]})"
            figures[f'{owner} {kind} at {pd.Timestamp(start):%b %d %H:%M}'] = curve_figure(
                latest, history=own, marginal_price=cleared_price_for(prices, latest), name=owner,
                color=self.colors.get(owner), theme=self.theme, title=f'{kind}: {owner}')
            if rows['interval_start'].nunique() > 1:
                figures[f'{owner} {kind} across intervals'] = curve_multiples(
                    rows, owner, prices=prices, colors=self.colors, theme=self.theme, title=f'{owner}: {kind} by delivery interval')
        system = curves[curves['measurement_type'] == 'SystemVertex']
        if not system.empty:
            series = self.sel.series[0] if self.sel.series else system['market_series'].dropna().iloc[0]
            net, crossing = net_curve(curves, series, start, prices)
            if not net.empty:
                figures[f'System curve, {series}'] = curve_figure(net, marginal_price=crossing['price'] if crossing else None,
                                                                 balance=crossing, name=f'{series} system',
                                                                 color=self.colors.get(series), theme=self.theme)
        return figures

    def negotiation(self) -> dict[str, go.Figure]:
        history = self.measurements(['MarginalPrice', 'ScheduledPower', 'ConvergenceFlag'], history=True)
        if history.empty:
            return {}
        starts = sorted(history['interval_start'].unique())
        start = self.sel.interval_start if self.sel.interval_start in set(starts) else starts[0]
        changes = load_state_changes(self.source)
        market_rows = history[(history['interval_start'] == start) & history['market_id'].notna()]
        market_name = None
        if not market_rows.empty:
            mk = self.source.catalog.markets
            hit = mk[mk['id'] == market_rows['market_id'].iloc[0]]
            market_name = str(hit['name'].iloc[0]) if not hit.empty else None
        figures = {'Price negotiation': negotiation_trace(history, 'MarginalPrice', start, changes, market_name,
                                                          colors=self.colors, theme=self.theme, unit='$/kWh'),
                   'Power negotiation': negotiation_trace(history, 'ScheduledPower', start, changes, market_name,
                                                          colors=self.colors, theme=self.theme, unit='kW')}
        matrix = horizon_matrix(history, 'MarginalPrice')
        if not matrix.empty and matrix.shape[1] > 1:
            figures['Price horizon'] = horizon_heatmap(matrix, theme=self.theme, title='Price known over time', unit='$/kWh')
        conv = convergence(history)
        if not conv.empty:
            figures['Convergence'] = convergence_panel(conv, colors=self.colors, theme=self.theme)
        cons = consensus(self.measurements(['FullConsensus']))
        if not cons.empty:
            figures['Full consensus'] = consensus_strip(cons, theme=self.theme)
        return figures

    def markets(self) -> dict[str, go.Figure]:
        changes = load_state_changes(self.source)
        if self.sel.series:
            changes = changes[changes['market_series'].isin(self.sel.series)]
        segments = state_segments(changes)
        return {'Market states': market_timeline(segments, theme=self.theme)} if not segments.empty else {}

    def signals(self) -> dict[str, go.Figure]:
        signals = load_signals(self.source, history=True)
        if signals.empty:
            return {}
        neighbor = self.sel.neighbor or sorted(signals['neighbor_name'].unique())[0]
        starts = sorted(signals[signals['neighbor_name'] == neighbor]['interval_start'].unique())
        start = self.sel.interval_start if self.sel.interval_start in set(starts) else starts[0]
        pairs = signal_pairs(signals)
        row = pairs[(pairs['neighbor_name'] == neighbor) & (pairs['interval_start'] == start)]
        gap = row['max_quantity_gap'].iloc[0] if not row.empty else None
        fig = signal_comparison(signals, neighbor, start, theme=self.theme)
        if gap is not None and pd.notna(gap):
            fig.add_annotation(xref='paper', yref='paper', x=0, y=1, xanchor='left', yanchor='bottom', showarrow=False,
                               text=f'largest quantity gap {gap:,.1f} kW', font=dict(size=10, color=self.theme.ink['muted']))
        return {f'{neighbor}: sent vs received': fig}

    def bids(self) -> dict[str, go.Figure]:
        power = scheduled_power(self.measurements(['ScheduledPower']))
        curves = self._filter(load_curves(self.source))
        if power.empty or curves.empty:
            return {}
        positioned = bid_position(power, curves)
        if positioned['position'].notna().sum() == 0:
            return {}
        return {'Schedule position': bid_position_figure(positioned, theme=self.theme)}

    def costs(self) -> dict[str, go.Figure]:
        latest = self.measurements(['MarginalPrice', 'ScheduledPower'])
        cost = energy_cost(scheduled_power(latest), cleared_prices(latest))
        figures = {}
        for by in ('hour', 'weekday', 'month', 'day'):
            agg = aggregate_cost(cost, by)
            if not agg.empty and agg[by].nunique() > 1:
                figures[f'Cost by {by}'] = cost_bars(agg, by, colors=self.colors, theme=self.theme)
        return figures

    def devices(self, pattern: str = '%') -> dict[str, go.Figure]:
        topics = load_historian_topics(self.source, pattern)
        return {'Device points': device_panel(topics, theme=self.theme)} if not topics.empty else {}


def comparison(sources: list[Source], baseline: Source, selection: Selection) -> dict[str, go.Figure]:
    """Price and cost deltas of every other source against the baseline, aggregated by hour."""
    theme = selection.theme
    prices = pd.concat([cleared_prices(load_measurements(s, types=['MarginalPrice'], start=selection.start,
                                                         end=selection.end)) for s in sources], ignore_index=True)
    figures = {}
    if not prices.empty:
        figures['Cleared price by source'] = price_timeline(prices, facet='source', theme=theme)
        cmp = compare_runs(prices, baseline=baseline.label, value='price', keys=['market_series', 'interval_start'])
        if not cmp.empty:
            figures['Price change vs baseline'] = run_comparison(cmp, x='interval_start', unit='$/kWh', theme=theme)
    costs = []
    for s in sources:
        latest = load_measurements(s, types=['MarginalPrice', 'ScheduledPower'], start=selection.start, end=selection.end)
        costs.append(aggregate_cost(energy_cost(scheduled_power(latest), cleared_prices(latest)), 'hour'))
    cost = pd.concat(costs, ignore_index=True)
    if not cost.empty:
        total = cost.groupby(['source', 'hour'], as_index=False)['cost'].sum()
        figures['Cost by hour and source'] = cost_bars(total, 'hour', baseline=baseline.label, theme=theme)
        cmp = compare_runs(total, baseline=baseline.label, value='cost', keys=['hour'])
        if not cmp.empty:
            figures['Cost change vs baseline'] = run_comparison(cmp, x='hour', unit='$', theme=theme)
    return figures
