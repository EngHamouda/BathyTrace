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

"""Projected, metre-based 3D contours for CAD and Shapefile exchange."""
import json
import math
from pathlib import Path
from qgis.PyQt.QtCore import QVariant
from qgis.core import (QgsCoordinateTransform, QgsProject, QgsVectorLayer, QgsField,
    QgsFeature, QgsGeometry, QgsLineString, QgsPoint, QgsUnitTypes, NULL)


def has_depth(feature):
    depth=feature['depth']
    try:
        return depth is not None and depth!=NULL and math.isfinite(float(depth)) and str(feature['unit']) in ('m','ft','fathom')
    except (TypeError,ValueError):
        return False


def build_3d_layer(source, target_crs, assigned_only=False):
    if not target_crs.isValid() or target_crs.isGeographic() or target_crs.mapUnits()!=QgsUnitTypes.DistanceMeters:
        raise ValueError('Choose a projected CRS in metres matching your Civil 3D drawing for 3D export.')
    output=QgsVectorLayer('LineStringZ','3D contours','memory'); output.setCrs(target_crs)
    output.dataProvider().addAttributes([QgsField('cs_id',QVariant.String,len=80),QgsField('depth',QVariant.Double),
        QgsField('elev_m',QVariant.Double),QgsField('unit',QVariant.String,len=12),
        QgsField('reviewed',QVariant.Int),QgsField('d_method',QVariant.String,len=32)])
    output.updateFields()
    transform=QgsCoordinateTransform(source.crs(),target_crs,QgsProject.instance())
    features=[]
    for f in source.getFeatures():
        depth=f['depth']; unit=str(f['unit'])
        if not has_depth(f):
            if assigned_only: continue
            raise ValueError('Some contours have no valid depth/unit. Apply the depth preview or save individual values; alternatively export only contours with depths.')
        z=float(depth)*{'m':1.,'ft':.3048,'fathom':1.8288}[unit]
        geometry=f.geometry()
        if geometry.isMultipart(): raise ValueError('Convert multipart contours to singlepart before CAD export.')
        pts=geometry.asPolyline()
        if len(pts)<2: raise ValueError('Empty or invalid contour geometry.')
        vertices=[]
        for point in pts:
            p=transform.transform(point)
            if not math.isfinite(p.x()) or not math.isfinite(p.y()): raise ValueError('A coordinate could not be transformed to the export CRS.')
            vertices.append(QgsPoint(p.x(),p.y(),z))
        result=QgsFeature(output.fields()); result.setGeometry(QgsGeometry(QgsLineString(vertices)))
        result.setAttributes([f['cs_id'],float(depth),z,unit,int(bool(f['reviewed'])),f['depth_method']])
        features.append(result)
    if not features: raise ValueError('There are no contours to export.')
    ok,_=output.dataProvider().addFeatures(features)
    if not ok: raise ValueError('Could not create 3D export geometry.')
    output.updateExtents()
    return output


def write_dxf(layer,path):
    """ASCII R12 3D POLYLINE/VERTEX entities (not flat 2D linework)."""
    path=Path(path)
    pairs=[]
    def pair(code,value): pairs.extend((str(code),str(value)))
    pair(0,'SECTION'); pair(2,'HEADER'); pair(9,'$ACADVER'); pair(1,'AC1009')
    pair(9,'$LUNITS'); pair(70,2); pair(9,'$LUPREC'); pair(70,6); pair(0,'ENDSEC')
    pair(0,'SECTION'); pair(2,'TABLES'); pair(0,'TABLE'); pair(2,'LAYER'); pair(70,1)
    pair(0,'LAYER'); pair(2,'BATHYMETRY'); pair(70,0); pair(62,4); pair(6,'CONTINUOUS')
    pair(0,'ENDTAB'); pair(0,'ENDSEC'); pair(0,'SECTION'); pair(2,'ENTITIES')
    count=0
    for f in layer.getFeatures():
        vertices=list(f.geometry().vertices())
        closed=vertices[0]==vertices[-1]
        if closed: vertices=vertices[:-1]
        pair(0,'POLYLINE'); pair(8,'BATHYMETRY'); pair(66,1)
        pair(10,0); pair(20,0); pair(30,0); pair(70,9 if closed else 8)
        for p in vertices:
            pair(0,'VERTEX'); pair(8,'BATHYMETRY')
            pair(10,format(p.x(),'.15g')); pair(20,format(p.y(),'.15g')); pair(30,format(p.z(),'.15g')); pair(70,32)
        pair(0,'SEQEND'); pair(8,'BATHYMETRY'); count+=1
    pair(0,'ENDSEC'); pair(0,'EOF')
    path.write_text('\n'.join(pairs)+'\n',encoding='ascii')
    Path(str(path)+'.crs.txt').write_text('BathyTrace — © 2026 Mohamed Hamouda\n'
        +'X, Y and Z are metres. Set the Civil 3D drawing to this coordinate system before use.\n'
        +'Z equals signed depth converted to metres; negative depths remain negative. No vertical datum transformation was applied.\n'
        +layer.crs().authid()+' — '+layer.crs().description()+'\n'+layer.crs().toWkt()+'\n',encoding='utf-8')
    return count
