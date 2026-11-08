import pandas as pd
import pytest

from tent_analysis.data import load_measurements, load_curves, load_state_changes, load_meter_readings
from tent_analysis.data.loaders import MEASUREMENT_COLUMNS, CURVE_COLUMNS


def test_source_schema_and_catalog(source):
    assert source.label == 'campus_a'
    assert not source.schema.has_historian_topics and source.schema.has_meter_readings
    cat = source.catalog
    assert list(cat.runs['actor']) == ['campus-A']
    assert set(cat.components['type']) >= {'market_series', 'market', 'neighbor'}
    assert not cat.markets.empty and set(cat.markets['series_name']) == set(cat.market_series['name'])
    assert (cat.intervals['end_time'] > cat.intervals['start_time']).all()
    m = cat.measurements
    assert {'MarginalPrice', 'ScheduledPower', 'ConvergenceFlag'} <= set(m['measurement_type'])
    assert m['owner_type'].notna().all()


def test_load_measurements_latest_and_history(source):
    latest = load_measurements(source, types=['MarginalPrice'])
    assert list(latest.columns) == MEASUREMENT_COLUMNS
    assert (latest['source'] == 'campus_a').all() and (latest['actor'] == 'campus-A').all()
    assert latest['market_series'].notna().all() and latest['interval_end'].gt(latest['interval_start']).all()
    # one latest row per (measurement, interval); history has three versions (default, round 1, cleared)
    assert not latest.duplicated(['measurement_id', 'interval_id']).any()
    history = load_measurements(source, types=['MarginalPrice'], history=True)
    per_interval = history.groupby(['measurement_id', 'interval_id']).size()
    assert per_interval.min() >= 2 and per_interval.max() == 3
    cleared = latest.set_index(['measurement_id', 'interval_id'])['value']
    last_in_history = history.sort_values('record_time').groupby(['measurement_id', 'interval_id'])['value'].last()
    pd.testing.assert_series_equal(cleared.sort_index(), last_in_history.sort_index(), check_names=False)
    assert load_measurements(source, types=['NoSuchType']).empty
    only = load_measurements(source, owners=['SolarPv'])
    assert set(only['owner_name']) == {'SolarPv'} and 'ScheduledPower' in set(only['measurement_type'])
    assert load_measurements(source, owners=['nobody']).empty
    # Bounds select on the interval start, half-open, and must be zoned (the archive compares UTC wall-clock values).
    starts = sorted(latest['interval_start'].unique())
    window = load_measurements(source, types=['MarginalPrice'], start=starts[1], end=starts[3])
    assert set(window['interval_start']) == set(starts[1:3])
    pacific_bounds = load_measurements(source, types=['MarginalPrice'], start=starts[1].tz_convert('America/Los_Angeles'),
                                       end=starts[3].tz_convert('America/Los_Angeles'))
    assert set(pacific_bounds['interval_start']) == set(starts[1:3]), 'the same instants, whatever zone they are given in'
    with pytest.raises(ValueError, match='start must be timezone-aware'):
        load_measurements(source, start=starts[1].tz_localize(None))


def test_load_curves(source):
    latest = load_curves(source)
    assert list(latest.columns) == CURVE_COLUMNS
    asset_curves = latest[latest['owner_type'].str.contains('asset|predictor|resource', regex=True)]
    assert not asset_curves.empty
    one = asset_curves[asset_curves['curve_id'] == asset_curves['curve_id'].iloc[0]]
    # SDCurve.update merges vertices, so the latest version holds all four, ordered by record.
    assert list(one['record']) == [0, 1, 2, 3] and (one['quantity'] < 0).all()
    history = load_curves(source, history=True)
    versions = history.groupby(['curve_id', 'record_time']).size()
    assert len(versions) == len(latest.groupby(['curve_id', 'record_time'])) + 1
    assert sorted(versions[versions.index.get_level_values(0) == one['curve_id'].iloc[0]]) == [2, 4]
    # Signal vertices carry their direction; every other curve has none.
    signals = latest[latest['measurement_type'] == 'SignalVertex']
    assert set(signals['direction']) == {'sent', 'received'}
    # Four intervals each way for the auction market and four more for the consensus market, whose acknowledgement
    # repeated the sent curves and so has no curves of its own archived (see load_signal_headers).
    assert signals.groupby(['market_series', 'direction'])['curve_id'].nunique().to_dict() == {
        ('Consensus_Market', 'received'): 4, ('Consensus_Market', 'sent'): 4, ('Day-Ahead_Auction', 'received'): 4,
        ('Day-Ahead_Auction', 'sent'): 4}
    assert latest.loc[latest['measurement_type'] != 'SignalVertex', 'direction'].isna().all()


def test_load_curves_filters(source):
    everything = load_curves(source)
    system = load_curves(source, types=['SystemVertex'])
    assert not system.empty and set(system['measurement_type']) == {'SystemVertex'}
    owner = everything[everything['measurement_type'] == 'ActiveVertex']['owner_name'].iloc[0]
    mine = load_curves(source, owners=[owner])
    assert set(mine['owner_name']) == {owner} and len(mine) < len(everything)
    # An unmatched filter is empty, not everything with null owners; an empty type list matches nothing.
    for unmatched in (dict(types=['NoSuchVertex']), dict(owners=['nobody']), dict(types=[]), dict(owners=[])):
        frame = load_curves(source, **unmatched)
        assert frame.empty and list(frame.columns) == CURVE_COLUMNS, unmatched
    assert (load_curves(source, types=['ActiveVertex'], owners=['nobody'])).empty


def test_state_changes_and_meters(source):
    sc = load_state_changes(source)
    assert {'Initial', 'Inactive'} <= set(sc['state']) and sc['market_series'].notna().all()
    meters = load_meter_readings(source)
    assert len(meters) == 6 and meters['meter_name'].nunique() == 1 and meters['value'].iloc[-1] == 105.0
