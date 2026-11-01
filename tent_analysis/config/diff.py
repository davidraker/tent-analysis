"""
Structural diff of two node configurations, each change classified as something a running node takes in place
(reconfigurable) or something that rebuilds the node, by the reconfigurable markers of TENT's models as they appear
in the schema. Components are matched by list and name (and class), as TransactiveNode.reconfigure matches them.
"""
from dataclasses import dataclass

from tent_analysis.config import schema as S
from tent_analysis.config.documents import HOST_KEYS, strip_docs


@dataclass(frozen=True)
class Change:
    path: str
    kind: str                       # 'added' | 'removed' | 'changed'
    old: object
    new: object
    reconfigurable: bool | None     # None: cannot tell (an unregistered class, a key no model knows)
    reason: str

    def __str__(self) -> str:
        verdict = {True: 'in place', False: 'rebuild', None: 'unknown'}[self.reconfigurable]
        return f'[{verdict}] {self.path}: {self.kind} ({self.reason}): {_short(self.old)} -> {_short(self.new)}'


def _short(value) -> str:
    if isinstance(value, list) and len(value) > 6 and all(isinstance(v, (int, float)) for v in value):
        return f'[{len(value)} values]'
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + '...'


def in_place(changes: list[Change]) -> bool:
    """True when every change is reconfigurable (an empty diff is)."""
    return all(change.reconfigurable for change in changes)


def diff_configs(old: dict, new: dict) -> list[Change]:
    old, new = strip_docs(old), strip_docs(new)
    changes: list[Change] = []
    node_props = S.node_schema()['properties']
    for key in sorted(set(old) | set(new)):
        if key in S.NODE_LISTS:
            changes.extend(_diff_list(old.get(key) or [], new.get(key) or [], key, S.NODE_LISTS[key], node_props[key]))
        elif key == 'data_manager':
            if old.get(key) != new.get(key):
                changes.append(_change(key, old, new, False, 'the data manager is not reconfigurable'))
        elif old.get(key) != new.get(key):
            reason = 'node settings are not reconfigurable' if key in node_props else 'not a configuration key'
            changes.append(_change(key, old, new, False if key in node_props else None, reason))
    return changes


def _change(key: str, old: dict, new: dict, reconfigurable, reason: str) -> Change:
    """A change of the node-level `key` between the two documents."""
    before, after = old.get(key, _MISSING), new.get(key, _MISSING)
    return Change(key, _kind_values(before, after), _value(before), _value(after), reconfigurable, reason)


_MISSING = object()


def _missing(value) -> bool:
    return value is _MISSING


def _value(value):
    return None if value is _MISSING else value


def _diff_list(old: list, new: list, list_name: str, slot: str, fragment: dict) -> list[Change]:
    changes = []
    by_name_old = {e.get('name', ''): (i, e) for i, e in enumerate(old) if isinstance(e, dict)}
    by_name_new = {e.get('name', ''): (i, e) for i, e in enumerate(new) if isinstance(e, dict)}
    for name in sorted(set(by_name_old) - set(by_name_new)):
        index, entry = by_name_old[name]
        changes.append(Change(S.entry_path(list_name, index), 'removed', entry, None, False,
                              f'{list_name} entry {name!r} removed'))
    for name in sorted(set(by_name_new) - set(by_name_old)):
        index, entry = by_name_new[name]
        changes.append(Change(S.entry_path(list_name, index), 'added', None, entry, False,
                              f'{list_name} entry {name!r} added'))
    for name in sorted(set(by_name_old) & set(by_name_new)):
        index, entry_old = by_name_old[name]
        entry_new = by_name_new[name][1]
        changes.extend(_diff_spec(entry_old, entry_new, slot, S.entry_path(list_name, index), fragment))
    return changes


def _diff_spec(old: dict, new: dict, slot: str, path: str, fragment: dict) -> list[Change]:
    """Two specifications of the same component: a class change rebuilds; otherwise fields are compared through the
    class's variant."""
    identity_old = tuple(old.get(k) for k in S.RUNTIME_KEYS)
    identity_new = tuple(new.get(k) for k in S.RUNTIME_KEYS)
    if identity_old != identity_new:
        return [Change(path, 'changed', {k: old.get(k) for k in S.RUNTIME_KEYS if old.get(k) is not None},
                       {k: new.get(k) for k in S.RUNTIME_KEYS if new.get(k) is not None}, False, 'class changed')]
    variant = S.variant_for(slot, new, fragment)
    if variant is None:
        return [Change(_join(path, key), 'changed', old.get(key), new.get(key), None,
                       f'{new.get("class_name")!r} is not a registered class; its fields cannot be classified')
                for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)]
    return _diff_object(old, new, variant, path)


def _diff_object(old: dict, new: dict, fragment: dict, path: str) -> list[Change]:
    """Fields of an object described by `fragment` (a variant or a nested model such as ModelFrameConfigs)."""
    changes = []
    properties = fragment.get('properties', {})
    for key in sorted(set(old) | set(new)):
        if key in S.RUNTIME_KEYS:
            continue
        if old.get(key, _MISSING) == new.get(key, _MISSING):
            continue
        sub_path = _join(path, key)
        prop = properties.get(key)
        if key in HOST_KEYS.get(path.split('[', 1)[0], ()):
            changes.append(Change(sub_path, _kind(old, new, key), old.get(key), new.get(key), None,
                                  'a host application key, not a TENT setting; the host decides'))
            continue
        if prop is None:
            changes.append(Change(sub_path, _kind(old, new, key), old.get(key), new.get(key), None,
                                  'not a configuration key of this class'))
            continue
        changes.extend(_diff_field(old.get(key, _MISSING), new.get(key, _MISSING), prop, sub_path, key))
    return changes


def _diff_field(old, new, prop: dict, path: str, key: str) -> list[Change]:
    slot = S.spec_slot(prop)
    reconfigurable = S.is_reconfigurable(prop)
    present = not _missing(old) and not _missing(new)
    if slot is not None:
        if reconfigurable and not isinstance(old, list) and not isinstance(new, list):
            # A reconfigurable nested block (occupancy manager) is rebuilt in place whatever changed inside it.
            return [Change(path, _kind_values(old, new), _value(old), _value(new), True, f'{key} is reconfigurable')]
        if isinstance(old, list) and isinstance(new, list):
            return _diff_spec_list(old, new, slot, path, prop)
        if present and isinstance(old, dict) and isinstance(new, dict):
            return _diff_spec(old, new, slot, path, prop)  # a class change rebuilds; fields follow their markers
        return [Change(path, _kind_values(old, new), _value(old), _value(new), False, f'{key} is not reconfigurable')]
    nested = [a for a in S.alternatives(prop) if 'properties' in a]
    if nested and present and isinstance(old, dict) and isinstance(new, dict):
        # A nested model (model_configs): each field inside follows its own marker.
        return _diff_object(old, new, nested[0], path)
    if reconfigurable:
        return [Change(path, _kind_values(old, new), _value(old), _value(new), True, _describe(old, new, 'reconfigurable'))]
    return [Change(path, _kind_values(old, new), _value(old), _value(new), False, _describe(old, new, 'not reconfigurable'))]


def _diff_spec_list(old: list, new: list, slot: str, path: str, prop: dict) -> list[Change]:
    """A list of nested specifications (model-frame models), matched by subscribe_key or name: membership and class
    changes rebuild; settings follow their markers."""
    def key_of(spec):
        return spec.get('subscribe_key') or spec.get('name') or spec.get('class_name')
    by_old = {key_of(s): (i, s) for i, s in enumerate(old) if isinstance(s, dict)}
    by_new = {key_of(s): (i, s) for i, s in enumerate(new) if isinstance(s, dict)}
    changes = []
    for key in sorted(set(by_old) ^ set(by_new)):
        index, spec = by_old.get(key) or by_new[key]
        kind = 'removed' if key in by_old else 'added'
        changes.append(Change(f'{path}[{index}]', kind, spec if kind == 'removed' else None,
                              spec if kind == 'added' else None, False, f'the set of {slot} entries changed'))
    for key in sorted(set(by_old) & set(by_new)):
        index, spec_old = by_old[key]
        spec_new = by_new[key][1]
        changes.extend(_diff_spec(spec_old, spec_new, slot, f'{path}[{index}]', prop))
    return changes


def _describe(old, new, verdict: str) -> str:
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new) \
            and all(isinstance(v, (int, float)) for v in old + new):
        differ = sum(1 for a, b in zip(old, new) if a != b)
        return f'{len(old)} values, {differ} differ; {verdict}'
    return verdict


def _kind(old: dict, new: dict, key: str) -> str:
    return 'added' if key not in old else 'removed' if key not in new else 'changed'


def _kind_values(old, new) -> str:
    return 'added' if _missing(old) else 'removed' if _missing(new) else 'changed'


def _join(path: str, key: str) -> str:
    return f'{path}.{key}' if path else key
