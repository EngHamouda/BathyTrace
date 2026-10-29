# Copyright (C) 2026 Mohamed Hamouda
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of BathyTrace.
#
# BathyTrace is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# BathyTrace is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BathyTrace. If not, see <https://www.gnu.org/licenses/>.

"""BathyTrace: native QGIS dock, map tools and editable vector output."""
import copy
import json
import math
import uuid
from pathlib import Path

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt import sip
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtWidgets import (QAction, QDockWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QFormLayout, QPushButton, QLabel, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QLineEdit, QFileDialog, QScrollArea, QMessageBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QGroupBox)
from qgis.core import (Qgis, QgsApplication, QgsTask, QgsProject, QgsRasterLayer,
    QgsVectorLayer, QgsField, QgsFeature, QgsGeometry, QgsPointXY,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsVectorFileWriter,
    QgsLineSymbol, QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat,
    QgsWkbTypes, NULL)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsMapToolPan, QgsProjectionSelectionWidget
from .cad_export import build_3d_layer, write_dxf, has_depth

from .joining import join_segments
from .depths import estimate_depths, nearest_point


def clean(value, fallback=None):
    return fallback if value is None or value == NULL else value


class ContourMapTool(QgsMapTool):
    def __init__(self, plugin):
        super().__init__(plugin.canvas)
        self.plugin = plugin
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.plugin.finish_line()
        elif event.button() == Qt.LeftButton:
            self.plugin.map_click(event.mapPoint())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.plugin.cancel_line()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.plugin.finish_line()
        elif event.key() == Qt.Key_Backspace:
            self.plugin.undo_anchor()


class ContourStudio:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.dock = None
        self.chart = None
        self.layer = None
        self.raster = None
        self.active = []
        self.anchors = []
        self.seed = None
        self.preview = None
        self.click_origin = None
        self.busy = False
        self.closing = False
        self.task = None
        self.tool = None
        self.previous_tool = None
        self.buttons = []
        self.layer_id=None
        self.raster_id=None
        self.generation=0
        self.suspended=False
        self.auto_preview=False

    def initGui(self):
        self.action = QAction(QIcon(str(Path(__file__).with_name('icon.svg'))), 'BathyTrace', self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToRasterMenu('BathyTrace', self.action)
        self.iface.addToolBarIcon(self.action)
        QgsProject.instance().layersWillBeRemoved.connect(self.layers_removed)

    def unload(self):
        self.closing = True
        self.deactivate_workspace()
        QgsProject.instance().layersWillBeRemoved.disconnect(self.layers_removed)
        if self.task:
            self.task.cancel()
        if self.layer_alive():
            try:
                self.layer.selectionChanged.disconnect(self.selection_changed)
                self.layer.layerModified.disconnect(self.invalidate_preview)
            except (TypeError, RuntimeError):
                pass
        if self.tool and self.canvas.mapTool() == self.tool:
            self.canvas.unsetMapTool(self.tool)
        if self.dock:
            for band in (self.draft_band,self.guide_band,self.bridge_band):
                self.canvas.scene().removeItem(band)
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
        self.iface.removePluginRasterMenu('BathyTrace',self.action)
        self.iface.removeToolBarIcon(self.action)
        self.action.deleteLater()

    def layer_alive(self):
        return self.layer is not None and not sip.isdeleted(self.layer) and self.layer_id is not None and QgsProject.instance().mapLayer(self.layer_id) is self.layer

    def disconnect_layer(self):
        if self.layer is not None and not sip.isdeleted(self.layer):
            for signal,slot in ((self.layer.selectionChanged,self.selection_changed),(self.layer.layerModified,self.invalidate_preview)):
                try: signal.disconnect(slot)
                except (TypeError,RuntimeError): pass

    def layers_removed(self,ids):
        ids={item if isinstance(item,str) else item.id() for item in ids}
        if self.raster_id in ids: self.raster=None; self.raster_id=None
        if self.layer_id not in ids: return
        self.disconnect_layer()
        self.layer=None; self.layer_id=None
        self.seed=None; self.preview=None; self.click_origin=None; self.active=[]; self.anchors=[]
        self.deactivate_workspace()
        if self.dock:
            self.invalidate_preview()
            self.selected_label.setText('Contour layer removed.')
            self.message('Contour layer removed. Open the TIFF again to start a new contour layer.')

    def deactivate_workspace(self):
        self.suspended=True; self.picking=False; self.auto_preview=False; self.generation+=1
        if self.task: self.task.cancel()
        if self.tool and not sip.isdeleted(self.tool) and self.canvas.mapTool()==self.tool:
            previous=self.previous_tool
            if previous is not None and not sip.isdeleted(previous) and previous!=self.tool:
                self.canvas.setMapTool(previous)
            else:
                self.pan_tool=QgsMapToolPan(self.canvas); self.canvas.setMapTool(self.pan_tool)
        for name in ('draft_band','guide_band','bridge_band'):
            band=getattr(self,name,None)
            if band is not None and not sip.isdeleted(band): band.hide()

    def exit_tool(self):
        self.deactivate_workspace()
        if self.dock: self.dock.hide()

    def dock_visibility(self,visible):
        if not visible and not self.closing: self.deactivate_workspace()

    def select_mode(self):
        self.mode.setCurrentIndex(0); self.activate_tool()

    def extract_joined(self):
        self.require_layer(); self.check_idle_line()
        if self.features(): raise ValueError('This layer already contains contours. Open the TIFF again to create a new layer.')
        chart=self.chart; threshold=self.threshold.value(); minimum=self.minimum.value(); gap=self.gap.value()
        def work():
            return join_segments(chart.detect(threshold,minimum),gap)
        def done(result):
            self.replace(result['features'],'Extract and join contours')
            self.message(f"Created {result['remaining']} contours; joined {result['joins']} gaps. Select the shallowest line to assign depths.")
        self.background('Extracting and joining contours…',work,done)

    def pick_and_preview(self):
        self.pick_direction(); self.auto_preview=True

    def message(self, text, error=False):
        self.status.setText(str(text))
        if error:
            self.iface.messageBar().pushWarning('BathyTrace',str(text))

    def guard(self, fn):
        def wrapped(*args):
            if self.busy:
                return
            try:
                return fn()
            except Exception as exc:
                self.message(exc,True)
        return wrapped

    def button(self, layout, title, fn):
        b=QPushButton(title)
        b.clicked.connect(self.guard(fn))
        layout.addWidget(b)
        self.buttons.append(b)
        return b

    def run(self):
        if self.dock:
            self.suspended=False; self.dock.show(); self.dock.raise_()
            return
        self.dock=QDockWidget('BathyTrace 0.1.0 — Mohamed Hamouda',self.iface.mainWindow())
        self.dock.setObjectName('MohamedHamoudaContourStudio'); self.dock.setMinimumWidth(330)
        self.dock.visibilityChanged.connect(self.dock_visibility)
        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        content=QWidget(); layout=QVBoxLayout(content); scroll.setWidget(content); self.dock.setWidget(scroll)
        subtitle = QLabel('Professional Bathymetric Contour Digitization from Charts & Imagery')
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.status=QLabel('Open a TIFF, extract contours, assign depths, then export.'); self.status.setWordWrap(True); layout.addWidget(self.status)
        def group(title,parent=layout):
            box=QGroupBox(title); parent.addWidget(box); return QVBoxLayout(box)
        load=group('1 · Chart')
        self.button(load,'Open TIFF…',self.browse)
        self.button(load,'Extract + join contours',self.extract_joined)
        depthbox=group('2 · Depths')
        self.button(depthbox,'Select a contour on the map',self.select_mode)
        self.selected_label=QLabel('No contour selected.'); self.selected_label.setWordWrap(True); depthbox.addWidget(self.selected_label)
        attrs=QFormLayout(); depthbox.addLayout(attrs)
        self.depth=QLineEdit(); self.depth.setPlaceholderText('Unassigned')
        self.unit=QComboBox(); self.unit.addItems(['unknown','m','ft','fathom'])
        attrs.addRow('Selected depth',self.depth); attrs.addRow('Unit',self.unit)
        self.button(depthbox,'Save selected value',self.apply_attribute)
        self.button(depthbox,'Use selected as shallowest',self.use_seed)
        seq=QFormLayout(); depthbox.addLayout(seq)
        self.base=QDoubleSpinBox(); self.base.setRange(-100000,0); self.base.setDecimals(3)
        self.interval=QDoubleSpinBox(); self.interval.setRange(.001,10000); self.interval.setDecimals(3); self.interval.setValue(.5)
        seq.addRow('Start depth (m)',self.base); seq.addRow('Interval (m)',self.interval)
        self.base.valueChanged.connect(self.invalidate_preview); self.interval.valueChanged.connect(self.invalidate_preview)
        self.button(depthbox,'Pick deeper direction + preview',self.pick_and_preview)
        self.table=QTableWidget(0,2); self.table.setHorizontalHeaderLabels(['Contour','Depth (m)'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setMaximumHeight(125); depthbox.addWidget(self.table)
        self.apply_sequence=self.button(depthbox,'Apply depth sequence',self.apply_depths); self.apply_sequence.setEnabled(False)
        exportbox=group('3 · Export')
        self.export_format=QComboBox(); self.export_format.addItems(['3D DXF — Civil 3D','3D Shapefile','GeoPackage','GeoJSON'])
        exportbox.addWidget(self.export_format)
        crslabel=QLabel('For DXF / Shapefile: choose the projected metre CRS used in your drawing.'); crslabel.setWordWrap(True); exportbox.addWidget(crslabel)
        self.export_crs=QgsProjectionSelectionWidget(); exportbox.addWidget(self.export_crs)
        self.button(exportbox,'Export…',self.export)
        toggle=QPushButton('Advanced ▸'); toggle.setCheckable(True); layout.addWidget(toggle)
        self.advanced=QWidget(); advanced=QVBoxLayout(self.advanced); layout.addWidget(self.advanced); self.advanced.hide()
        toggle.toggled.connect(self.advanced.setVisible)
        toggle.toggled.connect(lambda checked:toggle.setText('Advanced ▾' if checked else 'Advanced ▸'))
        self.button(advanced,'Use selected QGIS raster',self.use_raster)
        form=QFormLayout(); advanced.addLayout(form)
        self.mode=QComboBox(); self.mode.addItems(['Select contour','Assisted trace','Manual trace']); form.addRow('Map tool',self.mode)
        self.button(advanced,'Activate tracing tool',self.activate_tool)
        self.button(advanced,'Finish line (right-click / Enter)',self.finish_line)
        self.button(advanced,'Undo anchor (Backspace)',self.undo_anchor)
        self.button(advanced,'Discard line / direction (Esc)',self.cancel_line)
        self.threshold=QSpinBox(); self.threshold.setRange(30,200); self.threshold.setValue(150)
        self.minimum=QSpinBox(); self.minimum.setRange(30,5000); self.minimum.setValue(150)
        self.gap=QSpinBox(); self.gap.setRange(1,500); self.gap.setValue(300)
        form.addRow('Darkness threshold',self.threshold); form.addRow('Minimum pixels',self.minimum); form.addRow('Maximum gap (pixels)',self.gap)
        self.button(advanced,'Detect segments only',self.detect); self.button(advanced,'Join existing segments',self.join)
        self.show_bridges=QCheckBox('Show inferred connections'); self.show_bridges.setChecked(True)
        self.show_bridges.toggled.connect(self.draw_bridges); advanced.addWidget(self.show_bridges)
        self.reviewed=QCheckBox('Selected contour reviewed'); advanced.addWidget(self.reviewed)
        self.button(advanced,'Recalculate depth preview',self.preview_depths)
        self.button(advanced,'Save editable project…',self.save_project); self.button(advanced,'Open contour project…',self.open_project)
        self.button(layout,'Undo last change',self.undo)
        exit_button=QPushButton('Exit tool'); exit_button.clicked.connect(self.exit_tool); layout.addWidget(exit_button)
        helptext=QLabel('Contours are temporary until exported. Exit returns control to QGIS and keeps your layers. Estimated depths need review.'); helptext.setWordWrap(True); layout.addWidget(helptext)
        copyright_label = QLabel('<b>Copyright © 2026 Mohamed Hamouda</b>')
        copyright_label.setWordWrap(True)
        layout.addWidget(copyright_label)
        licence_label = QLabel('GPL-3.0-or-later · No warranty. You may modify and redistribute under the GPL. '
            + '<a href="' + Path(__file__).with_name('LICENSE').resolve().as_uri() + '">Read licence</a>')
        licence_label.setWordWrap(True)
        licence_label.setOpenExternalLinks(True)
        layout.addWidget(licence_label); layout.addStretch()
        self.tool=ContourMapTool(self)
        self.draft_band=QgsRubberBand(self.canvas,QgsWkbTypes.LineGeometry); self.draft_band.setColor(QColor('#dd3776')); self.draft_band.setWidth(2)
        self.guide_band=QgsRubberBand(self.canvas,QgsWkbTypes.LineGeometry); self.guide_band.setColor(QColor('#008bc4')); self.guide_band.setWidth(2)
        self.bridge_band=QgsRubberBand(self.canvas,QgsWkbTypes.LineGeometry); self.bridge_band.setColor(QColor('#00bcd4')); self.bridge_band.setWidth(3)
        self.picking=False
        self.iface.addDockWidget(Qt.RightDockWidgetArea,self.dock)
        self.suspended=False

    def background(self,title,fn,done):
        self.suspended=False
        generation=self.generation
        original_chart=self.chart
        original_layer=self.layer
        snapshot=self.features() if self.chart and self.layer_alive() else None
        self.busy=True
        for b in self.buttons: b.setEnabled(False)
        self.message(title)
        def work(task):
            if task.isCanceled(): return None
            result=fn()
            return None if task.isCanceled() else result
        def finished(error,result=None):
            if self.closing: return
            self.busy=False; self.task=None
            for b in self.buttons: b.setEnabled(True)
            self.apply_sequence.setEnabled(self.preview is not None)
            if generation!=self.generation or self.suspended: return
            if error: self.message(error,True)
            elif result is not None:
                try:
                    if self.chart is not original_chart or self.layer is not original_layer:
                        raise ValueError('Workspace changed during processing. Run the operation again.')
                    if snapshot is not None and self.features()!=snapshot:
                        raise ValueError('Contours were edited during processing. The computed result was discarded to preserve those edits.')
                    done(result)
                except Exception as exc: self.message(exc,True)
        self.task=QgsTask.fromFunction(title,work,on_finished=finished)
        QgsApplication.taskManager().addTask(self.task)

    def require_chart(self):
        if not self.chart: raise ValueError('Open a TIFF first.')

    def require_layer(self):
        self.require_chart()
        if not self.layer_alive():
            raise ValueError('The contour layer was removed. Reopen the TIFF or a contour project.')

    def browse(self):
        path,_=QFileDialog.getOpenFileName(self.dock,'Open georeferenced chart','','TIFF (*.tif *.tiff)')
        if path: self.load_chart(path)

    def use_raster(self):
        layer=self.iface.activeLayer()
        if not isinstance(layer,QgsRasterLayer): raise ValueError('Select a TIFF raster layer in QGIS first.')
        self.load_chart(layer.source(),layer)

    def load_chart(self,path,raster=None):
        # Dependency imports are delayed so the plugin can explain a missing package.
        from .raster_core import Chart
        self.background('Loading GeoTIFF…',lambda:Chart(path),lambda chart:self.set_chart(chart,raster))

    def set_chart(self,chart,raster=None):
        if self.layer_alive():
            self.layer.selectionChanged.disconnect(self.selection_changed)
            self.layer.layerModified.disconnect(self.invalidate_preview)
        self.chart=chart; self.seed=None; self.click_origin=None; self.active=[]; self.anchors=[]; self.picking=False
        self.raster=raster if raster is not None and not sip.isdeleted(raster) else QgsRasterLayer(str(chart.path),chart.name)
        if not self.raster.isValid(): raise ValueError('QGIS could not open the raster.')
        if not QgsProject.instance().mapLayer(self.raster.id()): QgsProject.instance().addMapLayer(self.raster)
        self.raster_id=self.raster.id()
        self.layer=QgsVectorLayer('LineString?crs=EPSG:'+str(chart.epsg),'BathyTrace — '+chart.name,'memory')
        self.layer_id=self.layer.id()
        fields=[QgsField('cs_id',QVariant.String),QgsField('depth',QVariant.Double),QgsField('unit',QVariant.String),QgsField('reviewed',QVariant.Bool),QgsField('method',QVariant.String),QgsField('depth_method',QVariant.String),QgsField('bridges',QVariant.String),QgsField('joined_segments',QVariant.Int)]
        self.layer.dataProvider().addAttributes(fields); self.layer.updateFields()
        self.layer.renderer().setSymbol(QgsLineSymbol.createSimple({'line_color':'199,110,36','line_width':'0.45'}))
        settings=QgsPalLayerSettings(); settings.fieldName='CASE WHEN "depth" IS NOT NULL THEN to_string("depth") || \' \' || "unit" END'; settings.isExpression=True
        text=QgsTextFormat(); text.setSize(9); text.setColor(QColor('#174f48')); settings.setFormat(text)
        self.layer.setLabeling(QgsVectorLayerSimpleLabeling(settings)); self.layer.setLabelsEnabled(True)
        QgsProject.instance().addMapLayer(self.layer)
        self.layer.startEditing()
        self.layer.selectionChanged.connect(self.selection_changed)
        self.layer.layerModified.connect(self.invalidate_preview)
        self.invalidate_preview(); self.draw_active()
        self.guide_band.reset(QgsWkbTypes.LineGeometry)
        transform=QgsCoordinateTransform(self.raster.crs(),self.canvas.mapSettings().destinationCrs(),QgsProject.instance())
        self.canvas.setExtent(transform.transformBoundingBox(self.raster.extent())); self.canvas.refresh()
        self.activate_tool(); self.message('TIFF loaded. Detect contours or open a saved contour project. Existing layers are retained.')

    def to_pixel(self,map_point):
        p=QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(),self.layer.crs(),QgsProject.instance()).transform(map_point)
        return self.layer_to_pixel(p)

    def layer_to_pixel(self,p):
        i,j,_,x,y,_=self.chart.tie
        offset=.5 if self.chart.pixel_type==2 else 0
        return [(p.x()-x)/self.chart.scale[0]+i+offset,(y-p.y())/self.chart.scale[1]+j+offset]

    def to_map(self,p):
        x,y=self.chart.projected(p)
        return QgsCoordinateTransform(self.layer.crs(),self.canvas.mapSettings().destinationCrs(),QgsProject.instance()).transform(QgsPointXY(x,y))

    def features(self):
        self.require_layer(); result=[]
        for f in self.layer.getFeatures():
            geom=f.geometry()
            if geom.isMultipart(): raise ValueError('Multipart contours must be converted to singlepart lines first.')
            points=[self.layer_to_pixel(p) for p in geom.asPolyline()]
            if len(points)<2: raise ValueError('A contour has invalid or empty geometry.')
            if any(not all(math.isfinite(v) for v in p) for p in points): raise ValueError('Invalid contour coordinate.')
            result.append(dict(id=str(f['cs_id']),points=points,depth=clean(f['depth']),unit=clean(f['unit'],'unknown'),reviewed=bool(clean(f['reviewed'],False)),method=clean(f['method'],'manual'),depth_method=clean(f['depth_method'],'unassigned'),bridges=json.loads(clean(f['bridges'],'[]')),joined_segments=int(clean(f['joined_segments'],1))))
        if len({f['id'] for f in result})!=len(result): raise ValueError('Contour IDs must be unique.')
        return result

    def replace(self,features,title):
        self.require_layer()
        if not self.layer.isEditable(): self.layer.startEditing()
        self.layer.beginEditCommand(title)
        try:
            if not self.layer.deleteFeatures([f.id() for f in self.layer.getFeatures()]): raise ValueError('Could not replace contours.')
            for data in features:
                f=QgsFeature(self.layer.fields())
                f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*self.chart.projected(p)) for p in data['points']]))
                f.setAttributes([str(data['id']),data.get('depth'),data.get('unit','unknown'),data.get('reviewed',False),data.get('method','manual'),data.get('depth_method','unassigned'),json.dumps(data.get('bridges',[])),data.get('joined_segments',1)])
                if not self.layer.addFeature(f): raise ValueError('Could not add contour.')
            self.layer.endEditCommand()
        except Exception:
            self.layer.destroyEditCommand(); raise
        self.layer.updateExtents(); self.layer.triggerRepaint(); self.invalidate_preview()

    def activate_tool(self):
        self.require_layer()
        self.suspended=False
        if self.canvas.mapTool()!=self.tool: self.previous_tool=self.canvas.mapTool()
        self.iface.setActiveLayer(self.layer); self.canvas.setMapTool(self.tool)

    def selected(self):
        self.require_layer(); f=self.layer.selectedFeatures()
        if len(f)!=1: raise ValueError('Select exactly one contour.')
        return f[0]

    def selection_changed(self,*args):
        self.click_origin=None
        if not self.layer_alive(): return
        f=self.layer.selectedFeatures()
        if len(f)!=1:
            self.selected_label.setText('Select exactly one contour.'); return
        feature=f[0]; self.selected_label.setText('Selected: '+str(feature['cs_id']))
        d=clean(feature['depth']); self.depth.setText('' if d is None else str(d))
        self.unit.setCurrentText(clean(feature['unit'],'unknown')); self.reviewed.setChecked(bool(clean(feature['reviewed'],False)))

    def map_click(self,p):
        if self.busy or self.suspended or not self.layer_alive(): return
        try:
            self.require_layer(); pixel=self.to_pixel(p)
            if not (0<=pixel[0]<self.chart.w and 0<=pixel[1]<self.chart.h): return
            if self.picking:
                self.seed['toward']=pixel; self.picking=False; self.invalidate_preview()
                self.guide_band.setToGeometry(QgsGeometry.fromPolylineXY([self.to_map(self.seed['origin']),p]),None)
                if self.auto_preview: self.auto_preview=False; self.preview_depths()
                else: self.message('Direction set. Preview the depth sequence.')
                return
            if self.mode.currentIndex()==0:
                limit=self.canvas.mapUnitsPerPixel()*10; best=None
                transform=QgsCoordinateTransform(self.layer.crs(),self.canvas.mapSettings().destinationCrs(),QgsProject.instance())
                point=QgsGeometry.fromPointXY(p)
                for f in self.layer.getFeatures():
                    geometry=QgsGeometry(f.geometry()); geometry.transform(transform)
                    distance=geometry.distance(point)
                    if 0<=distance<limit: best=f; limit=distance
                self.layer.selectByIds([] if best is None else [best.id()])
                if best is not None: self.click_origin=nearest_point([self.layer_to_pixel(q) for q in best.geometry().asPolyline()],pixel)
                return
            if not self.active or self.mode.currentIndex()==2:
                self.anchors.append(len(self.active)); self.active.append(pixel); self.draw_active(); return
            start=self.active[-1]
            def done(points):
                self.anchors.append(len(self.active)); self.active.extend(points); self.draw_active(); self.message('Segment traced. Inspect it, then add another anchor or finish.')
            self.background('Following contour…',lambda:self.chart.trace(start,pixel),done)
        except Exception as exc: self.message(exc,True)

    def draw_active(self):
        self.draft_band.reset(QgsWkbTypes.LineGeometry)
        if self.active and self.layer_alive() and not self.suspended: self.draft_band.setToGeometry(QgsGeometry.fromPolylineXY([self.to_map(p) for p in self.active]),None)

    def draw_bridges(self,*args):
        if not hasattr(self,'bridge_band'): return
        self.bridge_band.reset(QgsWkbTypes.LineGeometry)
        if self.suspended or not self.show_bridges.isChecked() or not self.chart or not self.layer_alive(): return
        bridges=[]
        for f in self.layer.getFeatures():
            points=[self.layer_to_pixel(p) for p in f.geometry().asPolyline()]
            # Draw only bridges still present after QGIS vertex editing.
            for a,b in json.loads(clean(f['bridges'],'[]')):
                if any((math.dist(a,c)<1e-5 and math.dist(b,d)<1e-5) or (math.dist(a,d)<1e-5 and math.dist(b,c)<1e-5) for c,d in zip(points,points[1:])):
                    bridges.append([QgsPointXY(*self.chart.projected(a)),QgsPointXY(*self.chart.projected(b))])
        if bridges: self.bridge_band.setToGeometry(QgsGeometry.fromMultiPolylineXY(bridges),self.layer)

    def finish_line(self):
        if self.busy or self.suspended or not self.layer_alive() or len(self.active)<2: return
        features=self.features(); features.append(dict(id=uuid.uuid4().hex,points=copy.deepcopy(self.active),depth=None,unit='unknown',method='assisted' if self.mode.currentIndex()==1 else 'manual'))
        self.replace(features,'Add contour'); self.active=[]; self.anchors=[]; self.draw_active(); self.message('Contour added. Select it to assign a depth.')

    def undo_anchor(self):
        if self.busy: return
        if self.anchors: self.active=self.active[:self.anchors.pop()]; self.draw_active()

    def cancel_line(self):
        if self.busy: return
        self.active=[]; self.anchors=[]; self.picking=False; self.auto_preview=False; self.draw_active()

    def check_idle_line(self):
        if self.active: raise ValueError('Finish or discard the current line first.')

    def detect(self):
        self.require_layer(); self.check_idle_line(); old=self.features()
        if old: raise ValueError('This layer already contains contours. Reopen the TIFF to detect into a new layer; the existing layer will be retained.')
        threshold,minimum=self.threshold.value(),self.minimum.value()
        def done(found):
            known={json.dumps(f['points']) for f in old}
            added=[dict(f,id=uuid.uuid4().hex) for f in found if json.dumps(f['points']) not in known]
            self.replace(old+added,'Detect contours'); self.message(f'Added {len(added)} draft segments. Join gaps, then assign depths.')
        self.background('Detecting contour segments…',lambda:self.chart.detect(threshold,minimum),done)

    def join(self):
        self.check_idle_line(); features=self.features(); gap=self.gap.value()
        def done(result):
            if result['joins']: self.replace(result['features'],'Join contour gaps')
            self.message(f"Joined {result['joins']} gaps; {result['remaining']} contours remain. Review inferred connections.")
        self.background('Joining contour gaps…',lambda:join_segments(features,gap),done)

    def undo(self):
        self.require_layer()
        if self.layer.isEditable() and self.layer.undoStack().canUndo():
            self.layer.undoStack().undo(); self.layer.triggerRepaint(); self.invalidate_preview(); self.message('Last layer change undone.')

    def apply_attribute(self):
        selected=self.selected(); depth=None if not self.depth.text().strip() else float(self.depth.text())
        if depth is not None and not math.isfinite(depth): raise ValueError('Enter a finite depth.')
        values=self.features()
        for f in values:
            if f['id']==str(selected['cs_id']): f.update(depth=depth,unit=self.unit.currentText(),reviewed=self.reviewed.isChecked(),depth_method='manual')
        self.replace(values,'Assign contour depth'); self.message('Depth saved.')

    def use_seed(self):
        f=self.selected(); pts=[self.layer_to_pixel(p) for p in f.geometry().asPolyline()]
        self.seed=dict(seed_id=str(f['cs_id']),origin=self.click_origin or pts[len(pts)//2],toward=None)
        d=clean(f['depth'])
        if d is not None and d<=0 and f['unit']=='m': self.base.setValue(d)
        self.invalidate_preview(); self.message('Shallowest reference set. Set its depth and interval, then pick deeper water.')

    def pick_direction(self):
        if not self.seed: raise ValueError('Use a selected contour as the shallowest reference first.')
        self.check_idle_line(); self.picking=True; self.activate_tool(); self.message('Click across the contours toward deeper water.')

    def invalidate_preview(self,*args):
        self.preview=None
        if self.dock: self.table.setRowCount(0); self.apply_sequence.setEnabled(False)
        self.draw_bridges()

    def preview_depths(self):
        self.check_idle_line()
        if not self.seed or not self.seed['toward']: raise ValueError('Choose a shallowest contour and the direction toward deeper water.')
        self.preview=estimate_depths(self.features(),**self.seed,base=self.base.value(),interval=self.interval.value())
        rows=self.preview['assignments']; self.table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            self.table.setItem(i,0,QTableWidgetItem(str(r['id']))); self.table.setItem(i,1,QTableWidgetItem(str(r['depth'])))
        self.apply_sequence.setEnabled(True)
        stop_note=' Stopped at the first return crossing; check this is the intended downhill slope.' if self.preview.get('stopped_at_return') else ''
        self.message(f"Preview: {len(rows)} contours, {rows[0]['depth']} to {rows[-1]['depth']} m. {len(self.preview['skipped'])} outside this slope remain unchanged.{stop_note} Click Apply depth sequence to save these values.")

    def apply_depths(self):
        if not self.preview: raise ValueError('Preview the sequence first.')
        values=self.features(); plan={a['id']:a['depth'] for a in self.preview['assignments']}
        for f in values:
            if f['id'] in plan: f.update(depth=plan[f['id']],unit='m',reviewed=False,depth_method='interval-estimate')
        self.replace(values,'Estimate depth sequence'); self.message(f'Applied {len(plan)} estimated depths. Use Undo last layer change to restore.')

    def save_project(self):
        self.check_idle_line(); values=self.features()
        path,_=QFileDialog.getSaveFileName(self.dock,'Save contour project','','Contour project (*.json)')
        if not path: return
        if not path.lower().endswith('.json'): path+='.json'
        data=dict(version=1,signature=self.chart.signature,features=values,active=[],history=[],editing=None,depthState=self.seed,depthSettings=dict(base=self.base.value(),interval=self.interval.value()))
        Path(path).write_text(json.dumps(data,indent=2),encoding='utf-8'); self.message('Contour project saved.')

    def open_project(self):
        self.require_layer(); self.check_idle_line()
        path,_=QFileDialog.getOpenFileName(self.dock,'Open BathyTrace project','','JSON (*.json)')
        if path: self.load_project(path)

    def load_project(self,path):
        self.require_layer()
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('version')!=1 or data.get('signature')!=self.chart.signature: raise ValueError('Project does not match the loaded TIFF.')
        if data.get('active') or data.get('editing'): raise ValueError('Finish or discard the unfinished line in the source tool, then save the project again.')
        values=data['features']; ids=set()
        for f in values:
            if not isinstance(f['id'],str) or f['id'] in ids or len(f['points'])<2: raise ValueError('Invalid contour IDs or geometry.')
            ids.add(f['id'])
            for p in f['points']:
                if len(p)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p) or not (0<=p[0]<=self.chart.w and 0<=p[1]<=self.chart.h): raise ValueError('Invalid contour coordinates.')
            if f.get('depth') is not None and not math.isfinite(f['depth']): raise ValueError('Invalid depth.')
            if f.get('unit','unknown') not in ('m','ft','fathom','unknown'): raise ValueError('Invalid depth unit.')
            for bridge in f.get('bridges',[]):
                if len(bridge)!=2 or any(len(p)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p) for p in bridge): raise ValueError('Invalid bridge geometry.')
        self.replace(values,'Open contour project')
        self.active=data.get('active',[]); self.anchors=data.get('history',[])
        self.seed=None; self.draw_active(); self.message(f'Loaded {len(values)} contours. Previous layer state is available through Undo.')

    def export(self):
        self.require_layer(); self.check_idle_line()
        driver,filter_=[('DXF','3D DXF (*.dxf)'),('ESRI Shapefile','3D Shapefile (*.shp)'),('GPKG','GeoPackage (*.gpkg)'),('GeoJSON','GeoJSON (*.geojson)')][self.export_format.currentIndex()]
        target=self.export_crs.crs()
        assigned_only=False
        if driver in ('DXF','ESRI Shapefile'):
            missing=[f.id() for f in self.layer.getFeatures() if not has_depth(f)]
            if missing:
                valid=self.layer.featureCount()-len(missing)
                dialog=QMessageBox(self.dock)
                dialog.setWindowTitle('3D export — missing depths')
                dialog.setText(f'{valid} contours have depths; {len(missing)} need a depth or unit. Preview values must be applied before export.')
                select=dialog.addButton('Select lines needing depths',QMessageBox.ActionRole)
                partial=dialog.addButton(f'Export {valid} valued contours only',QMessageBox.AcceptRole)
                partial.setEnabled(valid>0)
                dialog.addButton(QMessageBox.Cancel)
                dialog.exec_()
                if dialog.clickedButton()==select:
                    self.layer.selectByIds(missing)
                    self.message(f'Selected {len(missing)} contours needing depth/unit. Apply a sequence or select each line and save its value.')
                    return
                if dialog.clickedButton()!=partial: return
                assigned_only=True
            build_3d_layer(self.layer,target,assigned_only)  # Validate before asking where to save.
        path,_=QFileDialog.getSaveFileName(self.dock,'Export contours','',filter_)
        if path: self.export_to(path,driver,target,assigned_only)

    def export_to(self,path,driver='GPKG',target_crs=None,assigned_only=False):
        self.require_layer()
        suffix={'GeoJSON':'.geojson','GPKG':'.gpkg','DXF':'.dxf','ESRI Shapefile':'.shp'}[driver]
        if not path.lower().endswith(suffix): path+=suffix
        source=self.layer
        if driver in ('DXF','ESRI Shapefile'):
            source=build_3d_layer(self.layer,target_crs or QgsCoordinateReferenceSystem(),assigned_only)
        if driver=='DXF':
            write_dxf(source,path)
        else:
            options=QgsVectorFileWriter.SaveVectorOptions(); options.driverName=driver; options.layerName='bathymetry_contours'; options.fileEncoding='UTF-8'
            if driver=='GeoJSON': options.ct=QgsCoordinateTransform(self.layer.crs(),QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance())
            elif driver=='ESRI Shapefile': options.overrideGeometryType=QgsWkbTypes.LineStringZ; options.includeZ=True
            elif target_crs and target_crs.isValid(): options.ct=QgsCoordinateTransform(self.layer.crs(),target_crs,QgsProject.instance())
            result=QgsVectorFileWriter.writeAsVectorFormatV3(source,path,QgsProject.instance().transformContext(),options)
            if result[0]!=QgsVectorFileWriter.NoError: raise ValueError(str(result))
        self.message(f'Exported {source.featureCount()} contours; {self.layer.featureCount()-source.featureCount()} omitted. '+path)
        return path
