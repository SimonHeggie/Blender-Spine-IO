import bpy, shutil
from pathlib import Path
from .SPINE_Output_config import IMG_EXTS, COPY_IF_MISSING, PRESERVE_PARENT_DIRS

def first_image_and_uvmap_for_obj(obj):
    if not obj.material_slots: 
        return None
    mat = obj.material_slots[0].material if obj.material_slots[0].material else None
    if not mat or not mat.use_nodes: 
        return None
    for n in mat.node_tree.nodes:
        if n.type == 'TEX_IMAGE' and getattr(n,'image',None):
            img = n.image
            uvmap_name = None
            if n.inputs.get("Vector") and n.inputs["Vector"].links:
                src = n.inputs["Vector"].links[0].from_node
                if src.type == "UVMAP":
                    uvmap_name = getattr(src, "uv_map", None) or src.uv_map
            try:
                p = Path(bpy.path.abspath(img.filepath_raw or img.filepath))
                src_path = str(p.resolve()) if p.suffix.lower() in IMG_EXTS and p.exists() else None
            except:
                src_path = None
            stem = Path(img.name).stem
            size = None
            try:
                w, h = int(img.size[0]), int(img.size[1])
                if w > 0 and h > 0:
                    size = (w, h)
            except:
                pass
            return (stem, src_path, uvmap_name, size)
    return None

def rel_path_inside(base: Path, absfile: Path):
    try: 
        return absfile.relative_to(base)
    except Exception: 
        return None

def copy_into_textures(oca_dir: Path, src_path: str):
    if not src_path: 
        return None
    src = Path(src_path)
    parents=[]; p=src.parent
    for _ in range(PRESERVE_PARENT_DIRS):
        if p and p.name and p.name not in ("/","\\"):
            parents.insert(0,p.name); p=p.parent
        else: 
            break
    dst = oca_dir / "textures" / Path(*parents) / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not dst.exists(): 
            shutil.copy2(src, dst)
        return dst.relative_to(oca_dir)
    except Exception as e:
        print(f"[SpineExport] WARN copy failed: {src} -> {dst}: {e}")
        return None

__all__ = ["first_image_and_uvmap_for_obj","rel_path_inside","copy_into_textures"]
