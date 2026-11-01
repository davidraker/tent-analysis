"""
One visual system for every figure (dataviz skill conventions). Colours follow entities, never ranks: a colour map is
built once from the sorted entity names of a catalog and reused by every figure, so a filter never repaints survivors.
Palette values are the skill's validated reference palette (references/palette.md); swap them for a brand palette.
"""
import html
from dataclasses import dataclass

import plotly.graph_objects as go

CATEGORICAL = {
    'light': ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
    'dark':  ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
}
SEQUENTIAL = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf',
              '#1c5cab', '#184f95', '#104281', '#0d366b']
DIVERGING = {'light': ['#2a78d6', '#f0efec', '#e34948'], 'dark': ['#3987e5', '#383835', '#e66767']}
STATUS = {'good': '#0ca30c', 'warning': '#fab219', 'serious': '#ec835a', 'critical': '#d03b3b'}
INK = {
    'light': {'surface': '#fcfcfb', 'page': '#f9f9f7', 'primary': '#0b0b0b', 'secondary': '#52514e', 'muted': '#898781',
              'grid': '#e1e0d9', 'axis': '#c3c2b7'},
    'dark':  {'surface': '#1a1a19', 'page': '#0d0d0d', 'primary': '#f0efec', 'secondary': '#c3c2b7', 'muted': '#898781',
              'grid': '#2c2c2a', 'axis': '#383835'},
}
OTHER = {'light': '#898781', 'dark': '#898781'}  # the 9th+ series fold into "Other"
FONT = 'Inter, "Segoe UI", Helvetica, Arial, sans-serif'


@dataclass
class Theme:
    mode: str = 'light'

    @property
    def ink(self) -> dict:
        return INK[self.mode]

    @property
    def categorical(self) -> list[str]:
        return CATEGORICAL[self.mode]

    @property
    def diverging(self) -> list[str]:
        return DIVERGING[self.mode]

    def template(self) -> go.layout.Template:
        ink = self.ink
        axis = dict(showgrid=True, gridcolor=ink['grid'], gridwidth=1, zeroline=False, linecolor=ink['axis'],
                    linewidth=1, ticks='outside', tickcolor=ink['axis'], tickfont=dict(color=ink['muted'], size=11),
                    title=dict(font=dict(color=ink['secondary'], size=12)), showline=True, automargin=True)
        return go.layout.Template(layout=go.Layout(
            paper_bgcolor=ink['page'], plot_bgcolor=ink['surface'],
            font=dict(family=FONT, color=ink['primary'], size=12),
            title=dict(font=dict(size=15, color=ink['primary']), x=0.0, xanchor='left'),
            colorway=self.categorical,
            xaxis=axis, yaxis=axis,
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0, font=dict(color=ink['secondary']),
                        bgcolor='rgba(0,0,0,0)', title=dict(text='')),
            hovermode='x unified',
            hoverlabel=dict(bgcolor=ink['surface'], bordercolor=ink['axis'], font=dict(color=ink['primary'], family=FONT)),
            margin=dict(l=56, r=24, t=64, b=48),
        ))

    def apply(self, fig: go.Figure, title: str | None = None, height: int | None = None) -> go.Figure:
        """Template, title and height. The title is escaped: it usually carries names from the archive."""
        fig.update_layout(template=self.template())
        if title:
            fig.update_layout(title=dict(text=text(title)))
        if height:
            fig.update_layout(height=height)
        return fig


def text(value) -> str:
    """A value from the archive (owner, series, market, topic, source label) as Plotly text. Plotly renders a small
    HTML subset in titles, legends, hover and tick labels, so a name holding markup would become live markup; escaping
    makes it read as written. Use for every trace name, hover extra, title and category label built from data."""
    return html.escape(str(value), quote=False)


def color_map(names, theme: Theme | None = None) -> dict[str, str]:
    """Fixed colour per entity name: assigned in sorted order, never cycled. Beyond eight, the rest share "Other" grey;
    callers should fold or facet instead of seating a ninth hue."""
    theme = theme or Theme()
    unique = sorted({str(n) for n in names})
    palette = theme.categorical
    colors = {name: palette[i] for i, name in enumerate(unique[:len(palette)])}
    colors.update({name: OTHER[theme.mode] for name in unique[len(palette):]})
    return colors


def zone_name(values) -> str:
    """The zone of a datetime column or index as text for an axis title ('UTC', 'America/Los_Angeles'). Loader frames
    are always zoned (the source's zone); naive values would mislabel the axis, so they raise."""
    tz = values.dt.tz if hasattr(values, 'dt') else getattr(values, 'tz', None)
    if tz is None:
        raise ValueError('time values must be timezone-aware to label the axis')
    return str(tz)


def time_axis(label: str, values) -> str:
    """Axis title for a time axis: the label with the zone in parentheses."""
    return f'{label} ({zone_name(values)})'
