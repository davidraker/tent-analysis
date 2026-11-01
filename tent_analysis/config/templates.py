"""New configuration entries from the schema: every field with its default, and the field descriptions under DOC_KEY."""
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
