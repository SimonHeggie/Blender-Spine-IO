import bpy, statistics, math
from mathutils import Matrix
from .SPINE_Output_config import (AUTO_MIN_S,AUTO_MAX_S,RIG_SCALE_MODE,
    RIG_SCALE_CONSTANT, USE_EMPTY_AS_ROOT_FRAME, ROOT_EMPTY_NAME)
from .SPINE_Output_images_uv import first_image_and_uvmap_for_obj

def active_armature():
    a = bpy.context.view_layer.objects.active
    if a and a.type == 'ARMATURE': 
        return a
    for o in bpy.context.scene.objects:
        if o.type == 'ARMATURE': 
            return o
    return None

def armature_for_mesh(obj):
    for m in obj.modifiers:
        if m.type == 'ARMATURE' and m.object and m.object.type == 'ARMATURE':
            return m.object
    p = obj.parent
    while p:
        if p.type == 'ARMATURE': 
            return p
        p = p.parent
    return None

def find_root_empty(arm):
    if ROOT_EMPTY_NAME:
        obj = bpy.data.objects.get(ROOT_EMPTY_NAME)
        if obj and obj.type == 'EMPTY':
            return obj
    if arm.parent and arm.parent.type == 'EMPTY':
        return arm.parent
    for name in ("Root","root","ROOT","RigRoot","SceneRoot","ArmatureRoot"):
        obj = bpy.data.objects.get(name)
        if obj and obj.type == 'EMPTY':
            return obj
    return None

def frame_matrices(arm):
    if USE_EMPTY_AS_ROOT_FRAME:
        rempty = find_root_empty(arm)
        if rempty:
            try:
                toFrame = rempty.matrix_world.inverted_safe()
            except Exception:
                toFrame = rempty.matrix_world.inverted()
            return toFrame, rempty.matrix_world
    return Matrix.Identity(4), Matrix.Identity(4)

def head_tail_in_frame(bone, arm_obj, toFrame: Matrix):
    Mw = arm_obj.matrix_world
    hW = Mw @ bone.head_local
    tW = Mw @ bone.tail_local
    hF = toFrame @ hW.to_4d(); tF = toFrame @ tW.to_4d()
    return hF.xyz, tF.xyz

def angle_ccw_xz(p_head, p_tail):
    dx = (p_tail.x - p_head.x)
    dz = (p_tail.z - p_head.z)
    if abs(dx) < 1e-12 and abs(dz) < 1e-12:
        return 0.0
    return math.degrees(math.atan2(dz, dx))

def basis_from_parent(p_head, p_tail):
    dx = (p_tail.x - p_head.x)
    dz = (p_tail.z - p_head.z)
    L = math.hypot(dx, dz)
    if L < 1e-12:
        ux, uz = 1.0, 0.0
    else:
        ux, uz = dx/L, dz/L
    vx, vz = -uz, ux
    return (ux, uz), (vx, vz)

def project_to_basis_px(p_head, basis_x, basis_y, pointF, scale_px):
    vx = (pointF.x - p_head.x)
    vz = (pointF.z - p_head.z)
    lx_bu = basis_x[0]*vx + basis_x[1]*vz
    ly_bu = basis_y[0]*vx + basis_y[1]*vz
    return (lx_bu*scale_px, ly_bu*scale_px)

def compute_auto_rig_scale(meshes):
    samples=[]
    for o in meshes:
        info = first_image_and_uvmap_for_obj(o)
        if not info or not info[3]:
            continue
        img_w, img_h = info[3]
        dim_x = max(o.dimensions.x, 1e-6)
        dim_z = max(o.dimensions.z, 1e-6)
        if img_w > 0: samples.append(img_w / dim_x)
        if img_h > 0: samples.append(img_h / dim_z)
    if not samples:
        return 1.0
    s = statistics.median(samples)
    return float(max(AUTO_MIN_S, min(AUTO_MAX_S, s)))

__all__ = [
    "active_armature","armature_for_mesh","find_root_empty","frame_matrices",
    "head_tail_in_frame","angle_ccw_xz","basis_from_parent","project_to_basis_px",
    "compute_auto_rig_scale"
]
