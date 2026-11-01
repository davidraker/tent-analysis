"""
The configuration page of the Streamlit app: open a configuration file (or start from the node template), pick one
component, edit it in a form generated from TENT's schema, see the three-level validation of the whole file, and
download the result. Widgets only; the logic is in tent_analysis.config.
"""
import json

import streamlit as st

from tent_analysis.config import diff as D
from tent_analysis.config import documents, forms, templates
from tent_analysis.config import schema as S
from tent_analysis.config import checks as V

STATE = 'config_document'


def _load_document() -> dict | None:
    uploaded = st.sidebar.file_uploader('Configuration file', type=['json'], key='config_upload')
    if uploaded is not None and st.session_state.get('config_upload_name') != uploaded.name:
        st.session_state[STATE] = json.loads(uploaded.getvalue().decode('utf-8'))
        st.session_state['config_upload_name'] = uploaded.name
    if st.sidebar.button('Start from the node template'):
        st.session_state[STATE] = templates.node_template(doc=False)
    return st.session_state.get(STATE)


def _widget(spec: forms.FieldSpec, value, key: str):
    label = spec.name + (f' [{spec.unit}]' if spec.unit else '') + (' *' if spec.required else '')
    help_text = spec.description + (' Reconfigurable in place.' if spec.reconfigurable else ' Changing it rebuilds the node.')
    if spec.kind == 'boolean':
        return st.checkbox(label, value=bool(value), help=help_text, key=key)
    if spec.kind == 'enum':
        choices = list(spec.choices)
        index = choices.index(value) if value in choices else 0
        return st.selectbox(label, choices, index=index, help=help_text, key=key)
    if spec.kind in ('number', 'integer'):
        if value is None:
            text = st.text_input(label, value='', help=help_text + ' Empty means null.', key=key)
            if text.strip() == '':
                return None
            return int(text) if spec.kind == 'integer' else float(text)
        lo = spec.minimum if spec.minimum is not None else spec.exclusive_minimum
        hi = spec.maximum if spec.maximum is not None else spec.exclusive_maximum
        if spec.kind == 'integer':
            return st.number_input(label, value=int(value), min_value=None if lo is None else int(lo),
                                   max_value=None if hi is None else int(hi), step=1, help=help_text, key=key)
        return st.number_input(label, value=float(value), min_value=None if lo is None else float(lo),
                               max_value=None if hi is None else float(hi), format='%g', help=help_text, key=key)
    if spec.kind == 'text':
        text = st.text_input(label, value='' if value is None else str(value), help=help_text, key=key)
        return None if (text == '' and spec.nullable) else text
    text = st.text_area(label, value=forms.to_text(value), help=help_text + ' JSON.', key=key, height=120)
    return forms.from_text(text)


def _entry_form(document: dict, list_name: str, index: int) -> None:
    entries = document[list_name] if list_name in S.NODE_LISTS else [document[list_name]]
    entry = entries[index]
    slot = S.SLOTS[list_name]
    variant = S.variant_for(slot, entry, S.node_schema()['properties'][list_name])
    if variant is None:
        st.warning(f'{entry.get("class_name")!r} is not a class registered here; edit it as JSON.')
        text = st.text_area('Entry (JSON)', value=forms.to_text(entry), height=400, key=f'raw:{list_name}:{index}')
        try:
            entries[index] = forms.from_text(text)
        except ValueError as error:
            st.error(str(error))
        return
    st.caption(variant.get('description', '') or variant['title'])
    specs = forms.field_specs(variant)
    values = {}
    errors = []
    for spec in specs:
        try:
            values[spec.name] = _widget(spec, entry.get(spec.name, spec.default), key=f'{list_name}:{index}:{spec.name}')
        except ValueError as error:
            errors.append(f'{spec.name}: {error}')
    for message in errors:
        st.error(message)
    if not errors:
        entries[index] = forms.apply(entry, values, specs)
    if list_name not in S.NODE_LISTS:
        document[list_name] = entries[0]


def render() -> None:
    st.header('Configuration')
    document = _load_document()
    if document is None:
        st.info('Upload a node configuration file or start from the node template.')
        return
    original = json.loads(json.dumps(document))
    lists = ['data_manager'] + [name for name in S.NODE_LISTS if document.get(name)]
    list_name = st.selectbox('Component list', lists)
    if list_name == 'data_manager':
        if not isinstance(document.get('data_manager'), dict):
            st.info('No data manager block: nothing is archived.')
        else:
            _entry_form(document, 'data_manager', 0)
    else:
        names = [f'{i}: {e.get("name", "")} ({e.get("class_name", "?")})' for i, e in enumerate(document[list_name])]
        chosen = st.selectbox('Entry', range(len(names)), format_func=lambda i: names[i])
        _entry_form(document, list_name, chosen)
    st.subheader('Validation')
    report = V.validate(document)
    if report.ok:
        st.success(f'No problems ({", ".join(report.levels)} checked).')
    for problem in report.problems:
        st.error(str(problem))
    for unchecked in report.unchecked:
        st.warning(str(unchecked))
    for host in report.hosts:
        st.caption(str(host))
    changes = D.diff_configs(original, document)
    if changes:
        st.subheader('Changes in this session')
        for change in changes:
            st.write(str(change))
    st.download_button('Download configuration', data=documents.dump(documents.strip_docs(document)),
                       file_name='tn.config.json', mime='application/json')
    st.session_state[STATE] = document
