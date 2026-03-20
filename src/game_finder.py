"""
Angels Online game installation finder.

Searches for the Angels Online game install directory at startup.
Checks common paths, Windows registry, and falls back to a GUI file dialog.
Saves the found/selected path to a user config file for future runs.

The server needs the game install to:
  - Read .pak files for map data (via map_loader.py)
  - Locate the game's SERVER.XML to update for local connections

Some installs lack midage.exe (or use a different launcher name),
so we detect the install by the presence of core data files instead.
"""

import os

import logging
from pathlib import Path

log = logging.getLogger("game_finder")

# Files that identify a valid Angels Online installation.
# We check for any of these (some installs may lack midage.exe).
_GAME_MARKER_FILES = [
    "data1.pak",
    "ANGEL.DAT",
    "ANGLE.DAT",
    "update.pak",
    "ao.ico",
]

# Common install locations to search
_COMMON_PATHS = [
    r"C:\Program Files (x86)\Angels Online",
    r"C:\Program Files\Angels Online",
    r"C:\Angels Online",
    r"C:\Games\Angels Online",
    r"D:\Angels Online",
    r"D:\Games\Angels Online",
    r"E:\Angels Online",
    r"E:\Games\Angels Online",
]

# Registry keys where the installer may have recorded the install path
_REGISTRY_KEYS = [
    (r"SOFTWARE\WOW6432Node\Angels Online", "InstallPath"),
    (r"SOFTWARE\Angels Online", "InstallPath"),
    (r"SOFTWARE\WOW6432Node\Angels Online", "Path"),
    (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Angels Online",
     "InstallLocation"),
]

# User config file for saving the game dir between runs
def _get_user_config_path() -> Path:
    """Return path to user-writable config file.

    Always placed next to the server root so users can easily edit it.
    """
    base = Path(__file__).parent.parent
    return base / "ao_config.ini"


def _is_valid_game_dir(path: Path) -> bool:
    """Return True if the path looks like an Angels Online install."""
    if not path.is_dir():
        return False
    for marker in _GAME_MARKER_FILES:
        if (path / marker).exists():
            return True
    return False


def _check_registry() -> Path | None:
    """Try to find the install path via the Windows registry."""
    try:
        import winreg
        for key_path, value_name in _REGISTRY_KEYS:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        value, _ = winreg.QueryValueEx(key, value_name)
                        candidate = Path(str(value))
                        if _is_valid_game_dir(candidate):
                            log.info(f"Found game via registry: {candidate}")
                            return candidate
                except (FileNotFoundError, OSError):
                    continue
    except ImportError:
        pass  # Not on Windows
    return None


def _load_saved_path() -> Path | None:
    """Load a previously saved game directory from the user config file."""
    cfg = _get_user_config_path()
    if not cfg.exists():
        return None
    try:
        text = cfg.read_text(encoding="utf-8").strip()
        for line in text.splitlines():
            if line.startswith("game_dir="):
                candidate = Path(line[len("game_dir="):].strip())
                if _is_valid_game_dir(candidate):
                    return candidate
    except Exception as e:
        log.debug(f"Could not read saved config: {e}")
    return None


def _save_path(path: Path) -> None:
    """Save the game directory to the user config file."""
    cfg = _get_user_config_path()
    try:
        # Read existing config lines (preserve any other settings)
        lines = []
        if cfg.exists():
            lines = [
                ln for ln in cfg.read_text(encoding="utf-8").splitlines()
                if not ln.startswith("game_dir=")
            ]
        lines.append(f"game_dir={path}")
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.debug(f"Saved game dir to {cfg}")
    except Exception as e:
        log.warning(f"Could not save config: {e}")


def _prompt_gui(initial_dir: str = "") -> Path | None:
    """Show a tkinter directory chooser dialog. Returns None if cancelled."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        messagebox.showinfo(
            "Angels Online Private Server",
            "Could not automatically find your Angels Online installation.\n\n"
            "Please select the folder that contains data1.pak / ANGEL.DAT\n"
            "(e.g. C:\\Program Files (x86)\\Angels Online)",
            parent=root,
        )

        selected = filedialog.askdirectory(
            title="Select Angels Online Install Folder",
            initialdir=initial_dir or r"C:\Program Files (x86)",
            parent=root,
        )
        root.destroy()

        if selected:
            candidate = Path(selected)
            if _is_valid_game_dir(candidate):
                return candidate
            # Selected dir might be valid even without our marker files
            # (e.g. a stripped install). Accept it with a warning.
            log.warning(
                f"Selected directory does not contain expected game files: {candidate}\n"
                "Server will continue but map loading may fail."
            )
            return candidate
    except Exception as e:
        log.error(f"GUI file dialog failed: {e}")
    return None


def find_game_dir(allow_gui: bool = True) -> Path | None:
    """
    Find the Angels Online install directory.

    Search order:
      1. AO_GAME_DIR environment variable (highest priority)
      2. Previously saved path in ao_config.ini
      3. Windows registry
      4. Common install locations
      5. GUI directory chooser (if allow_gui=True and a display is available)

    Returns the Path if found, or None if not found and GUI is unavailable.
    """
    # 1. Environment variable (set by user or inherited from config.py)
    env_dir = os.environ.get("AO_GAME_DIR", "").strip()
    if env_dir:
        candidate = Path(env_dir)
        if _is_valid_game_dir(candidate):
            log.info(f"Game dir from AO_GAME_DIR: {candidate}")
            return candidate
        log.warning(f"AO_GAME_DIR={env_dir!r} does not look like a valid install")

    # 2. Saved config
    saved = _load_saved_path()
    if saved:
        log.info(f"Game dir from saved config: {saved}")
        return saved

    # 3. Windows registry
    reg_path = _check_registry()
    if reg_path:
        _save_path(reg_path)
        return reg_path

    # 4. Common paths
    for path_str in _COMMON_PATHS:
        candidate = Path(path_str)
        if _is_valid_game_dir(candidate):
            log.info(f"Found game at common path: {candidate}")
            _save_path(candidate)
            return candidate

    # 5. GUI prompt
    log.info("Game install not found automatically; prompting user")
    if allow_gui:
        selected = _prompt_gui()
        if selected:
            _save_path(selected)
            return selected

    return None


def ensure_game_dir(allow_gui: bool = True) -> Path:
    """
    Like find_game_dir() but logs a clear warning if not found.
    Returns the Path if found, or a placeholder path (may not exist).
    """
    game_dir = find_game_dir(allow_gui=allow_gui)
    if game_dir is None:
        log.warning(
            "Angels Online install directory not found.\n"
            "  Map loading and PAK file access will be unavailable.\n"
            "  Set AO_GAME_DIR=/path/to/angels-online to configure manually."
        )
        # Return the default path from config as a fallback
        return Path(r"C:\Program Files (x86)\Angels Online")
    return game_dir
