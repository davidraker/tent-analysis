"""
Streamlit dashboard: `tent-analysis dashboard <url> [<url> ...]` or `streamlit run dashboard.py -- <url>`.
Widgets only; everything else lives in tent_analysis.app.views so it can be tested without a browser.
"""
import os
import sys

import streamlit as st

from tent_analysis.app.views import Selection, Views, comparison
from tent_analysis.data import Source

PAGES = {'Overview': 'overview', 'Curves': 'curves', 'Negotiation': 'negotiation', 'Markets': 'markets',
         'Signals': 'signals', 'Bid position': 'bids', 'Costs': 'costs', 'Devices': 'devices'}


@st.cache_resource(show_spinner=False)
def open_source(url: str, label: str) -> Source:
    return Source(url, label=label)


def _urls_from_argv() -> list[str]:
    urls = [a for a in sys.argv[1:] if '://' in a]
    env = os.environ.get('TENT_ANALYSIS_URLS')
    return urls or ([u for u in env.split(';') if u] if env else [])


def _selected_point(event) -> object | None:
    try:
        points = event.selection.points
    except AttributeError:
        return None
    return points[0]['x'] if points else None


def main():
    st.set_page_config(page_title='TENT analysis', layout='wide')
    st.sidebar.title('TENT analysis')
    urls_text = st.sidebar.text_area('Archive URLs (one per line)', value='\n'.join(_urls_from_argv()), height=90)
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    if not urls:
        st.info('Enter at least one SQLAlchemy URL, e.g. sqlite:///path/to/archive.sqlite')
        return
    sources = [open_source(u, u.rsplit('/', 1)[-1].rsplit('.', 1)[0]) for u in urls]
    labels = [s.label for s in sources]
    current = sources[labels.index(st.sidebar.selectbox('Source', labels))]
    cat = current.catalog
    mode = st.sidebar.radio('Theme', ['light', 'dark'], horizontal=True)
    series = st.sidebar.multiselect('Market series', sorted(cat.market_series['name']))
    owners = st.sidebar.multiselect('Owners', sorted(cat.components[cat.components['type'] != 'market']['name']))
    sel = Selection(series=series, owners=owners, mode=mode,
                    interval_start=st.session_state.get('interval_start'), owner=st.session_state.get('owner'))
    page = st.sidebar.radio('Page', list(PAGES) + (['Compare'] if len(sources) > 1 else []))
    st.header(f'{current.label}: {page}')
    if page == 'Compare':
        baseline = sources[labels.index(st.sidebar.selectbox('Baseline', labels))]
        figures = comparison(sources, baseline, sel)
    else:
        views = Views(current, sel)
        intervals = views.intervals()
        if intervals and page in ('Curves', 'Negotiation', 'Signals'):
            default = sel.interval_start if sel.interval_start in intervals else intervals[0]
            picked = st.selectbox('Delivery interval', intervals, index=intervals.index(default),
                                  format_func=lambda t: t.strftime('%Y-%m-%d %H:%M'))
            st.session_state['interval_start'] = picked
            views.sel.interval_start = picked
        if page == 'Curves':
            names = views.curve_owners()
            if names:
                views.sel.owner = st.selectbox('Owner', names, index=names.index(sel.owner) if sel.owner in names else 0)
                st.session_state['owner'] = views.sel.owner
        figures = getattr(views, PAGES[page])()
    if not figures:
        st.warning('Nothing to show for this selection.')
        return
    for name, fig in figures.items():
        st.subheader(name)
        event = st.plotly_chart(fig, use_container_width=True, on_select='rerun', selection_mode='points',
                                key=f'{page}:{name}')
        picked = _selected_point(event)
        if picked is not None and page == 'Overview':
            # Linked view: clicking a point on a timeline focuses that delivery interval for Curves and Negotiation.
            st.session_state['interval_start'] = picked
            st.caption(f'Focused interval set to {picked}. Open Curves or Negotiation to drill in.')


if __name__ == '__main__' or st.runtime.exists():
    main()
