bl_info = {
    "name": "Output Manager (DEV)",
    "author": "MD",
    "version": (0, 1, 0),
    "blender": (5, 2, 0),
    "location": "Properties > Output > Output Panel",
    "description": "Global presets for render output filepath, file formats, encoding settings, and Compositor File Output nodes",
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


# --- Setting Capture & Restore Helpers ---

def format_summary_from_rna(img, ff=None) -> str:
    """Generate a clean format badge string: [Media Label] • [Format / Codec]."""
    media_type = getattr(img, 'media_type', 'IMAGE')
    file_format = getattr(img, 'file_format', 'PNG')

    media_label = "Image"
    if media_type == 'MULTI_LAYER_IMAGE' or file_format == 'OPEN_EXR_MULTILAYER':
        media_label = "EXR Multi"
    elif media_type == 'VIDEO' or file_format == 'FFMPEG':
        media_label = "Video"

    if file_format == 'FFMPEG' and ff:
        container = getattr(ff, 'format', 'VIDEO').title()
        codec = getattr(ff, 'codec', 'H264')
        fmt_details = f"{container} / {codec}"
    elif 'OPEN_EXR' in file_format:
        codec = getattr(img, 'exr_codec', '')
        fmt_details = f"EXR ({codec})" if codec else "EXR"
    else:
        mode = getattr(img, 'color_mode', '')
        fmt_details = f"{file_format} ({mode})" if mode else file_format

    return f"{media_label} • {fmt_details}"


def apply_format_settings_to_target(target_format, fmt: dict) -> None:
    """Apply format settings dictionary to an ImageFormatSettings or NodeFileFormat object."""
    if not fmt or not target_format:
        return

    # Set media_type first (Blender 5.2+)
    if 'media_type' in fmt and hasattr(target_format, 'media_type'):
        try:
            target_format.media_type = fmt['media_type']
        except Exception:
            pass

    if 'file_format' in fmt and hasattr(target_format, 'file_format'):
        try:
            target_format.file_format = fmt['file_format']
        except Exception:
            pass

    for prop in ('color_mode', 'color_depth', 'compression', 'quality', 'exr_codec', 'views_format'):
        if prop in fmt and hasattr(target_format, prop):
            try:
                setattr(target_format, prop, fmt[prop])
            except Exception:
                pass


def extract_version_info(filepath: str) -> dict:
    """
    Extract active version number, padding, and prefix from a filepath.
    Defaults to version 1 with 'v' prefix ('v001') if no version number is found.
    """
    if not filepath or not filepath.strip():
        return {'val': 1, 'pad': 3, 'has_v': False, 'prefix_char': 'v'}

    pattern = re.compile(r'(^|[^a-zA-Z0-9])([vV])?(\d+)(?=[^a-zA-Z0-9]|$)')
    matches = list(pattern.finditer(filepath))
    if not matches:
        return {'val': 1, 'pad': 3, 'has_v': False, 'prefix_char': 'v'}

    v_matches = [m for m in matches if m.group(2)]
    if v_matches:
        target = v_matches[-1]
        return {
            'val': int(target.group(3)),
            'pad': len(target.group(3)),
            'has_v': True,
            'prefix_char': target.group(2)
        }

    # If no explicit 'v' found, search filename part
    last_slash = max(filepath.rfind('/'), filepath.rfind('\\'))
    file_part = filepath[last_slash + 1:] if last_slash != -1 else filepath
    file_base = re.sub(r'#+$', '', file_part)
    file_base = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', file_base)

    file_matches = list(pattern.finditer(file_base))
    if file_matches:
        target = file_matches[-1]
        return {
            'val': int(target.group(3)),
            'pad': len(target.group(3)),
            'has_v': bool(target.group(2)),
            'prefix_char': target.group(2) or ''
        }

    return {'val': 1, 'pad': 3, 'has_v': False, 'prefix_char': 'v'}


def find_highest_version_on_disk(filepath: str) -> int:
    """
    Inspect disk to find the highest existing version number for a given filepath template.
    Returns 0 if no matching versioned files or directories exist on disk.
    """
    if not filepath or not filepath.strip():
        return 0

    # Expand relative path if blend file is saved
    if filepath.startswith('//'):
        try:
            if not bpy.data.is_saved:
                return 0
            abs_p = bpy.path.abspath(filepath)
        except Exception:
            return 0
    else:
        abs_p = filepath

    # Expand basic scene/blend tokens for disk checking if present
    try:
        scene = getattr(bpy.context, 'scene', None) or (bpy.data.scenes[0] if bpy.data.scenes else None)
        if scene and '{scene_name}' in abs_p:
            abs_p = abs_p.replace('{scene_name}', scene.name)
        if bpy.data.is_saved and '{blend}' in abs_p:
            abs_p = abs_p.replace('{blend}', Path(bpy.data.filepath).stem)
    except Exception:
        pass

    try:
        clean_path = re.sub(r'#+$', '', abs_p.strip()).replace('\\', '/')
    except Exception:
        return 0

    if clean_path.endswith('/'):
        dir_part = clean_path[:-1]
        file_part = ''
    else:
        last_slash = clean_path.rfind('/')
        if last_slash != -1:
            dir_part = clean_path[:last_slash]
            file_part = clean_path[last_slash + 1:]
        else:
            dir_part = '.'
            file_part = clean_path

    versions_found = set()
    v_pattern = re.compile(r'(^|[^a-zA-Z0-9])([vV])(\d+)(?=[^a-zA-Z0-9]|$)')

    # 1. Scan directory segments for version tokens (e.g. /v002/beauty)
    parts = dir_part.split('/')
    for i, part in enumerate(parts):
        m = v_pattern.search(part)
        if m:
            parent_dir = '/'.join(parts[:i]) if i > 0 else '.'
            prefix = part[:m.start(2)]
            if os.path.isdir(parent_dir):
                try:
                    for entry in os.scandir(parent_dir):
                        if entry.is_dir():
                            em = v_pattern.search(entry.name)
                            if em and entry.name.startswith(prefix):
                                versions_found.add(int(em.group(3)))
                except Exception:
                    pass

    # 2. Scan file_part versions
    if file_part:
        base_no_ext = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', file_part)
        fm = v_pattern.search(base_no_ext)
        if fm:
            file_prefix = base_no_ext[:fm.start(2)]
            target_dirs = []
            if os.path.isdir(dir_part):
                target_dirs.append(dir_part)
            for i, part in enumerate(parts):
                m = v_pattern.search(part)
                if m:
                    parent_dir = '/'.join(parts[:i]) if i > 0 else '.'
                    sub_path = '/'.join(parts[i+1:])
                    if os.path.isdir(parent_dir):
                        try:
                            for entry in os.scandir(parent_dir):
                                if entry.is_dir() and v_pattern.search(entry.name):
                                    candidate = os.path.join(entry.path, sub_path).replace('\\', '/')
                                    if os.path.isdir(candidate):
                                        target_dirs.append(candidate)
                        except Exception:
                            pass

            for d in set(target_dirs):
                try:
                    for entry in os.scandir(d):
                        if entry.is_file():
                            em = v_pattern.search(entry.name)
                            if em and entry.name.startswith(file_prefix):
                                versions_found.add(int(em.group(3)))
                except Exception:
                    pass

    return max(versions_found) if versions_found else 0


def adapt_preset_path(current_path: str, preset_path: str, target_file_format: str = '', check_disk: bool = True) -> str:
    """
    Intelligently adapt incoming preset path to keep current scene/node version,
    defaulting to 1 if no version was present or no prior versions exist on disk,
    and sanitizing image vs video extensions.
    """
    if not preset_path or not preset_path.strip():
        return preset_path

    ver_info = extract_version_info(current_path)
    current_ver = ver_info['val']
    pad = ver_info['pad']
    has_explicit_ver = ver_info.get('has_v', False)

    if check_disk:
        highest_disk_preset = find_highest_version_on_disk(preset_path)
        highest_disk_current = find_highest_version_on_disk(current_path) if current_path else 0
        highest_disk_ver = max(highest_disk_preset, highest_disk_current)

        if highest_disk_ver == 0:
            val = 1
        elif not has_explicit_ver:
            val = highest_disk_ver + 1
        else:
            if current_ver > highest_disk_ver + 1:
                val = highest_disk_ver + 1
            else:
                val = max(1, current_ver)
    else:
        val = current_ver

    pattern = re.compile(r'(^|[^a-zA-Z0-9])([vV])?(\d+)(?=[^a-zA-Z0-9]|$)')
    matches = list(pattern.finditer(preset_path))
    v_matches = [m for m in matches if m.group(2)]

    if v_matches:
        to_replace = []
        target_digits_candidates = {m.group(3) for m in v_matches}
        for m in matches:
            if m.group(2) or m.group(3) in target_digits_candidates:
                to_replace.append(m)

        new_path = preset_path
        for m in reversed(to_replace):
            pref = m.group(2) or ''
            target_pad = max(pad, len(m.group(3)))
            new_digits = f"{pref}{val:0{target_pad}d}" if pref else f"{val:0{target_pad}d}"
            start_idx = m.start(2) if m.group(2) else m.start(3)
            end_idx = m.end(3)
            new_path = new_path[:start_idx] + new_digits + new_path[end_idx:]
    elif matches:
        new_path = preset_path
        for m in reversed(matches):
            target_pad = max(pad, len(m.group(3)))
            new_digits = f"{val:0{target_pad}d}"
            start_idx = m.start(3)
            end_idx = m.end(3)
            new_path = new_path[:start_idx] + new_digits + new_path[end_idx:]
    else:
        new_path = preset_path

    # Format-aware sanitization
    if target_file_format == 'FFMPEG':
        # Video: strip trailing frame tokens and image extensions
        new_path = re.sub(r'#+$', '', new_path)
        new_path = re.sub(r'\.(exr|png|jpg|jpeg|tga|tif|tiff|dpx|bmp|cin|hdr|webp)$', '', new_path, flags=re.IGNORECASE)
        new_path = re.sub(r'#+$', '', new_path)
        if new_path.endswith('_') and not new_path.endswith(('/_', '\\_')):
            new_path = new_path[:-1]
    elif target_file_format in ('OPEN_EXR', 'OPEN_EXR_MULTILAYER', 'PNG', 'TARGA', 'TIFF', 'JPEG', 'BMP', 'CINEON', 'DPX', 'HDR', 'WEBP'):
        # Image sequence: strip video extensions
        new_path = re.sub(r'\.(mp4|mov|mkv|avi|webm)$', '', new_path, flags=re.IGNORECASE)
        # Ensure trailing frame separator if ends on alphanumeric
        if not new_path.endswith(('/', '\\', '_', '.', '#')) and not re.search(r'\.[a-zA-Z0-9]{2,4}$', new_path):
            new_path += '_'

    return new_path


def check_path_overwrite_status(filepath: str, file_format: str = '') -> bool:
    """Check if rendered output file(s) already exist on disk for the given filepath."""
    if not filepath or not filepath.strip():
        return False
    try:
        abs_p = bpy.path.abspath(filepath)
    except Exception:
        return False

    if file_format == 'FFMPEG':
        clean = re.sub(r'#+$', '', abs_p)
        if os.path.isfile(clean):
            return True
        base_no_ext = os.path.splitext(clean)[0]
        for ext in ('.mp4', '.mov', '.mkv', '.avi', '.webm'):
            if os.path.isfile(base_no_ext + ext):
                return True
        return False
    else:
        # Check if abs_p is a directory (typical in Compositor File Output nodes)
        if abs_p.endswith(('/', '\\')):
            if os.path.isdir(abs_p):
                try:
                    with os.scandir(abs_p) as it:
                        for entry in it:
                            if entry.is_file():
                                return True
                except Exception:
                    pass
            return False

        d, prefix = os.path.split(abs_p)
        prefix_clean = re.sub(r'#+$', '', prefix)
        prefix_clean = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', prefix_clean)

        if not os.path.isdir(d):
            return False

        if prefix_clean:
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        if entry.is_file() and entry.name.startswith(prefix_clean):
                            return True
            except Exception:
                pass
        return False


def increment_path_version(filepath: str) -> tuple[str, bool]:
    """Find version number occurrences across folder and filename, incrementing all in sync."""
    if not filepath or not filepath.strip():
        return filepath, False

    pattern = re.compile(r'(^|[^a-zA-Z0-9])([vV])?(\d+)(?=[^a-zA-Z0-9]|$)')
    matches = list(pattern.finditer(filepath))
    if not matches:
        return filepath, False

    v_matches = [m for m in matches if m.group(2)]
    if v_matches:
        max_val = max(int(m.group(3)) for m in v_matches)
        target_digits_candidates = {m.group(3) for m in v_matches}

        to_replace = []
        for m in matches:
            if m.group(2):
                to_replace.append(m)
            elif m.group(3) in target_digits_candidates:
                to_replace.append(m)

        next_val = max_val + 1
        new_path = filepath
        for m in reversed(to_replace):
            orig_digits = m.group(3)
            pad = len(orig_digits)
            new_digits = f"{next_val:0{pad}d}"
            start_idx = m.start(3)
            end_idx = m.end(3)
            new_path = new_path[:start_idx] + new_digits + new_path[end_idx:]
        return new_path, True

    last_slash = max(filepath.rfind('/'), filepath.rfind('\\'))
    file_part = filepath[last_slash + 1:] if last_slash != -1 else filepath

    file_base = file_part
    file_base = re.sub(r'#+$', '', file_base)
    file_base = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', file_base)

    file_matches = list(pattern.finditer(file_base))
    if file_matches:
        target_digits = file_matches[-1].group(3)
    else:
        target_digits = matches[-1].group(3)

    target_val = int(target_digits)
    next_val = target_val + 1
    pad = len(target_digits)

    to_replace = [m for m in matches if m.group(3) == target_digits]
    new_path = filepath
    for m in reversed(to_replace):
        new_digits = f"{next_val:0{pad}d}"
        start_idx = m.start(3)
        end_idx = m.end(3)
        new_path = new_path[:start_idx] + new_digits + new_path[end_idx:]

    return new_path, True


def capture_current_output_settings(scene) -> dict:
    """Capture current filepath and format settings from scene render settings."""
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

    summary = format_summary_from_rna(img, getattr(render, 'ffmpeg', None))

    return {
        'filepath': render.filepath,
        'format_summary': summary,
        'format_settings': format_settings
    }


def apply_output_settings(scene, preset_dict: dict) -> None:
    """Apply preset output settings and filepath to scene render settings with intelligent version adaptation."""
    render = scene.render
    fmt = preset_dict.get('format_settings', {})

    if 'filepath' in preset_dict:
        target_fmt = fmt.get('file_format', '')
        adapted_path = adapt_preset_path(render.filepath, preset_dict['filepath'], target_file_format=target_fmt)
        render.filepath = adapted_path

    if not fmt:
        return

    apply_format_settings_to_target(render.image_settings, fmt)

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

    # Check format first
    fmt = preset_dict.get('format_settings', {})
    if fmt:
        img = scene.render.image_settings
        if img.file_format != fmt.get('file_format'):
            return True
        if getattr(img, 'color_mode', None) != fmt.get('color_mode'):
            return True
        if getattr(img, 'color_depth', None) != fmt.get('color_depth'):
            return True

    # Compare adapted path rather than raw preset path
    preset_raw = preset_dict.get('filepath', '')
    if preset_raw:
        target_fmt = fmt.get('file_format', '')
        adapted = adapt_preset_path(scene.render.filepath, preset_raw, target_file_format=target_fmt, check_disk=False)
        if scene.render.filepath != adapted:
            return True

    return False


# --- Compositor File Output Node Helpers ---

def get_file_output_nodes(scene) -> list:
    """Return all Compositor File Output nodes in the active scene."""
    if not scene:
        return []
    tree = getattr(scene, 'compositing_node_group', None) or getattr(scene, 'node_tree', None)
    if not tree or not hasattr(tree, 'nodes'):
        return []
    return [n for n in tree.nodes if getattr(n, 'type', None) == 'OUTPUT_FILE']


def get_node_path_strings(node) -> tuple[str, str, str]:
    """Return (directory, filename, full_path) for a File Output node."""
    dir_str = getattr(node, 'directory', '') or getattr(node, 'base_path', '')
    fn_str = getattr(node, 'file_name', '')
    full_path = f"{dir_str}{fn_str}"
    return dir_str, fn_str, full_path


def set_node_path_strings(node, dir_str: str, file_name_str: str = ""):
    """Set directory and filename properties across Blender versions."""
    if hasattr(node, "directory"):
        node.directory = dir_str
    if hasattr(node, "file_name") and file_name_str:
        node.file_name = file_name_str
    if hasattr(node, "base_path"):
        node.base_path = f"{dir_str}{file_name_str}" if file_name_str else dir_str


def capture_file_output_node_settings(node) -> dict:
    """Capture format and path settings from a File Output node as an Output Manager preset."""
    img = node.format
    file_format = getattr(img, 'file_format', 'PNG')

    format_settings = {
        'media_type': getattr(img, 'media_type', 'IMAGE'),
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

    summary = format_summary_from_rna(img)
    _, _, full_path = get_node_path_strings(node)

    return {
        'filepath': full_path,
        'format_summary': summary,
        'format_settings': format_settings
    }


def apply_preset_to_file_output_node(node, preset_dict: dict, mode: str = 'FORMAT') -> tuple[bool, str]:
    """
    Apply preset to a File Output node with intelligent version preservation.
    Modes:
      - 'FORMAT': apply image format/codec settings only.
      - 'FULL': apply both format settings and base directory.
      - 'PATH': apply directory path only.
    """
    if not node or not preset_dict:
        return False, "Node or preset not found."

    fmt = preset_dict.get('format_settings', {})

    # Check for unsupported video format
    if mode in ('FORMAT', 'FULL') and fmt.get('file_format') == 'FFMPEG':
        return False, "File Output nodes only support image formats (not FFmpeg video)."

    # 1. Apply Format
    if mode in ('FORMAT', 'FULL') and fmt:
        apply_format_settings_to_target(node.format, fmt)

    # 2. Apply Path with intelligent version preservation
    if mode in ('PATH', 'FULL'):
        preset_path = preset_dict.get('filepath', '')
        if preset_path:
            _, _, current_full = get_node_path_strings(node)
            target_fmt = fmt.get('file_format', getattr(node.format, 'file_format', ''))
            adapted = adapt_preset_path(current_full, preset_path, target_file_format=target_fmt)

            if adapted.endswith(('/', '\\')):
                dir_part = adapted
                fn_part = ""
            else:
                last_slash = max(adapted.rfind('/'), adapted.rfind('\\'))
                if last_slash != -1:
                    dir_part = adapted[:last_slash + 1]
                    fn_part = adapted[last_slash + 1:]
                else:
                    dir_part = adapted + "/"
                    fn_part = ""

            set_node_path_strings(node, dir_part, fn_part)

    return True, "Success"


# --- Operators ---

class OUTPUT_MANAGER_OT_preset_add(bpy.types.Operator):
    """Store the current scene output path and format settings as a global preset"""
    bl_idname = "output_manager.preset_add"
    bl_label = "Store Current Output Preset"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Preset Name", default="")

    def invoke(self, context, event):
        data = load_presets_from_disk()
        active = data.get("active_preset", "")
        existing = {p["name"] for p in data.get("presets", [])}

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

        if target_name in existing_names:
            return bpy.ops.output_manager.confirm_overwrite('INVOKE_DEFAULT', name=target_name)

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
    """Apply the selected output preset to current scene render settings"""
    bl_idname = "output_manager.preset_apply"
    bl_label = "Apply Output Preset"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Preset Name")

    def execute(self, context):
        data = load_presets_from_disk()
        presets = data.get("presets", [])
        target = next((p for p in presets if p["name"] == self.name), None)

        if not target:
            self.report({'WARNING'}, f"Preset '{self.name}' not found")
            return {'CANCELLED'}

        apply_output_settings(context.scene, target)
        data['active_preset'] = self.name
        save_presets_to_disk(data)

        self.report({'INFO'}, f"Applied output preset '{self.name}'")
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


class OUTPUT_MANAGER_OT_increment_version(bpy.types.Operator):
    """Increment version number across all instances in the output filepath and active Compositor File Output nodes"""
    bl_idname = "output_manager.increment_version"
    bl_label = "Increment Version (+1)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        current_path = scene.render.filepath
        new_path, ok_main = increment_path_version(current_path)
        if ok_main:
            scene.render.filepath = new_path

        # Also increment active File Output nodes if enabled
        fo_updated = 0
        if getattr(scene, "output_manager_sync_file_outputs", True):
            for node in get_file_output_nodes(scene):
                if node.mute:
                    continue
                node_changed = False

                # Increment directory or base_path
                if hasattr(node, "directory") and node.directory:
                    new_dir, ok_dir = increment_path_version(node.directory)
                    if ok_dir:
                        node.directory = new_dir
                        node_changed = True
                elif hasattr(node, "base_path") and node.base_path:
                    new_bp, ok_bp = increment_path_version(node.base_path)
                    if ok_bp:
                        node.base_path = new_bp
                        node_changed = True

                # Increment filename template if versioned
                if hasattr(node, "file_name") and node.file_name:
                    new_fn, ok_fn = increment_path_version(node.file_name)
                    if ok_fn:
                        node.file_name = new_fn
                        node_changed = True

                if node_changed:
                    fo_updated += 1

        if not ok_main and fo_updated == 0:
            self.report({'WARNING'}, "No version number found to increment in main path or File Output nodes")
            return {'CANCELLED'}

        msg_parts = []
        if ok_main:
            msg_parts.append(f"Main path -> {new_path}")
        if fo_updated > 0:
            msg_parts.append(f"{fo_updated} File Output node(s)")

        self.report({'INFO'}, f"Incremented (+1): {', '.join(msg_parts)}")
        return {'FINISHED'}


def get_preset_enum_items(self, context):
    """Dynamic EnumProperty callback returning all saved presets."""
    data = load_presets_from_disk()
    presets = data.get("presets", [])
    if not presets:
        return [("", "(No presets available)", "")]
    items = []
    for p in presets:
        name = p["name"]
        summary = p.get("format_summary", "")
        desc = f"{summary} • {p.get('filepath', '')}"
        items.append((name, name, desc))
    return items


class OUTPUT_MANAGER_OT_file_output_apply_preset(bpy.types.Operator):
    """Apply an Output Manager preset to this Compositor File Output node"""
    bl_idname = "output_manager.file_output_apply_preset"
    bl_label = "Apply Preset to File Output Node"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty(name="Node Name")
    preset_name: bpy.props.StringProperty(name="Preset Name")
    preset_choice: bpy.props.EnumProperty(
        name="Preset",
        items=get_preset_enum_items
    )
    mode: bpy.props.EnumProperty(
        name="Apply Mode",
        items=[
            ('FORMAT', "Format Only (Recommended)", "Update node file format, compression, and bit depth only; keep pass path untouched"),
            ('FULL', "Path & Format", "Update both format settings and base directory"),
            ('PATH', "Path Only", "Update directory path only; keep node file format untouched"),
        ],
        default='FORMAT'
    )

    def invoke(self, context, event):
        if not self.preset_name:
            data = load_presets_from_disk()
            active = data.get("active_preset", "")
            if active:
                self.preset_choice = active
            return context.window_manager.invoke_props_dialog(self, width=340)
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text=f"Target Node: {self.node_name}", icon='NODE_COMPOSITING')
        col.separator()
        col.prop(self, "preset_choice")
        col.separator()
        col.label(text="Application Mode:")
        col.prop(self, "mode", expand=True)

    def execute(self, context):
        target_preset_name = self.preset_name if self.preset_name else self.preset_choice
        data = load_presets_from_disk()
        presets = data.get("presets", [])
        preset = next((p for p in presets if p["name"] == target_preset_name), None)

        if not preset:
            self.report({'WARNING'}, f"Preset '{target_preset_name}' not found")
            return {'CANCELLED'}

        nodes = get_file_output_nodes(context.scene)
        target_node = next((n for n in nodes if n.name == self.node_name), None)
        if not target_node:
            self.report({'WARNING'}, f"Node '{self.node_name}' not found in Compositor")
            return {'CANCELLED'}

        ok, msg = apply_preset_to_file_output_node(target_node, preset, mode=self.mode)
        if not ok:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        self.report({'INFO'}, f"Applied preset '{target_preset_name}' ({self.mode}) to '{target_node.name}'")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_file_output_apply_preset_all(bpy.types.Operator):
    """Apply an Output Manager preset to all active Compositor File Output nodes"""
    bl_idname = "output_manager.file_output_apply_preset_all"
    bl_label = "Apply Preset to All File Output Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    preset_name: bpy.props.StringProperty(name="Preset Name")
    mode: bpy.props.EnumProperty(
        name="Apply Mode",
        items=[
            ('FORMAT', "Format Only", "Update node file format, compression, and bit depth only"),
            ('FULL', "Path & Format", "Update both format settings and base directory"),
            ('PATH', "Path Only", "Update directory path only"),
        ],
        default='FORMAT'
    )

    def execute(self, context):
        data = load_presets_from_disk()
        presets = data.get("presets", [])
        preset = next((p for p in presets if p["name"] == self.preset_name), None)

        if not preset:
            self.report({'WARNING'}, f"Preset '{self.preset_name}' not found")
            return {'CANCELLED'}

        nodes = get_file_output_nodes(context.scene)
        active_nodes = [n for n in nodes if not n.mute]
        if not active_nodes:
            self.report({'INFO'}, "No active File Output nodes found")
            return {'CANCELLED'}

        applied_count = 0
        for node in active_nodes:
            ok, _ = apply_preset_to_file_output_node(node, preset, mode=self.mode)
            if ok:
                applied_count += 1

        self.report({'INFO'}, f"Applied preset '{self.preset_name}' ({self.mode}) to {applied_count} File Output node(s)")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_file_output_store_preset(bpy.types.Operator):
    """Capture format and path settings from this File Output node into the global presets list"""
    bl_idname = "output_manager.file_output_store_preset"
    bl_label = "Store Preset from Node"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty(name="Node Name")
    name: bpy.props.StringProperty(name="Preset Name", default="")

    def invoke(self, context, event):
        data = load_presets_from_disk()
        existing = {p["name"] for p in data.get("presets", [])}
        default_base = f"{self.node_name} Output"
        self.name = make_unique_name(existing, default_base)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text=f"Capture from Node: '{self.node_name}'", icon='NODE_COMPOSITING')
        col.separator()
        col.prop(self, "name")

    def execute(self, context):
        nodes = get_file_output_nodes(context.scene)
        node = next((n for n in nodes if n.name == self.node_name), None)
        if not node:
            self.report({'WARNING'}, f"Node '{self.node_name}' not found")
            return {'CANCELLED'}

        target_name = self.name.strip()
        data = load_presets_from_disk()
        presets = data.get("presets", [])
        existing_names = {p["name"] for p in presets}

        if not target_name:
            target_name = make_unique_name(existing_names, f"{self.node_name} Output")

        captured = capture_file_output_node_settings(node)

        # Overwrite existing if matched, otherwise append
        found = False
        for p in presets:
            if p["name"] == target_name:
                p['filepath'] = captured['filepath']
                p['format_summary'] = captured['format_summary']
                p['format_settings'] = captured['format_settings']
                found = True
                break
        if not found:
            presets.append({
                'name': target_name,
                'filepath': captured['filepath'],
                'format_summary': captured['format_summary'],
                'format_settings': captured['format_settings']
            })

        data['presets'] = presets
        data['active_preset'] = target_name
        save_presets_to_disk(data)

        self.report({'INFO'}, f"Saved output preset '{target_name}' from node '{self.node_name}'")
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
        layout.ui_units_x = 22
        scene = context.scene

        data = load_presets_from_disk()
        active_name = data.get("active_preset", "")
        presets = data.get("presets", [])

        col = layout.column(align=True)
        col.operator("output_manager.preset_add", text="Store Current Scene Output", icon='ADD')

        # Option: Overwrite Warnings Toggle
        row_opt = col.row(align=True)
        row_opt.prop(
            scene,
            "output_manager_show_overwrite_warnings",
            text="Warn on File Overwrite",
            icon='ERROR' if scene.output_manager_show_overwrite_warnings else 'INFO'
        )

        col.separator()

        if not presets:
            box = col.box()
            box.label(text="No stored presets yet", icon='INFO')
            return

        for p in presets:
            is_active = (p["name"] == active_name)
            box = col.box()

            # Row 1: Active Indicator + Name + Format/Media Tag + Delete
            r1 = box.row(align=True)
            apply_icon = r1.operator(
                "output_manager.preset_apply",
                text="",
                icon='CHECKMARK' if is_active else 'BLANK1'
            )
            apply_icon.name = p["name"]

            r1.label(text=p["name"])

            fmt_tag = p.get("format_summary", "")
            if fmt_tag:
                r1.label(text=fmt_tag)

            del_op = r1.operator("output_manager.preset_remove", text="", icon='X')
            del_op.name = p["name"]

            # Row 2: Filepath preview + single Apply button
            r2 = box.row(align=True)
            raw_path = p.get("filepath", "")
            disp_path = raw_path[:24] + "..." if len(raw_path) > 24 else raw_path
            r2.label(text=disp_path if disp_path else "(empty)")

            apply_btn = r2.operator("output_manager.preset_apply", text="Apply")
            apply_btn.name = p["name"]


class OUTPUT_MANAGER_PT_file_output_popover(bpy.types.Panel):
    """Compositor File Output Nodes & Presets Popover"""
    bl_label = "Compositor File Outputs"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_idname = "OUTPUT_MANAGER_PT_file_output_popover"

    def draw(self, context):
        layout = self.layout
        layout.ui_units_x = 25
        scene = context.scene

        fo_nodes = get_file_output_nodes(scene)
        if not fo_nodes:
            box = layout.box()
            box.label(text="No File Output nodes in Compositor", icon='INFO')
            return

        data = load_presets_from_disk()
        active_preset = data.get("active_preset", "")

        col = layout.column(align=True)

        # Global Versioning Toggle Box
        top_box = col.box()
        t_row = top_box.row(align=True)
        t_row.prop(
            scene,
            "output_manager_sync_file_outputs",
            text="Increment Versions with +1",
            icon='CHECKBOX_HLT' if scene.output_manager_sync_file_outputs else 'CHECKBOX_DEHLT'
        )

        if active_preset:
            t_row2 = top_box.row(align=True)
            apply_all_op = t_row2.operator(
                "output_manager.file_output_apply_preset_all",
                text=f"Apply '{active_preset}' (Format) to All",
                icon='FILE_REFRESH'
            )
            apply_all_op.preset_name = active_preset
            apply_all_op.mode = 'FORMAT'

        col.separator()
        col.label(text="Detected File Output Nodes:", icon='NODE_COMPOSITING')

        for node in fo_nodes:
            node_box = col.box()

            # Row 1: Mute status + Node Name + Format Tag + Store Preset
            r1 = node_box.row(align=True)
            mute_icon = 'CHECKMARK' if not node.mute else 'RESTRICT_VIEW_ON'
            r1.prop(node, "mute", text="", icon=mute_icon, invert_checkbox=True)
            r1.label(text=node.name, icon='OUTPUT')

            fmt_summary = format_summary_from_rna(node.format)
            r1.label(text=fmt_summary)

            store_btn = r1.operator("output_manager.file_output_store_preset", text="", icon='ADD')
            store_btn.node_name = node.name

            # Row 2: Target Path Preview + Overwrite check + Apply Buttons
            r2 = node_box.row(align=True)
            _, _, full_path = get_node_path_strings(node)
            is_node_overwrite = False
            if scene.output_manager_show_overwrite_warnings:
                is_node_overwrite = check_path_overwrite_status(full_path, getattr(node.format, 'file_format', ''))

            if is_node_overwrite:
                r2.label(text="", icon='ERROR')

            disp_path = full_path[:22] + "..." if len(full_path) > 22 else full_path
            r2.label(text=disp_path if disp_path else "(empty path)")

            if active_preset:
                q_apply = r2.operator("output_manager.file_output_apply_preset", text="Apply Active")
                q_apply.node_name = node.name
                q_apply.preset_name = active_preset
                q_apply.mode = 'FORMAT'

            custom_apply = r2.operator("output_manager.file_output_apply_preset", text="", icon='DOWNARROW_HLT')
            custom_apply.node_name = node.name
            custom_apply.preset_name = ""


def draw_output_manager_header(self, context):
    """Draw Output Manager preset popover, File Output indicator, and overwrite warnings."""
    layout = self.layout
    scene = context.scene

    data = load_presets_from_disk()
    active_name = data.get("active_preset", "")
    presets = data.get("presets", [])
    active_preset = next((p for p in presets if p["name"] == active_name), None)

    is_dirty = False
    if active_preset:
        is_dirty = is_output_settings_dirty(scene, active_preset)

    # Row 1: Output Presets Popover + Store (+) + Increment (+1)
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
    row.operator("output_manager.preset_add", text="", icon='ADD')
    row.operator("output_manager.increment_version", text="+1")

    # Row 2: Overwrite Warning (Optional / Informative)
    if getattr(scene, "output_manager_show_overwrite_warnings", True):
        is_overwrite = check_path_overwrite_status(scene.render.filepath, getattr(scene.render.image_settings, 'file_format', ''))
        if is_overwrite:
            warn_box = layout.box()
            w_row = warn_box.row(align=True)
            w_row.alert = True
            w_row.label(text="Existing render files found on disk (may be overwritten)", icon='ERROR')

    # Row 3 (Option A): Notification & Popover for Compositor File Output Nodes
    fo_nodes = get_file_output_nodes(scene)
    if fo_nodes:
        active_count = sum(1 for n in fo_nodes if not n.mute)
        muted_count = len(fo_nodes) - active_count
        if muted_count > 0:
            fo_label = f"File Output: {active_count} active ({muted_count} muted)"
        else:
            fo_label = f"File Output: {active_count} active"

        sub_row = layout.row(align=True)
        sub_row.popover(
            panel="OUTPUT_MANAGER_PT_file_output_popover",
            text=fo_label,
            icon='NODE_COMPOSITING',
        )


# --- Registration ---

classes = (
    OUTPUT_MANAGER_OT_preset_add,
    OUTPUT_MANAGER_OT_confirm_overwrite,
    OUTPUT_MANAGER_OT_preset_apply,
    OUTPUT_MANAGER_OT_preset_remove,
    OUTPUT_MANAGER_OT_increment_version,
    OUTPUT_MANAGER_OT_file_output_apply_preset,
    OUTPUT_MANAGER_OT_file_output_apply_preset_all,
    OUTPUT_MANAGER_OT_file_output_store_preset,
    OUTPUT_MANAGER_PT_presets_popover,
    OUTPUT_MANAGER_PT_file_output_popover,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.output_manager_sync_file_outputs = bpy.props.BoolProperty(
        name="Sync File Output Nodes with +1",
        description="When incrementing output path version with +1, also increment version numbers across all active Compositor File Output nodes",
        default=True
    )

    bpy.types.Scene.output_manager_show_overwrite_warnings = bpy.props.BoolProperty(
        name="Show Overwrite Warnings",
        description="Check disk and display an informative warning in the UI if rendered files already exist for the current output path",
        default=True
    )

    if hasattr(bpy.types, "RENDER_PT_output"):
        bpy.types.RENDER_PT_output.prepend(draw_output_manager_header)


def unregister():
    if hasattr(bpy.types, "RENDER_PT_output"):
        try:
            bpy.types.RENDER_PT_output.remove(draw_output_manager_header)
        except Exception:
            pass

    if hasattr(bpy.types.Scene, "output_manager_sync_file_outputs"):
        try:
            del bpy.types.Scene.output_manager_sync_file_outputs
        except Exception:
            pass

    if hasattr(bpy.types.Scene, "output_manager_show_overwrite_warnings"):
        try:
            del bpy.types.Scene.output_manager_show_overwrite_warnings
        except Exception:
            pass

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
