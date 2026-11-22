"""Operators for RenderLayer Camera (Blender 5.3+).

Author: Saif Sami
Inspired by BB-Camera-Layer (https://github.com/riouxr/BB-Camera-Layer).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Dict, List, Tuple

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from . import core, queue


class _BatchGuard:
    """Context manager suppressing single-layer render_pre/post hooks during synchronous batches."""

    def __enter__(self):
        from . import set_batch_rendering

        set_batch_rendering(True)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        from . import set_batch_rendering

        set_batch_rendering(False)
        return False


def _render_single_pass_sync(
    context: bpy.types.Context,
    scene: bpy.types.Scene,
    view_layer: bpy.types.ViewLayer,
    camera: bpy.types.Object,
    filepath_no_ext: str,
    *,
    animation: bool,
    stamp_enabled: bool,
    orig_use_stamp: bool,
) -> None:
    """Configure scene for one View Layer and execute Blender's render operator synchronously."""
    core.configure_scene_for_layer(
        scene,
        view_layer,
        camera,
        filepath_no_ext,
        stamp_enabled=stamp_enabled,
        orig_use_stamp=orig_use_stamp,
        window=getattr(context, "window", None),
    )

    if animation:
        bpy.ops.render.render(animation=True, write_still=False, scene=scene.name)
    else:
        bpy.ops.render.render(write_still=True, scene=scene.name)


def _collect_layer_jobs(
    scene: bpy.types.Scene,
    base_dir: str,
    enabled_layers: List[bpy.types.ViewLayer],
    *,
    animation: bool,
) -> Tuple[List[queue.RenderJobItem], List[str]]:
    """Build ordered render job descriptors and skipped layer names."""
    jobs: List[queue.RenderJobItem] = []
    skipped: List[str] = []
    used_paths: set = set()

    for vl in enabled_layers:
        cam = core.get_layer_camera(scene, vl)
        if cam is None:
            skipped.append(vl.name)
            continue

        target_dir, filepath_no_ext = core.build_layer_filepath(
            scene,
            base_dir,
            vl,
            cam,
            animation=animation,
            used_paths=used_paths,
        )
        jobs.append(
            queue.RenderJobItem(
                view_layer_name=vl.name,
                camera_name=cam.name,
                target_dir=target_dir,
                filepath_no_ext=filepath_no_ext,
            )
        )

    return jobs, skipped


class RLC_OT_render_all_layers(Operator):
    """Render every enabled View Layer using its assigned camera."""

    bl_idname = "rlc.render_all_layers"
    bl_label = "Render All View Layers"
    bl_description = (
        "Render each enabled View Layer with its assigned camera directly into the chosen Output Folder, "
        "naming each file after its View Layer"
    )

    animation: BoolProperty(
        name="Animation",
        description="Render the scene frame range for each enabled View Layer instead of a single still frame",
        default=False,
    )
    force_separate_files: BoolProperty(
        name="Force Separate Files",
        description="Always write one file per View Layer even when output format is Multilayer EXR",
        default=False,
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return context.scene is not None and len(context.scene.view_layers) > 0

    def execute(self, context: bpy.types.Context):
        from . import is_batch_rendering

        if is_batch_rendering():
            self.report({"WARNING"}, "A batch render is already running.")
            return {"CANCELLED"}

        scene = context.scene
        settings = scene.rlc_settings

        # When output is Multilayer EXR and the user wants a merged still (e.g. via F12),
        # dispatch cleanly to the Multilayer EXR merge operator.
        if not self.animation and not self.force_separate_files and core.is_multilayer_exr(scene):
            return bpy.ops.rlc.render_merge_multilayer()

        if not self.animation and core.is_video_format(scene):
            self.report(
                {"ERROR"},
                "Output format is set to Video. Switch Output to an Image format for still renders, "
                "or use 'Animation'.",
            )
            return {"CANCELLED"}

        base_dir, err = core.resolve_output_directory(scene)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        enabled_layers = [vl for vl in scene.view_layers if vl.use]
        if not enabled_layers:
            self.report({"WARNING"}, "No View Layers are enabled for rendering.")
            return {"CANCELLED"}

        jobs, skipped = _collect_layer_jobs(scene, base_dir, enabled_layers, animation=self.animation)
        if not jobs:
            msg = "No layers rendered."
            if skipped:
                msg += " Missing camera on: " + ", ".join(skipped)
            self.report({"WARNING"}, msg)
            return {"CANCELLED"}

        orig_use_stamp = scene.render.use_stamp

        # GUI mode: run asynchronously via INVOKE_DEFAULT so Blender opens the Render
        # window, stays responsive, and honors ESC cancellation.
        if not bpy.app.background:
            started, message = queue.get_queue().start(
                context,
                scene,
                jobs,
                base_dir=base_dir,
                animation=self.animation,
                stamp_enabled=settings.stamp_layer_name,
                orig_use_stamp=orig_use_stamp,
                skipped=skipped,
                multilayer_merge=False,
            )
            if not started:
                self.report({"WARNING"}, message)
                return {"CANCELLED"}
            self.report({"INFO"}, message)
            return {"FINISHED"}

        # Headless / background CLI mode: run synchronously until all layers complete.
        rendered_files: List[str] = []
        errors: List[str] = []

        with _BatchGuard(), core.preserve_render_state(context, scene):
            for job in jobs:
                vl = scene.view_layers.get(job.view_layer_name)
                cam = bpy.data.objects.get(job.camera_name)
                if vl is None or cam is None:
                    errors.append(f"{job.view_layer_name}: layer or camera missing")
                    continue

                try:
                    os.makedirs(job.target_dir, exist_ok=True)
                except OSError as exc:
                    errors.append(f"{vl.name}: cannot create folder ({exc})")
                    continue

                try:
                    _render_single_pass_sync(
                        context,
                        scene,
                        vl,
                        cam,
                        job.filepath_no_ext,
                        animation=self.animation,
                        stamp_enabled=settings.stamp_layer_name,
                        orig_use_stamp=orig_use_stamp,
                    )
                    rendered_files.append(job.filepath_no_ext)
                except Exception as exc:
                    errors.append(f"{vl.name}: {exc}")

        if errors:
            self.report(
                {"WARNING"},
                f"Rendered {len(rendered_files)} layer(s) with errors: " + " | ".join(errors),
            )
            return {"FINISHED"} if rendered_files else {"CANCELLED"}

        summary = f"Rendered {len(rendered_files)} View Layer(s) to {base_dir}"
        if skipped:
            summary += f" (skipped without camera: {', '.join(skipped)})"
        self.report({"INFO"}, summary)
        return {"FINISHED"}


class RLC_OT_render_merge_multilayer(Operator):
    """Render each View Layer with its camera and merge all passes into one Multilayer EXR."""

    bl_idname = "rlc.render_merge_multilayer"
    bl_label = "Render & Merge Multilayer EXR"
    bl_description = (
        "Render each enabled View Layer with its assigned camera and combine all layers "
        "and passes into a single Multilayer EXR file"
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return context.scene is not None and core.is_multilayer_exr(context.scene)

    def execute(self, context: bpy.types.Context):
        from . import is_batch_rendering

        if is_batch_rendering():
            self.report({"WARNING"}, "A batch render is already running.")
            return {"CANCELLED"}

        scene = context.scene
        settings = scene.rlc_settings

        base_dir, err = core.resolve_output_directory(scene)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        enabled_layers = [vl for vl in scene.view_layers if vl.use]
        if not enabled_layers:
            self.report({"WARNING"}, "No View Layers are enabled for rendering.")
            return {"CANCELLED"}

        out_path = core.build_multilayer_filepath(scene, base_dir)
        try:
            os.makedirs(base_dir, exist_ok=True)
        except OSError as exc:
            self.report({"ERROR"}, f"Cannot create output directory: {exc}")
            return {"CANCELLED"}

        tmp_dir = tempfile.mkdtemp(prefix="rlc_mlexr_")
        jobs: List[queue.RenderJobItem] = []
        skipped: List[str] = []

        for idx, vl in enumerate(enabled_layers):
            cam = core.get_layer_camera(scene, vl)
            if cam is None:
                skipped.append(vl.name)
                continue
            safe = core.sanitize_filename(vl.name, f"layer_{idx}")
            tmp_stem = os.path.join(tmp_dir, f"{idx:03d}_{safe}")
            jobs.append(
                queue.RenderJobItem(
                    view_layer_name=vl.name,
                    camera_name=cam.name,
                    target_dir=tmp_dir,
                    filepath_no_ext=tmp_stem,
                    expected_exr_path=tmp_stem + ".exr",
                )
            )

        if not jobs:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            msg = "No layers rendered."
            if skipped:
                msg += " Missing camera on: " + ", ".join(skipped)
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        orig_use_stamp = scene.render.use_stamp

        # GUI mode: render temp EXRs asynchronously, then merge and clean up on completion.
        if not bpy.app.background:
            started, message = queue.get_queue().start(
                context,
                scene,
                jobs,
                base_dir=base_dir,
                animation=False,
                stamp_enabled=settings.stamp_layer_name,
                orig_use_stamp=orig_use_stamp,
                skipped=skipped,
                multilayer_merge=True,
                multilayer_output_path=out_path,
                temp_dir=tmp_dir,
            )
            if not started:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                self.report({"WARNING"}, message)
                return {"CANCELLED"}
            self.report({"INFO"}, message)
            return {"FINISHED"}

        # Headless / background CLI mode: render temp EXRs synchronously and merge.
        layer_exrs: Dict[str, str] = {}
        errors: List[str] = []

        try:
            with _BatchGuard(), core.preserve_render_state(context, scene):
                # Keep MULTI_LAYER_IMAGE so every enabled render pass (Combined, Depth,
                # Normal, Cryptomatte, AOVs...) is written into each temp EXR.
                scene.render.image_settings.media_type = "MULTI_LAYER_IMAGE"

                for job in jobs:
                    vl = scene.view_layers.get(job.view_layer_name)
                    cam = bpy.data.objects.get(job.camera_name)
                    if vl is None or cam is None:
                        errors.append(f"{job.view_layer_name}: layer or camera missing")
                        continue

                    try:
                        _render_single_pass_sync(
                            context,
                            scene,
                            vl,
                            cam,
                            job.filepath_no_ext,
                            animation=False,
                            stamp_enabled=settings.stamp_layer_name,
                            orig_use_stamp=orig_use_stamp,
                        )
                        if job.expected_exr_path and os.path.isfile(job.expected_exr_path):
                            layer_exrs[vl.name] = job.expected_exr_path
                        else:
                            errors.append(f"{vl.name}: temporary EXR not written")
                    except Exception as exc:
                        errors.append(f"{vl.name}: {exc}")

            if not layer_exrs:
                msg = "No layers rendered."
                if errors:
                    msg += " " + " | ".join(errors)
                self.report({"ERROR"}, msg)
                return {"CANCELLED"}

            try:
                merge_warnings = core.merge_multilayer_exrs(layer_exrs, out_path, scene)
                errors.extend(merge_warnings)
            except Exception as exc:
                self.report({"ERROR"}, f"Failed to merge Multilayer EXR: {exc}")
                return {"CANCELLED"}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if errors:
            self.report({"WARNING"}, f"Merged to {out_path} with warnings: " + " | ".join(errors))
        else:
            msg = f"Merged {len(layer_exrs)} View Layer(s) -> {out_path}"
            if skipped:
                msg += f" (skipped without camera: {', '.join(skipped)})"
            self.report({"INFO"}, msg)

        return {"FINISHED"}


class RLC_OT_cancel_batch_render(Operator):
    """Cancel an active asynchronous batch render queue and restore scene state."""

    bl_idname = "rlc.cancel_batch_render"
    bl_label = "Cancel Batch Render"
    bl_description = "Stop the running View Layer batch render queue and restore original scene settings"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return queue.get_queue().is_active()

    def execute(self, context: bpy.types.Context):
        if queue.get_queue().request_cancel():
            self.report(
                {"INFO"},
                "Batch render cancellation requested (press ESC in the Render window to abort the active frame immediately).",
            )
            return {"FINISHED"}
        self.report({"WARNING"}, "No active batch render to cancel.")
        return {"CANCELLED"}


class RLC_OT_activate_layer_camera(Operator):
    """Switch to a View Layer and make its assigned camera the active Scene Camera."""

    bl_idname = "rlc.activate_layer_camera"
    bl_label = "Activate View Layer & Camera"
    bl_description = "Switch to this View Layer and preview its assigned camera"
    bl_options = {"REGISTER", "UNDO"}

    layer_name: StringProperty(name="View Layer")

    def execute(self, context: bpy.types.Context):
        scene = context.scene
        vl = scene.view_layers.get(self.layer_name)
        if vl is None:
            self.report({"WARNING"}, f"View Layer '{self.layer_name}' not found.")
            return {"CANCELLED"}

        if context.window is not None:
            context.window.view_layer = vl

        cam = vl.rlc_settings.camera
        if cam is not None and cam.type == "CAMERA":
            scene.camera = cam
            self.report({"INFO"}, f"Active: {vl.name} -> {cam.name}")
        else:
            self.report({"INFO"}, f"Active View Layer: {vl.name} (using Scene Camera)")
        return {"FINISHED"}


class RLC_OT_assign_active_camera(Operator):
    """Assign the current Scene Camera to one View Layer or all unassigned View Layers."""

    bl_idname = "rlc.assign_active_camera"
    bl_label = "Assign Scene Camera"
    bl_description = "Assign the active Scene Camera to this View Layer (or all unassigned layers)"
    bl_options = {"REGISTER", "UNDO"}

    layer_name: StringProperty(name="View Layer", default="")
    all_unassigned: BoolProperty(name="All Unassigned", default=False)

    def execute(self, context: bpy.types.Context):
        scene = context.scene
        cam = scene.camera
        if cam is None or cam.type != "CAMERA":
            # Fallback to active object if it's a camera
            active = getattr(context, "active_object", None)
            if active is not None and active.type == "CAMERA":
                cam = active
            else:
                self.report({"WARNING"}, "No active camera found in scene.")
                return {"CANCELLED"}

        count = 0
        if self.all_unassigned:
            for vl in scene.view_layers:
                if vl.rlc_settings.camera is None:
                    vl.rlc_settings.camera = cam
                    count += 1
            self.report({"INFO"}, f"Assigned '{cam.name}' to {count} unassigned layer(s).")
        else:
            vl = scene.view_layers.get(self.layer_name) or context.view_layer
            if vl is None:
                return {"CANCELLED"}
            vl.rlc_settings.camera = cam
            self.report({"INFO"}, f"Assigned '{cam.name}' to '{vl.name}'.")

        return {"FINISHED"}


class RLC_OT_clear_layer_cameras(Operator):
    """Clear assigned cameras from all View Layers in the current Scene."""

    bl_idname = "rlc.clear_layer_cameras"
    bl_label = "Clear All Camera Assignments"
    bl_description = "Remove dedicated camera assignments from all View Layers in this scene"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context):
        scene = context.scene
        for vl in scene.view_layers:
            vl.rlc_settings.camera = None
        self.report({"INFO"}, "Cleared all View Layer camera assignments.")
        return {"FINISHED"}


class RLC_OT_open_output_folder(Operator):
    """Open the resolved output directory in the system file browser."""

    bl_idname = "rlc.open_output_folder"
    bl_label = "Open Output Folder"
    bl_description = "Open the resolved output directory in your file manager"

    def execute(self, context: bpy.types.Context):
        base_dir, err = core.resolve_output_directory(context.scene)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        try:
            os.makedirs(base_dir, exist_ok=True)
        except OSError as exc:
            self.report({"ERROR"}, f"Cannot create output directory: {exc}")
            return {"CANCELLED"}

        bpy.ops.wm.path_open(filepath=base_dir)
        return {"FINISHED"}


class RLC_OT_use_scene_output_path(Operator):
    """Copy the directory from Scene Render Output into this add-on's Output Folder."""

    bl_idname = "rlc.use_scene_output_path"
    bl_label = "Copy Scene Output Folder"
    bl_description = "Populate Output Folder from the Scene's Render Output path"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context):
        scene = context.scene
        raw = (scene.render.filepath or "").strip()
        if not raw:
            self.report({"WARNING"}, "Scene Render Output path is empty.")
            return {"CANCELLED"}
        abs_path = bpy.path.abspath(raw)
        if abs_path.endswith(("/", "\\")) or os.path.isdir(abs_path):
            folder = abs_path
        else:
            folder = os.path.dirname(abs_path) or abs_path
        if folder and not folder.endswith(os.sep):
            folder += os.sep
        scene.rlc_settings.output_directory = folder
        return {"FINISHED"}


CLASSES = (
    RLC_OT_render_all_layers,
    RLC_OT_render_merge_multilayer,
    RLC_OT_cancel_batch_render,
    RLC_OT_activate_layer_camera,
    RLC_OT_assign_active_camera,
    RLC_OT_clear_layer_cameras,
    RLC_OT_open_output_folder,
    RLC_OT_use_scene_output_path,
)
