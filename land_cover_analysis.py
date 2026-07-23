# -*- coding: utf-8 -*-
"""
Statistiques de superficie par classe LULC et détection de changement entre deux années.
Utilise GDAL/numpy déjà fournis avec QGIS (aucune dépendance externe supplémentaire).
"""

from osgeo import gdal, osr
import numpy as np

from .land_cover_service import LULC_CLASSES


def _read_band_as_array(tif_path):
    ds = gdal.Open(tif_path)
    if ds is None:
        raise RuntimeError(f"Impossible d'ouvrir le raster : {tif_path}")
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray()
    gt = ds.GetGeoTransform()
    pixel_area_m2 = abs(gt[1] * gt[5])
    ds = None
    return arr, pixel_area_m2


def compute_class_areas(tif_path):
    """
    Retourne une liste de dicts {value, label, color, pixels, area_km2, pct}
    pour chaque classe présente dans le raster (hors NoData = 0).
    """
    arr, pixel_area_m2 = _read_band_as_array(tif_path)

    valid_mask = arr != 0
    total_valid_pixels = int(valid_mask.sum())

    values, counts = np.unique(arr[valid_mask], return_counts=True)

    results = []
    for v, c in zip(values, counts):
        v = int(v)
        label, color = LULC_CLASSES.get(v, (f"Classe {v}", "#999999"))
        area_km2 = (c * pixel_area_m2) / 1_000_000.0
        pct = (c / total_valid_pixels * 100.0) if total_valid_pixels else 0.0
        results.append({
            "value": v,
            "label": label,
            "color": color,
            "pixels": int(c),
            "area_km2": round(area_km2, 2),
            "pct": round(pct, 2),
        })

    results.sort(key=lambda r: r["area_km2"], reverse=True)
    return results


def compute_change_stats(tif_path_year1, tif_path_year2):
    """
    Compare deux rasters LULC (même grille/emprise/résolution) et retourne :
      - une matrice numpy 2D de changement (0 = inchangé, 1 = changé, 255 = NoData)
      - le geotransform et la projection du raster de référence (year1)
      - un résumé par classe : superficie gagnée / perdue (km²) entre year1 et year2
    """
    ds1 = gdal.Open(tif_path_year1)
    ds2 = gdal.Open(tif_path_year2)
    if ds1 is None or ds2 is None:
        raise RuntimeError("Impossible d'ouvrir un des deux rasters à comparer.")

    arr1 = ds1.GetRasterBand(1).ReadAsArray()
    arr2 = ds2.GetRasterBand(1).ReadAsArray()

    if arr1.shape != arr2.shape:
        raise RuntimeError(
            "Les deux rasters n'ont pas la même dimension "
            f"({arr1.shape} vs {arr2.shape}). Relance l'export avec la même emprise pour les deux années."
        )

    gt = ds1.GetGeoTransform()
    proj = ds1.GetProjection()
    pixel_area_m2 = abs(gt[1] * gt[5])

    valid = (arr1 != 0) & (arr2 != 0)
    change_map = np.full(arr1.shape, 255, dtype=np.uint8)
    change_map[valid & (arr1 == arr2)] = 0
    change_map[valid & (arr1 != arr2)] = 1

    # Superficie par classe pour chaque année (sur les pixels valides communs)
    class_areas_1 = {}
    class_areas_2 = {}
    for v in set(np.unique(arr1[valid]).tolist()) | set(np.unique(arr2[valid]).tolist()):
        v = int(v)
        class_areas_1[v] = int((arr1[valid] == v).sum()) * pixel_area_m2 / 1_000_000.0
        class_areas_2[v] = int((arr2[valid] == v).sum()) * pixel_area_m2 / 1_000_000.0

    summary = []
    for v in sorted(set(class_areas_1) | set(class_areas_2)):
        label, color = LULC_CLASSES.get(v, (f"Classe {v}", "#999999"))
        a1 = class_areas_1.get(v, 0.0)
        a2 = class_areas_2.get(v, 0.0)
        summary.append({
            "value": v,
            "label": label,
            "color": color,
            "area_km2_year1": round(a1, 2),
            "area_km2_year2": round(a2, 2),
            "delta_km2": round(a2 - a1, 2),
        })
    summary.sort(key=lambda r: abs(r["delta_km2"]), reverse=True)

    changed_pct = float((change_map == 1).sum()) / float(valid.sum()) * 100.0 if valid.sum() else 0.0

    ds1 = None
    ds2 = None

    return {
        "change_map": change_map,
        "geotransform": gt,
        "projection": proj,
        "summary": summary,
        "changed_pct": round(changed_pct, 2),
    }


def _normalize_raster_source(path):
    """QGIS ajoute parfois un suffixe '|option=...' au chemin source d'une couche raster
    (sous-jeux de données, options de provider). On ne garde que le chemin réel."""
    return path.split("|")[0] if path else path


def get_raster_extent_4326(path):
    """Retourne (xmin, ymin, xmax, ymax) de l'emprise d'un raster, reprojetée en EPSG:4326."""
    ds = gdal.Open(_normalize_raster_source(path))
    if ds is None:
        raise RuntimeError(f"Impossible d'ouvrir le raster : {path}")

    gt = ds.GetGeoTransform()
    width, height = ds.RasterXSize, ds.RasterYSize
    xmin = gt[0]
    ymax = gt[3]
    xmax = xmin + gt[1] * width
    ymin = ymax + gt[5] * height

    src_srs = osr.SpatialReference()
    wkt = ds.GetProjection()
    ds = None

    if not wkt:
        # Pas de CRS défini sur le raster : on suppose EPSG:4326 par défaut
        return (min(xmin, xmax), min(ymin, ymax), max(xmin, xmax), max(ymin, ymax))

    src_srs.ImportFromWkt(wkt)
    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromEPSG(4326)
    dst_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    transform = osr.CoordinateTransformation(src_srs, dst_srs)
    corners = [(xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)]
    transformed = [transform.TransformPoint(x, y)[:2] for x, y in corners]
    xs = [p[0] for p in transformed]
    ys = [p[1] for p in transformed]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_intersects(bbox1, bbox2):
    xmin1, ymin1, xmax1, ymax1 = bbox1
    xmin2, ymin2, xmax2, ymax2 = bbox2
    return not (xmax1 < xmin2 or xmax2 < xmin1 or ymax1 < ymin2 or ymax2 < ymin1)


def clip_geotiff_to_geometry(in_path, wkt_geometry, geometry_srs_epsg, out_path,
                              nodata=None, geometry_bbox_4326=None, target_epsg=None):
    """
    Découpe un GeoTIFF sur une géométrie (limite communale) et l'écrit dans out_path.
    La géométrie est fournie en WKT, avec son code EPSG.

    Vérifie d'abord que l'emprise du raster source recouvre bien la géométrie fournie,
    afin de donner un message clair plutôt qu'un résultat vide silencieux.
    """
    real_in_path = _normalize_raster_source(in_path)

    src_ds = gdal.Open(real_in_path)
    if src_ds is None:
        raise RuntimeError(
            f"Impossible d'ouvrir le raster source : {in_path}\n"
            "Vérifie qu'il s'agit bien d'un fichier local (GeoTIFF, IMG...) et non d'un "
            "service distant (WMS/XYZ), qui ne peut pas être découpé directement."
        )

    src_nodata = src_ds.GetRasterBand(1).GetNoDataValue()
    src_ds = None

    if geometry_bbox_4326 is not None and geometry_srs_epsg == 4326:
        try:
            raster_bbox_4326 = get_raster_extent_4326(real_in_path)
            if not _bbox_intersects(raster_bbox_4326, geometry_bbox_4326):
                raise RuntimeError(
                    "Le raster sélectionné ne recouvre pas la zone d'étude (aucune intersection "
                    f"entre son emprise {tuple(round(v, 3) for v in raster_bbox_4326)} et la commune "
                    f"{tuple(round(v, 3) for v in geometry_bbox_4326)}). Vérifie que c'est la bonne "
                    "couche et qu'elle couvre bien cette zone du Sénégal."
                )
        except RuntimeError:
            raise
        except Exception:
            pass  # si la vérification d'emprise échoue pour une raison technique, on tente quand même le découpage

    # Nodata de sortie : on conserve celui du raster source s'il existe, sinon une valeur
    # sentinelle qui ne collisionne pas avec des données réelles (élévation, etc.)
    dst_nodata = nodata if nodata is not None else (src_nodata if src_nodata is not None else -999999)

    warp_kwargs = dict(
        format="GTiff",
        cutlineWKT=wkt_geometry,
        cutlineSRS=f"EPSG:{geometry_srs_epsg}",
        cropToCutline=True,
        srcNodata=src_nodata,
        dstNodata=dst_nodata,
        resampleAlg="near",
    )
    # Si une projection cible est demandée (ex: EPSG:32628, CRS métrique de référence
    # des projets Sen Hydro), on reprojette à la volée. Indispensable pour que les
    # superficies calculées ensuite (ex: classes de pente) soient correctes : un raster
    # resté en EPSG:4326 (degrés) donnerait des superficies fausses (proches de 0).
    if target_epsg is not None:
        warp_kwargs["dstSRS"] = f"EPSG:{target_epsg}"

    warp_options = gdal.WarpOptions(**warp_kwargs)
    ds = gdal.Warp(out_path, real_in_path, options=warp_options)
    if ds is None:
        raise RuntimeError(
            f"Échec du découpage du raster sur la géométrie fournie ({in_path}). "
            "Cause possible : CRS non défini ou invalide sur le raster source."
        )
    width, height = ds.RasterXSize, ds.RasterYSize
    ds = None

    if width == 0 or height == 0:
        raise RuntimeError(
            "Le découpage a produit un raster vide (0 pixel). La géométrie ne recouvre "
            "probablement pas le raster source, ou le CRS du raster est incorrect."
        )

    return out_path


def compute_raster_zonal_stats(raster_path):
    """Statistiques simples (min, max, moyenne, écart-type) sur un raster déjà découpé, hors NoData."""
    ds = gdal.Open(raster_path)
    if ds is None:
        raise RuntimeError(f"Impossible d'ouvrir le raster : {raster_path}")
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    arr = band.ReadAsArray().astype("float64")
    ds = None

    if nodata is not None:
        mask = arr != nodata
    else:
        mask = np.ones_like(arr, dtype=bool)

    valid = arr[mask]
    if valid.size == 0:
        return None

    return {
        "min": float(np.min(valid)),
        "max": float(np.max(valid)),
        "mean": float(np.mean(valid)),
        "std": float(np.std(valid)),
        "pixel_count": int(valid.size),
    }


def compute_slope_percent(dem_path, out_path):
    """Calcule la pente (en %) à partir d'un MNT déjà découpé, via GDAL DEMProcessing."""
    ds = gdal.DEMProcessing(out_path, dem_path, "slope", slopeFormat="percent", computeEdges=True)
    if ds is None:
        raise RuntimeError("Échec du calcul de pente (gdal.DEMProcessing).")
    ds = None
    return out_path


# Classes de pente (seuils inspirés de la classification FAO, en %)
SLOPE_CLASSES = [
    (0.0, 2.0, "Plat"),
    (2.0, 5.0, "Faible"),
    (5.0, 15.0, "Modéré"),
    (15.0, 30.0, "Fort"),
    (30.0, float("inf"), "Très fort"),
]


def compute_slope_class_areas(slope_path):
    """
    Retourne une liste de dicts {label, min_pct, max_pct, pixels, area_km2, pct}
    pour chaque classe de pente FAO présente dans le raster de pente (hors NoData).
    """
    ds = gdal.Open(slope_path)
    if ds is None:
        raise RuntimeError(f"Impossible d'ouvrir le raster de pente : {slope_path}")
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    arr = band.ReadAsArray().astype("float64")
    gt = ds.GetGeoTransform()
    pixel_area_m2 = abs(gt[1] * gt[5])
    ds = None

    if nodata is not None:
        mask = arr != nodata
    else:
        mask = np.ones_like(arr, dtype=bool)

    valid = arr[mask]
    total_valid_pixels = int(valid.size)
    if total_valid_pixels == 0:
        return []

    results = []
    for smin, smax, label in SLOPE_CLASSES:
        class_mask = (valid >= smin) & (valid < smax)
        c = int(class_mask.sum())
        if c == 0:
            continue
        area_km2 = (c * pixel_area_m2) / 1_000_000.0
        pct = (c / total_valid_pixels * 100.0)
        results.append({
            "label": label,
            "min_pct": smin,
            "max_pct": smax if smax != float("inf") else None,
            "pixels": c,
            "area_km2": round(area_km2, 2),
            "pct": round(pct, 2),
        })

    return results


def write_array_as_geotiff(arr, geotransform, projection, out_path, nodata=255):
    driver = gdal.GetDriverByName("GTiff")
    rows, cols = arr.shape
    ds = driver.Create(out_path, cols, rows, 1, gdal.GDT_Byte)
    ds.SetGeoTransform(geotransform)
    ds.SetProjection(projection)
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.SetNoDataValue(nodata)
    band.FlushCache()
    ds = None
    return out_path
