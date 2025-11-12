# SPINE_Output_meshutils.py
# Single source of truth for mesh/UV/tri/hull/edge logic used by the exporter.
# Responsibilities:
#   - Build region-space verts & UVs (NEW logical order keyed by (vi,uv)).
#   - Remap triangles to NEW order and enforce CCW winding.
#   - Center region space; hull-first reordering; triangle remap.
#   - Build internal edges from boundary and artist markings; sanitize.
#   - Encode edges for Spine (weighted: vertex indices; unweighted: even-stream) + validate JSON attachment.

from math import isfinite

# --- Config-driven defaults (no globals that break import) ---
try:
    from .SPINE_Output_config import EDGES_MODE as _CFG_EDGES_MODE
except Exception:
    _CFG_EDGES_MODE = "mixed"

def _default_edge_mode():
    """Return normalized default edge mode from config; safe fallback."""
    try:
        m = (str(_CFG_EDGES_MODE) or "mixed").strip().lower()
    except Exception:
        m = "mixed"
    # normalize to one of: boundary | manual | mixed | all
    if m not in ("boundary", "manual", "mixed", "all"):
        m = "mixed"
    return m


# ------------------------------
# Small geometry helpers
# ------------------------------

def tri_area2_xy(pts, a, b, c):
    """Twice the signed area (ccw positive) for triangle a-b-c in pts[(x,y)]."""
    ax, ay = pts[a]; bx, by = pts[b]; cx, cy = pts[c]
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

def convex_hull_indices_xy(pts):
    """Returns indices of the convex hull in CCW order using monotone chain."""
    n = len(pts)
    if n <= 3:
        return list(range(n))
    sorted_idx = sorted(range(n), key=lambda i: (pts[i][0], pts[i][1]))
    def cross(i, j, k):
        ax, ay = pts[i]; bx, by = pts[j]; cx, cy = pts[k]
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    lower = []
    for i in sorted_idx:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], i) <= 0:
            lower.pop()
        lower.append(i)
    upper = []
    for i in reversed(sorted_idx):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], i) <= 0:
            upper.pop()
        upper.append(i)
    hull = lower[:-1] + upper[:-1]
    # de-duplicate while preserving order
    seen = set(); out = []
    for i in hull:
        if i not in seen:
            out.append(i); seen.add(i)
    return out

# ------------------------------
# Edge building (NEW index space)
# ------------------------------

def _pairs_to_flat(edges_pairs):
    out = []
    for a, b in edges_pairs:
        out.extend([int(a), int(b)])
    return out

def _sanitize_pairs(flat_list, vcount):
    if not isinstance(flat_list, list):
        return []
    ints = []
    for x in flat_list:
        try:
            xi = int(x)
            if 0 <= xi < max(0, int(vcount)):
                ints.append(xi)
        except Exception:
            pass
    if len(ints) % 2:
        ints = ints[:-1]
    out = []; seen = set()
    for i in range(0, len(ints), 2):
        a, b = ints[i], ints[i+1]
        if a == b:
            continue
        p = (a, b) if a < b else (b, a)
        if p in seen:
            continue
        seen.add(p)
        out.extend([p[0], p[1]])
    return out

def _edge_counts_from_tris(tri_indices):
    """Count undirected edges frequency from triangles (NEW index space)."""
    counts = {}
    for i in range(0, len(tri_indices), 3):
        a = int(tri_indices[i]); b = int(tri_indices[i+1]); c = int(tri_indices[i+2])
        for e in ((a,b),(b,c),(c,a)):
            u, v = (e[0], e[1]) if e[0] < e[1] else (e[1], e[0])
            counts[(u,v)] = counts.get((u,v), 0) + 1
    return counts

def _tri_boundary_edges_from_tris(tri_indices):
    counts = _edge_counts_from_tris(tri_indices)
    return [e for (e,cnt) in counts.items() if cnt == 1]

def _tri_internal_edges_from_tris(tri_indices):
    counts = _edge_counts_from_tris(tri_indices)
    return [e for (e,cnt) in counts.items() if cnt == 2]

def _map_src_edge_to_new(src_edge, new_to_src):
    """Map a Blender edge (src vertex indices) to the FIRST matching NEW indices.
       Good for marked seams/sharp; UV-split duplicates may map to first occurrence."""
    src_i, src_j = int(src_edge[0]), int(src_edge[1])
    first_i = None; first_j = None
    for new_idx, (svi, _loopi) in enumerate(new_to_src):
        if first_i is None and int(svi) == src_i:
            first_i = new_idx
            if first_j is not None: break
        if first_j is None and int(svi) == src_j:
            first_j = new_idx
            if first_i is not None: break
    if first_i is None or first_j is None or first_i == first_j:
        return None
    return (first_i, first_j) if first_i < first_j else (first_j, first_i)

def build_edges_from_tris_and_manual(
    triangles,
    new_to_src,
    region_xy,
    me_edges=None,
    mode=None,
    use_seams=True,
    use_sharp=True,
    include_boundary=True
):
    """
    Return flat [a,b,...] edges in NEW vertex index space.

    mode:
      - "boundary": only triangle boundary
      - "manual"  : only artist-marked edges (seam/sharp) from Blender
      - "mixed"   : boundary + artist-marked   (default)
      - "all"     : every Blender mesh edge mapped into NEW order

    include_boundary:
      - If True, add triangle boundary edges.
      - If False, suppress boundary (useful when you want only quads/n-gons from Blender).
    """
    # Resolve default from config at call-time (no import-time globals).
    m = (mode or _default_edge_mode()).strip().lower()
    vcount = len(region_xy) if region_xy is not None else (len(new_to_src) if new_to_src else 0)

    # Fast path: boundary-only
    edge_pairs = set()
    if m in ("boundary", "mixed", "all") and include_boundary and triangles:
        for a, b in _tri_boundary_edges_from_tris(triangles):
            if 0 <= a < vcount and 0 <= b < vcount and a != b:
                u, v = (a, b) if a < b else (b, a)
                edge_pairs.add((u, v))

    # Artist/manual edges or "all" mesh edges
    if me_edges is not None and m in ("manual", "mixed", "all"):
        for e in me_edges:
            try:
                sv0, sv1 = int(e.vertices[0]), int(e.vertices[1])
            except Exception:
                continue

            # In "all", we take every mesh edge; otherwise gate by seam/sharp.
            if m != "all":
                allowed = False
                if use_seams and getattr(e, "use_seam", False):
                    allowed = True
                if use_sharp and getattr(e, "use_edge_sharp", False):
                    allowed = True
                if not allowed:
                    continue

            mapped = _map_src_edge_to_new((sv0, sv1), new_to_src)
            if not mapped:
                continue
            u, v = (mapped if mapped[0] < mapped[1] else (mapped[1], mapped[0]))
            if 0 <= u < vcount and 0 <= v < vcount and u != v:
                edge_pairs.add((u, v))

    # Sanitize and return flat list
    flat = _pairs_to_flat(sorted(edge_pairs))
    return _sanitize_pairs(flat, vcount)


def sanitize_edges(edges_flat, vcount):
    return _sanitize_pairs(edges_flat, vcount)

# ------------------------------
# Region/UV/tri helpers (NEW)
# ------------------------------

def build_region_vertices_uvs(me, uv_layer, img_w, img_h):
    """
    Returns (uvs, region_xy, new_to_src, loopkey_to_new)
    NEW order is keyed by (vertex_index, uv) uniqueness.
    """
    uvs = []; loopkey_to_new = {}; new_to_src = []; region_xy = []
    cx = img_w * 0.5; cy = img_h * 0.5
    uv_data_len = len(uv_layer.data) if uv_layer else 0
    for li, loop in enumerate(me.loops):
        vi = loop.vertex_index
        if uv_layer and li < uv_data_len:
            uv = uv_layer.data[li].uv
            u = float(uv.x); v = float(1.0 - uv.y)
        else:
            u = 0.0; v = 1.0
        key = (vi, round(u, 6), round(v, 6))
        if key not in loopkey_to_new:
            loopkey_to_new[key] = len(loopkey_to_new)
            new_to_src.append((vi, li))
            uvs.extend([round(u, 6), round(v, 6)])
            vx = u * img_w - cx
            vy = cy - v * img_h
            region_xy.append((float(vx), float(vy)))
    return uvs, region_xy, new_to_src, loopkey_to_new

def remap_triangles_to_new(me, uv_layer, loopkey_to_new):
    uv_data_len = len(uv_layer.data) if uv_layer else 0
    def uv_key(v_index, l_index):
        if uv_layer and l_index < uv_data_len:
            uv = uv_layer.data[l_index].uv
            return (v_index, round(uv.x, 6), round(1.0 - uv.y, 6))
        else:
            return (v_index, 0.0, 1.0)
    triangles = []
    for lt in me.loop_triangles:
        l0, l1, l2 = lt.loops[0], lt.loops[1], lt.loops[2]
        k0 = uv_key(lt.vertices[0], l0)
        k1 = uv_key(lt.vertices[1], l1)
        k2 = uv_key(lt.vertices[2], l2)
        if k0 in loopkey_to_new and k1 in loopkey_to_new and k2 in loopkey_to_new:
            a = loopkey_to_new[k0]; b = loopkey_to_new[k1]; c = loopkey_to_new[k2]
            triangles.extend([a, b, c])
    return triangles

def center_region_xy(region_xy):
    cx = sum(x for x,_ in region_xy) / len(region_xy)
    cy = sum(y for _,y in region_xy) / len(region_xy)
    return [(x - cx, y - cy) for (x,y) in region_xy], cx, cy

def hull_first_reorder(region_xy_centered, uvs, new_to_src, triangles):
    """Hull-first reorder; remap triangles; enforce CCW winding; return (region_xy2, uvs2, new_to_src2, triangles2, hull_idx)."""
    if len(region_xy_centered) >= 3:
        hull_idx = convex_hull_indices_xy(region_xy_centered)
    else:
        hull_idx = list(range(len(region_xy_centered)))
    hull_set = set(hull_idx)
    tail_idx = [i for i in range(len(region_xy_centered)) if i not in hull_set]
    new_order = list(hull_idx) + tail_idx

    old_to_new = {old: i for i, old in enumerate(new_order)}
    region_xy2 = [region_xy_centered[old] for old in new_order]
    uvs_pairs = [(uvs[i*2], uvs[i*2+1]) for i in range(len(uvs)//2)]
    uvs_pairs = [uvs_pairs[old] for old in new_order]
    uvs2 = [x for pair in uvs_pairs for x in pair]
    new_to_src2 = [new_to_src[old] for old in new_order]

    remapped_tris = []
    for i in range(0, len(triangles), 3):
        a, b, c = triangles[i], triangles[i+1], triangles[i+2]
        a2, b2, c2 = old_to_new[a], old_to_new[b], old_to_new[c]
        area2 = tri_area2_xy(region_xy2, a2, b2, c2)
        if abs(area2) < 1e-9:
            continue
        if area2 < 0:
            b2, c2 = c2, b2
        remapped_tris.extend([a2, b2, c2])
    if not remapped_tris and len(region_xy2) >= 3:
        for i in range(1, len(region_xy2) - 1):
            remapped_tris.extend([0, i, i+1])
    return region_xy2, uvs2, new_to_src2, remapped_tris, hull_idx

# ------------------------------
# Spine serialization helpers
# ------------------------------

def is_spine_weighted(vertices_seq, uvs):
    """Weighted iff len(vertices_seq) != len(uvs)."""
    return len(vertices_seq) != len(uvs)

def edges_vertices_to_even_stream(edges_vert, vertex_count):
    """Vertex indices -> EVEN positions in the [x,y,...] stream (ALWAYS for Spine)."""
    if not edges_vert:
        return []
    out = []
    max_stream = max(0, (int(vertex_count)*2) - 1)
    for k in range(0, len(edges_vert), 2):
        a = int(edges_vert[k]) * 2
        b = int(edges_vert[k+1]) * 2
        if 0 <= a <= max_stream and 0 <= b <= max_stream and a != b:
            out.extend([a, b])
    return out

def edges_perimeter_from_hull(hull_idx):
    if not hull_idx or len(hull_idx) < 3:
        return []
    e = []
    n = len(hull_idx)
    for i in range(n):
        a = int(hull_idx[i]); b = int(hull_idx[(i+1) % n])
        e.extend([a, b])
    return e

def sanitize_vert_edges(edges_vert, vcount):
    if not edges_vert:
        return []
    ints = []
    for x in edges_vert:
        try:
            xi = int(x)
            if 0 <= xi < max(0, int(vcount)):
                ints.append(xi)
        except Exception:
            pass
    if len(ints) % 2:
        ints = ints[:-1]
    out, seen = [], set()
    for i in range(0, len(ints), 2):
        a, b = ints[i], ints[i+1]
        if a == b: 
            continue
        p = (a, b) if a < b else (b, a)
        if p in seen:
            continue
        seen.add(p)
        out.extend([p[0], p[1]])
    return out

def encode_spine_edges(keep_edges_new, vertex_count):
    """
    Convert NEW logical vertex pairs → Spine 'edges' array that indexes the
    [x,y,...] coordinate stream (ALWAYS even indices), for BOTH unweighted and
    weighted meshes. Spine 4.2+ tolerates/uses this consistently.
    """
    clean = _sanitize_pairs(list(keep_edges_new or []), vertex_count)
    if not clean:
        return []

    out = []
    max_stream = (int(vertex_count) * 2) - 1
    for i in range(0, len(clean), 2):
        a = int(clean[i]) * 2
        b = int(clean[i + 1]) * 2
        if 0 <= a <= max_stream and 0 <= b <= max_stream and a != b:
            out.extend((a, b))
    return out


def validate_attachment_edges(att):
    """
    Enforce Spine's edges indexing into the [x,y,...] stream (even-only),
    range 0..(2*v-1). We apply this uniformly to weighted and unweighted.
    """
    e = att.get("edges")
    if not e or not isinstance(e, list):
        return
    vcount = (len(att.get("uvs", [])) // 2)
    if vcount <= 0:
        raise RuntimeError(f"Attachment '{att.get('name','?')}' has edges but no UVs/verts.")

    max_stream = (vcount * 2) - 1
    for idx in e:
        ii = int(idx)
        if (ii & 1) != 0:
            raise RuntimeError(
                f"Attachment '{att.get('name','?')}' has ODD edge index {ii}; must be even"
            )
        if ii < 0 or ii > max_stream:
            raise RuntimeError(
                f"Attachment '{att.get('name','?')}' edge index out of range: {ii} (max={max_stream})"
            )

# ------------------------------
# Convenience: one-shot region prep
# ------------------------------

def prepare_region(me, uv_layer, img_w, img_h, rotate_fn=None, rotate_deg=0.0):
    """
    Returns tuple:
      region_xy_centered, uvs, new_to_src, triangles, hull_idx
    """
    uvs, region_xy, new_to_src, loopkey_to_new = build_region_vertices_uvs(me, uv_layer, img_w, img_h)
    if rotate_fn and abs(rotate_deg) > 1e-9:
        region_xy = [rotate_fn(x, y, rotate_deg) for (x, y) in region_xy]
    triangles = remap_triangles_to_new(me, uv_layer, loopkey_to_new)
    region_xy_centered, _, _ = center_region_xy(region_xy)
    region_xy2, uvs2, new_to_src2, triangles2, hull_idx = hull_first_reorder(
        region_xy_centered, uvs, new_to_src, triangles
    )
    return region_xy2, uvs2, new_to_src2, triangles2, hull_idx

__all__ = [
    "tri_area2_xy","convex_hull_indices_xy",
    "build_edges_from_tris_and_manual","sanitize_edges",
    "build_region_vertices_uvs","remap_triangles_to_new","center_region_xy","hull_first_reorder",
    "is_spine_weighted","edges_vertices_to_even_stream","edges_perimeter_from_hull","sanitize_vert_edges",
    "encode_spine_edges","validate_attachment_edges","prepare_region"
]