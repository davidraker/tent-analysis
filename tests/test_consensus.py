"""Analysis of consensus runs: outcomes per market, signal traffic, link agreement, the summary and its figures, over the
fixture's consensus market (two recorded passes, a market that closes without consensus in campus_a and in consensus
in campus_b, and an exchange with one neighbor ending in an acknowledgement sent after the deadline)."""
import pandas as pd
import plotly.graph_objects as go
import pytest

from tent_analysis.analysis import (compare_runs, consensus_outcomes, consensus_summary, link_agreement, signal_traffic)
from tent_analysis.analysis.consensus_runs import AGREEMENT_COLUMNS, OUTCOME_COLUMNS, SUMMARY_COLUMNS, TRAFFIC_COLUMNS
from tent_analysis.app.views import Selection, Views
from tent_analysis.data import load_measurements, load_signal_headers, load_signals, load_state_changes
from tent_analysis.data.loaders import SIGNAL_HEADER_COLUMNS
from tent_analysis.report.cli import main
from tent_analysis.viz import Theme, consensus_outcome_timeline, signal_traffic_panel, time_to_consensus

SERIES = 'Consensus_Market'


@pytest.fixture(scope='module')
def outcomes(source, variant):
    return {s.label: consensus_outcomes(load_measurements(s, types=['FullConsensus'], history=True), load_state_changes(s))
            for s in (source, variant)}


@pytest.fixture(scope='module')
def traffic(source):
    return signal_traffic(load_signal_headers(source), load_state_changes(source))


def test_outcomes_one_row_per_market(source, outcomes):
    out = outcomes['campus_a']
    assert list(out.columns) == OUTCOME_COLUMNS and len(out) == 1 and out['market_series'].iloc[0] == SERIES
    row = out.iloc[0]
    markets = source.catalog.markets
    assert row['market_name'] == markets[markets['series_name'] == SERIES]['name'].iloc[0]
    changes = load_state_changes(source)
    mine = changes[changes['market_name'] == row['market_name']].set_index('state')['record_time']
    assert row['negotiation_start'] == mine['Negotiation'] and row['negotiation_end'] == mine['MarketLead']
    assert row['negotiation_window'] == mine['MarketLead'] - mine['Negotiation'] > pd.Timedelta(0)
    # Two record_consensus calls left records (the one at the transition changed nothing); the second pass wrote only
    # the intervals whose flag changed, and the unchanged interval carries its earlier value forward.
    assert row['passes'] == 2 and row['intervals'] >= 4
    assert row['consensus_at_close'] == row['intervals'] - 1 and not row['closed_in_consensus']
    assert pd.isna(row['time_to_consensus']) and pd.isna(row['consensus_time']) and pd.isna(row['consensus_fraction'])
    assert row['late_passes'] == 0 and not row['final_consensus']
    assert row['delivery_start'] == load_measurements(source, types=['FullConsensus'])['interval_start'].min()


def test_outcomes_time_to_consensus(outcomes):
    row = outcomes['campus_b'].iloc[0]
    assert row['closed_in_consensus'] and row['final_consensus'] and row['consensus_at_close'] == row['intervals']
    assert row['time_to_consensus'] == pd.Timedelta(minutes=4), 'the second pass, four minutes into Negotiation'
    assert row['consensus_time'] == row['negotiation_start'] + pd.Timedelta(minutes=4)
    assert row['consensus_fraction'] == pytest.approx(240.0 / row['negotiation_window'].total_seconds())


def test_close_record_and_late_passes():
    """The record TENT writes just after the MarketLead state change is the close; later records are late passes."""
    t0 = pd.Timestamp('2017-07-01T00:00Z')
    intervals = [t0 + pd.Timedelta(hours=1), t0 + pd.Timedelta(hours=2)]
    rows = []
    for record, flags in ((t0, [0, 0]), (t0 + pd.Timedelta(minutes=5), [1, 1]), (t0 + pd.Timedelta(minutes=9), [1, 0]),
                          (t0 + pd.Timedelta(minutes=10, milliseconds=5), [0, 0]), (t0 + pd.Timedelta(minutes=10, seconds=20), [1, 1])):
        for ti, flag in zip(intervals, flags):
            rows.append({'source': 's', 'actor': 'a', 'run_id': 'r', 'market_id': 'm', 'owner_name': 'M', 'market_series': 'S',
                         'measurement_type': 'FullConsensus', 'interval_start': ti, 'record_time': record, 'value': flag})
    history = pd.DataFrame(rows)
    changes = pd.DataFrame({'source': 's', 'market_id': 'm', 'market_name': 'M', 'market_series': 'S',
                            'state': ['Negotiation', 'MarketLead'], 'record_time': [t0, t0 + pd.Timedelta(minutes=10)]})
    out = consensus_outcomes(history, changes).iloc[0]
    assert out['passes'] == 5 and out['consensus_at_close'] == 0 and not out['closed_in_consensus']
    assert out['close_time'] == t0 + pd.Timedelta(minutes=10, milliseconds=5)
    assert out['late_passes'] == 1 and out['final_consensus'], 'consensus regained on the late rebalance in MarketLead'
    assert out['time_to_consensus'] == pd.Timedelta(minutes=5), 'first reached at the second pass, lost afterwards'
    # Without state changes the latest record is the close.
    bare = consensus_outcomes(history).iloc[0]
    assert pd.isna(bare['negotiation_start']) and bare['closed_in_consensus'] and bare['late_passes'] == 0
    assert bare['market_name'] == 'M' and pd.isna(bare['time_to_consensus'])


def test_signal_headers_include_curveless_signals(source):
    """TENT archives every signal's header but a curve only when its content changed, so the acknowledgement (same
    curves as the signal before it) has a header and no vertices: in load_signal_headers, not in load_signals."""
    headers = load_signal_headers(source)
    assert list(headers.columns) == SIGNAL_HEADER_COLUMNS and headers['signal_id'].is_unique
    with_curves = set(load_signals(source, history=True)['signal_id'])
    assert len(headers) == 5 and len(with_curves) == 4 and with_curves < set(headers['signal_id'])
    ack = headers[~headers['signal_id'].isin(with_curves)].iloc[0]
    assert (ack['direction'], ack['sequence'], ack['in_reply_to'], ack['revised']) == ('sent', 2, 1, False)
    assert ack['market_series'] == SERIES and str(headers['signal_time'].dt.tz) == 'UTC'
    starts = source.catalog.intervals.groupby('market_id')['start_time'].min()
    assert (headers['delivery_start'] == headers['market_id'].map(starts)).all()


def test_signal_traffic_counts(source, traffic):
    assert list(traffic.columns) == TRAFFIC_COLUMNS
    headers = load_signal_headers(source)
    assert traffic['sent'].sum() == (headers['direction'] == 'sent').sum() == 3
    assert traffic['received'].sum() == (headers['direction'] == 'received').sum() == 2
    mine = traffic[traffic['market_series'] == SERIES].iloc[0]
    assert mine['neighbor_name'] == 'CoR' and mine['sent'] == 2 and mine['received'] == 1
    assert mine['sent_revised'] == 1 and mine['sent_acknowledgements'] == 1
    assert mine['received_revised'] == 1 and mine['received_acknowledgements'] == 0
    assert mine['after_deadline'] == 1, 'the acknowledgement went out from MarketLead'
    assert mine['first_signal_time'] < mine['last_signal_time']
    assert mine['delivery_start'] == load_measurements(source, types=['FullConsensus'])['interval_start'].min()
    other = traffic[traffic['market_series'] != SERIES].iloc[0]
    assert other['sent'] == other['sent_revised'] == 1, 'a null revised flag counts as revised'
    assert signal_traffic(headers)['after_deadline'].isna().all(), 'no state changes, no deadline'
    # A vertex frame counts the signals that have curves, and its delivery_start is the first interval the signals cover.
    from_vertices = signal_traffic(load_signals(source, history=True), load_state_changes(source))
    assert from_vertices['sent'].sum() == 2 and (from_vertices['delivery_start'] >= traffic['delivery_start']).all()
    assert from_vertices.loc[1, 'delivery_start'] == traffic.loc[1, 'delivery_start'], 'the auction signals start at interval 0'


def test_link_agreement_uses_the_balance_points_tent_way(source):
    """The consensus market's signals mark their balance points (archive schema 6): this node proposed -50 kW, the
    neighbor 48 kW from its side, -48 kW from here. TENT's test is the relative distance between the two points,
    |2| / 49 = 0.0408 with no price term (both points at 0.03 $/kWh), against the neighbor's threshold."""
    signals = load_signals(source, history=True)
    mine = signals[signals['market_series'] == SERIES]
    assert mine['balance_point'].dtype.name == 'boolean' and mine['balance_point'].notna().all()
    assert mine.groupby(['signal_id', 'interval_start'])['balance_point'].sum().eq(1).all(), 'one point per curve'
    marked = mine[mine['balance_point'].astype(bool)]
    assert set(marked.loc[marked['direction'] == 'sent', 'quantity']) == {-50.0}
    assert set(marked.loc[marked['direction'] == 'received', 'quantity']) == {48.0}, 'as the neighbor stated it'
    out = link_agreement(mine, thresholds={'CoR': 0.05})
    assert list(out.columns) == AGREEMENT_COLUMNS and len(out) == 4
    assert (out['sent_sequence'] == 1).all() and (out['received_sequence'] == 1).all(), \
        'the last signal each way with curves: the acknowledgement (sequence 2) repeated them and has none archived'
    assert (out['rule'] == 'balance_point').all()
    assert out['sent_balance_kw'].eq(-50.0).all() and out['received_balance_kw'].eq(-48.0).all(), 'restated from here'
    assert out['balance_gap_kw'].eq(-2.0).all()
    assert out['balance_distance'].round(6).eq(round(2.0 / 49.0, 6)).all()
    assert out['within_threshold'].all() and (out['threshold'] == 0.05).all()
    assert not link_agreement(mine, thresholds={'CoR': 0.02})['within_threshold'].any()
    assert link_agreement(mine)['within_threshold'].isna().all(), 'no threshold, no verdict'
    # The other readings are still there, whatever the rule.
    assert out['max_curve_gap'].round(6).eq(2.0).all(), 'sent -50 against received +48 restated to -48; 30 against 30'
    assert out['gap_kw'].isna().all() and out['price'].isna().all(), 'no cleared price given'
    prices = pd.DataFrame({'source': source.label, 'market_id': mine['market_id'].iloc[0],
                           'interval_start': sorted(mine['interval_start'].unique()), 'price': 0.06})
    priced = link_agreement(mine, thresholds={'CoR': 0.05}, prices=prices)
    assert (priced['rule'] == 'balance_point').all(), 'a marked balance point outranks the price'
    assert priced['sent_kw'].round(6).eq(-10.0).all() and priced['received_kw'].round(6).eq(-9.0).all()
    assert priced['gap_kw'].round(6).eq(-1.0).all() and priced['within_threshold'].all()
    assert source.catalog.convergence_thresholds.keys() >= {'CoR'}


def test_link_agreement_falls_back_where_no_balance_point_is_marked(source):
    """The auction market's signals in the same archive carry no balance point: those rows take the cleared-price rule
    with prices and the curve-gap rule without, as every row of an archive before schema version 6 does."""
    signals = load_signals(source, history=True)
    out = link_agreement(signals, thresholds={'CoR': 6.0})
    by_rule = out.groupby('market_series')['rule'].unique().map(list).to_dict()
    assert by_rule == {SERIES: ['balance_point'], 'Day-Ahead_Auction': ['curve_gap']}
    auction = out[out['market_series'] != SERIES]
    assert auction['sent_balance_kw'].isna().all() and auction['balance_distance'].isna().all()
    assert auction['max_curve_gap'].round(6).eq(6.0).all() and auction['within_threshold'].all(), '|30 - 36| = 6 kW'
    assert not link_agreement(signals, thresholds={'CoR': 5.0}).loc[out['market_series'] != SERIES, 'within_threshold'].any()
    prices = pd.DataFrame({'source': source.label, 'market_id': auction['market_id'].iloc[0],
                           'interval_start': sorted(auction['interval_start'].unique()), 'price': 0.06})
    priced = link_agreement(signals, thresholds={'CoR': 3.0}, prices=prices)
    priced_auction = priced[priced['market_series'] != SERIES]
    assert (priced_auction['rule'] == 'cleared_price').all()
    assert priced_auction['sent_kw'].round(6).eq(-10.0).all() and priced_auction['received_kw'].round(6).eq(-4.5).all()
    assert priced_auction['gap_kw'].round(6).eq(-5.5).all() and not priced_auction['within_threshold'].any()
    assert (priced.loc[priced['market_series'] == SERIES, 'rule'] == 'balance_point').all()


def test_older_archives_without_the_balance_point_column_still_load(source, tmp_path):
    """An archive of schema version 3 to 5 has no sd_curve.balance_point_record: the loaders say so with a null flag
    and link_agreement applies the fallback rules."""
    import shutil
    import sqlalchemy as sa
    from tent_analysis.data import Source, load_curves
    path = tmp_path / 'older.sqlite'
    shutil.copy(source.url.removeprefix('sqlite:///'), path)
    engine = sa.create_engine(f'sqlite:///{path}')
    with engine.begin() as connection:
        connection.execute(sa.text('ALTER TABLE sd_curve DROP COLUMN balance_point_record'))
        connection.execute(sa.text("UPDATE archive_schema SET version = 5 WHERE application = 'TENT'"))
    older = Source(f'sqlite:///{path}', label='older')
    assert older.reader.schema_version == 5
    signals = load_signals(older, history=True)
    assert len(signals) == len(load_signals(source, history=True)) and signals['balance_point'].isna().all()
    assert signals['balance_point'].dtype.name == 'boolean'
    curves = load_curves(older)
    assert not curves.empty and curves['balance_point'].isna().all()
    out = link_agreement(signals[signals['market_series'] == SERIES], thresholds={'CoR': 2.5})
    assert (out['rule'] == 'curve_gap').all() and out['within_threshold'].all()
    assert out['sent_balance_kw'].isna().all() and out['balance_distance'].isna().all()
    # The current archive, by contrast, says which vertex is the point on every signal curve, and False elsewhere.
    current = load_curves(source)
    assert current['balance_point'].notna().all()
    assert not current.loc[current['direction'].isna(), 'balance_point'].astype(bool).any(), 'system curves, active vertices'


def test_balance_point_distance_is_tents_relative_distance():
    """TransactiveSignal._relative_distance: quantity term relative to the average quantity (absolute at zero), price
    term only with flexibility on both sides at finite, non-zero prices, and never for a quantity-only market."""
    from tent_analysis.analysis import balance_point_distance
    sent = pd.Series({'price': 0.05, 'quantity': -100.0})
    received = pd.Series({'price': 0.05, 'quantity': -98.0})
    assert balance_point_distance(sent, received, flexible=True) == pytest.approx(2.0 / 99.0)
    priced = pd.Series({'price': 0.06, 'quantity': -98.0})
    expected = (2.0 / 99.0) ** 2 + (0.01 / 0.055) ** 2
    assert balance_point_distance(sent, priced, flexible=True) == pytest.approx(expected ** 0.5)
    assert balance_point_distance(sent, priced, flexible=False) == pytest.approx(2.0 / 99.0), 'an inelastic side: quantity only'
    assert balance_point_distance(sent, priced, flexible=True, quantity_only=True) == pytest.approx(2.0 / 99.0)
    inelastic = pd.Series({'price': float('inf'), 'quantity': -98.0})
    assert balance_point_distance(sent, inelastic, flexible=True) == pytest.approx(2.0 / 99.0), 'an infinite price has no term'
    opposite = pd.Series({'price': 0.05, 'quantity': -0.25})
    assert balance_point_distance(opposite, pd.Series({'price': 0.05, 'quantity': 0.25}), flexible=False) == pytest.approx(0.5), \
        'exact opposites average to zero: the absolute gap against a 1 kW scale'
    # A matching_consensus market in link_agreement leaves the price term out.
    t = pd.Timestamp('2017-07-01T00:00Z')
    rows = []
    for direction, signal_id, points in (('sent', 's1', [(0.03, -60.0, False), (0.05, -50.0, True), (0.09, 30.0, False)]),
                                        ('received', 'r1', [(0.03, 55.0, False), (0.07, 50.0, True), (0.09, -30.0, False)])):
        for record, (price, quantity, flag) in enumerate(points):
            rows.append({'source': 's', 'actor': 'a', 'run_id': 'r', 'signal_id': signal_id, 'direction': direction,
                         'signal_time': t, 'sequence': 1, 'neighbor_name': 'N', 'market_id': 'm', 'market_name': 'M',
                         'market_series': 'S', 'curve_id': signal_id + 'c', 'record_time': t, 'interval_start': t,
                         'record': record, 'price': price, 'quantity': quantity, 'balance_point': flag})
    frame = pd.DataFrame(rows)
    frame['balance_point'] = frame['balance_point'].astype('boolean')
    with_price = link_agreement(frame, thresholds={'N': 0.1}).iloc[0]
    quantity_only = link_agreement(frame, thresholds={'N': 0.1}, market_types={'m': 'matching_consensus'}).iloc[0]
    assert with_price['balance_distance'] == pytest.approx(((0.02 / 0.06) ** 2) ** 0.5) and not with_price['within_threshold']
    assert quantity_only['balance_distance'] == 0.0 and quantity_only['within_threshold']
    assert with_price['sent_balance_kw'] == -50.0 and with_price['received_balance_kw'] == -50.0


def test_summary_and_compare_runs(outcomes, traffic):
    a = consensus_summary(outcomes['campus_a'], traffic)
    assert list(a.columns) == SUMMARY_COLUMNS and len(a) == 1
    row = a.iloc[0]
    assert (row['markets'], row['closed_in_consensus'], row['closed_without'], row['final_consensus']) == (1, 0, 1, 0)
    assert row['mean_passes'] == 2.0 and pd.isna(row['median_minutes_to_consensus'])
    assert row['signals_sent'] == 2 and row['signals_received'] == 1, 'the consensus market\'s own signals'
    b = consensus_summary(outcomes['campus_b'])
    assert b['closed_in_consensus'].iloc[0] == 1 and b['median_minutes_to_consensus'].iloc[0] == 4.0
    assert b['signals_sent'].isna().all()
    both = pd.concat([a, b], ignore_index=True)
    cmp = compare_runs(both, baseline='campus_a', value='closed_in_consensus', keys=['market_series'])
    assert list(cmp['delta']) == [1] and cmp['source'].iloc[0] == 'campus_b'


def test_empty_data(source):
    """An archive without consensus data (an Auction run) yields empty frames with the right columns and no figures."""
    changes = load_state_changes(source)
    no_flags = load_measurements(source, types=['MarginalPrice'], history=True)
    assert consensus_outcomes(no_flags, changes).empty and list(consensus_outcomes(no_flags).columns) == OUTCOME_COLUMNS
    assert list(signal_traffic(pd.DataFrame()).columns) == TRAFFIC_COLUMNS
    assert list(link_agreement(pd.DataFrame()).columns) == AGREEMENT_COLUMNS
    assert list(consensus_summary(pd.DataFrame()).columns) == SUMMARY_COLUMNS
    auction_only = Views(source, Selection(series=['Day-Ahead_Auction']))
    figures = auction_only.negotiation()
    assert 'Price negotiation' in figures and not any(k.startswith(('Consensus', 'Time to', 'Signal traffic')) for k in figures)
    assert auction_only.consensus_table().empty


def test_figures_build(outcomes, traffic):
    both = pd.concat(outcomes.values(), ignore_index=True)
    for theme in (Theme('light'), Theme('dark')):
        tl = consensus_outcome_timeline(both, theme=theme)
        bars = {t.name: t for t in tl.data if isinstance(t, go.Bar)}
        assert set(bars) == {'closed in consensus', 'closed without consensus'} and all(t.orientation == 'h' for t in bars.values())
        assert any(t.name == 'full consensus reached' for t in tl.data)
        assert set(tl.layout.yaxis.categoryarray) == {f'campus-A: {SERIES}', f'campus-B: {SERIES}'}, 'one row per actor'
        ttc = time_to_consensus(both, theme=theme)
        names = {t.name for t in ttc.data}
        assert {'campus_a', 'campus_b', 'no consensus by close'} <= names, 'variants side by side; the market that never closed marked'
        passes = time_to_consensus(both, value='passes', theme=theme)
        assert all(list(t.y) == [2] for t in passes.data if isinstance(t, go.Bar))
        panel = signal_traffic_panel(traffic, theme=theme)
        assert {t.name for t in panel.data} == {'sent, revised', 'sent, acknowledgement', 'received, revised'}
        assert panel.layout.barmode == 'stack' and {t.offsetgroup for t in panel.data} == {'sent', 'received'}
        assert tl.layout.template.layout.plot_bgcolor == theme.ink['surface']


def test_views_report_and_cli(source, tmp_path, capsys):
    figures = Views(source, Selection()).negotiation()
    assert {'Full consensus', 'Consensus outcomes', 'Time to consensus', 'Consensus passes', 'Signal traffic'} <= set(figures)
    table = Views(source, Selection()).consensus_table()
    assert len(table) == 1 and table['signals_sent'].iloc[0] == 2
    assert main(['inspect', source.url, '--label', 'x']) == 0
    out = capsys.readouterr().out
    assert 'Consensus (markets closed in consensus / without' in out
    assert f'{SERIES:<24}   0 / 1' in out and 'signals sent 2 received 1' in out
    assert main(['export', source.url, '--what', 'consensus', '--out', str(tmp_path / 'c.csv')]) == 0
    back = pd.read_csv(tmp_path / 'c.csv')
    assert len(back) == 1 and list(back.columns) == OUTCOME_COLUMNS
