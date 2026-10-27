"""
RUN THIS IN THE QGIS PYTHON CONSOLE.

    Plugins -> Python Console   (Ctrl+Alt+P)
    click "Show Editor", open this file, press Run.

HOW TO USE IT
    1. Select the intersection on the canvas (drag a box round it).
    2. Press Run.

That is all. The script works out the arms, the directions and the yield
rules by itself. If your selection happens to cover more than one junction it
processes each one separately rather than mixing them together.

STEP 1 - set this once: the folder that CONTAINS the 'yield_pipeline' folder.
"""

PIPELINE_PATH = r"/home/mai/Desktop/Annotation rules/Yield automation/yield_to_pipeline"

# STEP 2 - True = print only, write nothing. Set False once it looks right.
DRY_RUN = False


# --------------------------------------------------------------------------
#  Optional. The defaults are what you want for normal use.
# --------------------------------------------------------------------------

# None = the layer highlighted in the Layers panel. Or name it: "my_lanes"
LAYER_NAME = None

# None = use whatever you SELECTED on the canvas. This is the normal way.
# Set a list only to bypass the selection entirely, e.g. [106, 108, 110].
ROAD_IDS = None

# Paths closer than this (METRES) count as conflicting.
CLEARANCE = 0.2

# The CRS the maths runs in; must be projected (metres), never degrees.
# None = worked out automatically. "EPSG:25832" to pin the German standard.
WORK_CRS = None

# Two selected junctions this far apart (metres) are treated as separate.
JUNCTION_SPACING = 25.0

# Per-feature detail. Turn off once you trust the output.
VERBOSE = True


# ===========================================================================
#  Nothing below here needs editing.
# ===========================================================================

import math
import os
import sys
import traceback

from qgis.core import QgsProject
from qgis.utils import iface


def fail(msg):
    print("")
    print("!" * 70)
    print(msg)
    print("!" * 70)


def banner(text):
    print("\n" + "=" * 70)
    print(text)
    print("=" * 70)


def load_package():
    if not os.path.isdir(PIPELINE_PATH):
        fail(f"PIPELINE_PATH does not exist:\n  {PIPELINE_PATH}")
        return None
    if not os.path.isdir(os.path.join(PIPELINE_PATH, "yield_pipeline")):
        found = [d for d in os.listdir(PIPELINE_PATH)
                 if os.path.isdir(os.path.join(PIPELINE_PATH, d))]
        fail(f"No 'yield_pipeline' folder inside:\n  {PIPELINE_PATH}\n"
             f"That folder contains: {found}")
        return None
    if PIPELINE_PATH not in sys.path:
        sys.path.insert(0, PIPELINE_PATH)
    for name in [m for m in list(sys.modules) if m.startswith("yield_pipeline")]:
        del sys.modules[name]
    import yield_pipeline
    return yield_pipeline


def pick_layer():
    if LAYER_NAME:
        matches = QgsProject.instance().mapLayersByName(LAYER_NAME)
        if not matches:
            names = [l.name() for l in QgsProject.instance().mapLayers().values()]
            fail(f"No layer called '{LAYER_NAME}'. Layers: {names}")
            return None
        return matches[0]
    layer = iface.activeLayer()
    if layer is None:
        names = [l.name() for l in QgsProject.instance().mapLayers().values()]
        fail(f"No layer is active. Click one in the Layers panel.\nLayers: {names}")
    return layer


def midpoint(record):
    pts = record.get("geometry") or []
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def cluster_by_distance(records, spacing):
    """Group features into junctions by how close they sit to each other."""
    groups = []
    for rec in records:
        mid = midpoint(rec)
        if mid is None:
            continue
        for g in groups:
            if math.dist(mid, g["mid"]) < spacing:
                g["items"].append(rec)
                g["mids"].append(mid)
                g["mid"] = (sum(p[0] for p in g["mids"]) / len(g["mids"]),
                            sum(p[1] for p in g["mids"]) / len(g["mids"]))
                break
        else:
            groups.append({"mid": mid, "items": [rec], "mids": [mid]})
    return groups


def run_one(yp, layer, features, cfg, label, work_crs):
    """Run the pipeline over one junction's features and report."""
    from yield_pipeline import qgis_adapter as qa
    from yield_pipeline.extract import ExtractionError

    banner(label)

    tally = {}
    for f in features:
        key = (str(f["attributes"].get("lane_type")),
               str(f["attributes"].get("lane_morphology")))
        tally[key] = tally.get(key, 0) + 1
    for (lt, lm), n in sorted(tally.items()):
        print(f"  {n:>4}  lane_type={lt!r:22} lane_morphology={lm!r}")

    try:
        junction = yp.build_junction(features, cfg)
    except ExtractionError as exc:
        fail(f"Extraction stopped: {exc}")
        return None
    except Exception:
        fail("Unexpected error:\n\n" + traceback.format_exc())
        return None

    result = yp.evaluate(junction, cfg)
    print()
    print(yp.summarise(result, cfg))

    if VERBOSE:
        print("\n  How each movement was read")
        print(f"  {'road/way':<16}{'move':<9}{'turn':<9}{'dot_path'}")
        for mv in sorted(junction.movements, key=lambda m: m.key):
            if mv.is_vru:
                continue
            print(f"  {mv.road_id}/{mv.way_id:<10}{mv.key:<9}{mv.turn.value:<9}{mv.dot_path}")

        keys = [m.key for m in junction.movements if not m.is_vru]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            print(f"\n  WARNING: repeated movements {dupes}. Two lanes reading")
            print(f"  as the same manoeuvre means a direction was read wrongly.")

    cells = yp.build_yield_to_map(result, cfg)
    print("\n  yield_to")
    for mv in sorted(junction.movements, key=lambda m: m.key):
        if mv.is_vru:
            continue
        print(f"    {mv.road_id}/{mv.way_id:<10} {mv.key:<9} -> "
              f"{cells[mv.feature_id] or '(empty)'}")

    if DRY_RUN:
        print("\n  DRY_RUN is True - nothing written.")
        return result

    written = qa.write_yield_to(layer, cells, cfg)
    qa.commit(layer)
    print(f"\n  Wrote {written} row(s) into '{cfg.schema.yield_to}'.")

    lines = yp.build_yield_lines(result, cfg)
    if lines:
        # The geometry was computed in the working CRS, so the new layer must
        # declare that CRS - not the source layer's - or it lands in the sea.
        lyr = qa.yield_lines_layer(lines, work_crs, f"Yield lines - {label}")
        qa.style_yield_lines(lyr)
        QgsProject.instance().addMapLayer(lyr)
        print(f"  Added {len(lines)} yield line(s).")
    return result


def main():
    banner("yield_to pipeline")

    yp = load_package()
    if yp is None:
        return
    from yield_pipeline import qgis_adapter as qa
    from yield_pipeline.config import GeometryConfig, PipelineConfig

    layer = pick_layer()
    if layer is None:
        return
    print(f"Layer : {layer.name()}")

    # ---- working CRS ---------------------------------------------------
    if WORK_CRS:
        from qgis.core import QgsCoordinateReferenceSystem
        work_crs = QgsCoordinateReferenceSystem(WORK_CRS)
        if not work_crs.isValid() or work_crs.isGeographic():
            fail(f"WORK_CRS {WORK_CRS!r} is not a projected (metric) CRS.")
            return
    else:
        work_crs, _ = qa.metric_crs_for(layer)
        if work_crs.isGeographic():
            fail("Could not find a metric CRS. Set WORK_CRS, e.g. 'EPSG:25832'.")
            return
    print(f"CRS   : {layer.crs().authid()} -> working in {work_crs.authid()}")

    cfg = PipelineConfig(geometry=GeometryConfig(conflict_clearance=CLEARANCE))

    # ---- what the user picked -------------------------------------------
    if ROAD_IDS:
        wanted = {str(r).strip() for r in ROAD_IDS}
        everything = qa.collect_features([layer], False, work_crs)
        chosen = [f for f in everything
                  if str(f["attributes"].get("road_id")) in wanted]
        print(f"Input : road_id filter {sorted(wanted)} -> {len(chosen)} feature(s)")
        if not chosen:
            fail(f"No features with road_id in {sorted(wanted)}.")
            return
    else:
        count = layer.selectedFeatureCount()
        print(f"Input : {count} feature(s) selected on the canvas")
        if count == 0:
            fail("Nothing is selected.\n\n"
                 "Select the intersection on the canvas - drag a box round it\n"
                 "with the Select tool - then press Run again.")
            return
        chosen = qa.collect_features([layer], True, work_crs)

    # Signs and lights are small and often fall outside the box, so take every
    # regulatory element on the layer; each junction keeps only the ones its
    # own lanes reference by re_id.
    regs = qa.collect_regulatory_elements(layer, cfg, work_crs)

    # ---- split into junctions -------------------------------------------
    lanes = [f for f in chosen
             if str(f["attributes"].get("lane_type", "")).lower()
             == cfg.schema.centerline_value
             and str(f["attributes"].get("lane_morphology", "")).lower()
             == cfg.schema.intersection_value]
    if not lanes:
        fail(f"The selection has no centerlines with "
             f"lane_morphology='{cfg.schema.intersection_value}'.\n\n"
             f"Either this is not an intersection, or the selection missed\n"
             f"the lanes inside it. Select across the junction itself.")
        return

    groups = cluster_by_distance(lanes, JUNCTION_SPACING)
    others = [f for f in chosen if f not in lanes]

    if len(groups) > 1:
        print(f"\nThe selection covers {len(groups)} separate junctions "
              f"(more than {JUNCTION_SPACING:.0f} m apart).")
        print("Each is processed on its own - they are NOT mixed together.")

    results = []
    for i, g in enumerate(sorted(groups, key=lambda g: -len(g["items"])), 1):
        members = list(g["items"])

        # road borders and anything else selected near this junction
        for f in others:
            mid = midpoint(f)
            if mid and math.dist(mid, g["mid"]) < JUNCTION_SPACING * 2:
                members.append(f)

        # the regulatory elements these lanes actually name
        wanted_re = set()
        for lane in g["items"]:
            rule = lane["attributes"].get(cfg.schema.traffic_rule)
            if rule:
                wanted_re.update(
                    x.strip() for x in
                    str(rule).replace(";", ",").split(",") if x.strip())
        members += [r for r in regs
                    if str(r["attributes"].get(cfg.schema.re_id)) in wanted_re]

        roads = sorted({str(l["attributes"].get("road_id")) for l in g["items"]})
        label = (f"Junction {i} of {len(groups)}  -  roads {', '.join(roads)}"
                 if len(groups) > 1 else f"Junction - roads {', '.join(roads)}")
        res = run_one(yp, layer, qa.merge_unique(members), cfg, label, work_crs)
        if res:
            results.append(res)

    banner(f"Done. {len(results)} junction(s) processed."
           + ("  DRY_RUN is on - nothing was written." if DRY_RUN else ""))


try:
    main()
except Exception:
    fail("Something went wrong:\n\n" + traceback.format_exc())
