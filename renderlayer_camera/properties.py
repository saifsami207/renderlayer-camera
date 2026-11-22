"""PropertyGroups for RenderLayer Camera (Blender 5.3+).

Author: Saif Sami
Inspired by BB-Camera-Layer (https://github.com/riouxr/BB-Camera-Layer).
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup


def _camera_poll(self, obj: bpy.types.Object) -> bool:
    return obj.type == "CAMERA"


def _on_intercept_f12_update(self, context: bpy.types.Context) -> None:
    from . import sync_keymap_from_scene

    scene = getattr(context, "scene", None)
    if scene is not None:
        sync_keymap_from_scene(scene)


def _on_auto_switch_update(self, context: bpy.types.Context) -> None:
    if not self.auto_switch_viewport_camera:
        return
    scene = getattr(context, "scene", None)
    vl = getattr(context, "view_layer", None)
    if scene is None or vl is None:
        return
    cam = vl.rlc_settings.camera
    if cam is not None and cam.type == "CAMERA":
        scene.camera = cam


class RLC_PG_view_layer_settings(PropertyGroup):
    """Per-ViewLayer camera assignment stored directly on bpy.types.ViewLayer."""

    camera: PointerProperty(
        name="Camera",
        description="Dedicated camera to use when rendering this View Layer (uses Scene Camera if empty)",
        type=bpy.types.Object,
        poll=_camera_poll,
    )
    custom_name: StringProperty(
        name="Custom Filename",
        description="Optional filename override for this View Layer (leave blank to use the View Layer's name)",
        default="",
    )


class RLC_PG_scene_settings(PropertyGroup):
    """Per-Scene output and batch-rendering settings for RenderLayer Camera."""

    output_directory: StringProperty(
        name="Output Folder",
        description=(
            "Directory where rendered View Layer images are saved. "
            "Leave empty to use the Scene Output folder"
        ),
        default="",
        subtype="DIR_PATH",
        options={"PATH_SUPPORTS_BLEND_RELATIVE"},
    )
    filename_template: StringProperty(
        name="Filename Pattern",
        description=(
            "Filename template for each rendered layer. "
            "Available tokens: {layer}, {camera}, {scene}, {frame}"
        ),
        default="{layer}",
    )
    use_subfolders: BoolProperty(
        name="Subfolder per Layer",
        description=(
            "Save each View Layer in its own subfolder under the Output Folder. "
            "Leave unchecked to save all layer images directly in the Output Folder"
        ),
        default=False,
    )
    include_frame_on_still: BoolProperty(
        name="Append Frame Number",
        description="Append the current frame number to still filenames (e.g. LayerName_0001.png)",
        default=False,
    )
    stamp_layer_name: BoolProperty(
        name="Burn Layer Name on Image",
        description="Burn the View Layer name visually onto each rendered image using Blender's metadata stamp",
        default=False,
    )
    fallback_to_scene_camera: BoolProperty(
        name="Fallback to Scene Camera",
        description=(
            "When a View Layer has no assigned camera, render it with the active Scene Camera. "
            "Disable to skip unassigned layers"
        ),
        default=True,
    )
    auto_switch_viewport_camera: BoolProperty(
        name="Sync Viewport Camera",
        description="Automatically switch the active Scene Camera when changing View Layers in the UI",
        default=False,
        update=_on_auto_switch_update,
    )
    intercept_f12: BoolProperty(
        name="F12 Renders All Layers",
        description="Remap F12 to render all enabled View Layers with their assigned cameras",
        default=False,
        update=_on_intercept_f12_update,
    )
    multilayer_filename: StringProperty(
        name="Multilayer EXR Name",
        description="Output filename stem for merged Multilayer EXR (tokens: {scene}, {frame})",
        default="{scene}_multilayer",
    )
    show_advanced: BoolProperty(
        name="Advanced Options",
        description="Show filename pattern, subfolder, and camera fallback options",
        default=False,
    )


CLASSES = (
    RLC_PG_view_layer_settings,
    RLC_PG_scene_settings,
)
