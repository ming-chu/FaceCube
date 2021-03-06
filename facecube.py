""" FaceCube: Copy objects using a Kinect and RepRap

Copyright (c) 2011, Nirav Patel <http://eclecti.cc>

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

This script allows you to capture whatever your Kinect is pointing at as a 
point cloud to be formed into a solid STL in MeshLab.  Specific objects can
be thresholded, segmented out, and hole filled.

PlyWriter - Saves a numpy array of a point cloud as a PLY file
FaceCube - Does the actual capture, thresholding, and segmentation
'main' - Pygame loop that displays the capture and accepts key and mouse input
"""

#!/usr/bin/env python

import sys
import subprocess
import freenect
import numpy
import scipy
import scipy.ndimage

class PlyWriter(object):
    """Writes out the point cloud in the PLY file format
    http://en.wikipedia.org/wiki/PLY_%28file_format%29"""
       
    def __init__(self,name):
        self.name =  name
        # depth to calculate x and y from, to keep a uniform perspective
        self.z_p = 0
        
    def to_world(self, point):
        x_out = float(point[0] - self.dims[0] / 2) * self.scale
        y_out = float(point[1] - self.dims[1] / 2) * self.scale
        return (x_out, y_out)
        
    def save(self,array,leave_holes):
        points = []
        
        farthest = numpy.amax(array)
        farthest_mm = 1000.0/(-0.00307 * farthest + 3.33)
        self.z_p = farthest_mm
        self.dims = array.shape
        minDistance = -100
        scaleFactor = 0.0021
        self.scale = float(self.z_p + minDistance) * scaleFactor
        
        a = numpy.argwhere(array)
        min_point, max_point = a.min(0), a.max(0) + 1
        min_point = self.to_world(min_point)
        max_point = self.to_world(max_point)
        center_mm = ((min_point[0]+max_point[0])/2.0,(min_point[1]+max_point[1])/2)
        size_mm = (max_point[0]-min_point[0],max_point[1]-min_point[1])

        points.extend(self.outline_points(array,farthest,leave_holes))
        points.extend(self.back_points(array,farthest,leave_holes))
        points.extend(self.mesh_points(array))
        
        f = open(self.name,'w')
        
        self.write_header(f,points)
        self.write_points(f,points,farthest_mm,center_mm)
        
        f.close()
        
        return size_mm
        
    # inspired by, but not based on http://borglabs.com/blog/create-point-clouds-from-kinect
    def mesh_points(self,array):
        points = []
        
        # depth approximation from ROS, in mm
        array = (array != 0) * 1000.0/(-0.00307 * array + 3.33)
        
        for i in range(0,self.dims[0]):
            for j in range(0,self.dims[1]):
                z = array[i,j]
                if z:
                    # from http://openkinect.org/wiki/Imaging_Information
                    (x, y) = self.to_world((i, j))
                    points.append((x,y,z))
                    
        return points
        
    def outline_points(self,array,depth,leave_holes):
        """Adds an outline going back to the farthest depth to give MeshLab an
        easier point cloud to turn into a solid"""
        points = []
        
        mask = array != 0
        if not leave_holes:
            scipy.ndimage.morphology.binary_fill_holes(mask, output=mask)
        outline = array * (mask - scipy.ndimage.morphology.binary_erosion(mask))
        
        for i in range(0,self.dims[0]):
            for j in range(0,self.dims[1]):
                z = outline[i,j]
                if z:
                    z += 1
                    while z < depth:
                        z_mm = 1000.0/(-0.00307 * z + 3.33)
                        (x, y) = self.to_world((i, j))
                        points.append((x,y,z_mm))
                        z += 1
        
        return points
        
    def back_points(self,array,depth,leave_holes):
        """Adds a plane of points at the maximum depth to make it easier for MeshLab
        to mesh a solid"""
        mask = array != 0
        if not leave_holes:
            scipy.ndimage.morphology.binary_fill_holes(mask, output=mask)
        array = depth * mask
        
        return self.mesh_points(array)
        
    def write_header(self,f,points):
        f.write('ply\n')
        f.write('format ascii 1.0\n')
        f.write('element vertex %d\n' % len(points))
        f.write('property float x\n')
        f.write('property float y\n')
        f.write('property float z\n')
        f.write('end_header\n')
        
    def write_points(self,f,points,farthest,center):
        """writes out the points with z starting at 0"""
        for point in points:
            f.write('%f %f %f\n' % (point[0]-center[0],point[1]-center[1],farthest-point[2]))
        

class FaceCube(object):
    def __init__(self):
        self.depth, timestamp = freenect.sync_get_depth()
        self.threshold = None
        self.segmented = None
        self.selected_segment = None
        pass
    
    def update(self):
        """grabs a new frame from the Kinect"""
        depth_rotated, timestamp = freenect.sync_get_depth()
        self.depth = depth_rotated.transpose()
        
    def generate_threshold(self, face_depth):
        """thresholds out the closest face_depth cm of stuff"""
        # the image breaks down when you get too close, so cap it at around 50cm
        self.depth = self.depth + 2047 * (self.depth <= 500)
        closest = numpy.amin(self.depth)
        closest_cm = 100.0/(-0.00307 * closest + 3.33)
        farthest = (100/(closest_cm + face_depth) - 3.33)/-0.00307
        self.threshold = self.depth * (self.depth <= farthest)
    
    def select_segment(self,point):
        """picks a segment at a specific point.  if there is no segment there,
        it resets to just show everything within the thresholded image"""
        segments, num_segments = scipy.ndimage.measurements.label(self.threshold)
        selected = segments[point[0],point[1]]
        
        if selected:
            self.selected_segment = (point[0],point[1])
        else:
            self.selected_segment = None
            self.segmented = None
    
    def segment(self):
        """does the actual segmenting"""
        if self.selected_segment != None:
            segments, num_segments = scipy.ndimage.measurements.label(self.threshold)
            selected = segments[self.selected_segment]
            if selected:
                self.segmented = self.threshold * (segments == selected)
            else:
                self.segmented = None
        
    def hole_fill(self,window):
        """fills holes in the object with an adjustable window size
        bigger windows fill bigger holes, but will start to alias the object"""
        if self.segmented != None:
            self.segmented = scipy.ndimage.morphology.grey_closing(self.segmented,size=(window,window))
            
    def get_array(self):
        if self.segmented != None:
            return self.segmented
        else:
            return self.threshold
        
def facecube_usage():
    print 'This script allows you to capture whatever your Kinect is pointing at as a'
    print 'point cloud to be formed into a solid STL in MeshLab.  Specific objects can'
    print 'be thresholded, segmented out, and hole filled.'
    print 'Usage: python facecube.py filename'
    print ' '
    print 'Up/Down      Adjusts the depth of the threshold closer or deeper'
    print '             (can still be used while paused)'
    print 'Spacebar     Pauses or unpauses capture'
    print 'Mouse Click  Click on an object to choose it and hide everything else.'
    print '             Click elsewhere to clear the selection.'
    print 'H/G          After choosing an object, H increases hole filling, G decreases'
    print 'D            Toggles donut mode. Defaults to off.  Turn on if the object'
    print '             should have holes going through it.'
    print 'S            Saves the object as a point cloud, filename.ply'
    print 'O            Outputs the object as a solid, filename.stl'
    print 'P            Saves a screenshot as filename.png'
        
def save_ply(facecube, filename, donut):
    print "Saving array as %s.ply..." % filename
    writer = PlyWriter(filename + '.ply')
    size = writer.save(facecube.get_array(),donut)
    print "done. size " + repr(size)
    return size
    
def save_stl(filename):
    print "Forming temporary solid %s.obj..." % filename
    subprocess.call(["meshlabserver","-i", filename+".ply","-o",filename+".obj","-s",sys.path[0]+"/meshing_poissonb.mlx"])
    print "Simplifying and saving %s.stl..." % filename
    subprocess.call(["meshlabserver","-i", filename+".obj","-o",filename+".stl","-s",sys.path[0]+"/meshing_simplifyb.mlx"])
    print "done"
    
if __name__ == '__main__':
    import pygame
    from pygame.locals import *

    facecube_usage()
    size = (640, 480)
    pygame.init()
    display = pygame.display.set_mode(size, 0)
    face_depth = 10.0
    facecube = FaceCube()
    going = True
    capturing = True
    donut = False
    hole_filling = 0
    changing_depth = 0.0
    filename = 'facecube_test'
    if len(sys.argv) > 1:
        filename = sys.argv[1]
    
    while going:
        events = pygame.event.get()
        for e in events:
            if e.type == QUIT or (e.type == KEYDOWN and e.key == K_ESCAPE):
                going = False
                
            elif e.type == KEYDOWN:
                if e.key == K_UP:
                    changing_depth = 1.0
                elif e.key == K_DOWN:
                    changing_depth = -1.0
                elif e.key == K_SPACE:
                    capturing = not capturing
                elif e.key == K_h:
                    hole_filling += 1
                    print "Hole filling window set to %d" % hole_filling
                elif e.key == K_g:
                    hole_filling = max(0,hole_filling-1)
                    print "Hole filling window set to %d" % hole_filling
                elif e.key == K_d:
                    donut = not donut
                    donutstring = "off"
                    if donut:
                        donutstring = "on"
                    print "Turning donut mode %s" % (donutstring)
                elif e.key == K_s:
                    save_ply(facecube, filename, donut)
                elif e.key == K_o:
                    save_ply(facecube, filename, donut)
                    save_stl(filename)
                elif e.key == K_p:
                    screenshot = pygame.surfarray.make_surface(facecube.get_array())
                    pygame.image.save(screenshot,filename + '.png')
                elif e.key == K_1:
                    size = save_ply(facecube, filename, donut)
                    save_stl(filename)
                    subprocess.call(["openscad","-s", filename+"_token.stl","-D","file=\"" + filename+".stl\"","-D","xin="+str(size[0]),"-D","yin="+str(size[1]),"token.scad"])
                    print "saved " + filename + "_token.stl"
                elif e.key == K_2:
                    size = save_ply(facecube, filename, donut)
                    save_stl(filename)
                    subprocess.call(["openscad","-s", filename+"_carbonite.stl","-D","file=\"" + filename+".stl\"","-D","xin="+str(size[0]),"-D","yin="+str(size[1]),"carbonite.scad"])
                    print "saved " + filename + "_carbonite.stl"
                elif e.key == K_3:
                    size = save_ply(facecube, filename, donut)
                    save_stl(filename)
                    subprocess.call(["openscad","-s", filename+"_rescale.stl","-D","file=\"" + filename+".stl\"","-D","xin="+str(size[0]),"-D","yin="+str(size[1]),"rescale.scad"])
                    print "saved " + filename + "_rescale.stl"
            elif e.type == KEYUP:
                if changing_depth != 0.0:
                    changing_depth = 0.0
                    print "Getting closest %d cm" % face_depth
                    
            elif e.type == MOUSEBUTTONDOWN:
                facecube.select_segment(pygame.mouse.get_pos())
                
        if capturing:
            facecube.update()
        
        face_depth = min(max(0.0,face_depth + changing_depth),2047.0)
        
        facecube.generate_threshold(face_depth)
        facecube.segment()
        if hole_filling:
            facecube.hole_fill(hole_filling)
        
        # this is not actually correct, but it sure does look cool!
        display.blit(pygame.surfarray.make_surface(facecube.get_array()),(0,0))
        pygame.display.flip()
