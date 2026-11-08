import plotly.graph_objects as go
import pytest

from tent_analysis.analysis import cleared_prices, price_revisions, scheduled_power, net_power, energy_cost, aggregate_cost, compare_runs
from tent_analysis.data import load_measurements, load_state_changes, load_meter_readings
from tent_analysis.report import build_report, standard_figures
from tent_analysis.report.cli import main
from tent_analysis.viz import Theme, color_map, price_timeline, power_timeline, add_state_bands, cost_bars, run_comparison
import pandas as pd


def test_theme_and_color_map():
    light, dark = Theme('light'), Theme('dark')
    assert light.template().layout.plot_bgcolor == '#fcfcfb' and dark.template().layout.plot_bgcolor == '#1a1a19'
    names = ['Zeta', 'Alpha', 'Beta']
    cm = color_map(names)
    assert cm['Alpha'] == '#2a78d6' and cm['Beta'] == '#eb6834' and cm['Zeta'] == '#1baf7a', 'fixed order by name'
    assert color_map(['Alpha', 'Beta'])['Beta'] == cm['Beta'], 'a filter does not repaint survivors'
    many = color_map([f'e{i}' for i in range(10)])
    assert many['e8'] == many['e9'] == '#898781', 'beyond eight, Other grey'


def test_figures_build(source):
    latest = load_measurements(source, types=['MarginalPrice', 'ScheduledPower'])
    history = load_measurements(source, types=['MarginalPrice'], history=True)
    prices, power = cleared_prices(latest), scheduled_power(latest)
    fig = price_timeline(prices, revisions=price_revisions(history))
    assert isinstance(fig, go.Figure)
    lines = [t for t in fig.data if t.mode == 'lines']
    assert len(lines) == prices['market_series'].nunique() and all(t.line.width == 2 for t in lines)
    assert any(t.fill == 'toself' for t in fig.data), 'revision band present'
    assert fig.layout.template.layout.hovermode == 'x unified'
    fig2 = power_timeline(power, net=net_power(power), meters=load_meter_readings(source))
    assert len([t for t in fig2.data if t.name == 'Net scheduled']) == 1
    assert len(fig2.layout.annotations) >= 2, 'two titled rows, no second y scale on one axis'
    changes = load_state_changes(source)
    negotiated = changes[changes['state'] == 'Negotiation']['market_name'].iloc[0]
    before = len(fig.layout.shapes)
    assert add_state_bands(fig, changes, market_name=negotiated) is fig
    mine = changes[changes['market_name'] == negotiated].sort_values('record_time')
    expected = int(mine['state'].isin(['Negotiation', 'Delivery']).iloc[:-1].sum())  # the last stay has no end
    assert expected >= 1 and len(fig.layout.shapes) == before + expected
    assert {a.text for a in fig.layout.annotations} >= {'Negotiation'}
    assert len(add_state_bands(fig, changes, market_name='no such market').layout.shapes) == before + expected
    agg = aggregate_cost(energy_cost(power, prices), 'hour')
    bars = cost_bars(agg, 'hour')
    assert all(t.marker.line.width == 2 for t in bars.data)


def test_run_comparison_figure(source, variant):
    a = cleared_prices(load_measurements(source, types=['MarginalPrice']))
    b = cleared_prices(load_measurements(variant, types=['MarginalPrice']))
    cmp = compare_runs(pd.concat([a, b]), baseline='campus_a', value='price', keys=['market_series', 'interval_start'])
    fig = run_comparison(cmp, x='interval_start', unit='$/kWh')
    assert len(fig.data) == 1 and all(c == '#e34948' for c in fig.data[0].marker.color), 'all deltas positive -> red'


def test_report_and_cli(source, tmp_path, capsys):
    figures = standard_figures(source)
    assert {'Cleared price', 'Scheduled power', 'Cost by hour'} <= set(figures)
    out = build_report(figures, tmp_path / 'report.html', title='Test report')
    text = out.read_text(encoding='utf-8')
    assert text.count('plotly.js') + text.count('plotly-latest') + text.count('Plotly.newPlot') >= 1
    assert text.count('<h2') == len(figures)
    assert main(['inspect', source.url, '--label', 'x']) == 0
    assert 'Runs (1)' in capsys.readouterr().out
    assert main(['export', source.url, '--what', 'measurements', '--out', str(tmp_path / 'm.csv')]) == 0
    assert len(pd.read_csv(tmp_path / 'm.csv')) == len(load_measurements(source))
    assert main(['report', source.url, '--out', str(tmp_path / 'r.html'), '--mode', 'dark']) == 0
    assert 'wrote' in capsys.readouterr().out and (tmp_path / 'r.html').stat().st_size > 0


def test_export_every_kind_and_parquet(source, tmp_path):
    from tent_analysis.report.cli import EXPORTS
    from tent_analysis.data import load_curves, load_signals
    assert set(EXPORTS) == {'measurements', 'history', 'curves', 'signals', 'orders', 'state_changes', 'meter_readings',
                            'historian_topics', 'consensus'}
    for what in EXPORTS:
        assert main(['export', source.url, '--what', what, '--out', str(tmp_path / f'{what}.csv')]) == 0
    assert len(pd.read_csv(tmp_path / 'signals.csv')) == len(load_signals(source, history=True))
    assert pd.read_csv(tmp_path / 'historian_topics.csv').empty, 'no historian tables in this archive'
    assert main(['export', source.url, '--what', 'curves', '--out', str(tmp_path / 'curves.parquet')]) == 0
    back = pd.read_parquet(tmp_path / 'curves.parquet')
    curves = load_curves(source, history=True)
    assert len(back) == len(curves) and list(back.columns) == list(curves.columns)
    assert str(back['interval_start'].dt.tz) == 'UTC'


def test_report_images(source, tmp_path):
    from tent_analysis.report import write_images
    figures = dict(list(standard_figures(source).items())[:2])
    pngs = write_images(figures, tmp_path / 'png', 'png')
    assert [p.name[:3] for p in pngs] == ['01_', '02_'] and all(p.suffix == '.png' for p in pngs)
    assert all(p.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n' for p in pngs)
    pdfs = write_images(figures, tmp_path / 'pdf', 'pdf')
    assert all(p.read_bytes()[:4] == b'%PDF' for p in pdfs)
    with pytest.raises(ValueError, match='format'):
        write_images(figures, tmp_path, 'svg')


def test_report_cli_png(source, tmp_path, capsys, monkeypatch):
    from tent_analysis.report import cli
    two = dict(list(standard_figures(source).items())[:2])
    monkeypatch.setattr(cli, 'standard_figures', lambda source, theme, on_error=None: two)  # render two, not fourteen
    assert main(['report', source.url, '--out', str(tmp_path / 'figs'), '--format', 'png']) == 0
    files = sorted((tmp_path / 'figs').glob('*.png'))
    assert [f.name[:3] for f in files] == ['01_', '02_'] and all(f.stat().st_size > 1000 for f in files)
    assert 'wrote 2 png files' in capsys.readouterr().out


def test_report_skips_a_failing_page(source, tmp_path, capsys, monkeypatch):
    from tent_analysis.app.views import Views
    from tent_analysis.report import PAGES

    def broken(self):
        raise RuntimeError('markets exploded')

    monkeypatch.setattr(Views, 'markets', broken)
    with pytest.raises(RuntimeError, match='markets exploded'):
        standard_figures(source)
    seen = []
    figures = standard_figures(source, on_error=lambda page, error: seen.append((page, str(error))))
    assert seen == [('markets', 'markets exploded')] and 'Market states' not in figures and 'Cleared price' in figures
    assert 'markets' in PAGES
    assert main(['report', source.url, '--out', str(tmp_path / 'partial.html')]) == 1
    captured = capsys.readouterr()
    assert 'markets page failed and was skipped: RuntimeError: markets exploded' in captured.err
    assert (tmp_path / 'partial.html').stat().st_size > 0 and 'wrote' in captured.out
