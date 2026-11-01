"""Curve, signal, consensus, and negotiation transforms (Phase 2). DataFrame in, DataFrame out."""
from datetime import datetime

import pandas as pd

POSITIONS = ['below', 'lower', 'midpoint', 'upper', 'above']  # ordered: where a schedule sits within its own curve


def latest_version(curves: pd.DataFrame, when: datetime | None = None) -> pd.DataFrame:
    """Keep one version per curve id: the newest, optionally no later than `when`."""
    if curves.empty:
        return curves
    df = curves if when is None else curves[curves['record_time'] <= when]
    newest = df.groupby('curve_id')['record_time'].transform('max')
    return df[df['record_time'] == newest]


def curve_at(curves: pd.DataFrame, owner: str, interval_start, when: datetime | None = None) -> pd.DataFrame:
    """The supply/demand curve (vertex rows sorted by price) of one owner for one interval, as of `when`."""
    df = curves[(curves['owner_name'] == owner) & (curves['interval_start'] == interval_start)]
    return latest_version(df, when).sort_values(['price', 'quantity']).reset_index(drop=True)


def quantity_at_price(curve: pd.DataFrame, price: float) -> float | None:
    """Quantity on a price-sorted curve at `price`: linear between vertices, clamped beyond the ends."""
    if curve.empty:
        return None
    pts = curve.sort_values(['price', 'quantity'])[['price', 'quantity']].to_numpy().tolist()
    if price <= pts[0][0]:
        return pts[0][1]
    if price >= pts[-1][0]:
        return pts[-1][1]
    for (p0, q0), (p1, q1) in zip(pts, pts[1:]):
        if p0 <= price <= p1:
            return q0 if p1 == p0 else q0 + (q1 - q0) * (price - p0) / (p1 - p0)
    return pts[-1][1]


def net_curve(curves: pd.DataFrame, market_series: str, interval_start, prices: pd.DataFrame | None = None,
              when: datetime | None = None) -> tuple[pd.DataFrame, dict | None]:
    """The market's aggregate SystemVertex curve for one interval and, if cleared prices are given, where the marginal
    price crosses it: {'price', 'quantity'}."""
    df = curves[(curves['measurement_type'] == 'SystemVertex') & (curves['market_series'] == market_series)
                & (curves['interval_start'] == interval_start)]
    curve = latest_version(df, when).sort_values(['price', 'quantity']).reset_index(drop=True)
    crossing = None
    if prices is not None and not curve.empty:
        p = prices[(prices['market_series'] == market_series) & (prices['interval_start'] == interval_start)]
        if not p.empty:
            price = float(p['price'].iloc[-1])
            crossing = {'price': price, 'quantity': quantity_at_price(curve, price)}
    return curve, crossing


def bid_position(power: pd.DataFrame, curves: pd.DataFrame) -> pd.DataFrame:
    """Where each scheduled power sits within its owner's own curve for that interval (project note
    visualization_ideas.rst): below the lowest vertex, lower half, midpoint, upper half, above the highest vertex."""
    out = power.copy()
    out['position'] = pd.NA
    if curves.empty:
        return out
    latest = latest_version(curves[curves['measurement_type'] == 'ActiveVertex'])
    bounds = latest.groupby(['owner_name', 'interval_start'])['quantity'].agg(['min', 'max']).reset_index()
    out = out.merge(bounds, on=['owner_name', 'interval_start'], how='left')

    def classify(row):
        lo, hi, q = row['min'], row['max'], row['power_kw']
        if pd.isna(lo):
            return pd.NA
        if hi == lo:
            return 'midpoint' if q == lo else ('below' if q < lo else 'above')
        if q < lo:
            return 'below'
        if q > hi:
            return 'above'
        frac = (q - lo) / (hi - lo)
        return 'lower' if frac < 0.45 else 'upper' if frac > 0.55 else 'midpoint'

    out['position'] = pd.Categorical(out.apply(classify, axis=1), categories=POSITIONS, ordered=True)
    return out.drop(columns=['min', 'max'])


def from_local_side(received: pd.DataFrame) -> pd.DataFrame:
    """A received signal stated from the local side. TENT archives a received signal as the remote node stated it,
    so its quantities carry the remote sign (the remote's import is the local export); negating them makes a received
    curve comparable with the sent one, as TransactiveSignal.balance_points_differ does before testing convergence."""
    return received.assign(quantity=-received['quantity'])


def signal_pairs(signals: pd.DataFrame, thresholds: dict | None = None) -> pd.DataFrame:
    """Sent versus received signal per neighbor and interval: latest curve each way, and the largest quantity gap
    between them across the union of their price breakpoints, with the received curve restated from the local side
    (see from_local_side). `thresholds` maps neighbor name to its convergence threshold [kW] to flag mismatches."""
    columns = ['source', 'neighbor_name', 'market_series', 'interval_start', 'sent_time', 'received_time',
               'sent_vertices', 'received_vertices', 'max_quantity_gap', 'exceeds_threshold']
    if signals.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for (source, neighbor, series, start), grp in signals.groupby(['source', 'neighbor_name', 'market_series',
                                                                   'interval_start'], sort=True):
        sides = {}
        for direction, part in grp.groupby('direction'):
            newest = part[part['record_time'] == part['record_time'].max()]
            if direction == 'received':
                newest = from_local_side(newest)
            sides[direction] = newest.sort_values(['price', 'quantity'])
        sent, received = sides.get('sent'), sides.get('received')
        gap = None
        if sent is not None and received is not None and not sent.empty and not received.empty:
            prices = sorted(set(sent['price']) | set(received['price']))
            gap = max(abs(quantity_at_price(sent, p) - quantity_at_price(received, p)) for p in prices)
        threshold = (thresholds or {}).get(neighbor)
        rows.append({'source': source, 'neighbor_name': neighbor, 'market_series': series, 'interval_start': start,
                     'sent_time': sent['signal_time'].iloc[0] if sent is not None and not sent.empty else pd.NaT,
                     'received_time': received['signal_time'].iloc[0] if received is not None and not received.empty else pd.NaT,
                     'sent_vertices': 0 if sent is None else len(sent),
                     'received_vertices': 0 if received is None else len(received),
                     'max_quantity_gap': gap,
                     'exceeds_threshold': (gap is not None and threshold is not None and gap > threshold)})
    return pd.DataFrame(rows, columns=columns)


def state_segments(state_changes: pd.DataFrame, until: datetime | None = None) -> pd.DataFrame:
    """One row per (market, state) stay: start, end (the next change, or `until`/the last known time), duration."""
    columns = ['source', 'market_id', 'market_name', 'market_series', 'state', 'start', 'end', 'duration']
    if state_changes.empty:
        return pd.DataFrame(columns=columns)
    df = state_changes.sort_values(['market_id', 'record_time']).copy()
    df['end'] = df.groupby('market_id')['record_time'].shift(-1)
    last = until if until is not None else df['record_time'].max()
    df['end'] = df['end'].fillna(last)
    df = df.rename(columns={'record_time': 'start'})
    df['duration'] = df['end'] - df['start']
    return df[columns]


def horizon_matrix(history: pd.DataFrame, measurement_type: str, owner: str | None = None, bins: int = 24,
                   value: str = 'value') -> pd.DataFrame:
    """Rows: delivery interval start; columns: record-time bins; cells: the value known at that time (forward-filled),
    showing how a forecast firms up as delivery approaches."""
    df = history[history['measurement_type'] == measurement_type]
    if owner is not None:
        df = df[df['owner_name'] == owner]
    if df.empty:
        return pd.DataFrame()
    df = df.sort_values('record_time')
    edges = pd.date_range(df['record_time'].min(), df['record_time'].max(), periods=bins + 1)
    if len(edges) < 2 or edges[0] == edges[-1]:
        edges = pd.DatetimeIndex([df['record_time'].min(), df['record_time'].max() + pd.Timedelta(seconds=1)])
    df['record_bin'] = pd.cut(df['record_time'], bins=edges, labels=edges[:-1], include_lowest=True)
    matrix = (df.groupby(['interval_start', 'record_bin'], observed=False)[value].last().unstack('record_bin')
              .ffill(axis=1))
    matrix.index.name = 'interval_start'
    matrix.columns = pd.to_datetime(matrix.columns.astype(str))
    return matrix


def consensus(measurements: pd.DataFrame) -> pd.DataFrame:
    """Full-consensus flag per market series and interval (latest version)."""
    df = measurements[measurements['measurement_type'] == 'FullConsensus']
    if df.empty:
        return pd.DataFrame(columns=['source', 'actor', 'run_id', 'market_series', 'interval_start', 'consensus', 'record_time'])
    out = df.sort_values('record_time').groupby(['source', 'actor', 'run_id', 'market_series', 'interval_start'],
                                                as_index=False, sort=False).last()
    out['consensus'] = out['value'].astype(float) >= 0.5
    return out[['source', 'actor', 'run_id', 'market_series', 'interval_start', 'consensus', 'record_time']]
