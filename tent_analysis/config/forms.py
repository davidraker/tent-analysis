"""What a form for one configuration entry needs, derived from the schema variant: one FieldSpec per setting with its
kind (number, integer, boolean, enum, text, json), bounds, default, description and markers. The Streamlit page draws
widgets from these; the mapping back to a configuration value is here too, so it is testable without a browser."""
import json
from dataclasses import dataclass

from tent_analysis.config import schema as S


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str                   # 'number' | 'integer' | 'boolean' | 'enum' | 'text' | 'json'
    description: str
    default: object
    required: bool
    reconfigurable: bool
    nullable: bool
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: float | None = None
    exclusive_maximum: float | None = None
    choices: tuple = ()
    unit: str | None = None
    reference: str | None = None  # the node list whose names this field holds


def _kind_of(prop: dict) -> tuple[str, bool]:
    """(kind, nullable) of a property: its type, or the non-null type of a two-way anyOf; anything else is json."""
    if 'enum' in prop:
        return 'enum', False
    if 'const' in prop:
        return 'text', False
    types = [prop.get('type')] if 'type' in prop else []
    nullable = False
    if 'anyOf' in prop:
        kinds = [a.get('type') for a in prop['anyOf']]
        nullable = 'null' in kinds
        kinds = [k for k in kinds if k != 'null']
        if len(kinds) == 1 and kinds[0] in ('number', 'integer', 'boolean', 'string'):
            types = kinds
        else:
            return 'json', nullable
    if types == ['number']:
        return 'number', nullable
    if types == ['integer']:
        return 'integer', nullable
    if types == ['boolean']:
        return 'boolean', nullable
    if types == ['string']:
        return 'text', nullable
    return 'json', nullable


def field_specs(variant: dict) -> list[FieldSpec]:
    """The settings of a variant as FieldSpecs, in schema order, class and module names excluded."""
    required = set(variant.get('required', []))
    specs = []
    for name, prop in S.settings_of(variant).items():
        kind, nullable = _kind_of(prop)
        specs.append(FieldSpec(name=name, kind=kind, description=prop.get('description', ''), default=prop.get('default'),
                               required=name in required, reconfigurable=S.is_reconfigurable(prop), nullable=nullable,
                               minimum=prop.get('minimum'), maximum=prop.get('maximum'),
                               exclusive_minimum=prop.get('exclusiveMinimum'), exclusive_maximum=prop.get('exclusiveMaximum'),
                               choices=tuple(prop.get('enum', ())), unit=prop.get('x-tent-unit'),
                               reference=S.reference_list(prop)))
    return specs


def to_text(value) -> str:
    """A json-kind field's value as editable text."""
    return json.dumps(value, indent=1) if value is not None else 'null'


def from_text(text: str):
    """The value a json-kind field holds, from its text; invalid JSON raises ValueError naming the problem."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f'not valid JSON: {error.msg} at line {error.lineno}, column {error.colno}') from None


def apply(entry: dict, values: dict, specs: list[FieldSpec]) -> dict:
    """The entry with the form's values applied. A field the entry did not have stays absent while the form shows its
    default (so an untouched form changes nothing); a value given for an absent field is added; a present field takes
    the form's value, None included (null)."""
    defaults = {spec.name: spec.default for spec in specs}
    result = dict(entry)
    for name, value in values.items():
        if name in result or value != defaults.get(name):
            result[name] = value
    return result
