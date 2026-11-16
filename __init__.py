bl_info = {
    "name": "Output Manager (DEV)",
    "author": "MD",
    "version": (0, 1, 2),
    "blender": (5, 2, 0),
    "location": "Properties > Output > Output Panel",
    "description": "Global presets for render output filepath, file formats, encoding settings, and Compositor File Output nodes",
    "category": "Render",
}

import json
import os
import re
from datetime import datetime
from pathlib import Path
import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper


# --- Storage Helpers ---

def get_presets_filepath() -> Path:
    """Return Path to the global presets JSON file."""
    base_dir = Path(bpy.utils.user_resource('SCRIPTS', path='presets')) / "output_manager"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "presets.json"


DEFAULT_SOCKET_PATTERNS = [
    {"name": "VFX Subfolder (Standard)", "pattern": r"\{socket}\{socket}_####"},
    {"name": "Flat Pass Sequence", "pattern": "{socket}_####"},
    {"name": "Subfolder / Frame Token", "pattern": "{socket}/_####"},
]


def load_presets_from_disk() -> dict:
    """Load presets JSON dictionary from disk."""
    path = get_presets_filepath()
    if not path.exists():
        return {
            "active_preset": "",
            "presets": [],
            "socket_patterns": list(DEFAULT_SOCKET_PATTERNS),
            "active_socket_pattern": "VFX Subfolder (Standard)"
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Validate untrusted/user-editable JSON so a malformed file can never
            # raise later inside a UI draw callback (which would hide a whole panel).
            if not isinstance(data, dict):
                raise ValueError("presets JSON root is not an object")
            if not isinstance(data.get("presets"), list):
                data["presets"] = []
            data["presets"] = [p for p in data["presets"] if isinstance(p, dict) and "name" in p]
            if not isinstance(data.get("socket_patterns"), list) or not data["socket_patterns"]:
                data["socket_patterns"] = list(DEFAULT_SOCKET_PATTERNS)
            data["socket_patterns"] = [p for p in data["socket_patterns"] if isinstance(p, dict) and "name" in p]
            if not data["socket_patterns"]:
                data["socket_patterns"] = list(DEFAULT_SOCKET_PATTERNS)
            if "active_socket_pattern" not in data:
                data["active_socket_pattern"] = data["socket_patterns"][0]["name"]
            return data
    except Exception as e:
        print(f"[Output Manager] Failed to load presets: {e}")
        return {
            "active_preset": "",
            "presets": [],
            "socket_patterns": list(DEFAULT_SOCKET_PATTERNS),
            "active_socket_pattern": "VFX Subfolder (Standard)"
        }


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


def decrement_path_version(filepath: str) -> tuple[str, bool]:
    """Find version number occurrences across folder and filename, decrementing all in sync (floor at 1)."""
    if not filepath or not filepath.strip():
        return filepath, False

    pattern = re.compile(r'(^|[^a-zA-Z0-9])([vV])?(\d+)(?=[^a-zA-Z0-9]|$)')
    matches = list(pattern.finditer(filepath))
    if not matches:
        return filepath, False

    v_matches = [m for m in matches if m.group(2)]
    if v_matches:
        max_val = max(int(m.group(3)) for m in v_matches)
        if max_val <= 1:
            return filepath, False  # Floor at 1

        target_digits_candidates = {m.group(3) for m in v_matches}
        to_replace = []
        for m in matches:
            if m.group(2):
                to_replace.append(m)
            elif m.group(3) in target_digits_candidates:
                to_replace.append(m)

        next_val = max(1, max_val - 1)
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
    if target_val <= 1:
        return filepath, False  # Floor at 1

    next_val = max(1, target_val - 1)
    pad = len(target_digits)

    to_replace = [m for m in matches if m.group(3) == target_digits]
    new_path = filepath
    for m in reversed(to_replace):
        new_digits = f"{next_val:0{pad}d}"
        start_idx = m.start(3)
        end_idx = m.end(3)
        new_path = new_path[:start_idx] + new_digits + new_path[end_idx:]

    return new_path, True


def set_path_version(filepath: str, target_val: int) -> tuple[str, bool]:
    """Find version number occurrences across folder and filename, updating all to target_val."""
    if not filepath or not filepath.strip() or target_val < 1:
        return filepath, False

    pattern = re.compile(r'(^|[^a-zA-Z0-9])([vV])?(\d+)(?=[^a-zA-Z0-9]|$)')
    matches = list(pattern.finditer(filepath))
    if not matches:
        return filepath, False

    v_matches = [m for m in matches if m.group(2)]
    if v_matches:
        target_digits_candidates = {m.group(3) for m in v_matches}
        to_replace = []
        for m in matches:
            if m.group(2):
                to_replace.append(m)
            elif m.group(3) in target_digits_candidates:
                to_replace.append(m)

        changed = False
        new_path = filepath
        for m in reversed(to_replace):
            orig_digits = m.group(3)
            pad = len(orig_digits)
            new_digits = f"{target_val:0{pad}d}"
            if orig_digits != new_digits:
                changed = True
            start_idx = m.start(3)
            end_idx = m.end(3)
            new_path = new_path[:start_idx] + new_digits + new_path[end_idx:]
        return new_path, changed

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

    pad = len(target_digits)
    new_digits = f"{target_val:0{pad}d}"
    if target_digits == new_digits:
        return filepath, False

    to_replace = [m for m in matches if m.group(3) == target_digits]
    new_path = filepath
    for m in reversed(to_replace):
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

def get_compositor_trees(scene) -> list:
    """Return the scene's compositor node tree(s) across Blender versions.

    Blender 5.3 replaced the single scene compositor tree with an effects stack
    (``Scene.compositor_effects``). ``Scene.compositing_node_group`` is deprecated
    there and only exposes the active effect, so it must not be used on 5.3+.
    """
    effects = getattr(scene, 'compositor_effects', None)
    if effects is not None:
        return [effect.node_group for effect in effects
                if getattr(effect, 'node_group', None) is not None]
    # Blender 5.0-5.2 (compositing_node_group) and older (node_tree).
    tree = getattr(scene, 'compositing_node_group', None) or getattr(scene, 'node_tree', None)
    return [tree] if tree is not None and hasattr(tree, 'nodes') else []


def get_file_output_nodes(scene) -> list:
    """Return all Compositor File Output nodes in the active scene."""
    if not scene:
        return []
    nodes = []
    for tree in get_compositor_trees(scene):
        nodes.extend(n for n in tree.nodes if getattr(n, 'type', None) == 'OUTPUT_FILE')
    return nodes


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


def get_node_slots_collection(node):
    """Retrieve the items/slots collection for a File Output node across Blender versions."""
    if not node:
        return []
    for attr in ('file_output_items', 'layer_slots', 'file_slots'):
        col = getattr(node, attr, None)
        if col is not None:
            return col
    return []


def get_path_version_number(filepath: str) -> int | None:
    """Return explicit version integer if filepath contains a version token, else None."""
    if not filepath or not filepath.strip():
        return None

    pattern = re.compile(r'(^|[^a-zA-Z0-9])([vV])(\d+)(?=[^a-zA-Z0-9]|$)')
    v_matches = list(pattern.finditer(filepath))
    if v_matches:
        return int(v_matches[-1].group(3))

    clean = re.sub(r'#+$', '', filepath.strip()).rstrip('._- ')
    last_slash = max(clean.rfind('/'), clean.rfind('\\'))
    filename = clean[last_slash + 1:] if last_slash != -1 else clean
    filename_no_ext = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', filename)

    num_pattern = re.compile(r'(^|[^a-zA-Z0-9])(\d+)(?=[^a-zA-Z0-9]|$)')
    num_matches = list(num_pattern.finditer(filename_no_ext))
    if num_matches:
        return int(num_matches[-1].group(2))

    return None


def get_node_versions(node) -> list[int]:
    """Return all explicit version numbers found across node directory, filename, and item slots."""
    vers = []
    dir_str = getattr(node, 'directory', '') or getattr(node, 'base_path', '')
    fn_str = getattr(node, 'file_name', '')

    v_dir = get_path_version_number(dir_str)
    if v_dir is not None:
        vers.append(v_dir)

    v_fn = get_path_version_number(fn_str)
    if v_fn is not None:
        vers.append(v_fn)

    for item in get_node_slots_collection(node):
        raw_name = getattr(item, 'name', '')
        if raw_name:
            v_item = get_path_version_number(raw_name)
            if v_item is not None:
                vers.append(v_item)

    return vers


def sync_node_to_version(node, target_ver: int) -> int:
    """Set version on node directory, filename, and all slot items to target_ver."""
    count = 0
    if hasattr(node, "directory") and node.directory:
        new_dir, ok = set_path_version(node.directory, target_ver)
        if ok:
            node.directory = new_dir
            count += 1
    elif hasattr(node, "base_path") and node.base_path:
        new_bp, ok = set_path_version(node.base_path, target_ver)
        if ok:
            node.base_path = new_bp
            count += 1

    if hasattr(node, "file_name") and node.file_name:
        new_fn, ok = set_path_version(node.file_name, target_ver)
        if ok:
            node.file_name = new_fn
            count += 1

    for item in get_node_slots_collection(node):
        raw_name = getattr(item, 'name', '')
        if raw_name:
            new_item, ok = set_path_version(raw_name, target_ver)
            if ok:
                item.name = new_item
                count += 1

    return count


def get_version_pool_status(scene) -> dict:
    """Analyze all active render outputs and compositor file nodes to find highest version and sync status."""
    all_versions = []

    # 1. Main output
    main_path = getattr(scene.render, 'filepath', '')
    main_ver = get_path_version_number(main_path)
    if main_ver is not None:
        all_versions.append(main_ver)

    # 2. File output nodes
    fo_nodes = get_file_output_nodes(scene)
    node_data = {}
    for node in fo_nodes:
        if node.mute:
            continue
        n_vers = get_node_versions(node)
        all_versions.extend(n_vers)
        node_data[node.name] = {
            'node': node,
            'versions': n_vers,
            'primary_ver': max(n_vers) if n_vers else None
        }

    max_version = max(all_versions) if all_versions else (main_ver or 1)

    scene_out_of_sync = (main_ver is not None and main_ver < max_version)
    out_of_sync_nodes = []
    for name, data in node_data.items():
        if data['primary_ver'] is not None and data['primary_ver'] < max_version:
            out_of_sync_nodes.append(name)
        elif any(v < max_version for v in data['versions']):
            out_of_sync_nodes.append(name)

    is_all_synced = (not scene_out_of_sync) and (len(out_of_sync_nodes) == 0)

    return {
        'max_version': max_version,
        'is_all_synced': is_all_synced,
        'main_ver': main_ver,
        'scene_out_of_sync': scene_out_of_sync,
        'out_of_sync_nodes': out_of_sync_nodes,
        'node_data': node_data
    }


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


# --- Compositor Socket Pattern Helpers ---

def extract_socket_base_name(sock_input, slot_item) -> str:
    """Extract clean pass/socket name from connected link or slot item string."""
    if sock_input and hasattr(sock_input, 'is_linked') and sock_input.is_linked and sock_input.links:
        from_sock = sock_input.links[0].from_socket
        if from_sock and hasattr(from_sock, 'name') and from_sock.name:
            return from_sock.name

    raw = getattr(slot_item, 'name', '') or ''
    if not raw:
        return "Pass"

    clean = re.sub(r'#+$', '', raw.strip())
    clean = clean.rstrip('_.- ')
    parts = [p for p in re.split(r'[\\/]', clean) if p]
    return parts[-1] if parts else raw


def abstract_pattern_from_slot(slot_name: str, base_name: str) -> str:
    """Replace occurrences of base socket name in slot_name with {socket} token."""
    if not base_name or not slot_name:
        return slot_name
    return slot_name.replace(base_name, "{socket}")


def clean_multilayer_channel_name(sock_input, slot_item) -> str:
    """Extract clean channel/layer name from connected link or strip paths/tokens from item name."""
    if sock_input and hasattr(sock_input, 'is_linked') and sock_input.is_linked and sock_input.links:
        from_sock = sock_input.links[0].from_socket
        if from_sock and hasattr(from_sock, 'name') and from_sock.name:
            return from_sock.name

    raw = getattr(slot_item, 'name', '') or ''
    if not raw:
        return "Render"

    clean = re.sub(r'#+$', '', raw.strip())
    clean = clean.rstrip('_.- ')
    parts = [p for p in re.split(r'[\\/]', clean) if p]
    return parts[-1] if parts else raw


def format_pass_sockets_on_node(node, pattern: str, clear_filename: bool = True) -> int:
    """Format all pass slots on a Compositor File Output node according to pattern."""
    if not node or getattr(node, 'type', None) != 'OUTPUT_FILE':
        return 0

    items = get_node_slots_collection(node)
    if not items:
        return 0

    count = 0
    for i, item in enumerate(items):
        sock_input = node.inputs[i] if i < len(node.inputs) else None
        base_name = extract_socket_base_name(sock_input, item)
        if not base_name:
            base_name = f"Pass{i+1}"

        new_name = pattern.replace("{socket}", base_name).replace("{pass}", base_name)
        item.name = new_name
        count += 1

    if clear_filename and ('\\' in pattern or '/' in pattern):
        if hasattr(node, 'file_name'):
            node.file_name = ""

    return count


def get_socket_pattern_enum_items(self, context):
    """Dynamic EnumProperty callback returning all saved socket patterns."""
    data = load_presets_from_disk()
    patterns = data.get("socket_patterns", DEFAULT_SOCKET_PATTERNS)
    items = []
    for p in patterns:
        name = p["name"]
        pat = p.get("pattern", "")
        items.append((name, name, pat))
    return items if items else [("", "(No patterns)", "")]


def update_socket_pattern_choice(self, context):
    """Callback when user selects a different socket pattern preset."""
    data = load_presets_from_disk()
    patterns = data.get("socket_patterns", DEFAULT_SOCKET_PATTERNS)
    choice = getattr(self, 'output_manager_socket_pattern_choice', '')
    target = next((p for p in patterns if p["name"] == choice), None)
    if target:
        self.output_manager_custom_socket_pattern = target.get("pattern", r"\{socket}\{socket}_####")


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


class OUTPUT_MANAGER_OT_decrement_version(bpy.types.Operator):
    """Decrement version number across all instances in the output filepath and active Compositor File Output nodes (floor at 1)"""
    bl_idname = "output_manager.decrement_version"
    bl_label = "Decrement Version (-1)"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty(name="Node Name", default="")

    def execute(self, context):
        scene = context.scene

        # Target a specific node if requested (e.g. from Node Tab in Compositor)
        if self.node_name:
            nodes = get_file_output_nodes(scene)
            target_node = next((n for n in nodes if n.name == self.node_name), None)
            if not target_node:
                self.report({'WARNING'}, f"Node '{self.node_name}' not found")
                return {'CANCELLED'}

            changed = False
            if hasattr(target_node, "directory") and target_node.directory:
                new_dir, ok_dir = decrement_path_version(target_node.directory)
                if ok_dir:
                    target_node.directory = new_dir
                    changed = True
            elif hasattr(target_node, "base_path") and target_node.base_path:
                new_bp, ok_bp = decrement_path_version(target_node.base_path)
                if ok_bp:
                    target_node.base_path = new_bp
                    changed = True

            if hasattr(target_node, "file_name") and target_node.file_name:
                new_fn, ok_fn = decrement_path_version(target_node.file_name)
                if ok_fn:
                    target_node.file_name = new_fn
                    changed = True

            for item in get_node_slots_collection(target_node):
                raw_name = getattr(item, 'name', '')
                if raw_name:
                    new_item, ok_item = decrement_path_version(raw_name)
                    if ok_item:
                        item.name = new_item
                        changed = True

            if not changed:
                self.report({'WARNING'}, f"Cannot decrement: Node '{self.node_name}' has no version number or is already at v001")
                return {'CANCELLED'}

            _, _, full_p = get_node_path_strings(target_node)
            self.report({'INFO'}, f"Decremented (-1): Node '{self.node_name}' -> {full_p}")
            return {'FINISHED'}

        # Global decrement (main path + synced active File Output nodes)
        current_path = scene.render.filepath
        new_path, ok_main = decrement_path_version(current_path)
        if ok_main:
            scene.render.filepath = new_path

        fo_updated = 0
        if getattr(scene, "output_manager_sync_file_outputs", True):
            for node in get_file_output_nodes(scene):
                if node.mute:
                    continue
                node_changed = False
                if hasattr(node, "directory") and node.directory:
                    new_dir, ok_dir = decrement_path_version(node.directory)
                    if ok_dir:
                        node.directory = new_dir
                        node_changed = True
                elif hasattr(node, "base_path") and node.base_path:
                    new_bp, ok_bp = decrement_path_version(node.base_path)
                    if ok_bp:
                        node.base_path = new_bp
                        node_changed = True

                if hasattr(node, "file_name") and node.file_name:
                    new_fn, ok_fn = decrement_path_version(node.file_name)
                    if ok_fn:
                        node.file_name = new_fn
                        node_changed = True

                for item in get_node_slots_collection(node):
                    raw_name = getattr(item, 'name', '')
                    if raw_name:
                        new_item, ok_item = decrement_path_version(raw_name)
                        if ok_item:
                            item.name = new_item
                            node_changed = True

                if node_changed:
                    fo_updated += 1

        if not ok_main and fo_updated == 0:
            self.report({'WARNING'}, "Cannot decrement: No version number found or already at version 1")
            return {'CANCELLED'}

        msg_parts = []
        if ok_main:
            msg_parts.append(f"Main path -> {new_path}")
        if fo_updated > 0:
            msg_parts.append(f"{fo_updated} File Output node(s)")

        self.report({'INFO'}, f"Decremented (-1): {', '.join(msg_parts)}")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_increment_version(bpy.types.Operator):
    """Increment version number across all instances in the output filepath and active Compositor File Output nodes"""
    bl_idname = "output_manager.increment_version"
    bl_label = "Increment Version (+1)"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty(name="Node Name", default="")

    def execute(self, context):
        scene = context.scene

        # Target a specific node if requested (e.g. from Node Tab in Compositor)
        if self.node_name:
            nodes = get_file_output_nodes(scene)
            target_node = next((n for n in nodes if n.name == self.node_name), None)
            if not target_node:
                self.report({'WARNING'}, f"Node '{self.node_name}' not found")
                return {'CANCELLED'}

            changed = False
            if hasattr(target_node, "directory") and target_node.directory:
                new_dir, ok_dir = increment_path_version(target_node.directory)
                if ok_dir:
                    target_node.directory = new_dir
                    changed = True
            elif hasattr(target_node, "base_path") and target_node.base_path:
                new_bp, ok_bp = increment_path_version(target_node.base_path)
                if ok_bp:
                    target_node.base_path = new_bp
                    changed = True

            if hasattr(target_node, "file_name") and target_node.file_name:
                new_fn, ok_fn = increment_path_version(target_node.file_name)
                if ok_fn:
                    target_node.file_name = new_fn
                    changed = True

            for item in get_node_slots_collection(target_node):
                raw_name = getattr(item, 'name', '')
                if raw_name:
                    new_item, ok_item = increment_path_version(raw_name)
                    if ok_item:
                        item.name = new_item
                        changed = True

            if not changed:
                self.report({'WARNING'}, f"No version number found in node '{self.node_name}'")
                return {'CANCELLED'}

            _, _, full_p = get_node_path_strings(target_node)
            self.report({'INFO'}, f"Incremented (+1): Node '{self.node_name}' -> {full_p}")
            return {'FINISHED'}

        # Global increment (main path + synced active File Output nodes)
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

                for item in get_node_slots_collection(node):
                    raw_name = getattr(item, 'name', '')
                    if raw_name:
                        new_item, ok_item = increment_path_version(raw_name)
                        if ok_item:
                            item.name = new_item
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


class OUTPUT_MANAGER_OT_format_pass_sockets(bpy.types.Operator):
    """Format and re-sync all pass slot paths on this File Output node using the active pattern template"""
    bl_idname = "output_manager.format_pass_sockets"
    bl_label = "Format Pass Sockets"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty(name="Node Name", default="")

    def execute(self, context):
        scene = context.scene
        nodes = get_file_output_nodes(scene)
        target_name = self.node_name or (context.active_node.name if context.active_node else "")
        target_node = next((n for n in nodes if n.name == target_name), None)

        if not target_node:
            self.report({'WARNING'}, "Target File Output node not found")
            return {'CANCELLED'}

        switched_format = False
        if getattr(target_node.format, 'file_format', '') == 'OPEN_EXR_MULTILAYER':
            if hasattr(target_node.format, 'media_type'):
                target_node.format.media_type = 'IMAGE'
            target_node.format.file_format = 'OPEN_EXR'
            switched_format = True

        pattern = getattr(scene, 'output_manager_custom_socket_pattern', r"\{socket}\{socket}_####")
        if not pattern.strip():
            pattern = r"\{socket}\{socket}_####"

        count = format_pass_sockets_on_node(target_node, pattern, clear_filename=True)
        if count == 0:
            self.report({'WARNING'}, f"Node '{target_node.name}' has no pass slots to format")
            return {'CANCELLED'}

        msg = f"Formatted {count} pass slot(s) on '{target_node.name}' using pattern '{pattern}'"
        if switched_format:
            msg += " (switched format from MultiLayer to OpenEXR)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_capture_pattern_from_node(bpy.types.Operator):
    """Detect the socket name in the active slot and abstract it into a reusable {socket} pattern template"""
    bl_idname = "output_manager.capture_pattern_from_node"
    bl_label = "Capture Pattern from Active Slot"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty(name="Node Name", default="")

    def execute(self, context):
        scene = context.scene
        nodes = get_file_output_nodes(scene)
        target_name = self.node_name or (context.active_node.name if context.active_node else "")
        node = next((n for n in nodes if n.name == target_name), None)

        if not node:
            self.report({'WARNING'}, "Target File Output node not found")
            return {'CANCELLED'}

        items = get_node_slots_collection(node)
        if not items:
            self.report({'WARNING'}, f"Node '{node.name}' has no slots to capture from")
            return {'CANCELLED'}

        idx = getattr(node, 'active_item_index', 0)
        if idx >= len(items):
            idx = 0
        target_item = items[idx]
        sock_input = node.inputs[idx] if idx < len(node.inputs) else None

        base_name = extract_socket_base_name(sock_input, target_item)
        abstracted = abstract_pattern_from_slot(target_item.name, base_name)

        if '{socket}' not in abstracted:
            abstracted = r"\{socket}\{socket}_####"

        scene.output_manager_custom_socket_pattern = abstracted
        self.report({'INFO'}, f"Captured pattern template: '{abstracted}' from slot '{target_item.name}'")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_save_socket_pattern_preset(bpy.types.Operator):
    """Save current socket pattern template as a named preset"""
    bl_idname = "output_manager.save_socket_pattern_preset"
    bl_label = "Save Socket Pattern Preset"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Preset Name", default="Pass Subfolder")
    pattern: bpy.props.StringProperty(name="Pattern Template", default=r"\{socket}\{socket}_####")

    def invoke(self, context, event):
        scene = context.scene
        self.pattern = getattr(scene, 'output_manager_custom_socket_pattern', r"\{socket}\{socket}_####")
        data = load_presets_from_disk()
        existing = {p["name"] for p in data.get("socket_patterns", [])}
        self.name = make_unique_name(existing, "Pass Subfolder")
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "name")
        col.prop(self, "pattern")
        sample = self.pattern.replace("{socket}", "Depth").replace("{pass}", "Depth")
        col.separator()
        col.label(text=f"Sample: {sample}", icon='FORWARD')

    def execute(self, context):
        target_name = self.name.strip()
        if not target_name:
            target_name = "Pass Pattern"

        data = load_presets_from_disk()
        patterns = data.get("socket_patterns", list(DEFAULT_SOCKET_PATTERNS))

        found = False
        for p in patterns:
            if p["name"] == target_name:
                p["pattern"] = self.pattern
                found = True
                break
        if not found:
            patterns.append({"name": target_name, "pattern": self.pattern})

        data["socket_patterns"] = patterns
        data["active_socket_pattern"] = target_name
        save_presets_to_disk(data)

        context.scene.output_manager_socket_pattern_choice = target_name
        context.scene.output_manager_custom_socket_pattern = self.pattern
        self.report({'INFO'}, f"Saved socket pattern preset '{target_name}'")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_delete_socket_pattern_preset(bpy.types.Operator):
    """Delete the active socket pattern preset"""
    bl_idname = "output_manager.delete_socket_pattern_preset"
    bl_label = "Delete Socket Pattern Preset"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Preset Name", default="")

    def execute(self, context):
        data = load_presets_from_disk()
        patterns = data.get("socket_patterns", [])
        target_name = self.name or context.scene.output_manager_socket_pattern_choice

        if len(patterns) <= 1:
            self.report({'WARNING'}, "Cannot delete the only remaining pattern preset")
            return {'CANCELLED'}

        data["socket_patterns"] = [p for p in patterns if p["name"] != target_name]
        data["active_socket_pattern"] = data["socket_patterns"][0]["name"]
        save_presets_to_disk(data)

        context.scene.output_manager_socket_pattern_choice = data["active_socket_pattern"]
        context.scene.output_manager_custom_socket_pattern = data["socket_patterns"][0].get("pattern", r"\{socket}\{socket}_####")
        self.report({'INFO'}, f"Deleted socket pattern preset '{target_name}'")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_clean_multilayer_slots(bpy.types.Operator):
    """Reset layer names to clean channel names, removing leftover subfolders or frame tokens"""
    bl_idname = "output_manager.clean_multilayer_slots"
    bl_label = "Clean Layer Names"
    bl_options = {'REGISTER', 'UNDO'}

    node_name: bpy.props.StringProperty(name="Node Name", default="")

    def execute(self, context):
        scene = context.scene
        nodes = get_file_output_nodes(scene)
        target_name = self.node_name or (context.active_node.name if getattr(context, 'active_node', None) else "")
        node = next((n for n in nodes if n.name == target_name), None)
        if not node and getattr(context, 'active_node', None) and getattr(context.active_node, 'type', None) == 'OUTPUT_FILE':
            node = context.active_node

        if not node:
            self.report({'WARNING'}, "Target File Output node not found")
            return {'CANCELLED'}

        items = get_node_slots_collection(node)
        if not items:
            self.report({'WARNING'}, f"Node '{node.name}' has no slots to clean")
            return {'CANCELLED'}

        count = 0
        for i, item in enumerate(items):
            sock_input = node.inputs[i] if i < len(node.inputs) else None
            clean_name = clean_multilayer_channel_name(sock_input, item)
            if item.name != clean_name:
                item.name = clean_name
                count += 1

        self.report({'INFO'}, f"Cleaned {count} layer name(s) on '{node.name}'")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_sync_versions(bpy.types.Operator):
    """Synchronize output versions across scene output and Compositor File Output nodes favoring the highest version"""
    bl_idname = "output_manager.sync_versions"
    bl_label = "Sync Output Versions"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(
        name="Target",
        items=[
            ('ALL', "All Outputs", "Synchronize scene output and all active File Output nodes to highest pool version"),
            ('NODE', "Specific Node", "Synchronize this File Output node to highest pool version"),
        ],
        default='ALL'
    )
    node_name: bpy.props.StringProperty(name="Node Name", default="")

    def execute(self, context):
        scene = context.scene
        pool = get_version_pool_status(scene)
        target_ver = pool['max_version']
        target_v_str = f"v{target_ver:03d}"

        if self.target == 'NODE':
            target_name = self.node_name or (context.active_node.name if getattr(context, 'active_node', None) else "")
            nodes = get_file_output_nodes(scene)
            node = next((n for n in nodes if n.name == target_name), None)
            if not node and getattr(context, 'active_node', None) and getattr(context.active_node, 'type', None) == 'OUTPUT_FILE':
                node = context.active_node

            if not node:
                self.report({'WARNING'}, f"Node '{target_name}' not found")
                return {'CANCELLED'}

            count = sync_node_to_version(node, target_ver)
            self.report({'INFO'}, f"Synced node '{node.name}' to {target_v_str} ({count} path(s)/slot(s) updated)")
            return {'FINISHED'}

        # Target ALL
        updated_count = 0
        new_scene_path, ok_scene = set_path_version(scene.render.filepath, target_ver)
        if ok_scene:
            scene.render.filepath = new_scene_path
            updated_count += 1

        fo_updated = 0
        for node in get_file_output_nodes(scene):
            if node.mute:
                continue
            cnt = sync_node_to_version(node, target_ver)
            if cnt > 0:
                fo_updated += 1
                updated_count += cnt

        if updated_count == 0:
            self.report({'INFO'}, f"All outputs are already in sync at {target_v_str}")
            return {'FINISHED'}

        self.report({'INFO'}, f"Synced all outputs to {target_v_str} (Main + {fo_updated} node(s))")
        return {'FINISHED'}


class OUTPUT_MANAGER_OT_open_presets_dir(bpy.types.Operator):
    """Open the directory containing presets.json in the OS file explorer"""
    bl_idname = "output_manager.open_presets_dir"
    bl_label = "Open Presets Folder"
    bl_options = {'REGISTER'}

    def execute(self, context):
        p = get_presets_filepath().parent
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
        try:
            bpy.ops.wm.path_open(filepath=str(p))
            return {'FINISHED'}
        except Exception:
            try:
                import os
                os.startfile(str(p))
                return {'FINISHED'}
            except Exception as e:
                self.report({'ERROR'}, f"Failed to open folder: {e}")
                return {'CANCELLED'}


class OUTPUT_MANAGER_OT_export_presets(bpy.types.Operator, ExportHelper):
    """Export all Output Manager presets and pattern templates to a JSON backup file"""
    bl_idname = "output_manager.export_presets"
    bl_label = "Export Presets Backup"
    bl_options = {'REGISTER'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        data = load_presets_from_disk()
        export_payload = {
            "schema_version": 1,
            "addon": "output_manager",
            "export_time": datetime.now().isoformat(),
            "active_preset": data.get("active_preset", ""),
            "active_socket_pattern": data.get("active_socket_pattern", ""),
            "presets": data.get("presets", []),
            "socket_patterns": data.get("socket_patterns", list(DEFAULT_SOCKET_PATTERNS)),
        }

        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(export_payload, f, indent=2)
            p_cnt = len(export_payload["presets"])
            s_cnt = len(export_payload["socket_patterns"])
            self.report({'INFO'}, f"Exported {p_cnt} preset(s) and {s_cnt} pattern(s) to '{os.path.basename(self.filepath)}'")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to export presets: {e}")
            return {'CANCELLED'}

    def invoke(self, context, event):
        date_str = datetime.now().strftime("%Y%m%d")
        self.filepath = f"output_manager_backup_{date_str}.json"
        return super().invoke(context, event)


class OUTPUT_MANAGER_OT_import_presets(bpy.types.Operator, ImportHelper):
    """Import Output Manager presets and pattern templates from a JSON backup file"""
    bl_idname = "output_manager.import_presets"
    bl_label = "Import Presets Backup"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,
    )

    import_mode: bpy.props.EnumProperty(
        name="Import Mode",
        description="Choose how incoming presets should be merged with your current settings",
        items=[
            ('MERGE', "Merge & Update", "Add new presets and patterns; update matching names with backup values while keeping existing ones"),
            ('REPLACE', "Full Restore (Replace All)", "Completely replace current presets and patterns with the backup file"),
        ],
        default='MERGE'
    )

    def execute(self, context):
        if not os.path.exists(self.filepath):
            self.report({'ERROR'}, f"File not found: {self.filepath}")
            return {'CANCELLED'}

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                imported_data = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, f"Invalid JSON file: {e}")
            return {'CANCELLED'}

        if not isinstance(imported_data, dict):
            self.report({'ERROR'}, "Invalid backup file: root must be a JSON object")
            return {'CANCELLED'}

        incoming_presets = imported_data.get("presets", [])
        incoming_patterns = imported_data.get("socket_patterns", [])

        if not isinstance(incoming_presets, list) or not isinstance(incoming_patterns, list):
            self.report({'ERROR'}, "Invalid backup file structure: 'presets' and 'socket_patterns' must be lists")
            return {'CANCELLED'}

        current_data = load_presets_from_disk()

        if self.import_mode == 'REPLACE':
            current_data["presets"] = incoming_presets
            current_data["socket_patterns"] = incoming_patterns if incoming_patterns else list(DEFAULT_SOCKET_PATTERNS)
            if imported_data.get("active_preset"):
                current_data["active_preset"] = imported_data["active_preset"]
            elif current_data["presets"]:
                current_data["active_preset"] = current_data["presets"][0]["name"]
            if imported_data.get("active_socket_pattern"):
                current_data["active_socket_pattern"] = imported_data["active_socket_pattern"]
            save_presets_to_disk(current_data)
            self.report({'INFO'}, f"Restored {len(incoming_presets)} preset(s) and {len(incoming_patterns)} pattern(s)")
            return {'FINISHED'}

        # Mode == 'MERGE'
        existing_presets = current_data.get("presets", [])
        preset_map = {p["name"]: p for p in existing_presets if isinstance(p, dict) and "name" in p}
        added_presets = 0
        updated_presets = 0

        for p in incoming_presets:
            if not isinstance(p, dict) or "name" not in p:
                continue
            name = p["name"]
            if name in preset_map:
                preset_map[name] = p
                updated_presets += 1
            else:
                preset_map[name] = p
                added_presets += 1

        current_data["presets"] = list(preset_map.values())

        existing_patterns = current_data.get("socket_patterns", list(DEFAULT_SOCKET_PATTERNS))
        pattern_map = {pat["name"]: pat for pat in existing_patterns if isinstance(pat, dict) and "name" in pat}
        added_patterns = 0
        updated_patterns = 0

        for pat in incoming_patterns:
            if not isinstance(pat, dict) or "name" not in pat:
                continue
            name = pat["name"]
            if name in pattern_map:
                pattern_map[name] = pat
                updated_patterns += 1
            else:
                pattern_map[name] = pat
                added_patterns += 1

        current_data["socket_patterns"] = list(pattern_map.values())
        save_presets_to_disk(current_data)

        self.report({'INFO'}, f"Merged: {added_presets} added / {updated_presets} updated presets; {added_patterns} added / {updated_patterns} updated patterns")
        return {'FINISHED'}


class OUTPUT_MANAGER_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__ or "output_manager"

    def draw(self, context):
        layout = self.layout

        data = load_presets_from_disk()
        presets = data.get("presets", [])
        patterns = data.get("socket_patterns", [])
        preset_file = get_presets_filepath()

        # Section: Preset Statistics & Storage Location
        box = layout.box()
        h_row = box.row(align=True)
        h_row.label(text="Preset Storage & Statistics", icon='PRESET')
        h_row.operator("output_manager.open_presets_dir", text="Open Folder", icon='FILE_FOLDER')

        col = box.column(align=True)
        col.label(text=f"Stored Presets: {len(presets)} scene preset(s)")
        col.label(text=f"Stored Patterns: {len(patterns)} pass socket pattern template(s)")
        col.label(text=f"File: {preset_file}", icon='DISK_DRIVE')

        # Section: Backup & Restore (JSON Export / Import)
        box_backup = layout.box()
        b_hdr = box_backup.row(align=True)
        b_hdr.label(text="Backup & Restore (JSON)", icon='FILE_BACKUP')

        b_row = box_backup.row(align=True)
        b_row.scale_y = 1.25
        b_row.operator("output_manager.export_presets", text="Export Presets Backup...", icon='EXPORT')
        b_row.operator("output_manager.import_presets", text="Import Presets Backup...", icon='IMPORT')

        box_backup.label(text="Exports all presets, pass socket patterns, and active selections to a single portable JSON file.", icon='INFO')


# --- UI Panels & Controls ---

class NODE_PT_output_manager_node(bpy.types.Panel):
    """File Output Presets & Path Information in Compositor Node Tab"""
    bl_label = "File Output Presets & Paths"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Node"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        snode = getattr(context, 'space_data', None)
        if not snode or getattr(snode, 'tree_type', None) != 'CompositorNodeTree':
            return False
        node = getattr(context, 'active_node', None)
        return node and getattr(node, 'type', None) == 'OUTPUT_FILE'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        node = context.active_node
        if not node:
            return

        # Row 1: Preset Actions + Version Stepping for this active node
        row = layout.row(align=True)
        apply_op = row.operator("output_manager.file_output_apply_preset", text="Apply Preset...", icon='PRESET')
        apply_op.node_name = node.name

        store_op = row.operator("output_manager.file_output_store_preset", text="", icon='ADD')
        store_op.node_name = node.name

        dec_op = row.operator("output_manager.decrement_version", text="-1")
        dec_op.node_name = node.name

        inc_op = row.operator("output_manager.increment_version", text="+1")
        inc_op.node_name = node.name

        # Version Sync Notice if out of sync with pool
        pool = get_version_pool_status(scene)
        is_node_out_of_sync = node.name in pool['out_of_sync_nodes']
        if is_node_out_of_sync:
            alert_box = layout.box()
            a_row = alert_box.row(align=True)
            a_row.alert = True
            node_v = pool['node_data'].get(node.name, {}).get('primary_ver')
            node_v_str = f"v{node_v:03d}" if node_v else "unversioned"
            a_row.label(text=f"Version Mismatch: Node is {node_v_str} (Pool max: v{pool['max_version']:03d})", icon='ERROR')

            btn_row = alert_box.row(align=True)
            sync_n = btn_row.operator("output_manager.sync_versions", text=f"⟳ Sync Node to v{pool['max_version']:03d}", icon='FILE_REFRESH')
            sync_n.target = 'NODE'
            sync_n.node_name = node.name

            sync_a = btn_row.operator("output_manager.sync_versions", text="Sync All Outputs", icon='LINKED')
            sync_a.target = 'ALL'

        # Box: Output Architecture Breakdown
        box = layout.box()
        file_format = getattr(node.format, 'file_format', 'OPEN_EXR')
        is_multilayer = (file_format == 'OPEN_EXR_MULTILAYER')

        dir_str = getattr(node, 'directory', '') or getattr(node, 'base_path', '')
        fn_str = getattr(node, 'file_name', '')
        items = get_node_slots_collection(node)

        if is_multilayer:
            # MultiLayer OpenEXR: Single unified file
            b_hdr = box.row(align=True)
            b_hdr.label(text="MultiLayer EXR: Single File for All Layers", icon='FILE_IMAGE')

            target_fn = f"{dir_str}{fn_str}####.exr" if fn_str else f"{dir_str}####.exr"
            box.label(text=f"Target: {target_fn}")

            if items:
                layer_names = [it.name for it in items[:5]]
                more = f" (+{len(items)-5} more)" if len(items) > 5 else ""
                box.label(text=f"Layers: {', '.join(layer_names)}{more}")
            else:
                box.label(text="Layers: (no passes connected)", icon='INFO')

            clean_btn = box.operator("output_manager.clean_multilayer_slots", text="⚡ Clean Layer Names", icon='FILE_REFRESH')
            clean_btn.node_name = node.name
        else:
            # Multi-File Output (PNG, TIFF, OpenEXR single, etc.)
            b_hdr = box.row(align=True)
            b_hdr.label(text="Multi-File Output: Separate File Per Pass", icon='DOCUMENTS')

            ext = 'exr'
            if 'png' in file_format.lower():
                ext = 'png'
            elif 'jpeg' in file_format.lower() or 'jpg' in file_format.lower():
                ext = 'jpg'
            elif 'tiff' in file_format.lower():
                ext = 'tif'
            elif 'targa' in file_format.lower():
                ext = 'tga'

            first_item = items[0].name if items and len(items) > 0 else "<Pass>"
            example_fn = f"{dir_str}{fn_str}{first_item}####.{ext}"

            box.label(text="Rule: <Directory> + <File Name> + <Pass Name>")
            box.label(text=f"Example: {example_fn}")

        # Section: Pass Socket Formatter (Available for multi-file outputs)
        if not is_multilayer:
            box_fmt = layout.box()
            h_row = box_fmt.row(align=True)
            h_row.label(text="Pass Socket Formatter:", icon='NODE_COMPOSITING')
            cap_op = h_row.operator("output_manager.capture_pattern_from_node", text="", icon='EYEDROPPER')
            cap_op.node_name = node.name

            # Pattern Dropdown + Save / Delete buttons
            p_row = box_fmt.row(align=True)
            p_row.prop(scene, "output_manager_socket_pattern_choice", text="")
            save_p = p_row.operator("output_manager.save_socket_pattern_preset", text="", icon='ADD')
            del_p = p_row.operator("output_manager.delete_socket_pattern_preset", text="", icon='X')

            # Editable Template String
            box_fmt.prop(scene, "output_manager_custom_socket_pattern", text="Template")

            # Live Sample Preview
            cur_pat = getattr(scene, 'output_manager_custom_socket_pattern', '') or r"\{socket}\{socket}_####"
            sample_out = cur_pat.replace("{socket}", "Depth").replace("{pass}", "Depth")
            box_fmt.label(text=f"Preview (Depth) -> {sample_out}", icon='FORWARD')

            # Action Button
            btn_row = box_fmt.row(align=True)
            fmt_btn = btn_row.operator("output_manager.format_pass_sockets", text="⚡ Format Pass Sockets", icon='FILE_REFRESH')
            fmt_btn.node_name = node.name


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
        col.separator()

        if not presets:
            box = col.box()
            box.label(text="No stored presets yet", icon='INFO')
        else:
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

        # Backup & Restore row at the bottom of the popover
        col.separator()
        b_box = col.box()
        b_hdr = b_box.row(align=True)
        b_hdr.label(text="Backup & Restore", icon='FILE_BACKUP')
        b_row = b_box.row(align=True)
        b_row.operator("output_manager.export_presets", text="Export JSON...", icon='EXPORT')
        b_row.operator("output_manager.import_presets", text="Import JSON...", icon='IMPORT')


def _draw_output_manager_header(self, context):
    """Draw Output Manager preset popover, compositor toggles, and inline File Output nodes."""
    layout = self.layout
    scene = context.scene

    data = load_presets_from_disk()
    active_name = data.get("active_preset", "")
    presets = data.get("presets", [])
    active_preset = next((p for p in presets if p["name"] == active_name), None)

    is_dirty = False
    if active_preset:
        is_dirty = is_output_settings_dirty(scene, active_preset)

    # Row 1: Output Presets Popover + Store (+) + Decrement (-1) + Increment (+1)
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
    row.operator("output_manager.decrement_version", text="-1")
    row.operator("output_manager.increment_version", text="+1")

    # Row 2: Compact 1-row control: 1) Enable/disable Compositor, 2) Toggle lockstep sync
    comp_row = layout.row(align=True)
    comp_row.prop(scene.render, "use_compositing", text="Compositor", icon='NODE_COMPOSITING')
    comp_row.prop(scene, "output_manager_sync_file_outputs", text="Sync Comp (+1/-1)", icon='LINKED')

    pool = get_version_pool_status(scene)
    if not pool['is_all_synced']:
        sync_row = layout.row(align=True)
        sync_row.alert = True
        sync_all = sync_row.operator(
            "output_manager.sync_versions",
            text=f"⚠️ Versions Out of Sync — Align All to v{pool['max_version']:03d}",
            icon='FILE_REFRESH'
        )
        sync_all.target = 'ALL'

    # Row 3: Detected Compositor File Output Nodes directly inline
    fo_nodes = get_file_output_nodes(scene)
    if fo_nodes:
        box = layout.box()
        active_count = sum(1 for n in fo_nodes if not n.mute)
        hdr = box.row(align=True)
        hdr.label(text=f"Compositor File Outputs ({active_count} active):", icon='NODE_COMPOSITING')

        for node in fo_nodes:
            nrow = box.row(align=True)
            mute_icon = 'CHECKMARK' if not node.mute else 'RESTRICT_VIEW_ON'
            nrow.prop(node, "mute", text="", icon=mute_icon, invert_checkbox=True)
            nrow.label(text=node.name, icon='OUTPUT')
            nrow.label(text=format_summary_from_rna(node.format))

            if node.name in pool['out_of_sync_nodes']:
                curr_v = pool['node_data'].get(node.name, {}).get('primary_ver')
                curr_str = f"v{curr_v:03d}" if curr_v else "v???"
                sync_btn = nrow.operator("output_manager.sync_versions", text=f"⟳ {curr_str} → v{pool['max_version']:03d}", icon='FILE_REFRESH')
                sync_btn.target = 'NODE'
                sync_btn.node_name = node.name

            _, _, full_p = get_node_path_strings(node)
            disp_p = full_p[:20] + "..." if len(full_p) > 20 else full_p
            nrow.label(text=disp_p if disp_p else "(empty)")


def draw_output_manager_header(self, context):
    """Hook for RENDER_PT_output: never let an add-on error hide Blender's Output panel."""
    try:
        _draw_output_manager_header(self, context)
    except Exception:
        import traceback
        traceback.print_exc()


# --- Registration ---

classes = (
    OUTPUT_MANAGER_OT_preset_add,
    OUTPUT_MANAGER_OT_confirm_overwrite,
    OUTPUT_MANAGER_OT_preset_apply,
    OUTPUT_MANAGER_OT_preset_remove,
    OUTPUT_MANAGER_OT_decrement_version,
    OUTPUT_MANAGER_OT_increment_version,
    OUTPUT_MANAGER_OT_file_output_apply_preset,
    OUTPUT_MANAGER_OT_file_output_store_preset,
    OUTPUT_MANAGER_OT_format_pass_sockets,
    OUTPUT_MANAGER_OT_clean_multilayer_slots,
    OUTPUT_MANAGER_OT_sync_versions,
    OUTPUT_MANAGER_OT_capture_pattern_from_node,
    OUTPUT_MANAGER_OT_save_socket_pattern_preset,
    OUTPUT_MANAGER_OT_delete_socket_pattern_preset,
    OUTPUT_MANAGER_OT_open_presets_dir,
    OUTPUT_MANAGER_OT_export_presets,
    OUTPUT_MANAGER_OT_import_presets,
    OUTPUT_MANAGER_preferences,
    OUTPUT_MANAGER_PT_presets_popover,
    NODE_PT_output_manager_node,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.output_manager_sync_file_outputs = bpy.props.BoolProperty(
        name="Sync Comp (+1/-1)",
        description="When stepping output path version with +1 or -1, also step version numbers across all active Compositor File Output nodes",
        default=True
    )

    bpy.types.Scene.output_manager_socket_pattern_choice = bpy.props.EnumProperty(
        name="Socket Pattern",
        description="Choose a saved pass socket naming pattern preset",
        items=get_socket_pattern_enum_items,
        update=update_socket_pattern_choice
    )

    bpy.types.Scene.output_manager_custom_socket_pattern = bpy.props.StringProperty(
        name="Pattern Template",
        description="Template string for pass sockets. Use {socket} for pass/socket name",
        default=r"\{socket}\{socket}_####"
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

    if hasattr(bpy.types.Scene, "output_manager_socket_pattern_choice"):
        try:
            del bpy.types.Scene.output_manager_socket_pattern_choice
        except Exception:
            pass

    if hasattr(bpy.types.Scene, "output_manager_custom_socket_pattern"):
        try:
            del bpy.types.Scene.output_manager_custom_socket_pattern
        except Exception:
            pass

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
