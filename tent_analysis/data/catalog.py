"""The entity graph of one Source, resolved once: runs, components with concrete types, series, markets, measurements."""
from functools import cached_property

import pandas as pd

from tent_analysis.data.source import Source


class Catalog:
    def __init__(self, source: Source):
        self.source = source
        self.reader = source.reader

    @cached_property
    def runs(self) -> pd.DataFrame:
        """One row per TransactiveNode process start. `actor` is the configured actor_id, else the node name."""
        df = pd.DataFrame(self.reader.runs())
        if df.empty:
            return pd.DataFrame(columns=['run_id', 'actor', 'name', 'actor_id', 'organization_id', 'created',
                                         'simulation', 'simulation_start_time', 'simulation_one_hour_in_seconds', 'tz'])
        df = df.rename(columns={'id': 'run_id'})
        df['actor'] = df['actor_id'].where(df['actor_id'].notna(), df['name'])
        return df

    @cached_property
    def components(self) -> pd.DataFrame:
        rows = self.reader.components()
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=['id', 'name', 'description', 'tn_id', 'type'])
        return df

    def _component_frame(self, type_name: str) -> pd.DataFrame:
        df = self.components
        return df[df['type'].apply(lambda t: type_name in self.reader.table_chain(t))].copy()

    @cached_property
    def market_series(self) -> pd.DataFrame:
        return self._component_frame('market_series')

    @cached_property
    def markets(self) -> pd.DataFrame:
        df = pd.DataFrame(self.reader.markets())
        if df.empty:
            return pd.DataFrame(columns=['id', 'name', 'tn_id', 'market_series_id', 'series_name', 'market_class',
                                         'delivery_start_time', 'market_type'])
        return df

    @cached_property
    def intervals(self) -> pd.DataFrame:
        df = pd.DataFrame(self.reader.intervals())
        if df.empty:
            return pd.DataFrame(columns=['id', 'name', 'market_id', 'start_time', 'duration'])
        df['end_time'] = df['start_time'] + df['duration']
        return df

    @cached_property
    def measurements(self) -> pd.DataFrame:
        """Measurement headers joined with owner (name, type) and run."""
        df = pd.DataFrame(self.reader.measurements())
        if df.empty:
            return pd.DataFrame(columns=['measurement_id', 'owner_id', 'owner_name', 'owner_type', 'run_id',
                                         'measurement_type', 'unit'])
        df = df.rename(columns={'id': 'measurement_id', 'source_id': 'owner_id', 'source_name': 'owner_name',
                                'tn_id': 'run_id', 'measurement_unit': 'unit'})
        types = self.components.set_index('id')['type'] if not self.components.empty else pd.Series(dtype=object)
        df['owner_type'] = df['owner_id'].map(types)
        df['measurement_type'] = df['measurement_type'].map(_enum_name)
        df['unit'] = df['unit'].map(_enum_name)
        return df

    def run_label(self, run_id) -> str:
        runs = self.runs
        row = runs[runs['run_id'] == run_id]
        return str(row['actor'].iloc[0]) if not row.empty else str(run_id)


def _enum_name(value):
    return getattr(value, 'name', value)
