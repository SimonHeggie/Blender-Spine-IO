# SPINE_Output_animation.py
#
# Exports Blender F-Curves (no baking) to Spine JSON "animations".
# - Preserves Bezier shapes using Blender keyframe handles.
# - Matches Spine 4.3 curve encoding used by Spine sample files:
#   * Rotate (single channel): 4-number curve [cx1, cy1, cx2, cy2]
#   * Translate/Scale (two channels): 8-number curve [cx1x, cy1x, cx2x, cy2x, cx1y, cy1y, cx2y, cy2y]
#   All control points are in ABSOLUTE SECONDS (X) and VALUE UNITS (Y).
#
# Supported: bone translate(x,y), rotate, scale(x,y)
# Source: arm.animation_data.action

import bpy
import math

AXIS_MAP = {
    # TRANSLATE: use Blender X -> Spine x, Blender Y -> Spine y  (was Y=2/Z; now Y=1)
    "loc_to_spine": {"x_index": 1, "y_index": 0},

    # ROTATE: leave as you had it (Euler Z)
    "rot_euler_index": 2,

    # SCALE: unchanged (X/Z -> Spine x/y). Adjust if your Y scale should drive Spine y.
    "scale_to_spine": {"x_index": 0, "y_index": 2},

    "per_bone_rot_euler_index": {}
}

# --- helpers ---------------------------------------------------------------

def _scene_seconds(frame: float, fps: float) -> float:
    return float(frame) / float(fps) if fps > 0 else float(frame)

def _knot_map_by_frame(fc):
    return {int(round(k.co.x)): k for k in fc.keyframe_points}

def _handles_to_curve_abs_seconds(k_i, k_j, fps, value_xform):
    if k_i is None or k_j is None:
        return None
    if (k_i.interpolation != 'BEZIER') or (k_j.interpolation != 'BEZIER'):
        return None

    ti_f, vi = k_i.co
    tj_f, vj = k_j.co
    hi_t_f, hi_v = k_i.handle_right
    hj_t_f, hj_v = k_j.handle_left

    if abs(tj_f - ti_f) <= 1e-12:
        return None  # zero-length time

    def vx(v):
        return float(value_xform(v)) if value_xform else float(v)

    ti = _scene_seconds(ti_f, fps)
    tj = _scene_seconds(tj_f, fps)
    cx1 = _scene_seconds(hi_t_f, fps)
    cy1 = vx(hi_v)
    cx2 = _scene_seconds(hj_t_f, fps)
    cy2 = vx(hj_v)

    if abs(vj - vi) <= 1e-12:
        return None

    return [round(cx1, 6), round(cy1, 6), round(cx2, 6), round(cy2, 6)]

def _emit_channel_timelines(fcurves, to_seconds, value_xform_per_channel):
    if not fcurves:
        return [], False

    fps = bpy.context.scene.render.fps
    frames = set()
    for fc in fcurves:
        for kp in fc.keyframe_points:
            frames.add(int(round(kp.co.x)))
    frames = sorted(frames)
    if not frames:
        return [], False

    knotmaps = [_knot_map_by_frame(fc) for fc in fcurves]

    keys = []
    stepped_only = True

    for idx, f in enumerate(frames):
        t_sec = to_seconds(f)
        vals = []
        for ch, fc in enumerate(fcurves):
            v = fc.evaluate(f)
            xform = value_xform_per_channel[ch] if (value_xform_per_channel and ch < len(value_xform_per_channel)) else (lambda x: x)
            vals.append(xform(v))

        rec = {"time": round(t_sec, 6), "_vals": [float(v) for v in vals]}

        if idx < len(frames) - 1:
            nxt = frames[idx + 1]
            k_i_ref = knotmaps[0].get(f)
            k_j_ref = knotmaps[0].get(nxt)
            if k_i_ref and k_i_ref.interpolation == 'CONSTANT':
                rec["curve"] = "stepped"
            else:
                if len(fcurves) == 1:
                    c = _handles_to_curve_abs_seconds(
                        k_i_ref, k_j_ref, fps,
                        value_xform_per_channel[0] if value_xform_per_channel else None
                    )
                    if c:
                        rec["curve"] = c
                        stepped_only = False
                else:
                    k_i_x = knotmaps[0].get(f);    k_j_x = knotmaps[0].get(nxt)
                    k_i_y = knotmaps[1].get(f);    k_j_y = knotmaps[1].get(nxt)
                    cx = _handles_to_curve_abs_seconds(
                        k_i_x, k_j_x, fps,
                        value_xform_per_channel[0] if value_xform_per_channel else None
                    )
                    cy = _handles_to_curve_abs_seconds(
                        k_i_y, k_j_y, fps,
                        value_xform_per_channel[1] if value_xform_per_channel else None
                    )
                    if cx and cy:
                        rec["curve"] = [*cx, *cy]
                        stepped_only = False
        keys.append(rec)

    return keys, stepped_only

def _pick_action(arm):
    return getattr(arm.animation_data, "action", None)

def _fcurves_for_path(action, data_path_prefix, index_whitelist=None):
    out = []
    if not action:
        return out
    for fc in action.fcurves:
        if not fc.data_path.startswith(data_path_prefix):
            continue
        if index_whitelist is not None and fc.array_index not in index_whitelist:
            continue
        out.append(fc)
    return out

def _bone_path(bone_name, prop):
    return f'pose.bones["{bone_name}"].{prop}'

# --- public API --------------------------------------------------------------

def build_animations(arm, SCALE, toFrame, headF, tailF, angleF, basis_from_parent, project_to_basis_px):
    anim = {}
    action = _pick_action(arm)
    if not action:
        return anim

    fps = bpy.context.scene.render.fps
    to_seconds = lambda f: _scene_seconds(f, fps)

    anim_name = action.name or "Action"
    anim_block = {}
    bones_block = {}

    per_bone_rot = AXIS_MAP.get("per_bone_rot_euler_index", {})

    for b in arm.data.bones:
        bname = b.name

        # ---------- TRANSLATE (x,y) from PoseBone.location X/Y (in px) ----------
        loc_path = _bone_path(bname, "location")
        loc_fc = _fcurves_for_path(action, loc_path, index_whitelist=[0, 1, 2])
        ix = AXIS_MAP["loc_to_spine"]["x_index"]  # 0
        iy = AXIS_MAP["loc_to_spine"]["y_index"]  # 1
        # map by index, not order
        by_idx = {fc.array_index: fc for fc in loc_fc}
        pair = []
        if ix in by_idx: pair.append(by_idx[ix])
        if iy in by_idx: pair.append(by_idx[iy])

        if len(pair) == 2:
            vx = lambda v: round(float(v) * float(SCALE), 4)
            vy = lambda v: round(float(v) * float(SCALE), 4)
            # keep channel order [X, Y] explicitly
            pair.sort(key=lambda fc: 0 if fc.array_index == ix else 1)
            keys, _ = _emit_channel_timelines(pair, to_seconds, [vx, vy])
            if keys:
                for k in keys:
                    x, y = k.pop("_vals")
                    # Always write both axes so Spine shows motion even if one stays 0
                    k["x"] = x
                    k["y"] = y
                bones_block.setdefault(bname, {})["translate"] = keys

        # ---------- ROTATE (leave unchanged) ----------
        rot_idx = per_bone_rot.get(bname, AXIS_MAP["rot_euler_index"])
        rot_path = _bone_path(bname, "rotation_euler")
        rot_fc = _fcurves_for_path(action, rot_path, index_whitelist=[rot_idx])
        if rot_fc:
            rxf = lambda v: round(math.degrees(float(v)), 4)
            keys, _ = _emit_channel_timelines(rot_fc, to_seconds, [rxf])
            if keys:
                wrote_any = False
                for k in keys:
                    (angle,) = k.pop("_vals")
                    # NOTE: keeping your field name usage as-is ("value") per your request not to touch rotation section
                    if abs(angle) > 1e-9:
                        k["value"] = angle
                        wrote_any = True
                if wrote_any:
                    bones_block.setdefault(bname, {})["rotate"] = keys

        # ---------- SCALE (x,y) from PoseBone.scale X/Z (unitless) ----------
        scl_path = _bone_path(bname, "scale")
        scl_fc = _fcurves_for_path(action, scl_path, index_whitelist=[0, 1, 2])
        sx_i = AXIS_MAP["scale_to_spine"]["x_index"]
        sy_i = AXIS_MAP["scale_to_spine"]["y_index"]
        by_idx_s = {fc.array_index: fc for fc in scl_fc}
        spair = []
        if sx_i in by_idx_s: spair.append(by_idx_s[sx_i])
        if sy_i in by_idx_s: spair.append(by_idx_s[sy_i])

        if len(spair) == 2:
            # keep channel order [scaleX(source index), scaleY(source index)]
            spair.sort(key=lambda fc: 0 if fc.array_index == sx_i else 1)
            idf = lambda v: round(float(v), 6)
            keys, _ = _emit_channel_timelines(spair, to_seconds, [idf, idf])
            if keys:
                for k in keys:
                    sx, sy = k.pop("_vals")
                    k["x"] = sx
                    k["y"] = sy
                bones_block.setdefault(bname, {})["scale"] = keys

    if bones_block:
        anim_block["bones"] = bones_block

    anim[anim_name] = anim_block
    return anim

__all__ = ["build_animations"]
