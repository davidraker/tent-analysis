"""Configuration files: reading, writing, the inline documentation key, and the migration of legacy spellings."""
import json
from copy import deepcopy
from pathlib import Path

from tent import registry
from tent.transactive_node import LEGACY_NODE_KEYS

NODE_LIST_ORDER = tuple(registry.NODE_LISTS)  # the node's construction order: meter points first, market series last

DOC_KEY = '_doc'
"""Templates carry field descriptions under this key (JSON has no comments); strip_docs removes it, and validate,
diff and migrate work on the stripped document, so a documented file is still a valid configuration to this tool.
TENT itself does not accept the key: strip it before handing a file to a node."""

# Legacy key -> current key, for the component dictionaries (class_name was model_type in the earlier agent format).
LEGACY_COMPONENT_KEYS = {'model_type': 'class_name', 'market_model_type': 'market_class_name'}
# Keys TENT never read and the models now reject, by the dictionary they appeared in.
DROPPED_KEYS = {'db_params': ('db_type',)}


def load(path: str | Path) -> dict:
    with open(path, encoding='utf-8') as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError(f'{path}: a node configuration is a JSON object, not {type(config).__name__}')
    return config


def dump(config: dict, path: str | Path | None = None) -> str:
    """The configuration as JSON text (two-space indentation, keys as given); written to `path` when one is given."""
    text = json.dumps(config, indent=2, ensure_ascii=False) + '\n'
    if path is not None:
        Path(path).write_text(text, encoding='utf-8')
    return text


def strip_docs(value):
    """A copy of `value` without any DOC_KEY entries, at every depth."""
    if isinstance(value, dict):
        return {key: strip_docs(v) for key, v in value.items() if key != DOC_KEY}
    if isinstance(value, list):
        return [strip_docs(v) for v in value]
    return value


def migrate(config: dict) -> tuple[dict, list[str]]:
    """
    A copy of `config` with the legacy spellings the models identify rewritten, and a note per change (with its path):
    the camelCase node lists (meterPoints, localAssets, markets, ...) renamed; model_type renamed to class_name in every
    component dictionary; db_type dropped from db_params (TENT never read it); a TemperatureForecastModel location
    given as "" made null. Anything else the models reject is left for validate to report with the model's own words.
    """
    result = deepcopy(config)
    notes: list[str] = []
    for legacy, current in LEGACY_NODE_KEYS.items():
        if legacy in result:
            if current in result:
                raise ValueError(f'both {legacy!r} and {current!r} are present; keep one list of {current}')
            result[current] = result.pop(legacy)
            notes.append(f'{legacy} -> {current}')
    _migrate_dicts(result, '', notes)
    return result, notes


def _migrate_dicts(value, path: str, notes: list[str]) -> None:
    if isinstance(value, dict):
        for legacy, current in LEGACY_COMPONENT_KEYS.items():
            if legacy in value:
                if current in value:
                    raise ValueError(f'{path or "node"}: both {legacy!r} and {current!r} are present; keep {current}')
                value[current] = value.pop(legacy)
                notes.append(f'{path or "node"}: {legacy} -> {current}')
        for owner, dropped in DROPPED_KEYS.items():
            if path.rsplit('.', 1)[-1] == owner:
                for key in dropped:
                    if key in value:
                        value.pop(key)
                        notes.append(f'{path}: dropped {key} (never read by TENT)')
        if value.get('class_name') == 'TemperatureForecastModel' and value.get('location') == '':
            value['location'] = None
            notes.append(f'{path or "node"}: location "" -> null')
        for key, sub in list(value.items()):
            _migrate_dicts(sub, f'{path}.{key}' if path else key, notes)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _migrate_dicts(item, f'{path}[{index}]', notes)


PLACEHOLDER_PREFIX = '<FILL:'
"""A value a template leaves for the user to fill (paths and URLs of the deployment): a string starting with this
marker and describing what belongs there, e.g. "<FILL: path of the hourly temperature CSV>". validate reports every
placeholder still present, so a template cannot be mistaken for a finished configuration."""


def is_placeholder(value) -> bool:
    return isinstance(value, str) and value.startswith(PLACEHOLDER_PREFIX)


def placeholders(value, path: str = '') -> list[tuple[str, str]]:
    """(path, placeholder text) of every placeholder in a configuration, at any depth."""
    found = []
    if is_placeholder(value):
        found.append((path, value))
    elif isinstance(value, dict):
        for key, sub in value.items():
            found.extend(placeholders(sub, f'{path}.{key}' if path else key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(placeholders(item, f'{path}[{index}]'))
    return found


# The host application's keys in a node configuration, by node list ('' for the top level). The TransactiveNodeAgent
# reads these and removes them before the configuration reaches TENT, whose models refuse what they do not declare.
# Source of truth: the agent package's own HOST_KEYS export when it is importable (see host_keys); until the agent
# exports one, this mapping is the tool's. The top-level keys are also fields of TENT's node model (TENT accepts them
# for the host), so only the per-list keys are stripped before validation.
DEFAULT_HOST_KEYS: dict[str, frozenset[str]] = {
    '': frozenset({'db_topic', 'subscribe_all_platforms', 'topics'}),
    'neighbors': frozenset({'sender'}),  # identity@instance the platform must present for the neighbor's signals
}


def host_keys() -> dict[str, frozenset[str]]:
    """List name ('' for the top level) -> the host application's keys there: the agent package's HOST_KEYS when the
    package is installed and exports one, else DEFAULT_HOST_KEYS."""
    try:
        from transactive_node import HOST_KEYS as declared  # noqa: PLC0415  optional host package
    except ImportError:
        return dict(DEFAULT_HOST_KEYS)
    return {list_name: frozenset(keys) for list_name, keys in dict(declared).items()}


def host_entries(config: dict) -> list[tuple[str, str, object]]:
    """(path, key, value) of every host key present in the component lists of `config` (the keys TENT would refuse)."""
    found = []
    keys_by_list = host_keys()
    for list_name in sorted(keys_by_list, key=lambda name: (list(NODE_LIST_ORDER).index(name) if name in NODE_LIST_ORDER else len(NODE_LIST_ORDER), name)):
        keys = keys_by_list[list_name]
        if not list_name:
            continue
        entries = config.get(list_name)
        if isinstance(entries, list):
            for index, entry in enumerate(entries):
                if isinstance(entry, dict):
                    found.extend((f'{list_name}[{index}]', key, entry[key]) for key in sorted(keys) if key in entry)
    return found


def strip_host_keys(config: dict) -> dict:
    """A copy of `config` without the host application's per-list keys (host_entries), as the host hands it to TENT."""
    result = dict(config)
    for list_name, keys in host_keys().items():
        entries = result.get(list_name) if list_name else None
        if isinstance(entries, list):
            result[list_name] = [{k: v for k, v in e.items() if k not in keys} if isinstance(e, dict) else e for e in entries]
    return result


def prepared(config: dict) -> dict:
    """A configuration as TENT receives it: without documentation keys and without the host application's keys."""
    return strip_host_keys(strip_docs(config))
