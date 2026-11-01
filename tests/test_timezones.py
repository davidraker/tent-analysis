"""Archive times are UTC; loaders convert them to the run's configured zone and figures label axes with it."""
import pandas as pd
import pytest

from tent_analysis.analysis import aggregate_cost, cleared_prices, energy_cost, horizon_matrix, scheduled_power
from tent_analysis.data import (Source, load_curves, load_measurements, load_meter_readings, load_signals,
                                load_state_changes)
from tent_analysis.viz import horizon_heatmap, power_timeline, price_timeline, time_axis, zone_name
from conftest import build_archive

PACIFIC = 'America/Los_Angeles'


def test_loaders_convert_to_the_runs_zone(source, pacific):
    assert source.catalog.tz == 'UTC' and pacific.catalog.tz == PACIFIC
    assert list(pacific.catalog.runs['tz']) == [PACIFIC]
    frames = {'measurements': load_measurements(pacific), 'curves': load_curves(pacific),
              'signals': load_signals(pacific), 'state_changes': load_state_changes(pacific),
              'meters': load_meter_readings(pacific)}
    zoned = {'measurements': ['interval_start', 'interval_end', 'record_time'], 'curves': ['interval_start', 'record_time'],
             'signals': ['signal_time', 'interval_start', 'record_time'], 'state_changes': ['record_time'],
             'meters': ['reading_time']}
    for name, columns in zoned.items():
        assert not frames[name].empty, name
        for column in columns:
            assert str(frames[name][column].dt.tz) == PACIFIC, (name, column)
    # Same instants as stored: the catalog keeps the archive's UTC, the loader only re-labels the zone.
    stored = set(pacific.catalog.intervals['start_time'])
    loaded = set(frames['measurements']['interval_start'].dt.tz_convert('UTC'))
    assert loaded <= stored and (frames['measurements']['interval_start'].dt.tz_convert('UTC').dt.tz == pd.Timestamp.utcnow().tz)
    for column in ('interval_start', 'record_time'):
        assert str(load_measurements(source)[column].dt.tz) == 'UTC'


def test_hour_bins_follow_the_zone(pacific):
    latest = load_measurements(pacific, types=['MarginalPrice', 'ScheduledPower'])
    cost = energy_cost(scheduled_power(latest), cleared_prices(latest))
    utc_cost = cost.assign(interval_start=cost['interval_start'].dt.tz_convert('UTC'))
    local = aggregate_cost(cost, 'hour').groupby('hour')['cost'].sum()
    utc = aggregate_cost(utc_cost, 'hour').groupby('hour')['cost'].sum()
    # July: Pacific daylight time is UTC-7, so the cost booked at UTC hour h lands in local hour h-7.
    shifted = pd.Series({(h - 7) % 24: c for h, c in utc.items()}).sort_index()
    pd.testing.assert_series_equal(local, shifted, check_names=False, check_index_type=False)
    assert not local.equals(utc), 'the costs differ by hour, so the shift is observable'
    # Day and month bins: the archive starts at 00:05 local on 1 July and runs just past midnight into 2 July.
    by_day = aggregate_cost(cost, 'day')
    assert sorted(by_day['day'].astype(str).unique()) == ['2017-07-01', '2017-07-02']
    assert by_day['cost'].sum() == pytest.approx(cost['cost'].sum())
    assert by_day.groupby(['owner_name', 'day']).size().eq(1).all(), 'one row per owner and day'
    by_month = aggregate_cost(cost, 'month')
    assert list(by_month['month'].astype(str).unique()) == ['July'] and str(by_month['month'].dtype) == 'category'
    assert by_month.groupby('owner_name')['cost'].sum().to_dict() == pytest.approx(cost.groupby('owner_name')['cost'].sum().to_dict())


def test_axes_are_labelled_with_the_zone(pacific, source):
    latest = load_measurements(pacific, types=['MarginalPrice', 'ScheduledPower'])
    prices, power = cleared_prices(latest), scheduled_power(latest)
    assert price_timeline(prices).layout.xaxis.title.text == f'Delivery interval start ({PACIFIC})'
    assert power_timeline(power).layout.xaxis.title.text == f'Delivery interval start ({PACIFIC})'
    matrix = horizon_matrix(load_measurements(pacific, types=['MarginalPrice'], history=True), 'MarginalPrice', bins=4)
    assert str(matrix.columns.tz) == PACIFIC and str(matrix.index.tz) == PACIFIC
    heat = horizon_heatmap(matrix)
    assert heat.layout.xaxis.title.text == f'Record time ({PACIFIC})'
    utc_prices = cleared_prices(load_measurements(source, types=['MarginalPrice']))
    assert price_timeline(utc_prices).layout.xaxis.title.text == 'Delivery interval start (UTC)'
    assert zone_name(prices['interval_start']) == PACIFIC and time_axis('T', matrix.index) == f'T ({PACIFIC})'
    with pytest.raises(ValueError, match='timezone-aware'):
        zone_name(prices['interval_start'].dt.tz_localize(None))


def test_zone_override_and_disagreeing_runs(pacific, tmp_path):
    override = Source(pacific.url, label='as_utc', tz='UTC')
    assert override.catalog.tz == 'UTC' and str(load_measurements(override)['interval_start'].dt.tz) == 'UTC'
    url = f'sqlite:///{tmp_path / "two_zones.sqlite"}'
    build_archive(url, actor='west', tz='America/Los_Angeles')
    build_archive(url, actor='east', tz='America/New_York')
    mixed = Source(url, label='mixed')
    assert sorted(mixed.catalog.runs['actor']) == ['east', 'west']
    assert mixed.catalog.run_labels == dict(zip(mixed.catalog.runs['run_id'], mixed.catalog.runs['actor']))
    assert mixed.catalog.run_label('no-such-run') == 'no-such-run'
    with pytest.raises(ValueError, match='different time zones'):
        load_measurements(mixed)
    chosen = Source(url, label='mixed', tz='America/New_York')
    frame = load_measurements(chosen, types=['MarginalPrice'])
    assert set(frame['actor']) == {'east', 'west'} and str(frame['interval_start'].dt.tz) == 'America/New_York'
