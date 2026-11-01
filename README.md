# tent-analysis

Analysis and visualisation for TENT archives: the SQLite or PostgreSQL databases written by `tent.data_manager`, with
or without a VOLTTRON SQL historian (`topics`/`data`) in the same database.

## Install

    pip install -e ../TENT -e ".[dev]"

Extras: `export` (Kaleido, for `report --format png|pdf`; needs a Chrome or Chromium, see `plotly.io.get_chrome`),
`parquet` (pyarrow, for `export --out file.parquet`), `postgres` (psycopg), `app` (Streamlit, for `dashboard`).
`dev` includes pytest and the three optional runtime dependencies so the whole test suite runs.

## Command line

    tent-analysis inspect   <url> [--label NAME]
    tent-analysis export    <url> --what WHAT --out FILE
    tent-analysis report    <url> --out FILE_OR_DIR [--format html|png|pdf] [--mode light|dark]
    tent-analysis dashboard <url> [<url> ...]

`<url>` is a SQLAlchemy URL (`sqlite:///path/to/archive.sqlite`, `postgresql+psycopg://user:password@host/db`).
Passwords are never printed or used in labels: the default label is the database name (file stem for SQLite), else
the host.

- `inspect` prints the archive schema version the database was written with (`tent.data_manager.ARCHIVE_SCHEMA_VERSION`
  of the writing TENT; version 1, without an `archive_schema` table, is everything before 2 November 2026), then lists
  runs (with their configured time zone), components by type, measurement headers by owner type, and the order book's
  size and latest statuses when the archive has one.
- `export` writes one tidy frame as CSV or Parquet. `--what` is one of `measurements` (latest version per interval),
  `history` (every version), `curves` (one row per vertex, every version, order book excluded), `signals` (sent and
  received transactive signals, one row per vertex), `orders` (every version of every order, one row per vertex),
  `state_changes`, `meter_readings`, `historian_topics` (decoded `topics`/`data` rows).
- `report` builds every figure family the archive supports (overview, curves, negotiation, markets, orders, signals,
  bid position, costs, devices). Orders appear only for archives with a MatchingEngine order book that holds orders,
  devices only for archives with historian tables. `html` writes one self-contained page; `png` and `pdf` write one
  file per figure into the `--out` directory, numbered in report order. A figure family that fails is reported on
  stderr and skipped; the report is still written and the exit status is 1.
- `dashboard` opens the Streamlit app on one or more archives. The URLs are handed to the Streamlit process through
  the `TENT_ANALYSIS_URLS` environment variable (semicolon-separated), not on its command line. Sidebar: source, theme,
  market series, owners, delivery date range; the Curves page has an owner picker, the Signals page a neighbor picker,
  the Devices page a topic pattern, and clicking a point on an Overview timeline focuses that delivery interval for
  Curves and Negotiation. The Orders and Devices pages are listed only when the archive can fill them. With several
  archives a Compare page shows price and cost deltas against a chosen baseline.

## Library

Layers: `data` (sources, catalog, tidy loaders over TENT's `ArchiveReader`), `analysis` (DataFrame in, DataFrame
out), `viz` (pure Plotly figure builders, one theme), `app` (dashboard logic without Streamlit, and the Streamlit
script), `report` (HTML assembly, static images, the CLI).

    from tent_analysis.data import Source, load_measurements
    from tent_analysis.analysis import cleared_prices, scheduled_power, energy_cost
    source = Source('sqlite:///archive.sqlite')
    latest = load_measurements(source, types=['MarginalPrice', 'ScheduledPower'])
    cost = energy_cost(scheduled_power(latest), cleared_prices(latest))

Conventions the frames follow:

- Every loader frame carries `source` (the label), `actor` and `run_id`, so frames from several archives concatenate.
- Archive times are stored in UTC. Loaders convert every time column to the zone the archive's runs were configured
  with (`TransactiveNode.tz`); figures name that zone in their time-axis titles. Runs with different zones have no
  common local time: pass `Source(url, tz='America/Los_Angeles')` to choose one. Time bounds (`start`, `end`,
  `as_of`, `when`, `until`) must be timezone-aware.
- Quantities follow TENT's sign convention: generation and export positive, load and import negative. A received
  transactive signal is archived as the remote node stated it, so `load_signals` returns its quantities with the
  remote sign; `signal_pairs` and the signal comparison figure restate it from the local side (`from_local_side`)
  before comparing it with the sent signal.
- A curve is identified by its `curve_id`; `load_curves` carries `measurement_type` and, for signal vertices,
  `direction`. `curve_at` returns one owner's curve for one interval and raises when the owner has several distinct
  curves there (a neighbor's sent and received signals); `single_curve` keeps the newest version per curve id and lets
  a later market of the same series supersede an earlier one for the same interval.
- "Latest" always means the newest `record_time` per key, taken as a whole row.
- The order book (`load_orders`) is one row per order vertex and version; `order_book` collapses it to one row per
  version with the order's quantity and price taken from its last vertex (a matched leg's matched quantity and match
  price), `order_counts` counts latest statuses per market and interval, and `matched_quantities` reports matched
  kilowatts, legs, the quantity-weighted match price (NaN when every match price is infinite) and standing supply and
  demand.
- Historian topics are drawn one panel per point name (the topic's last path segment) with one line per device;
  topics whose values are not numeric are left out and counted in the figure title.

## Tests

    pytest tests -q

The fixtures build archives by driving TENT objects (`tests/conftest.py`): two auction series, a consensus series,
a MatchingEngine series with one negotiation pass of orders, signals and meter readings; one archive configured in
`America/Los_Angeles` without an order book; and one with historian tables. The loaders, metrics, figures, report, CLI
and dashboard logic are exercised against real TENT output.
