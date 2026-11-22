"""Headless Blender 5.3 integration & state-machine tests for RenderLayer Camera."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import bpy
import numpy as np

# Ensure the repository root is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import renderlayer_camera as addon
from renderlayer_camera import core as rlc_core
from renderlayer_camera import queue as rlc_queue


def _load_rgba_mean(path: str):
    """Load an image via Blender and return its mean (R, G, B, A) tuple."""
    img = bpy.data.images.load(path, check_existing=False)
    try:
        arr = np.empty(len(img.pixels), dtype=np.float32)
        img.pixels.foreach_get(arr)
        arr = arr.reshape((-1, 4))
        return tuple(float(x) for x in arr.mean(axis=0))
    finally:
        bpy.data.images.remove(img)


def _setup_two_color_scene():
    """Create a scene with 2 enabled View Layers, 1 disabled View Layer, and 3 cameras."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "FLAT"
    scene.display.shading.color_type = "OBJECT"
    scene.render.resolution_x = 96
    scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    scene.render.image_settings.media_type = "IMAGE"
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("TestWorld")
    scene.world.color = (0.0, 0.0, 0.0)

    # Collection 1: bright red plane under CamRed
    col_red = bpy.data.collections.new("ColRed")
    scene.collection.children.link(col_red)
    bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
    plane_red = bpy.context.active_object
    plane_red.name = "RedPlane"
    plane_red.color = (1.0, 0.0, 0.0, 1.0)
    scene.collection.objects.unlink(plane_red)
    col_red.objects.link(plane_red)

    # Collection 2: bright blue plane under CamBlue
    col_blue = bpy.data.collections.new("ColBlue")
    scene.collection.children.link(col_blue)
    bpy.ops.mesh.primitive_plane_add(size=10, location=(20, 0, 0))
    plane_blue = bpy.context.active_object
    plane_blue.name = "BluePlane"
    plane_blue.color = (0.0, 0.0, 1.0, 1.0)
    scene.collection.objects.unlink(plane_blue)
    col_blue.objects.link(plane_blue)

    # Camera 1 looking straight down at RedPlane
    cam1 = bpy.data.objects.new("CamRed", bpy.data.cameras.new("CamRed"))
    cam1.location = (0, 0, 4)
    cam1.rotation_euler = (0, 0, 0)
    scene.collection.objects.link(cam1)

    # Camera 2 looking straight down at BluePlane
    cam2 = bpy.data.objects.new("CamBlue", bpy.data.cameras.new("CamBlue"))
    cam2.location = (20, 0, 4)
    cam2.rotation_euler = (0, 0, 0)
    scene.collection.objects.link(cam2)

    # Camera 3 (default scene camera looking at empty black space)
    cam0 = bpy.data.objects.new("CamDefault", bpy.data.cameras.new("CamDefault"))
    cam0.location = (-50, 0, 4)
    cam0.rotation_euler = (0, 0, 0)
    scene.collection.objects.link(cam0)
    scene.camera = cam0

    # View Layers with distinct collection visibility
    vl_red = scene.view_layers[0]
    vl_red.name = "Hero Shot"
    vl_blue = scene.view_layers.new("Detail.001")
    vl_disabled = scene.view_layers.new("DisabledLayer")
    vl_disabled.use = False

    vl_red.layer_collection.children["ColBlue"].exclude = True
    vl_blue.layer_collection.children["ColRed"].exclude = True

    vl_red.rlc_settings.camera = cam1
    vl_blue.rlc_settings.camera = cam2
    vl_disabled.rlc_settings.camera = cam0

    return scene, vl_red, vl_blue, vl_disabled, cam0, cam1, cam2


def _setup_cycles_emissive_materials():
    """Assign emissive red/blue materials for fast 1-sample Cycles EXR tests."""
    for obj_name, rgba in (("RedPlane", (1.0, 0.0, 0.0, 1.0)), ("BluePlane", (0.0, 0.0, 1.0, 1.0))):
        ob = bpy.data.objects[obj_name]
        mat = bpy.data.materials.new(f"Mat_{obj_name}")
        nt = mat.node_tree
        nt.nodes.clear()
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = rgba
        em.inputs["Strength"].default_value = 1.0
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
        ob.data.materials.append(mat)


def test_flat_still_render_and_pixels():
    print("[TEST 1] Flat still render (no subfolders, layer name in filename, distinct camera pixels)...")
    scene, vl_red, vl_blue, vl_disabled, cam0, cam1, cam2 = _setup_two_color_scene()
    tmp = tempfile.mkdtemp(prefix="rlc_test1_")
    try:
        out_dir = os.path.join(tmp, "my_renders")  # non-existent dir -> auto-created
        scene.rlc_settings.output_directory = out_dir
        scene.rlc_settings.use_subfolders = False
        scene.rlc_settings.filename_template = "{layer}"

        res = bpy.ops.rlc.render_all_layers(animation=False)
        assert res == {"FINISHED"}, f"Expected FINISHED, got {res}"

        entries = sorted(os.listdir(out_dir))
        print("  Output files:", entries)
        assert entries == ["Detail.001.png", "Hero Shot.png"], f"Unexpected files: {entries}"
        assert all(os.path.isfile(os.path.join(out_dir, e)) for e in entries)

        red_mean = _load_rgba_mean(os.path.join(out_dir, "Hero Shot.png"))
        blue_mean = _load_rgba_mean(os.path.join(out_dir, "Detail.001.png"))
        print(f"  Hero Shot RGBA mean:  {tuple(round(v, 3) for v in red_mean)}")
        print(f"  Detail.001 RGBA mean: {tuple(round(v, 3) for v in blue_mean)}")
        assert red_mean[0] > 0.7 and red_mean[0] > red_mean[2] * 3, f"Expected red image, got {red_mean}"
        assert blue_mean[2] > 0.7 and blue_mean[2] > blue_mean[0] * 3, f"Expected blue image, got {blue_mean}"

        # Confirm full state restoration & collection exclusions preserved
        assert scene.camera == cam0, f"Scene camera not restored: {scene.camera}"
        assert vl_red.use is True and vl_blue.use is True and vl_disabled.use is False
        assert vl_red.layer_collection.children["ColBlue"].exclude is True
        assert vl_blue.layer_collection.children["ColRed"].exclude is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_filename_template_and_stamp_burn_in():
    print("[TEST 2] Custom filename pattern + Burn Layer Name on Image + state restore...")
    scene, vl_red, vl_blue, _, cam0, _, _ = _setup_two_color_scene()
    tmp = tempfile.mkdtemp(prefix="rlc_test2_")
    try:
        scene.frame_set(7)
        scene.rlc_settings.output_directory = tmp
        scene.rlc_settings.filename_template = "{scene}_{layer}_{camera}_{frame}"
        scene.rlc_settings.stamp_layer_name = True
        vl_red.rlc_settings.custom_name = "HeroCustom"
        scene.render.use_stamp = False
        scene.render.stamp_note_text = "ORIGINAL_NOTE"

        res = bpy.ops.rlc.render_all_layers(animation=False)
        assert res == {"FINISHED"}

        entries = sorted(os.listdir(tmp))
        print("  Output files:", entries)
        assert entries == [
            "Scene_Detail.001_CamBlue_0007.png",
            "Scene_HeroCustom_CamRed_0007.png",
        ], entries

        # Confirm stamp settings were cleanly restored
        assert scene.render.use_stamp is False
        assert scene.render.stamp_note_text == "ORIGINAL_NOTE"
        assert scene.camera == cam0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_subfolders_option():
    print("[TEST 3] Optional Subfolder per Layer mode...")
    scene, vl_red, vl_blue, _, _, _, _ = _setup_two_color_scene()
    tmp = tempfile.mkdtemp(prefix="rlc_test3_")
    try:
        scene.rlc_settings.output_directory = tmp
        scene.rlc_settings.use_subfolders = True
        scene.rlc_settings.filename_template = "{layer}"

        res = bpy.ops.rlc.render_all_layers(animation=False)
        assert res == {"FINISHED"}

        assert os.path.isfile(os.path.join(tmp, "Hero Shot", "Hero Shot.png"))
        assert os.path.isfile(os.path.join(tmp, "Detail.001", "Detail.001.png"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_animation_render():
    print("[TEST 4] Batch animation render across View Layers...")
    scene, vl_red, vl_blue, _, _, _, _ = _setup_two_color_scene()
    tmp = tempfile.mkdtemp(prefix="rlc_test4_")
    try:
        scene.frame_start = 1
        scene.frame_end = 2
        scene.rlc_settings.output_directory = tmp
        scene.rlc_settings.use_subfolders = False
        scene.rlc_settings.filename_template = "{layer}"

        res = bpy.ops.rlc.render_all_layers(animation=True)
        assert res == {"FINISHED"}

        entries = sorted(os.listdir(tmp))
        print("  Animation files:", entries)
        assert entries == [
            "Detail.001_0001.png",
            "Detail.001_0002.png",
            "Hero Shot_0001.png",
            "Hero Shot_0002.png",
        ], entries
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_multilayer_exr_merge():
    print("[TEST 5] Multilayer EXR merge with multiple passes (Combined + Depth + Normal)...")
    import OpenEXR

    scene, vl_red, vl_blue, _, cam0, _, _ = _setup_two_color_scene()
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    _setup_cycles_emissive_materials()

    tmp = tempfile.mkdtemp(prefix="rlc_test5_")
    try:
        scene.render.image_settings.media_type = "MULTI_LAYER_IMAGE"
        scene.render.image_settings.color_depth = "32"
        scene.render.image_settings.exr_codec = "ZIP"
        vl_red.use_pass_z = True
        vl_red.use_pass_normal = True
        vl_blue.use_pass_z = True
        vl_blue.use_pass_normal = True

        scene.rlc_settings.output_directory = tmp
        scene.rlc_settings.multilayer_filename = "all_cams_merged"

        # Calling render_all_layers automatically dispatches to multilayer merge
        res = bpy.ops.rlc.render_all_layers(animation=False)
        assert res == {"FINISHED"}, res

        out_exr = os.path.join(tmp, "all_cams_merged.exr")
        assert os.path.isfile(out_exr) and os.path.getsize(out_exr) > 0

        with OpenEXR.File(out_exr) as exr:
            all_channels = {}
            for part in exr.parts:
                for ch_name, ch_obj in part.channels.items():
                    all_channels[ch_name] = ch_obj.pixels
            ch_names = sorted(all_channels.keys())
            print("  Merged EXR parts:", len(exr.parts), "channels:", ch_names)
            assert "Hero Shot.Combined" in ch_names, ch_names
            assert "Detail.001.Combined" in ch_names, ch_names
            assert "Hero Shot.Depth.Z" in ch_names, ch_names
            assert "Detail.001.Depth.Z" in ch_names, ch_names
            assert "Hero Shot.Normal.X" in ch_names, ch_names
            assert "Detail.001.Normal.X" in ch_names, ch_names

            red_px = all_channels["Hero Shot.Combined"]
            blue_px = all_channels["Detail.001.Combined"]
            assert float(red_px[:, :, 0].mean()) > 0.7 and float(red_px[:, :, 0].mean()) > float(red_px[:, :, 2].mean()) * 3
            assert float(blue_px[:, :, 2].mean()) > 0.7 and float(blue_px[:, :, 2].mean()) > float(blue_px[:, :, 0].mean()) * 3

        # Verify Blender recognizes the merged EXR as MULTILAYER in the Compositor Image node
        img = bpy.data.images.load(out_exr, check_existing=False)
        comp_tree = bpy.data.node_groups.new("CheckMergedEXR", "CompositorNodeTree")
        inode = comp_tree.nodes.new("CompositorNodeImage")
        inode.image = img
        assert img.type == "MULTILAYER", f"Expected MULTILAYER, got {img.type}"
        inode.layer = "Hero Shot"
        inode.layer = "Detail.001"
        out_sockets = [s.name for s in inode.outputs]
        print("  Compositor Image node sockets for Detail.001:", out_sockets)
        assert "Combined" in out_sockets and "Depth" in out_sockets and "Normal" in out_sockets, out_sockets
        bpy.data.node_groups.remove(comp_tree)
        bpy.data.images.remove(img)

        assert scene.render.image_settings.media_type == "MULTI_LAYER_IMAGE"
        assert scene.camera == cam0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_error_guards_and_single_layer_handler():
    print("[TEST 6] Error guards (unsaved relative path, video on still, missing camera, single-layer handler)...")
    scene, vl_red, vl_blue, _, cam0, cam1, _ = _setup_two_color_scene()

    # 6a. Relative path on unsaved .blend -> clean ERROR
    scene.rlc_settings.output_directory = "//renders/"
    try:
        bpy.ops.rlc.render_all_layers(animation=False)
        raise AssertionError("Expected RuntimeError for relative unsaved path")
    except RuntimeError as exc:
        assert "relative path" in str(exc).lower(), str(exc)

    tmp = tempfile.mkdtemp(prefix="rlc_test6_")
    try:
        # 6b. Video format on still render -> clean ERROR
        scene.rlc_settings.output_directory = tmp
        scene.render.image_settings.media_type = "VIDEO"
        try:
            bpy.ops.rlc.render_all_layers(animation=False)
            raise AssertionError("Expected RuntimeError for video still render")
        except RuntimeError as exc:
            assert "video" in str(exc).lower(), str(exc)

        # 6c. Missing camera with fallback disabled -> skips unassigned layer without crashing
        scene.render.image_settings.media_type = "IMAGE"
        scene.render.image_settings.file_format = "PNG"
        vl_blue.rlc_settings.camera = None
        scene.rlc_settings.fallback_to_scene_camera = False
        res = bpy.ops.rlc.render_all_layers(animation=False)
        assert res == {"FINISHED"}
        assert os.listdir(tmp) == ["Hero Shot.png"], os.listdir(tmp)

        # 6d. Single-layer standard render_pre / render_post handler swaps camera & restores
        scene.camera = cam0
        scene.render.filepath = os.path.join(tmp, "single_f12")
        bpy.ops.render.render(write_still=True)
        assert scene.camera == cam0, f"Single-layer handler failed to restore camera: {scene.camera}"
        single_mean = _load_rgba_mean(os.path.join(tmp, "single_f12.png"))
        assert single_mean[0] > 0.7 and single_mean[0] > single_mean[2] * 3, (
            f"Single-layer handler did not swap to Hero Shot's red camera: {single_mean}"
        )

        # 6e. Utility operators (assign_active_camera, clear_layer_cameras, activate_layer_camera, use_scene_output_path)
        scene.camera = cam1
        assert bpy.ops.rlc.assign_active_camera(all_unassigned=True) == {"FINISHED"}
        assert vl_blue.rlc_settings.camera == cam1
        assert bpy.ops.rlc.activate_layer_camera(layer_name="Detail.001") == {"FINISHED"}
        assert scene.camera == cam1
        assert bpy.ops.rlc.clear_layer_cameras() == {"FINISHED"}
        assert vl_red.rlc_settings.camera is None and vl_blue.rlc_settings.camera is None
        scene.render.filepath = os.path.join(tmp, "render_prefix")
        assert bpy.ops.rlc.use_scene_output_path() == {"FINISHED"}
        assert os.path.normpath(scene.rlc_settings.output_directory) == os.path.normpath(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rename_persistence_and_reregister():
    print("[TEST 7] ViewLayer rename persistence & clean unregister/re-register...")
    scene, vl_red, _, _, _, cam1, _ = _setup_two_color_scene()
    vl_red.name = "Renamed Hero"
    assert vl_red.rlc_settings.camera == cam1

    addon.unregister()
    assert not hasattr(bpy.types.ViewLayer, "rlc_settings")
    assert not hasattr(bpy.types.Scene, "rlc_settings")
    assert addon._on_render_pre not in bpy.app.handlers.render_pre
    assert addon._on_render_complete not in bpy.app.handlers.render_complete
    assert addon._on_render_cancel not in bpy.app.handlers.render_cancel
    assert not rlc_queue.get_queue().is_active()

    addon.register()
    assert hasattr(bpy.types.ViewLayer, "rlc_settings")
    assert hasattr(bpy.types.Scene, "rlc_settings")
    assert addon._on_render_pre in bpy.app.handlers.render_pre
    assert addon._on_render_complete in bpy.app.handlers.render_complete


def test_async_queue_state_machine_and_cancellation():
    print("[TEST 8] Async BatchRenderQueue state machine (progression, guards, ESC cancel, EXR cleanup)...")
    scene, vl_red, vl_blue, vl_disabled, cam0, cam1, cam2 = _setup_two_color_scene()
    tmp = tempfile.mkdtemp(prefix="rlc_test8_")
    try:
        # --- 8a. Multi-layer normal async progression & full state restoration ---
        q = rlc_queue.BatchRenderQueue()
        invoked_layers = []
        job_running_flag = {"running": False}

        def fake_invoke(scene_name: str, animation: bool):
            sc = bpy.data.scenes[scene_name]
            enabled = [vl.name for vl in sc.view_layers if vl.use]
            invoked_layers.append((enabled, sc.camera.name if sc.camera else None, sc.render.filepath, animation))
            job_running_flag["running"] = True
            return {"RUNNING_MODAL"}

        q._invoke_render_fn = fake_invoke
        q._job_running_fn = lambda: job_running_flag["running"]

        orig_filepath = scene.render.filepath
        scene.render.use_stamp = False
        scene.render.stamp_note_text = "KEEP_ME"

        jobs = [
            rlc_queue.RenderJobItem("Hero Shot", "CamRed", tmp, os.path.join(tmp, "Hero Shot")),
            rlc_queue.RenderJobItem("Detail.001", "CamBlue", tmp, os.path.join(tmp, "Detail.001")),
        ]

        started, msg = q.start(
            bpy.context,
            scene,
            jobs,
            base_dir=tmp,
            animation=False,
            stamp_enabled=True,
            orig_use_stamp=False,
        )
        assert started is True, msg
        assert q.is_active() is True
        assert q.awaiting_job is True
        assert q.current_index == 0
        assert "1/2" in q.status_label() and "Hero Shot" in q.status_label()
        assert len(invoked_layers) == 1
        assert invoked_layers[0][0] == ["Hero Shot"] and invoked_layers[0][1] == "CamRed"
        assert scene.render.use_stamp is True and scene.render.stamp_note_text == "Hero Shot"

        # Concurrent start guard while batch is active
        started2, msg2 = q.start(bpy.context, scene, jobs, base_dir=tmp)
        assert started2 is False and "already running" in msg2.lower(), (started2, msg2)

        # Layer 1 completes -> schedules timer; while WM render job is still tearing down, _timer_step defers
        q.on_render_complete(scene)
        assert q.awaiting_job is False
        assert q._timer_step() == 0.1, "Expected timer to wait while render job is still active"
        assert len(invoked_layers) == 1

        # Once render job clears, _timer_step launches Layer 2
        job_running_flag["running"] = False
        assert q._timer_step() is None
        assert q.current_index == 1 and q.awaiting_job is True
        assert len(invoked_layers) == 2
        assert invoked_layers[1][0] == ["Detail.001"] and invoked_layers[1][1] == "CamBlue"
        assert scene.render.stamp_note_text == "Detail.001"

        # Layer 2 completes -> finalizes and restores ALL scene state
        q.on_render_complete(scene)
        job_running_flag["running"] = False
        assert q._timer_step() is None
        assert q.is_active() is False
        assert q.last_status_level == "INFO"
        assert "2 View Layer" in q.last_status, q.last_status
        assert scene.camera == cam0
        assert scene.render.filepath == orig_filepath
        assert scene.render.use_stamp is False
        assert scene.render.stamp_note_text == "KEEP_ME"
        assert vl_red.use is True and vl_blue.use is True and vl_disabled.use is False

        # --- 8b. Guard against starting when another Blender render job is already running ---
        job_running_flag["running"] = True
        started_busy, msg_busy = q.start(bpy.context, scene, jobs, base_dir=tmp)
        assert started_busy is False and "another blender render job" in msg_busy.lower(), (started_busy, msg_busy)
        job_running_flag["running"] = False

        # --- 8c. ESC cancellation (on_render_cancel) mid-batch halts queue & restores state ---
        invoked_layers.clear()
        started, _ = q.start(
            bpy.context,
            scene,
            jobs,
            base_dir=tmp,
            stamp_enabled=True,
            orig_use_stamp=False,
        )
        assert started is True and len(invoked_layers) == 1
        q.on_render_cancel(scene)
        assert q.cancel_requested is True
        job_running_flag["running"] = False
        assert q._timer_step() is None
        assert q.is_active() is False
        assert q.last_status_level == "WARNING"
        assert "cancelled" in q.last_status.lower(), q.last_status
        assert len(invoked_layers) == 1, "Cancelled batch must NOT launch layer 2"
        assert scene.camera == cam0
        assert scene.render.use_stamp is False
        assert scene.render.stamp_note_text == "KEEP_ME"
        assert vl_red.use is True and vl_blue.use is True and vl_disabled.use is False

        # --- 8d. UI cancel button (request_cancel) + Multilayer EXR temp dir cleanup ---
        exr_tmp = tempfile.mkdtemp(prefix="rlc_mlexr_cancel_")
        exr_out = os.path.join(tmp, "should_not_exist.exr")
        exr_jobs = [
            rlc_queue.RenderJobItem(
                "Hero Shot", "CamRed", exr_tmp, os.path.join(exr_tmp, "000_Hero Shot"),
                expected_exr_path=os.path.join(exr_tmp, "000_Hero Shot.exr"),
            ),
            rlc_queue.RenderJobItem(
                "Detail.001", "CamBlue", exr_tmp, os.path.join(exr_tmp, "001_Detail.001"),
                expected_exr_path=os.path.join(exr_tmp, "001_Detail.001.exr"),
            ),
        ]
        scene.render.image_settings.media_type = "IMAGE"
        started, _ = q.start(
            bpy.context,
            scene,
            exr_jobs,
            base_dir=tmp,
            multilayer_merge=True,
            multilayer_output_path=exr_out,
            temp_dir=exr_tmp,
        )
        assert started is True
        assert scene.render.image_settings.media_type == "MULTI_LAYER_IMAGE"
        assert q.request_cancel() is True
        assert "Cancelling" in q.status_label()
        # Simulate current render finishing after cancel request
        q.on_render_complete(scene)
        job_running_flag["running"] = False
        assert q._timer_step() is None
        assert q.is_active() is False
        assert not os.path.exists(exr_tmp), "Temp EXR directory must be deleted on cancel"
        assert not os.path.exists(exr_out), "Merged EXR must not be written when cancelled"
        assert scene.render.image_settings.media_type == "IMAGE", "Original media_type not restored"
        assert scene.camera == cam0

        # --- 8e. abort_and_cleanup() (e.g. file load / unregister) cleans up timers & temp dir ---
        abort_tmp = tempfile.mkdtemp(prefix="rlc_mlexr_abort_")
        started, _ = q.start(
            bpy.context,
            scene,
            jobs,
            base_dir=tmp,
            temp_dir=abort_tmp,
        )
        assert started is True
        q.abort_and_cleanup(restore_state=True)
        assert q.is_active() is False
        assert not os.path.exists(abort_tmp), "Temp dir must be removed on abort_and_cleanup"
        assert scene.camera == cam0
        assert vl_red.use is True and vl_blue.use is True and vl_disabled.use is False
    finally:
        rlc_queue.get_queue().abort_and_cleanup(restore_state=False)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    addon.register()
    try:
        test_flat_still_render_and_pixels()
        test_filename_template_and_stamp_burn_in()
        test_subfolders_option()
        test_animation_render()
        test_multilayer_exr_merge()
        test_error_guards_and_single_layer_handler()
        test_rename_persistence_and_reregister()
        test_async_queue_state_machine_and_cancellation()
        print("ALL 8 TESTS PASSED!")
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
