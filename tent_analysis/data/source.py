"""A Source is one archive (database) opened for reading, with a label used throughout every DataFrame and figure."""
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import PurePath

from sqlalchemy.engine import make_url
from tent.data_manager.local_data_manager import LocalDataManager

HISTORIAN_TABLES = ('topics', 'data')  # VOLTTRON SQL historian tables that may share the database


def redact_url(url: str) -> str:
    """The URL with any password replaced by ***, for printing and labels."""
    return make_url(url).render_as_string(hide_password=True)


def default_label(url: str) -> str:
    """A label from the database name (file stem for SQLite, database for a server), else the host. Never the
    credentials: a URL without database or host has no safe label and must be labelled explicitly."""
    parsed = make_url(url)
    if parsed.database:
        return PurePath(parsed.database).stem
    if parsed.host:
        return parsed.host
    raise ValueError(f'no database or host in {redact_url(url)} to label the source by; pass label=')


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
        """A MatchingEngine's order-book table exists (it may still be empty; see Catalog.order_count)."""
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
            # Only TENT's own tables are registered; of anything else in the database, only the historian tables are
            # reflected, and only when present.
            self.reader = LocalDataManager.from_url(self.url, reflect_unknown=False)
            if set(HISTORIAN_TABLES) <= self.reader.present_tables:
                self.reader.reflect_unknown_tables(only=HISTORIAN_TABLES)
        if self.label is None:
            self.label = default_label(self.url)

    def __repr__(self) -> str:
        return f'Source(url={redact_url(self.url)!r}, label={self.label!r}, tz={self.tz!r})'

    @cached_property
    def schema(self) -> Schema:
        return Schema(frozenset(self.reader.present_tables))

    @cached_property
    def catalog(self):
        from tent_analysis.data.catalog import Catalog
        return Catalog(self)
