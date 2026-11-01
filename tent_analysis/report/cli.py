"""Command line: inspect an archive, export tidy frames, write a report (HTML, or PNG/PDF per figure), open the
dashboard."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

from tent_analysis.data import (Source, load_measurements, load_curves, load_state_changes, load_meter_readings,
                                load_historian_topics, load_orders, load_signals, redact_url)
from tent_analysis.report.html import build_report, standard_figures
from tent_analysis.report.images import FORMATS, write_images
from tent_analysis.viz import Theme

EXPORTS = {'measurements': lambda s: load_measurements(s), 'history': lambda s: load_measurements(s, history=True),
           'curves': lambda s: load_curves(s, history=True), 'signals': lambda s: load_signals(s, history=True),
           'orders': lambda s: load_orders(s, history=True), 'state_changes': load_state_changes,
           'meter_readings': load_meter_readings, 'historian_topics': load_historian_topics}


def inspect(args) -> int:
    source = Source(args.url, label=args.label)
    cat = source.catalog
    print(f'Source: {source.label} ({redact_url(source.url)})')
    schema = source.reader.archive_schema_record()
    if schema is None:
        print('Archive schema version: 1 (no archive_schema table: written by a TENT before 2 November 2026)')
    else:
        print(f'Archive schema version: {schema["version"]} (TENT {schema["tent_version"]}, '
              f'created {schema["created"]})')
    print(f'Tables present: {len(source.schema.tables)} | historian topics: {source.schema.has_historian_topics} '
          f'| meter readings: {source.schema.has_meter_readings} | order book: {source.schema.has_orders}')
    runs = cat.runs
    print(f'\nRuns ({len(runs)}):')
    for _, r in runs.iterrows():
        print(f'  {r.actor:<24} run {r.run_id}  created {r.created}  simulation={r.simulation} '
              f'start={r.simulation_start_time}  tz={r.tz}')
    comps = cat.components
    if not comps.empty:
        print(f'\nComponents ({len(comps)}):')
        for t, grp in comps.groupby('type'):
            print(f'  {t:<28} {len(grp):>3}  {", ".join(sorted(map(str, grp["name"]))[:8])}')
    print(f'\nMarkets: {len(cat.markets)} | intervals: {len(cat.intervals)} | measurements: {len(cat.measurements)} '
          f'| orders: {cat.order_count}')
    if cat.order_count:
        from tent_analysis.analysis import order_book, order_counts
        from tent_analysis.data import load_orders
        counts = order_counts(order_book(load_orders(source)))
        by_status = counts.groupby('status', observed=True)['orders'].sum()
        print('Orders by latest status: ' + ', '.join(f'{status} {n}' for status, n in by_status.items()))
    with pd.option_context('display.width', 160, 'display.max_rows', 60):
        counts = cat.measurements.groupby(['owner_type', 'measurement_type']).size()
        if not counts.empty:
            print('\nMeasurement headers by owner type:')
            print(counts.to_string())
    return 0


def export(args) -> int:
    source = Source(args.url, label=args.label)
    df = EXPORTS[args.what](source)
    out = Path(args.out)
    if out.suffix.lower() == '.parquet':
        df.to_parquet(out, index=False)
    else:
        df.to_csv(out, index=False)
    print(f'wrote {len(df)} rows to {out}')
    return 0


def report(args) -> int:
    """Build every figure family; a family that fails is reported on stderr and skipped, and the exit status is 1 even
    though the report is written, so a script notices."""
    source = Source(args.url, label=args.label)
    theme = Theme(args.mode)
    failures = []

    def on_error(page, error):
        failures.append(page)
        print(f'{page} page failed and was skipped: {type(error).__name__}: {error}', file=sys.stderr)

    figures = standard_figures(source, theme, on_error=on_error)
    if not figures:
        print('no figures could be built: the archive holds no prices or scheduled powers', file=sys.stderr)
        return 1
    if args.format == 'html':
        out = build_report(figures, args.out, title=f'TENT analysis: {source.label}', theme=theme)
        print(f'wrote {len(figures)} figures to {out}')
    else:
        paths = write_images(figures, args.out, args.format)
        print(f'wrote {len(paths)} {args.format} files to {Path(args.out)}')
    return 1 if failures else 0


def dashboard(args) -> int:
    """Launch the Streamlit dashboard on the given archives. The URLs travel in the TENT_ANALYSIS_URLS environment
    variable, not on the Streamlit process's command line, where a password would be visible to every process list."""
    script = Path(__file__).resolve().parent.parent / 'app' / 'dashboard.py'
    env = dict(os.environ, TENT_ANALYSIS_URLS=';'.join(args.urls))
    return subprocess.call([sys.executable, '-m', 'streamlit', 'run', str(script)], env=env)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='tent-analysis', description='Analyse and visualise TENT archives.')
    sub = parser.add_subparsers(dest='command', required=True)
    for name, fn in (('inspect', inspect), ('export', export), ('report', report)):
        p = sub.add_parser(name)
        p.add_argument('url', help='SQLAlchemy URL, e.g. sqlite:///archive.sqlite or postgresql+psycopg://...')
        p.add_argument('--label', help='Label for this source in tables and legends (default: from the URL)')
        p.set_defaults(func=fn)
        if name == 'export':
            p.add_argument('--what', choices=sorted(EXPORTS), default='measurements')
            p.add_argument('--out', required=True, help='.csv or .parquet (parquet needs the parquet extra)')
        if name == 'report':
            p.add_argument('--out', required=True, help='HTML file to write, or a directory for png/pdf')
            p.add_argument('--format', choices=['html', *FORMATS], default='html',
                           help='html: one self-contained page; png/pdf: one file per figure (export extra)')
            p.add_argument('--mode', choices=['light', 'dark'], default='light')
    d = sub.add_parser('dashboard', help='Open the Streamlit dashboard on one or more archives')
    d.add_argument('urls', nargs='+')
    d.set_defaults(func=dashboard)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
