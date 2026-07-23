# -*- coding: utf-8 -*-
"""
Indice d'aléa d'inondation multi-critères, calculé à l'échelle de la commune
(valeurs moyennes/dominantes sur la zone), selon la méthodologie fournie :

  Indice = 0.35 x Hauteur_eau + 0.20 x Pente + 0.15 x Distance_riviere
         + 0.10 x Accumulation + 0.05 x Occupation_sol + 0.05 x Sol
         + 0.05 x Pluie + 0.05 x Recurrence

Chaque critère est d'abord converti en un score de 1 (risque faible) à 4 (risque
très fort), puis combiné selon les pondérations ci-dessus.

Important : ceci est un indice INDICATIF à l'échelle communale (valeurs moyennes/
dominantes), pas une carte d'aléa pixel par pixel type PPRI officielle. Certains
critères (accumulation de flux, pluviométrie, récurrence) nécessitent une saisie
manuelle car ils ne sont pas dérivables automatiquement des couches disponibles.
"""

import unicodedata

WEIGHTS = {
    "hauteur_eau": 0.35,
    "pente": 0.20,
    "distance_riviere": 0.15,
    "accumulation": 0.10,
    "occupation_sol": 0.05,
    "sol": 0.05,
    "pluie": 0.05,
    "recurrence": 0.05,
}

MANUAL_LEVELS = ["Faible", "Moyenne", "Forte", "Très forte"]
MANUAL_LEVEL_SCORE = {"Faible": 1, "Moyenne": 2, "Forte": 3, "Très forte": 4}

RECURRENCE_OPTIONS = ["2-10 ans", "20 ans", "50 ans", "100 ans"]
RECURRENCE_SCORE = {"2-10 ans": 4, "20 ans": 3, "50 ans": 2, "100 ans": 1}

# Occupation du sol (à partir des classes LULC déjà calculées par le plugin)
OCCUPATION_SCORE = {
    "Eau": 4,
    "Végétation inondée": 4,
    "Sol nu": 3,
    "Zones bâties": 3,
    "Cultures": 2,
    "Végétation herbacée / savane": 2,
    "Arbres": 1,
}

# Type de sol (MSDNOM, Morpho_Pedo) -> texture indicative (Argile=4, Limon=3, Sable=1)
# Estimation basée sur les caractéristiques pédologiques générales de chaque type.
SOIL_TEXTURE_SCORE = {
    "VERTIQUES": 4,
    "HYDROMORPHES": 4,
    "VASIERES": 4,
    "HALOMORPHES": 3,
    "FERRUGINEUX TROPICAUX": 3,
    "FERRALITIQUES": 3,
    "ROUGE BRUN": 3,
    "PEU EVOLUES": 2,
    "BRUN SUBARIDES": 2,
    "REGOSOLS": 1,
    "DUNES LITTORALES": 1,
    "LITHOSOLS": 4,  # sol rocheux peu profond -> ruissellement élevé malgré texture non argileuse
    "EAU": 4,
}


def _normalize(text):
    if text is None:
        return ""
    text = str(text).strip().upper()
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def score_hauteur_eau(hauteur_m):
    if hauteur_m < 0.30:
        return 1, "Faible"
    if hauteur_m < 0.50:
        return 2, "Modéré"
    if hauteur_m < 1.00:
        return 3, "Fort"
    return 4, "Très fort"


def score_pente(pente_pct):
    if pente_pct > 10:
        return 1
    if pente_pct > 5:
        return 2
    if pente_pct > 2:
        return 3
    return 4


def score_distance_riviere(distance_m):
    if distance_m > 500:
        return 1
    if distance_m > 300:
        return 2
    if distance_m > 100:
        return 3
    return 4


def score_occupation_sol(dominant_label):
    return OCCUPATION_SCORE.get(dominant_label, 2)


def score_sol(dominant_soil_label):
    normalized = _normalize(dominant_soil_label)
    if normalized in SOIL_TEXTURE_SCORE:
        return SOIL_TEXTURE_SCORE[normalized]
    for key, value in SOIL_TEXTURE_SCORE.items():
        if key in normalized or normalized in key:
            return value
    return 2


def classify_index(index_value):
    if index_value < 1.5:
        return "Faible", "#2ECC71"
    if index_value < 2.5:
        return "Modéré", "#F1C40F"
    if index_value < 3.2:
        return "Fort", "#E67E22"
    return "Très fort", "#E74C3C"


PPRI_RULES = {
    "Faible": "Vert - Risque faible : construction généralement possible.",
    "Modéré": "Jaune - Construction sous conditions (mesures de mitigation recommandées).",
    "Fort": "Orange - Très fortes restrictions à la construction.",
    "Très fort": "Rouge - Construction interdite (zone d'aléa très fort).",
}


def compute_flood_index(hauteur_eau_m, pente_pct, distance_riviere_m,
                         accumulation_level, occupation_dominant_label,
                         sol_dominant_label, pluie_level, recurrence_level):
    """
    Calcule l'indice d'inondation pondéré à l'échelle de la commune.

    :param hauteur_eau_m: Niveau_crue - Altitude_moyenne (peut être négatif si la
                           commune est globalement au-dessus du niveau de crue de référence)
    :param pente_pct: pente moyenne de la commune (%)
    :param distance_riviere_m: distance moyenne/minimale au réseau hydrographique (m)
    :param accumulation_level: 'Faible' / 'Moyenne' / 'Forte' / 'Très forte' (estimation manuelle)
    :param occupation_dominant_label: libellé de la classe LULC dominante dans la commune
    :param sol_dominant_label: libellé MSDNOM du type de sol dominant dans la commune
    :param pluie_level: 'Faible' / 'Moyenne' / 'Forte' / 'Très forte' (estimation manuelle)
    :param recurrence_level: une valeur parmi RECURRENCE_OPTIONS
    :return: dict détaillé (scores par critère, indice final, classe, couleur, règle PPRI)
    """
    hauteur_eau_m = max(hauteur_eau_m, 0.0)  # une hauteur négative = pas de submersion => score faible
    s_hauteur, hauteur_class = score_hauteur_eau(hauteur_eau_m)
    s_pente = score_pente(pente_pct)
    s_distance = score_distance_riviere(distance_riviere_m)
    s_accum = MANUAL_LEVEL_SCORE.get(accumulation_level, 2)
    s_occupation = score_occupation_sol(occupation_dominant_label)
    s_sol = score_sol(sol_dominant_label)
    s_pluie = MANUAL_LEVEL_SCORE.get(pluie_level, 2)
    s_recurrence = RECURRENCE_SCORE.get(recurrence_level, 3)

    index_value = (
        WEIGHTS["hauteur_eau"] * s_hauteur +
        WEIGHTS["pente"] * s_pente +
        WEIGHTS["distance_riviere"] * s_distance +
        WEIGHTS["accumulation"] * s_accum +
        WEIGHTS["occupation_sol"] * s_occupation +
        WEIGHTS["sol"] * s_sol +
        WEIGHTS["pluie"] * s_pluie +
        WEIGHTS["recurrence"] * s_recurrence
    )

    classe, couleur = classify_index(index_value)

    return {
        "index_value": round(index_value, 2),
        "classe": classe,
        "couleur": couleur,
        "ppri": PPRI_RULES[classe],
        "criteres": [
            {"nom": "Hauteur d'eau", "valeur": f"{hauteur_eau_m:.2f} m ({hauteur_class})", "score": s_hauteur, "poids": WEIGHTS["hauteur_eau"]},
            {"nom": "Pente", "valeur": f"{pente_pct:.1f} %", "score": s_pente, "poids": WEIGHTS["pente"]},
            {"nom": "Distance au cours d'eau", "valeur": f"{distance_riviere_m:.0f} m", "score": s_distance, "poids": WEIGHTS["distance_riviere"]},
            {"nom": "Accumulation de flux", "valeur": accumulation_level, "score": s_accum, "poids": WEIGHTS["accumulation"]},
            {"nom": "Occupation du sol", "valeur": occupation_dominant_label, "score": s_occupation, "poids": WEIGHTS["occupation_sol"]},
            {"nom": "Type de sol", "valeur": sol_dominant_label, "score": s_sol, "poids": WEIGHTS["sol"]},
            {"nom": "Pluviométrie", "valeur": pluie_level, "score": s_pluie, "poids": WEIGHTS["pluie"]},
            {"nom": "Récurrence", "valeur": recurrence_level, "score": s_recurrence, "poids": WEIGHTS["recurrence"]},
        ],
    }
