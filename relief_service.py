# -*- coding: utf-8 -*-
"""
Acc\u00e8s au service ArcGIS Living Atlas "World Elevation 3D / Terrain3D" (Esri),
pour la fonctionnalit\u00e9 relief de "Sen Couverture Terrestre".

Service public, sans cl\u00e9 API. ATTENTION : la pr\u00e9cision de ce service a \u00e9t\u00e9
test\u00e9e sur une commune du S\u00e9n\u00e9gal (YOFF) et s'est r\u00e9v\u00e9l\u00e9e tr\u00e8s en dessous
de la r\u00e9alit\u00e9 (relief quasi plat 1-2m au lieu de 0-46m r\u00e9els, confirm\u00e9 par
comparaison avec un MNT Copernicus local et par le endpoint identify montrant
l'utilisation d'une couche de survol basse r\u00e9solution, "WorldDTM_OV256", pour
cette zone). \u00c0 utiliser avec prudence, en particulier pour l'indice d'inondation.

Service : https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer
"""

import json
import os
import urllib.parse
import urllib.request
import urllib.error

IMAGE_SERVER_URL = "https://elevation3d.arcgis.com/arcgis/rest/services/WorldElevation3D/Terrain3D/ImageServer"


class ReliefApiError(Exception):
    pass


def _compute_export_size(bbox, max_dim=1500):
    """Calcule une taille (largeur, hauteur) en pixels respectant le ratio du bbox,
    plafonn\u00e9e \u00e0 max_dim sur le plus grand c\u00f4t\u00e9."""
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


def export_relief_geotiff(bbox_4326, out_path, out_sr=32628, max_dim=1500, timeout=60):
    """
    T\u00e9l\u00e9charge un extrait GeoTIFF d'altitude pour une emprise donn\u00e9e, via le
    service ArcGIS World Elevation 3D (Esri Living Atlas). Voir avertissement
    de pr\u00e9cision dans le docstring du module.

    :param bbox_4326: (xmin, ymin, xmax, ymax) en EPSG:4326
    :param out_path: chemin du fichier .tif \u00e0 \u00e9crire
    :param out_sr: code EPSG de sortie (32628 = UTM 28N)
    :param max_dim: dimension max (en pixels) du plus grand c\u00f4t\u00e9 de l'image export\u00e9e
    :return: out_path
    """
    width, height = _compute_export_size(bbox_4326, max_dim=max_dim)
    xmin, ymin, xmax, ymax = bbox_4326

    params = {
        "f": "image",
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR": 4326,
        "imageSR": out_sr,
        "size": f"{width},{height}",
        "format": "tiff",
        "pixelType": "F32",
        "noData": "-9999",
        "noDataInterpretation": "esriNoDataMatchAny",
        "interpolation": "RSP_BilinearInterpolation",
    }

    query = urllib.parse.urlencode(params)
    url = f"{IMAGE_SERVER_URL}/exportImage?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "QGIS-SenCouvertureTerrestre/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read()
    except urllib.error.URLError as e:
        raise ReliefApiError(f"Erreur r\u00e9seau lors de l'export du MNT : {e}") from e

    if "image" not in content_type:
        try:
            payload = json.loads(data.decode("utf-8"))
            msg = payload.get("error", {}).get("message", str(payload))
        except Exception:
            msg = data[:300]
        raise ReliefApiError(f"Le service d'\u00e9l\u00e9vation n'a pas renvoy\u00e9 d'image : {msg}")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)

    return out_path
