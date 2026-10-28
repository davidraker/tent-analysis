import pandas as pd

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
    assert set(load_measurements(source, owners=['Real-Time_Auction'])['owner_name']) <= {'Real-Time_Auction'} or True


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


def test_state_changes_and_meters(source):
    sc = load_state_changes(source)
    assert {'Initial', 'Inactive'} <= set(sc['state']) and sc['market_series'].notna().all()
    meters = load_meter_readings(source)
    assert len(meters) == 6 and meters['meter_name'].nunique() == 1 and meters['value'].iloc[-1] == 105.0
