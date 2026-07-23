# -*- coding: utf-8 -*-
"""
Fonctions utilitaires pour repérer et exploiter les couches déjà présentes dans le
projet QGIS courant : limites communales, sol/pédologie, relief (MNT).
"""

from qgis.core import (
    QgsProject, QgsWkbTypes, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsGeometry, QgsRectangle
)

NAME_FIELD_HINTS = ["ccrca", "nom", "name", "commune", "comm", "libelle", "label", "adm"]

# Champ prioritaire pour la couche de pédologie Morpho_Pedo (type de sol)
SOIL_FIELD_HINTS = ["msdnom", "nom", "name", "type", "sol", "pedo"]


def guess_soil_field(layer):
    """Devine le champ de type de sol, en priorisant MSDNOM (couche Morpho_Pedo)."""
    field_names = [f.name() for f in layer.fields()]
    lower_map = {f.lower(): f for f in field_names}
    for hint in SOIL_FIELD_HINTS:
        for lower_name, original in lower_map.items():
            if hint in lower_name:
                return original
    return field_names[0] if field_names else None


def guess_soil_layer():
    """Cherche une couche déjà chargée dont le nom contient 'morpho' et/ou 'pedo'."""
    for layer in list_vector_layers():
        lname = layer.name().lower()
        if "morpho" in lname or "pedo" in lname or "sol" in lname:
            return layer
    return None


def list_polygon_vector_layers():
    """Couches vecteur de type polygone chargées dans le projet."""
    layers = []
    for layer in QgsProject.instance().mapLayers().values():
        if layer.type() == layer.VectorLayer and QgsWkbTypes.geometryType(layer.wkbType()) == QgsWkbTypes.PolygonGeometry:
            layers.append(layer)
    return layers


def list_vector_layers():
    return [l for l in QgsProject.instance().mapLayers().values() if l.type() == l.VectorLayer]


def list_raster_layers():
    return [l for l in QgsProject.instance().mapLayers().values() if l.type() == l.RasterLayer]


def guess_name_field(layer):
    """Devine le champ contenant le nom de la commune, par heuristique sur le nom du champ."""
    field_names = [f.name() for f in layer.fields()]
    lower_map = {f.lower(): f for f in field_names}
    for hint in NAME_FIELD_HINTS:
        for lower_name, original in lower_map.items():
            if hint in lower_name:
                return original
    return field_names[0] if field_names else None


def get_unique_values_sorted(layer, field_name):
    idx = layer.fields().indexFromName(field_name)
    if idx < 0:
        return []
    values = layer.uniqueValues(idx)
    return sorted([v for v in values if v not in (None, "")], key=lambda x: str(x))


def get_feature_by_field_value(layer, field_name, value):
    expr = f'"{field_name}" = \'{value}\'' if isinstance(value, str) else f'"{field_name}" = {value}'
    for feat in layer.getFeatures(expr):
        return feat
    return None


def geometry_to_4326(geometry, source_crs):
    """Reprojette une QgsGeometry vers EPSG:4326 et retourne (geometry_4326, wkt_4326, bbox_4326)."""
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    geom = QgsGeometry(geometry)
    if source_crs != wgs84:
        transform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())
        geom.transform(transform)
    rect = geom.boundingBox()
    bbox = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
    return geom, geom.asWkt(), bbox


def buffer_bbox(bbox, fraction=0.05):
    """Ajoute une petite marge autour d'une bbox (xmin, ymin, xmax, ymax)."""
    xmin, ymin, xmax, ymax = bbox
    dx = (xmax - xmin) * fraction
    dy = (ymax - ymin) * fraction
    return (xmin - dx, ymin - dy, xmax + dx, ymax + dy)


def zoom_canvas_to_geometry(iface, geometry, source_crs, margin_fraction=0.15):
    canvas = iface.mapCanvas()
    geom = QgsGeometry(geometry)
    canvas_crs = canvas.mapSettings().destinationCrs()
    if source_crs != canvas_crs:
        transform = QgsCoordinateTransform(source_crs, canvas_crs, QgsProject.instance())
        geom.transform(transform)
    rect = geom.boundingBox()
    rect.scale(1 + margin_fraction)
    canvas.setExtent(rect)
    canvas.refresh()


def compute_min_distance_to_layer(commune_geometry, commune_crs, river_layer, search_margin_deg=0.2):
    """
    Calcule la distance minimale (en mètres) entre la géométrie d'une commune et les
    entités d'une couche linéaire (ex: réseau hydrographique).

    :return: distance en mètres, ou None si aucune entité trouvée à proximité
    """
    from qgis.core import QgsDistanceArea

    river_crs = river_layer.crs()
    geom = QgsGeometry(commune_geometry)
    if commune_crs != river_crs:
        transform = QgsCoordinateTransform(commune_crs, river_crs, QgsProject.instance())
        geom.transform(transform)

    bbox = geom.boundingBox()
    bbox.grow(search_margin_deg if river_crs.isGeographic() else search_margin_deg * 111000)

    da = QgsDistanceArea()
    da.setEllipsoid("WGS84")

    min_dist_m = None
    for feat in river_layer.getFeatures(bbox):
        feat_geom = feat.geometry()
        if feat_geom is None or feat_geom.isEmpty():
            continue
        if feat_geom.intersects(geom):
            return 0.0
        # Distance métrique correcte, y compris pour un CRS géographique
        if river_crs.isGeographic():
            p1 = geom.nearestPoint(feat_geom).asPoint()
            p2 = feat_geom.nearestPoint(geom).asPoint()
            dist = da.measureLine(p1, p2)
        else:
            dist = geom.distance(feat_geom)
        if min_dist_m is None or dist < min_dist_m:
            min_dist_m = dist

    return min_dist_m


def compute_vector_zonal_stats(commune_geometry, commune_crs, target_layer, class_field, area_unit_km2=True):
    """
    Intersecte la géométrie d'une commune avec une couche vecteur (ex: sol/pédologie)
    et retourne la superficie par valeur de classe (class_field).

    :return: liste de dicts {label, area_km2, pct}, triée par superficie décroissante
    """
    target_crs = target_layer.crs()
    geom = QgsGeometry(commune_geometry)
    if commune_crs != target_crs:
        transform = QgsCoordinateTransform(commune_crs, target_crs, QgsProject.instance())
        geom.transform(transform)

    bbox_rect = geom.boundingBox()
    results = {}
    total_area = 0.0

    request_bbox = bbox_rect
    for feat in target_layer.getFeatures(request_bbox):
        feat_geom = feat.geometry()
        if feat_geom is None or feat_geom.isEmpty():
            continue
        if not feat_geom.intersects(geom):
            continue
        inter = feat_geom.intersection(geom)
        if inter is None or inter.isEmpty():
            continue

        area_m2 = inter.area()
        if target_crs.isGeographic():
            # Approximation pour CRS géographiques : recalcul via une transformation
            # vers une projection métrique locale n'est pas garanti disponible ; on
            # utilise QgsDistanceArea pour une estimation correcte sur l'ellipsoïde.
            from qgis.core import QgsDistanceArea
            da = QgsDistanceArea()
            da.setEllipsoid("WGS84")
            area_m2 = da.measureArea(inter)

        class_value = feat[class_field]
        label = str(class_value) if class_value not in (None, "") else "Non renseigné"

        results[label] = results.get(label, 0.0) + area_m2
        total_area += area_m2

    from .soil_fertility import get_fertility_range, format_fertility

    output = []
    for label, area_m2 in results.items():
        area_km2 = area_m2 / 1_000_000.0
        pct = (area_m2 / total_area * 100.0) if total_area else 0.0
        fmin, fmax = get_fertility_range(label)
        output.append({
            "label": label,
            "area_km2": round(area_km2, 2),
            "pct": round(pct, 2),
            "fertility_min": fmin,
            "fertility_max": fmax,
            "fertility_label": format_fertility(label),
        })

    output.sort(key=lambda r: r["area_km2"], reverse=True)
    return output


def format_feature_attributes(feature, exclude_fields=None):
    exclude_fields = exclude_fields or []
    lines = []
    for field in feature.fields():
        name = field.name()
        if name in exclude_fields:
            continue
        value = feature[name]
        if value in (None, ""):
            continue
        lines.append(f"<b>{name}</b> : {value}")
    return "<br>".join(lines)
