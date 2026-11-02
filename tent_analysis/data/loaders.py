"""Tidy loaders. Every loader returns a long-format DataFrame with stable column names and carries `source`, `actor`
and `run_id` so frames from several sources can be concatenated and compared. Archive times are stored in UTC; every
time column a loader returns is converted to the source's zone (Catalog.tz: the runs' configured zone, or the
Source(tz=...) override), so hour-of-day and weekday bins and axis labels read in local time."""
import json
from datetime import datetime, timezone
from typing import Iterable, Sequence

import pandas as pd
from sqlalchemy import select

from tent_analysis.data.catalog import _enum_name
from tent_analysis.data.source import Source

MEASUREMENT_COLUMNS = ['source', 'actor', 'run_id', 'owner_id', 'owner_name', 'owner_type', 'measurement_id',
                       'measurement_type', 'unit', 'market_series', 'market_id', 'interval_id', 'interval_start',
                       'interval_end', 'record_time', 'value']


def _names(values) -> list | None:
    if values is None:
        return None
    return [getattr(v, 'name', v) for v in values]


def _utc(bound: datetime | None, name: str) -> datetime | None:
    """A time bound for the reader, in UTC: the archive stores UTC wall-clock values and SQLite compares them as text,
    so a bound in another zone must be converted first. Naive bounds are ambiguous and refused."""
    if bound is None:
        return None
    if bound.tzinfo is None:
        raise ValueError(f'{name} must be timezone-aware, got {bound!r}')
    return bound.astimezone(timezone.utc)


def _in_zone(df: pd.DataFrame, source: Source, columns: Sequence[str]) -> pd.DataFrame:
    """Convert the zoned time columns of a non-empty frame to the source's zone."""
    zone = source.catalog.tz
    for column in columns:
        df[column] = pd.to_datetime(df[column], utc=True).dt.tz_convert(zone)
    return df


def load_measurements(source: Source, types: Iterable = None, owners: Sequence[str] = None, start: datetime = None,
                      end: datetime = None, history: bool = False, as_of: datetime = None) -> pd.DataFrame:
    """
    Measurement values with their interval and owner. `types` are MeasurementType names or members; `owners` are
    component names. With history=False only the newest version per (measurement, interval) is returned.
    """
    catalog = source.catalog
    headers = catalog.measurements
    if headers.empty:
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)
    if types is not None:
        headers = headers[headers['measurement_type'].isin(_names(types))]
    if owners is not None:
        headers = headers[headers['owner_name'].isin(list(owners))]
    if headers.empty:
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)
    rows = source.reader.values(measurement_ids=list(headers['measurement_id']), start=_utc(start, 'start'),
                                end=_utc(end, 'end'), latest_only=not history, as_of=_utc(as_of, 'as_of'))
    if not rows:
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)
    df = pd.DataFrame(rows)
    df['interval_end'] = df['interval_start'] + df['interval_duration']
    markets = catalog.markets[['id', 'series_name']].rename(columns={'id': 'market_id', 'series_name': 'market_series'})
    df = df.merge(headers, on='measurement_id', how='left').merge(markets, on='market_id', how='left')
    df['source'] = source.label
    df['actor'] = df['run_id'].map(catalog.run_labels)
    df = _in_zone(df, source, ['interval_start', 'interval_end', 'record_time'])
    return df[MEASUREMENT_COLUMNS].sort_values(['owner_name', 'measurement_type', 'interval_start', 'record_time']) \
        .reset_index(drop=True)


CURVE_COLUMNS = ['source', 'actor', 'run_id', 'curve_id', 'record_time', 'owner_id', 'owner_name', 'owner_type',
                 'measurement_id', 'measurement_type', 'direction', 'market_series', 'market_id', 'interval_id',
                 'interval_start', 'record', 'price', 'quantity', 'cost', 'continuity', 'uncertainty']


def load_curves(source: Source, types: Iterable = None, owners: Sequence[str] = None, history: bool = False,
                as_of: datetime = None) -> pd.DataFrame:
    """One row per vertex, with its curve version and owner. `types` default to every vertex-type measurement except
    the order book (orders are curves too, but hundreds per market; load_orders returns them with their status); a
    filter that matches no measurement header yields an empty frame. `direction` is 'sent' or 'received' for the
    vertices of a transactive signal (quantities as archived, see load_signals) and null for every other curve."""
    catalog = source.catalog
    headers = catalog.measurements
    filtered = types is not None or owners is not None
    if types is not None:
        headers = headers[headers['measurement_type'].isin(_names(types))]
    else:
        headers = headers[headers['measurement_type'] != 'Order']
        filtered = filtered or (catalog.measurements['measurement_type'] == 'Order').any()
    if owners is not None:
        headers = headers[headers['owner_name'].isin(list(owners))]
    if headers.empty:
        return pd.DataFrame(columns=CURVE_COLUMNS)
    curves = source.reader.curves(measurement_ids=list(headers['measurement_id']) if filtered else None,
                                  latest_only=not history, as_of=_utc(as_of, 'as_of'))
    records = []
    for curve in curves:
        for v in curve['vertices']:
            records.append({'curve_id': curve['id'], 'record_time': curve['record_time'],
                            'owner_id': curve['owner_id'], 'measurement_id': curve['measurement_id'],
                            'market_id': curve['market_id'], 'interval_id': curve['time_interval_id'],
                            'interval_start': curve['interval_start'], 'record': v['record'], 'price': v['price'],
                            'quantity': v['quantity'], 'cost': v['cost'], 'continuity': v['continuity'],
                            'uncertainty': v['uncertainty']})
    if not records:
        return pd.DataFrame(columns=CURVE_COLUMNS)
    df = pd.DataFrame(records)
    df = df.merge(headers[['measurement_id', 'owner_name', 'owner_type', 'measurement_type', 'run_id']],
                  on='measurement_id', how='left')
    markets = catalog.markets[['id', 'series_name']].rename(columns={'id': 'market_id', 'series_name': 'market_series'})
    df = df.merge(markets, on='market_id', how='left')
    df['direction'] = df['measurement_id'].map(_signal_directions(source))
    df['source'] = source.label
    df['actor'] = df['run_id'].map(catalog.run_labels)
    df = _in_zone(df, source, ['interval_start', 'record_time'])
    return df[CURVE_COLUMNS].sort_values(['owner_name', 'interval_start', 'record_time', 'record']).reset_index(drop=True)


def _signal_directions(source: Source) -> dict:
    """transactive_signal id (the signal's measurement id) -> 'sent' | 'received'."""
    reader = source.reader
    if not reader.has_table('transactive_signal'):
        return {}
    sig = reader.tables['transactive_signal']
    return {row['id']: _enum_name(row['direction']) for row in reader.execute(select(sig.c.id, sig.c.direction))}


def load_state_changes(source: Source) -> pd.DataFrame:
    """Market state transitions with the market's series name."""
    df = pd.DataFrame(source.reader.state_changes())
    if df.empty:
        return pd.DataFrame(columns=['source', 'market_id', 'market_name', 'market_series', 'state', 'record_time'])
    markets = source.catalog.markets[['id', 'name', 'series_name']].rename(
        columns={'id': 'market_id', 'name': 'market_name', 'series_name': 'market_series'})
    df = df.merge(markets, on='market_id', how='left')
    df['state'] = df['state'].map(_enum_name)
    df['source'] = source.label
    df = _in_zone(df, source, ['record_time'])
    return df[['source', 'market_id', 'market_name', 'market_series', 'state', 'record_time']]


def load_meter_readings(source: Source, meters: Sequence[str] = None) -> pd.DataFrame:
    """Raw meter telemetry (meter_reading) with the meter's name, type, and unit."""
    columns = ['source', 'meter_id', 'meter_name', 'measurement_type', 'unit', 'reading_time', 'value']
    if not source.schema.has_meter_readings:
        return pd.DataFrame(columns=columns)
    reader = source.reader
    mr, mp, comp = reader.tables['meter_reading'], reader.tables['meter_point'], reader.tables['component']
    statement = (select(mr, comp.c.name.label('meter_name'), mp.c.measurement_type, mp.c.measurement_unit.label('unit'))
                 .join(mp, mp.c.id == mr.c.meter_id).join(comp, comp.c.id == mr.c.meter_id))
    if meters:
        statement = statement.where(comp.c.name.in_(list(meters)))
    df = pd.DataFrame(reader.execute(statement.order_by(mr.c.meter_id, mr.c.reading_time)))
    if df.empty:
        return pd.DataFrame(columns=columns)
    df['measurement_type'] = df['measurement_type'].map(_enum_name)
    df['unit'] = df['unit'].map(_enum_name)
    df['source'] = source.label
    df = _in_zone(df, source, ['reading_time'])
    return df[columns]


def load_historian_topics(source: Source, pattern: str = '%') -> pd.DataFrame:
    """VOLTTRON historian rows (topics/data) whose topic matches an SQL LIKE pattern; JSON values are decoded."""
    columns = ['source', 'topic', 'ts', 'value']
    if not source.schema.has_historian_topics:
        return pd.DataFrame(columns=columns)
    reader = source.reader
    topics, data = reader.tables['topics'], reader.tables['data']
    statement = (select(topics.c.topic_name.label('topic'), data.c.ts, data.c.value_string)
                 .join(topics, topics.c.topic_id == data.c.topic_id).where(topics.c.topic_name.like(pattern))
                 .order_by(topics.c.topic_name, data.c.ts))
    df = pd.DataFrame(reader.execute(statement))
    if df.empty:
        return pd.DataFrame(columns=columns)
    df['value'] = df['value_string'].map(_decode_historian_value)
    df['source'] = source.label
    df = _in_zone(df, source, ['ts'])
    return df[columns]


def _decode_historian_value(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        try:
            return float(text)
        except (TypeError, ValueError):
            return text


SIGNAL_COLUMNS = ['source', 'actor', 'run_id', 'signal_id', 'direction', 'signal_time', 'neighbor_id', 'neighbor_name',
                  'market_id', 'market_name', 'market_series', 'curve_id', 'record_time', 'interval_id',
                  'interval_start', 'record', 'price', 'quantity', 'cost', 'continuity', 'uncertainty']


def load_signals(source: Source, neighbors: Sequence[str] = None, history: bool = False) -> pd.DataFrame:
    """Transactive signals (sent and received) as one row per vertex, with neighbor, market, direction, signal time.
    Quantities are as archived: a received signal states the exchange from the remote side, so its sign is opposite to
    the local convention (analysis.curves.from_local_side restates it)."""
    reader = source.reader
    if not source.reader.has_table('transactive_signal'):
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    sig, meas, comp = reader.tables['transactive_signal'], reader.tables['measurement'], reader.tables['component']
    statement = (select(sig.c.id.label('signal_id'), sig.c.direction, sig.c.signal_time, sig.c.market_id,
                        meas.c.source_id.label('neighbor_id'), comp.c.name.label('neighbor_name'), comp.c.tn_id)
                 .join(meas, meas.c.id == sig.c.id).join(comp, comp.c.id == meas.c.source_id, isouter=True))
    if neighbors:
        statement = statement.where(comp.c.name.in_(list(neighbors)))
    headers = pd.DataFrame(reader.execute(statement))
    if headers.empty:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    headers['direction'] = headers['direction'].map(_enum_name)  # SignalDirection member -> 'sent' / 'received'
    curves = reader.curves(measurement_ids=list(headers['signal_id']), latest_only=not history)
    records = []
    for curve in curves:
        for v in curve['vertices']:
            records.append({'signal_id': curve['measurement_id'], 'curve_id': curve['id'],
                            'record_time': curve['record_time'], 'interval_id': curve['time_interval_id'],
                            'interval_start': curve['interval_start'], 'record': v['record'], 'price': v['price'],
                            'quantity': v['quantity'], 'cost': v['cost'], 'continuity': v['continuity'],
                            'uncertainty': v['uncertainty']})
    if not records:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    df = pd.DataFrame(records).merge(headers, on='signal_id', how='left')
    catalog = source.catalog
    markets = catalog.markets[['id', 'name', 'series_name']].rename(
        columns={'id': 'market_id', 'name': 'market_name', 'series_name': 'market_series'})
    df = df.merge(markets, on='market_id', how='left').rename(columns={'tn_id': 'run_id'})
    df['source'] = source.label
    df['actor'] = df['run_id'].map(catalog.run_labels)
    df = _in_zone(df, source, ['signal_time', 'interval_start', 'record_time'])
    return df[SIGNAL_COLUMNS].sort_values(['neighbor_name', 'interval_start', 'direction', 'record_time', 'record']) \
        .reset_index(drop=True)


# Most values bound into one IN (...) clause; SQLite accepts 999 in older builds and 32766 in newer ones.
IN_CHUNK = 500

ORDER_COLUMNS = ['source', 'actor', 'run_id', 'order_id', 'record_time', 'status', 'side', 'actor_name', 'buyer_name',
                 'seller_name', 'parent_order_id', 'time_stamp', 'expire_by', 'market_id', 'market_name', 'market_series',
                 'interval_id', 'interval_start', 'record', 'price', 'quantity', 'continuity']


def load_orders(source: Source, history: bool = False) -> pd.DataFrame:
    """
    The order book of every MatchingEngine market: one row per order vertex and version, with the order's status, side
    (supply or demand), actor, buyer and seller, parent order, and delivery interval. An order is archived each time its
    state changes (submitted, matched, superseded, left standing, cancelled, expired); with history=False only the
    newest version of each order id is returned. A matched leg carries one vertex: the matched quantity at the match
    price. Empty without an order table.
    """
    if not source.schema.has_orders:
        return pd.DataFrame(columns=ORDER_COLUMNS)
    reader = source.reader
    order, sd, ti, vx = reader.tables['order'], reader.tables['sd_curve'], reader.tables['time_interval'], reader.tables['vertex']
    comp = reader.tables['component']
    actor, buyer, seller = comp.alias('actor'), comp.alias('buyer'), comp.alias('seller')
    statement = (select(order.c.id.label('order_id'), order.c.record_time, order.c.status, order.c.supply_or_demand.label('side'),
                        actor.c.name.label('actor_name'), buyer.c.name.label('buyer_name'), seller.c.name.label('seller_name'),
                        order.c.parent_order_id, order.c.time_stamp, order.c.expire_by, ti.c.market_id,
                        sd.c.time_interval_id.label('interval_id'), ti.c.start_time.label('interval_start'))
                 .join(sd, (sd.c.id == order.c.id) & (sd.c.record_time == order.c.record_time))
                 .join(ti, ti.c.id == sd.c.time_interval_id)
                 .join(actor, actor.c.id == order.c.actor_id, isouter=True)
                 .join(buyer, buyer.c.id == order.c.buyer_id, isouter=True)
                 .join(seller, seller.c.id == order.c.seller_id, isouter=True))
    versions = pd.DataFrame(reader.execute(statement))
    if versions.empty:
        return pd.DataFrame(columns=ORDER_COLUMNS)
    if not history:
        newest = versions.groupby('order_id')['record_time'].transform('max')
        versions = versions[versions['record_time'] == newest]
    # One query per chunk of order ids: an IN list of every order of a negotiated archive (one book per balancing pass,
    # tens of thousands of orders) exceeds SQLite's bound-parameter limit.
    order_ids = list(versions['order_id'].unique())
    rows = []
    for start in range(0, len(order_ids), IN_CHUNK):
        rows.extend(reader.execute(
            select(vx.c.curve_id.label('order_id'), vx.c.curve_record_time.label('record_time'), vx.c.record, vx.c.price,
                   vx.c.quantity, vx.c.continuity).where(vx.c.curve_id.in_(order_ids[start:start + IN_CHUNK]))))
    vertices = pd.DataFrame(rows)
    df = versions.merge(vertices, on=['order_id', 'record_time'], how='inner')
    if df.empty:
        return pd.DataFrame(columns=ORDER_COLUMNS)
    for column in ('status', 'side'):
        df[column] = df[column].map(_enum_name)
    catalog = source.catalog
    markets = catalog.markets[['id', 'name', 'series_name', 'tn_id']].rename(
        columns={'id': 'market_id', 'name': 'market_name', 'series_name': 'market_series', 'tn_id': 'run_id'})
    df = df.merge(markets, on='market_id', how='left')
    df['source'] = source.label
    df['actor'] = df['run_id'].map(catalog.run_labels)
    df = _in_zone(df, source, ['record_time', 'time_stamp', 'expire_by', 'interval_start'])
    return df[ORDER_COLUMNS].sort_values(['market_series', 'interval_start', 'record_time', 'order_id', 'record']) \
        .reset_index(drop=True)
