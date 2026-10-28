import plotly.graph_objects as go

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
    add_state_bands(fig, load_state_changes(source), market_name=str(source.catalog.markets['name'].iloc[0]))
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
    assert (tmp_path / 'm.csv').stat().st_size > 0
    assert main(['report', source.url, '--out', str(tmp_path / 'r.html'), '--mode', 'dark']) == 0
