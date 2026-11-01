"""New configuration entries from the schema: every field with its default, and the field descriptions under DOC_KEY."""
import json
from importlib import resources

from tent_analysis.config import schema as S
from tent_analysis.config.documents import DOC_KEY

REQUIRED = None  # a required field without a default is written as null; validate names it until it is filled


def _doc_line(name: str, prop: dict, required: bool) -> str:
    kind = prop.get('type') or ('/'.join(a.get('type', 'object') for a in prop.get('anyOf', [])) or 'object')
    parts = [prop.get('description', '').strip() or '(no description)', f'type {kind}']
    if 'const' in prop:
        parts.append(f'fixed at {prop["const"]!r} for this class')
    if 'enum' in prop:
        parts.append('one of ' + ', '.join(map(str, prop['enum'])))
    for bound, word in (('minimum', '>='), ('exclusiveMinimum', '>'), ('maximum', '<='), ('exclusiveMaximum', '<')):
        if bound in prop:
            parts.append(f'{word} {prop[bound]}')
    if prop.get('x-tent-unit'):
        parts.append(f'in {prop["x-tent-unit"]}')
    if S.reference_list(prop):
        parts.append(f'names of {S.reference_list(prop)} entries')
    parts.append('required' if required else f'default {prop.get("default")!r}')
    if S.is_reconfigurable(prop):
        parts.append('reconfigurable in place')
    return '; '.join(parts)


def _nested_object(prop: dict) -> dict | None:
    """The object schema a property holds (directly, through a $ref, or as the object alternative of a nullable
    anyOf), when it describes fields of its own and is not a class specification."""
    if S.spec_slot(prop) is not None:
        return None
    for alternative in S.alternatives(prop):
        if 'properties' in alternative:
            return alternative
    return None


def _entry_from_variant(variant: dict, doc: bool) -> dict:
    """Every field of a variant (or nested object schema) at its default; a required field without a default is null,
    unless it is an object with fields of its own, which is filled in the same way; an optional field without a default
    is documented but left out."""
    required = set(variant.get('required', []))
    entry: dict = {}
    docs: dict = {}
    for name, prop in variant['properties'].items():
        if name in ('module_name', 'market_module_name'):
            continue  # a shipped class needs none
        if 'const' in prop:
            entry[name] = prop['const']
            if name not in S.RUNTIME_KEYS:
                docs[name] = _doc_line(name, prop, name in required)
            continue
        docs[name] = _doc_line(name, prop, name in required)
        if 'default' in prop:
            entry[name] = prop['default']
        elif name in required:
            nested = _nested_object(prop)
            entry[name] = _entry_from_variant(nested, doc) if nested is not None else REQUIRED
        # an optional field without a default (an adapter's free-form model_config) is left out; the doc names it
    if doc:
        entry[DOC_KEY] = docs
    return entry


def template(slot: str, class_name: str, market_class_name: str | None = None, doc: bool = True) -> dict:
    """
    A new entry for `class_name` in `slot` ('Neighbor', 'LocalAsset', 'MarketSeries' with `market_class_name`, ...):
    class_name (and market_class_name), every field at its default, required fields without a default as null, and
    with `doc` the descriptions under DOC_KEY. Fill the nulls, strip the docs (strip_docs) and the entry validates.
    """
    spec = {'class_name': class_name}
    if market_class_name is not None:
        spec['market_class_name'] = market_class_name
    variant = S.variant_for(slot, spec)
    if variant is None:
        names = sorted({S.class_name_of(v) for v in S.slot_variants(slot)})
        extra = ''
        if slot == 'MarketSeries':
            extra = f'; market classes: {sorted({S.market_class_name_of(v) for v in S.slot_variants(slot)})}'
        raise ValueError(f'{class_name!r} is not a registered {slot}; registered: {names}{extra}')
    return _entry_from_variant(variant, doc)


def node_template(doc: bool = True) -> dict:
    """A whole node configuration: the node's settings at their defaults, no archive (NullDataManager), empty lists."""
    schema = S.node_schema()
    node: dict = {}
    docs: dict = {}
    for name, prop in schema['properties'].items():
        if name in S.NODE_LISTS:
            node[name] = []
        elif name == 'data_manager':
            node[name] = {'class_name': 'NullDataManager'}
        else:
            node[name] = prop.get('default')
        docs[name] = _doc_line(name, prop, name in set(schema.get('required', [])))
    if doc:
        node[DOC_KEY] = docs
    return node


def slots() -> dict[str, list[str]]:
    """Slot -> registered class names (variant titles for market series, which name both classes)."""
    return {slot: sorted(S.registered_variants(slot)) for slot in sorted({*S.SLOTS.values(), 'PriceModel', 'Model',
                                                                          'ActuationManager', 'OccupancyManager'})}


# ------------------------------------------------------------------------------------- whole-node templates

TEMPLATES_DIR = resources.files('tent_analysis.config') / 'templates'
"""Node templates: the TENT sample (campus) and the configurations deployed in the simulation containers (city, the
small office with direct-ratio actuation, and the one driven by the ILC agent), with their deployment paths replaced by
placeholders (documents.PLACEHOLDER_PREFIX) that validate reports until they are filled."""


def template_names() -> list[str]:
    return sorted(path.stem for path in TEMPLATES_DIR.iterdir() if path.name.endswith('.json'))


def document(config: dict) -> dict:
    """A copy of a configuration with DOC_KEY descriptions added to the node and to every entry whose class the schema
    knows (an entry of an unregistered class is left as it is)."""
    schema = S.node_schema()
    result = dict(config)
    result[DOC_KEY] = {name: _doc_line(name, prop, name in set(schema.get('required', [])))
                       for name, prop in schema['properties'].items()}
    for list_name, slot in S.SLOTS.items():
        value = result.get(list_name)
        fragment = schema['properties'][list_name]
        if isinstance(value, dict):
            result[list_name] = _document_entry(value, slot, fragment)
        elif isinstance(value, list):
            result[list_name] = [_document_entry(e, slot, fragment) if isinstance(e, dict) else e for e in value]
    return result


def _document_entry(entry: dict, slot: str, fragment: dict) -> dict:
    variant = S.variant_for(slot, entry, fragment)
    if variant is None:
        return entry
    required = set(variant.get('required', []))
    documented = dict(entry)
    documented[DOC_KEY] = {name: _doc_line(name, prop, name in required) for name, prop in variant['properties'].items()
                           if name in entry and name not in S.RUNTIME_KEYS}
    return documented


def new(name: str, doc: bool = True) -> dict:
    """A node configuration from the template `name` (see template_names), documented unless `doc` is False."""
    path = TEMPLATES_DIR / f'{name}.json'
    if name not in template_names():
        raise ValueError(f'{name!r} is not a template; the templates are {template_names()}')
    config = json.loads(path.read_text(encoding='utf-8'))
    return document(config) if doc else config


def add_entry(config: dict, list_name: str, class_name: str, market_class_name: str | None = None,
              name: str | None = None, doc: bool = False) -> dict:
    """A copy of `config` with a template entry for `class_name` appended to `list_name` (data_manager is replaced),
    named `name` (default: the class name). An entry of that name in the list is an error: names are how components
    refer to each other and how a running node matches a new configuration to its components."""
    if list_name not in S.SLOTS:
        raise ValueError(f'{list_name!r} is not a component list; the lists are {sorted(S.SLOTS)}')
    entry = template(S.SLOTS[list_name], class_name, market_class_name, doc=doc)
    result = dict(config)
    if list_name == 'data_manager':
        result['data_manager'] = entry
        return result
    entry['name'] = name or class_name
    entries = list(result.get(list_name) or [])
    taken = {e.get('name') for e in entries if isinstance(e, dict)}
    if entry['name'] in taken:
        raise ValueError(f'{list_name} already has an entry named {entry["name"]!r}; give --name another name')
    entries.append(entry)
    result[list_name] = entries
    return result
