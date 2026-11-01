import pandas as pd
import pytest

from tent_analysis.analysis import (cleared_prices, price_revisions, scheduled_power, net_power, energy_cost,
                                    aggregate_cost, convergence, compare_runs)
from tent_analysis.data import load_measurements


@pytest.fixture(scope='module')
def frames(source):
    latest = load_measurements(source, types=['MarginalPrice', 'ScheduledPower'])
    history = load_measurements(source, types=['MarginalPrice', 'ScheduledPower', 'ConvergenceFlag'], history=True)
    return latest, history


def test_cleared_prices_and_revisions(frames):
    latest, history = frames
    prices = cleared_prices(latest)
    assert not prices.duplicated(['source', 'run_id', 'market_series', 'interval_start']).any()
    assert prices['price'].between(0.03, 0.1).all()
    rev = price_revisions(history)
    # Rolling markets of one series re-cover intervals, so revisions accumulate across markets: multiples of three.
    assert (rev['revisions'] >= 3).all() and (rev['revisions'] % 3 == 0).all()
    assert (rev['max'] >= rev['last']).all() and (rev['min'] < rev['max']).all()


def test_power_cost_and_aggregation(frames):
    latest, history = frames
    power = scheduled_power(latest)
    assert set(power['owner_name']) and (power.groupby(['owner_name', 'market_series', 'interval_start']).size() == 1).all()
    net = net_power(power)
    assert len(net) == power.groupby(['market_series', 'interval_start']).ngroups
    prices = cleared_prices(latest)
    cost = energy_cost(power, prices)
    one = cost.iloc[0]
    hours = (one['interval_start'] - one['interval_start']).total_seconds()  # placeholder to keep pandas happy
    assert one['cost'] == pytest.approx(-one['energy_kwh'] * one['price'])
    by_hour = aggregate_cost(cost, 'hour')
    assert set(by_hour['hour']) <= set(range(24)) and by_hour['cost'].sum() == pytest.approx(cost['cost'].sum())
    by_weekday = aggregate_cost(cost, 'weekday')
    assert str(by_weekday['weekday'].dtype) == 'category'
    with pytest.raises(ValueError):
        aggregate_cost(cost, 'fortnight')


def test_convergence(frames):
    latest, history = frames
    conv = convergence(history)
    assert (conv['revisions'] >= 2).all(), 'two fixture versions per market, plus any the state machine scheduled'
    neighbors = conv[conv['converged'].notna()]
    # The fixture leaves exactly one interval unconverged for the transactive neighbors.
    assert not neighbors.empty and neighbors['converged'].astype(bool).mean() > 0.9
    assert (~neighbors['converged'].astype(bool)).any()


def test_compare_runs(source, variant):
    a = cleared_prices(load_measurements(source, types=['MarginalPrice']))
    b = cleared_prices(load_measurements(variant, types=['MarginalPrice']))
    both = pd.concat([a, b], ignore_index=True)
    cmp = compare_runs(both, baseline='campus_a', value='price', keys=['market_series', 'interval_start'])
    assert set(cmp['source']) == {'campus_b'} and (cmp['baseline'] == 'campus_a').all()
    assert (cmp['delta'] > 0).all(), 'the variant archive was generated with prices 20% higher'
    assert cmp['delta_pct'].round(0).eq(20.0).all()


def test_latest_row_is_taken_whole():
    """The newest version wins as a row: a null in it is not back-filled from an older version."""
    t = pd.to_datetime(['2017-07-01T00:00Z', '2017-07-01T00:01Z'])
    frame = pd.DataFrame({'source': 'a', 'actor': 'a', 'run_id': 'r', 'market_series': 's', 'measurement_type': 'MarginalPrice',
                          'interval_start': pd.Timestamp('2017-07-01T01:00Z'), 'interval_end': [pd.Timestamp('2017-07-01T02:00Z'), pd.NaT],
                          'record_time': t, 'value': [0.05, 0.07]})
    out = cleared_prices(frame)
    assert len(out) == 1 and out['price'].iloc[0] == 0.07 and pd.isna(out['interval_end'].iloc[0])
    assert out['record_time'].iloc[0] == t[1]


def test_compare_runs_zero_baseline():
    base = pd.DataFrame({'source': 'base', 'hour': [1, 2], 'cost': [0.0, 10.0]})
    variant = pd.DataFrame({'source': 'v', 'hour': [1, 2], 'cost': [5.0, 15.0]})
    cmp = compare_runs(pd.concat([base, variant]), baseline='base', value='cost', keys=['hour'])
    assert list(cmp['delta']) == [5.0, 5.0]
    assert pd.isna(cmp['delta_pct'].iloc[0]) and cmp['delta_pct'].iloc[1] == pytest.approx(50.0)
    assert str(cmp['delta_pct'].dtype).startswith('float'), 'no object dtype from the NA replacement'
    assert compare_runs(variant, baseline='missing', value='cost', keys=['hour']).empty
