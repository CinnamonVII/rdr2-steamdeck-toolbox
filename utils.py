from typing import Optional, Tuple, List, Dict, Any
import os
import struct
import shutil
from pathlib import Path
import json
import zlib
import subprocess
import datetime
import math
import re
import glob
try:
    import vdf  # type: ignore
except ImportError:
    vdf = None

try:
    from rich.console import Console  # type: ignore
    from rich.prompt import Prompt  # type: ignore
    from rich.table import Table  # type: ignore
    from rich.panel import Panel  # type: ignore
except ImportError:
    class Console:
        def print(self, msg, style=None):
            if "[red]" in str(msg):
                print(f"ERROR: {msg}")
            elif "[success]" in str(msg) or "[bold green]" in str(msg):
                print(f"SUCCESS: {msg}")
            else:
                print(msg)
    class Prompt:
        @staticmethod
        def ask(msg, choices=None, default=None):
            return input(f"{msg}: ")
    class Table:
        def __init__(self, *a, **k): pass
        def add_column(self, *a, **k): pass
        def add_row(self, *a, **k): pass
    class Panel:
        def __init__(self, *a, **k): pass

console = Console()

BACKUP_SCHEMA_VERSION = "1.1"
BACKUP_EXTENSION      = ".rdr2cfg"

# ---------------------------------------------------------------------------
# Photo Mode Exporter
# ---------------------------------------------------------------------------

def export_photo_mode_images(prefix_path: Path, output_dir: Path) -> int:
    """
    Scans Rockstar Profiles for PRDR* photo files and exports them as JPEGs.
    Returns the count of exported images.
    """
    profiles_dir = prefix_path / "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Profiles"
    if not profiles_dir.exists():
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    exported_count = 0
    jpeg_magic = b"\xff\xd8\xff"

    for profile in profiles_dir.iterdir():
        if not profile.is_dir():
            continue
        for photo_file in profile.glob("PRDR*"):
            try:
                data = photo_file.read_bytes()
                idx = data.find(jpeg_magic)
                if idx != -1:
                    target_file = output_dir / f"{profile.name}_{photo_file.name}.jpg"
                    target_file.write_bytes(data[idx:])  # type: ignore
                    exported_count += 1  # type: ignore
            except Exception as e:
                console.print(f"[yellow]Failed to export {photo_file.name}: {e}[/yellow]")
    
    return exported_count

# ---------------------------------------------------------------------------
# Stutter Fix Utility
# ---------------------------------------------------------------------------

def clear_graphics_cache(prefix_path: Path) -> int:
    """
    Deletes RDR2 graphics cache files (sga_* and pipelinestate.cache) to fix stutters.
    Returns the count of deleted files.
    """
    settings_dir = prefix_path / "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Settings"
    if not settings_dir.exists():
        return 0

    cleared_count = 0
    targets = list(settings_dir.glob("sga_*"))
    pipeline_cache = settings_dir / "pipelinestate.cache"
    if pipeline_cache.exists():
        targets.append(pipeline_cache)

    for target in targets:
        try:
            if target.is_file():
                target.unlink()
                cleared_count += 1  # type: ignore
        except Exception as e:
            console.print(f"[yellow]Failed to delete {target.name}: {e}[/yellow]")

    return cleared_count

# ---------------------------------------------------------------------------
# Launcher Maintenance
# ---------------------------------------------------------------------------

def clear_launcher_cache(prefix_path: Path) -> int:
    """
    Clears the Rockstar Games Launcher cache in the Proton prefix.
    This can fix 'Social Club' or activation errors.
    Returns the count of deleted files/folders.
    """
    launcher_data_dirs = [
        prefix_path / "drive_c/users/steamuser/Local Settings/Application Data/Rockstar Games/Launcher",
        prefix_path / "drive_c/users/steamuser/Local Settings/Application Data/Rockstar Games/Social Club",
    ]
    
    cleared_count = 0
    for data_dir in launcher_data_dirs:
        if not data_dir.exists():
            continue
            
        cache_dirs = [
            data_dir / "Cache",
            data_dir / "GPUCache",
            data_dir / "CEF",
        ]
        
        for cache in cache_dirs:
            if cache.exists():
                try:
                    if cache.is_dir():
                        shutil.rmtree(cache)
                        cleared_count += 1
                    elif cache.is_file():
                        cache.unlink()
                        cleared_count += 1
                except Exception as e:
                    console.print(f"[yellow]Failed to clear launcher cache at {cache.name}: {e}[/yellow]")
                    
    return cleared_count

# ---------------------------------------------------------------------------
# Mod configuration discovery (Staged mods)
# ---------------------------------------------------------------------------

def _crc32_file(path: Path) -> str:
    """CRC32 of a file as zero-padded 8-char hex. Fast enough for large mod files."""
    crc = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            crc = zlib.crc32(chunk, crc)
    return format(crc & 0xFFFFFFFF, "08x")


def _detect_mod_structure(mod_dir: Path) -> str:
    """Returns 'lml_package' | 'asi_plugin' | 'stream_loose' | 'mixed' | 'unknown'."""
    files      = list(mod_dir.rglob("*"))
    has_xml    = any(f.name.lower() == "install.xml" for f in files)
    has_asi    = any(f.suffix.lower() == ".asi" for f in files if f.is_file())
    has_stream = any(f.name.lower() == "stream" for f in files if f.is_dir())
    has_lml    = (mod_dir / "lml").exists()
    if has_xml or has_lml:     return "lml_package"
    if has_asi and has_stream: return "mixed"
    if has_asi:                return "asi_plugin"
    if has_stream:             return "stream_loose"
    return "unknown"


def backup_mod_config(staging_dir: Path, output_path: Path) -> bool:
    """
    Writes a .rdr2cfg JSON snapshot of the entire mod configuration:
      - Full modlist.json  (enabled states, priorities)
      - Per-mod registry: structure type, file list, CRC32 hashes, sizes
    Binary mod files are NOT embedded; this is a config + integrity snapshot.
    The output is human-readable JSON and safe to version-control or share.
    """
    mods_staging = staging_dir / "mods"
    modlist_file = staging_dir / "modlist.json"

    if not mods_staging.exists():
        console.print("[red]No mods staging directory found. Nothing to backup.[/red]")
        return False

    modlist: Dict[str, Any] = {}
    if modlist_file.exists():
        try:
            with open(modlist_file) as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    modlist = loaded
        except Exception:
            pass

    discovered = sorted([d for d in mods_staging.iterdir() if d.is_dir()])
    for mod_dir in discovered:
        if mod_dir.name not in modlist:  # type: ignore
            modlist[mod_dir.name] = {"enabled": True, "priority": 50}  # type: ignore

    console.print(f"[cyan]Scanning {len(discovered)} mod(s) – computing CRC32 hashes...[/cyan]")

    mod_registry: Dict[str, Any] = {}
    for mod_dir in discovered:
        rel_files:   List[str]      = []
        file_hashes: Dict[str, str] = {}
        total_bytes                 = 0
        for f in sorted(mod_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = str(f.relative_to(mod_dir))
            rel_files.append(rel)
            try:
                size = f.stat().st_size  # type: ignore
                file_hashes[rel] = _crc32_file(f)
                total_bytes     += size
            except OSError:
                file_hashes[rel] = "ioerror"
        mod_registry[mod_dir.name] = {
            "structure_type": _detect_mod_structure(mod_dir),
            "file_count":     len(rel_files),
            "total_size_kb":  round(total_bytes / 1024, 1),  # type: ignore
            "files":          rel_files,
            "file_hashes":    file_hashes,
        }

    payload = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at":     datetime.datetime.now().isoformat(timespec="seconds"),
        "app_id":         "1174180",
        "mod_count":      len(mod_registry),
        "modlist":        modlist,
        "mod_registry":   mod_registry,
    }

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        size_kb = round(output_path.stat().st_size / 1024, 1)  # type: ignore
        console.print(
            f"\n[bold green]Backup saved:[/bold green] it's located at [cyan]{output_path}[/cyan] "
            f"({size_kb} KB — {len(mod_registry)} mod(s) catalogued)"
        )
        return True
    except (OSError, TypeError) as e:
        console.print(f"[red]Failed to write backup: {e}[/red]")
        return False


def restore_mod_config(backup_path: Path, staging_dir: Path) -> Tuple[bool, List[str], List[str]]:
    """
    Restores mod configuration from a .rdr2cfg backup.

    - Merges backup modlist entries into the current modlist.json (backup wins).
    - Verifies CRC32 hashes for mods present in staging; warns on mismatches.
    - Reports mods absent from staging (config noted, cannot be applied yet).
    - Never deletes or modifies existing staging mod files.

    Returns (success, restored_mods, missing_mods).
    """
    if not backup_path.exists():
        console.print(f"[red]Backup file not found: {backup_path}[/red]")
        return False, [], []

    try:
        with open(backup_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        console.print(f"[red]Could not parse backup file: {e}[/red]")
        return False, [], []

    schema = payload.get("schema_version", "unknown")
    if schema not in ("1.0", "1.1"):
        console.print(f"[yellow]Warning: unknown schema version '{schema}'. Attempting import anyway.[/yellow]")

    backup_modlist:  Dict[str, Any] = payload.get("modlist",      {})
    backup_registry: Dict[str, Any] = payload.get("mod_registry", {})
    created_at = payload.get("created_at", "unknown")

    console.print(Panel(
        f"[bold]Created:[/bold]        {created_at}\n"
        f"[bold]Schema:[/bold]         {schema}\n"
        f"[bold]Mods in backup:[/bold] {len(backup_registry)}",
        title="[cyan]Restore Preview[/cyan]", expand=False,
    ))

    mods_staging     = staging_dir / "mods"
    modlist_file     = staging_dir / "modlist.json"
    current_modlist: Dict[str, Any] = {}
    if modlist_file.exists():
        try:
            with open(modlist_file) as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    current_modlist = loaded
        except Exception:
            pass

    restored_mods: List[str] = []
    missing_mods:  List[str] = []
    hash_warnings: List[str] = []

    table = Table(title="Restore Status", show_lines=True)
    table.add_column("Mod",        style="white",  min_width=22)
    table.add_column("Structure",  style="cyan",   width=14)
    table.add_column("Files",      style="yellow", width=6,  justify="right")
    table.add_column("Status",     style="bold",   width=22)
    table.add_column("Integrity",  style="dim",    width=16)

    for mod_name, reg in backup_registry.items():
        mod_dir        = mods_staging / mod_name
        structure_type = reg.get("structure_type", "unknown")
        file_count     = reg.get("file_count",     len(reg.get("files", [])))
        backup_hashes  = reg.get("file_hashes",    {})

        if not mod_dir.exists():
            missing_mods.append(mod_name)
            table.add_row(mod_name, structure_type, str(file_count),  # type: ignore
                          "[red]MISSING[/red]", "[dim]n/a[/dim]")
            continue

        mismatches = 0
        for rel_path, expected in backup_hashes.items():
            actual_file = mod_dir / rel_path
            if not actual_file.exists():
                mismatches += 1
                continue
            try:
                if _crc32_file(actual_file) != expected:
                    mismatches += 1
            except OSError:
                mismatches += 1

        if mismatches == len(backup_hashes) and len(backup_hashes) > 0:
            missing_mods.append(mod_name)
            table.add_row(mod_name, structure_type, str(file_count),  # type: ignore
                          "[red]MISSING (empty dir)[/red]", "[red]0/N[/red]")
            continue

        integrity_str = "[green]OK[/green]" if mismatches == 0 else f"[yellow]{mismatches} changed[/yellow]"
        if mismatches:
            hash_warnings.append(mod_name)

        current_modlist[mod_name] = backup_modlist.get(mod_name, {"enabled": True, "priority": 50})  # type: ignore
        restored_mods.append(mod_name)
        table.add_row(mod_name, structure_type, str(file_count), "[green]RESTORED[/green]", integrity_str)  # type: ignore

    console.print(table)

    if missing_mods:
        console.print(f"\n[yellow]{len(missing_mods)} mod(s) absent from staging:[/yellow]")
        for name in missing_mods:
            cfg   = backup_modlist.get(name, {})
            state = "[green]enabled[/green]" if cfg.get("enabled", True) else "[red]disabled[/red]"
            console.print(f"   [dim]•[/dim] [white]{name}[/white]  (was {state}, priority {cfg.get('priority', 50)})")
        console.print("[dim]  Re-install these mods via option 7 or 9 then restore again.[/dim]")

    if hash_warnings:
        console.print(f"\n[yellow]{len(hash_warnings)} mod(s) have files that differ from the backup snapshot:[/yellow]")
        for name in hash_warnings:
            console.print(f"   [dim]•[/dim] [white]{name}[/white]")
        console.print("[dim]  Config was restored; files are untouched.[/dim]")

    if restored_mods:
        try:
            modlist_file.parent.mkdir(parents=True, exist_ok=True)
            with open(modlist_file, "w") as f:
                json.dump(current_modlist, f, indent=4)
            console.print(f"\n[bold green]Config restored for {len(restored_mods)} mod(s). modlist.json updated.[/bold green]")
        except OSError as e:
            console.print(f"[red]Failed to write modlist.json: {e}[/red]")
            return False, restored_mods, missing_mods
    else:
        console.print("[yellow]No mods could be restored (all missing from staging).[/yellow]")

    return True, restored_mods, missing_mods


def list_backups(backup_dir: Path) -> List[Dict]:
    """Lists all .rdr2cfg files in backup_dir, sorted newest-first."""
    if not backup_dir.exists():
        return []
    entries: List[Dict] = []
    for f in backup_dir.glob(f"*{BACKUP_EXTENSION}"):
        if not f.is_file():
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            try:
                created_at = datetime.datetime.fromisoformat(data.get("created_at", ""))
            except (ValueError, TypeError):
                created_at = datetime.datetime.fromtimestamp(f.stat().st_mtime)
            entries.append({
                "path": f, "name": f.name, "created_at": created_at,
                "mod_count": data.get("mod_count", 0),
                "schema_version": data.get("schema_version", "?"),
                "size_kb": round(f.stat().st_size / 1024, 1),  # type: ignore
            })
        except (json.JSONDecodeError, OSError):
            try:
                entries.append({
                    "path": f, "name": f.name,
                    "created_at": datetime.datetime.fromtimestamp(f.stat().st_mtime),
                    "mod_count": -1, "schema_version": "corrupt",
                    "size_kb": round(f.stat().st_size / 1024, 1),  # type: ignore
                })
            except OSError:
                pass
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    return entries


# =============================================================================
# Steam / installation discovery
# =============================================================================

def get_steam_root() -> Optional[Path]:
    candidates = [
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    for path in candidates:
        if (path / "steamapps/compatdata/1174180").exists():
            return path
    for path in candidates:
        if path.exists():
            return path
    return None

def find_rdr2_installation() -> Tuple[Optional[Path], Optional[Path]]:
    all_library_folders: List[Path] = []
    steam_roots = [
        Path.home() / ".local/share/Steam",
        Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    ]
    
    sd_patterns = [
        "/run/media/mmcblk0p*/SteamLibrary",
        "/run/media/deck/*/SteamLibrary",
    ]
    for pattern in sd_patterns:
        for p in glob.glob(pattern):
            candidate = Path(p)
            if candidate not in all_library_folders:
                all_library_folders.append(candidate)
    
    default_root = get_steam_root()
    if default_root is not None and default_root not in steam_roots:
        steam_roots.append(default_root)

    for root in steam_roots:
        vdf_path = root / "steamapps/libraryfolders.vdf"
        if vdf_path.exists():
            if vdf:
                try:
                    with open(vdf_path, "r") as f:
                        data = vdf.load(f)
                    for key in data.get("libraryfolders", {}):
                        entry = data["libraryfolders"][key]
                        lib_path = Path(entry["path"] if isinstance(entry, dict) else entry)
                        if lib_path not in all_library_folders:
                            all_library_folders.append(lib_path)
                except Exception:
                    pass
            else:
                try:
                    text = vdf_path.read_text()
                    paths = re.findall(r'"path"\s+"([^"]+)"', text)
                    for p in paths:
                        lib_p = Path(p)
                        if lib_p not in all_library_folders:
                            all_library_folders.append(lib_p)
                except Exception:
                    pass
        
        if (root / "steamapps").exists() and root not in all_library_folders:
            all_library_folders.append(root)

    for path in all_library_folders:
        game_path = path / "steamapps/common/Red Dead Redemption 2"
        if (game_path / "RDR2.exe").exists():
            prefix_path = path / "steamapps/compatdata/1174180/pfx"
            if not prefix_path.exists() and default_root:
                prefix_path = default_root / "steamapps/compatdata/1174180/pfx"
            return game_path, prefix_path

    return None, None