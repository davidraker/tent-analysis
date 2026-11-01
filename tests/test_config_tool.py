"""The configuration tool against TENT's sample configuration, the deployed container configurations and synthetic
files: schema export, three-level validation, diff classification, templates, migration, and the CLI."""
import glob
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tent_analysis.config import (DOC_KEY, checks, diff_configs, documents, load, migrate, node_schema,
                                  node_template, strip_docs, template, templates, write_schema)
from tent_analysis.config import schema as S
from tent_analysis.config.cli import main
from tent_analysis.config.diff import in_place
from tent_analysis.config.forms import FieldSpec, apply, field_specs, from_text, to_text

REPO = Path(__file__).resolve().parents[3]
TENT = REPO / 'code' / 'TENT'
SAMPLE = TENT / 'sample_configs' / 'tn.campus.json'
DEPLOYED = sorted(glob.glob(str(REPO / 'containers' / 'tent' / '*' / 'agent_configs' / 'tn.*.json')))


@pytest.fixture(scope='module')
def sample() -> dict:
    return load(SAMPLE)


# --- schema -------------------------------------------------------------------------------------------------------

def test_schema_export_is_tents_schema(tmp_path):
    out = write_schema(tmp_path / 'tent-node.schema.json')
    written = json.loads(out.read_text())
    assert written == node_schema() and written['$schema'].endswith('2020-12/schema')
    Draft202012Validator.check_schema(written)
    assert {'AnyNeighbor', 'AnyLocalAsset', 'AnyMarketSeries', 'AnyDataManager', 'AnyModel'} <= set(written['$defs'])
    assert main(['schema', '--out', str(tmp_path / 's.json')]) == 0 and (tmp_path / 's.json').stat().st_size > 10000


def test_variants_and_markers():
    neighbor = S.variant_for('Neighbor', {'class_name': 'Neighbor'})
    assert S.class_name_of(neighbor) == 'Neighbor' and S.is_reconfigurable(neighbor['properties']['loss_factor'])
    assert not S.is_reconfigurable(neighbor['properties']['transactive'])
    assert S.reference_list(neighbor['properties']['meter_points']) == 'meter_points'
    auction = S.variant_for('MarketSeries', {'class_name': 'MarketSeries', 'market_class_name': 'Auction'})
    assert auction['title'] == 'MarketSeries of Auction' and 'actuate_assets' in auction['properties']
    assert S.variant_for('MarketSeries', {'class_name': 'MarketSeries'})['title'] == 'MarketSeries of Market'
    assert S.variant_for('Neighbor', {'class_name': 'Nope'}) is None
    price_model = S.node_schema()['$defs']['AnyMarketSeries']['oneOf'][0]['properties']['price_model']
    assert S.spec_slot(price_model) == 'PriceModel' and S.default_class_of(price_model) == 'HourlyAveragePriceModel'
    assert S.variant_for('PriceModel', {'default_price': 0.1}, price_model)['title'] == 'HourlyAveragePriceModel'


def test_iter_specs_finds_nested_blocks(sample):
    asset = next(a for a in load(DEPLOYED[-1])['local_assets'] if a['class_name'] == 'ModelFrameAsset')
    variant = S.variant_for('LocalAsset', asset)
    found = {(path, slot) for path, slot, _, _ in S.iter_specs(asset, variant, '')}
    assert ('occupancy_manager', 'OccupancyManager') in found and ('actuation_manager', 'ActuationManager') in found
    assert ('model_configs.models[0]', 'Model') in found and len([f for f in found if f[1] == 'Model']) == len(asset['model_configs']['models'])


# --- validation ---------------------------------------------------------------------------------------------------

def test_sample_configuration_passes_every_level(sample):
    report = checks.validate(sample, construct=True, base_dir=TENT)
    assert report.ok and report.levels == ('placeholders', 'structure', 'semantics', 'construction') and not report.unchecked
    assert report.text() == '0 problems (placeholders, structure, semantics, construction checked)'


@pytest.mark.parametrize('path', DEPLOYED, ids=lambda p: Path(p).parent.parent.name)
def test_deployed_configurations_validate(path):
    config = load(path)
    assert checks.validate_semantics(config).ok, checks.validate_semantics(config).text()
    structure = checks.validate_structure(config)
    assert structure.ok, structure.text()  # the deployed files follow the schema exactly (weekday names lowercase)
    report = checks.validate(config)
    # The agent's per-neighbor sender key is stripped before TENT's models see the file, as the agent does.
    assert any('sender' in n for n in config['neighbors']), 'the deployed files carry the host key'
    assert all('sender' not in n for n in documents.prepared(config)['neighbors'])
    changed = deepcopy(config)
    changed['neighbors'][0]['sender'] = 'someone@elsewhere'
    sender_changes = diff_configs(config, changed)
    assert [(c.path, c.reconfigurable) for c in sender_changes] == ['neighbors[0].sender', None][0:1] + [None] if False else \
        [(c.reconfigurable, 'host application key' in c.reason) for c in sender_changes] == [(None, True)]
    # Classes from outside TENT (the VOLTTRON agent package and the volttron_archivable data manager) are reported as
    # unchecked here, never as problems.
    outside = ('transactive_node', 'volttron_archivable')
    assert all(any(name in (u.module_name or '') or name in u.reason for name in outside) for u in report.unchecked), \
        report.unchecked
    assert bool(report.unchecked) == any(name in Path(path).read_text() for name in outside)


def test_structure_reports_the_entry_path_and_the_schema_message(sample):
    broken = deepcopy(sample)
    broken['neighbors'][0]['loss_factor'] = 1.5
    broken['neighbors'][1]['up_or_down'] = 'sideways'
    broken['local_assets'][0]['no_such_key'] = 1
    broken['market_series'][1]['interval_duration'] = -5
    broken['meterPoints'] = []
    report = checks.validate_structure(broken)
    by_path = {p.path: p.message for p in report.problems}
    assert '1.5 is greater than or equal to the maximum of 1' in by_path['neighbors[0].loss_factor']
    assert "'sideways' is not one of" in by_path['neighbors[1].up_or_down']
    assert "'no_such_key' was unexpected" in by_path['local_assets[0]']
    assert '-5 is less than or equal to the minimum of 0' in by_path['market_series[1].interval_duration']
    assert "'meterPoints' was unexpected" in by_path[''] and "use 'meter_points'" in by_path['']
    assert all(p.level == 'structure' for p in report.problems) and len(report.problems) == 5


def test_structure_checks_nested_blocks_against_their_own_class(sample):
    broken = deepcopy(sample)
    broken['market_series'][0]['price_model'] = {'default_price': -1.0}            # the default class, no class_name
    broken['local_assets'][0]['bogus_block'] = {'class_name': 'X'}
    report = checks.validate_structure(broken)
    paths = {p.path: p.message for p in report.problems}
    assert '-1.0 is less than the minimum of 0' in paths['market_series[0].price_model.default_price']
    assert "'bogus_block' was unexpected" in paths['local_assets[0]']
    unregistered = deepcopy(sample)
    unregistered['neighbors'][0]['class_name'] = 'MyNeighbor'
    report = checks.validate_structure(unregistered)
    assert [p.path for p in report.problems] == ['neighbors[0]'] and 'not a registered Neighbor' in report.problems[0].message
    unregistered['neighbors'][0]['module_name'] = 'my_package.neighbors'
    report = checks.validate_structure(unregistered)
    assert report.ok and [(u.path, u.class_name) for u in report.unchecked] == [('neighbors[0]', 'MyNeighbor')]


def test_semantics_reports_the_models_words_per_component(sample):
    broken = deepcopy(sample)
    broken['neighbors'][0]['loss_factr'] = 0.1
    broken['market_series'][0]['delivery_anchor_time'] = 'noon'
    broken['local_assets'][1]['name'] = broken['local_assets'][0]['name']
    report = checks.validate_semantics(broken)
    assert [p.path for p in report.problems] == [''], 'duplicate names are a node-level rule and stop the component pass'
    assert 'duplicate names' in report.problems[0].message
    broken['local_assets'][1]['name'] = 'SolarPv'
    report = checks.validate_semantics(broken)
    by_path = {p.path: p.message for p in report.problems}
    assert by_path['neighbors[0]'].startswith("Neighbor 'CoR': unknown configuration key 'loss_factr'")
    assert 'delivery_anchor_time' in by_path['market_series[0]'] and set(by_path) == {'neighbors[0]', 'market_series[0]'}


def test_construction_attributes_the_failure_to_the_entry(sample):
    from tent.utils.timer import Timer
    clock = (Timer.simulation, Timer.sim_start_time, Timer.sim_one_hr_in_sec, Timer.created_time)
    broken = deepcopy(sample)
    broken['information_service_models'][0]['weather_file_path'] = '/nonexistent/weather.csv'
    report = checks.validate_construction(broken, base_dir=TENT)
    assert [p.path for p in report.problems] == ['information_service_models[0]']
    assert 'FileNotFoundError' in report.problems[0].message and '/nonexistent/weather.csv' in report.problems[0].message
    bad_reference = deepcopy(sample)
    bad_reference['local_assets'][0]['meter_points'] = ['NoSuchMeter']
    report = checks.validate_construction(bad_reference, base_dir=TENT)
    assert [p.path for p in report.problems] == ['local_assets[0]'] and 'NoSuchMeter' in report.problems[0].message
    assert (Timer.simulation, Timer.sim_start_time, Timer.sim_one_hr_in_sec, Timer.created_time) == clock, \
        'the construction check restores the process clock it found'


def test_validate_runs_construction_only_when_the_models_accept(sample):
    broken = deepcopy(sample)
    broken['neighbors'][0]['loss_factr'] = 0.1
    report = checks.validate(broken, construct=True, base_dir=TENT)
    assert report.levels == ('placeholders', 'structure', 'semantics') and not report.ok
    assert {p.level for p in report.problems} == {'structure', 'semantics'}


def test_docs_are_stripped_before_checking(sample):
    documented = deepcopy(sample)
    documented[DOC_KEY] = {'name': 'the node'}
    documented['neighbors'][0][DOC_KEY] = {'loss_factor': 'x'}
    assert checks.validate(documented).ok and DOC_KEY not in strip_docs(documented)['neighbors'][0]
    assert diff_configs(sample, documented) == []


# --- diff -------------------------------------------------------------------------------------------------------------

def test_diff_classifies_reconfigurable_and_rebuild_changes(sample):
    changed = deepcopy(sample)
    changed['neighbors'][0]['loss_factor'] = 0.07
    changes = diff_configs(sample, changed)
    assert [(c.path, c.kind, c.old, c.new, c.reconfigurable) for c in changes] == [('neighbors[0].loss_factor', 'changed', 0.03, 0.07, True)]
    assert in_place(changes) and str(changes[0]).startswith('[in place] neighbors[0].loss_factor')
    changed = deepcopy(sample)
    changed['market_series'][0]['interval_duration'] = 1800
    changes = diff_configs(sample, changed)
    assert [(c.path, c.reconfigurable, c.reason) for c in changes] == [('market_series[0].interval_duration', False, 'not reconfigurable')]
    assert not in_place(changes) and str(changes[0]).startswith('[rebuild]')
    assert diff_configs(sample, sample) == [] and in_place([])


def test_diff_node_settings_components_and_classes(sample):
    changed = deepcopy(sample)
    changed['tz'] = 'America/Los_Angeles'
    changed['neighbors'].append({'class_name': 'Neighbor', 'name': 'Extra'})
    del changed['local_assets'][1]
    changed['neighbors'][0]['class_name'] = 'BulkSupplierDC'
    changed['market_series'][1]['market_class_name'] = 'ConsensusMarket'
    changed['data_manager']['db_params']['url'] = 'sqlite:///other.sqlite'
    changed['mystery'] = 1
    changes = {c.path: c for c in diff_configs(sample, changed)}
    assert changes['tz'].reconfigurable is False and 'node settings' in changes['tz'].reason
    assert changes['neighbors[3]'].kind == 'added' and changes['neighbors[3]'].reconfigurable is False
    assert changes['local_assets[1]'].kind == 'removed' and changes['local_assets[1]'].old['name'] == 'SolarPv'
    assert changes['neighbors[0]'].reason == 'class changed' and changes['market_series[1]'].reason == 'class changed'
    assert changes['data_manager'].reconfigurable is False
    assert changes['mystery'].reconfigurable is None
    assert not in_place(list(changes.values()))


def test_diff_inside_a_model_frame_asset():
    base = load(DEPLOYED[-1])  # the ILC small office: a ModelFrameAsset with thermostats and lighting
    asset_index = next(i for i, a in enumerate(base['local_assets']) if a['class_name'] == 'ModelFrameAsset')
    asset = base['local_assets'][asset_index]
    model = next(m for m in asset['model_configs']['models'] if m['class_name'] == 'Thermostat')
    changed = deepcopy(base)
    changed_model = next(m for m in changed['local_assets'][asset_index]['model_configs']['models'] if m is not None and m['subscribe_key'] == model['subscribe_key'])
    changed_model['model_config']['rated_power'] = model['model_config']['rated_power'] + 1.0
    changed['local_assets'][asset_index]['occupancy_manager']['schedule']['Saturday'] = 'always_on'
    changes = diff_configs(base, changed)
    verdicts = {c.path.split('.', 1)[1]: c for c in changes}
    rated = next(c for p, c in verdicts.items() if p.endswith('model_config.rated_power'))
    assert rated.reconfigurable is True and rated.old + 1.0 == rated.new
    assert verdicts['occupancy_manager'].reconfigurable is True and 'reconfigurable' in verdicts['occupancy_manager'].reason
    assert in_place(changes)
    rebuild = deepcopy(base)
    rebuild['local_assets'][asset_index]['model_configs']['demand_curve_points'] = 3
    rebuild['local_assets'][asset_index]['model_configs']['models'][0]['subscribe_key'] = 'renamed'
    changes = diff_configs(base, rebuild)
    reasons = {c.path.split('.', 1)[1]: (c.kind, c.reconfigurable) for c in changes}
    assert reasons['model_configs.demand_curve_points'] == ('changed', False)
    assert {k for k, v in reasons.items() if v[0] in ('added', 'removed')} and not in_place(changes)
    coefficients = deepcopy(base)
    thermostat = next(m for m in coefficients['local_assets'][asset_index]['model_configs']['models'] if m['class_name'] == 'Thermostat')
    thermostat['model_config']['c3'][0] += 1.0
    changes = diff_configs(base, coefficients)
    assert len(changes) == 1 and changes[0].reconfigurable is True and '24 values, 1 differ' in changes[0].reason
    assert '[24 values]' in str(changes[0])


def test_diff_cli(sample, tmp_path):
    changed = deepcopy(sample)
    changed['neighbors'][0]['loss_factor'] = 0.07
    documents.dump(changed, tmp_path / 'b.json')
    assert main(['diff', str(SAMPLE), str(tmp_path / 'b.json')]) == 0
    changed['market_series'][0]['interval_duration'] = 1800
    documents.dump(changed, tmp_path / 'c.json')
    assert main(['diff', str(SAMPLE), str(tmp_path / 'c.json')]) == 2
    assert main(['diff', str(SAMPLE), str(SAMPLE)]) == 0


# --- templates ------------------------------------------------------------------------------------------------------

def _every_variant():
    for slot, titles in templates.slots().items():
        for title in titles:
            variant = S.registered_variants(slot)[title]
            yield slot, variant


def _example(prop: dict):
    """A value the property's schema accepts, for a required field the template leaves null."""
    prop = S.resolve(prop)
    if 'const' in prop:
        return prop['const']
    if 'enum' in prop:
        return prop['enum'][0]
    if 'anyOf' in prop:
        return _example(next(a for a in prop['anyOf'] if a.get('type') != 'null'))
    kind = prop.get('type')
    if kind == 'number':
        return float(prop.get('minimum', prop.get('exclusiveMinimum', 0)) or 0) + 1.0
    if kind == 'integer':
        return int(prop.get('minimum', 0)) + 1
    if kind == 'boolean':
        return True
    if kind == 'string':
        return 'sqlite:///x.sqlite' if 'url' in prop.get('description', '').lower() else 'x'
    if kind == 'array':
        return [_example(prop.get('items', {'type': 'number'})) for _ in range(prop.get('minItems', 1))]
    if kind == 'object' or 'properties' in prop:
        return {name: _example(sub) for name, sub in prop.get('properties', {}).items() if name in prop.get('required', [])}
    raise AssertionError(f'no example for {prop}')


def _fill(entry: dict, variant: dict) -> dict:
    """Required fields the template left null get a schema-accepted value, nested objects included."""
    for name in variant.get('required', []):
        prop = S.resolve(variant['properties'][name])
        if entry.get(name) is None:
            entry[name] = _example(prop)
        elif isinstance(entry[name], dict):
            nested = next((a for a in S.alternatives(prop) if 'properties' in a), None)
            if nested is not None:
                _fill(entry[name], nested)
    return entry


@pytest.mark.parametrize('slot,variant', list(_every_variant()), ids=lambda v: v if isinstance(v, str) else v['title'])
def test_template_per_registered_class_validates_once_filled(slot, variant):
    entry = template(slot, S.class_name_of(variant), S.market_class_name_of(variant))
    assert set(entry[DOC_KEY]) == set(S.settings_of(variant)), 'every setting is documented'
    optional_without_default = {k for k, p in S.settings_of(variant).items()
                                if 'default' not in p and 'const' not in p and k not in variant.get('required', [])}
    assert not (optional_without_default & set(entry)), 'an optional field without a default is left out'
    assert all('type ' in line for line in entry[DOC_KEY].values())
    filled = strip_docs(entry)
    assert DOC_KEY not in filled and filled['class_name'] == S.class_name_of(variant)
    nulls_before = [k for k in variant.get('required', []) if filled.get(k) is None]
    _fill(filled, variant)
    assert all(filled[k] is not None for k in nulls_before)
    errors = list(Draft202012Validator({**variant, '$defs': S.defs()}).iter_errors(filled))
    assert errors == [], [f'{list(e.absolute_path)}: {e.message}' for e in errors]
    if slot in S.SLOTS.values():
        node = node_template(doc=False)
        list_name = next(name for name, s in S.SLOTS.items() if s == slot)
        if list_name == 'data_manager':
            node['data_manager'] = filled
        else:
            filled['name'] = 'one'
            node[list_name] = [filled]
        report = checks.validate(node)
        assert report.ok, report.text()


def test_node_template_and_template_cli(tmp_path, capsys):
    node = node_template()
    assert node['data_manager'] == {'class_name': 'NullDataManager'} and node['neighbors'] == [] and node['tz'] == 'UTC'
    assert 'IANA zone' in node[DOC_KEY]['tz'] and checks.validate(node).ok
    assert main(['template', 'Neighbor', 'Neighbor', '--out', str(tmp_path / 'n.json')]) == 0
    assert capsys.readouterr().out.startswith('wrote ')
    neighbor = load(tmp_path / 'n.json')
    assert neighbor['class_name'] == 'Neighbor' and neighbor['loss_factor'] == 0.01 and 'reconfigurable in place' in neighbor[DOC_KEY]['loss_factor']
    assert main(['template', 'MarketSeries', 'MarketSeries', '--market-class-name', 'MatchingEngine', '--no-doc']) == 0
    series = json.loads(capsys.readouterr().out)
    assert series['market_class_name'] == 'MatchingEngine' and 'order_expiry' in series and DOC_KEY not in series
    assert main(['template']) == 0 and json.loads(capsys.readouterr().out)['data_manager']['class_name'] == 'NullDataManager'
    assert main(['template', '--list-classes']) == 0 and 'Neighbor: BulkSupplierDC, Neighbor, ResponseNeighbor' in capsys.readouterr().out
    with pytest.raises(ValueError, match='not a registered Neighbor'):
        template('Neighbor', 'Nope')
    assert main(['template', 'Neighbor']) == 2


# --- migration ------------------------------------------------------------------------------------------------------

LEGACY = {'name': 'Old', 'tz': 'UTC',
          'data_manager': {'model_type': 'LocalDataManager', 'db_params': {'url': 'sqlite:///x.sqlite', 'db_type': 'sqlite'}},
          'neighbours': [{'model_type': 'Neighbor', 'name': 'CoR', 'loss_factor': 0.02}],
          'informationServiceModels': [{'model_type': 'TemperatureForecastModel', 'name': 'TF', 'location': '',
                                        'weather_file_path': 'w.csv'}],
          'localAssets': [{'model_type': 'LocalAsset', 'name': 'L', 'default_power': -1.0}]}


def test_migrate_rewrites_the_legacy_spellings_the_models_identify():
    assert not checks.validate(LEGACY).ok
    migrated, notes = migrate(LEGACY)
    assert set(migrated) == {'name', 'tz', 'data_manager', 'neighbors', 'information_service_models', 'local_assets'}
    assert migrated['data_manager'] == {'class_name': 'LocalDataManager', 'db_params': {'url': 'sqlite:///x.sqlite'}}
    assert migrated['information_service_models'][0]['location'] is None
    assert all('model_type' not in entry for lst in ('neighbors', 'information_service_models', 'local_assets') for entry in migrated[lst])
    assert 'neighbours -> neighbors' in notes and 'data_manager.db_params: dropped db_type (never read by TENT)' in notes
    assert 'information_service_models[0]: location "" -> null' in notes
    assert checks.validate(migrated).ok, checks.validate(migrated).text()
    assert LEGACY['neighbours'][0]['model_type'] == 'Neighbor', 'the input is not modified'
    with pytest.raises(ValueError, match='both'):
        migrate({**LEGACY, 'neighbors': []})


def test_migrate_cli_reports_what_the_models_still_reject(tmp_path, capsys):
    documents.dump(LEGACY, tmp_path / 'legacy.json')
    assert main(['migrate', str(tmp_path / 'legacy.json'), '--out', str(tmp_path / 'new.json')]) == 0
    assert 'migrated neighbours -> neighbors' in capsys.readouterr().out and load(tmp_path / 'new.json')['neighbors']
    typo = deepcopy(LEGACY)
    typo['localAssets'][0]['defualt_power'] = 1.0
    documents.dump(typo, tmp_path / 'typo.json')
    assert main(['migrate', str(tmp_path / 'typo.json')]) == 1
    err = capsys.readouterr().err
    assert "unknown configuration key 'defualt_power'" in err and 'local_assets[0]' in err
    assert main(['migrate', str(SAMPLE)]) == 0 and 'nothing to migrate' in capsys.readouterr().out


# --- validate CLI -----------------------------------------------------------------------------------------------------

def test_validate_cli(tmp_path, capsys, sample):
    assert main(['validate', str(SAMPLE), '--construct', '--base-dir', str(TENT)]) == 0
    assert capsys.readouterr().out.strip() == '0 problems (placeholders, structure, semantics, construction checked)'
    broken = deepcopy(sample)
    broken['neighbors'][0]['loss_factor'] = 2.0
    documents.dump(broken, tmp_path / 'broken.json')
    assert main(['validate', str(tmp_path / 'broken.json')]) == 1
    out = capsys.readouterr().out
    assert '[structure] neighbors[0].loss_factor:' in out and '[semantics] neighbors[0]:' in out


# --- forms ------------------------------------------------------------------------------------------------------------

def test_field_specs_describe_a_variant():
    specs = {s.name: s for s in field_specs(S.variant_for('Neighbor', {'class_name': 'Neighbor'}))}
    assert 'class_name' not in specs and 'module_name' not in specs
    assert specs['loss_factor'] == FieldSpec('loss_factor', 'number', specs['loss_factor'].description, 0.01, False, True, False,
                                             minimum=0, exclusive_maximum=1)
    assert specs['up_or_down'].kind == 'enum' and 'upstream' in specs['up_or_down'].choices
    assert specs['transactive'].kind == 'boolean' and specs['friend'].reconfigurable is False
    assert specs['meter_points'].kind == 'json' and specs['meter_points'].nullable and specs['meter_points'].reference == 'meter_points'
    assert specs['demand_month'].kind == 'integer' and specs['demand_month'].nullable
    series = {s.name: s for s in field_specs(S.variant_for('MarketSeries', {'class_name': 'MarketSeries', 'market_class_name': 'Auction'}))}
    assert series['interval_duration'].unit == 'seconds' and series['delivery_anchor_time'].kind == 'text'
    assert from_text(to_text([1, 2])) == [1, 2] and from_text('null') is None
    with pytest.raises(ValueError, match='not valid JSON'):
        from_text('{')
    neighbor_specs = field_specs(S.variant_for('Neighbor', {'class_name': 'Neighbor'}))
    entry = {'class_name': 'Neighbor', 'loss_factor': 0.02}
    untouched = {spec.name: entry.get(spec.name, spec.default) for spec in neighbor_specs}
    assert apply(entry, untouched, neighbor_specs) == entry, 'an untouched form changes nothing'
    assert apply(entry, {**untouched, 'loss_factor': 0.05, 'friend': True}, neighbor_specs) == \
        {'class_name': 'Neighbor', 'loss_factor': 0.05, 'friend': True}
    assert apply(entry, {**untouched, 'loss_factor': None}, neighbor_specs)['loss_factor'] is None


# --- whole-node templates, add, steps, docs ---------------------------------------------------------------------------

from tent_analysis.config import PLACEHOLDER_PREFIX, add_entry, new, placeholders, template_names  # noqa: E402
from tent_analysis.config.docs import render, write_docs  # noqa: E402

WEATHER = TENT / 'sample_configs' / 'temperature_forecast_model' / 'pasco_2017_dry_bulb_temperature.csv'


def test_template_names():
    assert template_names() == ['campus', 'city', 'small-office', 'small-office-ilc']
    with pytest.raises(ValueError, match='not a template'):
        new('village')


@pytest.mark.parametrize('name', ['campus', 'city', 'small-office', 'small-office-ilc'])
def test_node_templates_validate_at_every_level_available_here(name):
    config = new(name)
    assert DOC_KEY in config and all(DOC_KEY in e for e in config['neighbors'] if e.get('class_name') == 'Neighbor')
    assert 'loss_factor' in config['neighbors'][0][DOC_KEY]
    plain = strip_docs(config)
    assert plain == new(name, doc=False)
    # Deployment paths are placeholders, which validate lists until they are filled; otherwise the models accept it.
    marked = placeholders(plain)
    assert {path for path, _ in marked} == {'data_manager.db_params.url', 'information_service_models[0].weather_file_path'}
    assert all(text.startswith(PLACEHOLDER_PREFIX) for _, text in marked)
    report = checks.validate(plain)
    assert [p.path for p in report.problems] == [path for path, _ in marked] and all(p.level == 'placeholders' for p in report.problems)
    assert checks.validate_structure(plain).ok and checks.validate_semantics(plain).ok
    outside = ('transactive_node',)
    assert all(any(n in (u.module_name or '') or n in u.reason for n in outside) for u in report.unchecked), report.unchecked
    plain['information_service_models'][0]['weather_file_path'] = str(WEATHER)
    plain['data_manager'] = {'class_name': 'NullDataManager'}
    if report.unchecked:  # the small offices name the VOLTTRON agent's classes, which cannot be built here
        assert not checks.validate(plain, construct=True).ok
        return
    stepped = checks.validate(plain, construct=True, steps=4)
    assert stepped.ok, stepped.text()
    assert stepped.levels == ('placeholders', 'structure', 'semantics', 'construction')


def test_new_cli(tmp_path, capsys):
    assert main(['new', '--template', 'city', '--out', str(tmp_path / 'city.json')]) == 0
    city = load(tmp_path / 'city.json')
    assert city['name'] == 'CoR' and DOC_KEY in city
    assert main(['validate', str(tmp_path / 'city.json')]) == 1
    out = capsys.readouterr().out
    assert '[placeholders] information_service_models[0].weather_file_path: still to fill: <FILL:' in out
    assert main(['new', '--template', 'campus', '--no-doc']) == 0
    assert DOC_KEY not in json.loads(capsys.readouterr().out)


def test_add_appends_a_named_entry_and_refuses_a_duplicate(sample):
    added = add_entry(sample, 'neighbors', 'BulkSupplierDC', name='BPA')
    assert [n['name'] for n in added['neighbors']] == ['CoR', 'SMALL_OFFICE_ILC', 'SMALL_OFFICE_DR', 'BPA']
    assert added['neighbors'][-1]['class_name'] == 'BulkSupplierDC' and DOC_KEY not in added['neighbors'][-1]
    assert len(sample['neighbors']) == 3, 'the input is not modified'
    assert checks.validate(added).ok
    with pytest.raises(ValueError, match="already has an entry named 'CoR'"):
        add_entry(sample, 'neighbors', 'Neighbor', name='CoR')
    with pytest.raises(ValueError, match='not a component list'):
        add_entry(sample, 'things', 'Neighbor')
    series = add_entry(sample, 'market_series', 'MarketSeries', 'MatchingEngine', name='Engine', doc=True)
    assert series['market_series'][-1]['market_class_name'] == 'MatchingEngine' and DOC_KEY in series['market_series'][-1]
    assert checks.validate(series).ok
    archive = add_entry(sample, 'data_manager', 'NullDataManager')['data_manager']
    assert archive['class_name'] == 'NullDataManager' and archive['archival_buffer_length'] == 10000 and DOC_KEY not in archive
    assert add_entry(sample, 'local_assets', 'SolarPvResource')['local_assets'][-1]['name'] == 'SolarPvResource'


def test_add_cli(sample, tmp_path, capsys):
    documents.dump(sample, tmp_path / 'c.json')
    assert main(['add', str(tmp_path / 'c.json'), '--list', 'neighbors', '--class', 'Neighbor', '--name', 'Extra']) == 0
    assert [n['name'] for n in load(tmp_path / 'c.json')['neighbors']][-1] == 'Extra'
    assert main(['add', str(tmp_path / 'c.json'), '--list', 'neighbors', '--class', 'Neighbor', '--name', 'Extra']) == 2
    assert "already has an entry named 'Extra'" in capsys.readouterr().err
    # A class with required fields: the entry is written and the problems to resolve are reported, exit 1.
    assert main(['add', str(tmp_path / 'c.json'), '--list', 'local_assets', '--class', 'OpenLoopLoadPredictor',
                 '--name', 'Loads', '--out', str(tmp_path / 'd.json')]) == 1
    err = capsys.readouterr().err
    assert 'local_assets[2]' in err and 'dow_intercept' in err
    assert load(tmp_path / 'd.json')['local_assets'][-1]['name'] == 'Loads' and len(load(tmp_path / 'c.json')['local_assets']) == 2


def test_steps_run_the_markets_and_catch_a_raising_component(sample, monkeypatch):
    from tent.market.auction import Auction
    report = checks.validate_construction(sample, base_dir=TENT, steps=4)
    assert report.ok, report.text()
    from tent.utils.timer import Timer
    seen = []
    original = Auction.events

    def events(self):  # every market's events runs once per iteration; the fault appears at the second instant
        seen.append(Timer.now())
        if Timer.now().hour == 1 and Timer.now().minute == 45:
            raise RuntimeError('the auction could not clear')
        return original(self)

    monkeypatch.setattr(Auction, 'events', events)
    report = checks.validate_construction(sample, base_dir=TENT, steps=4)
    assert [p.path for p in report.problems] == ['step 2'], report.text()
    assert 'RuntimeError: the auction could not clear' in report.problems[0].message and 'at 2017-07-01T01:45:00+00:00' in report.problems[0].message
    assert len({t for t in seen}) == 2, 'the first raising iteration ends the run: no later instant was reached'
    no_series = {**strip_docs(sample), 'market_series': []}
    report = checks.validate_construction(no_series, base_dir=TENT, steps=2)
    assert [(p.path, p.message) for p in report.problems] == [('step 1', 'the configuration has no market series to step')]


def test_validate_cli_steps(capsys):
    assert main(['validate', str(SAMPLE), '--steps', '3', '--base-dir', str(TENT)]) == 0
    assert 'construction checked' in capsys.readouterr().out


def test_docs_page_lists_every_registered_class(tmp_path):
    md = render('md')
    rst = render('rst')
    for slot in ('DataManager', 'MeterPoint', 'InformationServiceModel', 'LocalAsset', 'Neighbor', 'MarketSeries',
                 'PriceModel', 'Model', 'ActuationManager', 'OccupancyManager'):
        for variant in S.slot_variants(slot):
            assert f'### {variant["title"]}\n' in md and f'\n{variant["title"]}\n{"~" * len(variant["title"])}\n' in rst
    assert md.startswith('# Configuring a node\n') and rst.startswith('==================\nConfiguring a node\n==================\n')
    assert '| loss_factor | number | 0.01 | >= 0, < 1 |' in md and '| yes |' in md
    assert '``interval_duration``\n     - number [seconds]' in rst
    assert '| meter_points | array or null | None | names of meter_points |' in md
    assert '| up_or_down | one of "bidirectional", "downstream", "unknown", "upstream" |' in md
    sections = [line for line in md.splitlines() if line.startswith('## ')]
    assert len(sections) == 1 + len(S.SLOTS) + 4, 'node settings, data manager, five lists, four nested slots'
    assert write_docs(tmp_path / 'docs', 'rst').read_text() == rst and (tmp_path / 'docs' / 'configuring_a_node.rst').exists()
    assert main(['docs', '--out', str(tmp_path / 'md')]) == 0 and (tmp_path / 'md' / 'configuring_a_node.md').read_text() == md
    with pytest.raises(ValueError, match='format'):
        render('html')
