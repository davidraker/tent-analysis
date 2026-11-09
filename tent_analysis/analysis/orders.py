"""Order-book transforms over load_orders output. DataFrame in, DataFrame out."""
import numpy as np
import pandas as pd

from tent_analysis.analysis.metrics import KEY

STATUS_ORDER = ['NEW', 'NEW_REVISED', 'STANDING_ORDER', 'REVISED_STANDING_ORDER', 'MATCHED', 'REVISED_MATCHED',
                'SUPERSEDED', 'CANCELLED', 'EXPIRED']
STANDING = ('STANDING_ORDER', 'REVISED_STANDING_ORDER')
MATCHED = ('MATCHED', 'REVISED_MATCHED')
BOOK_COLUMNS = KEY + ['order_id', 'record_time', 'status', 'side', 'actor_name', 'buyer_name', 'seller_name',
                      'parent_order_id', 'market_series', 'market_name', 'interval_start', 'price', 'quantity', 'vertices']
MATCHED_COLUMNS = KEY + ['market_series', 'market_name', 'interval_start', 'matched_kw', 'legs', 'match_price',
                         'standing_supply_kw', 'standing_demand_kw']
FILL_COST_COLUMNS = KEY + ['owner_name', 'market_series', 'market_name', 'interval_start', 'interval_end', 'legs',
                           'unpriced_legs', 'energy_kwh', 'cost']


def order_book(orders: pd.DataFrame) -> pd.DataFrame:
    """One row per order version: the order's quantity and price are those of its last vertex (the full quantity of a
    quote, the matched quantity and match price of a matched leg), `vertices` the vertex count."""
    if orders.empty:
        return pd.DataFrame(columns=BOOK_COLUMNS)
    keys = ['order_id', 'record_time']
    last = orders.sort_values(keys + ['record']).drop_duplicates(keys, keep='last')
    counts = orders.groupby(keys).size().rename('vertices').reset_index()
    return last.merge(counts, on=keys)[BOOK_COLUMNS].reset_index(drop=True)


def order_counts(book: pd.DataFrame) -> pd.DataFrame:
    """How many orders are in each state per market and interval? Counts the latest version of each order id."""
    columns = KEY + ['market_series', 'market_name', 'interval_start', 'status', 'orders']
    if book.empty:
        return pd.DataFrame(columns=columns)
    latest = book.sort_values('record_time').drop_duplicates('order_id', keep='last')
    out = latest.groupby(KEY + ['market_series', 'market_name', 'interval_start', 'status'], as_index=False).size() \
        .rename(columns={'size': 'orders'})
    out['status'] = pd.Categorical(out['status'], categories=STATUS_ORDER, ordered=True)
    return out.sort_values(['market_series', 'interval_start', 'status']).reset_index(drop=True)[columns]


def matched_quantities(book: pd.DataFrame) -> pd.DataFrame:
    """What traded, and what is still standing, per market and interval? matched_kw is the quantity of the matched
    supply legs (each match is a supply leg and a demand leg of equal size), legs their count, match_price the
    quantity-weighted mean of the finite match prices (NaN when every match price is infinite, as market orders
    against market orders are), and standing_*_kw the quantity still offered or wanted in standing orders."""
    if book.empty:
        return pd.DataFrame(columns=MATCHED_COLUMNS)
    latest = book.sort_values('record_time').drop_duplicates('order_id', keep='last')
    group = KEY + ['market_series', 'market_name', 'interval_start']
    rows = []
    for key, part in latest.groupby(group, sort=True):
        matched = part[part['status'].isin(MATCHED) & (part['side'] == 'SUPPLY')]
        finite = matched[np.isfinite(matched['price'].astype(float))]
        weight = finite['quantity'].abs()
        standing = part[part['status'].isin(STANDING)]
        rows.append(dict(zip(group, key)) | {
            'matched_kw': float(matched['quantity'].abs().sum()), 'legs': int(len(matched)),
            'match_price': float((finite['price'] * weight).sum() / weight.sum()) if weight.sum() > 0 else np.nan,
            'standing_supply_kw': float(standing.loc[standing['side'] == 'SUPPLY', 'quantity'].sum()),
            'standing_demand_kw': float(standing.loc[standing['side'] == 'DEMAND', 'quantity'].abs().sum())})
    return pd.DataFrame(rows, columns=MATCHED_COLUMNS)


def fill_cost(orders: pd.DataFrame) -> pd.DataFrame:
    """
    What did each actor pay or earn in a book market, from its fills? A book market's legs are filled at their own
    prices (the resting order's), not at one marginal price, so the settlement is the fills: per actor
    (`owner_name`, the order's actor), market and interval, over the latest version of each of its matched legs
    (statuses MATCHED and REVISED_MATCHED; a leg's quantity and price are those of its last vertex, the matched quantity
    at the match price, as order_book reads them), `energy_kwh` is the sum of quantity [kW] x the interval's duration [h]
    and `cost` the sum of -energy x price, the sign
    convention of metrics.energy_cost: a supply leg (positive quantity) earns, a negative cost; a demand leg (negative
    quantity) pays. A leg whose match price is infinite (market orders against market orders) has no money value: it
    counts in `legs` and `unpriced_legs` and in energy_kwh, not in cost. Over load_orders output (which carries the
    interval's end); the result feeds aggregate_cost like energy_cost output does.
    """
    if orders.empty:
        return pd.DataFrame(columns=FILL_COST_COLUMNS)
    versions = orders.sort_values(['order_id', 'record_time', 'record']).drop_duplicates(['order_id', 'record_time'], keep='last')
    newest = versions.groupby('order_id')['record_time'].transform('max')
    legs = versions[(versions['record_time'] == newest) & versions['status'].isin(MATCHED)].copy()
    if legs.empty:
        return pd.DataFrame(columns=FILL_COST_COLUMNS)
    hours = (legs['interval_end'] - legs['interval_start']).dt.total_seconds() / 3600.0
    legs['energy_kwh'] = legs['quantity'].astype(float) * hours
    price = legs['price'].astype(float)
    priced = np.isfinite(price)
    legs['leg_cost'] = (-legs['energy_kwh'] * price).where(priced, 0.0)
    legs['unpriced'] = ~priced
    group = KEY + ['owner_name', 'market_series', 'market_name', 'interval_start', 'interval_end']
    out = (legs.rename(columns={'actor_name': 'owner_name'}).groupby(group, sort=True, dropna=False)
           .agg(legs=('order_id', 'size'), unpriced_legs=('unpriced', 'sum'), energy_kwh=('energy_kwh', 'sum'),
                cost=('leg_cost', 'sum')).reset_index())
    out['legs'] = out['legs'].astype(int)
    out['unpriced_legs'] = out['unpriced_legs'].astype(int)
    return out[FILL_COST_COLUMNS].sort_values(['market_series', 'interval_start', 'owner_name']).reset_index(drop=True)
