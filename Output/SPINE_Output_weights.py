# SPINE_Output_weights.py
# Weight helpers for Blender → Spine
# - Decide if a mesh is truly weighted (has usable deform weights)
# - Choose fallback bones
# - Build per-vertex influences for weighted export
#
# NOTE:
#   "Weighted" here means: at least one vertex has a deform bone group with
#   weight > WEIGHT_EPS. If no such weights exist, we export UNWEIGHTED
#   vertices as [x, y, ...] pairs (Spine's unweighted encoding).

def mesh_has_deform_weights(obj, deform_names, weight_eps: float) -> bool:
    """True if any vertex in obj has a deform group with weight > eps."""
    if not getattr(obj, "vertex_groups", None):
        return False
    vg_index_to_name = {i: vg.name for i, vg in enumerate(obj.vertex_groups)}
    me = obj.data
    for v in me.vertices:
        for g in v.groups:
            vg_name = vg_index_to_name.get(g.group)
            if vg_name in deform_names and g.weight > weight_eps:
                return True
    return False

def dominant_deform_vg_on_mesh(obj, deform_names):
    """Existing helper (unchanged): which deform VG appears most across verts."""
    counts = {}
    me = obj.data
    vg_index_to_name = {i: vg.name for i, vg in enumerate(obj.vertex_groups)}
    for v in me.vertices:
        for g in v.groups:
            vg = vg_index_to_name.get(g.group)
            if vg in deform_names and g.weight > 0.0:
                counts[vg] = counts.get(vg, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]

def first_nonroot_deform_child(all_bones, deform_names):
    """Existing helper (unchanged): a decent deform fallback bone."""
    tops = [b for b in all_bones if b.parent is None]
    roots = {b.name for b in tops}
    for b in all_bones:
        if b.name in deform_names and b.parent and b.parent.name in roots:
            return b.name
    for b in all_bones:
        if b.name in deform_names and b.parent is not None:
            return b.name
    for b in all_bones:
        if b.name in deform_names:
            return b.name
    return None

def pick_slot_bone_for_object(obj, arm, bone_to_skel_idx, all_bones, deform_names):
    """
    Pick a reasonable slot bone for 'obj':
      1) If parented directly to the Armature object (no bone), use root-ish.
      2) If it has a dominant deform VG, prefer that.
      3) Else pick "first non-root deform child" (or last resort: first root).
    """
    # 1) Parent rule
    if arm and getattr(obj, "parent", None) is arm and not getattr(obj, "parent_bone", ""):
        tops = [b for b in all_bones if b.parent is None]
        for tb in tops:
            if tb.name.lower() == "root":
                return tb.name
        return (tops[0].name if tops else all_bones[0].name)

    # 2) Dominant VG
    dom = dominant_deform_vg_on_mesh(obj, deform_names)
    if dom and dom in bone_to_skel_idx:
        return dom

    # 3) Fallback deform
    fb = first_nonroot_deform_child(all_bones, deform_names)
    if fb:
        return fb

    # 4) Last resort
    tops = [b for b in all_bones if b.parent is None]
    for tb in tops:
        if tb.name.lower() == "root":
            return tb.name
    return (tops[0].name if tops else all_bones[0].name)

def build_vertex_influences(obj, deform_names, weight_eps: float, max_influences: int, fallback_bone: str):
    """
    Build influences for WEIGHTED export:
      returns dict: src_vert_index -> [(bone_name, normalized_weight), ...]
    """
    me = obj.data
    vg_index_to_name = {i: vg.name for i, vg in enumerate(obj.vertex_groups)}
    out = {}

    for v in me.vertices:
        infl_raw = []
        for g in v.groups:
            vg_name = vg_index_to_name.get(g.group)
            if vg_name and vg_name in deform_names and g.weight > 0.0:
                infl_raw.append((vg_name, float(g.weight)))
        # prune and normalize
        infl_raw = [(bn, w) for (bn, w) in infl_raw if w > weight_eps]
        infl_raw.sort(key=lambda t: t[1], reverse=True)
        infl_raw = infl_raw[:max_influences]
        if infl_raw:
            s = sum(w for _, w in infl_raw)
            infl = [(bn, (w / s) if s > 0 else 0.0) for (bn, w) in infl_raw]
        else:
            infl = [(fallback_bone, 1.0)]
        out[v.index] = infl
    return out

__all__ = [
    "mesh_has_deform_weights",
    "dominant_deform_vg_on_mesh",
    "first_nonroot_deform_child",
    "pick_slot_bone_for_object",
    "build_vertex_influences",
]
