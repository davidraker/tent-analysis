"""
Analysis of consensus runs: how each ConsensusMarket (or MatchingConsensusMarket) negotiation ended, how many signals
it took, and how far each link's final signals lie apart. DataFrame in, DataFrame out, over load_measurements(...,
history=True) filtered to FullConsensus, load_state_changes and load_signals(..., history=True).

What the archive records, and what this module can therefore say:

- A ConsensusMarket calls record_consensus at the end of every negotiation pass, when Negotiation ends (just after the
  MarketLead state change is written) and after a late rebalance in MarketLead. A FullConsensus value is archived only
  when it changes (Measurement.update skips an identical value), so the recorded passes are the passes that moved some
  interval's flag, not every balancing pass; the signals sent per neighbor (signal_traffic) count the exchanges.
- The records of one pass share a record_time to within milliseconds, and passes are seconds apart (a state-machine
  iteration), so records closer than PASS_GAP belong to one pass.
- From archive schema version 6 on (TENT, 8 November 2026) a signal curve's row says which vertex is its balance point,
  the exchange the signal proposes (sd_curve.balance_point_record; load_signals exposes it as the `balance_point`
  flag), so link_agreement can test a link as TENT's Neighbor.check_for_convergence does: the last sent balance point
  against the last received one restated from the local side, as a relative distance against the neighbor's
  convergence threshold. Older archives numbered the vertices in price order and recorded nothing else, so there
  link_agreement falls back to reading each curve at the market's cleared price when prices are given, and otherwise to
  comparing the curves as functions; the `rule` column says which applied to each row.
"""
import math

import numpy as np
import pandas as pd

from tent_analysis.analysis.curves import from_local_side, latest_version, quantity_at_price
from tent_analysis.analysis.metrics import KEY

PASS_GAP = pd.Timedelta(seconds=1)  # records closer than this belong to one record_consensus call
MARKET = ['market_series', 'market_id', 'market_name']
OUTCOME_COLUMNS = KEY + MARKET + ['delivery_start', 'negotiation_start', 'negotiation_end', 'negotiation_window', 'passes',
                                  'intervals', 'consensus_at_close', 'closed_in_consensus', 'close_time', 'late_passes',
                                  'final_consensus', 'consensus_time', 'time_to_consensus', 'consensus_fraction']
TRAFFIC_COLUMNS = KEY + MARKET + ['neighbor_name', 'delivery_start', 'sent', 'received', 'sent_revised',
                                  'sent_acknowledgements', 'received_revised', 'received_acknowledgements',
                                  'first_signal_time', 'last_signal_time', 'after_deadline']
AGREEMENT_COLUMNS = KEY + MARKET + ['neighbor_name', 'interval_start', 'sent_time', 'received_time', 'sent_sequence',
                                    'received_sequence', 'rule', 'sent_balance_kw', 'received_balance_kw',
                                    'balance_gap_kw', 'balance_distance', 'price', 'sent_kw', 'received_kw', 'gap_kw',
                                    'max_curve_gap', 'threshold', 'within_threshold']
RULES = ('balance_point', 'cleared_price', 'curve_gap')  # what link_agreement tested against the threshold, see there
QUANTITY_ONLY_MARKET_TYPES = ('matching_consensus',)  # market types whose neighbors agree on the quantity alone
SUMMARY_COLUMNS = ['source', 'actor', 'market_series', 'markets', 'closed_in_consensus', 'closed_without', 'final_consensus',
                   'mean_passes', 'median_time_to_consensus', 'median_minutes_to_consensus', 'median_consensus_fraction',
                   'signals_sent', 'signals_received']


def _is_acknowledgement(revised: pd.Series) -> pd.Series:
    """True where a signal's revised flag is exactly False: an acknowledgement. Null (archives before the protocol)
    means revised, the class's default."""
    return revised.map(lambda v: v is not None and not pd.isna(v) and not bool(v)).astype(bool)


def _negotiation_bounds(state_changes: pd.DataFrame | None) -> pd.DataFrame:
    """Per (source, market_id): the market's name, when Negotiation was entered and when MarketLead was."""
    columns = ['source', 'market_id', 'market_name', 'negotiation_start', 'negotiation_end']
    if state_changes is None or state_changes.empty:
        return pd.DataFrame(columns=columns)
    df = state_changes.sort_values('record_time')
    names = df.drop_duplicates(['source', 'market_id'])[['source', 'market_id', 'market_name']]
    starts = (df[df['state'] == 'Negotiation'].drop_duplicates(['source', 'market_id'], keep='last')
              .rename(columns={'record_time': 'negotiation_start'})[['source', 'market_id', 'negotiation_start']])
    ends = (df[df['state'] == 'MarketLead'].drop_duplicates(['source', 'market_id'], keep='last')
            .rename(columns={'record_time': 'negotiation_end'})[['source', 'market_id', 'negotiation_end']])
    out = names.merge(starts, on=['source', 'market_id'], how='left').merge(ends, on=['source', 'market_id'], how='left')
    for column in ('negotiation_start', 'negotiation_end'):
        out[column] = pd.to_datetime(out[column])
    return out[columns]


def _passes(record_times: pd.Series) -> pd.Series:
    """Each record's pass time: the first record time of the cluster of records closer than PASS_GAP to one another."""
    times = pd.Series(sorted(record_times.unique()))
    new_pass = times.diff().isna() | (times.diff() > PASS_GAP)
    pass_time = times.where(new_pass).ffill()
    return record_times.map(dict(zip(times, pass_time)))


def consensus_outcomes(full_consensus_history: pd.DataFrame, state_changes: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    How did each consensus negotiation end? One row per market (source, actor, run, market) from every version of its
    FullConsensus flags (load_measurements(..., history=True); other measurement types in the frame are ignored):
    negotiation_start and negotiation_end from the state changes (entering Negotiation and MarketLead; NaT without
    them), `passes` recorded (see the module note: only passes that changed a flag leave records), `intervals`,
    `consensus_at_close` (intervals whose flag was true at the close record: the latest pass no later than PASS_GAP
    after the MarketLead change, since TENT writes that record just after the state change; the latest record when the
    deadline is unknown), `closed_in_consensus` (every interval true at close), `late_passes` (records after the close:
    a market in MarketLead absorbing a neighbor's last word) and `final_consensus` (every interval true at the latest
    record, late passes included), `consensus_time` (the first pass at which every interval was true, NaT if never),
    `time_to_consensus` (that minus negotiation_start) and `consensus_fraction` (as a fraction of the negotiation
    window). `delivery_start` is the market's first interval.
    """
    df = full_consensus_history
    if 'measurement_type' in df:
        df = df[df['measurement_type'] == 'FullConsensus']
    if df.empty:
        return pd.DataFrame(columns=OUTCOME_COLUMNS)
    bounds = _negotiation_bounds(state_changes)
    rows = []
    for (source, actor, run_id, market_id), part in df.groupby(KEY + ['market_id'], sort=False, dropna=False):
        bound = bounds[(bounds['source'] == source) & (bounds['market_id'] == market_id)]
        start = bound['negotiation_start'].iloc[0] if not bound.empty else pd.NaT
        end = bound['negotiation_end'].iloc[0] if not bound.empty else pd.NaT
        name = bound['market_name'].iloc[0] if not bound.empty and pd.notna(bound['market_name'].iloc[0]) \
            else (part['owner_name'].iloc[0] if 'owner_name' in part else market_id)
        part = part.assign(pass_time=_passes(part['record_time']), flag=part['value'].astype(float) >= 0.5)
        # Flags per pass: the value each interval held after the pass (unchanged flags carry forward).
        flags = (part.sort_values('record_time').groupby(['pass_time', 'interval_start'])['flag'].last()
                 .unstack('interval_start').ffill().fillna(False).astype(bool))
        all_true = flags.all(axis=1)
        eligible = flags.index if pd.isna(end) else flags.index[flags.index <= end + PASS_GAP]
        close_time = eligible[-1] if len(eligible) else flags.index[-1]
        at_close = int(flags.loc[close_time].sum())
        consensus_time = all_true[all_true].index[0] if all_true.any() else pd.NaT
        window = end - start
        time_to = consensus_time - start if pd.notna(consensus_time) and pd.notna(start) else pd.NaT
        fraction = (time_to / window) if pd.notna(time_to) and pd.notna(window) and window > pd.Timedelta(0) else np.nan
        rows.append({'source': source, 'actor': actor, 'run_id': run_id, 'market_series': part['market_series'].iloc[0],
                     'market_id': market_id, 'market_name': name, 'delivery_start': part['interval_start'].min(),
                     'negotiation_start': start, 'negotiation_end': end, 'negotiation_window': window,
                     'passes': int(flags.shape[0]), 'intervals': int(flags.shape[1]), 'consensus_at_close': at_close,
                     'closed_in_consensus': bool(flags.shape[1] > 0 and at_close == flags.shape[1]),
                     'close_time': close_time, 'late_passes': int((flags.index > close_time).sum()),
                     'final_consensus': bool(flags.shape[1] > 0 and all_true.iloc[-1]),
                     'consensus_time': consensus_time, 'time_to_consensus': time_to, 'consensus_fraction': fraction})
    out = pd.DataFrame(rows, columns=OUTCOME_COLUMNS)
    for column in ('negotiation_start', 'negotiation_end', 'close_time', 'consensus_time'):
        out[column] = pd.to_datetime(out[column])
    for column in ('negotiation_window', 'time_to_consensus'):
        out[column] = pd.to_timedelta(out[column].astype(object))  # an all-NaT column would otherwise infer datetime
    return out.sort_values(['source', 'actor', 'market_series', 'delivery_start']).reset_index(drop=True)


def _signal_headers(signals: pd.DataFrame) -> pd.DataFrame:
    """One row per signal (a vertex frame repeats a signal's header on every row), with `acknowledgement` from
    `revised` and `delivery_start`, the market's first interval, from the frame or from the vertices' intervals."""
    ordered = signals.sort_values('record_time') if 'record_time' in signals else signals
    headers = ordered.drop_duplicates('signal_id').copy()
    for column in ('sequence', 'in_reply_to', 'revised'):
        if column not in headers:
            headers[column] = None
    headers['acknowledgement'] = _is_acknowledgement(headers['revised'])
    if 'delivery_start' not in headers:
        starts = signals.groupby(['source', 'market_id'])['interval_start'].min().rename('delivery_start').reset_index()
        headers = headers.merge(starts, on=['source', 'market_id'], how='left')
    return headers


def signal_traffic(signals: pd.DataFrame, state_changes: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    How many signals did each link carry per market? Per source/actor/run/market/neighbor over load_signal_headers
    output (every signal; a load_signals frame works too but lacks the signals TENT archived without curves, the
    acknowledgements and repeats whose content it already held): signals sent and received, each split into revised
    signals and acknowledgements (revised False; a null flag, from an archive before the protocol, counts as revised),
    the first and last signal time, and, when state changes are given, `after_deadline`: signals whose signal_time is
    after the market entered MarketLead (the late words a market absorbs or acknowledges there). `delivery_start`, for
    ordering, is the market's first interval (from a header frame) or the first interval the signals cover (from a
    vertex frame).
    """
    if signals.empty:
        return pd.DataFrame(columns=TRAFFIC_COLUMNS)
    headers = _signal_headers(signals)
    bounds = _negotiation_bounds(state_changes)
    if bounds.empty:  # no deadline known for any market: a zoned NaT column, comparable with the signal times
        headers['negotiation_end'] = pd.Series(pd.NaT, index=headers.index, dtype=headers['signal_time'].dtype)
    else:
        headers = headers.merge(bounds[['source', 'market_id', 'negotiation_end']], on=['source', 'market_id'], how='left')
    sent, received = headers['direction'] == 'sent', headers['direction'] == 'received'
    ack = headers['acknowledgement']
    headers = headers.assign(sent=sent, received=received, sent_revised=sent & ~ack, sent_acknowledgements=sent & ack,
                             received_revised=received & ~ack, received_acknowledgements=received & ack,
                             after_deadline=headers['signal_time'] > headers['negotiation_end'])
    group = KEY + MARKET + ['neighbor_name']
    counts = headers.groupby(group, sort=True, dropna=False).agg(
        delivery_start=('delivery_start', 'first'), sent=('sent', 'sum'), received=('received', 'sum'),
        sent_revised=('sent_revised', 'sum'), sent_acknowledgements=('sent_acknowledgements', 'sum'),
        received_revised=('received_revised', 'sum'), received_acknowledgements=('received_acknowledgements', 'sum'),
        first_signal_time=('signal_time', 'min'), last_signal_time=('signal_time', 'max'),
        after_deadline=('after_deadline', 'sum'), deadline_known=('negotiation_end', lambda s: s.notna().any())).reset_index()
    for column in ('sent', 'received', 'sent_revised', 'sent_acknowledgements', 'received_revised', 'received_acknowledgements'):
        counts[column] = counts[column].astype(int)
    counts['after_deadline'] = counts['after_deadline'].astype('Int64').where(counts['deadline_known'], pd.NA)
    return counts[TRAFFIC_COLUMNS].sort_values(['source', 'actor', 'market_series', 'delivery_start', 'neighbor_name']) \
        .reset_index(drop=True)


def _last_signal_curves(signals: pd.DataFrame, direction: str) -> pd.DataFrame:
    """The vertex rows of the newest version of the last signal (by signal time, then record time) of one direction per
    source, market, neighbor and interval."""
    df = latest_version(signals[signals['direction'] == direction])
    if df.empty:
        return df
    keys = KEY + ['market_id', 'neighbor_name', 'interval_start']
    last = df.sort_values(['signal_time', 'record_time']).groupby(keys, sort=False, dropna=False)['curve_id'].last()
    return df[df['curve_id'].isin(set(last))]


def _price_lookup(prices: pd.DataFrame | None) -> tuple[list[str], pd.DataFrame | None]:
    """Cleared price per market (a measurements frame with market_id) or per series (cleared_prices output)."""
    if prices is None or prices.empty:
        return [], None
    df = prices[prices['measurement_type'] == 'MarginalPrice'] if 'measurement_type' in prices else prices
    value = 'price' if 'price' in df else 'value'
    keys = ['source', 'market_id', 'interval_start'] if 'market_id' in df else ['source', 'market_series', 'interval_start']
    newest = df.sort_values('record_time').drop_duplicates(keys, keep='last') if 'record_time' in df else df.drop_duplicates(keys)
    return keys, newest[keys + [value]].rename(columns={value: 'price'})


def _balance_point(curve: pd.DataFrame | None) -> pd.Series | None:
    """The vertex row a signal curve marks as its balance point, or None when the curve has no marked one (an archive
    before schema version 6, a signal with nothing scheduled, no curve). A lone vertex is its own balance point, as it
    is to TransactiveSignal.balance_points_differ."""
    if curve is None or curve.empty:
        return None
    if len(curve) == 1:
        return curve.iloc[0]
    if 'balance_point' not in curve:
        return None
    marked = curve[curve['balance_point'].fillna(False).astype(bool)]
    return marked.iloc[0] if len(marked) == 1 else None


def balance_point_distance(sent: pd.Series, received: pd.Series, flexible: bool, quantity_only: bool = False) -> float:
    """
    TENT's relative distance between two balance points (TransactiveSignal._relative_distance), the received one already
    restated from the local side: the quantity difference relative to the average quantity (absolute, against a 1 kW
    scale, when the average is zero) and, when both curves express flexibility (`flexible`: more than one vertex each)
    at finite, non-zero prices and the market does not agree on the quantity alone (`quantity_only`, a
    MatchingConsensusMarket), the price difference relative to the average price, combined as a Euclidean distance. This
    is the number Neighbor.check_for_convergence compares with the neighbor's convergence threshold.
    """
    q1, q2 = float(sent['quantity']), float(received['quantity'])
    average = 0.5 * abs(q1 + q2)
    quantity_term = abs(q2 - q1) if average == 0 else abs(q2 - q1) / average
    price_term = 0.0
    if flexible and not quantity_only:
        p1, p2 = float(sent['price']), float(received['price'])
        average_price = 0.5 * abs(p1 + p2)
        if math.isfinite(p1) and math.isfinite(p2) and average_price != 0:
            price_term = abs(p2 - p1) / average_price
    return math.sqrt(quantity_term ** 2 + price_term ** 2)


def link_agreement(signals: pd.DataFrame, thresholds: dict | None = None, prices: pd.DataFrame | None = None,
                   market_types: dict | None = None) -> pd.DataFrame:
    """
    Where did each link end up, interval by interval? Per source/actor/run/market/neighbor/interval, the last sent and
    the last received signal (by signal time) and their curves compared from the local side (received quantities
    negated, see from_local_side). Three rules, named in `rule`, decide `within_threshold`; the first that applies:

    - 'balance_point', TENT's convergence test, when both signals mark their balance point for the interval (the
      `balance_point` flag of load_signals, archive schema version 6 on; a lone vertex is its own): `sent_balance_kw`
      is the exchange this node proposed, `received_balance_kw` the one the neighbor proposed, restated from the local
      side, `balance_gap_kw` their difference and `balance_distance` TENT's relative distance between the two points
      (balance_point_distance: quantity, and price when both curves have flexibility and the market does not agree on
      the quantity alone), which is what Neighbor.check_for_convergence compares with the neighbor's convergence
      threshold (a dimensionless fraction, Catalog.convergence_thresholds). `market_types` maps market_id to the
      market's type (Catalog.markets 'market_type'); a type in QUANTITY_ONLY_MARKET_TYPES leaves the price term out.
    - 'cleared_price', when a balance point is missing but `prices` are given (a MarginalPrice measurements frame,
      matched by market, or cleared_prices output, matched by series): `sent_kw` and `received_kw` are each curve's
      quantity at the market's cleared price and `gap_kw` their difference, the exchange the two sides would make at
      that price; `within_threshold` is |gap_kw| <= threshold. On a lossy or demand-charged link the balance-point price
      differs from the local one, so this gap can be large while TENT recorded consensus; it is a stand-in.
    - 'curve_gap' otherwise: `max_curve_gap`, the largest quantity gap between the curves over the union of their price
      breakpoints (as signal_pairs), tested against the threshold.

    The cleared-price columns and max_curve_gap are filled whatever the rule, when they can be. `within_threshold` is
    null without a threshold or without a pair of curves.
    """
    if signals.empty:
        return pd.DataFrame(columns=AGREEMENT_COLUMNS)
    sent, received = _last_signal_curves(signals, 'sent'), _last_signal_curves(signals, 'received')
    price_keys, price_table = _price_lookup(prices)
    keys = KEY + MARKET + ['neighbor_name', 'interval_start']
    sides = {'sent': sent, 'received': from_local_side(received) if not received.empty else received}
    groups = pd.concat([s[keys].drop_duplicates() for s in sides.values() if not s.empty]).drop_duplicates(keys)
    if price_table is not None:
        groups = groups.merge(price_table, on=price_keys, how='left')
    else:
        groups['price'] = np.nan
    # The curve rows of each side, keyed once: a lookup per group, not an index search.
    by_key = {d: ({k: part for k, part in s.groupby(keys, sort=False, dropna=False)} if not s.empty else {})
              for d, s in sides.items()}
    rows = []
    for _, g in groups.iterrows():
        key = tuple(g[k] for k in keys)
        row = dict(zip(keys, key)) | {'price': g['price']}
        curves, points = {}, {}
        for direction, table in by_key.items():
            curve = table.get(key)
            if curve is not None:
                curve = curve.sort_values(['price', 'quantity'])
            curves[direction] = curve
            points[direction] = _balance_point(curve)
            row[f'{direction}_time'] = curve['signal_time'].iloc[0] if curve is not None else pd.NaT
            row[f'{direction}_sequence'] = curve['sequence'].iloc[0] if curve is not None and 'sequence' in curve else None
            row[f'{direction}_kw'] = (quantity_at_price(curve, float(g['price']))
                                      if curve is not None and pd.notna(g['price']) else np.nan)
            row[f'{direction}_balance_kw'] = np.nan if points[direction] is None else float(points[direction]['quantity'])
        s, r = curves['sent'], curves['received']
        gap = np.nan
        if s is not None and r is not None:
            breakpoints = sorted(set(s['price']) | set(r['price']))
            gap = max(abs(quantity_at_price(s, p) - quantity_at_price(r, p)) for p in breakpoints)
        row['max_curve_gap'] = gap
        row['gap_kw'] = row['sent_kw'] - row['received_kw'] if pd.notna(row['sent_kw']) and pd.notna(row['received_kw']) else np.nan
        row['balance_gap_kw'] = row['balance_distance'] = np.nan
        if points['sent'] is not None and points['received'] is not None:
            row['balance_gap_kw'] = row['sent_balance_kw'] - row['received_balance_kw']
            quantity_only = (market_types or {}).get(g['market_id']) in QUANTITY_ONLY_MARKET_TYPES
            row['balance_distance'] = balance_point_distance(points['sent'], points['received'],
                                                             flexible=len(s) > 1 and len(r) > 1, quantity_only=quantity_only)
            rule, tested = 'balance_point', row['balance_distance']
        elif pd.notna(row['gap_kw']):
            rule, tested = 'cleared_price', abs(row['gap_kw'])
        else:
            rule, tested = 'curve_gap', gap
        row['rule'] = rule
        threshold = (thresholds or {}).get(g['neighbor_name'])
        row['threshold'] = np.nan if threshold is None else float(threshold)
        row['within_threshold'] = pd.NA if threshold is None or pd.isna(tested) else bool(tested <= threshold)
        rows.append(row)
    out = pd.DataFrame(rows, columns=AGREEMENT_COLUMNS)
    out['within_threshold'] = out['within_threshold'].astype('boolean')
    return out.sort_values(['source', 'actor', 'market_series', 'neighbor_name', 'interval_start']).reset_index(drop=True)


def consensus_summary(outcomes: pd.DataFrame, traffic: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    One row per source, actor and market series: markets, how many closed in consensus and how many without, how many
    were in full consensus at their latest record (late MarketLead passes included), the mean recorded passes, the
    median time to consensus (as a Timedelta and in minutes, over the markets that reached it) and the median fraction
    of the negotiation window it took, plus signals sent and received when `traffic` (signal_traffic output) is given.
    Keyed by source so compare_runs(summary, baseline=<label>, value='closed_in_consensus', keys=['actor',
    'market_series']) sets variants against a baseline archive.
    """
    if outcomes.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    group = ['source', 'actor', 'market_series']
    out = outcomes.groupby(group, sort=True, dropna=False).agg(
        markets=('market_id', 'size'), closed_in_consensus=('closed_in_consensus', 'sum'),
        final_consensus=('final_consensus', 'sum'), mean_passes=('passes', 'mean'),
        median_time_to_consensus=('time_to_consensus', 'median'),
        median_consensus_fraction=('consensus_fraction', 'median')).reset_index()
    for column in ('closed_in_consensus', 'final_consensus'):
        out[column] = out[column].astype(int)
    out['closed_without'] = out['markets'].astype(int) - out['closed_in_consensus']
    out['median_minutes_to_consensus'] = out['median_time_to_consensus'].dt.total_seconds() / 60.0
    if traffic is not None and not traffic.empty:
        totals = traffic.groupby(group, dropna=False)[['sent', 'received']].sum().reset_index() \
            .rename(columns={'sent': 'signals_sent', 'received': 'signals_received'})
        out = out.merge(totals, on=group, how='left')
        for column in ('signals_sent', 'signals_received'):
            out[column] = out[column].fillna(0).astype(int)
    else:
        out['signals_sent'] = out['signals_received'] = pd.array([pd.NA] * len(out), dtype='Int64')
    return out[SUMMARY_COLUMNS]
