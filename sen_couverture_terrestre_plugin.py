# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon

from .sen_couverture_terrestre_dialog import SenCouvertureTerrestreDialog


class SenCouvertureTerrestrePlugin:

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.landcover_action = None
        self.landcover_dialog = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.landcover_action = QAction(icon, "Sen Couverture Terrestre", self.iface.mainWindow())
        self.landcover_action.triggered.connect(self.run_landcover)
        self.iface.addToolBarIcon(self.landcover_action)
        self.iface.addPluginToWebMenu("&Sen Couverture Terrestre", self.landcover_action)

    def unload(self):
        self.iface.removePluginWebMenu("&Sen Couverture Terrestre", self.landcover_action)
        self.iface.removeToolBarIcon(self.landcover_action)

    def run_landcover(self):
        if self.landcover_dialog is None:
            self.landcover_dialog = SenCouvertureTerrestreDialog(self.iface, self.iface.mainWindow())
        self.landcover_dialog.show()
        self.landcover_dialog.raise_()
        self.landcover_dialog.activateWindow()
