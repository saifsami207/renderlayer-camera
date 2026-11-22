"""RenderLayer Camera — Blender 5.3+ Extension.

Assign a dedicated camera to every View Layer and batch-render all enabled
layers directly into a chosen output folder with each layer's name in the
output image (plus optional burn-in image stamp, non-blocking Render window
queue, and native multi-part Multilayer EXR merging).

Author: Saif Sami
Inspired by BB-Camera-Layer (https://github.com/riouxr/BB-Camera-Layer).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import bpy
from bpy.props import PointerProperty

from . import core, operators, properties, queue, ui

# ---------------------------------------------------------------------------
# Runtime state (IDs are ALWAYS referenced by name, never raw bpy.types.ID!)
# ---------------------------------------------------------------------------

_sync_batch_rendering: bool = False
_saved_camera_names: Dict[str, Optional[str]] = {}
_keymaps: List[Tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []
_msgbus_owner = object()


def set_batch_rendering(active: bool) -> None:
    global _sync_batch_rendering
    _sync_batch_rendering = active


def is_batch_rendering() -> bool:
    """Return True if either a synchronous or asynchronous batch render is active."""
    return _sync_batch_rendering or queue.get_queue().is_active()


# ---------------------------------------------------------------------------
# Keymap (F12 intercept)
# ---------------------------------------------------------------------------


def _register_keymap() -> None:
    if _keymaps:
        return
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    kc = wm.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new(name="Window", space_type="EMPTY")
    kmi = km.keymap_items.new("rlc.render_all_layers", type="F12", value="PRESS")
    kmi.properties.animation = False
    kmi.properties.force_separate_files = False
    _keymaps.append((km, kmi))


def _unregister_keymap() -> None:
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()


def sync_keymap_from_scene(scene: Optional[bpy.types.Scene]) -> None:
    """Align the F12 keymap override with the given scene's intercept_f12 toggle."""
    if scene is not None and getattr(scene, "rlc_settings", None) is not None:
        if scene.rlc_settings.intercept_f12:
            _register_keymap()
            return
    _unregister_keymap()


# ---------------------------------------------------------------------------
# Msgbus: Auto-switch viewport camera when active View Layer changes
# ---------------------------------------------------------------------------


def _on_view_layer_changed() -> None:
    if is_batch_rendering():
        return
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        scene = window.scene
        vl = window.view_layer
        if scene is None or vl is None:
            continue
        scene_cfg = getattr(scene, "rlc_settings", None)
        if scene_cfg is None or not scene_cfg.auto_switch_viewport_camera:
            continue
        vl_cfg = getattr(vl, "rlc_settings", None)
        if vl_cfg is not None and vl_cfg.camera is not None and vl_cfg.camera.type == "CAMERA":
            if scene.camera != vl_cfg.camera:
                scene.camera = vl_cfg.camera


def _subscribe_msgbus() -> None:
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.Window, "view_layer"),
        owner=_msgbus_owner,
        args=(),
        notify=_on_view_layer_changed,
    )


# ---------------------------------------------------------------------------
# Persistent Render & File-Load Handlers
# ---------------------------------------------------------------------------


def _find_active_view_layer(scene: bpy.types.Scene) -> Optional[bpy.types.ViewLayer]:
    win = core.find_window_for_scene(scene.name)
    if win is not None and win.scene == scene and win.view_layer is not None:
        return win.view_layer
    return scene.view_layers[0] if len(scene.view_layers) > 0 else None


@bpy.app.handlers.persistent
def _on_render_pre(scene: bpy.types.Scene, depsgraph=None) -> None:
    """For normal single-layer F12 renders, swap to the active View Layer's camera."""
    if is_batch_rendering():
        return
    scene_cfg = getattr(scene, "rlc_settings", None)
    if scene_cfg is None:
        return
    active_vl = _find_active_view_layer(scene)
    if active_vl is None:
        return
    vl_cfg = getattr(active_vl, "rlc_settings", None)
    cam = vl_cfg.camera if vl_cfg is not None else None
    if cam is not None and cam.type == "CAMERA" and cam.name in bpy.data.objects:
        _saved_camera_names[scene.name] = scene.camera.name if scene.camera else None
        scene.camera = cam


def _restore_single_layer_camera(scene: Optional[bpy.types.Scene]) -> None:
    if scene is None or scene.name not in _saved_camera_names:
        return
    orig_name = _saved_camera_names.pop(scene.name)
    scene.camera = bpy.data.objects.get(orig_name) if orig_name else None


@bpy.app.handlers.persistent
def _on_render_post(scene: bpy.types.Scene, depsgraph=None) -> None:
    """Restore original scene camera after a single-layer F12 render frame finishes."""
    _restore_single_layer_camera(scene)


@bpy.app.handlers.persistent
def _on_render_complete(scene: bpy.types.Scene, depsgraph=None) -> None:
    """Advance the asynchronous batch render queue when a layer render completes."""
    queue.get_queue().on_render_complete(scene)


@bpy.app.handlers.persistent
def _on_render_cancel(scene: bpy.types.Scene, depsgraph=None) -> None:
    """Restore camera on single-layer cancel or halt & restore the async batch queue."""
    _restore_single_layer_camera(scene)
    queue.get_queue().on_render_cancel(scene)


@bpy.app.handlers.persistent
def _on_load_pre(_filepath=None) -> None:
    """Abort any active batch render queue cleanly before loading a new .blend file."""
    queue.get_queue().abort_and_cleanup(restore_state=False)
    _saved_camera_names.clear()


@bpy.app.handlers.persistent
def _on_load_post(_filepath=None) -> None:
    _saved_camera_names.clear()
    _subscribe_msgbus()
    scene = getattr(bpy.context, "scene", None)
    if scene is None and len(bpy.data.scenes) > 0:
        scene = bpy.data.scenes[0]
    sync_keymap_from_scene(scene)


def _deferred_init() -> Optional[float]:
    """Finish msgbus & keymap sync once Blender's full context is available."""
    try:
        _subscribe_msgbus()
        scene = getattr(bpy.context, "scene", None)
        if scene is not None:
            sync_keymap_from_scene(scene)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_CLASSES = (
    *properties.CLASSES,
    *operators.CLASSES,
    *ui.CLASSES,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.ViewLayer.rlc_settings = PointerProperty(type=properties.RLC_PG_view_layer_settings)
    bpy.types.Scene.rlc_settings = PointerProperty(type=properties.RLC_PG_scene_settings)

    bpy.types.TOPBAR_MT_render.append(ui.draw_topbar_render_menu)

    bpy.app.handlers.render_pre.append(_on_render_pre)
    bpy.app.handlers.render_post.append(_on_render_post)
    bpy.app.handlers.render_complete.append(_on_render_complete)
    bpy.app.handlers.render_cancel.append(_on_render_cancel)
    bpy.app.handlers.load_pre.append(_on_load_pre)
    bpy.app.handlers.load_post.append(_on_load_post)

    # Defer msgbus/keymap setup safely so register() never touches _RestrictContext
    bpy.app.timers.register(_deferred_init, first_interval=0.1)


def unregister() -> None:
    queue.get_queue().abort_and_cleanup(restore_state=True)

    if bpy.app.timers.is_registered(_deferred_init):
        bpy.app.timers.unregister(_deferred_init)

    bpy.msgbus.clear_by_owner(_msgbus_owner)
    _unregister_keymap()
    _saved_camera_names.clear()
    set_batch_rendering(False)

    for handler, hlist in (
        (_on_render_pre, bpy.app.handlers.render_pre),
        (_on_render_post, bpy.app.handlers.render_post),
        (_on_render_complete, bpy.app.handlers.render_complete),
        (_on_render_cancel, bpy.app.handlers.render_cancel),
        (_on_load_pre, bpy.app.handlers.load_pre),
        (_on_load_post, bpy.app.handlers.load_post),
    ):
        if handler in hlist:
            hlist.remove(handler)

    try:
        bpy.types.TOPBAR_MT_render.remove(ui.draw_topbar_render_menu)
    except Exception:
        pass

    if hasattr(bpy.types.Scene, "rlc_settings"):
        del bpy.types.Scene.rlc_settings
    if hasattr(bpy.types.ViewLayer, "rlc_settings"):
        del bpy.types.ViewLayer.rlc_settings

    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
