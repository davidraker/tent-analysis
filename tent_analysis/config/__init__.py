"""
Configuration tool: produce, validate, compare and migrate the node configuration files TransactiveNode.configure_node
consumes, on top of TENT's configuration models (tent.config) and their JSON Schema (tent.registry.configuration_schema).
The logic lives here; the CLI (tent-config) and the Streamlit page only call it.
"""
from tent_analysis.config.schema import node_schema, write_schema, variant_for, registered_variants
from tent_analysis.config.documents import load, dump, strip_docs, migrate, DOC_KEY
from tent_analysis.config.checks import Problem, Unchecked, Report, validate, validate_structure, validate_semantics, \
    validate_construction
from tent_analysis.config.diff import Change, diff_configs
from tent_analysis.config.templates import template, node_template

__all__ = ['node_schema', 'write_schema', 'variant_for', 'registered_variants', 'load', 'dump', 'strip_docs', 'migrate',
           'DOC_KEY', 'Problem', 'Unchecked', 'Report', 'validate', 'validate_structure', 'validate_semantics',
           'validate_construction', 'Change', 'diff_configs', 'template', 'node_template']
