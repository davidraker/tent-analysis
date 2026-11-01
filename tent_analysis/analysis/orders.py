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
