import math
from collections import defaultdict
from qgis.core import (
    QgsProject,
    QgsSpatialIndex,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsVectorLayer,
    QgsGeometry,
    QgsWkbTypes,
    QgsPointXY,
    QgsFeatureRequest,
)
from PyQt5.QtCore import QVariant


# ─────────────────────────────────────────────
#  CONFIGURATION  
# ─────────────────────────────────────────────
SNAP_TOLERANCE   = 1e-6   # degrees; points closer than this are "the same"
OUTPUT_MODE      = "layer"  # "layer" | "print" | "field"
OUTPUT_LAYER_NAME = "RBL_yield_results"
# ─────────────────────────────────────────────


def get_active_layer():
    layer = iface.activeLayer()
    if layer is None:
        raise RuntimeError("No active layer. Select your road layer in the Layers panel.")
    if layer.geometryType() != QgsWkbTypes.LineGeometry:
        raise RuntimeError("Active layer must be a LINE layer.")
    return layer


def filter_features(layer):
    """Return only centerline + intersection features."""
    features = []
    for feat in layer.getFeatures():
        lt  = str(feat["lane_type"]      or "").strip().lower()
        lm  = str(feat["lane_morphology"] or "").strip().lower()
        if lt == "centerline" and lm == "intersection":
            features.append(feat)
    print(f"[RBL] Filtered {len(features)} centerline-intersection features.")
    return features


def build_spatial_index(features):
    """Build a QgsSpatialIndex over the filtered feature list."""
    index = QgsSpatialIndex()
    fid_map = {}
    for feat in features:
        index.insertFeature(feat)
        fid_map[feat.id()] = feat
    return index, fid_map


def line_direction_at_point(geom, pt, use_start=True):
    """
    Return the azimuth (degrees, 0 = North, CW) of a linestring
    at its start (use_start=True) or end vertex.
    """
    vertices = [v for v in geom.vertices()]
    if len(vertices) < 2:
        return 0.0
    if use_start:
        p1, p2 = vertices[0],  vertices[1]
    else:
        p1, p2 = vertices[-1], vertices[-2]
    dx = p2.x() - p1.x()
    dy = p2.y() - p1.y()
    return math.degrees(math.atan2(dx, dy)) % 360


def angle_of_approach(geom, intersection_pt):
    """
    Return the direction (degrees) in which the road *arrives* at the
    intersection point (i.e. the bearing pointing INTO the junction).
    We pick whichever endpoint of the line is closest to intersection_pt.
    """
    vertices = [v for v in geom.vertices()]
    if len(vertices) < 2:
        return 0.0

    start = QgsPointXY(vertices[0].x(),  vertices[0].y())
    end   = QgsPointXY(vertices[-1].x(), vertices[-1].y())
    ipt   = QgsPointXY(intersection_pt.x(), intersection_pt.y())

    dist_start = start.distance(ipt)
    dist_end   = end.distance(ipt)

    if dist_start < dist_end:
        # Intersection is near the start  →  road arrives from start
        # approach bearing = direction from vertex[1] toward vertex[0]
        dx = vertices[0].x() - vertices[1].x()
        dy = vertices[0].y() - vertices[1].y()
    else:
        # Intersection is near the end  →  road arrives from end
        dx = vertices[-1].x() - vertices[-2].x()
        dy = vertices[-1].y() - vertices[-2].y()

    return math.degrees(math.atan2(dx, dy)) % 360


def normalise_angle(a):
    return a % 360


def is_on_right(bearing_ego, bearing_other):
    """
    Return True if 'other' comes from the RIGHT of 'ego'.
    Relative angle: positive = clockwise = right side.
    A road is on the right if its relative bearing is in (0°, 180°).
    """
    rel = normalise_angle(bearing_other - bearing_ego)
    return 0 < rel < 180

def get_turn_direction(geom, intersection_point):

    line = geom.asPolyline()

    if len(line) < 3:
        return "straight"

    # find closest point index
    min_dist = float("inf")
    idx = 0

    for i, pt in enumerate(line):
        dist = pt.distance(intersection_point)
        if dist < min_dist:
            min_dist = dist
            idx = i

    if idx == 0 or idx == len(line) - 1:
        return "straight"

    prev_pt = line[idx - 1]
    curr_pt = line[idx]
    next_pt = line[idx + 1]

    angle1 = math.atan2(curr_pt.y() - prev_pt.y(), curr_pt.x() - prev_pt.x())
    angle2 = math.atan2(next_pt.y() - curr_pt.y(), next_pt.x() - curr_pt.x())

    angle = math.degrees(angle2 - angle1)

    if angle > 20:
        return "left"
    elif angle < -20:
        return "right"
    else:
        return "straight"


def find_intersection_point(geom_a, geom_b):
    """Return the intersection geometry (may be Point or MultiPoint)."""
    inter = geom_a.intersection(geom_b)
    if inter is None or inter.isEmpty():
        return None
    return inter


def centroid_of_intersection(inter_geom):
    """Get a single representative point from the intersection geometry."""
    
    if inter_geom.isEmpty():
        return None

    geom_type = QgsWkbTypes.geometryType(inter_geom.wkbType())

    # Point
    if geom_type == QgsWkbTypes.PointGeometry:
        if QgsWkbTypes.isMultiType(inter_geom.wkbType()):
            pts = inter_geom.asMultiPoint()
            if pts:
                return pts[0]
        else:
            return inter_geom.asPoint()

    # LineString (overlapping lines)
    elif geom_type == QgsWkbTypes.LineGeometry:
        return inter_geom.interpolate(inter_geom.length() / 2).asPoint()

    # Polygon or anything else
    return inter_geom.centroid().asPoint()


def apply_rbl(features, index, fid_map, layer):
    results = defaultdict(set)
    processed = set()

    for feat_a in features:
        fid_a = feat_a.id()
        geom_a = feat_a.geometry()
        bbox_a = geom_a.boundingBox()
        bbox_a.grow(SNAP_TOLERANCE * 10)

        candidates = index.intersects(bbox_a)

        for fid_b in candidates:
            if fid_b == fid_a:
                continue

            pair = frozenset([fid_a, fid_b])
            if pair in processed:
                continue
            processed.add(pair)

            feat_b = fid_map[fid_b]
            geom_b = feat_b.geometry()

            inter = find_intersection_point(geom_a, geom_b)
            if inter is None:
                continue

            ipt = centroid_of_intersection(inter)
            turn_a = get_turn_direction(geom_a, ipt)
            turn_b = get_turn_direction(geom_b, ipt)

            # detect opposite directions
            angle_diff = abs(bearing_a - bearing_b)
            if angle_diff > 150:

                if turn_a == "left" and turn_b in ["straight", "right"]:
                    yielder, priority = feat_a, feat_b

                elif turn_b == "left" and turn_a in ["straight", "right"]:
                    yielder, priority = feat_b, feat_a

                else:
                    # fallback to RBL
                    pass
            if ipt is None:
                continue

            bearing_a = angle_of_approach(geom_a, ipt)
            bearing_b = angle_of_approach(geom_b, ipt)

            b_right_of_a = is_on_right(bearing_a, bearing_b)
            a_right_of_b = is_on_right(bearing_b, bearing_a)

            if b_right_of_a:
                yielder, priority = feat_a, feat_b
            elif a_right_of_b:
                yielder, priority = feat_b, feat_a
            else:
                continue

            road_id = str(yielder["road_id"])
            way_id = str(yielder["way_id"])

            yield_val = f"{priority['road_id']}_{priority['way_id']}"
            results[(road_id, way_id)].add(yield_val)

    # pedestrian + cycle logic
    add_pedestrian_cycle_yield(results, layer)

    return results

def add_pedestrian_cycle_yield(results, layer):

    roads = [f for f in layer.getFeatures() if f["lane_type"] == "centerline"]
    pedestrians = [f for f in layer.getFeatures() if f["lane_type"] == "pedestrian_marking"]
    cycles = [f for f in layer.getFeatures() if f["lane_type"] == "cycle_lane"]

    for road in roads:
        road_geom = road.geometry()
        road_id = str(road["road_id"])
        way_id = str(road["way_id"])

        key = (road_id, way_id)

        # pedestrian
        for ped in pedestrians:
            if road_geom.intersects(ped.geometry()):
                results[key].add(str(ped["road_id"]))

        # cycle
        for cycle in cycles:
            if road_geom.intersects(cycle.geometry()):
                results[key].add(
                    f"{cycle['road_id']}_{cycle['way_id']}"
                )

# ── Output helpers ────────────────────────────────────────────────────────────

def print_results(results):
    print("\n{'─'*72}")
    print(f"{'road_id':<20} {'way_id':<20} {'yields_to_way_id':<20}")
    print("─" * 72)
    for r in results:
        print(f"{r['road_id']:<20} {r['way_id']:<20} {r['yields_to_way_id']:<20}")
    print(f"─" * 72)
    print(f"Total yielding relationships found: {len(results)}")


def write_to_field(layer, results):
    """Write yields_to_way_id back into the 'yield_to' field."""
    # Build lookup: fid_yielder → yields_to_way_id
    lookup = {r["fid_yielder"]: r["yields_to_way_id"] for r in results}

    layer.startEditing()
    field_idx = layer.fields().indexOf("yield_to")
    if field_idx == -1:
        # Add field if missing
        layer.dataProvider().addAttributes([QgsField("yield_to", QVariant.String)])
        layer.updateFields()
        field_idx = layer.fields().indexOf("yield_to")

    for feat in layer.getFeatures():
        if feat.id() in lookup:
            layer.changeAttributeValue(feat.id(), field_idx, lookup[feat.id()])

    layer.commitChanges()
    print(f"[RBL] 'yield_to' field updated for {len(lookup)} features.")


def create_output_layer(results, source_crs):

    layer_uri = f"Point?crs={source_crs.authid()}"
    mem_layer = QgsVectorLayer(layer_uri, OUTPUT_LAYER_NAME, "memory")
    dp = mem_layer.dataProvider()

    dp.addAttributes([
        QgsField("road_id", QVariant.String),
        QgsField("way_id", QVariant.String),
        QgsField("yield_to", QVariant.String),
    ])

    mem_layer.updateFields()

    feats_out = []

    for (road_id, way_id), yield_vals in results.items():

        f = QgsFeature(mem_layer.fields())

        f["road_id"] = road_id
        f["way_id"] = way_id
        f["yield_to"] = ",".join(sorted(yield_vals))

        feats_out.append(f)

    dp.addFeatures(feats_out)

    mem_layer.updateExtents()
    QgsProject.instance().addMapLayer(mem_layer)

    print(f"[RBL] Output layer created with {len(feats_out)} rows")

    return mem_layer


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    print("[RBL] Starting Right-Before-Left analysis …")

    layer    = get_active_layer()
    features = filter_features(layer)

    if not features:
        print("[RBL] No matching features found. Check lane_type / lane_morphology values.")
        return

    index, fid_map = build_spatial_index(features)
    results = apply_rbl(features, index, fid_map, layer)

    print(f"[RBL] Found {len(results)} yielding relationships.")

    if OUTPUT_MODE == "print":
        print_results(results)
    elif OUTPUT_MODE == "field":
        write_to_field(layer, results)
    elif OUTPUT_MODE == "layer":
        create_output_layer(results, layer.crs())
    else:
        print("[RBL] Unknown OUTPUT_MODE. Set to 'print', 'field', or 'layer'.")

    print("[RBL] Done.")


run()
