import pandas as pd
import plotly.graph_objects as go
import pytest

from tent_analysis.analysis import (bid_position, cleared_prices, consensus, convergence, curve_at, horizon_matrix,
                                    net_curve, quantity_at_price, scheduled_power, signal_pairs, single_curve,
                                    state_segments)
from tent_analysis.app.views import Selection, Views, comparison, date_bounds, snap_interval
from tent_analysis.data import load_curves, load_measurements, load_signals, load_state_changes
from tent_analysis.report import standard_figures
from tent_analysis.viz import (bid_position_figure, consensus_strip, convergence_panel, curve_figure, curve_multiples,
                               horizon_heatmap, market_timeline, negotiation_trace, signal_comparison)


def test_signals_loader_and_pairs(source):
    sig = load_signals(source, history=True)
    assert set(sig['direction']) == {'sent', 'received'} and sig['neighbor_name'].nunique() == 1
    assert sig['market_series'].notna().all() and sig['signal_time'].notna().all()
    # Stored as archived: the received signal carries the remote side's sign, opposite to the sent one.
    sent, received = (sig[sig['direction'] == d].sort_values('price') for d in ('sent', 'received'))
    assert list(sent['quantity'].unique()) == [-50.0, 30.0] and list(received['quantity'].unique()) == [45.0, -36.0]
    pairs = signal_pairs(sig, thresholds={sig['neighbor_name'].iloc[0]: 2.0})
    assert len(pairs) == 4 and (pairs['sent_vertices'] == 2).all()
    assert pairs['max_quantity_gap'].round(3).eq(6.0).all(), \
        'compared from the local side: |-50 - (-45)| = 5 and |30 - 36| = 6; without the flip the gap would be 95'
    assert pairs['exceeds_threshold'].all()
    assert signal_pairs(sig, thresholds={sig['neighbor_name'].iloc[0]: 10.0})['exceeds_threshold'].eq(False).all()


def test_signal_comparison_draws_received_from_local_side(source):
    sig = load_signals(source, history=True)
    neighbor, start = sig['neighbor_name'].iloc[0], sorted(sig['interval_start'].unique())[0]
    fig = signal_comparison(sig, neighbor, start)
    traces = {t.name: t for t in fig.data}
    assert set(traces) == {'Sent', 'Received'}
    assert list(traces['Sent'].y) == [-50.0, 30.0]
    assert list(traces['Received'].y) == [-45.0, 36.0], 'received quantities negated so both curves share a sign'
    assert traces['Received'].line.dash == 'dash' and traces['Sent'].line.dash == 'solid'
    assert any('negated' in (a.text or '') for a in fig.layout.annotations)


def test_curve_helpers(source):
    curves = load_curves(source, history=True)
    system = curves[curves['measurement_type'] == 'SystemVertex']
    assert not system.empty
    series, start = system['market_series'].iloc[0], sorted(system['interval_start'].unique())[0]
    prices = cleared_prices(load_measurements(source, types=['MarginalPrice']))
    net, crossing = net_curve(curves, series, start, prices)
    assert list(net['price']) == [0.02, 0.06, 0.10] and crossing is not None
    assert -80.0 <= crossing['quantity'] <= 90.0
    assert quantity_at_price(net, 0.04) == pytest.approx(-40.0) and quantity_at_price(net, 0.5) == 90.0
    owner = curves[curves['measurement_type'] == 'ActiveVertex']['owner_name'].iloc[0]
    own_start = curves[curves['owner_name'] == owner]['interval_start'].iloc[0]
    latest = curve_at(curves, owner, own_start)
    assert len(latest) == 4 and latest['price'].is_monotonic_increasing
    assert curve_at(curves, owner, own_start, measurement_type='ActiveVertex').equals(latest)
    assert curve_at(curves, owner, own_start, measurement_type='SystemVertex').empty
    earlier = curve_at(curves, owner, own_start, when=curves['record_time'].min())
    assert len(earlier) == 2 and earlier['record_time'].nunique() == 1, 'as-of the first record: the first version only'
    with pytest.raises(ValueError, match='timezone-aware'):
        curve_at(curves, owner, own_start, when=curves['record_time'].min().tz_localize(None))


def test_distinct_curves_are_not_merged(source):
    """A neighbor archives its sent and received signal vertices as two curves per interval (same owner, same type)."""
    curves = load_curves(source, history=True)
    neighbor = curves[curves['direction'].notna()]['owner_name'].iloc[0]
    start = sorted(curves[curves['owner_name'] == neighbor]['interval_start'].unique())[0]
    both = curves[(curves['owner_name'] == neighbor) & (curves['interval_start'] == start)]
    assert both['curve_id'].nunique() == 2 and both['measurement_type'].nunique() == 1
    with pytest.raises(ValueError, match=r'2 distinct curves \(SignalVertex \(received\), SignalVertex \(sent\)\)'):
        curve_at(curves, neighbor, start)
    with pytest.raises(ValueError, match='distinct curves'):
        curve_multiples(both, neighbor)
    sent = single_curve(both[both['direction'] == 'sent'])
    assert sent['curve_id'].nunique() == 1 and list(sent.sort_values('price')['quantity']) == [-50.0, 30.0]
    # The dashboard draws each as its own figure, with its own history.
    figures = Views(source, Selection(owner=neighbor, interval_start=start)).curves()
    keys = [k for k in figures if k.startswith(neighbor)]
    assert any('(sent) at' in k for k in keys) and any('(received) at' in k for k in keys)
    for key in keys:
        if ' at ' in key:
            assert len(figures[key].data) == 1, 'one version, so no faded revisions and no merged second curve'
            assert len(figures[key].data[0].y) == 2


def test_bid_position_and_consensus(source):
    latest = load_measurements(source, types=['ScheduledPower', 'FullConsensus'])
    power = scheduled_power(latest)
    positioned = bid_position(power, load_curves(source))
    assert 'position' in positioned and positioned['position'].notna().any()
    assert set(positioned['position'].dropna().astype(str)) <= {'below', 'lower', 'midpoint', 'upper', 'above'}
    cons = consensus(latest)
    assert not cons.empty and cons['consensus'].any() and (~cons['consensus']).any()


def test_state_segments_and_horizon(source):
    seg = state_segments(load_state_changes(source))
    assert (seg['end'] >= seg['start']).all() and set(seg['state']) >= {'Initial'}
    history = load_measurements(source, types=['MarginalPrice'], history=True)
    matrix = horizon_matrix(history, 'MarginalPrice', bins=6)
    assert matrix.shape == (history['interval_start'].nunique(), 6)
    assert matrix.iloc[:, -1].notna().all(), 'by the last record bin every interval has a known price'
    assert matrix.columns.is_monotonic_increasing and matrix.index.is_monotonic_increasing
    # Known values only ever accumulate along a row (forward fill): once known, never unknown again.
    known = matrix.notna().to_numpy()
    assert (known[:, 1:] >= known[:, :-1]).all()
    last = history.sort_values('record_time').groupby('interval_start')['value'].last()
    pd.testing.assert_series_equal(matrix.iloc[:, -1].sort_index(), last.sort_index(), check_names=False, check_index=False)


def test_phase2_figures(source):
    curves = load_curves(source, history=True)
    history = load_measurements(source, types=['MarginalPrice', 'ScheduledPower', 'ConvergenceFlag'], history=True)
    owner = curves[curves['measurement_type'] == 'ActiveVertex']['owner_name'].iloc[0]
    start = curves[curves['owner_name'] == owner]['interval_start'].iloc[0]
    own = curves[(curves['owner_name'] == owner) & (curves['interval_start'] == start)]
    fig = curve_figure(curve_at(curves, owner, start), history=own, marginal_price=0.05, name=owner)
    assert isinstance(fig, go.Figure) and len(fig.data) == 2, 'latest plus one faded earlier version'
    assert fig.data[-1].marker.symbol[0] in ('circle', 'circle-open')
    multi = curve_multiples(curves, curves[curves['measurement_type'] == 'SystemVertex']['owner_name'].iloc[0])
    assert len(multi.layout.annotations) >= 6
    trace = negotiation_trace(history, 'MarginalPrice', start, load_state_changes(source), None, unit='$/kWh')
    assert len(trace.data) >= 1
    heat = horizon_heatmap(horizon_matrix(history, 'MarginalPrice', bins=6), unit='$/kWh')
    assert isinstance(heat.data[0], go.Heatmap)
    conv = convergence_panel(convergence(history))
    assert any(isinstance(t, go.Bar) for t in conv.data) and any(t.name == 'converged' for t in conv.data)
    tl = market_timeline(state_segments(load_state_changes(source)))
    assert all(isinstance(t, go.Bar) and t.orientation == 'h' for t in tl.data)
    sig = load_signals(source, history=True)
    comp = signal_comparison(sig, sig['neighbor_name'].iloc[0], sig['interval_start'].iloc[0])
    assert {t.name for t in comp.data} == {'Sent', 'Received'}
    cons = consensus_strip(consensus(load_measurements(source, types=['FullConsensus'])))
    assert {t.name for t in cons.data} == {'consensus', 'no consensus'}
    positioned = bid_position(scheduled_power(load_measurements(source, types=['ScheduledPower'])), load_curves(source))
    assert len(bid_position_figure(positioned[positioned['position'].notna()]).data) >= 2


def test_views_and_report(source, variant):
    views = Views(source, Selection())
    pages = {page: getattr(views, page)() for page in ('overview', 'curves', 'negotiation', 'markets', 'signals',
                                                        'bids', 'costs', 'devices')}
    assert pages['devices'] == {}, 'no historian tables in this archive'
    assert all(pages[p] for p in ('overview', 'curves', 'negotiation', 'markets', 'signals', 'bids', 'costs'))
    system = load_curves(source, types=['SystemVertex'])
    market, third = system['owner_name'].iloc[0], sorted(system['interval_start'].unique())[2]
    focused = Views(source, Selection(owner=market, interval_start=third)).curves()
    assert any(k.endswith(f'at {third:%b %d %H:%M}') for k in focused), 'the focused interval is the one drawn'
    assert any(k.startswith('System curve') for k in focused)
    figures = standard_figures(source)
    assert len(figures) >= 12
    cmp = comparison([source, variant], baseline=source, selection=Selection())
    assert {'Cleared price by source', 'Price change vs baseline', 'Cost change vs baseline'} <= set(cmp)


def test_owner_filter_keeps_market_owned_prices(source):
    everything = Views(source, Selection())
    asset = sorted(source.catalog.participants['name'])[0]
    only_asset = Views(source, Selection(owners=[asset]))
    assert only_asset.intervals() == everything.intervals(), 'prices are owned by the market, not by any owner'
    power = only_asset.measurements(['ScheduledPower'])
    assert set(power['owner_name']) == {asset}
    assert set(only_asset.measurements(['MarginalPrice'])['owner_name']) == set(everything.measurements(['MarginalPrice'])['owner_name'])
    assert only_asset.costs(), 'costs need prices and the owner\'s power; before the fix the owner filter emptied them'
    power_traces = {t.name for t in only_asset.overview()['Scheduled power'].data}
    assert asset in power_traces and not (power_traces & (set(source.catalog.participants['name']) - {asset}))
    assert 'market' not in set(source.catalog.participants['type']) and set(source.catalog.participants['type']) >= {'neighbor', 'solar_pv_resource'}


def test_snap_interval(source):
    intervals = Views(source, Selection()).intervals()
    first, second = intervals[0], intervals[1]
    assert first.tz is not None
    # Plotly hands back the axis value as wall-clock text without the zone.
    assert snap_interval(first.strftime('%Y-%m-%d %H:%M'), intervals) == first
    assert snap_interval(first.strftime('%Y-%m-%d %H:%M:%S.%f'), intervals) == first
    # A click between two intervals snaps to the nearer one; an aware value is converted, not re-localised.
    nearer_second = second - (second - first) / 4
    assert snap_interval(nearer_second.strftime('%Y-%m-%d %H:%M:%S'), intervals) == second
    assert snap_interval(nearer_second.tz_convert('Asia/Tokyo').isoformat(), intervals) == second
    assert snap_interval(None, intervals) is None and snap_interval('2017-07-01', []) is None


def test_date_bounds_and_range_selection(source):
    from datetime import date
    start, end = date_bounds((date(2017, 7, 1), date(2017, 7, 1)), 'UTC')
    assert start == pd.Timestamp('2017-07-01T00:00Z') and end == pd.Timestamp('2017-07-02T00:00Z')
    start, end = date_bounds((date(2017, 7, 1),), 'America/Los_Angeles')
    assert str(start.tz) == 'America/Los_Angeles' and start.hour == 0 and end is None
    assert date_bounds((), 'UTC') == (None, None) and date_bounds(date(2017, 7, 2), 'UTC')[0] == pd.Timestamp('2017-07-02T00:00Z')
    everything = Views(source, Selection()).intervals()
    start, end = date_bounds((date(2017, 7, 1), date(2017, 7, 1)), 'UTC')
    first_day = Views(source, Selection(start=start, end=end))
    chosen = first_day.intervals()
    assert chosen and len(chosen) < len(everything) and all(t.date() == date(2017, 7, 1) for t in chosen)
    assert first_day.overview() and first_day.costs()


def test_signal_neighbor_selection(source):
    views = Views(source, Selection())
    names = views.signal_neighbors()
    assert names == ['CoR']
    assert list(views.signals()) == ['CoR: sent vs received']
    assert Views(source, Selection(neighbor='nobody')).signals().keys() == {'CoR: sent vs received'}, 'unknown neighbor: first known'
    late = Views(source, Selection(start=pd.Timestamp('2030-01-01T00:00Z')))
    assert late.signals() == {}, 'no signals in the selected date range'
