# -*- coding: utf-8 -*-
"""
Estimation du taux de fertilité par type de sol (colonne MSDNOM de la couche Morpho_Pedo).

Les pourcentages de superficie occupée par type de sol (calculés à partir de la couche)
n'indiquent PAS la fertilité : ce module fournit une estimation agronomique indicative
du taux de fertilité pour chaque type, à afficher à titre de repère et non comme une
mesure scientifique précise.
"""

import unicodedata

# (fertilité min %, fertilité max %) — estimation indicative
FERTILITY_RANGES = {
    "VERTIQUES": (85, 95),
    "HYDROMORPHES": (70, 90),
    "ROUGE BRUN": (60, 80),
    "VASIERES": (50, 80),
    "FERRUGINEUX TROPICAUX": (55, 75),
    "BRUN SUBARIDES": (50, 70),
    "FERRALITIQUES": (40, 60),
    "PEU EVOLUES": (30, 60),
    "HALOMORPHES": (10, 40),
    "REGOSOLS": (20, 50),
    "DUNES LITTORALES": (5, 20),
    "LITHOSOLS": (10, 30),
    "EAU": (None, None),  # pas un sol
}


def _normalize(text):
    if text is None:
        return ""
    text = str(text).strip().upper()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return text


def get_fertility_range(soil_type_label):
    """
    Retourne (min, max) en % pour un type de sol donné (label MSDNOM), en tolérant
    accents/casse/espaces. Retourne (None, None) si le type n'est pas reconnu.
    """
    normalized = _normalize(soil_type_label)

    if normalized in FERTILITY_RANGES:
        return FERTILITY_RANGES[normalized]

    # correspondance partielle (ex: "PEU ÉVOLUÉS TROPICAUX" contient "PEU EVOLUES")
    for key, value in FERTILITY_RANGES.items():
        if key in normalized or normalized in key:
            return value

    return (None, None)


def format_fertility(soil_type_label):
    fmin, fmax = get_fertility_range(soil_type_label)
    if fmin is None:
        return "N/A"
    if fmin == fmax:
        return f"{fmin} %"
    return f"{fmin}–{fmax} %"


def compute_weighted_fertility_index(soil_stats):
    """
    Calcule un indice de fertilité globale pondérée pour une commune, à partir des
    statistiques de sol (liste de dicts avec 'pct', 'fertility_min', 'fertility_max').

    Chaque type de sol contribue avec sa fertilité moyenne (milieu de la fourchette),
    pondérée par sa part de superficie. Les types sans fertilité connue (ex: EAU) sont
    exclus du calcul mais leur part est signalée pour indiquer la fiabilité de l'indice.

    :return: dict {score, coverage_pct, label} ou None si aucune donnée exploitable
    """
    weighted_sum = 0.0
    weight_total = 0.0

    for entry in soil_stats:
        fmin = entry.get("fertility_min")
        fmax = entry.get("fertility_max")
        pct = entry.get("pct", 0.0)
        if fmin is None or fmax is None:
            continue
        midpoint = (fmin + fmax) / 2.0
        weighted_sum += midpoint * pct
        weight_total += pct

    if weight_total == 0:
        return None

    score = weighted_sum / weight_total

    if score >= 70:
        label = "Fertilité globale élevée"
    elif score >= 45:
        label = "Fertilité globale moyenne"
    else:
        label = "Fertilité globale faible"

    return {
        "score": round(score, 1),
        "coverage_pct": round(weight_total, 1),
        "label": label,
    }
