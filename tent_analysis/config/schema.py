"""TENT's node configuration schema and the questions the tool asks of it: which variant describes an entry, where the
nested class specifications are, what a field's markers say."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from tent import registry
from tent.config import RECONFIGURABLE, REFERENCE, SPEC_DEFAULT, SPEC_OF

RUNTIME_KEYS = ('class_name', 'module_name', 'market_class_name', 'market_module_name')
NODE_LISTS: dict[str, str] = dict(registry.NODE_LISTS)  # list name -> slot (base class name)
SLOTS: dict[str, str] = {'data_manager': 'DataManager', **NODE_LISTS}  # node property -> slot


@lru_cache(maxsize=1)
def node_schema() -> dict:
    """The JSON Schema of a node configuration, built once from the installed TENT's models."""
    return registry.configuration_schema()


def write_schema(path: str | Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(node_schema(), indent=2) + '\n', encoding='utf-8')
    return path


def defs() -> dict:
    return node_schema()['$defs']


def resolve(fragment: dict) -> dict:
    """The fragment with a `$ref` followed (one level: the schema's references all point into $defs)."""
    if '$ref' in fragment:
        name = fragment['$ref'].rsplit('/', 1)[-1]
        return {**defs()[name], **{k: v for k, v in fragment.items() if k != '$ref'}}
    return fragment


def alternatives(fragment: dict) -> list[dict]:
    """The object alternatives of a fragment: itself, or the non-null members of its anyOf."""
    fragment = resolve(fragment)
    if 'anyOf' in fragment:
        return [resolve(a) for a in fragment['anyOf'] if a.get('type') != 'null']
    return [fragment]


def slot_variants(slot: str) -> list[dict]:
    """The registered class variants (oneOf) of a slot such as 'Neighbor' or 'MarketSeries'."""
    return defs()[f'Any{slot}']['oneOf']


def class_name_of(variant: dict) -> str:
    return variant['properties']['class_name']['const']


def market_class_name_of(variant: dict) -> str | None:
    prop = variant['properties'].get('market_class_name')
    return prop['const'] if prop else None


def default_class_of(fragment: dict) -> str | None:
    """The class an entry of this spec field names when it has no class_name (price models and the managers)."""
    return fragment.get(SPEC_DEFAULT)


def registered_variants(slot: str) -> dict[str, dict]:
    """Variant title -> variant for a slot ('Neighbor', 'MarketSeries of Auction', ...)."""
    return {v['title']: v for v in slot_variants(slot)}


def variant_for(slot: str, spec: dict, fragment: dict | None = None) -> dict | None:
    """The schema variant describing `spec` (a dict with class_name, and market_class_name for a series) in `slot`, or
    None when no registered class matches. Without class_name the slot's default class (from `fragment`) is meant."""
    class_name = spec.get('class_name') or (default_class_of(fragment) if fragment else None)
    if not class_name:
        return None
    market = spec.get('market_class_name')
    for variant in slot_variants(slot):
        if class_name_of(variant) != class_name:
            continue
        if slot == 'MarketSeries' and market_class_name_of(variant) != (market or 'Market'):
            continue
        return variant
    return None


def is_reconfigurable(fragment: dict) -> bool:
    return bool(fragment.get(RECONFIGURABLE))


def reference_list(fragment: dict) -> str | None:
    """The node list a name-reference field points into."""
    return fragment.get(REFERENCE)


def spec_slot(fragment: dict) -> str | None:
    """The slot of a nested class specification field, else None."""
    return fragment.get(SPEC_OF)


def iter_specs(value, fragment: dict, path: str) -> Iterator[tuple[str, str, dict, dict]]:
    """
    Every nested class specification inside `value` as the schema `fragment` describes it, as (path, slot, spec,
    fragment): the price model, the managers, the data manager, the model-frame models. Does not descend into the
    specifications themselves; the caller validates those against their own variant.
    """
    slot = spec_slot(fragment)
    if slot is not None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    yield f'{path}[{index}]', slot, item, fragment
        elif isinstance(value, dict):
            yield path, slot, value, fragment
        return
    for alternative in alternatives(fragment):
        if isinstance(value, dict) and 'properties' in alternative:
            for key, sub in alternative['properties'].items():
                if key in value:
                    yield from iter_specs(value[key], sub, f'{path}.{key}' if path else key)
        elif isinstance(value, list) and 'items' in alternative:
            for index, item in enumerate(value):
                yield from iter_specs(item, alternative['items'], f'{path}[{index}]')


def settings_of(variant: dict) -> dict[str, dict]:
    """The configuration fields of a variant: its properties other than the class and module names."""
    return {name: prop for name, prop in variant['properties'].items() if name not in RUNTIME_KEYS}


def entry_path(list_name: str, index: int) -> str:
    return f'{list_name}[{index}]'
