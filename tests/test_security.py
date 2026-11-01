"""Credentials stay out of output, archive names cannot inject markup into figures, unknown tables are not reflected."""
import shutil

import pandas as pd
import pytest
import sqlalchemy as sa

from tent_analysis.app.views import Selection, Views, source_labels
from tent_analysis.data import Source, default_label, load_historian_topics, redact_url
from tent_analysis.report import cli
from tent_analysis.viz import (consensus_strip, convergence_panel, cost_bars, curve_figure, device_panel, market_timeline,
                               negotiation_trace, power_timeline, price_timeline, run_comparison, text)

SECRET_URL = 'postgresql+psycopg://tent:s3cret@db.example:5432/central'


def test_url_redaction_and_default_label():
    assert redact_url(SECRET_URL) == 'postgresql+psycopg://tent:***@db.example:5432/central'
    assert default_label(SECRET_URL) == 'central'
    assert default_label('postgresql://tent:s3cret@db.example') == 'db.example', 'host when there is no database'
    assert default_label('sqlite:////tmp/archives/campus_a.sqlite') == 'campus_a'
    with pytest.raises(ValueError, match='pass label='):
        default_label('sqlite://')
    labelled = Source(SECRET_URL, reader=object())
    assert labelled.label == 'central' and 's3cret' not in repr(labelled) and '***' in repr(labelled)
    assert source_labels(['sqlite:///a/campus.sqlite', 'sqlite:///b/campus.sqlite', 'sqlite:///c/city.sqlite']) == \
        ['campus (1)', 'campus (2)', 'city']


def test_inspect_prints_the_redacted_url(source, capsys):
    assert cli.main(['inspect', source.url]) == 0
    out = capsys.readouterr().out
    assert f'Source: campus_a ({redact_url(source.url)})' in out and 'Runs (1)' in out


def test_dashboard_launcher_keeps_urls_off_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.subprocess, 'call', lambda argv, env=None: calls.append((argv, env)) or 0)
    assert cli.main(['dashboard', SECRET_URL, 'sqlite:///other.sqlite']) == 0
    (argv, env), = calls
    assert not any('s3cret' in a or '://' in a for a in argv), argv
    assert argv[1:4] == ['-m', 'streamlit', 'run'] and argv[4].endswith('dashboard.py')
    assert env['TENT_ANALYSIS_URLS'] == f'{SECRET_URL};sqlite:///other.sqlite'


EVIL = '<a href="https://example.invalid">click</a>'


def test_names_from_the_archive_are_escaped_in_figures():
    assert text(EVIL) == '&lt;a href="https://example.invalid"&gt;click&lt;/a&gt;'
    t = pd.to_datetime(['2017-07-01T00:00Z', '2017-07-01T01:00Z'])
    key = dict(source='s', actor='a', run_id='r')
    prices = pd.DataFrame({**key, 'market_series': EVIL, 'interval_start': t, 'interval_end': t + pd.Timedelta(hours=1),
                           'price': [0.05, 0.06], 'record_time': t})
    power = pd.DataFrame({**key, 'owner_name': EVIL, 'owner_type': 'neighbor', 'market_series': 'S', 'interval_start': t,
                          'interval_end': t + pd.Timedelta(hours=1), 'power_kw': [1.0, 2.0], 'record_time': t})
    history = pd.DataFrame({**key, 'owner_name': EVIL, 'owner_type': 'neighbor', 'market_series': 'S', 'measurement_type': 'MarginalPrice',
                            'interval_start': t[0], 'record_time': t, 'value': [0.05, 0.06]})
    conv = pd.DataFrame({**key, 'owner_name': EVIL, 'market_series': 'S', 'interval_start': t, 'revisions': [2, 3],
                         'converged': [True, False]})
    curve = pd.DataFrame({'owner_name': EVIL, 'curve_id': 'c', 'record_time': t[0], 'price': [0.02, 0.08],
                          'quantity': [-10.0, 10.0], 'continuity': True})
    cost = pd.DataFrame({'source': EVIL, 'hour': [1, 2], 'cost': [1.0, 2.0]})
    cmp = pd.DataFrame({'source': EVIL, 'baseline': EVIL + 'b', 'hour': [1], 'cost': [2.0], 'cost_baseline': [1.0],
                        'delta': [1.0], 'delta_pct': [100.0]})
    segments = pd.DataFrame({'source': 's', 'market_id': 'm', 'market_name': EVIL, 'market_series': 'S', 'state': 'Active',
                             'start': t[:1], 'end': t[1:], 'duration': [pd.Timedelta(hours=1)]})
    cons = pd.DataFrame({'source': 's', 'market_series': EVIL, 'interval_start': t, 'consensus': [True, False]})
    topics = pd.DataFrame({'source': 's', 'topic': f'campus/{EVIL}', 'ts': t, 'value': [1.0, 2.0]})
    figures = [price_timeline(prices), power_timeline(power), negotiation_trace(history, 'MarginalPrice', t[0]),
               convergence_panel(conv), curve_figure(curve), cost_bars(cost, 'hour'), run_comparison(cmp, x='hour'),
               market_timeline(segments), consensus_strip(cons), device_panel(topics)]
    for fig in figures:
        encoded = fig.to_json()
        assert '<a ' not in encoded and '<a href' not in encoded, fig.layout.title.text
        assert '&lt;a href' in encoded, 'the name is still there, as text'
    assert curve_figure(curve).layout.title.text == f'Supply/demand curve: {text(EVIL)}'
    assert market_timeline(segments).layout.yaxis.categoryarray == (text(EVIL),)


@pytest.fixture(scope='module')
def shared_db(source, tmp_path_factory):
    """The fixture archive plus a VOLTTRON historian (topics/data) and an unrelated table in the same database."""
    path = tmp_path_factory.mktemp('shared') / 'shared.sqlite'
    shutil.copy(source.url.removeprefix('sqlite:///'), path)
    engine = sa.create_engine(f'sqlite:///{path}')
    meta = sa.MetaData()
    topics = sa.Table('topics', meta, sa.Column('topic_id', sa.Integer, primary_key=True), sa.Column('topic_name', sa.Text))
    data = sa.Table('data', meta, sa.Column('ts', sa.DateTime), sa.Column('topic_id', sa.Integer), sa.Column('value_string', sa.Text))
    notes = sa.Table('secret_notes', meta, sa.Column('id', sa.Integer, primary_key=True), sa.Column('note', sa.Text))
    meta.create_all(engine)
    t0 = pd.Timestamp('2017-07-01T00:00Z')
    with engine.begin() as conn:
        conn.execute(topics.insert(), [{'topic_id': 1, 'topic_name': 'campus/building1/OutdoorAirTemperature'},
                                       {'topic_id': 2, 'topic_name': 'campus/building1/Status'}])
        conn.execute(data.insert(), [{'ts': (t0 + pd.Timedelta(minutes=10 * i)).to_pydatetime().replace(tzinfo=None),
                                      'topic_id': 1, 'value_string': str(20.0 + i)} for i in range(4)]
                     + [{'ts': t0.to_pydatetime().replace(tzinfo=None), 'topic_id': 2, 'value_string': '{"mode": "cool"}'}])
        conn.execute(notes.insert(), [{'id': 1, 'note': 'not for the analysis tool'}])
    return Source(f'sqlite:///{path}', label='shared')


def test_only_historian_tables_are_reflected(shared_db, source):
    assert shared_db.schema.has_historian_topics and 'secret_notes' in shared_db.schema.tables
    assert {'topics', 'data'} <= set(shared_db.reader.tables) and 'secret_notes' not in shared_db.reader.tables
    assert 'topics' not in source.reader.tables, 'nothing reflected when the historian tables are absent'
    frame = load_historian_topics(shared_db)
    assert list(frame.columns) == ['source', 'topic', 'ts', 'value'] and len(frame) == 5
    assert str(frame['ts'].dt.tz) == 'UTC' and frame['ts'].min() == pd.Timestamp('2017-07-01T00:00Z')
    temps = frame[frame['topic'].str.endswith('OutdoorAirTemperature')]
    assert list(temps['value']) == [20.0, 21.0, 22.0, 23.0], 'numeric strings decode to floats'
    assert frame[frame['topic'].str.endswith('Status')]['value'].iloc[0] == {'mode': 'cool'}, 'JSON decodes'
    assert load_historian_topics(shared_db, '%Temperature').topic.nunique() == 1
    devices = Views(shared_db, Selection()).devices()
    assert set(devices) == {'Device points'} and len(devices['Device points'].data) == 2
    assert load_historian_topics(source).empty
