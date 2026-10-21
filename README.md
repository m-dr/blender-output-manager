# Output Manager for Blender 5.2+

Global presets for Blender Render Output settings, supporting literal tokenized filepaths (`$blendfile`, `$camera`, etc.) and optional image/video format settings.

## Features

- 🌐 **Global Presets**: Presets are stored globally in your Blender configuration, accessible across all `.blend` files.
- 📁 **Token Support**: Preserves Blender native render tokens (`$blendfile`, `$camera`, `_###`, etc.) verbatim.
- 🎛️ **Flexible Apply**:
  - **Apply All**: Restores filepath and all image/video format parameters (format, compression, color space, codec).
  - **Apply Path Only**: Restores only the filepath without altering the current file format.
- ⚡ **Seamless Overwrite Flow**: Built-in overwrite confirmation and auto-incremented `.001` naming for duplicates.
- ⏱️ **Dirty State Indicator (`*`)**: Visual marker indicating when current scene settings deviate from the active preset.

## License

GPL-3.0-or-later
