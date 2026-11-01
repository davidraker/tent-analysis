"""Static export: one PNG or PDF file per figure, rendered by Kaleido (the `export` extra; needs a Chromium)."""
import re
from pathlib import Path

import plotly.graph_objects as go

FORMATS = ('png', 'pdf')
WIDTH = 1200  # px; heights come from each figure's layout


def slug(name: str) -> str:
    """A file-name-safe form of a figure name: lower case, runs of other characters collapsed to one underscore."""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'figure'


def write_images(figures: dict[str, go.Figure], out_dir: str | Path, fmt: str, scale: float = 2.0) -> list[Path]:
    """Write every figure to `out_dir` as NN_name.fmt (NN keeps the report order) and return the paths."""
    if fmt not in FORMATS:
        raise ValueError(f'format must be one of {FORMATS}, got {fmt!r}')
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (name, fig) in enumerate(figures.items(), start=1):
        path = out_dir / f'{i:02d}_{slug(name)}.{fmt}'
        fig.write_image(path, format=fmt, width=WIDTH, height=fig.layout.height or 450, scale=scale)
        paths.append(path)
    return paths
