"""Non-blocking asynchronous batch render queue for RenderLayer Camera (Blender 5.3+).

In Blender GUI mode, each View Layer is launched via
``bpy.ops.render.render("INVOKE_DEFAULT", ...)`` so Blender opens the native
Render window, keeps the UI responsive, shows live progress, and honors ESC
cancellation. Subsequent layers are scheduled on the main thread via
``bpy.app.timers`` after ``render_complete`` / ``render_cancel`` and after
``bpy.app.is_job_running("RENDER")`` clears.

All retained state uses ID names and primitive values only (never raw
``bpy.types.ID`` or ``bpy.types.Window`` pointers).

Author: Saif Sami
Inspired by BB-Camera-Layer (https://github.com/riouxr/BB-Camera-Layer).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import bpy

from . import core


@dataclass(frozen=True)
class RenderJobItem:
    """Immutable descriptor for a single View Layer render pass (names/paths only)."""

    view_layer_name: str
    camera_name: str
    target_dir: str
    filepath_no_ext: str
    expected_exr_path: Optional[str] = None


def is_render_job_running() -> bool:
    """Return True if Blender currently has an active render job running."""
    checker = getattr(bpy.app, "is_job_running", None)
    if checker is None:
        return False
    try:
        return bool(checker("RENDER"))
    except Exception:
        return False


def _pick_render_area(screen: Optional[bpy.types.Screen]) -> Optional[bpy.types.Area]:
    """Select an appropriate area for invoking ``bpy.ops.render.render``."""
    if screen is None or not screen.areas:
        return None
    for preferred in ("VIEW_3D", "IMAGE_EDITOR", "PROPERTIES"):
        for area in screen.areas:
            if area.type == preferred:
                return area
    return screen.areas[0]


def _default_invoke_render(scene_name: str, animation: bool) -> Set[str]:
    """Invoke Blender's native non-blocking render operator with a valid GUI context."""
    scene = bpy.data.scenes.get(scene_name)
    if scene is None:
        return {"CANCELLED"}

    win = core.find_window_for_scene(scene_name)
    override_kwargs = {"scene": scene}
    if win is not None:
        override_kwargs["window"] = win
        screen = getattr(win, "screen", None)
        if screen is not None:
            override_kwargs["screen"] = screen
            area = _pick_render_area(screen)
            if area is not None:
                override_kwargs["area"] = area
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is not None:
                    override_kwargs["region"] = region

    with bpy.context.temp_override(**override_kwargs):
        if animation:
            return set(bpy.ops.render.render("INVOKE_DEFAULT", animation=True, write_still=False, scene=scene_name))
        return set(bpy.ops.render.render("INVOKE_DEFAULT", write_still=True, scene=scene_name))


def _tag_ui_redraw() -> None:
    """Request redraw of Properties panels so batch progress and cancel controls update."""
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for win in wm.windows:
        screen = getattr(win, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == "PROPERTIES":
                area.tag_redraw()


def _set_workspace_status(text: Optional[str]) -> None:
    """Update or clear Blender's bottom status bar text."""
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for win in wm.windows:
        ws = getattr(win, "workspace", None)
        if ws is not None:
            try:
                ws.status_text_set(text)
            except Exception:
                pass


class BatchRenderQueue:
    """State machine driving asynchronous multi-View-Layer renders in GUI mode."""

    def __init__(self) -> None:
        self.active: bool = False
        self.scene_name: str = ""
        self.base_dir: str = ""
        self.animation: bool = False
        self.multilayer_merge: bool = False
        self.multilayer_output_path: Optional[str] = None
        self.temp_dir: Optional[str] = None
        self.stamp_enabled: bool = False
        self.orig_use_stamp: bool = False
        self.snapshot: Optional[core.RenderStateSnapshot] = None

        self.jobs: List[RenderJobItem] = []
        self.current_index: int = -1
        self.awaiting_job: bool = False
        self.cancel_requested: bool = False

        self.rendered_files: List[str] = []
        self.layer_exrs: Dict[str, str] = {}
        self.skipped: List[str] = []
        self.errors: List[str] = []

        self.last_status: str = ""
        self.last_status_level: str = "INFO"

        # Injectable hooks for headless unit testing
        self._invoke_render_fn: Callable[[str, bool], Set[str]] = _default_invoke_render
        self._job_running_fn: Callable[[], bool] = is_render_job_running

    # ------------------------------------------------------------------
    # Query helpers (safe to call from UI draw)
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        return self.active

    def status_label(self) -> str:
        """Human-readable status string for UI panels while a batch is active."""
        if not self.active:
            return self.last_status
        total = len(self.jobs)
        idx = max(0, min(self.current_index + 1, total))
        layer_name = self.jobs[self.current_index].view_layer_name if 0 <= self.current_index < total else ""
        if self.cancel_requested:
            return f"Cancelling batch ({idx}/{total})..."
        mode = "EXR " if self.multilayer_merge else ""
        if layer_name:
            return f"Rendering {mode}{idx}/{total}: {layer_name}"
        return f"Rendering {mode}{idx}/{total}..."

    # ------------------------------------------------------------------
    # Queue lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        context: Optional[bpy.types.Context],
        scene: bpy.types.Scene,
        jobs: Sequence[RenderJobItem],
        *,
        base_dir: str,
        animation: bool = False,
        stamp_enabled: bool = False,
        orig_use_stamp: bool = False,
        skipped: Sequence[str] = (),
        multilayer_merge: bool = False,
        multilayer_output_path: Optional[str] = None,
        temp_dir: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Begin a non-blocking batch render queue.

        Returns ``(started, message)``.
        """
        if self.active:
            return False, "A batch render is already running."
        if self._job_running_fn():
            return False, "Another Blender render job is currently running. Wait for it to finish or cancel it first."
        if not jobs:
            return False, "No View Layers queued for rendering."

        self.snapshot = core.RenderStateSnapshot.capture(context, scene)
        self.active = True
        self.scene_name = scene.name
        self.base_dir = base_dir
        self.animation = animation
        self.multilayer_merge = multilayer_merge
        self.multilayer_output_path = multilayer_output_path
        self.temp_dir = temp_dir
        self.stamp_enabled = stamp_enabled
        self.orig_use_stamp = orig_use_stamp

        self.jobs = list(jobs)
        self.current_index = -1
        self.awaiting_job = False
        self.cancel_requested = False

        self.rendered_files = []
        self.layer_exrs = {}
        self.skipped = list(skipped)
        self.errors = []
        self.last_status = ""
        self.last_status_level = "INFO"

        if self.multilayer_merge:
            scene.render.image_settings.media_type = "MULTI_LAYER_IMAGE"

        _tag_ui_redraw()
        self._launch_next_job()

        if not self.active:
            return self.last_status_level != "ERROR", self.last_status

        if self.multilayer_merge and self.multilayer_output_path:
            return True, f"Started Multilayer EXR render of {len(self.jobs)} View Layer(s) -> {self.multilayer_output_path}"
        return True, f"Started batch render of {len(self.jobs)} View Layer(s) to {base_dir}"

    def request_cancel(self) -> bool:
        """Request cancellation of the active batch queue.

        If a layer is currently rendering, stops the queue from launching any
        further layers and restores all state as soon as the current render
        finishes or is cancelled via ESC. If no render job is currently running,
        restores state immediately.
        """
        if not self.active:
            return False

        self.cancel_requested = True
        _set_workspace_status("RenderLayer Camera: Cancelling batch render...")
        _tag_ui_redraw()

        if not self.awaiting_job and not self._job_running_fn():
            self._unregister_timer()
            self._finalize(cancelled=True)
        return True

    def abort_and_cleanup(self, *, restore_state: bool = True) -> None:
        """Immediately halt the queue and clean up timers/temp files (e.g. on file load or unregister)."""
        self._unregister_timer()
        snapshot = self.snapshot
        temp_dir = self.temp_dir

        self._reset_state()

        if restore_state and snapshot is not None:
            try:
                snapshot.restore()
            except Exception:
                pass
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

        _set_workspace_status(None)
        _tag_ui_redraw()

    # ------------------------------------------------------------------
    # Blender render handler hooks
    # ------------------------------------------------------------------

    def on_render_complete(self, scene: Optional[bpy.types.Scene]) -> None:
        """Called from ``bpy.app.handlers.render_complete`` when one layer finishes."""
        if not self.active or not self.awaiting_job:
            return
        if scene is not None and scene.name != self.scene_name:
            return

        self.awaiting_job = False
        self._record_current_job_completed()
        self._schedule_step(delay=0.1)

    def on_render_cancel(self, scene: Optional[bpy.types.Scene]) -> None:
        """Called from ``bpy.app.handlers.render_cancel`` when user presses ESC / cancels."""
        if not self.active:
            return
        if scene is not None and scene.name != self.scene_name:
            return

        self.awaiting_job = False
        self.cancel_requested = True
        self._schedule_step(delay=0.1)

    # ------------------------------------------------------------------
    # Internal timer & job advancement
    # ------------------------------------------------------------------

    def _schedule_step(self, delay: float = 0.1) -> None:
        self._unregister_timer()
        bpy.app.timers.register(self._timer_step, first_interval=delay)

    def _unregister_timer(self) -> None:
        if bpy.app.timers.is_registered(self._timer_step):
            bpy.app.timers.unregister(self._timer_step)

    def _timer_step(self) -> Optional[float]:
        """Advance the queue on Blender's main thread after the previous render job exits."""
        if not self.active:
            return None

        # Wait until Blender has fully torn down WM_JOB_TYPE_RENDER before
        # launching the next layer or restoring state.
        if self._job_running_fn():
            return 0.1

        if self.cancel_requested:
            self._finalize(cancelled=True)
            return None

        if self.current_index + 1 >= len(self.jobs):
            self._finalize(cancelled=False)
            return None

        self._launch_next_job()
        return None

    def _record_current_job_completed(self) -> None:
        if not (0 <= self.current_index < len(self.jobs)):
            return
        job = self.jobs[self.current_index]
        if self.multilayer_merge:
            if job.expected_exr_path and os.path.isfile(job.expected_exr_path):
                self.layer_exrs[job.view_layer_name] = job.expected_exr_path
            else:
                self.errors.append(f"{job.view_layer_name}: temporary EXR not written")
        else:
            self.rendered_files.append(job.filepath_no_ext)

    def _launch_next_job(self) -> None:
        """Configure scene for the next View Layer and start its async render job."""
        while self.current_index + 1 < len(self.jobs):
            if self.cancel_requested:
                self._finalize(cancelled=True)
                return

            self.current_index += 1
            job = self.jobs[self.current_index]

            scene = bpy.data.scenes.get(self.scene_name)
            if scene is None:
                self.errors.append(f"Scene '{self.scene_name}' no longer exists")
                self._finalize(cancelled=True)
                return

            vl = scene.view_layers.get(job.view_layer_name)
            if vl is None:
                self.errors.append(f"{job.view_layer_name}: View Layer not found")
                continue

            cam = bpy.data.objects.get(job.camera_name)
            if cam is None or cam.type != "CAMERA":
                self.errors.append(f"{job.view_layer_name}: camera '{job.camera_name}' not found")
                continue

            try:
                os.makedirs(job.target_dir, exist_ok=True)
            except OSError as exc:
                self.errors.append(f"{job.view_layer_name}: cannot create folder ({exc})")
                continue

            if self.multilayer_merge:
                scene.render.image_settings.media_type = "MULTI_LAYER_IMAGE"

            core.configure_scene_for_layer(
                scene,
                vl,
                cam,
                job.filepath_no_ext,
                stamp_enabled=self.stamp_enabled,
                orig_use_stamp=self.orig_use_stamp,
            )

            _set_workspace_status(f"RenderLayer Camera: {self.status_label()} (ESC to cancel)")
            _tag_ui_redraw()

            self.awaiting_job = True
            try:
                res = self._invoke_render_fn(self.scene_name, self.animation)
            except Exception as exc:
                self.awaiting_job = False
                self.errors.append(f"{job.view_layer_name}: {exc}")
                continue

            if "RUNNING_MODAL" in res:
                # Normal async GUI path: wait for render_complete / render_cancel
                return

            if "CANCELLED" in res:
                if self.awaiting_job:
                    self.awaiting_job = False
                    self.cancel_requested = True
                    self._schedule_step(delay=0.1)
                return

            if "FINISHED" in res:
                # Synchronous completion (e.g. test stub or instant finish)
                if self.awaiting_job:
                    self.awaiting_job = False
                    self._record_current_job_completed()
                    self._schedule_step(delay=0.05)
                return

            # Unexpected return set — treat as finished step and advance safely
            if self.awaiting_job:
                self.awaiting_job = False
                self._schedule_step(delay=0.1)
            return

        # All remaining jobs failed validation or were skipped
        self._finalize(cancelled=False)

    def _finalize(self, *, cancelled: bool) -> None:
        """Complete or cancel the batch, merge EXRs if needed, clean up temp files, and restore state."""
        if not self.active:
            return

        self._unregister_timer()

        scene = bpy.data.scenes.get(self.scene_name)
        snapshot = self.snapshot
        temp_dir = self.temp_dir
        multilayer_merge = self.multilayer_merge
        out_path = self.multilayer_output_path
        base_dir = self.base_dir
        layer_exrs = dict(self.layer_exrs)
        rendered_files = list(self.rendered_files)
        skipped = list(self.skipped)
        errors = list(self.errors)

        try:
            if not cancelled and multilayer_merge:
                if not layer_exrs:
                    if not errors:
                        errors.append("No layers rendered")
                elif scene is not None and out_path:
                    try:
                        merge_warnings = core.merge_multilayer_exrs(layer_exrs, out_path, scene)
                        errors.extend(merge_warnings)
                    except Exception as exc:
                        errors.append(f"Failed to merge Multilayer EXR: {exc}")
        finally:
            if snapshot is not None:
                try:
                    snapshot.restore()
                except Exception:
                    pass
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

            # Build final summary before resetting
            if cancelled:
                done_count = len(layer_exrs) if multilayer_merge else len(rendered_files)
                status = f"Batch render cancelled ({done_count}/{len(self.jobs)} layer(s) completed)."
                level = "WARNING"
            elif multilayer_merge:
                if errors:
                    status = f"Multilayer EXR finished with warnings: {' | '.join(errors)}"
                    level = "WARNING" if layer_exrs else "ERROR"
                else:
                    status = f"Merged {len(layer_exrs)} View Layer(s) -> {out_path}"
                    if skipped:
                        status += f" (skipped without camera: {', '.join(skipped)})"
                    level = "INFO"
            else:
                if errors:
                    status = f"Rendered {len(rendered_files)} layer(s) with errors: {' | '.join(errors)}"
                    level = "WARNING" if rendered_files else "ERROR"
                else:
                    status = f"Rendered {len(rendered_files)} View Layer(s) to {base_dir}"
                    if skipped:
                        status += f" (skipped without camera: {', '.join(skipped)})"
                    level = "INFO"

            self._reset_state()
            self.last_status = status
            self.last_status_level = level

            _set_workspace_status(None)
            _tag_ui_redraw()
            print(f"[RenderLayer Camera] {status}")

    def _reset_state(self) -> None:
        self.active = False
        self.scene_name = ""
        self.base_dir = ""
        self.animation = False
        self.multilayer_merge = False
        self.multilayer_output_path = None
        self.temp_dir = None
        self.stamp_enabled = False
        self.orig_use_stamp = False
        self.snapshot = None
        self.jobs = []
        self.current_index = -1
        self.awaiting_job = False
        self.cancel_requested = False
        self.rendered_files = []
        self.layer_exrs = {}
        self.skipped = []
        self.errors = []


_QUEUE = BatchRenderQueue()


def get_queue() -> BatchRenderQueue:
    """Return the singleton asynchronous batch render queue."""
    return _QUEUE
