"""The "Configuring a node" page, generated from the schema: the node's own settings, then one section per component
list (and per nested slot) with one subsection per registered class listing its fields with type, unit, default,
bounds, and whether a running node takes a change in place. Rendered as Markdown or reStructuredText."""
from pathlib import Path

from tent_analysis.config import schema as S

TITLE = 'Configuring a node'
INTRO = ('A TENT node is configured by one JSON object: the node\'s own settings, the data manager, and five lists of '
         'components (meter_points, information_service_models, local_assets, neighbors, market_series). Every '
         'component entry names its class with class_name (and module_name for a class TENT does not ship) and gives '
         'that class\'s settings. The settings, their types, bounds and defaults are the fields of each class\'s Config '
         'model; this page is generated from the JSON Schema those models produce '
         '(tent.registry.configuration_schema), so it describes exactly what the installed TENT accepts. '
         'Durations are numbers of seconds, times of day are "HH:MM", enumerations are given by member name, and a '
         'field marked "in place" may be changed on a running node through reconfigure; any other change rebuilds '
         'the node. The configuration tool (tent-config) validates files against the same schema and models.')
SECTIONS = [('Data manager', 'data_manager', 'DataManager')] + \
           [(name.replace('_', ' ').capitalize(), name, slot) for name, slot in S.NODE_LISTS.items()]
NESTED = [('Price models (market_series[].price_model)', 'PriceModel'),
          ('Model-frame models (local_assets[].model_configs.models[])', 'Model'),
          ('Actuation managers (local_assets[].actuation_manager)', 'ActuationManager'),
          ('Occupancy managers (local_assets[].occupancy_manager)', 'OccupancyManager')]
COLUMNS = ('Field', 'Type', 'Default', 'Bounds', 'Description', 'In place')


def _type(prop: dict) -> str:
    if 'const' in prop:
        return f'constant {prop["const"]!r}'
    if 'enum' in prop:
        return 'one of ' + ', '.join(f'"{v}"' for v in prop['enum'])
    if S.spec_slot(prop):
        return f'{S.spec_slot(prop)} block'
    if 'anyOf' in prop:
        kinds = [a.get('type') or ('object' if '$ref' in a or 'properties' in a else 'value') for a in prop['anyOf']]
        return ' or '.join(dict.fromkeys(kinds))
    kind = prop.get('type', 'object')
    if kind == 'array' and 'items' in prop:
        inner = prop['items'].get('type', 'object') if isinstance(prop['items'], dict) else 'value'
        size = f' of {prop["minItems"]}' if prop.get('minItems') and prop.get('minItems') == prop.get('maxItems') else ''
        return f'array{size} of {inner}'
    unit = prop.get('x-tent-unit') or prop.get('x-tent-format')
    return f'{kind} [{unit}]' if unit else kind


def _bounds(prop: dict) -> str:
    parts = []
    for key, sign in (('minimum', '>='), ('exclusiveMinimum', '>'), ('maximum', '<='), ('exclusiveMaximum', '<')):
        if key in prop:
            parts.append(f'{sign} {prop[key]}')
    if 'minLength' in prop:
        parts.append(f'length >= {prop["minLength"]}')
    if S.reference_list(prop):
        parts.append(f'names of {S.reference_list(prop)}')
    return ', '.join(parts)


def _default(prop: dict, required: bool) -> str:
    if required and 'default' not in prop:
        return 'required'
    if 'default' not in prop:
        return ''
    value = prop['default']
    if isinstance(value, list) and len(value) > 6:
        return f'[{len(value)} values]'
    text = repr(value) if not isinstance(value, str) else f'"{value}"'
    return text if len(text) <= 40 else text[:37] + '...'


def _rows(properties: dict, required: set) -> list[tuple]:
    rows = []
    for name, prop in properties.items():
        if name in S.RUNTIME_KEYS:
            continue
        rows.append((name, _type(prop), _default(prop, name in required), _bounds(prop),
                     ' '.join(prop.get('description', '').split()), 'yes' if S.is_reconfigurable(prop) else ''))
    return rows


class _Doc:
    """A tiny document model rendered to Markdown or reStructuredText."""

    def __init__(self):
        self.blocks: list[tuple] = []

    def heading(self, text: str, level: int):
        self.blocks.append(('heading', level, text))

    def paragraph(self, text: str):
        self.blocks.append(('paragraph', text))

    def table(self, rows: list[tuple]):
        self.blocks.append(('table', rows))

    def markdown(self) -> str:
        out = []
        for block in self.blocks:
            if block[0] == 'heading':
                out.append('#' * block[1] + ' ' + block[2] + '\n')
            elif block[0] == 'paragraph':
                out.append(block[1] + '\n')
            else:
                escape = lambda cell: str(cell).replace('|', '\\|')  # noqa: E731
                out.append('| ' + ' | '.join(COLUMNS) + ' |')
                out.append('|' + '---|' * len(COLUMNS))
                out.extend('| ' + ' | '.join(escape(c) for c in row) + ' |' for row in block[1])
                out.append('')
        return '\n'.join(out)

    def rst(self) -> str:
        underline = {1: '=', 2: '-', 3: '~', 4: '^'}
        out = []
        for block in self.blocks:
            if block[0] == 'heading':
                text = block[2]
                if block[1] == 1:
                    out.append('=' * len(text))
                out.append(text)
                out.append(underline[block[1]] * len(text) + '\n')
            elif block[0] == 'paragraph':
                out.append(block[1] + '\n')
            else:
                out.append('.. list-table::')
                out.append('   :header-rows: 1')
                out.append('   :widths: 18 16 12 14 32 8\n')
                out.append('   * - ' + '\n     - '.join(COLUMNS))
                for row in block[1]:
                    cells = [str(c).replace('\n', ' ') or ' ' for c in row]
                    cells[0] = f'``{cells[0]}``'
                    out.append('   * - ' + '\n     - '.join(cells))
                out.append('')
        return '\n'.join(out)


def build() -> _Doc:
    schema = S.node_schema()
    doc = _Doc()
    doc.heading(TITLE, 1)
    doc.paragraph(INTRO)
    doc.heading('Node settings', 2)
    node_props = {k: v for k, v in schema['properties'].items() if k not in S.SLOTS}
    doc.table(_rows(node_props, set(schema.get('required', []))))
    for title, key, slot in SECTIONS + [(t, None, s) for t, s in NESTED]:
        doc.heading(title, 2)
        description = schema['properties'][key]['description'] if key else S.defs()[f'Any{slot}']['description']
        doc.paragraph(' '.join(description.split()) + f' Registered classes: '
                      + ', '.join(S.class_name_of(v) for v in S.slot_variants(slot)) + '.')
        for variant in S.slot_variants(slot):
            doc.heading(variant['title'], 3)
            if variant.get('description'):
                doc.paragraph(' '.join(variant['description'].split()))
            doc.table(_rows(variant['properties'], set(variant.get('required', []))))
    return doc


def render(fmt: str = 'md') -> str:
    if fmt not in ('md', 'rst'):
        raise ValueError(f"format must be 'md' or 'rst', got {fmt!r}")
    document = build()
    return document.markdown() if fmt == 'md' else document.rst()


def write_docs(out_dir: str | Path, fmt: str = 'md') -> Path:
    """Write configuring_a_node.<fmt> into `out_dir` (created if needed) and return its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'configuring_a_node.{fmt}'
    path.write_text(render(fmt), encoding='utf-8')
    return path
