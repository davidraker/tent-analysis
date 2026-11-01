"""Headless smoke test of the dashboard's Configuration mode through Streamlit's AppTest."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tent_analysis.config import node_template

SCRIPT = Path(__file__).resolve().parents[1] / 'tent_analysis' / 'app' / 'dashboard.py'


def test_configuration_mode_renders_a_form_and_validates():
    app = AppTest.from_file(str(SCRIPT), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value('Configuration').run()
    assert any('Upload a node configuration' in str(info.value) for info in app.info)
    app.sidebar.button[0].click().run()
    assert app.session_state['config_document']['data_manager'] == {'class_name': 'NullDataManager'}
    assert any('No problems' in str(s.value) for s in app.success), [str(e.value) for e in app.error]
    assert app.selectbox[0].value == 'data_manager'
    assert app.session_state['config_document'] == node_template(doc=False)
