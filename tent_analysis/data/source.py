"""A Source is one archive (database) opened for reading, with a label used throughout every DataFrame and figure."""
from dataclasses import dataclass, field
from functools import cached_property

from tent.data_manager.local_data_manager import LocalDataManager


@dataclass(frozen=True)
class Schema:
    """What this archive contains. TENT's own tables are assumed present; only optional extras are detected."""
    tables: frozenset[str]

    @property
    def has_historian_topics(self) -> bool:
        """VOLTTRON historian tables (topics/data) written into the same database."""
        return {'topics', 'data'} <= self.tables

    @property
    def has_meter_readings(self) -> bool:
        return 'meter_reading' in self.tables

    @property
    def has_orders(self) -> bool:
        return 'order' in self.tables


@dataclass
class Source:
    """`tz` overrides the zone the loaders convert archive times to; by default it is the zone the archive's runs were
    configured with (TransactiveNode.tz), which must agree across runs."""
    url: str
    label: str | None = None
    tz: str | None = None
    reader: LocalDataManager = field(default=None, repr=False)

    def __post_init__(self):
        if self.reader is None:
            self.reader = LocalDataManager.from_url(self.url)
        if self.label is None:
            self.label = self._default_label()

    def _default_label(self) -> str:
        tail = self.url.rsplit('/', 1)[-1]
        return tail.rsplit('.', 1)[0] if tail else self.url

    @cached_property
    def schema(self) -> Schema:
        return Schema(frozenset(self.reader.present_tables))

    @cached_property
    def catalog(self):
        from tent_analysis.data.catalog import Catalog
        return Catalog(self)
