# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QCheckBox, QTableWidget, QTableWidgetItem, QMessageBox, QGroupBox,
    QApplication, QTextEdit, QDoubleSpinBox, QScrollArea, QWidget,
    QLineEdit, QFileDialog
)

from qgis.core import (
    QgsRasterLayer, QgsVectorLayer, QgsProject, QgsPalettedRasterRenderer, Qgis,
    QgsCoordinateReferenceSystem, QgsMapLayerProxyModel, QgsFeature,
    QgsSimpleFillSymbolLayer, QgsFillSymbol
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox
from qgis.utils import iface as qgis_iface

from .land_cover_service import (
    export_landcover_geotiff, LULC_CLASSES, SENEGAL_BBOX_4326, MIN_YEAR, MAX_YEAR,
    LandCoverServiceError,
)
from .relief_service import export_relief_geotiff, ReliefApiError
from .land_cover_analysis import (
    compute_class_areas, compute_change_stats, write_array_as_geotiff,
    clip_geotiff_to_geometry, compute_raster_zonal_stats, compute_slope_percent,
    compute_slope_class_areas,
)
from . import qgis_layers_helper as qh
from . import flood_risk as fr


class SenCouvertureTerrestreDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Sen Couverture Terrestre")
        self.resize(820, 620)
        self.setMinimumSize(600, 400)
        self._build_ui()

        self._current_bbox_4326 = SENEGAL_BBOX_4326
        self._commune_geom_4326 = None  # QgsGeometry, pour découpage précis
        self._commune_wkt_4326 = None
        self._commune_highlight_layer = None  # couche mémoire affichant la limite de la commune sélectionnée
        self._last_results = None  # dict rempli après chaque analyse, utilisé pour l'export texte

        project_path = QgsProject.instance().fileName()
        base_dir = os.path.dirname(project_path) if project_path else os.path.expanduser("~")
        self.output_dir = os.path.join(base_dir, "sen_couverture_terrestre")

        self._ensure_bundled_reference_layers()
        self._refresh_commune_layer_fields()
        self._refresh_soil_layer_fields()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)

        info = QLabel(
            "Sélectionne une commune (à partir d'une couche déjà chargée dans QGIS) pour zoomer "
            "automatiquement dessus, télécharger la couverture terrestre découpée sur sa limite, "
            f"et afficher les statistiques de sol et de relief. Occupation du sol : Sentinel-2 10m, "
            f"{MIN_YEAR}-{MAX_YEAR} (Living Atlas)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # -- Zone d'étude : sélection par commune --
        commune_box = QGroupBox("Zone d'étude - Commune")
        commune_layout = QVBoxLayout(commune_box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Couche des communes :"))
        self.commune_layer_combo = QgsMapLayerComboBox()
        self.commune_layer_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.commune_layer_combo.layerChanged.connect(self._refresh_commune_layer_fields)
        row1.addWidget(self.commune_layer_combo, stretch=1)
        commune_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Champ nom commune :"))
        self.commune_field_combo = QgsFieldComboBox()
        self.commune_field_combo.fieldChanged.connect(self._refresh_commune_values)
        row2.addWidget(self.commune_field_combo, stretch=1)
        commune_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Commune :"))
        self.commune_value_combo = QComboBox()
        self.commune_value_combo.setEditable(True)
        self.commune_value_combo.currentIndexChanged.connect(self._on_commune_selected)
        row3.addWidget(self.commune_value_combo, stretch=1)
        commune_layout.addLayout(row3)

        self.commune_info_label = QTextEdit()
        self.commune_info_label.setReadOnly(True)
        self.commune_info_label.setMaximumHeight(90)
        self.commune_info_label.setPlaceholderText("Sélectionne une commune pour afficher ses informations...")
        commune_layout.addWidget(self.commune_info_label)

        self.extent_label = QLabel("Aucune commune sélectionnée — emprise Sénégal utilisée par défaut.")
        self.extent_label.setWordWrap(True)
        commune_layout.addWidget(self.extent_label)

        layout.addWidget(commune_box)

        # -- Couches complémentaires : sol et relief --
        extra_box = QGroupBox("Informations complémentaires (couches déjà chargées)")
        extra_layout = QVBoxLayout(extra_box)

        soil_row = QHBoxLayout()
        self.soil_check = QCheckBox("Type de sol :")
        soil_row.addWidget(self.soil_check)
        self.soil_layer_combo = QgsMapLayerComboBox()
        self.soil_layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.soil_layer_combo.layerChanged.connect(self._refresh_soil_layer_fields)
        soil_row.addWidget(self.soil_layer_combo, stretch=1)
        soil_row.addWidget(QLabel("Champ classe :"))
        self.soil_field_combo = QgsFieldComboBox()
        soil_row.addWidget(self.soil_field_combo, stretch=1)
        extra_layout.addLayout(soil_row)

        relief_row = QHBoxLayout()
        self.relief_check = QCheckBox("Relief (MNT) :")
        relief_row.addWidget(self.relief_check)
        self.relief_layer_combo = QgsMapLayerComboBox()
        self.relief_layer_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.relief_layer_combo.setAllowEmptyLayer(True)
        self.relief_layer_combo.setCurrentIndex(0)
        relief_row.addWidget(self.relief_layer_combo, stretch=1)
        extra_layout.addLayout(relief_row)

        relief_file_row = QHBoxLayout()
        relief_file_row.addWidget(QLabel("  └ ou MNT fichier (arrière-plan, non ajouté au panneau) :"))
        self.relief_file_edit = QLineEdit()
        self.relief_file_edit.setPlaceholderText("Aucun fichier MNT mémorisé — clique sur Parcourir...")
        self.relief_file_edit.setReadOnly(True)
        relief_file_row.addWidget(self.relief_file_edit, stretch=1)
        self.relief_browse_btn = QPushButton("Parcourir...")
        self.relief_browse_btn.clicked.connect(self._browse_relief_file)
        relief_file_row.addWidget(self.relief_browse_btn)
        self.relief_clear_btn = QPushButton("Effacer")
        self.relief_clear_btn.clicked.connect(self._clear_relief_file)
        relief_file_row.addWidget(self.relief_clear_btn)
        extra_layout.addLayout(relief_file_row)

        relief_api_row = QHBoxLayout()
        self.relief_api_check = QCheckBox(
            "  └ ou via API (Esri, en ligne, aucun fichier requis — prioritaire, ⚠️ précision variable)"
        )
        self.relief_api_check.setToolTip(
            "Télécharge automatiquement un MNT depuis le service ArcGIS Living Atlas "
            "(elevation3d.arcgis.com), découpé sur la commune. Aucune clé requise.\n\n"
            "⚠️ ATTENTION : testé comme peu précis sur certaines zones du Sénégal (relief "
            "quasi plat renvoyé là où le terrain réel varie de plusieurs dizaines de mètres). "
            "À vérifier avec un MNT local avant de s'y fier, surtout pour l'indice d'inondation."
        )
        relief_api_row.addWidget(self.relief_api_check)
        extra_layout.addLayout(relief_api_row)

        self._settings = QSettings("GeoSenegal", "SenCouvertureTerrestre")
        saved_mnt_path = self._settings.value("mnt_path", "", type=str)
        if saved_mnt_path and os.path.exists(saved_mnt_path):
            self.relief_file_edit.setText(saved_mnt_path)

        layout.addWidget(extra_box)

        # -- Zone inondable --
        flood_box = QGroupBox("Zone inondable (indice d'aléa multi-critères)")
        flood_layout = QVBoxLayout(flood_box)

        self.flood_check = QCheckBox("Calculer l'indice d'inondation (nécessite le relief coché ci-dessus)")
        flood_layout.addWidget(self.flood_check)

        flood_row1 = QHBoxLayout()
        flood_row1.addWidget(QLabel("Niveau de crue de référence (m) :"))
        self.crue_spin = QDoubleSpinBox()
        self.crue_spin.setRange(-50.0, 500.0)
        self.crue_spin.setDecimals(2)
        self.crue_spin.setValue(0.0)
        self.crue_spin.setToolTip(
            "Altitude du niveau d'eau de référence (crue de projet), même unité verticale que le MNT."
        )
        flood_row1.addWidget(self.crue_spin)
        flood_row1.addWidget(QLabel("Cours d'eau :"))
        self.river_layer_combo = QgsMapLayerComboBox()
        self.river_layer_combo.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.river_layer_combo.setAllowEmptyLayer(True)
        flood_row1.addWidget(self.river_layer_combo, stretch=1)
        flood_layout.addLayout(flood_row1)

        flood_row2 = QHBoxLayout()
        flood_row2.addWidget(QLabel("Accumulation de flux (estimation) :"))
        self.accum_combo = QComboBox()
        self.accum_combo.addItems(fr.MANUAL_LEVELS)
        self.accum_combo.setCurrentText("Moyenne")
        flood_row2.addWidget(self.accum_combo)

        flood_row2.addWidget(QLabel("Pluviométrie :"))
        self.pluie_combo = QComboBox()
        self.pluie_combo.addItems(fr.MANUAL_LEVELS)
        self.pluie_combo.setCurrentText("Moyenne")
        flood_row2.addWidget(self.pluie_combo)

        flood_row2.addWidget(QLabel("Récurrence :"))
        self.recurrence_combo = QComboBox()
        self.recurrence_combo.addItems(fr.RECURRENCE_OPTIONS)
        self.recurrence_combo.setCurrentText("20 ans")
        flood_row2.addWidget(self.recurrence_combo)
        flood_layout.addLayout(flood_row2)

        layout.addWidget(flood_box)

        # -- Années --
        years_box = QGroupBox("Années - couverture terrestre")
        years_layout = QHBoxLayout(years_box)
        years_layout.addWidget(QLabel("Année 1 :"))
        self.year1_combo = QComboBox()
        for y in range(MIN_YEAR, MAX_YEAR + 1):
            self.year1_combo.addItem(str(y), y)
        self.year1_combo.setCurrentIndex(self.year1_combo.count() - 1)
        years_layout.addWidget(self.year1_combo)

        self.compare_check = QCheckBox("Comparer avec une 2e année")
        self.compare_check.toggled.connect(self._toggle_year2)
        years_layout.addWidget(self.compare_check)

        years_layout.addWidget(QLabel("Année 2 :"))
        self.year2_combo = QComboBox()
        for y in range(MIN_YEAR, MAX_YEAR + 1):
            self.year2_combo.addItem(str(y), y)
        self.year2_combo.setEnabled(False)
        years_layout.addWidget(self.year2_combo)
        layout.addWidget(years_box)

        res_row = QHBoxLayout()
        res_row.addWidget(QLabel("Résolution d'export (taille max en pixels) :"))
        self.res_combo = QComboBox()
        self.res_combo.addItem("Rapide (1000 px)", 1000)
        self.res_combo.addItem("Standard (2000 px)", 2000)
        self.res_combo.addItem("Détaillée (3500 px)", 3500)
        self.res_combo.setCurrentIndex(1)
        res_row.addWidget(self.res_combo)
        layout.addLayout(res_row)

        self.run_btn = QPushButton("Télécharger, afficher et analyser")
        self.run_btn.clicked.connect(self.run)
        layout.addWidget(self.run_btn)

        legend_box = QGroupBox("Légende - couverture terrestre")
        legend_layout = QHBoxLayout(legend_box)
        for value, (label, color) in LULC_CLASSES.items():
            chip = QLabel(f"  {label}  ")
            chip.setStyleSheet(f"background-color:{color}; border:1px solid #666;")
            legend_layout.addWidget(chip)
        layout.addWidget(legend_box)

        self.results_table = QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(["Classe", "km² (année 1)", "km² (année 2)", "Δ km²"])
        self.results_table.setMinimumHeight(160)
        layout.addWidget(self.results_table, stretch=1)

        self.extra_results_text = QTextEdit()
        self.extra_results_text.setReadOnly(True)
        self.extra_results_text.setMinimumHeight(220)
        layout.addWidget(self.extra_results_text)

        # -- Fin du contenu défilant --
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, stretch=1)

        # -- Pied de page fixe, toujours visible --
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(8, 6, 8, 8)

        self.status_label = QLabel("Prêt.")
        footer_layout.addWidget(self.status_label)

        self.export_btn = QPushButton("Exporter les résultats (format texte)")
        self.export_btn.clicked.connect(self.export_results_to_text)
        self.export_btn.setEnabled(False)
        footer_layout.addWidget(self.export_btn)

        outer_layout.addWidget(footer)

    # ------------------------------------------------------- Commune logic

    def _browse_relief_file(self):
        start_dir = os.path.dirname(self.relief_file_edit.text()) if self.relief_file_edit.text() else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Choisir un MNT (raster)", start_dir,
            "Rasters (*.tif *.tiff *.img *.asc);;Tous les fichiers (*)"
        )
        if path:
            self.relief_file_edit.setText(path)
            self._settings.setValue("mnt_path", path)

    def _clear_relief_file(self):
        self.relief_file_edit.clear()
        self._settings.remove("mnt_path")

    def _get_relief_source_path(self):
        """
        Retourne le chemin du raster à utiliser pour le relief, en priorisant le
        fichier MNT mémorisé (utilisé directement via GDAL, jamais ajouté au projet
        ni au panneau des couches). À défaut, retombe sur la couche sélectionnée
        dans la combo (couche déjà chargée dans le projet, ex: MNT spécifique à une
        zone déjà ouverte par l'utilisateur).
        """
        file_path = self.relief_file_edit.text().strip()
        if file_path and os.path.exists(file_path):
            return file_path
        layer = self.relief_layer_combo.currentLayer()
        return layer.source() if layer is not None else None

    def _ensure_bundled_reference_layers(self):
        """
        Charge automatiquement dans le projet les couches de référence embarquées
        avec le plugin (communes du Sénégal, type de sol) si aucune couche équivalente
        n'est déjà présente. Évite d'avoir à les importer manuellement via le panneau
        des couches avant de pouvoir utiliser le plugin.
        """
        plugin_dir = os.path.dirname(__file__)
        data_dir = os.path.join(plugin_dir, "data")
        project = QgsProject.instance()
        # Exclut les couches internes du plugin (ex: surbrillance de commune, qui peut
        # déjà exister d'une session précédente) de la détection : sinon son nom
        # (contenant "commune") ferait croire à tort qu'une couche de communes existe
        # déjà dans le projet, et empêcherait le chargement de la couche intégrée.
        internal_layer_ids = set()
        if getattr(self, "_commune_highlight_layer", None) is not None:
            internal_layer_ids.add(self._commune_highlight_layer.id())
        existing_names = [
            l.name().lower() for l in project.mapLayers().values()
            if l.id() not in internal_layer_ids and "sélectionnée" not in l.name().lower()
        ]

        def _has_existing(keywords):
            return any(any(k in name for k in keywords) for name in existing_names)

        def _load_bundled(gpkg_filename, display_name, layername=None):
            path = os.path.join(data_dir, gpkg_filename)
            if not os.path.exists(path):
                return None
            uri = f"{path}|layername={layername}" if layername else path
            layer = QgsVectorLayer(uri, display_name, "ogr")
            if not layer.isValid():
                return None
            # Ajoutée au projet et à l'arbre des couches, mais DÉCOCHÉE (invisible) :
            # QgsMapLayerComboBox a besoin que la couche soit dans l'arbre pour la
            # proposer/sélectionner correctement (setLayer échoue silencieusement sinon).
            project.addMapLayer(layer, False)
            root = project.layerTreeRoot()
            node = root.insertLayer(0, layer)
            if node is not None:
                node.setItemVisibilityChecked(False)
            return layer

        if not _has_existing(["commune"]):
            layer = _load_bundled("communes_senegal.gpkg", "Limites_communes (intégré)", "communes")
            if layer is not None:
                self.commune_layer_combo.setLayer(layer)
                # CCRCA = champ du nom de commune dans ce jeu de données (pas le 1er champ, "REG")
                if layer.fields().indexFromName("CCRCA") >= 0:
                    self.commune_field_combo.setLayer(layer)
                    self.commune_field_combo.setField("CCRCA")

        if not _has_existing(["sol", "pedo", "morpho"]):
            layer = _load_bundled("type_de_sol.gpkg", "Type de sol (intégré)", "type_de_sol")
            if layer is not None:
                self.soil_layer_combo.setLayer(layer)
                if layer.fields().indexFromName("MSDNOM") >= 0:
                    self.soil_field_combo.setLayer(layer)
                    self.soil_field_combo.setField("MSDNOM")

    def _refresh_commune_layer_fields(self, *args):
        layer = self.commune_layer_combo.currentLayer()
        self.commune_field_combo.setLayer(layer)
        if layer is not None:
            guessed = qh.guess_name_field(layer)
            if guessed:
                self.commune_field_combo.setField(guessed)
        self._refresh_commune_values()

    def _refresh_commune_values(self, *args):
        self.commune_value_combo.blockSignals(True)
        self.commune_value_combo.clear()
        layer = self.commune_layer_combo.currentLayer()
        field = self.commune_field_combo.currentField()
        if layer is not None and field:
            values = qh.get_unique_values_sorted(layer, field)
            self.commune_value_combo.addItems([str(v) for v in values])
        self.commune_value_combo.setCurrentIndex(-1)
        self.commune_value_combo.blockSignals(False)

    def _refresh_soil_layer_fields(self, *args):
        layer = self.soil_layer_combo.currentLayer()

        # Si aucune couche sol n'est sélectionnée, OU que la couche actuelle ne
        # ressemble pas à une couche de sol (nom sans "sol"/"pedo"/"morpho"),
        # tente de détecter automatiquement la bonne couche parmi celles du projet.
        # Évite que QgsMapLayerComboBox ne présélectionne par défaut une couche
        # vectorielle sans rapport (ex: la couche des communes).
        looks_like_soil = layer is not None and any(
            k in layer.name().lower() for k in ("sol", "pedo", "morpho")
        )
        if not looks_like_soil:
            guessed_layer = qh.guess_soil_layer()
            if guessed_layer is not None:
                self.soil_layer_combo.setLayer(guessed_layer)
                layer = guessed_layer
                self.soil_check.setChecked(True)

        self.soil_field_combo.setLayer(layer)
        if layer is not None:
            guessed_field = qh.guess_soil_field(layer)
            if guessed_field:
                self.soil_field_combo.setField(guessed_field)

    def _on_commune_selected(self, index):
        layer = self.commune_layer_combo.currentLayer()
        field = self.commune_field_combo.currentField()
        value = self.commune_value_combo.currentText()

        if layer is None or not field or not value:
            return

        feature = qh.get_feature_by_field_value(layer, field, value)
        if feature is None:
            return

        geom_4326, wkt_4326, bbox_4326 = qh.geometry_to_4326(feature.geometry(), layer.crs())
        self._commune_geom_4326 = geom_4326
        self._commune_wkt_4326 = wkt_4326
        self._current_bbox_4326 = qh.buffer_bbox(bbox_4326, fraction=0.03)

        qh.zoom_canvas_to_geometry(self.iface, feature.geometry(), layer.crs())
        self._update_commune_highlight(feature.geometry(), layer.crs())

        info_html = qh.format_feature_attributes(feature)
        self.commune_info_label.setHtml(info_html or "Aucune information disponible.")

        self.extent_label.setText(
            f"Commune sélectionnée : <b>{value}</b> — la couverture terrestre sera découpée "
            "précisément sur sa limite."
        )

    def _update_commune_highlight(self, geometry, crs):
        """
        Affiche la limite de la commune sélectionnée sur le canevas : remplissage
        simple transparent, contour en tirets, largeur de trait 0.26 mm. Réutilise
        la même couche mémoire d'un appel à l'autre (une seule entité à la fois).
        """
        project = QgsProject.instance()
        layer = self._commune_highlight_layer
        if layer is None or project.mapLayer(layer.id()) is None:
            layer = QgsVectorLayer(f"Polygon?crs={crs.authid()}", "Limite commune sélectionnée", "memory")

            symbol = QgsFillSymbol()
            fill_symbol_layer = QgsSimpleFillSymbolLayer()
            fill_symbol_layer.setBrushStyle(Qt.NoBrush)      # remplissage transparent
            fill_symbol_layer.setStrokeStyle(Qt.DashLine)    # ligne en tirets
            fill_symbol_layer.setStrokeWidth(0.26)           # largeur de trait 0.26 mm
            symbol.changeSymbolLayer(0, fill_symbol_layer)
            layer.renderer().setSymbol(symbol)

            project.addMapLayer(layer)
            self._commune_highlight_layer = layer

        provider = layer.dataProvider()
        provider.truncate()
        feat = QgsFeature()
        feat.setGeometry(geometry)
        provider.addFeature(feat)
        layer.updateExtents()
        layer.triggerRepaint()

    def _toggle_year2(self, checked):
        self.year2_combo.setEnabled(checked)
        self.results_table.setColumnHidden(2, not checked)
        self.results_table.setColumnHidden(3, not checked)

    # ------------------------------------------------------------ Rendering

    def _apply_legend(self, layer):
        classes = [QgsPalettedRasterRenderer.Class(v, QColor(c), l) for v, (l, c) in LULC_CLASSES.items()]
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def _fill_table_single(self, class_areas):
        self.results_table.setColumnHidden(2, True)
        self.results_table.setColumnHidden(3, True)
        self.results_table.setRowCount(len(class_areas))
        for row, entry in enumerate(class_areas):
            self.results_table.setItem(row, 0, QTableWidgetItem(entry["label"]))
            self.results_table.setItem(row, 1, QTableWidgetItem(f"{entry['area_km2']} ({entry['pct']}%)"))

    def _fill_table_comparison(self, summary):
        self.results_table.setColumnHidden(2, False)
        self.results_table.setColumnHidden(3, False)
        self.results_table.setRowCount(len(summary))
        for row, entry in enumerate(summary):
            self.results_table.setItem(row, 0, QTableWidgetItem(entry["label"]))
            self.results_table.setItem(row, 1, QTableWidgetItem(str(entry["area_km2_year1"])))
            self.results_table.setItem(row, 2, QTableWidgetItem(str(entry["area_km2_year2"])))
            delta = entry["delta_km2"]
            self.results_table.setItem(row, 3, QTableWidgetItem(f"{'+' if delta > 0 else ''}{delta}"))

    # ------------------------------------------------------------- Run

    def run(self):
        compare = self.compare_check.isChecked()
        year1 = self.year1_combo.currentData()
        year2 = self.year2_combo.currentData() if compare else None
        max_dim = self.res_combo.currentData()
        bbox = self._current_bbox_4326
        commune_name = self.commune_value_combo.currentText() or "zone"

        if compare and year1 == year2:
            QMessageBox.warning(self, "Années identiques", "Choisis deux années différentes pour la comparaison.")
            return

        self.run_btn.setEnabled(False)
        self.status_label.setText("Téléchargement en cours (peut prendre jusqu'à une minute)...")
        self.extra_results_text.clear()
        self.export_btn.setEnabled(False)
        QApplication.processEvents()

        results = {
            "commune_name": commune_name,
            "year1": year1,
            "year2": year2,
            "compare": compare,
            "class_areas_1": None,
            "change_summary": None,
            "changed_pct": None,
            "soil_stats": None,
            "fertility_index": None,
            "relief_stats": None,
            "flood_index": None,
        }

        try:
            path_y1 = self._download_and_clip(commune_name, year1, max_dim, bbox)

            layer1 = QgsRasterLayer(path_y1, f"Couverture terrestre {commune_name} {year1}")
            if not layer1.isValid():
                raise LandCoverServiceError("Le GeoTIFF téléchargé pour l'année 1 n'a pas pu être chargé dans QGIS.")
            self._apply_legend(layer1)
            QgsProject.instance().addMapLayer(layer1)
            class_areas_1 = compute_class_areas(path_y1)
            results["class_areas_1"] = class_areas_1

            if compare:
                path_y2 = self._download_and_clip(commune_name, year2, max_dim, bbox)
                layer2 = QgsRasterLayer(path_y2, f"Couverture terrestre {commune_name} {year2}")
                if not layer2.isValid():
                    raise LandCoverServiceError("Le GeoTIFF téléchargé pour l'année 2 n'a pas pu être chargé dans QGIS.")
                self._apply_legend(layer2)
                QgsProject.instance().addMapLayer(layer2)

                self.status_label.setText("Calcul du changement entre les deux années...")
                QApplication.processEvents()

                change = compute_change_stats(path_y1, path_y2)
                change_path = os.path.join(self.output_dir, f"changement_{commune_name}_{year1}_{year2}.tif")
                write_array_as_geotiff(change["change_map"], change["geotransform"], change["projection"], change_path)

                change_layer = QgsRasterLayer(change_path, f"Changement {commune_name} {year1} → {year2}")
                if change_layer.isValid():
                    change_classes = [
                        QgsPalettedRasterRenderer.Class(0, QColor("#DDDDDD"), "Inchangé"),
                        QgsPalettedRasterRenderer.Class(1, QColor("#D7263D"), "Changé"),
                    ]
                    change_layer.setRenderer(QgsPalettedRasterRenderer(change_layer.dataProvider(), 1, change_classes))
                    QgsProject.instance().addMapLayer(change_layer)

                self._fill_table_comparison(change["summary"])
                results["change_summary"] = change["summary"]
                results["changed_pct"] = change["changed_pct"]
                self.status_label.setText(
                    f"Terminé. {change['changed_pct']}% de la zone a changé de classe entre {year1} et {year2}."
                )
            else:
                self._fill_table_single(class_areas_1)
                self.status_label.setText(f"Terminé pour l'année {year1}.")

            soil_stats, fertility_index, relief_stats, flood_index = self._run_extra_analysis(class_areas_1)
            results["soil_stats"] = soil_stats
            results["fertility_index"] = fertility_index
            results["relief_stats"] = relief_stats
            results["flood_index"] = flood_index

            self._last_results = results
            self.export_btn.setEnabled(True)

            if qgis_iface:
                qgis_iface.messageBar().pushMessage(
                    "Sen Couverture Terrestre", "Traitement terminé.", level=Qgis.Success, duration=5
                )

        except LandCoverServiceError as e:
            self.status_label.setText("Erreur.")
            QMessageBox.critical(self, "Erreur", str(e))
        except Exception as e:
            self.status_label.setText("Erreur inattendue.")
            QMessageBox.critical(self, "Erreur inattendue", str(e))
        finally:
            self.run_btn.setEnabled(True)

    def _download_and_clip(self, commune_name, year, max_dim, bbox):
        raw_path = os.path.join(self.output_dir, f"lulc_{commune_name}_{year}_brut.tif")
        export_landcover_geotiff(bbox, year, raw_path, max_dim=max_dim)

        if self._commune_wkt_4326 is not None:
            clipped_path = os.path.join(self.output_dir, f"lulc_{commune_name}_{year}.tif")
            clip_geotiff_to_geometry(raw_path, self._commune_wkt_4326, 4326, clipped_path)
            return clipped_path

        return raw_path

    def _run_extra_analysis(self, class_areas_1=None):
        soil_stats_out = None
        fertility_index_out = None
        relief_stats_out = None
        flood_index_out = None

        if self._commune_geom_4326 is None:
            return soil_stats_out, fertility_index_out, relief_stats_out, flood_index_out

        blocks = []
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")

        if self.soil_check.isChecked():
            soil_layer = self.soil_layer_combo.currentLayer()
            soil_field = self.soil_field_combo.currentField()
            if soil_layer is not None and soil_field:
                stats = qh.compute_vector_zonal_stats(self._commune_geom_4326, wgs84, soil_layer, soil_field)
                if stats:
                    from .soil_fertility import compute_weighted_fertility_index
                    fertility_index = compute_weighted_fertility_index(stats)
                    soil_stats_out = stats
                    fertility_index_out = fertility_index

                    rows = "".join(
                        f"<tr><td>{s['label']}</td><td>{s['area_km2']} km²</td><td>{s['pct']}%</td>"
                        f"<td>{s['fertility_label']}</td></tr>"
                        for s in stats
                    )

                    index_html = ""
                    if fertility_index:
                        index_html = (
                            "<p style='font-size:13px;'>"
                            f"<b>Indice de fertilité globale pondérée : {fertility_index['score']} % "
                            f"— {fertility_index['label']}</b><br>"
                            f"<span style='color:#666;'>(basé sur {fertility_index['coverage_pct']}% de la "
                            "superficie communale dont le type de sol a une fertilité connue)</span></p>"
                        )

                    blocks.append(
                        index_html +
                        "<b>Type de sol (MSDNOM)</b><table border='1' cellspacing='0' cellpadding='3'>"
                        "<tr><th>Type de sol</th><th>Superficie</th><th>% de la commune</th>"
                        "<th>Fertilité estimée</th></tr>"
                        f"{rows}</table>"
                        "<i>Remarque : le % représente la part de superficie occupée par chaque type de sol "
                        "dans la commune, pas sa fertilité. La fertilité est une estimation agronomique "
                        "indicative (Vertiques et Hydromorphes généralement les plus fertiles ; Lithosols, "
                        "Dunes littorales et Halomorphes généralement les moins fertiles).</i>"
                    )
                else:
                    blocks.append("<b>Type de sol</b> : aucune donnée intersectant la commune.")

        if self.relief_check.isChecked():
            using_api = self.relief_api_check.isChecked()
            if using_api:
                try:
                    rect = self._commune_geom_4326.boundingBox()
                    commune_bbox_4326 = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
                    relief_source_path = os.path.join(self.output_dir, "relief_api_raw_temp.tif")
                    export_relief_geotiff(commune_bbox_4326, relief_source_path)
                except ReliefApiError as e:
                    relief_source_path = None
                    blocks.append(f"<b>Relief</b> : erreur API Esri ({e}).")
            else:
                relief_source_path = self._get_relief_source_path()

            if relief_source_path is None:
                if not using_api:
                    blocks.append(
                        "<b>Relief</b> : aucun MNT disponible — clique sur \"Parcourir...\" pour "
                        "sélectionner un fichier MNT, choisis une couche raster déjà chargée, "
                        "ou coche l'option \"via API\"."
                    )
            else:
                try:
                    rect = self._commune_geom_4326.boundingBox()
                    commune_bbox_4326 = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())
                    clipped_relief = os.path.join(self.output_dir, "relief_clip_temp.tif")
                    clip_geotiff_to_geometry(
                        relief_source_path, self._commune_wkt_4326, 4326, clipped_relief,
                        geometry_bbox_4326=commune_bbox_4326, target_epsg=32628
                    )
                    stats = compute_raster_zonal_stats(clipped_relief)
                    relief_stats_out = stats
                    if stats:
                        denivele = stats['max'] - stats['min']
                        relief_stats_out["denivele_m"] = round(denivele, 1)
                        blocks.append(
                            "<b>Relief (altitude)</b><br>"
                            f"Min : {stats['min']:.1f} m &nbsp; Max : {stats['max']:.1f} m &nbsp; "
                            f"Moyenne : {stats['mean']:.1f} m &nbsp; Écart-type : {stats['std']:.1f} m<br>"
                            f"Dénivelé : {denivele:.1f} m"
                        )

                        # -- Pente moyenne / min / max (nécessaire aussi pour l'indice d'inondation) --
                        try:
                            slope_path = os.path.join(self.output_dir, "relief_slope_temp.tif")
                            compute_slope_percent(clipped_relief, slope_path)
                            slope_stats = compute_raster_zonal_stats(slope_path)
                            relief_stats_out["slope_mean_pct"] = slope_stats["mean"] if slope_stats else None
                            relief_stats_out["slope_min_pct"] = slope_stats["min"] if slope_stats else None
                            relief_stats_out["slope_max_pct"] = slope_stats["max"] if slope_stats else None
                            if slope_stats:
                                blocks.append(
                                    f"Pente moyenne : {slope_stats['mean']:.1f} % &nbsp; "
                                    f"(min : {slope_stats['min']:.1f} % &nbsp; max : {slope_stats['max']:.1f} %)"
                                )

                            # -- Répartition par classes de pente (FAO) --
                            try:
                                slope_classes = compute_slope_class_areas(slope_path)
                                relief_stats_out["slope_classes"] = slope_classes
                                if slope_classes:
                                    rows = "".join(
                                        f"<tr><td>{c['label']}</td><td>{c['pct']:.1f} %</td>"
                                        f"<td>{c['area_km2']:.2f} km²</td></tr>"
                                        for c in slope_classes
                                    )
                                    blocks.append(
                                        "<b>Répartition des pentes (classes FAO)</b>"
                                        "<table cellspacing='4'>"
                                        "<tr><th>Classe</th><th>%</th><th>Superficie</th></tr>"
                                        f"{rows}</table>"
                                    )
                            except Exception:
                                relief_stats_out["slope_classes"] = None
                        except Exception:
                            relief_stats_out["slope_mean_pct"] = None
                            relief_stats_out["slope_min_pct"] = None
                            relief_stats_out["slope_max_pct"] = None
                    else:
                        blocks.append(
                            "<b>Relief</b> : le découpage a réussi mais ne contient aucune donnée valide "
                            "(toute la zone est en NoData sur cette couche)."
                        )
                except Exception as e:
                    blocks.append(f"<b>Relief</b> : erreur lors du calcul ({e}).")

        if self.flood_check.isChecked():
            if not self.relief_check.isChecked() or not relief_stats_out or relief_stats_out.get("slope_mean_pct") is None:
                blocks.append(
                    "<b>Zone inondable</b> : impossible de calculer l'indice — coche d'abord "
                    "\"Relief (MNT)\" ci-dessus (l'altitude et la pente sont nécessaires)."
                )
            else:
                try:
                    altitude_moyenne = relief_stats_out["mean"]
                    pente_moyenne = relief_stats_out["slope_mean_pct"]
                    niveau_crue = self.crue_spin.value()
                    hauteur_eau = niveau_crue - altitude_moyenne

                    river_layer = self.river_layer_combo.currentLayer()
                    if river_layer is not None:
                        distance_riviere = qh.compute_min_distance_to_layer(
                            self._commune_geom_4326, QgsCoordinateReferenceSystem("EPSG:4326"), river_layer
                        )
                        if distance_riviere is None:
                            distance_riviere = 500.0  # aucune entité trouvée à proximité -> risque faible par défaut
                    else:
                        distance_riviere = 500.0  # pas de couche fournie -> valeur neutre (risque faible)

                    occupation_dominant = class_areas_1[0]["label"] if class_areas_1 else "Cultures"
                    sol_dominant = soil_stats_out[0]["label"] if soil_stats_out else "PEU EVOLUES"

                    flood_index = fr.compute_flood_index(
                        hauteur_eau_m=hauteur_eau,
                        pente_pct=pente_moyenne,
                        distance_riviere_m=distance_riviere,
                        accumulation_level=self.accum_combo.currentText(),
                        occupation_dominant_label=occupation_dominant,
                        sol_dominant_label=sol_dominant,
                        pluie_level=self.pluie_combo.currentText(),
                        recurrence_level=self.recurrence_combo.currentText(),
                    )
                    flood_index_out = flood_index

                    crit_rows = "".join(
                        f"<tr><td>{c['nom']}</td><td>{c['valeur']}</td><td>{c['score']}/4</td>"
                        f"<td>{int(c['poids']*100)}%</td></tr>"
                        for c in flood_index["criteres"]
                    )

                    blocks.append(
                        "<p style='font-size:14px;'>"
                        f"<b>Indice d'inondation : {flood_index['index_value']} / 4 — "
                        f"<span style='color:{flood_index['couleur']};'>{flood_index['classe']}</span></b><br>"
                        f"<span style='color:#666;'>Règle PPRI : {flood_index['ppri']}</span></p>"
                        "<table border='1' cellspacing='0' cellpadding='3'>"
                        "<tr><th>Critère</th><th>Valeur</th><th>Score</th><th>Poids</th></tr>"
                        f"{crit_rows}</table>"
                        "<i>Indice indicatif à l'échelle communale (valeurs moyennes/dominantes), "
                        "pas une carte d'aléa pixel par pixel officielle.</i>"
                    )
                except Exception as e:
                    blocks.append(f"<b>Zone inondable</b> : erreur lors du calcul ({e}).")

        self.extra_results_text.setHtml("<br><br>".join(blocks) if blocks else "Aucune analyse complémentaire activée.")
        return soil_stats_out, fertility_index_out, relief_stats_out, flood_index_out

    # ------------------------------------------------------------- Export

    def export_results_to_text(self):
        if not self._last_results:
            QMessageBox.information(self, "Aucun résultat", "Lance d'abord une analyse avant d'exporter.")
            return

        from qgis.PyQt.QtWidgets import QFileDialog
        import datetime

        r = self._last_results
        default_name = f"rapport_{r['commune_name']}_{r['year1']}"
        if r["compare"]:
            default_name += f"_{r['year2']}"
        default_name += ".txt"
        default_path = os.path.join(self.output_dir, default_name)

        os.makedirs(self.output_dir, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter les résultats en texte", default_path, "Fichier texte (*.txt)"
        )
        if not path:
            return

        lines = []
        lines.append("=" * 70)
        lines.append("SEN COUVERTURE TERRESTRE - RAPPORT D'ANALYSE")
        lines.append("=" * 70)
        lines.append(f"Commune : {r['commune_name']}")
        lines.append(f"Généré le : {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}")
        lines.append("")

        lines.append("-" * 70)
        if r["compare"]:
            lines.append(f"COUVERTURE TERRESTRE - COMPARAISON {r['year1']} -> {r['year2']}")
            lines.append("-" * 70)
            if r["changed_pct"] is not None:
                lines.append(f"Part de la commune ayant changé de classe : {r['changed_pct']}%")
                lines.append("")
            if r["change_summary"]:
                lines.append(f"{'Classe':30s} {'km2 (' + str(r['year1']) + ')':>15s} "
                              f"{'km2 (' + str(r['year2']) + ')':>15s} {'Delta km2':>12s}")
                for entry in r["change_summary"]:
                    lines.append(
                        f"{entry['label']:30s} {entry['area_km2_year1']:>15.2f} "
                        f"{entry['area_km2_year2']:>15.2f} {entry['delta_km2']:>+12.2f}"
                    )
        else:
            lines.append(f"COUVERTURE TERRESTRE - ANNEE {r['year1']}")
            lines.append("-" * 70)
            if r["class_areas_1"]:
                lines.append(f"{'Classe':30s} {'km2':>12s} {'%':>8s}")
                for entry in r["class_areas_1"]:
                    lines.append(f"{entry['label']:30s} {entry['area_km2']:>12.2f} {entry['pct']:>7.2f}%")
        lines.append("")

        if r["soil_stats"]:
            lines.append("-" * 70)
            lines.append("TYPE DE SOL (MSDNOM) ET FERTILITE ESTIMEE")
            lines.append("-" * 70)
            if r["fertility_index"]:
                fi = r["fertility_index"]
                lines.append(
                    f"Indice de fertilité globale pondérée : {fi['score']}% ({fi['label']}) "
                    f"- basé sur {fi['coverage_pct']}% de la superficie communale renseignée"
                )
                lines.append("")
            lines.append(f"{'Type de sol':30s} {'km2':>12s} {'%':>8s} {'Fertilité estimée':>20s}")
            for s in r["soil_stats"]:
                lines.append(f"{s['label']:30s} {s['area_km2']:>12.2f} {s['pct']:>7.2f}% {s['fertility_label']:>20s}")
            lines.append("")
            lines.append(
                "Remarque : le % représente la part de superficie occupée par chaque type de sol dans la "
                "commune, pas sa fertilité. La fertilité est une estimation agronomique indicative."
            )
            lines.append("")

        if r["relief_stats"]:
            lines.append("-" * 70)
            lines.append("RELIEF (ALTITUDE)")
            lines.append("-" * 70)
            rs = r["relief_stats"]
            lines.append(f"Min : {rs['min']:.1f} m")
            lines.append(f"Max : {rs['max']:.1f} m")
            lines.append(f"Moyenne : {rs['mean']:.1f} m")
            lines.append(f"Écart-type : {rs['std']:.1f} m")
            if rs.get("slope_mean_pct") is not None:
                lines.append(f"Pente moyenne : {rs['slope_mean_pct']:.1f} %")
            lines.append("")

        if r["flood_index"]:
            fi = r["flood_index"]
            lines.append("-" * 70)
            lines.append("ZONE INONDABLE - INDICE D'ALEA MULTI-CRITERES")
            lines.append("-" * 70)
            lines.append(f"Indice : {fi['index_value']} / 4  ->  Classe : {fi['classe']}")
            lines.append(f"Règle PPRI : {fi['ppri']}")
            lines.append("")
            lines.append(f"{'Critère':28s} {'Valeur':>22s} {'Score':>8s} {'Poids':>8s}")
            for c in fi["criteres"]:
                lines.append(f"{c['nom']:28s} {str(c['valeur']):>22s} {c['score']:>6d}/4 {int(c['poids']*100):>6d}%")
            lines.append("")
            lines.append(
                "Note : indice indicatif à l'échelle communale (valeurs moyennes/dominantes), "
                "pas une carte d'aléa pixel par pixel officielle. L'accumulation de flux, la "
                "pluviométrie et la récurrence sont des estimations manuelles à ajuster selon "
                "les données locales disponibles."
            )
            lines.append("")

        lines.append("=" * 70)
        lines.append("Source couverture terrestre : Sentinel-2 10m Land Use/Land Cover Time Series")
        lines.append("(Esri / Impact Observatory / Microsoft, ArcGIS Living Atlas)")
        lines.append("Plugin : Sen Couverture Terrestre - Adiouma Fall - Géo")
        lines.append("=" * 70)

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            QMessageBox.critical(self, "Erreur d'export", f"Impossible d'écrire le fichier : {e}")
            return

        self.status_label.setText(f"Résultats exportés : {path}")
        if qgis_iface:
            qgis_iface.messageBar().pushMessage(
                "Sen Couverture Terrestre", f"Rapport exporté : {os.path.basename(path)}",
                level=Qgis.Success, duration=5
            )
