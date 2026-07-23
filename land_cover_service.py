# -*- coding: utf-8 -*-
"""
Accès au service ArcGIS Living Atlas "Sentinel-2 10m Land Use/Land Cover Time Series"
(Impact Observatory / Esri / Microsoft), pour la fonctionnalité "Sen Couverture Terrestre".

Service : https://ic.imagery1.arcgis.com/arcgis/rest/services/Sentinel2_10m_LandCover/ImageServer
Mosaïque temporelle par attribut "Year" (2017-2024), résolution 10 m, 9 classes thématiques.
"""

import json
import os
import urllib.parse
import urllib.request
import urllib.error

IMAGE_SERVER_URL = "https://ic.imagery1.arcgis.com/arcgis/rest/services/Sentinel2_10m_LandCover/ImageServer"

MIN_YEAR = 2017
MAX_YEAR = 2024

# Classes thématiques officielles Esri/Impact Observatory (valeur de pixel -> (label, couleur hex))
LULC_CLASSES = {
    1:  ("Eau",                         "#1A5BAB"),
    2:  ("Arbres",                      "#358221"),
    4:  ("Végétation inondée",          "#87D19E"),
    5:  ("Cultures",                    "#FFDB5C"),
    7:  ("Zones bâties",                "#ED022A"),
    8:  ("Sol nu",                      "#EDE9E4"),
    9:  ("Neige / glace",               "#F2FAFF"),
    10: ("Nuages",                      "#C8C8C8"),
    11: ("Végétation herbacée / savane", "#C6AD8D"),
}

# Emprise approximative du Sénégal en EPSG:4326 (xmin, ymin, xmax, ymax)
SENEGAL_BBOX_4326 = (-17.6, 12.0, -11.3, 16.7)


class LandCoverServiceError(Exception):
    pass


def _build_mosaic_rule(year):
    return {
        "mosaicMethod": "esriMosaicAttribute",
        "sortField": "Year",
        "sortValue": year,
        "ascending": True,
        "mosaicOperation": "MT_FIRST",
        "where": f"Year={year}",
    }


def _compute_export_size(bbox, max_dim=2000):
    """Calcule une taille (largeur, hauteur) en pixels respectant le ratio du bbox,
    plafonnée à max_dim sur le plus grand côté."""
    xmin, ymin, xmax, ymax = bbox
    dx = max(xmax - xmin, 1e-9)
    dy = max(ymax - ymin, 1e-9)
    if dx >= dy:
        width = max_dim
        height = max(1, int(max_dim * dy / dx))
    else:
        height = max_dim
        width = max(1, int(max_dim * dx / dy))
    return width, height


def export_landcover_geotiff(bbox_4326, year, out_path, out_sr=32628, max_dim=2000, timeout=60):
    """
    Télécharge un extrait GeoTIFF (valeurs de classe brutes, pas de rendu couleur)
    de la couverture terrestre pour une année donnée.

    :param bbox_4326: (xmin, ymin, xmax, ymax) en EPSG:4326
    :param year: année (2017-2024)
    :param out_path: chemin du fichier .tif à écrire
    :param out_sr: code EPSG de sortie (32628 = UTM 28N, CRS de référence des projets Sen Hydro)
    :param max_dim: dimension max (en pixels) du plus grand côté de l'image exportée
    :return: out_path
    """
    if not (MIN_YEAR <= year <= MAX_YEAR):
        raise LandCoverServiceError(f"Année hors plage ({MIN_YEAR}-{MAX_YEAR}) : {year}")

    width, height = _compute_export_size(bbox_4326, max_dim=max_dim)
    xmin, ymin, xmax, ymax = bbox_4326

    params = {
        "f": "image",
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR": 4326,
        "imageSR": out_sr,
        "size": f"{width},{height}",
        "format": "tiff",
        "pixelType": "U8",
        "noData": "0",
        "noDataInterpretation": "esriNoDataMatchAny",
        "interpolation": "RSP_NearestNeighbor",
        "mosaicRule": json.dumps(_build_mosaic_rule(year)),
    }

    query = urllib.parse.urlencode(params)
    url = f"{IMAGE_SERVER_URL}/exportImage?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "QGIS-SenCouvertureTerrestre/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read()
    except urllib.error.URLError as e:
        raise LandCoverServiceError(f"Erreur réseau lors de l'export : {e}") from e

    if "image" not in content_type:
        # Le service a probablement renvoyé une erreur JSON plutôt qu'une image
        try:
            payload = json.loads(data.decode("utf-8"))
            msg = payload.get("error", {}).get("message", str(payload))
        except Exception:
            msg = data[:300]
        raise LandCoverServiceError(f"Le service n'a pas renvoyé d'image : {msg}")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)

    return out_path
