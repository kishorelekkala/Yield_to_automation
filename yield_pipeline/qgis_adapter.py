"""
QGIS bindings.

The rest of the package never imports qgis, so it stays unit-testable. This
module is the only place that knows about QgsFeature, QgsVectorLayer and the
editing session.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import PipelineConfig, DEFAULT_CONFIG

Point = Tuple[float, float]


# ----------------------------------------------------------------- read -----

def metric_crs_for(layer):
    """Pick a projected CRS in metres for a layer that is in degrees.

    The whole rule engine works in metres: bearings, the 1 m conflict
    clearance, turn classification. In a geographic CRS (EPSG:4326) one degree
    of longitude is much shorter than one degree of latitude - at latitude 54
    the x axis is squashed by a factor of 1.69 - which rotates every bearing by
    up to 14 degrees. That is enough to merge two arms into one and to make
    every turn look straight.

    So: if the layer is already projected, use it unchanged. If it is in
    degrees, pick the UTM zone its centre falls in.
    """
    from qgis.core import QgsCoordinateReferenceSystem

    crs = layer.crs()
    if not crs.isGeographic():
        return crs, False

    extent = layer.extent()
    lon = (extent.xMinimum() + extent.xMaximum()) / 2.0
    lat = (extent.yMinimum() + extent.yMaximum()) / 2.0
    zone = int((lon + 180.0) / 6.0) + 1
    epsg = (32600 if lat >= 0 else 32700) + zone
    return QgsCoordinateReferenceSystem(f"EPSG:{epsg}"), True


def make_transform(layer, target_crs):
    """QgsCoordinateTransform from the layer to the working CRS, or None."""
    from qgis.core import QgsCoordinateTransform, QgsProject

    if target_crs is None or target_crs == layer.crs():
        return None
    return QgsCoordinateTransform(layer.crs(), target_crs, QgsProject.instance())


def geometry_to_points(geom, transform=None) -> List[Point]:
    """QgsGeometry -> list of (x, y). Takes the longest part of a multipart.

    If `transform` is given the geometry is reprojected first, so the rest of
    the pipeline always sees metres.
    """
    if geom is None or geom.isEmpty():
        return []
    if transform is not None:
        geom = QgsGeometryHelper.transformed(geom, transform)
        if geom is None:
            return []
    try:
        if geom.isMultipart():
            parts = geom.asMultiPolyline()
            if not parts:
                return []
            part = max(parts, key=len)
        else:
            part = geom.asPolyline()
    except Exception:                                   # noqa: BLE001
        return []
    return [(p.x(), p.y()) for p in part]


class QgsGeometryHelper:
    """Small wrapper so the transform failure mode is explicit."""

    @staticmethod
    def transformed(geom, transform):
        from qgis.core import QgsGeometry
        clone = QgsGeometry(geom)
        if clone.transform(transform) != 0:
            return None
        return clone


def feature_to_dict(feature, field_names: Sequence[str], transform=None) -> dict:
    attributes = {}
    for name in field_names:
        try:
            value = feature[name]
        except KeyError:
            continue
        # QGIS NULL compares equal to None through this guard
        attributes[name] = None if (value is None or repr(value) == "NULL") else value
    attributes["fid"] = feature.id()
    return {"attributes": attributes,
            "geometry": geometry_to_points(feature.geometry(), transform)}


def features_from_layer(layer, selected_only: bool = True,
                        transform=None) -> List[dict]:
    """Pull the selection (or the whole layer) out as plain dicts."""
    names = [f.name() for f in layer.fields()]
    source = layer.selectedFeatures() if selected_only else layer.getFeatures()
    out = []
    for feature in source:
        record = feature_to_dict(feature, names, transform)
        record["attributes"]["__layer_id"] = layer.id()
        out.append(record)
    return out


def collect_features(layers: Iterable, selected_only: bool = True,
                     target_crs=None) -> List[dict]:
    """Gather several layers into one feature list, reprojected to metres.

    Feature ids are only unique within a layer, so they are namespaced here.
    """
    out: List[dict] = []
    for layer in layers:
        if layer is None:
            continue
        transform = make_transform(layer, target_crs)
        for record in features_from_layer(layer, selected_only, transform):
            record["attributes"]["fid"] = (
                f"{layer.id()}:{record['attributes']['fid']}")
            out.append(record)
    return out


def collect_regulatory_elements(layer, cfg, target_crs=None) -> List[dict]:
    """Every regulatory element on the LAYER, not just the selection.

    A sign or light is a small feature that often sits outside the box the
    user drags round the junction, so requiring it to be selected is a trap:
    the centerline names an re_id, the element is not in the selection, and
    the arm silently drops to UNREGULATED. Scanning the whole layer for them
    costs one pass and removes the failure mode entirely.
    """
    from qgis.core import QgsFeatureRequest

    field = cfg.schema.lane_type
    if layer.fields().indexFromName(field) < 0:
        return []

    want = cfg.schema.regulatory_element_value.replace("'", "''")
    request = QgsFeatureRequest().setFilterExpression(
        f'lower("{field}") = \'{want.lower()}\'')
    names = [f.name() for f in layer.fields()]
    transform = make_transform(layer, target_crs)

    out = []
    for feature in layer.getFeatures(request):
        record = feature_to_dict(feature, names, transform)
        record["attributes"]["fid"] = f"{layer.id()}:{record['attributes']['fid']}"
        record["attributes"]["__layer_id"] = layer.id()
        out.append(record)
    return out


def merge_unique(*feature_lists) -> List[dict]:
    """Concatenate feature lists, keeping the first copy of each fid."""
    seen, out = set(), []
    for features in feature_lists:
        for record in features:
            fid = record["attributes"].get("fid")
            if fid in seen:
                continue
            seen.add(fid)
            out.append(record)
    return out


# ---------------------------------------------------------------- write -----

def ensure_field(layer, name: str, length: int = 2000) -> bool:
    """Add the yield_to field if the layer has not got one. Returns True if added."""
    from qgis.core import QgsField
    from qgis.PyQt.QtCore import QVariant

    if layer.fields().indexFromName(name) >= 0:
        return False
    layer.startEditing()
    layer.addAttribute(QgsField(name, QVariant.String, len=length))
    layer.updateFields()
    return True


def write_yield_to(layer, cells: Dict[str, str],
                   cfg: PipelineConfig = DEFAULT_CONFIG) -> int:
    """Write the yield_to cells back onto the layer. Returns rows changed.

    `cells` is keyed by the feature_id the pipeline used, which for QGIS
    features is '<layer id>:<fid>'.
    """
    field = cfg.schema.yield_to
    ensure_field(layer, field)
    index = layer.fields().indexFromName(field)
    if index < 0:
        raise RuntimeError(f"could not create or find the '{field}' field")

    prefix = f"{layer.id()}:"
    changed = 0
    if not layer.isEditable():
        layer.startEditing()
    for feature_id, value in cells.items():
        key = str(feature_id)
        if key.startswith(prefix):
            fid = int(key[len(prefix):])
        elif key.isdigit():
            fid = int(key)
        else:
            continue                      # a feature from another layer
        if layer.changeAttributeValue(fid, index, value):
            changed += 1
    return changed


def commit(layer) -> None:
    if layer.isEditable() and not layer.commitChanges():
        errors = "; ".join(layer.commitErrors())
        layer.rollBack()
        raise RuntimeError(f"could not save edits to {layer.name()}: {errors}")


# ------------------------------------------------------------ yield lines ---

YIELD_LINE_FIELDS = [
    ("road_id", "String"), ("way_id", "String"), ("movement", "String"),
    ("turn", "String"), ("yield_count", "Int"), ("yield_to", "String"),
]


def yield_lines_layer(lines: Sequence[dict], crs, name: str = "Yield lines"):
    """Build an in-memory line layer from the pipeline's yield-line records."""
    from qgis.core import (QgsFeature, QgsField, QgsGeometry, QgsPointXY,
                           QgsVectorLayer)
    from qgis.PyQt.QtCore import QVariant

    uri = f"LineString?crs={crs.authid()}" if hasattr(crs, "authid") else "LineString"
    layer = QgsVectorLayer(uri, name, "memory")
    provider = layer.dataProvider()
    qt_type = {"String": QVariant.String, "Int": QVariant.Int}
    provider.addAttributes([QgsField(n, qt_type[t], len=2000 if t == "String" else 10)
                            for n, t in YIELD_LINE_FIELDS])
    layer.updateFields()

    features = []
    for record in lines:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(x, y) for x, y in record["geometry"]]))
        feature.setAttributes([record.get(n) for n, _ in YIELD_LINE_FIELDS])
        features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()
    return layer


def style_yield_lines(layer) -> None:
    """Red bars, thicker where a movement yields to more traffic."""
    from qgis.core import QgsLineSymbol
    symbol = QgsLineSymbol.createSimple({
        "color": "200,30,30,255", "width": "0.9", "capstyle": "flat"})
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()
