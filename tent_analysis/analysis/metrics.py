"""Metrics and transforms: DataFrame in, DataFrame out. Each function is documented with the question it answers."""
import pandas as pd

KEY = ['source', 'actor', 'run_id']
PERIODS = {'hour': lambda s: s.dt.hour, 'weekday': lambda s: s.dt.day_name(), 'month': lambda s: s.dt.month_name(),
           'day': lambda s: s.dt.date}
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December']


def _latest_per_interval(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """The newest row (by record_time) per key, taken whole: a null in the newest row stays null rather than being
    filled from an older version, which is what groupby().last() would do column by column."""
    return df.sort_values('record_time').drop_duplicates(keys, keep='last').reset_index(drop=True)


def cleared_prices(measurements: pd.DataFrame) -> pd.DataFrame:
    """What price did each market series clear at, per delivery interval?
    Latest MarginalPrice version per (run, series, interval start); when two markets of a series cover the same
    interval (a refinement), the later record wins."""
    df = measurements[measurements['measurement_type'] == 'MarginalPrice']
    if df.empty:
        return pd.DataFrame(columns=KEY + ['market_series', 'interval_start', 'interval_end', 'price', 'record_time'])
    out = _latest_per_interval(df, KEY + ['market_series', 'interval_start'])
    return out.rename(columns={'value': 'price'})[KEY + ['market_series', 'interval_start', 'interval_end', 'price',
                                                          'record_time']].sort_values(['market_series', 'interval_start'])


def price_revisions(history: pd.DataFrame) -> pd.DataFrame:
    """How did the price for each interval evolve? Needs load_measurements(..., history=True).
    One row per (series, interval) with revision count, first, last, min, max."""
    df = history[history['measurement_type'] == 'MarginalPrice']
    if df.empty:
        return pd.DataFrame(columns=KEY + ['market_series', 'interval_start', 'revisions', 'first', 'last', 'min', 'max'])
    g = df.sort_values('record_time').groupby(KEY + ['market_series', 'interval_start'], sort=False)['value']
    out = g.agg(revisions='size', first='first', last='last', min='min', max='max').reset_index()
    return out


def scheduled_power(measurements: pd.DataFrame) -> pd.DataFrame:
    """What did each asset and neighbor schedule per interval? Latest ScheduledPower per owner and interval."""
    df = measurements[measurements['measurement_type'] == 'ScheduledPower']
    if df.empty:
        return pd.DataFrame(columns=KEY + ['owner_name', 'owner_type', 'market_series', 'interval_start',
                                           'interval_end', 'power_kw', 'record_time'])
    out = _latest_per_interval(df, KEY + ['owner_name', 'market_series', 'interval_start'])
    return out.rename(columns={'value': 'power_kw'})[KEY + ['owner_name', 'owner_type', 'market_series',
                                                            'interval_start', 'interval_end', 'power_kw',
                                                            'record_time']]


def net_power(power: pd.DataFrame) -> pd.DataFrame:
    """Does the schedule balance? Sum of scheduled power over owners per interval (should be ~0 for a balanced node)."""
    if power.empty:
        return pd.DataFrame(columns=KEY + ['market_series', 'interval_start', 'net_kw'])
    return (power.groupby(KEY + ['market_series', 'interval_start'], as_index=False)['power_kw'].sum()
            .rename(columns={'power_kw': 'net_kw'}))


def energy_cost(power: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Energy cost per owner and interval: power [kW] x duration [h] x cleared price [$/kWh].
    Positive power is generation (revenue, negative cost); loads carry negative power and positive cost."""
    columns = KEY + ['owner_name', 'market_series', 'interval_start', 'power_kw', 'price', 'energy_kwh', 'cost']
    if power.empty or prices.empty:
        return pd.DataFrame(columns=columns)
    merged = power.merge(prices[KEY + ['market_series', 'interval_start', 'price']],
                         on=KEY + ['market_series', 'interval_start'], how='inner')
    hours = (merged['interval_end'] - merged['interval_start']).dt.total_seconds() / 3600.0
    merged['energy_kwh'] = merged['power_kw'] * hours
    merged['cost'] = -merged['energy_kwh'] * merged['price']
    return merged[columns]


def aggregate_cost(cost: pd.DataFrame, by: str = 'hour') -> pd.DataFrame:
    """Cost summed by hour of day, weekday, month, or day, per source/run and owner (reproduces cost_analyzer)."""
    if by not in PERIODS:
        raise ValueError(f"by must be one of {sorted(PERIODS)}, got {by!r}")
    if cost.empty:
        return pd.DataFrame(columns=KEY + ['owner_name', by, 'cost', 'energy_kwh'])
    df = cost.copy()
    df[by] = PERIODS[by](pd.to_datetime(df['interval_start']))
    out = df.groupby(KEY + ['owner_name', by], as_index=False)[['cost', 'energy_kwh']].sum()
    order = {'weekday': WEEKDAYS, 'month': MONTHS}.get(by)
    if order:
        out[by] = pd.Categorical(out[by], categories=order, ordered=True)
        out = out.sort_values(KEY + ['owner_name', by])
    return out


def convergence(history: pd.DataFrame) -> pd.DataFrame:
    """How many revisions did each interval take, and did each neighbor converge? Needs history=True.
    Revisions counted over ScheduledPower per owner and interval; `converged` from the latest ConvergenceFlag."""
    columns = KEY + ['owner_name', 'market_series', 'interval_start', 'revisions', 'converged']
    if history.empty:
        return pd.DataFrame(columns=columns)
    sched = history[history['measurement_type'] == 'ScheduledPower']
    rev = (sched.groupby(KEY + ['owner_name', 'market_series', 'interval_start'], as_index=False)['record_time']
           .size().rename(columns={'size': 'revisions'}))
    flags = history[history['measurement_type'] == 'ConvergenceFlag']
    if not flags.empty:
        latest = _latest_per_interval(flags, KEY + ['owner_name', 'market_series', 'interval_start'])
        latest['converged'] = latest['value'].astype(float) >= 0.5
        rev = rev.merge(latest[KEY + ['owner_name', 'market_series', 'interval_start', 'converged']],
                        on=KEY + ['owner_name', 'market_series', 'interval_start'], how='left')
    else:
        rev['converged'] = pd.NA
    return rev[columns]


def compare_runs(frames: pd.DataFrame, baseline: str, value: str, keys: list[str]) -> pd.DataFrame:
    """Baseline versus variants: for each key, the baseline value, the variant value, and their difference.
    `frames` is any metric frame covering several sources (stacked); `baseline` is a source label."""
    columns = keys + ['source', 'baseline', value, f'{value}_baseline', 'delta', 'delta_pct']
    if frames.empty or baseline not in set(frames['source']):
        return pd.DataFrame(columns=columns)
    base = frames[frames['source'] == baseline][keys + [value]].rename(columns={value: f'{value}_baseline'})
    variants = frames[frames['source'] != baseline]
    out = variants.merge(base, on=keys, how='inner')
    out['baseline'] = baseline
    out['delta'] = out[value] - out[f'{value}_baseline']
    base = out[f'{value}_baseline'].astype(float)
    out['delta_pct'] = out['delta'] / base.where(base != 0) * 100.0  # NaN where the baseline is zero
    return out[columns]
