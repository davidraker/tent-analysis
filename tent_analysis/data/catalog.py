"""The entity graph of one Source, resolved once: runs, components with concrete types, series, markets, measurements."""
from functools import cached_property

import pandas as pd
from sqlalchemy import distinct, func, select

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

    def types_under(self, root: str) -> set[str]:
        """Concrete component type names whose table chain includes `root` ('market', 'local_asset', 'neighbor', ...)."""
        return {t for t in self.components['type'].unique() if root in self.reader.table_chain(t)}

    def _component_frame(self, type_name: str) -> pd.DataFrame:
        df = self.components
        return df[df['type'].isin(self.types_under(type_name))].copy()

    @cached_property
    def participants(self) -> pd.DataFrame:
        """Local assets and neighbors: the components that schedule power and own curves. Owner filters apply to their
        measurements; prices and other market-owned measurements are not owner-scoped."""
        df = self.components
        return df[df['type'].isin(self.types_under('local_asset') | self.types_under('neighbor'))].copy()

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

    @cached_property
    def order_count(self) -> int:
        """Distinct orders in the order book (0 without an order table). The order-book views exist only when > 0."""
        if not self.source.schema.has_orders:
            return 0
        order = self.reader.tables['order']
        return int(self.reader.execute(select(func.count(distinct(order.c.id)).label('n')))[0]['n'])

    @cached_property
    def convergence_thresholds(self) -> dict[str, float]:
        """Neighbor name -> its convergence threshold [kW], the tolerance within which a neighbor's signal counts as
        agreeing with this node's (Neighbor.convergence_threshold), for signal_pairs and link_agreement."""
        if not self.reader.has_table('neighbor'):
            return {}
        neighbor, component = self.reader.tables['neighbor'], self.reader.tables['component']
        rows = self.reader.execute(select(component.c.name, neighbor.c.convergence_threshold)
                                   .join(component, component.c.id == neighbor.c.id))
        return {str(r['name']): float(r['convergence_threshold']) for r in rows
                if r['convergence_threshold'] is not None and not pd.isna(r['convergence_threshold'])}

    @cached_property
    def run_labels(self) -> dict:
        """run_id -> actor label, for mapping a run_id column in one pass."""
        return {run_id: str(actor) for run_id, actor in zip(self.runs['run_id'], self.runs['actor'])}

    def run_label(self, run_id) -> str:
        return self.run_labels.get(run_id, str(run_id))

    @cached_property
    def run_zones(self) -> dict:
        """run_id -> IANA zone name the run was configured with (TransactiveNode.tz)."""
        return dict(zip(self.runs['run_id'], self.runs['tz']))

    @cached_property
    def tz(self) -> str:
        """The zone archive times are converted to: the source's override, else the single zone of the archive's runs.
        Runs configured with different zones have no common local time; pass Source(tz=...) to choose one."""
        if self.source.tz is not None:
            return self.source.tz
        zones = sorted({str(z) for z in self.run_zones.values() if z is not None and not pd.isna(z)})
        if len(zones) == 1:
            return zones[0]
        if not zones:
            raise ValueError(f'{self.source.label}: no run records a time zone; pass Source(tz=...)')
        raise ValueError(f'{self.source.label}: runs use different time zones ({", ".join(zones)}); pass Source(tz=...)')


def _enum_name(value):
    return getattr(value, 'name', value)
