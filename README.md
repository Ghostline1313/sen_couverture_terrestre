# Sen Couverture Terrestre

Plugin QGIS pour l'analyse par commune au Senegal : couverture terrestre Sentinel-2 (ArcGIS Living Atlas), type de sol et fertilite estimee (Morpho_Pedo), et relief (MNT).

## Fonctionnalites

- Selection de commune : liste deroulante avec les 550 communes du Senegal, couches de reference embarquees (aucun import manuel necessaire).
- - Couverture terrestre : telechargement et decoupage automatique de la couverture Sentinel-2 10m (2017-2024, Impact Observatory / Esri / Microsoft), avec comparaison entre deux annees et statistiques de superficie par classe.
  - - Type de sol et fertilite : statistiques de superficie par type de sol a partir de la couche pedologique Morpho_Pedo integree, avec indice de fertilite pondere estime.
    - - Relief : altitude min/max/moyenne/ecart-type, denivele, pente moyenne/min/max et repartition par classes FAO (Plat/Faible/Modere/Fort/Tres fort). Trois sources de MNT possibles : couche deja chargee dans le projet, fichier local (memorise), ou API en ligne (Esri World Elevation 3D).
      - - Indice d'inondation : indice indicatif multi-criteres (altitude, pente, distance au reseau hydrographique, pluviometrie, recurrence).
       
        - ## Prerequis
       
        - - QGIS 3.16 ou superieur
          - - Connexion internet pour le telechargement de la couverture terrestre et, si utilisee, l'option relief via API
           
            - ## Installation
           
            - Depuis le depot officiel des plugins QGIS : Extensions -> Installer/Gerer les extensions, rechercher "Sen Couverture Terrestre".
           
            - Installation manuelle : telecharger le zip depuis ce depot, puis Extensions -> Installer une extension a partir d'un ZIP.
           
            - ## Avertissement sur la source relief "via API"
           
            - L'option relief "via API" (Esri World Elevation 3D) peut renvoyer des donnees peu precises sur certaines zones du Senegal (relief quasi plat renvoye la ou le terrain reel varie fortement). Pour une precision fiable, privilegier un MNT local (ex: Copernicus GLO-30) via l'option fichier.
           
            - ## Licence
           
            - GPL-3.0-or-later - voir LICENCE.
           
            - ## Auteur
           
            - ADIOUMA FALL - Geo - mouridefalltouba@gmail.com
            - 
