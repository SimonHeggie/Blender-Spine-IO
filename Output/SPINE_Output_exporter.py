# SPINE_Output_exporter.py
# Thin exporter: bones/slots/weights/IO/logging only.
# Mesh, UV, tris, hull, edge logic → SPINE_Output_meshutils.
# Weight detection + influence building → SPINE_Output_weights.

import bpy, os, json, math, traceback
from pathlib import Path
from mathutils import Vector

from .SPINE_Output_config import (
    SPINE_VERSION, RIG_SCALE_MODE, RIG_SCALE_CONSTANT,
    ROOT_ROTATION_DEG, VERTICAL_TOL_DEG, WEIGHT_EPS, MAX_INFLUENCES,
    MESH_ROTATE_DEG_DEFAULT, MESH_ROTATE_DEG_BY_OBJECT,
    EMIT_NONESSENTIAL, EDGES_MODE, COPY_IF_MISSING
)

from .SPINE_Output_rigspace import (
    active_armature, armature_for_mesh, frame_matrices, head_tail_in_frame,
    angle_ccw_xz, basis_from_parent, project_to_basis_px, compute_auto_rig_scale
)

from .SPINE_Output_helpers import normalize_deg, is_vertical_rel_deg, rotate2d

from .SPINE_Output_images_uv import (
    first_image_and_uvmap_for_obj, rel_path_inside, copy_into_textures
)

from .SPINE_Output_meshutils import (
    prepare_region, build_edges_from_tris_and_manual, sanitize_vert_edges,
    encode_spine_edges, validate_attachment_edges, edges_perimeter_from_hull
)

from .SPINE_Output_weights import (
    mesh_has_deform_weights, pick_slot_bone_for_object, build_vertex_influences,
    first_nonroot_deform_child
)

try:
    from .SPINE_Output_animation import build_animations
except Exception:
    try:
        from SPINE_Output_animation import build_animations
    except Exception:
        build_animations = None


def export_now():
    # Paths / log
    blend_path = Path(bpy.data.filepath) if bpy.data.filepath else Path(os.getcwd()) / "untitled.blend"
    base = blend_path.stem
    oca_dir = blend_path.with_name(f"{base}.oca")
    oca_dir.mkdir(parents=True, exist_ok=True)
    out_json = oca_dir / f"{base}.json"
    out_log  = blend_path.with_name(f"{base}_log.txt")

    LOG = []
    def log(m):
        s = str(m); print(s); LOG.append(s)

    log("=== Spine Export Start ===")
    log(f"Blend: {blend_path}")
    log(f"OCA dir: {oca_dir}")
    log(f"JSON out: {out_json}")

    try:
        arm = active_armature()
        if not arm:
            raise RuntimeError("No armature found.")

        meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH' and armature_for_mesh(o) == arm]
        if not meshes:
            raise RuntimeError("No meshes linked to active armature.")

        # Depth order: front first
        meshes.sort(key=lambda o: (o.matrix_world.translation.y, o.name), reverse=True)

        SCALE = float(RIG_SCALE_CONSTANT) if RIG_SCALE_MODE.upper() == "CONSTANT" else compute_auto_rig_scale(meshes)
        log(f"[SpineExport] Rig scale = {SCALE:.4f} px/BU")

        toFrame, fromFrame = frame_matrices(arm)
        all_bones = list(arm.data.bones)

        # Bone-space caches
        headF, tailF, angleF = {}, {}, {}
        for b in all_bones:
            hF, tF = head_tail_in_frame(b, arm, toFrame)
            headF[b.name] = hF
            tailF[b.name] = tF
            angleF[b.name] = angle_ccw_xz(hF, tF)

        # Emit bones
        bones_out = []
        for b in all_bones:
            hF_b = headF[b.name]; tF_b = tailF[b.name]
            length_px = max(0.01, math.hypot(tF_b.x - hF_b.x, tF_b.z - hF_b.z) * SCALE)
            if b.parent is None:
                bones_out.append({
                    "name": b.name,
                    "length": round(length_px, 4),
                    "rotation": float(f"{ROOT_ROTATION_DEG:.4f}")
                })
            else:
                p = b.parent
                bx, by = basis_from_parent(headF[p.name], tailF[p.name])
                x_px, y_px = project_to_basis_px(headF[p.name], bx, by, headF[b.name], SCALE)
                rel_ccw = angleF[b.name] - angleF[p.name]
                rot_spine = normalize_deg(-rel_ccw)
                if not is_vertical_rel_deg(rel_ccw, VERTICAL_TOL_DEG):
                    rot_spine = -rot_spine
                d = {"name": b.name, "parent": p.name, "length": round(length_px, 4)}
                if abs(x_px) > 1e-6: d["x"] = round(x_px, 4)
                if abs(y_px) > 1e-6: d["y"] = round(y_px, 4)
                if abs(rot_spine) > 1e-6: d["rotation"] = round(rot_spine, 4)
                bones_out.append(d)

        bone_to_skel_idx = {bones_out[i]["name"]: i for i in range(len(bones_out))}
        deform_bones = [b for b in all_bones if getattr(b, "use_deform", True)]
        deform_names = {b.name for b in deform_bones}

        slots_out = []
        skin_atts = {}

        # ============== attachments ==============
        for o in meshes:
            # Slot bone: prefer deform context, tolerate root if needed
            slot_bone = pick_slot_bone_for_object(o, arm, bone_to_skel_idx, all_bones, deform_names)
            slot_name = o.name
            slots_out.append({"name": slot_name, "bone": slot_bone, "attachment": slot_name})

            me = o.data
            me.calc_loop_triangles()

            # Image / UV primitives
            info = first_image_and_uvmap_for_obj(o)
            if info:
                stem, src_path, uvmap_name, img_size = info
                img_w, img_h = img_size if img_size else (100, 100)
                rel = (rel_path_inside(oca_dir, Path(src_path)) if src_path else None)
                if not rel and src_path and COPY_IF_MISSING:
                    rel = copy_into_textures(oca_dir, src_path)
                if rel:
                    from pathlib import Path as _P
                    rp = _P(str(rel)); parts = list(rp.parts)
                    if parts and parts[0] == "textures": parts = parts[1:]
                    region_path = _P(*parts).with_suffix("").as_posix()
                else:
                    region_path = stem
            else:
                img_w, img_h = 100, 100
                region_path = slot_name

            # UV layer
            if info and uvmap_name and (uvmap_name in me.uv_layers):
                uv_layer = me.uv_layers[uvmap_name]
            else:
                uv_layer = me.uv_layers.active
                if uv_layer is None and len(me.uv_layers) == 0:
                    uv_layer = me.uv_layers.new(name="UVMap")
                    me.calc_loop_triangles()

            # ---- mesh prep (meshutils) → NEW logical space
            rot_deg = MESH_ROTATE_DEG_BY_OBJECT.get(o.name, MESH_ROTATE_DEG_DEFAULT)
            region_xy, uvs, new_to_src, triangles, hull_idx = prepare_region(
                me=me, uv_layer=uv_layer, img_w=img_w, img_h=img_h,
                rotate_fn=rotate2d, rotate_deg=(rot_deg or 0.0)
            )

            # Decide weighted/unweighted up-front
            weighted_flag = mesh_has_deform_weights(o, deform_names, WEIGHT_EPS)

            # Attachment center (slot placement)
            vsum = Vector((0.0, 0.0, 0.0)); cnt = 0
            for v in me.vertices:
                vsum += o.matrix_world @ v.co
                cnt += 1
            center_world = (vsum / cnt) if cnt > 0 else (o.matrix_world @ Vector((0.0, 0.0, 0.0)))
            center_frame = (toFrame @ center_world.to_4d()).xyz

            # Slot basis for projection
            bx_slot, by_slot = basis_from_parent(headF[slot_bone], tailF[slot_bone])
            att_lx_slot, att_ly_slot = project_to_basis_px(headF[slot_bone], bx_slot, by_slot, center_frame, SCALE)

            # Weight fallback bone
            fb = first_nonroot_deform_child(all_bones, deform_names) or slot_bone

            # Build vertices stream (weighted vs unweighted)
            if weighted_flag:
                vgroups_for_vert = build_vertex_influences(
                    obj=o,
                    deform_names=deform_names,
                    weight_eps=WEIGHT_EPS,
                    max_influences=MAX_INFLUENCES,
                    fallback_bone=fb
                )

                vertices_seq = []
                for new_i, (src_vi, _src_loop) in enumerate(new_to_src):
                    vx_reg, vy_reg = region_xy[new_i]
                    lx_slot = vx_reg + att_lx_slot
                    ly_slot = vy_reg + att_ly_slot

                    dx_bu = (bx_slot[0] * (lx_slot / SCALE) + by_slot[0] * (ly_slot / SCALE))
                    dz_bu = (bx_slot[1] * (lx_slot / SCALE) + by_slot[1] * (ly_slot / SCALE))
                    pF = Vector((headF[slot_bone].x + dx_bu, 0.0, headF[slot_bone].z + dz_bu))

                    infl = vgroups_for_vert.get(src_vi) or [(fb, 1.0)]

                    vertices_seq.append(len(infl))
                    for bn, w in infl:
                        if bn not in bone_to_skel_idx:
                            bn = fb
                        bx_b, by_b = basis_from_parent(headF[bn], tailF[bn])
                        lx_px, ly_px = project_to_basis_px(headF[bn], bx_b, by_b, pF, SCALE)
                        vertices_seq.extend([
                            int(bone_to_skel_idx[bn]),
                            round(lx_px, 4), round(ly_px, 4),
                            round(float(w), 6)
                        ])
            else:
                # Unweighted: plain (x, y) stream in attachment space
                vertices_seq = []
                for (x, y) in region_xy:
                    vertices_seq.extend([round(float(x), 4), round(float(y), 4)])

            # ---------------- Attachment block ----------------
            att = {
                "type": "mesh",
                "name": slot_name,
                "path": region_path,
                "x": 0, "y": 0,
                "uvs": uvs,
                "triangles": [int(t) for t in triangles],
                "vertices": vertices_seq
            }

            # ---- Edges (export all mesh edges → preserve quads/n-gons visually)
            edges_for_spine = []
            if EMIT_NONESSENTIAL and EDGES_MODE != "off":
                raw_edges_vert = build_edges_from_tris_and_manual(
                    triangles=triangles,
                    new_to_src=new_to_src,
                    region_xy=region_xy,
                    me_edges=me.edges,
                    mode="all",             # take every Blender edge in NEW vertex space
                    use_seams=True,         # harmless in "all"
                    use_sharp=True,         # harmless in "all"
                    include_boundary=False  # avoid duplicating boundary from tris
                )

                vertex_count = len(region_xy)
                clean_vert = sanitize_vert_edges(raw_edges_vert, vertex_count)

                # Fallback: perimeter from hull if nothing survived
                if not clean_vert and hull_idx and len(hull_idx) >= 3:
                    clean_vert = edges_perimeter_from_hull(hull_idx)

                if clean_vert:
                    # Encode to even indices into [x,y,...] stream (Spine requirement)
                    edges_for_spine = encode_spine_edges(
                        keep_edges_new=clean_vert,
                        vertex_count=vertex_count
                    )

                # Attach nonessential data
                att["hull"] = int(max(3, len(hull_idx))) if hull_idx else int(len(region_xy))
                if edges_for_spine and (len(edges_for_spine) % 2 == 0):
                    att["edges"] = edges_for_spine

            # Validate edges formatting
            try:
                validate_attachment_edges(att)
                log(f"[OK] {slot_name}: weighted={weighted_flag} v={len(uvs)//2} "
                    f"vertsLen={len(att['vertices'])} edgesLen={len(att.get('edges',[]))}")
            except RuntimeError as ve:
                log(f"[EdgeValidation][ERROR] '{slot_name}': {ve}")

            skin_atts.setdefault(slot_name, {})[slot_name] = att

        # Animations (optional)
        animations_out = {}
        if build_animations:
            animations_out = build_animations(
                arm=arm, SCALE=SCALE, toFrame=toFrame,
                headF=headF, tailF=tailF, angleF=angleF,
                basis_from_parent=basis_from_parent,
                project_to_basis_px=project_to_basis_px,
            ) or {}

        spine_doc = {
            "skeleton": {
                "hash": "",
                "spine": SPINE_VERSION,
                "x": 0, "y": 0, "width": 0, "height": 0,
                "images": "./textures/"
            },
            "bones": bones_out,
            "slots": slots_out,
            "skins": [{"name": "default", "attachments": skin_atts}],
            "animations": animations_out
        }

        out_json.write_text(json.dumps(spine_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"[SpineExport] Wrote: {out_json}")

    except Exception as ex:
        tb = traceback.format_exc()
        log("[FATAL] " + str(ex))
        log(tb)
        out_log.write_text("\n".join(LOG), encoding="utf-8")
        raise

    out_log.write_text("\n".join(LOG), encoding="utf-8")
    print(f"[SpineExport] Log written: {out_log}")
    return str(out_json)

__all__ = ["export_now"]
