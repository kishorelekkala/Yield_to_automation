"""
Automated yield_to population for HD-Map intersections, German traffic rules.

Typical use::

    from yield_pipeline import run

    result, cells, lines = run(features)          # features = list of dicts

`features` is whatever was selected in QGIS, converted to::

    {"attributes": {...}, "geometry": [(x, y), ...]}

See qgis_adapter.py for the conversion, and scripts/yield_to_algorithm.py for
the Processing-Toolbox entry point.
"""

from .config import (DEFAULT_CONFIG, GeometryConfig, OutputConfig,
                     PipelineConfig, RuleConfig, SchemaConfig, SignCodes)
from .conflicts import detect_conflicts
from .extract import ExtractionError, build_junction
from .model import (Arm, Conflict, ConflictType, Junction, Movement,
                    PipelineResult, PriorityClass, Reason, TurnType,
                    YieldRelation)
from .rules import evaluate
from .writer import (build_yield_lines, build_yield_to_map, format_yield_to,
                     summarise)

__all__ = [
    "run", "build_junction", "evaluate", "detect_conflicts",
    "build_yield_to_map", "build_yield_lines", "format_yield_to", "summarise",
    "PipelineConfig", "SchemaConfig", "OutputConfig", "SignCodes",
    "GeometryConfig", "RuleConfig", "DEFAULT_CONFIG",
    "Junction", "Movement", "Arm", "Conflict", "ConflictType", "TurnType",
    "PriorityClass", "Reason", "YieldRelation", "PipelineResult",
    "ExtractionError",
]

__version__ = "0.1.0"


def run(features, config: PipelineConfig = DEFAULT_CONFIG):
    """Full pipeline: features in, (result, yield_to cells, yield lines) out."""
    junction = build_junction(features, config)
    result = evaluate(junction, config)
    return result, build_yield_to_map(result, config), build_yield_lines(result, config)
