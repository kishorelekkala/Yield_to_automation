"""
QGIS Processing algorithm: Populate yield_to (German traffic rules).

Install
-------
1. Put the `yield_pipeline` package somewhere QGIS can see it. Easiest is next
   to this file, then set PIPELINE_PATH below (or leave it: this script adds
   its own parent folder to sys.path).
2. Processing Toolbox -> the Python icon -> "Add Script to Toolbox..." and pick
   this file. It then appears under  HD Map / Populate yield_to.

Use
---
Select the centerlines (and the regulatory-element features) of one
intersection, run the tool, and it writes the yield_to column and adds a
"Yield lines" layer to the project.
"""

import os
import sys

from qgis.core import (QgsProcessing, QgsProcessingAlgorithm,
                       QgsProcessingException,
                       QgsProcessingParameterBoolean,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterString,
                       QgsProcessingParameterVectorLayer,
                       QgsProject)
from qgis.PyQt.QtCore import QCoreApplication

# ---------------------------------------------------------------------------
# IMPORTANT
# "Add Script to Toolbox..." COPIES this file into the QGIS scripts folder, so
# the path back to the yield_pipeline package is lost and the import fails
# silently - the tool then does not appear at all.
#
# Set this to the folder that CONTAINS 'yield_pipeline'. Forward slashes, even
# on Windows. Leave it empty only if you did NOT let QGIS copy the file.
# ---------------------------------------------------------------------------
PIPELINE_PATH = r""          # e.g. r"C:/hdmap/yield_to_pipeline"


def _resolve_pipeline_path():
    """Find the package whether the file was copied by QGIS or not."""
    candidates = []
    if PIPELINE_PATH:
        candidates.append(PIPELINE_PATH)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.dirname(here))      # ../ from scripts/
    candidates.append(here)                        # beside this file
    for path in candidates:
        if path and os.path.isdir(os.path.join(path, "yield_pipeline")):
            return path
    raise ImportError(
        "Could not find the 'yield_pipeline' package. Open this file and set "
        "PIPELINE_PATH near the top to the folder that contains it. Looked in: "
        + "; ".join(c for c in candidates if c))


_ROOT = _resolve_pipeline_path()
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class PopulateYieldToAlgorithm(QgsProcessingAlgorithm):

    CENTERLINES = "CENTERLINES"
    REGULATORY = "REGULATORY"
    SELECTED_ONLY = "SELECTED_ONLY"
    YIELD_FIELD = "YIELD_FIELD"
    CLEARANCE = "CLEARANCE"
    DRY_RUN = "DRY_RUN"
    ADD_YIELD_LINES = "ADD_YIELD_LINES"

    # ----------------------------------------------------------- plumbing --

    def createInstance(self):
        return PopulateYieldToAlgorithm()

    def name(self):
        return "populate_yield_to"

    def displayName(self):
        return self.tr("Populate yield_to (German traffic rules)")

    def group(self):
        return self.tr("HD Map")

    def groupId(self):
        return "hdmap"

    def shortHelpString(self):
        return self.tr(
            "Reads the selected intersection centerlines and their linked "
            "regulatory elements, works out who gives way to whom under "
            "German traffic rules (signals > signs > right-before-left, plus "
            "left-turn and 1002-xx priority-course handling), and writes the "
            "result into the yield_to column.\n\n"
            "Select the features of ONE intersection before running.")

    def tr(self, string):
        return QCoreApplication.translate("PopulateYieldToAlgorithm", string)

    # --------------------------------------------------------- parameters --

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.CENTERLINES, self.tr("Centerline layer"),
            [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.REGULATORY, self.tr("Regulatory-element layer (optional, if separate)"),
            [QgsProcessing.TypeVectorAnyGeometry], optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SELECTED_ONLY, self.tr("Use the selected features only"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterString(
            self.YIELD_FIELD, self.tr("Output column"), defaultValue="yield_to"))
        self.addParameter(QgsProcessingParameterNumber(
            self.CLEARANCE,
            self.tr("Conflict clearance (map units) - paths closer than this conflict"),
            QgsProcessingParameterNumber.Double, defaultValue=1.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.ADD_YIELD_LINES, self.tr("Add a 'Yield lines' layer"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DRY_RUN, self.tr("Report only, do not write the column"),
            defaultValue=False))

    # ------------------------------------------------------------- the run --

    def processAlgorithm(self, parameters, context, feedback):
        from yield_pipeline import (PipelineConfig, build_junction, evaluate,
                                    build_yield_to_map, build_yield_lines,
                                    summarise)
        from yield_pipeline.config import GeometryConfig, SchemaConfig
        from yield_pipeline import qgis_adapter as qa
        from yield_pipeline.extract import ExtractionError

        centerlines = self.parameterAsVectorLayer(parameters, self.CENTERLINES, context)
        regulatory = self.parameterAsVectorLayer(parameters, self.REGULATORY, context)
        selected_only = self.parameterAsBool(parameters, self.SELECTED_ONLY, context)
        yield_field = self.parameterAsString(parameters, self.YIELD_FIELD, context)
        clearance = self.parameterAsDouble(parameters, self.CLEARANCE, context)
        add_lines = self.parameterAsBool(parameters, self.ADD_YIELD_LINES, context)
        dry_run = self.parameterAsBool(parameters, self.DRY_RUN, context)

        cfg = PipelineConfig(
            schema=SchemaConfig(yield_to=yield_field or "yield_to"),
            geometry=GeometryConfig(conflict_clearance=clearance))

        layers = [centerlines] + ([regulatory] if regulatory else [])
        feedback.pushInfo("Reading features...")
        features = qa.collect_features(layers, selected_only)
        if not features:
            raise QgsProcessingException(
                "Nothing to work with. Select the intersection's features, or "
                "untick 'Use the selected features only'.")
        feedback.pushInfo(f"  {len(features)} feature(s) read")

        # ---- Epic 1: extract and confirm the intersection ----------------
        feedback.setProgress(15)
        try:
            junction = build_junction(features, cfg)
        except ExtractionError as exc:
            raise QgsProcessingException(str(exc))

        feedback.pushInfo(
            f"Intersection confirmed: {len(junction.arms)} arm(s), "
            f"{len([m for m in junction.movements if not m.is_vru])} movement(s)")

        # ---- Epics 2-5: the rule engine ----------------------------------
        feedback.setProgress(45)
        result = evaluate(junction, cfg)
        feedback.pushInfo(summarise(result, cfg))

        for warning in result.warnings:
            feedback.reportError(f"WARNING: {warning}", fatalError=False)

        # ---- Epic 6: write out -------------------------------------------
        feedback.setProgress(75)
        cells = build_yield_to_map(result, cfg)
        written = 0
        if dry_run:
            feedback.pushInfo("Dry run: the column was not written.")
        else:
            written = qa.write_yield_to(centerlines, cells, cfg)
            qa.commit(centerlines)
            feedback.pushInfo(f"Wrote {written} row(s) into '{cfg.schema.yield_to}'.")

        lines = build_yield_lines(result, cfg)
        if add_lines and lines:
            layer = qa.yield_lines_layer(lines, centerlines.crs())
            qa.style_yield_lines(layer)
            QgsProject.instance().addMapLayer(layer)
            feedback.pushInfo(f"Added {len(lines)} yield line(s).")

        feedback.setProgress(100)
        return {
            "ARMS": {n: a.priority.name for n, a in junction.arms.items()},
            "MOVEMENTS": len([m for m in junction.movements if not m.is_vru]),
            "RELATIONS": len(result.relations),
            "ROWS_WRITTEN": written,
            "WARNINGS": result.warnings,
        }
