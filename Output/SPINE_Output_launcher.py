# SPINE_Output_launcher.py
# Fresh-load exporter submodules from Blender Text datablocks,
# make relative imports work via a fake package, and load in correct order.

import sys, types, hashlib, time
import bpy

# ---------------------------------------------------------------------------
# CRITICAL: Load dependencies BEFORE modules that import them.
# images_uv must precede rigspace because rigspace imports it.
# ---------------------------------------------------------------------------
ORDER = [
    ("SPINE_Output_config.py",     "SPINE_Output_config"),
    ("SPINE_Output_helpers.py",    "SPINE_Output_helpers"),
    ("SPINE_Output_images_uv.py",  "SPINE_Output_images_uv"),   # <- moved up
    ("SPINE_Output_meshutils.py",  "SPINE_Output_meshutils"),
    ("SPINE_Output_weights.py",    "SPINE_Output_weights"),
    ("SPINE_Output_rigspace.py",   "SPINE_Output_rigspace"),    # <- after images_uv
    # Optional:
    ("SPINE_Output_animation.py",  "SPINE_Output_animation"),
    ("SPINE_Output_exporter.py",   "SPINE_Output_exporter"),
]

# Set True to guarantee zero edges in JSON (clean import test).
FORCE_NO_EDGES = False

PKG_NAME = "SPINE_PKG"

def _sha12(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()[:12]

def _ensure_fake_package():
    pkg = sys.modules.get(PKG_NAME)
    if pkg is None:
        pkg = types.ModuleType(PKG_NAME)
        pkg.__file__ = f"<{PKG_NAME}>"
        pkg.__path__ = []        # mark package-like
        pkg.__package__ = PKG_NAME
        sys.modules[PKG_NAME] = pkg
    return pkg

def _register_dual(module_obj: types.ModuleType, short_name: str):
    top_name = short_name
    pkg_name = f"{PKG_NAME}.{short_name}"
    sys.modules[top_name] = module_obj
    sys.modules[pkg_name] = module_obj

def _load_text_as_module(text_name: str, short_name: str):
    txt = bpy.data.texts.get(text_name)
    if not txt:
        raise RuntimeError(f"Text datablock not found: {text_name}")

    src = txt.as_string()
    code = compile(src, f"<blender_text:{text_name}>", "exec")

    mod = types.ModuleType(short_name)
    mod.__file__ = f"<blender_text:{text_name}>"
    mod.__package__ = PKG_NAME
    mod.__dict__["bpy"] = bpy

    _ensure_fake_package()

    # Register the empty module under both names *before* exec so that
    # intra-package imports during exec resolve.
    _register_dual(mod, short_name)

    # Execute into the pre-registered module dict
    exec(code, mod.__dict__)

    print(f"[STAMP] {short_name:>22} chars={len(src):>6} sha1={_sha12(src.encode('utf-8'))} id={id(mod)}")
    return mod

def _maybe_load(text_name: str, short_name: str):
    if bpy.data.texts.get(text_name) is None:
        return None
    return _load_text_as_module(text_name, short_name)

def _bootstrap():
    print("\n=== SPINE OUTPUT BOOTSTRAP ===", time.ctime())

    # Purge any stale copies
    for _, mn in ORDER:
        for key in (mn, f"{PKG_NAME}.{mn}"):
            if key in sys.modules:
                del sys.modules[key]
    if PKG_NAME in sys.modules:
        del sys.modules[PKG_NAME]

    loaded = {}
    _ensure_fake_package()

    # Load in dependency-safe order above
    for tn, mn in ORDER:
        mod = _maybe_load(tn, mn)
        if mod:
            loaded[mn] = mod

    cfg = loaded.get("SPINE_Output_config")
    if cfg and FORCE_NO_EDGES:
        try:
            setattr(cfg, "EDGES_MODE", "off")
            setattr(cfg, "EMIT_NONESSENTIAL", False)
            print("[BOOT] Forced EDGES_MODE='off' and EMIT_NONESSENTIAL=False")
        except Exception as e:
            print(f"[BOOT] Could not force edges off: {e}")

    exp = loaded.get("SPINE_Output_exporter")
    if not exp:
        raise RuntimeError("SPINE_Output_exporter not loaded — check text names.")

    if cfg and hasattr(cfg, "SPINE_VERSION"):
        print(f"[BOOT] SPINE_VERSION: {cfg.SPINE_VERSION}")

    print("[BOOT] Calling export_now() ...")
    out = exp.export_now()
    print("[BOOT] DONE. Wrote:", out)

if __name__ == "__main__":
    _bootstrap()
else:
    _bootstrap()
