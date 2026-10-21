bl_info = {
    "name": "Output Manager (DEV)",
    "author": "MD",
    "version": (0, 1, 0),
    "blender": (5, 2, 0),
    "location": "Properties > Output > Output Panel",
    "description": "Global presets for render output filepath, file formats, and encoding settings",
    "category": "Render",
}

import json
import os
import re
from pathlib import Path
import bpy


# --- Storage Helpers ---

def get_presets_filepath() -> Path:
    """Return Path to the global presets JSON file."""
    base_dir = Path(bpy.utils.user_resource('SCRIPTS', path='presets')) / "output_manager"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "presets.json"


def load_presets_from_disk() -> dict:
    """Load presets JSON dictionary from disk."""
    path = get_presets_filepath()
    if not path.exists():
        return {"active_preset": "", "presets": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Output Manager] Failed to load presets: {e}")
        return {"active_preset": "", "presets": []}


def save_presets_to_disk(data: dict) -> None:
    """Save presets JSON dictionary to disk."""
    path = get_presets_filepath()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Output Manager] Failed to save presets: {e}")


def make_unique_name(existing_names: set, base_name: str) -> str:
    """Generate unique name with .001, .002 increments."""
    base = base_name.strip() if base_name else "Output 01"
    if not base:
        base = "Output 01"

    if base not in existing_names:
        return base

    match = re.match(r"^(.*?)\.(\d{3,})$", base)
    if match:
        prefix = match.group(1)
        num = int(match.group(2))
    else:
        prefix = base
        num = 1

    while True:
        candidate = f"{prefix}.{num:03d}"
        if candidate not in existing_names:
            return candidate
        num += 1


# --- Setting Capture & Restore ---

def capture_current_output_settings(scene) -> dict:
    """Capture current filepath and format settings from scene."""
    render = scene.render
    img = render.image_settings

    media_type = getattr(img, 'media_type', 'IMAGE')
    file_format = img.file_format

    format_settings = {
        'media_type': media_type,
        'file_format': file_format,
        'color_mode': getattr(img, 'color_mode', 'RGB'),
        'color_depth': getattr(img, 'color_depth', '8'),
        'compression': getattr(img, 'compression', 15),
        'quality': getattr(img, 'quality', 90),
    }

    if hasattr(img, 'exr_codec') and 'OPEN_EXR' in file_format:
        format_settings['exr_codec'] = img.exr_codec

    if hasattr(img, 'views_format'):
        format_settings['views_format'] = img.views_format

    if file_format == 'FFMPEG':
        ff = render.ffmpeg
        format_settings['ffmpeg'] = {
            'format': getattr(ff, 'format', 'QUICKTIME'),
            'codec': getattr(ff, 'codec', 'H264'),
            'constant_rate_factor': getattr(ff, 'constant_rate_factor', 'MEDIUM'),
            'audio_codec': getattr(ff, 'audio_codec', 'AAC'),
        }

    # Format badge for UI display: [Media Type] • [Codec / Format]
    media_label = "Image"
    if media_type == 'MULTI_LAYER_IMAGE' or file_format == 'OPEN_EXR_MULTILAYER':
        media_label = "EXR Multi"
    elif media_type == 'VIDEO' or file_format == 'FFMPEG':
        media_label = "Video"

    if file_format == 'FFMPEG':
        ff = render.ffmpeg
        container = getattr(ff, 'format', 'VIDEO').title()
        codec = getattr(ff, 'codec', 'H264')
        fmt_details = f"{container} / {codec}"
    elif 'OPEN_EXR' in file_format:
        codec = getattr(img, 'exr_codec', '')
        fmt_details = f"EXR ({codec})" if codec else "EXR"
    else:
        mode = getattr(img, 'color_mode', '')
        fmt_details = f"{file_format} ({mode})" if mode else file_format

    summary = f"{media_label} • {fmt_details}"

    return {
        'filepath': render.filepath,
        'format_summary': summary,
        'format_settings': format_settings
    }


def apply_output_settings(scene, preset_dict: dict, path_only: bool = False) -> None:
    """Apply preset settings to scene."""
    render = scene.render

    # Always apply filepath
    if 'filepath' in preset_dict:
        render.filepath = preset_dict['filepath']

    if path_only:
        return

    # Apply format settings
    fmt = preset_dict.get('format_settings', {})
    if not fmt:
        return

    img = render.image_settings

    # Set media_type first in Blender 5.2+
    if 'media_type' in fmt and hasattr(img, 'media_type'):
        try:
            img.media_type = fmt['media_type']
        except Exception:
            pass

    if 'file_format' in fmt:
        try:
            img.file_format = fmt['file_format']
        except Exception:
            pass

    for prop in ('color_mode', 'color_depth', 'compression', 'quality', 'exr_codec', 'views_format'):
        if prop in fmt and hasattr(img, prop):
            try:
                setattr(img, prop, fmt[prop])
            except Exception:
                pass

    if fmt.get('file_format') == 'FFMPEG' and 'ffmpeg' in fmt:
        ff = render.ffmpeg
        for k, v in fmt['ffmpeg'].items():
            if hasattr(ff, k):
                try:
                    setattr(ff, k, v)
                except Exception:
                    pass


def is_output_settings_dirty(scene, preset_dict: dict) -> bool:
    """Return True if current scene output settings differ from active preset."""
    if not preset_dict:
        return False

    # Check filepath
    if scene.render.filepath != preset_dict.get('filepath', ''):
        return True

    # Check format
    fmt = preset_dict.get('format_settings', {})
    if not fmt:
        return False

    img = scene.render.image_settings
    if img.file_format != fmt.get('file_format'):
        return True
    if getattr(img, 'color_mode', None) != fmt.get('color_mode'):
        return True
    if getattr(img, 'color_depth', None) != fmt.get('color_depth'):
        return True

    return False


# --- Operators ---

class OUTPUT_MANAGER_OT_preset_add(bpy.types.Operator):
    """Store the current output path and format settings as a global preset"""
    bl_idname = "output_manager.preset_add"
    bl_label = "Store Current Output Preset"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Preset Name", default="")

    def invoke(self, context, event):
        data = load_presets_from_disk()
        active = data.get("active_preset", "")
        existing = {p["name"] for p in data.get("presets", [])}

        # Suggest active preset name by default, or Output 01
        if active and active in existing:
            self.name = active
        else:
            self.name = make_unique_name(existing, "Output 01")
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "name")

    def execute(self, context):
        target_name = self.name.strip()
        data = load_presets_from_disk()
        presets = data.get("presets", [])
        existing_names = {p["name"] for p in presets}

        if not target_name:
            target_name = make_unique_name(existing_names, "Output 01")

        # Check for collision -> prompt overwrite
        if target_name in existing_names:
            return bpy.ops.output_manager.confirm_overwrite('INVOKE_DEFAULT', name=target_name)

        # Save new preset
        captured = capture_current_output_settings(context.scene)
        presets.append({
            'name': target_name,
            'filepath': captured['filepath'],
            'format_summary': captured['format_summary'],
            'format_settings': captured['format_settings']
        })

        data['presets'] = presets
        data['active_preset'] = target_name
        save_presets_to_disk(data)

        self.report({'INFO'}, f"Saved output preset '{target_name}'")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_confirm_overwrite(bpy.types.Operator):
    """Confirm whether to overwrite an existing output preset or save with a unique name"""
    bl_idname = "output_manager.confirm_overwrite"
    bl_label = "Preset Already Exists"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    name: bpy.props.StringProperty(name="Name")
    action: bpy.props.EnumProperty(
        name="Action",
        items=[
            ('OVERWRITE', "Overwrite", "Overwrite the existing preset with current output settings"),
            ('UNIQUE', "Save as New", "Keep existing and save with a unique name (.001)"),
        ],
        default='OVERWRITE'
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        data = load_presets_from_disk()
        existing = {p["name"] for p in data.get("presets", [])}
        unique_name = make_unique_name(existing, self.name)

        col = layout.column(align=True)
        col.label(text=f"Preset '{self.name}' already exists.", icon='QUESTION')
        col.separator()
        col.prop(self, "action", expand=True)

        if self.action == 'UNIQUE':
            col.separator()
            col.label(text=f"Will be saved as: '{unique_name}'", icon='INFO')

    def execute(self, context):
        data = load_presets_from_disk()
        presets = data.get("presets", [])
        captured = capture_current_output_settings(context.scene)

        if self.action == 'OVERWRITE':
            found = False
            for p in presets:
                if p["name"] == self.name:
                    p['filepath'] = captured['filepath']
                    p['format_summary'] = captured['format_summary']
                    p['format_settings'] = captured['format_settings']
                    found = True
                    break
            if not found:
                presets.append({
                    'name': self.name,
                    'filepath': captured['filepath'],
                    'format_summary': captured['format_summary'],
                    'format_settings': captured['format_settings']
                })
            data['active_preset'] = self.name
            save_presets_to_disk(data)
            self.report({'INFO'}, f"Overwrote output preset '{self.name}'")
            return {'FINISHED'}
        else:
            existing = {p["name"] for p in presets}
            target_name = make_unique_name(existing, self.name)
            presets.append({
                'name': target_name,
                'filepath': captured['filepath'],
                'format_summary': captured['format_summary'],
                'format_settings': captured['format_settings']
            })
            data['active_preset'] = target_name
            save_presets_to_disk(data)
            self.report({'INFO'}, f"Saved output preset '{target_name}'")
            return {'FINISHED'}


class OUTPUT_MANAGER_OT_preset_apply(bpy.types.Operator):
    """Apply the selected output preset to current scene"""
    bl_idname = "output_manager.preset_apply"
    bl_label = "Apply Output Preset"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Preset Name")
    path_only: bpy.props.BoolProperty(name="Path Only", default=False)

    def execute(self, context):
        data = load_presets_from_disk()
        presets = data.get("presets", [])
        target = next((p for p in presets if p["name"] == self.name), None)

        if not target:
            self.report({'WARNING'}, f"Preset '{self.name}' not found")
            return {'CANCELLED'}

        apply_output_settings(context.scene, target, path_only=self.path_only)
        data['active_preset'] = self.name
        save_presets_to_disk(data)

        mode_str = " (Path Only)" if self.path_only else " (All Settings)"
        self.report({'INFO'}, f"Applied output preset '{self.name}'{mode_str}")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_preset_remove(bpy.types.Operator):
    """Delete an output preset"""
    bl_idname = "output_manager.preset_remove"
    bl_label = "Delete Output Preset"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Preset Name")

    def execute(self, context):
        data = load_presets_from_disk()
        presets = data.get("presets", [])
        data['presets'] = [p for p in presets if p["name"] != self.name]

        if data.get("active_preset") == self.name:
            data['active_preset'] = data['presets'][0]["name"] if data['presets'] else ""

        save_presets_to_disk(data)
        self.report({'INFO'}, f"Removed output preset '{self.name}'")
        return {'FINISHED'}


# --- UI Popover & Panel Integration ---

class OUTPUT_MANAGER_PT_presets_popover(bpy.types.Panel):
    """Global Output Presets Popover"""
    bl_label = "Output Presets"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_idname = "OUTPUT_MANAGER_PT_presets_popover"

    def draw(self, context):
        layout = self.layout
        layout.ui_units_x = 21

        data = load_presets_from_disk()
        active_name = data.get("active_preset", "")
        presets = data.get("presets", [])

        col = layout.column(align=True)
        col.operator("output_manager.preset_add", text="Store Current Output", icon='ADD')

        col.separator()

        if not presets:
            box = col.box()
            box.label(text="No stored presets yet", icon='INFO')
            return

        for p in presets:
            is_active = (p["name"] == active_name)
            box = col.box()

            # Row 1: Active Tickmark + Name + Format/Media Tag + Delete
            r1 = box.row(align=True)
            apply_all_icon = r1.operator(
                "output_manager.preset_apply",
                text="",
                icon='CHECKMARK' if is_active else 'BLANK1'
            )
            apply_all_icon.name = p["name"]
            apply_all_icon.path_only = False

            r1.label(text=p["name"])

            fmt_tag = p.get("format_summary", "")
            if fmt_tag:
                r1.label(text=fmt_tag)

            del_op = r1.operator("output_manager.preset_remove", text="", icon='X')
            del_op.name = p["name"]

            # Row 2: Filepath preview + Apply All button + Path Only button
            r2 = box.row(align=True)
            raw_path = p.get("filepath", "")
            disp_path = raw_path[:22] + "..." if len(raw_path) > 22 else raw_path
            r2.label(text=disp_path if disp_path else "(empty)")

            all_btn = r2.operator("output_manager.preset_apply", text="Apply All")
            all_btn.name = p["name"]
            all_btn.path_only = False

            path_btn = r2.operator("output_manager.preset_apply", text="Path")
            path_btn.name = p["name"]
            path_btn.path_only = True


def draw_output_manager_header(self, context):
    """Draw Output Manager preset popover header right at the top of the Output panel."""
    layout = self.layout
    scene = context.scene

    data = load_presets_from_disk()
    active_name = data.get("active_preset", "")
    presets = data.get("presets", [])
    active_preset = next((p for p in presets if p["name"] == active_name), None)

    is_dirty = False
    if active_preset:
        is_dirty = is_output_settings_dirty(scene, active_preset)

    row = layout.row(align=True)
    if active_name:
        prefix = "* " if is_dirty else ""
        label = f"{prefix}{active_name}"
    else:
        label = "Output Presets"

    disp_label = label[:20] + "..." if len(label) > 20 else label

    row.popover(
        panel="OUTPUT_MANAGER_PT_presets_popover",
        text=disp_label,
        icon='PRESET',
    )
    add_op = row.operator("output_manager.preset_add", text="", icon='ADD')


# --- Registration ---

classes = (
    OUTPUT_MANAGER_OT_preset_add,
    OUTPUT_MANAGER_OT_confirm_overwrite,
    OUTPUT_MANAGER_OT_preset_apply,
    OUTPUT_MANAGER_OT_preset_remove,
    OUTPUT_MANAGER_PT_presets_popover,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    if hasattr(bpy.types, "RENDER_PT_output"):
        bpy.types.RENDER_PT_output.prepend(draw_output_manager_header)


def unregister():
    if hasattr(bpy.types, "RENDER_PT_output"):
        try:
            bpy.types.RENDER_PT_output.remove(draw_output_manager_header)
        except Exception:
            pass

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
