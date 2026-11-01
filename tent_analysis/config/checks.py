"""
Three-level validation of a node configuration, each level reporting path -> message:

1. structure: the JSON Schema TENT builds from its models (types, bounds, enumerations, unknown keys), entry by entry
   so that every message carries the entry's path and nested blocks are checked against their own class's variant;
2. semantics: TENT's models themselves (TransactiveNode.validate_configuration, component by component), which add the
   cross-field rules (duplicate names, zones, aware datetimes, a price model's completeness) in the models' own words;
3. construction: a node is built from the configuration with a NullDataManager, so constructor-time failures (a name
   reference that matches nothing, a weather file that does not exist) surface here, attributed to the entry whose
   addition breaks the build.

A class the configuration names from a package that is not installed here (the VOLTTRON agent's data manager, meter
points and actuation managers) cannot be checked: it is reported as unchecked, not as a problem, and the rest of the
file is still checked. Keys the host application reads and removes before TENT sees the configuration (a neighbor's
sender, see documents.HOST_KEYS) are stripped first, as the host does.
"""
import contextlib
import logging
import os
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from tent import registry
from tent.config import ConfigurationError
from tent.transactive_node import LEGACY_NODE_KEYS, TransactiveNode
from tent.utils.timer import Timer

from tent_analysis.config import schema as S
from tent_analysis.config.documents import placeholders, prepared, strip_docs

LEVELS = ('placeholders', 'structure', 'semantics', 'construction')


@dataclass(frozen=True)
class Problem:
    level: str
    path: str      # '' for the node itself, else 'neighbors[0]', 'local_assets[1].model_configs.models[0].rated_power'
    message: str

    def __str__(self) -> str:
        return f'[{self.level}] {self.path or "node"}: {self.message}'


@dataclass(frozen=True)
class Unchecked:
    path: str
    class_name: str
    module_name: str | None
    reason: str

    def __str__(self) -> str:
        where = f'{self.class_name}' + (f' from {self.module_name}' if self.module_name else '')
        return f'[unchecked] {self.path or "node"}: {where}: {self.reason}'


@dataclass
class Report:
    problems: list[Problem] = field(default_factory=list)
    unchecked: list[Unchecked] = field(default_factory=list)
    levels: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    def extend(self, other: 'Report') -> 'Report':
        self.problems.extend(other.problems)
        seen = {(u.path, u.class_name, u.module_name) for u in self.unchecked}
        self.unchecked.extend(u for u in other.unchecked if (u.path, u.class_name, u.module_name) not in seen)
        self.levels = tuple(dict.fromkeys(self.levels + other.levels))
        return self

    def text(self) -> str:
        lines = [str(p) for p in self.problems] + [str(u) for u in self.unchecked]
        summary = (f'{len(self.problems)} problem{"s" if len(self.problems) != 1 else ""}'
                   f' ({", ".join(self.levels)} checked)'
                   + (f', {len(self.unchecked)} unchecked' if self.unchecked else ''))
        return '\n'.join(lines + [summary])


# ---------------------------------------------------------------------------------------------- level 0: placeholders

def validate_placeholders(config: dict) -> Report:
    """Every template placeholder (documents.PLACEHOLDER_PREFIX) still in the file: a path or URL of the deployment
    that must be filled before the configuration is complete."""
    report = Report(levels=('placeholders',))
    for path, text in placeholders(strip_docs(config)):
        report.problems.append(Problem('placeholders', path, f'still to fill: {text}'))
    return report


# ------------------------------------------------------------------------------------------------- level 1: structure

def validate_structure(config: dict) -> Report:
    """The configuration against TENT's JSON Schema, entry by entry (see module docstring)."""
    config = prepared(config)
    report = Report(levels=('structure',))
    node = {key: ([] if key in S.NODE_LISTS else None if key == 'data_manager' else value)
            for key, value in config.items()}
    for error in _errors(S.node_schema(), node):
        message = error.message
        for key in LEGACY_NODE_KEYS:
            if f"'{key}'" in message:
                message += f'; use {LEGACY_NODE_KEYS[key]!r}'
        report.problems.append(Problem('structure', _json_path(error.absolute_path), message))
    if isinstance(config.get('data_manager'), dict):
        _check_entry(config['data_manager'], 'DataManager', 'data_manager', S.node_schema()['properties']['data_manager'], report)
    for list_name, slot in S.NODE_LISTS.items():
        entries = config.get(list_name)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            path = S.entry_path(list_name, index)
            if not isinstance(entry, dict):
                report.problems.append(Problem('structure', path, f'an entry is an object with class_name, not {type(entry).__name__}'))
                continue
            _check_entry(entry, slot, path, S.node_schema()['properties'][list_name], report)
    return report


def _check_entry(spec: dict, slot: str, path: str, slot_fragment: dict, report: Report) -> None:
    """Validate one class specification against its variant, then its nested specifications against theirs."""
    variant = S.variant_for(slot, spec, slot_fragment)
    class_name = spec.get('class_name') or S.default_class_of(slot_fragment)
    if variant is None:
        if not class_name:
            report.problems.append(Problem('structure', path, f'class_name is required for a {slot}'))
        elif spec.get('module_name'):
            report.unchecked.append(Unchecked(path, class_name, spec.get('module_name'),
                                              f'not a class registered for {slot} slots; not checked against the schema'))
        else:
            names = sorted({S.class_name_of(v) for v in S.slot_variants(slot)})
            detail = ''
            if slot == 'MarketSeries' and spec.get('market_class_name'):
                markets = sorted({S.market_class_name_of(v) for v in S.slot_variants(slot)})
                detail = f' with market_class_name {spec["market_class_name"]!r} (registered markets: {markets})'
            report.problems.append(Problem('structure', path, f'class_name {class_name!r}{detail} is not a registered '
                                                              f'{slot} (registered: {names}); give module_name for a '
                                                              f'class from another package'))
        return
    # Nested specifications are validated on their own, so the parent is checked without them.
    pruned = deepcopy(spec)
    nested = list(S.iter_specs(spec, variant, ''))
    for nested_path, nested_slot, nested_spec, fragment in nested:
        _check_entry(nested_spec, nested_slot, f'{path}.{nested_path}', fragment, report)
    for nested_path, _, _, _ in sorted(nested, key=lambda n: _path_key(n[0]), reverse=True):  # higher indices first
        _remove(pruned, nested_path)
    for error in _errors({**variant, '$defs': S.defs()}, pruned):
        report.problems.append(Problem('structure', _join(path, _json_path(error.absolute_path)), error.message))


def _path_key(dotted: str) -> tuple:
    """'model_configs.models[10]' -> ('model_configs', 'models', 10), so list indices sort numerically."""
    return tuple(int(part) if part.isdigit() else part
                 for part in dotted.replace(']', '').replace('[', '.').split('.'))


def _remove(value: dict, dotted: str) -> None:
    """Remove the nested specification at `dotted` ('price_model', 'model_configs.models[0]') from `value`: a list item
    is deleted, an object field set to null, so the parent still validates without it."""
    parts = dotted.replace(']', '').replace('[', '.').split('.')
    parent = value
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    last = parts[-1]
    if isinstance(parent, list):
        del parent[int(last)]
    else:
        parent[last] = None


def _errors(schema: dict, instance) -> list:
    validator = Draft202012Validator(schema)
    return sorted(validator.iter_errors(instance), key=lambda e: (list(map(str, e.absolute_path)), e.message))


def _json_path(parts) -> str:
    text = ''
    for part in parts:
        text += f'[{part}]' if isinstance(part, int) else (f'.{part}' if text else str(part))
    return text


def _join(path: str, sub: str) -> str:
    if not sub:
        return path
    return f'{path}{sub}' if sub.startswith('[') else f'{path}.{sub}'


# ------------------------------------------------------------------------------------------------ level 2: semantics

def validate_semantics(config: dict) -> Report:
    """TENT's models, component by component, so one failing component (or one class from a package that is not
    installed) does not hide the rest. Messages are the models' own (ConfigurationError)."""
    config = prepared(config)
    report = Report(levels=('semantics',))
    try:
        cfg = TransactiveNode.Config.validate_for('TransactiveNode', config)
    except ConfigurationError as error:
        report.problems.append(Problem('semantics', '', '; '.join(error.problems)))
        return report
    specs = [('data_manager', cfg.data_manager, 'DataManager')] if cfg.data_manager is not None else []
    for list_name, base_name in registry.NODE_LISTS.items():
        specs.extend((S.entry_path(list_name, i), spec, base_name) for i, spec in enumerate(getattr(cfg, list_name)))
    for where, spec, base_name in specs:
        try:
            component_class = registry.resolve(spec.class_name, registry.base_class(base_name), spec.module_name)
        except ImportError as error:
            report.unchecked.append(Unchecked(where, spec.class_name, spec.module_name, f'cannot be imported here ({error})'))
            continue
        except (ValueError, TypeError) as error:
            report.problems.append(Problem('semantics', where, str(error)))
            continue
        if not component_class.declares_config():
            report.unchecked.append(Unchecked(where, spec.class_name, spec.module_name,
                                              'declares no Config model; its constructor validates its own keywords'))
            continue
        try:
            TransactiveNode._validate_component_spec(component_class, spec.spec(), where)
        except ConfigurationError as error:
            owner = error.owner[len(where) + 2:] if error.owner.startswith(where + ': ') else error.owner
            report.problems.append(Problem('semantics', where, f'{owner}: ' + '; '.join(error.problems)))
        except ImportError as error:  # a nested specification names a class from a package that is not installed
            report.unchecked.append(Unchecked(where, spec.class_name, spec.module_name,
                                              f'a nested class cannot be imported here ({error})'))
        except (ValueError, TypeError) as error:
            report.problems.append(Problem('semantics', where, str(error)))
    return report


# --------------------------------------------------------------------------------------------- level 3: construction

class CheckNode(TransactiveNode):
    """A node that can be built but never runs: the abstract host hooks do nothing."""

    def _sleep(self, *args, **kwargs):
        return None

    def issue_signal(self, *args, **kwargs):
        return None

    def register_subscription(self, *args, **kwargs):
        return None

    def state_machine_loop(self, *args, **kwargs):
        return None


BUILD_ORDER = ('meter_points', 'information_service_models', 'local_assets', 'neighbors', 'market_series')


@contextlib.contextmanager
def _isolated_build(base_dir: str | Path | None):
    """configure_node sets the process-wide simulation clock and reads relative paths from the working directory;
    both are restored afterwards."""
    saved = {name: getattr(Timer, name) for name in ('created_time', 'sim_one_hr_in_sec', 'sim_start_time', 'simulation', 'tz')}
    cwd = os.getcwd()
    level = logging.getLogger('tent').level
    try:
        if base_dir is not None:
            os.chdir(base_dir)
        logging.getLogger('tent').setLevel(logging.ERROR)
        yield
    finally:
        os.chdir(cwd)
        logging.getLogger('tent').setLevel(level)
        for name, value in saved.items():
            setattr(Timer, name, value)


def _build(config: dict) -> CheckNode:
    node = CheckNode()
    node.configure_node(deepcopy(config))
    return node


def _step(node: CheckNode, steps: int, report: Report) -> None:
    """Run `steps` state-machine iterations with the clock set to the first market's transition times in turn (the
    clock stays at the last one once they are used up), so the markets move through their states; the first iteration
    that raises is reported as `step k` and stops the run."""
    if not node.market_series:
        report.problems.append(Problem('construction', 'step 1', 'the configuration has no market series to step'))
        return
    timings = node.market_series[0].newest_market.transition_times
    instants = sorted({value for value in vars(timings).values() if hasattr(value, 'tzinfo')})
    clock = {'t': instants[0]}
    with patch.object(Timer, 'now', side_effect=lambda *args, **kwargs: clock['t']):
        for k in range(1, steps + 1):
            clock['t'] = instants[min(k - 1, len(instants) - 1)]
            try:
                node._state_machine_iteration()
            except Exception as error:  # noqa: BLE001  the failure is the finding
                report.problems.append(Problem('construction', f'step {k}',
                                               f'at {clock["t"].isoformat()}: {type(error).__name__}: {error}'))
                return


def validate_construction(config: dict, base_dir: str | Path | None = None, steps: int = 0) -> Report:
    """
    Build a node from the configuration with a NullDataManager (nothing is archived). When the build fails, entries
    are added one at a time in TENT's construction order to find the one whose addition breaks it, and the failure is
    reported at that entry's path (or at the node when the node settings alone fail). Relative paths in the
    configuration (weather files) resolve against `base_dir`, by default the working directory, as they do for a node.
    With `steps`, the built node then runs that many state-machine iterations along its first market's transition
    times (see _step), so errors a component raises only when the markets run surface too.
    """
    config = prepared(config)
    report = Report(levels=('construction',))
    checked = {**config, 'data_manager': {'class_name': 'NullDataManager'}}
    with _isolated_build(base_dir):
        try:
            node = _build(checked)
        except Exception as error:  # noqa: BLE001  the failure is the finding
            failure = error
        else:
            if steps > 0:
                _step(node, steps, report)
            return report
        partial = {**checked, **{name: [] for name in BUILD_ORDER}}
        try:
            _build(partial)
        except Exception as error:  # noqa: BLE001
            report.problems.append(Problem('construction', '', f'{type(error).__name__}: {error}'))
            return report
        for list_name in BUILD_ORDER:
            for index, entry in enumerate(checked.get(list_name) or []):
                partial[list_name] = (checked[list_name])[:index + 1]
                try:
                    _build(partial)
                except Exception as error:  # noqa: BLE001
                    report.problems.append(Problem('construction', S.entry_path(list_name, index),
                                                   f'{type(error).__name__}: {error}'))
                    return report
    report.problems.append(Problem('construction', '', f'{type(failure).__name__}: {failure}'))
    return report


# ----------------------------------------------------------------------------------------------------------- all

def validate(config: dict, construct: bool = False, base_dir: str | Path | None = None, steps: int = 0) -> Report:
    """Placeholders, structure, then semantics, then (with `construct`) construction and `steps` iterations.
    Construction runs only when the earlier levels found no problem: a configuration the models reject cannot build,
    and the models say why better; a placeholder is a path that does not exist."""
    report = validate_placeholders(config).extend(validate_structure(config)).extend(validate_semantics(config))
    if construct and report.ok:
        report.extend(validate_construction(config, base_dir, steps))
    return report
