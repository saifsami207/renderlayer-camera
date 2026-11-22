"""UI Panels, Menus, and Preferences for RenderLayer Camera (Blender 5.3+).

Author: Saif Sami
Inspired by BB-Camera-Layer (https://github.com/riouxr/BB-Camera-Layer).
"""
from __future__ import annotations

import bpy
from bpy.types import AddonPreferences, Menu, Panel

from . import core, queue


class RLC_AP_preferences(AddonPreferences):
    """Extension information and credits for RenderLayer Camera."""

    bl_idname = __package__

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        box = layout.box()
        box.label(text="RenderLayer Camera", icon="RENDERLAYERS")
        box.label(text="Maintainer: Saif Sami  |  License: GPL-3.0-or-later")
        box.label(text="Inspired by BB-Camera-Layer (https://github.com/riouxr/BB-Camera-Layer)")

        info = layout.box()
        info.label(text="UI Locations:", icon="INFO")
        info.label(text="  • Properties -> View Layer -> RenderLayer Camera")
        info.label(text="  • Properties -> Output -> RenderLayer Camera")
        info.label(text="  • Topbar -> Render -> Render All View Layers")


class RLC_MT_utilities(Menu):
    """Utility actions menu for RenderLayer Camera."""

    bl_label = "RenderLayer Camera Utilities"
    bl_idname = "RLC_MT_utilities"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        op = layout.operator("rlc.assign_active_camera", text="Assign Scene Camera to Unassigned", icon="CAMERA_DATA")
        op.all_unassigned = True
        layout.operator("rlc.use_scene_output_path", text="Copy Folder from Scene Output", icon="IMPORT")
        layout.separator()
        layout.operator("rlc.clear_layer_cameras", text="Clear All Assignments", icon="X")


def _draw_panel_body(layout: bpy.types.UILayout, context: bpy.types.Context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None:
        return

    settings = scene.rlc_settings
    is_mlexr = core.is_multilayer_exr(scene)
    is_video = core.is_video_format(scene)
    active_vl = getattr(context, "view_layer", None)
    batch_queue = queue.get_queue()
    is_running = batch_queue.is_active()

    # ── Active Async Batch Status & Cancel Control ───────────────────────────
    if is_running:
        status_box = layout.box()
        status_row = status_box.row(align=True)
        status_row.label(text=batch_queue.status_label(), icon="TIME")
        cancel_row = status_box.row(align=True)
        cancel_row.scale_y = 1.2
        cancel_row.operator("rlc.cancel_batch_render", text="Cancel Batch Render", icon="CANCEL")

    # ── Top Toolbar ──────────────────────────────────────────────────────────
    top = layout.row(align=True)
    top.prop(settings, "intercept_f12", text="F12 All Layers", toggle=True, icon="RENDER_STILL")
    top.prop(settings, "auto_switch_viewport_camera", text="Sync Cam", toggle=True, icon="VIEW_CAMERA")
    top.menu("RLC_MT_utilities", text="", icon="DOWNARROW_HLT")

    # ── Per-View-Layer Camera Assignment List ────────────────────────────────
    box = layout.box()
    header = box.row(align=True)
    header.label(text="View Layer", icon="RENDERLAYERS")
    header.label(text="Assigned Camera", icon="CAMERA_DATA")

    col = box.column(align=True)
    for vl in scene.view_layers:
        vl_cfg = vl.rlc_settings
        row = col.row(align=True)

        # Render enable/disable checkbox
        row.prop(vl, "use", text="")

        # Clickable layer button (switches active layer + previews its camera)
        is_active = active_vl is not None and vl.name == active_vl.name
        op_act = row.operator(
            "rlc.activate_layer_camera",
            text=vl.name,
            depress=is_active,
            emboss=is_active,
        )
        op_act.layer_name = vl.name

        # Camera picker
        cam_sub = row.row(align=True)
        cam_sub.enabled = vl.use
        cam_sub.prop(vl_cfg, "camera", text="")

        # Quick assign active scene camera when empty
        if vl_cfg.camera is None:
            op_assign = cam_sub.operator("rlc.assign_active_camera", text="", icon="EYEDROPPER")
            op_assign.layer_name = vl.name
            op_assign.all_unassigned = False

    # ── Output Path & Naming ─────────────────────────────────────────────────
    out_box = layout.box()
    out_box.label(text="Output", icon="OUTPUT")

    path_row = out_box.row(align=True)
    path_row.prop(settings, "output_directory", text="")
    path_row.operator("rlc.open_output_folder", text="", icon="FILE_FOLDER")

    opt_col = out_box.column(align=True)
    opt_col.use_property_split = True
    opt_col.use_property_decorate = False

    if is_mlexr:
        opt_col.prop(settings, "multilayer_filename", text="EXR Filename")
    else:
        opt_col.prop(settings, "filename_template", text="Filename")

    opt_col.prop(settings, "stamp_layer_name", text="Burn Name on Image")

    # Collapsible advanced options
    adv_row = out_box.row(align=True)
    adv_icon = "TRIA_DOWN" if settings.show_advanced else "TRIA_RIGHT"
    adv_row.prop(settings, "show_advanced", text="More Options", icon=adv_icon, emboss=False)
    if settings.show_advanced:
        adv_col = out_box.column(align=True)
        adv_col.use_property_split = True
        adv_col.use_property_decorate = False
        if not is_mlexr:
            adv_col.prop(settings, "use_subfolders")
            adv_col.prop(settings, "include_frame_on_still")
        adv_col.prop(settings, "fallback_to_scene_camera")

        if active_vl is not None:
            adv_col.separator()
            adv_col.prop(active_vl.rlc_settings, "custom_name", text=f"'{active_vl.name}' Name")

    # Live preview of resolved output file
    preview_vl = next((vl for vl in scene.view_layers if vl.use), active_vl)
    if preview_vl is not None:
        preview_text = core.preview_output_path(scene, preview_vl)
        prev_row = out_box.row()
        prev_row.scale_y = 0.8
        prev_row.label(text=preview_text, icon="FILE_IMAGE")

    # ── Render Action Buttons ────────────────────────────────────────────────
    layout.separator()
    actions = layout.column(align=True)
    actions.enabled = not is_running

    if is_mlexr:
        row = actions.row(align=True)
        row.scale_y = 1.3
        row.operator("rlc.render_merge_multilayer", text="Render & Merge Multilayer EXR", icon="RENDER_STILL")

        sep_row = actions.row(align=True)
        op_sep = sep_row.operator(
            "rlc.render_all_layers",
            text="Render Separate EXR per Layer",
            icon="RENDERLAYERS",
        )
        op_sep.animation = False
        op_sep.force_separate_files = True
    else:
        row = actions.row(align=True)
        row.scale_y = 1.3

        still_col = row.row(align=True)
        still_col.enabled = not is_video
        op_still = still_col.operator("rlc.render_all_layers", text="Render All Layers", icon="RENDER_STILL")
        op_still.animation = False
        op_still.force_separate_files = False

        op_anim = row.operator("rlc.render_all_layers", text="Animation", icon="RENDER_ANIMATION")
        op_anim.animation = True
        op_anim.force_separate_files = False

        if is_video:
            warn = layout.box()
            warn.scale_y = 0.8
            warn.label(text="Output format is Video: use Animation or switch to PNG/EXR.", icon="INFO")


class RLC_PT_view_layer_panel(Panel):
    """Primary panel in Properties -> View Layer."""

    bl_label = "RenderLayer Camera"
    bl_idname = "RLC_PT_view_layer_panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "view_layer"

    def draw(self, context: bpy.types.Context) -> None:
        _draw_panel_body(self.layout, context)


class RLC_PT_output_panel(Panel):
    """Companion panel in Properties -> Output for quick access alongside Render Output."""

    bl_label = "RenderLayer Camera"
    bl_idname = "RLC_PT_output_panel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "output"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: bpy.types.Context) -> None:
        _draw_panel_body(self.layout, context)


def draw_topbar_render_menu(self, context: bpy.types.Context) -> None:
    """Append batch layer render actions to the topbar Render menu."""
    layout = self.layout
    layout.separator()
    op_still = layout.operator("rlc.render_all_layers", text="Render All View Layers", icon="RENDER_STILL")
    op_still.animation = False
    op_still.force_separate_files = False

    op_anim = layout.operator(
        "rlc.render_all_layers",
        text="Render All View Layers (Animation)",
        icon="RENDER_ANIMATION",
    )
    op_anim.animation = True
    op_anim.force_separate_files = False


CLASSES = (
    RLC_AP_preferences,
    RLC_MT_utilities,
    RLC_PT_view_layer_panel,
    RLC_PT_output_panel,
)
