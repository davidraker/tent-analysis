# tent-analysis

Analysis and visualisation for TENT archives: the SQLite or PostgreSQL databases a TENT node writes through its data
manager (the `lib-archivable` library), with or without a VOLTTRON SQL historian (`topics`/`data`) in the same database.

## Install

    pip install -e ../tns-utilities/lib-archivable -e ../TENT -e ".[dev]"

Extras: `export` (Kaleido, for `report --format png|pdf`; needs a Chrome or Chromium, see `plotly.io.get_chrome`),
`parquet` (pyarrow, for `export --out file.parquet`), `postgres` (psycopg), `app` (Streamlit, for `dashboard`),
`config` (jsonschema, for `tent-config` and the dashboard's Configuration mode). `dev` includes pytest and the
optional runtime dependencies so the whole test suite runs.

## Command line

    tent-analysis inspect   <url> [--label NAME]
    tent-analysis export    <url> --what WHAT --out FILE
    tent-analysis report    <url> --out FILE_OR_DIR [--format html|png|pdf] [--mode light|dark]
    tent-analysis dashboard <url> [<url> ...]

`<url>` is a SQLAlchemy URL (`sqlite:///path/to/archive.sqlite`, `postgresql+psycopg://user:password@host/db`).
Passwords are never printed or used in labels: the default label is the database name (file stem for SQLite), else
the host.

- `inspect` prints the archive schema version the database was written with (`tent.data_manager.ARCHIVE_SCHEMA_VERSION`
  of the writing TENT, from the `archive_schema` row of application `TENT`; version 1, without that row, is everything
  before 2 November 2026) and names any other application that writes into the database, then lists
  runs (with their configured time zone), components by type, measurement headers by owner type, and the order book's
  size and latest statuses when the archive has one. When the archive holds a consensus market's `FullConsensus`
  flags it ends with a consensus block: per actor and series, the markets that closed in consensus / without at the
  end of Negotiation, the mean recorded passes, the median time to full consensus, and the signals sent and received
  (see Consensus runs below for the definitions).
- `export` writes one tidy frame as CSV or Parquet. `--what` is one of `measurements` (latest version per interval),
  `history` (every version), `curves` (one row per vertex, every version, order book excluded), `signals` (sent and
  received transactive signals, one row per vertex, with the acknowledgement protocol's `sequence`, `in_reply_to` and
  `revised`), `orders` (every version of every order, one row per vertex), `state_changes`, `meter_readings`,
  `historian_topics` (decoded `topics`/`data` rows), `consensus` (one row per consensus market: how its negotiation
  closed, `consensus_outcomes` below).
- `report` builds every figure family the archive supports (overview, curves, negotiation, markets, orders, signals,
  bid position, costs, devices). Orders appear only for archives with a MatchingEngine order book that holds orders,
  devices only for archives with historian tables; the negotiation family carries the consensus figures (outcome
  timeline, time to consensus, recorded passes, signal traffic) only when the archive records `FullConsensus`.
  `html` writes one self-contained page; `png` and `pdf` write one
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
  before comparing it with the sent signal. `load_signals` returns the signals that have archived curves;
  `load_signal_headers` returns every signal as one row (direction, time, protocol header, market).
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

### Consensus runs

`tent_analysis.analysis.consensus_runs` reads how a ConsensusMarket (or MatchingConsensusMarket) negotiation went from
three archive records: the `FullConsensus` flags the market writes per interval at the end of every negotiation pass,
when Negotiation ends and after a late rebalance in MarketLead (load with `history=True`); the market state changes;
and the transactive signals with their acknowledgement header.

    from tent_analysis.analysis import consensus_outcomes, signal_traffic, link_agreement, consensus_summary, compare_runs
    full = load_measurements(source, types=['FullConsensus'], history=True)
    changes = load_state_changes(source)
    outcomes = consensus_outcomes(full, changes)                  # one row per market
    traffic = signal_traffic(load_signal_headers(source), changes)  # per market and neighbor
    summary = consensus_summary(outcomes, traffic)                # per actor and series

- `consensus_outcomes`: per market, the negotiation start and end (entering Negotiation and MarketLead), the recorded
  passes, the intervals, how many were in consensus at the close and whether the market `closed_in_consensus`, the
  late passes recorded in MarketLead and `final_consensus` at the latest record, the first time every interval was in
  consensus (`consensus_time`, `time_to_consensus` from the negotiation start, `consensus_fraction` of the window).
  "Closed in consensus" means every interval's flag was true at the record made when Negotiation ended: TENT writes
  that record just after the MarketLead state change, so the close record is the latest pass no later than one second
  (`PASS_GAP`) after it, and the latest record when no state changes are known. TENT archives a flag only when it
  changes, so `passes` counts the passes that moved some interval's flag, not every balancing pass; the signals sent
  per neighbor count the exchanges.
- `signal_traffic`: per market and neighbor, signals sent and received, each split into revised signals and
  acknowledgements (`revised` false: content unchanged, needs no reply; a null flag, from an archive before the
  protocol, counts as revised), first and last signal time, and `after_deadline` (signal time after the market entered
  MarketLead) when state changes are given. Count from `load_signal_headers`, one row per signal: TENT archives every
  signal's header but a curve only when its content differs from the last one archived for that neighbor, market and
  interval, so acknowledgements and repeated signals have no vertex rows and are missing from `load_signals`.
- `link_agreement`: per market, neighbor and interval, the last sent and last received signal compared from the local
  side. The archive numbers a curve's vertices in price order and does not mark the balance point a signal carried,
  so with `prices` (a MarginalPrice frame, matched by market) the curves are read at the market's cleared price
  (`sent_kw`, `received_kw`, `gap_kw`), and `max_curve_gap` is the largest quantity gap between them over the union of
  their breakpoints. This is not TENT's convergence test, which compares balance points whose price allows for losses
  and demand charges on the link: on a steep upstream curve the gap at the local price can be large while the market
  recorded consensus. `thresholds` (`Catalog.convergence_thresholds`) gives `within_threshold`.
- `consensus_summary`: per source, actor and series, the markets closed in consensus and without, `final_consensus`,
  mean passes, median time to consensus (and in minutes), median fraction of the window, signals sent and received.

Variants compare like any other metric frame: stack the summaries of two archives (a ConsensusMarket run as the
baseline, a MatchingConsensusMarket run as the variant) and call `compare_runs(summary, baseline='consensus',
value='closed_in_consensus', keys=['actor', 'market_series'])`, or `value='median_minutes_to_consensus'`,
`'mean_passes'`, `'signals_sent'`. The figures (`viz.consensus_runs`): `consensus_outcome_timeline` (one bar per market
along simulated time from negotiation start to end, status-coloured by how it closed, a diamond where full consensus
was first reached; one row per actor and series), `time_to_consensus` (minutes, or `value='passes'`, per market with
the sources side by side; a market that never reached consensus is marked at the top of its window) and
`signal_traffic_panel` (sent and received per market, revised and acknowledgements stacked). The dashboard's
Negotiation page shows the summary table and these figures when the archive records `FullConsensus`.

## Configuration tool

`tent-config` (extra `config`, which adds jsonschema) works on the node configuration files that
`TransactiveNode.configure_node` consumes, through TENT's configuration models (`tent.config`) and the JSON Schema TENT
builds from them (`tent.registry.configuration_schema`). The same logic drives the dashboard's Configuration mode.

    tent-config schema   [--out tent-node.schema.json]
    tent-config validate FILE [--construct] [--steps N] [--base-dir DIR]
    tent-config diff     OLD NEW
    tent-config template [SLOT CLASS] [--market-class-name CLASS] [--no-doc] [--out FILE] [--list-classes]
    tent-config new      --template campus|city|small-office|small-office-ilc [--no-doc] [--out FILE]
    tent-config add      FILE --list LIST --class CLASS [--market-class-name CLASS] [--name NAME] [--doc] [--out FILE]
    tent-config migrate  FILE [--out FILE]
    tent-config docs     --out DIR [--format md|rst]

- `schema` writes TENT's JSON Schema (draft 2020-12) of a whole node configuration, for editors and other tools.
- `validate` checks a file at three levels and prints `[level] path: message` lines. Structure: the schema, entry by
  entry, nested blocks (price model, managers, model-frame models) against their own class's variant. Semantics: the
  models themselves (`TransactiveNode.validate_configuration`, component by component) in their own words: duplicate
  names, zones, aware datetimes, enumeration names. Construction (`--construct`): a node is built with a
  `NullDataManager`; when the build fails, entries are added one at a time to attribute the failure (a name reference
  that matches nothing, a weather file that does not exist) to the entry whose addition breaks it. Relative paths in
  the file resolve against `--base-dir` (default: the working directory, as for a node). With `--steps N` the built
  node then runs N state-machine iterations with the clock set to its first market's transition times in turn, so an
  error a component raises only when the markets run is reported as `[construction] step k: ...`. Template
  placeholders still in the file (strings starting `<FILL:`) are reported first, as `[placeholders]`. Construction
  runs only when the earlier levels pass. A class from a package that is not installed here (the VOLTTRON agent's data
  manager, meter points and actuation managers) is reported as `[unchecked]`, not as a problem. The host application's
  keys (the agent reads and removes them before TENT sees the file: `db_topic`, `subscribe_all_platforms`, `topics`
  and `slow_iteration_warning` at the top level, and a transactive neighbor's `sender`, the `identity@instance` the
  platform must present for its signals) are stripped before checking in the same way and listed as `[host]` entries;
  the mapping comes from the agent package's `HOST_KEYS` when it is installed, else from the tool's own copy of it.
  Exit status 1 when there are problems.
- `diff` compares two configurations structurally, components matched by list and name, and classifies every change
  as `[in place]` (a running node takes it through `reconfigure`) or `[rebuild]` by the reconfigurable markers of the
  models: node settings, the data manager, components added or removed, class changes, market timing, model-frame
  membership and `demand_curve_points` rebuild; costs, limits, thresholds, loss factors, schedules and a model-frame
  asset's occupancy manager block do not. Numeric tables are summarised (`24 values, 1 differ`). Exit status 0 when
  every change is in place, 2 when the node must be rebuilt.
- `template` writes a new entry for a registered class (`tent-config template Neighbor BulkSupplierDC`,
  `tent-config template MarketSeries MarketSeries --market-class-name MatchingEngine`, or the whole node without
  arguments): every field at its default, required fields without a default as `null`, and the field descriptions
  (type, bounds, unit, default, whether reconfigurable) under the key `_doc`. JSON has no comments, so `_doc` is the
  documentation form; `validate`, `diff` and `migrate` strip it (`strip_docs`) before checking, and TENT itself does
  not accept it, so strip it before handing a file to a node. `--list-classes` lists the registered classes per slot.
- `new` writes a whole node configuration from a template: `campus` is TENT's sample configuration, `city`,
  `small-office` (direct-ratio actuation) and `small-office-ilc` (actuation through the ILC agent) are the
  configurations deployed in this repository's simulation containers, each with its deployment paths (the archive URL,
  the weather file) replaced by `<FILL: ...>` placeholders that `validate` lists until they are filled, and with
  `_doc` descriptions on the node and on every entry of a registered class.
- `add` appends a template entry for a registered class to a list of an existing file (`--list neighbors --class
  BulkSupplierDC --name BPA`; `--list data_manager` replaces the block), refuses a name the list already has, writes
  the file (or `--out`), and reports what the models still need (the new entry's required fields), exit status 1.
- `docs` writes the "Configuring a node" page generated from the schema: the node settings, then one section per
  component list and per nested slot with one subsection per registered class and a table of its fields (type, unit,
  default, bounds, description, whether a running node takes a change in place). Markdown by default, reStructuredText
  with `--format rst`; TENT's documentation carries the reStructuredText page under `docs/source/configuration/`.
- `migrate` rewrites the legacy spellings the models identify: the camelCase lists (`meterPoints`, `localAssets`,
  `markets`, `neighbours`, ...), `model_type` to `class_name`, `db_type` dropped from `db_params`, a
  `TemperatureForecastModel` location given as `""` made `null`. It then reports, in the models' words, what the file
  still gets wrong (an unknown key is named with the keys the class accepts). Exit status 1 when problems remain.

The dashboard's Configuration mode (sidebar, Mode) uploads a file or starts from the node template, edits one
component at a time in a form generated from the schema (bounds, enumerations, units and reconfigurability from the
field's schema; complex fields as JSON), shows the three-level validation of the whole file and the changes made in
the session with their classification, and downloads the result.

## Tests

    pytest tests -q

The fixtures build archives by driving TENT objects (`tests/conftest.py`): two auction series, a consensus series
whose market negotiates in two recorded passes (closing without consensus in one archive and in consensus in the
other) and exchanges signals ending in an acknowledgement, a MatchingEngine series with one negotiation pass of orders,
signals and meter readings; one archive configured in
`America/Los_Angeles` without an order book; and one with historian tables. The loaders, metrics, figures, report, CLI
and dashboard logic are exercised against real TENT output. The configuration tool is tested against TENT's sample
configuration, the configurations deployed in `containers/tent/*/agent_configs`, a template per registered class, the
four node templates, a legacy-spelled file, and the stepped construction check; the Configuration mode of the dashboard
runs headless through Streamlit's AppTest.
