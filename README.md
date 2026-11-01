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

- `inspect` lists runs (with their configured time zone), components by type, and measurement headers by owner type.
- `export` writes one tidy frame as CSV or Parquet. `--what` is one of `measurements` (latest version per interval),
  `history` (every version), `curves` (one row per vertex, every version), `signals` (sent and received transactive
  signals, one row per vertex), `state_changes`, `meter_readings`, `historian_topics` (decoded `topics`/`data` rows).
- `report` builds every figure family the archive supports (overview, curves, negotiation, markets, signals, bid
  position, costs, devices). `html` writes one self-contained page; `png` and `pdf` write one file per figure into the
  `--out` directory, numbered in report order. A figure family that fails is reported on stderr and skipped; the report
  is still written and the exit status is 1.
- `dashboard` opens the Streamlit app on one or more archives. The URLs are handed to the Streamlit process through
  the `TENT_ANALYSIS_URLS` environment variable (semicolon-separated), not on its command line. Sidebar: source, theme,
  market series, owners, delivery date range; the Curves page has an owner picker, the Signals page a neighbor picker,
  and clicking a point on an Overview timeline focuses that delivery interval for Curves and Negotiation. With several
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

## Tests

    pytest tests -q

The fixtures build archives by driving TENT objects (`tests/conftest.py`), including one configured in
`America/Los_Angeles` and one with historian tables, so the loaders, metrics, figures, report, CLI and dashboard
logic are exercised against real TENT output.
