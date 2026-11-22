"""Core utilities for RenderLayer Camera (Blender 5.3+).

Author: Saif Sami
Inspired by BB-Camera-Layer (https://github.com/riouxr/BB-Camera-Layer).
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

import bpy

# Characters illegal in filenames on Windows/macOS/Linux
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

_STAMP_BOOL_PROPS = (
    "use_stamp",
    "use_stamp_time",
    "use_stamp_date",
    "use_stamp_render_time",
    "use_stamp_frame",
    "use_stamp_frame_range",
    "use_stamp_memory",
    "use_stamp_hostname",
    "use_stamp_camera",
    "use_stamp_lens",
    "use_stamp_scene",
    "use_stamp_marker",
    "use_stamp_filename",
    "use_stamp_sequencer_strip",
    "use_stamp_note",
    "use_stamp_labels",
)

# Map Blender 5.3 image_settings.exr_codec strings to OpenEXR compression enums
_EXR_CODEC_MAP = {
    "NONE": "NO_COMPRESSION",
    "PXR24": "PXR24_COMPRESSION",
    "ZIP": "ZIP_COMPRESSION",
    "PIZ": "PIZ_COMPRESSION",
    "RLE": "RLE_COMPRESSION",
    "ZIPS": "ZIPS_COMPRESSION",
    "B44": "B44_COMPRESSION",
    "B44A": "B44A_COMPRESSION",
    "DWAA": "DWAA_COMPRESSION",
    "DWAB": "DWAB_COMPRESSION",
    "HTJ2K256": "HTJ2K256_COMPRESSION",
    "HTJ2K32": "HTJ2K32_COMPRESSION",
}


def sanitize_filename(name: str, fallback: str = "layer") -> str:
    """Return a filesystem-safe filename stem without path separators."""
    cleaned = _INVALID_FILENAME_RE.sub("_", name or "").strip(" .")
    # Collapse repeated underscores introduced by replacement
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or fallback


def is_multilayer_exr(scene: bpy.types.Scene) -> bool:
    """Return True if the scene output format is Multilayer EXR."""
    fmt = scene.render.image_settings
    if hasattr(fmt, "media_type"):
        return fmt.media_type == "MULTI_LAYER_IMAGE"
    return fmt.file_format == "OPEN_EXR_MULTILAYER"


def is_video_format(scene: bpy.types.Scene) -> bool:
    """Return True if the scene output format is a video container."""
    fmt = scene.render.image_settings
    if hasattr(fmt, "media_type"):
        return fmt.media_type == "VIDEO"
    return fmt.file_format in {"FFMPEG", "AVI_JPEG", "AVI_RAW"}


def find_window_for_scene(scene_name: str) -> Optional[bpy.types.Window]:
    """Return the main (non-temporary) window displaying ``scene_name`` if available."""
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None or not wm.windows:
        return None

    fallback: Optional[bpy.types.Window] = None
    for win in wm.windows:
        if win.scene is not None and win.scene.name == scene_name:
            screen = getattr(win, "screen", None)
            if screen is not None and not getattr(screen, "is_temporary", False):
                return win
            if fallback is None:
                fallback = win

    if fallback is not None:
        return fallback

    for win in wm.windows:
        screen = getattr(win, "screen", None)
        if screen is not None and not getattr(screen, "is_temporary", False):
            return win
    return wm.windows[0]


def get_layer_camera(scene: bpy.types.Scene, view_layer: bpy.types.ViewLayer) -> Optional[bpy.types.Object]:
    """Resolve the camera to use for a View Layer, respecting fallback settings."""
    vl_settings = getattr(view_layer, "rlc_settings", None)
    cam = vl_settings.camera if vl_settings else None
    if cam is not None and cam.type == "CAMERA" and cam.name in bpy.data.objects:
        return cam
    scene_settings = getattr(scene, "rlc_settings", None)
    if scene_settings is None or scene_settings.fallback_to_scene_camera:
        if scene.camera is not None and scene.camera.type == "CAMERA":
            return scene.camera
    return None


def resolve_output_directory(scene: bpy.types.Scene) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the absolute output directory from add-on settings or scene fallback.

    Returns ``(abs_dir, error_message)``. When ``error_message`` is not None,
    ``abs_dir`` is None.
    """
    settings = scene.rlc_settings
    raw = (settings.output_directory or "").strip()

    if raw:
        if raw.startswith("//") and not bpy.data.filepath:
            return (
                None,
                "Output Folder uses a relative path ('//'). Save the .blend file first or pick an absolute folder.",
            )
        abs_dir = os.path.normpath(bpy.path.abspath(raw))
        return abs_dir, None

    # Fallback to Scene Render Output path
    scene_raw = (scene.render.filepath or "").strip()
    if not scene_raw:
        return (
            None,
            "No Output Folder is set. Choose an Output Folder in the RenderLayer Camera panel.",
        )
    if scene_raw.startswith("//") and not bpy.data.filepath:
        return (
            None,
            "Scene Output path is relative ('//') and the .blend file is unsaved. "
            "Choose an absolute Output Folder or save the .blend file first.",
        )

    abs_scene = bpy.path.abspath(scene_raw)
    if abs_scene.endswith(("/", "\\")) or os.path.isdir(abs_scene):
        abs_dir = os.path.normpath(abs_scene)
    else:
        parent = os.path.dirname(abs_scene)
        abs_dir = os.path.normpath(parent) if parent else os.path.normpath(abs_scene)
    return abs_dir, None


def format_filename_stem(
    scene: bpy.types.Scene,
    view_layer: bpy.types.ViewLayer,
    camera: Optional[bpy.types.Object],
    *,
    animation: bool = False,
) -> str:
    """Expand the filename pattern into a sanitized stem for a View Layer."""
    settings = scene.rlc_settings
    vl_settings = getattr(view_layer, "rlc_settings", None)

    custom = (vl_settings.custom_name or "").strip() if vl_settings else ""
    layer_token = custom or view_layer.name
    cam_token = camera.name if camera is not None else "NoCamera"
    scene_token = scene.name
    frame_token = "####" if animation else f"{scene.frame_current:04d}"

    template = (settings.filename_template or "").strip() or "{layer}"

    # Guarantee the layer name appears in the filename even if the user omitted {layer}.
    if "{layer}" not in template:
        template = f"{template}_{{layer}}"

    # Safe substitution that leaves unknown braces harmless
    stem = template
    stem = stem.replace("{layer}", layer_token)
    stem = stem.replace("{camera}", cam_token)
    stem = stem.replace("{scene}", scene_token)

    has_frame_token = "{frame}" in stem
    if has_frame_token:
        stem = stem.replace("{frame}", frame_token)
    elif not animation and settings.include_frame_on_still:
        stem = f"{stem}_{frame_token}"

    safe_stem = sanitize_filename(stem, fallback=sanitize_filename(view_layer.name, "layer"))

    # For animation renders without explicit '#' placeholders, add a clean
    # separator before Blender's automatic 4-digit frame suffix (e.g. Layer_0001.png).
    if animation and not is_video_format(scene) and "#" not in safe_stem:
        safe_stem = f"{safe_stem}_"

    return safe_stem


def build_layer_filepath(
    scene: bpy.types.Scene,
    base_dir: str,
    view_layer: bpy.types.ViewLayer,
    camera: Optional[bpy.types.Object],
    *,
    animation: bool = False,
    used_paths: Optional[set] = None,
) -> Tuple[str, str]:
    """Return ``(target_dir, filepath_without_ext)`` for a View Layer render."""
    settings = scene.rlc_settings
    stem = format_filename_stem(scene, view_layer, camera, animation=animation)

    if settings.use_subfolders:
        sub_name = sanitize_filename(view_layer.name, "layer")
        target_dir = os.path.join(base_dir, sub_name)
    else:
        target_dir = base_dir

    candidate = os.path.join(target_dir, stem)
    if used_paths is not None:
        key = os.path.normcase(candidate)
        if key in used_paths:
            suffix = 2
            trailing_us = stem.endswith("_")
            base_stem = stem[:-1] if trailing_us else stem
            while True:
                alt_stem = f"{base_stem}_{suffix}_" if trailing_us else f"{base_stem}_{suffix}"
                alt = os.path.join(target_dir, alt_stem)
                if os.path.normcase(alt) not in used_paths:
                    candidate = alt
                    key = os.path.normcase(candidate)
                    break
                suffix += 1
        used_paths.add(key)

    return target_dir, candidate


def build_multilayer_filepath(scene: bpy.types.Scene, base_dir: str) -> str:
    """Return the final .exr path for a merged Multilayer EXR."""
    settings = scene.rlc_settings
    template = (settings.multilayer_filename or "").strip() or "{scene}_multilayer"
    stem = template.replace("{scene}", scene.name).replace("{frame}", f"{scene.frame_current:04d}")
    safe_stem = sanitize_filename(stem, fallback="multilayer")
    if not safe_stem.lower().endswith(".exr"):
        safe_stem += ".exr"
    return os.path.join(base_dir, safe_stem)


def preview_output_path(scene: bpy.types.Scene, view_layer: bpy.types.ViewLayer) -> str:
    """Build a human-readable preview of the file that will be written for a layer."""
    base_dir, err = resolve_output_directory(scene)
    if err:
        return err

    if is_multilayer_exr(scene):
        return build_multilayer_filepath(scene, base_dir)

    cam = get_layer_camera(scene, view_layer)
    _, path_no_ext = build_layer_filepath(scene, base_dir, view_layer, cam, animation=False)
    ext = scene.render.file_extension if scene.render.use_file_extension else ""
    return f"{path_no_ext}{ext}"


class RenderStateSnapshot:
    """Captures and restores scene render state using ID names and primitives only.

    Never retains raw ``bpy.types.ID`` or ``bpy.types.Window`` references so it
    remains safe across asynchronous renders, timers, and undo steps.
    """

    def __init__(
        self,
        *,
        scene_name: str,
        orig_camera_name: Optional[str],
        orig_filepath: str,
        orig_single_layer: bool,
        orig_use_file_ext: bool,
        orig_layer_use: Dict[str, bool],
        orig_active_layer_name: Optional[str],
        orig_stamp_bools: Dict[str, bool],
        orig_stamp_note_text: str,
        orig_media_type: Optional[str],
        orig_file_format: str,
        orig_color_depth: str,
        orig_exr_codec: Optional[str],
    ) -> None:
        self.scene_name = scene_name
        self.orig_camera_name = orig_camera_name
        self.orig_filepath = orig_filepath
        self.orig_single_layer = orig_single_layer
        self.orig_use_file_ext = orig_use_file_ext
        self.orig_layer_use = orig_layer_use
        self.orig_active_layer_name = orig_active_layer_name
        self.orig_stamp_bools = orig_stamp_bools
        self.orig_stamp_note_text = orig_stamp_note_text
        self.orig_media_type = orig_media_type
        self.orig_file_format = orig_file_format
        self.orig_color_depth = orig_color_depth
        self.orig_exr_codec = orig_exr_codec

    @classmethod
    def capture(cls, context: Optional[bpy.types.Context], scene: bpy.types.Scene) -> "RenderStateSnapshot":
        render = scene.render
        img_settings = render.image_settings
        window = getattr(context, "window", None) if context is not None else None
        if window is None or getattr(window, "scene", None) != scene:
            window = find_window_for_scene(scene.name)

        orig_active_layer_name: Optional[str] = None
        if window is not None and window.scene == scene and window.view_layer is not None:
            orig_active_layer_name = window.view_layer.name

        return cls(
            scene_name=scene.name,
            orig_camera_name=scene.camera.name if scene.camera else None,
            orig_filepath=render.filepath,
            orig_single_layer=render.use_single_layer,
            orig_use_file_ext=render.use_file_extension,
            orig_layer_use={vl.name: vl.use for vl in scene.view_layers},
            orig_active_layer_name=orig_active_layer_name,
            orig_stamp_bools={
                prop: getattr(render, prop) for prop in _STAMP_BOOL_PROPS if hasattr(render, prop)
            },
            orig_stamp_note_text=render.stamp_note_text,
            orig_media_type=getattr(img_settings, "media_type", None),
            orig_file_format=img_settings.file_format,
            orig_color_depth=img_settings.color_depth,
            orig_exr_codec=getattr(img_settings, "exr_codec", None),
        )

    def restore(self) -> None:
        """Unconditionally restore the captured render state if the scene still exists."""
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is None:
            return

        render = scene.render
        img_settings = render.image_settings

        # Restore camera
        scene.camera = bpy.data.objects.get(self.orig_camera_name) if self.orig_camera_name else None

        # Restore render paths & flags
        render.filepath = self.orig_filepath
        render.use_single_layer = self.orig_single_layer
        render.use_file_extension = self.orig_use_file_ext

        # Restore image format
        if self.orig_media_type is not None and hasattr(img_settings, "media_type"):
            img_settings.media_type = self.orig_media_type
        if self.orig_media_type != "MULTI_LAYER_IMAGE":
            try:
                img_settings.file_format = self.orig_file_format
            except TypeError:
                pass
        try:
            img_settings.color_depth = self.orig_color_depth
        except TypeError:
            pass
        if self.orig_exr_codec is not None and hasattr(img_settings, "exr_codec"):
            try:
                img_settings.exr_codec = self.orig_exr_codec
            except TypeError:
                pass

        # Restore stamp settings
        for prop, val in self.orig_stamp_bools.items():
            if hasattr(render, prop):
                setattr(render, prop, val)
        render.stamp_note_text = self.orig_stamp_note_text

        # Restore view layer enable flags and active view layer
        for vl in scene.view_layers:
            if vl.name in self.orig_layer_use:
                vl.use = self.orig_layer_use[vl.name]

        if self.orig_active_layer_name:
            orig_vl = scene.view_layers.get(self.orig_active_layer_name)
            window = find_window_for_scene(self.scene_name)
            if orig_vl is not None and window is not None and window.scene == scene:
                try:
                    window.view_layer = orig_vl
                except Exception:
                    pass


@contextmanager
def preserve_render_state(context: Optional[bpy.types.Context], scene: bpy.types.Scene):
    """Context manager that snapshots and unconditionally restores render state."""
    snapshot = RenderStateSnapshot.capture(context, scene)
    try:
        yield snapshot
    finally:
        snapshot.restore()


def apply_layer_stamp(scene: bpy.types.Scene, layer_name: str, orig_use_stamp: bool) -> None:
    """Configure Blender's built-in metadata stamp to burn the layer name into the image."""
    render = scene.render
    if not orig_use_stamp:
        # Start clean so only the View Layer name is burned onto the photo
        for prop in _STAMP_BOOL_PROPS:
            if hasattr(render, prop):
                setattr(render, prop, False)
    render.use_stamp = True
    render.use_stamp_note = True
    render.stamp_note_text = layer_name


def configure_scene_for_layer(
    scene: bpy.types.Scene,
    view_layer: bpy.types.ViewLayer,
    camera: bpy.types.Object,
    filepath_no_ext: str,
    *,
    stamp_enabled: bool,
    orig_use_stamp: bool,
    window: Optional[bpy.types.Window] = None,
) -> None:
    """Configure scene camera, View Layer mask, filepath, and stamp for one render pass."""
    scene.camera = camera
    # Disable use_single_layer and enable ONLY the target View Layer via vl.use.
    # This works identically in GUI and headless/background mode and preserves
    # each View Layer's exact name, passes, and collection overrides.
    scene.render.use_single_layer = False
    scene.render.use_file_extension = True
    scene.render.filepath = filepath_no_ext

    for vl in scene.view_layers:
        vl.use = vl.name == view_layer.name

    if window is None:
        window = find_window_for_scene(scene.name)
    if window is not None and window.scene == scene:
        try:
            window.view_layer = view_layer
        except Exception:
            pass

    if stamp_enabled:
        apply_layer_stamp(scene, view_layer.name, orig_use_stamp)


def merge_multilayer_exrs(
    layer_exr_paths: Dict[str, str],
    output_path: str,
    scene: bpy.types.Scene,
) -> List[str]:
    """Merge per-layer EXR files into a single Blender-compatible Multilayer EXR.

    Uses Blender 5.3's bundled OpenEXR Python module to combine all EXR parts
    and passes (Combined, Depth, Normal, AOVs, etc.) from every View Layer
    verbatim without running an extra render pass or touching the compositor.
    """
    import OpenEXR

    warnings: List[str] = []
    merged_parts: List[object] = []

    codec_name = _EXR_CODEC_MAP.get(
        getattr(scene.render.image_settings, "exr_codec", "ZIP"),
        "ZIP_COMPRESSION",
    )
    compression = getattr(OpenEXR, codec_name, OpenEXR.ZIP_COMPRESSION)

    for layer_name, exr_path in layer_exr_paths.items():
        if not os.path.isfile(exr_path):
            warnings.append(f"{layer_name}: temporary EXR missing")
            continue

        with OpenEXR.File(exr_path) as exr:
            for part_idx, part in enumerate(exr.parts):
                src_header = part.header
                part_channels: Dict[str, object] = {}
                for ch_name, ch_obj in part.channels.items():
                    target_name = ch_name if "." in ch_name else f"{layer_name}.{ch_name}"
                    part_channels[target_name] = ch_obj.pixels.copy()

                if not part_channels:
                    continue

                part_name = part.name() or f"{layer_name}.Pass{part_idx}"
                header = {
                    "name": part_name,
                    "compression": compression,
                    "type": OpenEXR.scanlineimage,
                    "BlenderMultiChannel": "Blender V2.55.1 and newer",
                    "Scene": scene.name,
                    "Frame": f"{scene.frame_current:04d}",
                    "Software": f"Blender {bpy.app.version_string}",
                }
                if "colorInteropID" in src_header:
                    header["colorInteropID"] = src_header["colorInteropID"]

                merged_parts.append(OpenEXR.Part(header, part_channels))

    if not merged_parts:
        raise RuntimeError("No EXR channels were collected to merge.")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with OpenEXR.File(merged_parts) as out_exr:
        out_exr.write(output_path)

    return warnings
