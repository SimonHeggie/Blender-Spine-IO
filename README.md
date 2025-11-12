# Blender-Spine-IO
## Scripts for importing and exporting 4.3+ Spine .Json files to and from Blender - RXLAB's .OCA/.OCO style.

Blender-Spine-IO is a blender extension/module-set for a Spine Exporter (And later an importer) by Simon Heggie.

This is currently experimental.

INSTALLATION:

Load all PY scripts into your Blend file, run the launcher script.

What works:

-Meshes, Internal edges and all. Quads, Tris, Ngons.
-Bones, with length. (Armature)
-Bone Weights.
-FK Bone animation (OF ONE ACTION)

HOW IT WORKS:
This will export everything in your blender file to a .JSON file name after your blend file. It will be saved to a folder right beside your blend file, matching your blendfile name but instead of .blend it's .OCA. (Blendfilename.OCA). 

KNOWN BUGS: 
Images don't reliably path right now, there may be some issues when test unless you manually place the images in the right path.

SPINE BUG:
4.3.49-beta has invisible meshes, current workaround is to tweak a point on the mesh.

TODO:
-Ensure image paths work reliably.
-Get controller bones working (non deforming bones currently won't animate properly)
-Add driver support. (initially supporting bone driven alpha fade controllers.)
-Add IK support
-ETC

