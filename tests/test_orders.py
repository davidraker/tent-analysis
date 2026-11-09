"""The order book of a MatchingEngine: loader, metrics, figures, and the views that exist only when orders exist."""
import numpy as np
import pandas as pd
import pytest

from tent_analysis.analysis import STATUS_ORDER, aggregate_cost, fill_cost, matched_quantities, order_book, order_counts
from tent_analysis.analysis.orders import FILL_COST_COLUMNS
from tent_analysis.app.dashboard import pages_for
from tent_analysis.app.views import Selection, Views
from tent_analysis.data import load_curves, load_orders
from tent_analysis.data.loaders import ORDER_COLUMNS
from tent_analysis.report import standard_figures
from tent_analysis.report.cli import main
from tent_analysis.viz import device_panel


def test_load_orders(source, pacific):
    assert source.schema.has_orders and source.catalog.order_count == 216
    latest, history = load_orders(source), load_orders(source, history=True)
    assert list(latest.columns) == ORDER_COLUMNS and len(history) > len(latest)
    assert latest['order_id'].nunique() == 216 and not latest.duplicated(['order_id', 'record_time', 'record']).any()
    assert latest.groupby('order_id')['record_time'].nunique().eq(1).all()
    assert history.groupby('order_id')['record_time'].nunique().max() == 3, 'submitted, standing, superseded'
    assert set(history['status']) == {'NEW', 'STANDING_ORDER', 'REVISED_STANDING_ORDER', 'MATCHED', 'SUPERSEDED'}
    assert set(history['side']) == {'SUPPLY', 'DEMAND'} and set(history['market_series']) == {'Matching_Market'}
    assert (history.loc[history['side'] == 'SUPPLY', 'quantity'] >= 0).all()
    assert (history.loc[history['side'] == 'DEMAND', 'quantity'] <= 0).all()
    for column in ('record_time', 'time_stamp', 'expire_by', 'interval_start'):
        assert str(history[column].dt.tz) == 'UTC'
    assert (history['expire_by'] > history['time_stamp']).all() and history['actor_name'].notna().all()
    first = history.sort_values('record_time').drop_duplicates('order_id')
    # Quotes enter the archive when submitted (NEW); matched legs and remainders are born by matching, in their state.
    assert first['status'].value_counts().to_dict() == {'MATCHED': 96, 'NEW': 72, 'REVISED_STANDING_ORDER': 48}
    # Orders are curves too, but load_curves leaves them to load_orders.
    assert 'Order' not in set(load_curves(source)['measurement_type'])
    assert set(load_curves(source, types=['Order'])['measurement_type']) == {'Order'}
    assert not pacific.schema.has_orders and pacific.catalog.order_count == 0 and load_orders(pacific).empty


def test_order_book_and_matches(source):
    history = load_orders(source, history=True)
    book = order_book(history)
    assert not book.duplicated(['order_id', 'record_time']).any() and len(book) == len(history.drop_duplicates(['order_id', 'record_time']))
    assert (book['vertices'] >= 1).all()
    latest = book.sort_values('record_time').drop_duplicates('order_id', keep='last')
    assert latest['status'].value_counts().to_dict() == {'MATCHED': 96, 'SUPERSEDED': 96, 'REVISED_STANDING_ORDER': 24}
    matched = latest[latest['status'] == 'MATCHED']
    assert matched['buyer_name'].notna().all() and matched['seller_name'].notna().all()
    assert set(matched['parent_order_id']) <= set(latest.loc[latest['status'] == 'SUPERSEDED', 'order_id']), \
        'a matched leg descends from a superseded order'
    per_interval = matched.groupby(['interval_start', 'side'])['quantity'].sum().unstack('side')
    assert np.allclose(per_interval['SUPPLY'], -per_interval['DEMAND']), 'legs balance within every interval'
    counts = order_counts(book)
    assert str(counts['status'].dtype) == 'category' and list(counts['status'].cat.categories) == STATUS_ORDER
    assert counts['orders'].sum() == 216 and counts['interval_start'].nunique() == 24
    assert counts.groupby('interval_start')['orders'].sum().eq(9).all(), 'four matched legs, four superseded, one standing'
    matches = matched_quantities(book)
    assert len(matches) == 24 and (matches['legs'] == 2).all() and (matches['matched_kw'] == 120.0).all()
    assert matches['match_price'].between(0.03, 0.1).all() and matches['match_price'].nunique() == 1
    assert (matches['standing_supply_kw'] > 0).all() and (matches['standing_demand_kw'] == 0).all()
    assert matched_quantities(book[book['status'] == 'NEW'])['matched_kw'].eq(0).all()
    assert order_book(history.iloc[0:0]).empty and order_counts(book.iloc[0:0]).empty and matched_quantities(book.iloc[0:0]).empty


def test_fill_cost_settles_each_leg_at_its_own_price(source):
    """The money of a book market is its fills: every matched leg at its match price, per actor and interval, with the
    sign convention of energy_cost (a supply leg earns, a negative cost; a demand leg pays)."""
    history = load_orders(source, history=True)
    assert (history['interval_end'] - history['interval_start'] == pd.Timedelta(hours=1)).all()
    cost = fill_cost(history)
    assert list(cost.columns) == FILL_COST_COLUMNS and cost['interval_start'].nunique() == 24
    assert cost['legs'].sum() == 96 and (cost['unpriced_legs'] == 0).all() and set(cost['market_series']) == {'Matching_Market'}
    latest = order_book(history).sort_values('record_time').drop_duplicates('order_id', keep='last')
    legs = latest[latest['status'] == 'MATCHED']
    # Leg by leg: -quantity [kW] x 1 h x match price; the same from the latest-version frame as from the history.
    expected = (legs.assign(cost=-legs['quantity'] * legs['price']).groupby(['actor_name', 'interval_start'])['cost'].sum())
    got = cost.set_index(['owner_name', 'interval_start'])['cost']
    assert np.allclose(got.sort_index(), expected.sort_index())
    assert np.allclose(fill_cost(load_orders(source))['cost'], cost['cost'])
    sellers = set(legs.loc[legs['side'] == 'SUPPLY', 'actor_name'])
    assert (cost.loc[cost['owner_name'].isin(sellers), 'cost'] < 0).all(), 'supply legs earn'
    assert (cost.loc[~cost['owner_name'].isin(sellers), 'cost'] > 0).all(), 'demand legs pay'
    assert np.allclose(cost.groupby('interval_start')['cost'].sum(), 0.0), 'every leg at one price: buyers pay what sellers earn'
    assert np.allclose(cost.groupby('interval_start')['energy_kwh'].sum(), 0.0)
    assert (cost.groupby('interval_start')['energy_kwh'].apply(lambda s: s.abs().sum()) == 240.0).all(), '120 kW matched each way'
    by_hour = aggregate_cost(cost, 'hour')
    assert set(by_hour['owner_name']) == set(cost['owner_name']) and by_hour['hour'].nunique() == 24
    assert fill_cost(history.iloc[0:0]).empty and list(fill_cost(history.iloc[0:0]).columns) == FILL_COST_COLUMNS
    assert fill_cost(history[history['status'] == 'NEW']).empty, 'no fills, no money'


def test_fill_cost_leaves_unpriced_legs_out_of_the_money():
    t = pd.Timestamp('2017-07-01T00:00Z')
    legs = pd.DataFrame({'source': 's', 'actor': 'a', 'run_id': 'r', 'order_id': ['1', '2', '3', '4'], 'record_time': t,
                         'status': ['MATCHED', 'MATCHED', 'REVISED_MATCHED', 'SUPERSEDED'], 'side': ['SUPPLY', 'DEMAND', 'SUPPLY', 'SUPPLY'],
                         'actor_name': ['gen', 'load', 'gen', 'gen'], 'buyer_name': 'b', 'seller_name': 's', 'parent_order_id': None,
                         'market_series': 'M', 'market_name': 'M1', 'interval_start': t, 'interval_end': t + pd.Timedelta(minutes=30),
                         'record': 0, 'price': [np.inf, np.inf, 0.05, 0.05], 'quantity': [10.0, -10.0, 30.0, 100.0], 'continuity': True})
    out = fill_cost(legs).set_index('owner_name')
    assert list(out.index) == ['gen', 'load'] and out.loc['gen', 'legs'] == 2 and out.loc['gen', 'unpriced_legs'] == 1
    assert out.loc['gen', 'energy_kwh'] == 20.0 and out.loc['gen', 'cost'] == pytest.approx(-15.0 * 0.05), 'the superseded order is not a fill'
    assert out.loc['load', 'energy_kwh'] == -5.0 and out.loc['load', 'cost'] == 0.0 and out.loc['load', 'unpriced_legs'] == 1


def test_infinite_match_prices_are_not_averaged():
    t = pd.Timestamp('2017-07-01T00:00Z')
    legs = pd.DataFrame({'source': 's', 'actor': 'a', 'run_id': 'r', 'order_id': ['1', '2', '3'], 'record_time': t,
                         'status': 'MATCHED', 'side': ['SUPPLY', 'DEMAND', 'SUPPLY'], 'actor_name': 'x', 'buyer_name': 'b',
                         'seller_name': 's', 'parent_order_id': None, 'market_series': 'M', 'market_name': 'M1',
                         'interval_start': t, 'price': [np.inf, np.inf, 0.05], 'quantity': [10.0, -10.0, 30.0], 'vertices': 1})
    out = matched_quantities(legs)
    assert out['matched_kw'].iloc[0] == 40.0 and out['legs'].iloc[0] == 2
    assert out['match_price'].iloc[0] == 0.05, 'only the finite price counts'
    assert np.isnan(matched_quantities(legs[legs['price'] == np.inf])['match_price'].iloc[0])


def test_order_views_exist_only_with_orders(source, pacific, historian):
    figures = Views(source, Selection()).orders()
    assert set(figures) == {'Orders by status', 'Matched quantity'}
    costs = Views(source, Selection()).costs()
    assert 'Fill cost by hour' in costs and 'Cost by hour' in costs, 'the fills of the book market next to price x power'
    assert costs['Fill cost by hour'].layout.title.text.startswith('Cost of book-market fills by hour')
    assert 'Fill cost by hour' not in Views(pacific, Selection()).costs(), 'no order book, no fill cost'
    assert 'Fill cost by hour' not in Views(source, Selection(series=['Day-Ahead_Auction'])).costs()
    assert 'Fill cost by hour' in standard_figures(source)
    bars = figures['Orders by status']
    assert {t.name for t in bars.data} == {'MATCHED', 'SUPERSEDED', 'REVISED_STANDING_ORDER'} and bars.layout.barmode == 'stack'
    quantity = figures['Matched quantity']
    assert any(t.name.startswith('matched:') for t in quantity.data) and any(t.name.startswith('match price') for t in quantity.data)
    assert quantity.layout.xaxis3.title.text == 'Delivery interval start (UTC)'
    assert [a.text for a in quantity.layout.annotations] == ['<b>Matched</b>', '<b>Standing</b>', '<b>Match price</b>']
    assert Views(pacific, Selection()).orders() == {}
    assert Views(source, Selection(series=['Day-Ahead_Auction'])).orders() == {}, 'no orders in an auction series'
    assert Views(source, Selection(start=pd.Timestamp('2030-01-01T00:00Z'))).orders() == {}
    assert 'Orders by status' in standard_figures(source) and 'Orders by status' not in standard_figures(pacific)
    assert pages_for(source) == ['Overview', 'Curves', 'Negotiation', 'Markets', 'Orders', 'Signals', 'Bid position', 'Costs']
    assert pages_for(pacific) == ['Overview', 'Curves', 'Negotiation', 'Markets', 'Signals', 'Bid position', 'Costs']
    assert pages_for(historian)[-1] == 'Devices' and 'Orders' in pages_for(historian)


def test_inspect_reports_orders(source, pacific, capsys):
    assert main(['inspect', source.url]) == 0
    out = capsys.readouterr().out
    assert 'order book: True' in out and '| orders: 216' in out
    assert 'Orders by latest status: REVISED_STANDING_ORDER 24, MATCHED 96, SUPERSEDED 96' in out
    assert main(['inspect', pacific.url]) == 0
    out = capsys.readouterr().out
    assert 'order book: False' in out and '| orders: 0' in out and 'Orders by latest status' not in out


def test_device_panel_groups_points_by_device():
    t = pd.to_datetime(['2017-07-01T00:00Z', '2017-07-01T00:10Z'])
    rows = [{'source': 's', 'topic': f'campus/{device}/{point}', 'ts': ts, 'value': v}
            for device in ('HP1', 'HP2') for point in ('Power', 'ZoneTemperature') for ts, v in zip(t, (1.0, 2.0))]
    topics = pd.DataFrame(rows)
    fig = device_panel(topics)
    assert len(fig.data) == 4 and {t.name for t in fig.data} == {'campus/HP1', 'campus/HP2'}
    assert [a.text for a in fig.layout.annotations] == ['<b>Power</b>', '<b>ZoneTemperature</b>']
    assert sum(t.showlegend for t in fig.data) == 2, 'one legend entry per device'
    many = pd.DataFrame([{'source': 's', 'topic': f'campus/HP{i}/Power', 'ts': t[0], 'value': 1.0} for i in range(9)])
    crowded = device_panel(many)
    assert crowded.layout.showlegend is False and crowded.layout.title.text == 'Device points; 9 devices, hover for names'
    assert fig.layout.showlegend is True
    with pytest.raises(ValueError, match='exceed max_rows'):
        device_panel(topics, max_rows=1)
    with pytest.raises(ValueError, match='non-numeric'):
        device_panel(topics.assign(value=[{'a': 1}] * len(topics)))


def test_devices_view_splits_and_filters(historian):
    views = Views(historian, Selection())
    assert list(views.devices()) == ['Device points']
    split = views.devices(points_per_figure=1)
    assert list(split) == ['Device points (1 of 2)', 'Device points (2 of 2)']
    assert [a.text for f in split.values() for a in f.layout.annotations] == ['<b>OutdoorAirTemperature</b>', '<b>Power</b>']
    assert split['Device points (1 of 2)'].layout.title.text == 'Device points (1 of 2), 1 non-numeric topics omitted'
    assert split['Device points (2 of 2)'].layout.title.text == 'Device points (2 of 2)'
    only = views.devices('%Temperature')
    assert list(only) == ['Device points'] and only['Device points'].layout.title.text == 'Device points'
    assert views.devices('%NoSuchPoint') == {}
