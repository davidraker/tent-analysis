import pandas as pd
import plotly.graph_objects as go
import pytest

from tent_analysis.analysis import (bid_position, cleared_prices, consensus, convergence, curve_at, horizon_matrix,
                                    net_curve, quantity_at_price, scheduled_power, signal_pairs, state_segments)
from tent_analysis.app.views import Selection, Views, comparison
from tent_analysis.data import load_curves, load_measurements, load_signals, load_state_changes
from tent_analysis.report import standard_figures
from tent_analysis.viz import (bid_position_figure, consensus_strip, convergence_panel, curve_figure, curve_multiples,
                               horizon_heatmap, market_timeline, negotiation_trace, signal_comparison)


def test_signals_loader_and_pairs(source):
    sig = load_signals(source, history=True)
    assert set(sig['direction']) == {'sent', 'received'} and sig['neighbor_name'].nunique() == 1
    assert sig['market_series'].notna().all() and sig['signal_time'].notna().all()
    pairs = signal_pairs(sig, thresholds={sig['neighbor_name'].iloc[0]: 2.0})
    assert len(pairs) == 4 and (pairs['sent_vertices'] == 2).all()
    assert pairs['max_quantity_gap'].round(3).eq(6.0).all(), 'received curve is 5..6 kW off the sent one'
    assert pairs['exceeds_threshold'].all()


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
    assert matrix.shape[0] == history['interval_start'].nunique() and matrix.shape[1] >= 1
    assert matrix.notna().all(axis=None) or matrix.ffill(axis=1).iloc[:, -1].notna().all()


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
    focused = Views(source, Selection(interval_start=views.intervals()[2]))
    assert any('curve' in k.lower() or 'at' in k for k in focused.curves())
    figures = standard_figures(source)
    assert len(figures) >= 12
    cmp = comparison([source, variant], baseline=source, selection=Selection())
    assert {'Cleared price by source', 'Price change vs baseline', 'Cost change vs baseline'} <= set(cmp)
