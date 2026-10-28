"""Command line: inspect an archive, export tidy frames, or write an HTML report."""
import argparse
import sys
from pathlib import Path

import pandas as pd

from tent_analysis.data import Source, load_measurements, load_curves, load_state_changes, load_meter_readings
from tent_analysis.report.html import build_report, standard_figures
from tent_analysis.viz import Theme

EXPORTS = {'measurements': lambda s: load_measurements(s), 'history': lambda s: load_measurements(s, history=True),
           'curves': lambda s: load_curves(s, history=True), 'state_changes': load_state_changes,
           'meter_readings': load_meter_readings}


def inspect(args) -> int:
    source = Source(args.url, label=args.label)
    cat = source.catalog
    print(f'Source: {source.label} ({source.url})')
    print(f'Tables present: {len(source.schema.tables)} | historian topics: {source.schema.has_historian_topics} '
          f'| meter readings: {source.schema.has_meter_readings} | orders: {source.schema.has_orders}')
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
    print(f'\nMarkets: {len(cat.markets)} | intervals: {len(cat.intervals)} | measurements: {len(cat.measurements)}')
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
    source = Source(args.url, label=args.label)
    theme = Theme(args.mode)
    figures = standard_figures(source, theme)
    if not figures:
        print('no figures could be built: the archive holds no prices or scheduled powers', file=sys.stderr)
        return 1
    out = build_report(figures, args.out, title=f'TENT analysis: {source.label}', theme=theme)
    print(f'wrote {len(figures)} figures to {out}')
    return 0


def dashboard(args) -> int:
    """Launch the Streamlit dashboard on the given archives."""
    import os
    import subprocess
    from pathlib import Path as _P
    script = _P(__file__).resolve().parent.parent / 'app' / 'dashboard.py'
    env = dict(os.environ, TENT_ANALYSIS_URLS=';'.join(args.urls))
    return subprocess.call([sys.executable, '-m', 'streamlit', 'run', str(script), '--', *args.urls], env=env)


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
            p.add_argument('--out', required=True, help='.csv or .parquet')
        if name == 'report':
            p.add_argument('--out', required=True, help='HTML file to write')
            p.add_argument('--mode', choices=['light', 'dark'], default='light')
    d = sub.add_parser('dashboard', help='Open the Streamlit dashboard on one or more archives')
    d.add_argument('urls', nargs='+')
    d.set_defaults(func=dashboard)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
