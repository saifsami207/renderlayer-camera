# RenderLayer Camera

[![Blender 5.3+](https://img.shields.io/badge/Blender-5.3%2B-E87D0D?logo=blender&logoColor=white)](https://www.blender.org/)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPL--3.0--or--later-blue.svg)](LICENSE)

A **Blender 5.3+** extension by **Saif Sami** that lets you assign a dedicated camera to every **View Layer** and batch-render all enabled layers directly into your chosen output folder — with each layer's name in the filename, optional burn-in image stamp, and a non-blocking Render window queue.

---

## Inspiration & Credits

**RenderLayer Camera** is inspired by [**BB-Camera-Layer**](https://github.com/riouxr/BB-Camera-Layer) by *riouxr*.

Built from the ground up for Blender 5.3+ as a native Blender Extension, RenderLayer Camera adds and fixes:

- **Dedicated Output Folder Picker**: Choose your output folder directly in the panel (supports `//` blend-relative paths, an **Open Folder** shortcut, and automatic directory creation).
- **Flat Output by Default (No Forced Subfolders)**: Every View Layer renders directly into your chosen output folder as `<LayerName>.<ext>` (with an optional *Subfolder per Layer* toggle under *More Options*).
- **Layer Name in Every Photo**: Customizable filename tokens (`{layer}`, `{camera}`, `{scene}`, `{frame}`) with a live path preview, plus an optional **Burn Name on Image** metadata stamp toggle.
- **Zero Manual Sync**: Camera assignments are stored directly on `bpy.types.ViewLayer` (`view_layer.rlc_settings`), so adding, deleting, or renaming View Layers never loses assignments.
- **Non-Blocking Render Window Queue**: In GUI mode, **Render All Layers** opens Blender's Render window asynchronously without freezing the UI, displays live progress, and supports pressing **ESC** (or clicking **Cancel Batch Render**) while cleanly restoring all scene state.
- **Native Blender 5.3 Multilayer EXR Merge**: Uses Blender 5.3's bundled `OpenEXR` module to merge all rendered View Layers and all their render passes (`Combined`, `Depth`, `Normal`, etc.) into a single Blender-compatible Multilayer EXR without touching your Compositor or running an extra render pass.

---

## Installation

1. Download `renderlayer_camera-1.0.0.zip` from [**Releases**](https://github.com/saifsami207/renderlayer-camera/releases).
2. In Blender 5.3+: **Edit → Preferences → Get Extensions → ▾ → Install from Disk...**
3. Select the downloaded `.zip` and enable **RenderLayer Camera**.

---

## UI Locations

- **Properties → View Layer → RenderLayer Camera**
- **Properties → Output → RenderLayer Camera**
- **Topbar → Render → Render All View Layers / Render All View Layers (Animation)**

---

## Usage

1. **Assign Cameras**: Open **Properties → View Layer → RenderLayer Camera**, pick a camera for each View Layer (or click the eyedropper to assign the active Scene Camera), and toggle each layer's render checkbox (`vl.use`).
2. **Choose Output Folder & Filename**: Set **Output Folder** (leave blank to use Blender's Scene Output folder). Customize **Filename** using `{layer}`, `{camera}`, `{scene}`, `{frame}` (default: `{layer}`), and optionally toggle **Burn Name on Image**.
3. **Render**:
   - Click **Render All Layers** (Still) or **Animation** — Blender opens the Render window and renders each layer sequentially without freezing. Press **ESC** or click **Cancel Batch Render** to stop cleanly.
   - Toggle **F12 All Layers** to remap `F12` to batch-render all enabled layers. When off, standard `F12` still renders the active View Layer through its assigned camera and restores your Scene Camera afterward.
   - Set **Output → Media Type** to **Multi-Layer Image** to use **Render & Merge Multilayer EXR** (or **Render Separate EXR per Layer**).
   - Toggle **Sync Cam** to automatically switch the active Scene Camera when switching View Layers.

---

## Development & Testing

Requires **Blender 5.3.0+**:

```powershell
# Validate manifest & source
& "C:\Program Files\Blender Foundation\Blender 5.3\blender.exe" --factory-startup --command extension validate renderlayer_camera

# Build installable .zip into dist/
& "C:\Program Files\Blender Foundation\Blender 5.3\blender.exe" --factory-startup --command extension build --source-dir renderlayer_camera --output-dir dist

# Run headless integration test suite
& "C:\Program Files\Blender Foundation\Blender 5.3\blender.exe" -b --factory-startup --python-exit-code 1 -P tests/test_addon.py
```

---

## Author

**Saif Sami**

## License

Licensed under the **GNU General Public License v3.0 or later** (`GPL-3.0-or-later`). See [LICENSE](LICENSE).
