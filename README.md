# tent-analysis

Analysis and visualisation for TENT archives (SQLite or PostgreSQL databases written by `tent.data_manager`).

    pip install -e ../TENT -e ".[dev]"
    tent-analysis inspect sqlite:///path/to/archive.sqlite
    tent-analysis report  sqlite:///path/to/archive.sqlite --out report.html
    tent-analysis export  sqlite:///path/to/archive.sqlite --what measurements --out measurements.csv

Layers: `data` (sources, catalog, tidy loaders over TENT's `ArchiveReader`), `analysis` (DataFrame in, DataFrame out),
`viz` (pure Plotly figure builders), `report` (HTML assembly and the CLI). Design: `TENT_ANALYSIS_TOOL_DESIGN.md` in
the eclong_tcf repository.
