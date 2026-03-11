#!/home/deck/rdr2-steamdeck-toolbox/venv/bin/python
import os
import errno
import sys
import datetime
import json
import struct
import shutil
import tempfile
import re
import subprocess
import zipfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from utils import (  # type: ignore
    get_steam_root,
    find_rdr2_installation,
    backup_mod_config,
    restore_mod_config,
    list_backups,
    BACKUP_EXTENSION,
    export_photo_mode_images,
    clear_graphics_cache,
    clear_launcher_cache,
)
from save_modifier import (  # type: ignore
    validate_and_sign_srdr,
    edit_save_file,
    list_save_files,
    select_save_file,
    handle_option_4,
    farm_honor,
    create_save_snapshot,
    restore_save_snapshot,
)

try:
    from rich.console import Console  # type: ignore
    from rich.panel import Panel  # type: ignore
    from rich.prompt import Prompt  # type: ignore
    from rich.table import Table  # type: ignore
    from rich import print as rprint  # type: ignore
    import requests  # type: ignore
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:
    class Console:
        def print(self, msg, style=None):
            if hasattr(msg, "__rich__"):
                msg = msg.__rich__()
            msg_str = str(msg)
            msg_str = re.sub(r'\[.*?\]', '', msg_str)
            print(msg_str)
    class Panel:
        def __init__(self, content, **kwargs): self.content = content
        def __rich__(self): return self.content
        def __str__(self): return str(self.content)
    class Prompt:
        @staticmethod
        def ask(text, **kwargs):
            choices = kwargs.get('choices')
            default = kwargs.get('default')
            prompt_text = text
            if choices: prompt_text += f" ({'/'.join(choices)})"
            if default: prompt_text += f" [{default}]"
            try:
                res = input(f"{prompt_text}: ").strip()
            except EOFError:
                res = ""
            return res if res else (default if default is not None else "")
    class Table:
        def __init__(self, **kwargs): pass
        def add_column(self, *args, **kwargs): pass
        def add_row(self, *args, **kwargs): pass
    
    requests = None
    BeautifulSoup = None
    rprint = print
    print("Warning: Optional libraries ('rich', 'requests', 'beautifulsoup4') not found. Features will be limited.")



console = Console()
APP_ID = "1174180"

TOOLBOX_DIR   = Path.home() / "RDR2_Toolbox"
STAGING_DIR   = TOOLBOX_DIR / "staging"
BACKUP_DIR    = TOOLBOX_DIR / "backups"
PROFILES_DIR  = TOOLBOX_DIR / "profiles"
MANIFEST_FILE = TOOLBOX_DIR / "manifest.json"
SCRIPTHOOK_URL = "https://www.dev-c.com/files/ScriptHookRDR2_1.0.1491.17.zip"
LML_URL = "https://www.rdr2mods.com/downloads/rdr2/tools/76-lennys-mod-loader-rdr/"
SKIP_INTRO_URL = "https://www.nexusmods.com/reddeadredemption2/mods/36"

def set_simulation_mode(base_dir: Path):
    global TOOLBOX_DIR, STAGING_DIR, BACKUP_DIR, MANIFEST_FILE
    TOOLBOX_DIR   = base_dir
    STAGING_DIR   = TOOLBOX_DIR / "staging"
    BACKUP_DIR    = TOOLBOX_DIR / "backups"
    PROFILES_DIR  = TOOLBOX_DIR / "profiles"
    MANIFEST_FILE = TOOLBOX_DIR / "manifest.json"
    setup_directories()


def setup_directories():
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    if not MANIFEST_FILE.exists():
        with open(MANIFEST_FILE, "w") as f:
            json.dump({}, f)

def manual_fallback_prompt() -> Tuple[Optional[Path], Optional[Path]]:
    console.print("[yellow]Could not automatically locate RDR2 (AppID 1174180).[/yellow]")
    path_input = Prompt.ask("Please paste the absolute path to your 'Red Dead Redemption 2' folder")
    game_path = Path(path_input)
    prefix_path: Optional[Path] = None
    if "common" in game_path.parts:
        try:
            parts_list = list(game_path.parts)
            # Find index safely
            idx = -1
            for i, p in enumerate(parts_list):
                if p == "common":
                    idx = i
                    break
            if idx != -1:
                # Reconstruct path safely without slicing if linter is confused
                base_path = Path("/")
                for i in range(idx):
                    base_path = base_path / parts_list[i]
                candidate_prefix = base_path / "compatdata" / APP_ID / "pfx"
                if candidate_prefix.exists():
                    prefix_path = candidate_prefix
        except Exception:
            pass
    return game_path, prefix_path

def print_proton_setup(prefix: Optional[Path]):
    if not prefix:
        console.print("[yellow]Proton prefix not found. Cannot provide specific setup instructions.[/yellow]")
        return

    console.print("\n[bold cyan]--- Proton Setup Instructions ---[/bold cyan]")
    if prefix.exists():
        console.print(f"[green]Prefix located at:[/green] {prefix}")
    else:
        console.print("[red]Warning: Proton prefix (compatdata) not found![/red]")
    console.print("\nTo enable ASI Loaders (ScriptHookRDR2), add the following to RDR2's Steam Launch Options:")
    console.print(Panel("WINEDLLOVERRIDES=\"dinput8,version,ScriptHookRDR2=n,b\" %command%", border_style="green"))

def load_manifest() -> Dict[str, Any]:
    if MANIFEST_FILE.exists():
        try:
            with open(MANIFEST_FILE, "r") as f:
                content = json.load(f)
                return content if isinstance(content, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def save_manifest(manifest: Dict[str, Any]):
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=4)

def handle_mod_list_editor(game_path: Optional[Path], prefix_path: Optional[Path]):
    modlist_file = STAGING_DIR / "modlist.json"
    if not modlist_file.exists():
        console.print("[yellow]No mods registered. Install some first.[/yellow]")
        return

    try:
        with open(modlist_file, "r") as f:
            modlist = json.load(f)
    except Exception:
        modlist = {}

    if not modlist:
        console.print("[yellow]Modlist is empty.[/yellow]")
        return

    table = Table(title="Mod Manager")  # type: ignore
    table.add_column("#", style="dim")
    table.add_column("Mod Name")
    table.add_column("Status")
    table.add_column("Priority")

    mod_names = sorted(modlist.keys())
    for i, name in enumerate(mod_names):
        status = "[green]Enabled[/green]" if modlist[name].get("enabled", True) else "[red]Disabled[/red]"  # type: ignore
        table.add_row(str(i+1), name, status, str(modlist[name].get("priority", 50)))  # type: ignore

    console.print(table)
    console.print("Option: [bold]e[/bold] <num> to toggle, [bold]p[/bold] <num> <val> for priority, [bold]0[/bold] to back")
    
    cmd = Prompt.ask("Action").lower().strip()
    if cmd == "0" or not cmd:
        return

    try:
        if cmd.startswith("e "):
            idx = int(cmd.split()[1]) - 1
            selected = mod_names[idx]
            enabled = modlist[selected].get("enabled", True)  # type: ignore
            modlist[selected]["enabled"] = not enabled  # type: ignore
            with open(modlist_file, "w") as f:
                json.dump(modlist, f, indent=4)
            
            if game_path and (STAGING_DIR / "modlist.json").exists():
                console.print("[cyan]Re-deploying mods...[/cyan]")
                clean_purge()
                deploy_mods(game_path, prefix_path)
            else:
                console.print("[yellow]Config saved but mods NOT re-deployed (game path unknown). Run option 1 first.[/yellow]")
        elif cmd.startswith("p "):
            parts = cmd.split()
            idx = int(parts[1]) - 1
            val = int(parts[2])
            selected = mod_names[idx]
            modlist[selected]["priority"] = val  # type: ignore
            with open(modlist_file, "w") as f:
                json.dump(modlist, f, indent=4)
            console.print(f"[success]Priority for {selected} set to {val}.[/success]")
    except (ValueError, IndexError):
        console.print("[red]Invalid command format.[/red]")

def apply_wine_overrides(prefix_path: Optional[Path]):
    if not prefix_path:
        console.print("[red]Prefix path not provided. Skipping Wine overrides.[/red]")
        return
    assert prefix_path is not None

    user_reg_path = prefix_path / "user.reg"
    if not user_reg_path.exists():
        console.print(f"[yellow]Warning: user.reg not found in {prefix_path}. DLL overrides may not stick.[/yellow]")
        return
    overrides = {
        "dinput8": "native,builtin",
        "version": "native,builtin",
        "ScriptHookRDR2": "native,builtin",
        "vulkan-1": "native,builtin"
    }
    section_header = '[Software\\\\Wine\\\\DllOverrides]'
    try:
        with open(user_reg_path, "r") as f:
            lines = f.readlines()

        new_lines = []
        section_found = False
        in_target_section = False
        for line in lines:
            stripped = line.strip()
            if stripped == section_header:
                section_found = True
                in_target_section = True
                new_lines.append(line)
                continue
            if in_target_section:
                if stripped.startswith("[") and stripped != section_header:
                    in_target_section = False
                elif any(stripped.startswith(f'"{dll}"=') for dll in overrides):
                    continue
            new_lines.append(line)

        if not section_found:
            if new_lines and not new_lines[-1].endswith('\n'):
                new_lines.append('\n')
            new_lines.append(f"\n{section_header}\n")

        final_lines = []
        i = 0
        section_injected = False
        while i < len(new_lines):
            line = new_lines[i]
            final_lines.append(line)
            if line.strip() == section_header and not section_injected:
                for dll, val in overrides.items():
                    final_lines.append(f'"{dll}"="{val}"\n')
                section_injected = True
                i += 1
                while i < len(new_lines) and not new_lines[i].strip().startswith("["):  # type: ignore
                    stripped_inner = new_lines[i].strip()  # type: ignore
                    if not any(stripped_inner.startswith(f'"{dll}"=') for dll in overrides):
                         if stripped_inner:
                             final_lines.append(new_lines[i])  # type: ignore
                    i += 1
                continue
            i += 1
        new_lines = final_lines

        with open(user_reg_path, "w") as f:
            f.writelines(new_lines)
        console.print("[SUCCESS] WINE DLL Overrides injected into user.reg.", style="bold green")
    except Exception as e:
        console.print(f"[red]Failed to inject DLL overrides: {e}[/red]")

def set_windows_version(prefix_path: Optional[Path]):
    if not prefix_path: return
    user_reg_path = prefix_path / "user.reg"
    if not user_reg_path.exists():
         console.print(f"[yellow]Warning: user.reg not found in {prefix_path}.[/yellow]")
         return
    section_header = '[Software\\\\Wine]'
    try:
        with open(user_reg_path, "r") as f:
            lines = f.readlines()

        new_lines = []
        section_found = False
        in_target_section = False
        version_found = False

        for line in lines:
            stripped = line.strip()
            if stripped == section_header:
                section_found = True
                in_target_section = True
                new_lines.append(line)
                continue
            if in_target_section:
                if stripped.startswith("[") and stripped != section_header:
                    in_target_section = False
                elif stripped.startswith('"Version"='):
                    new_lines.append('"Version"="win10"\n')
                    version_found = True
                    continue
            new_lines.append(line)

        if not section_found:
            if new_lines and not new_lines[-1].endswith('\n'):
                new_lines.append('\n')
            new_lines.append(f"\n{section_header}\n")
            new_lines.append('"Version"="win10"\n')
        elif not version_found:
            idx = -1
            for i, l in enumerate(new_lines):
                if l.strip() == section_header:
                    idx = i + 1
                    break
            if idx != -1:
                new_lines.insert(idx, '"Version"="win10"\n')
            else:
                console.print(f"[yellow]Warning: Could not locate {section_header} for insertion.[/yellow]")

        with open(user_reg_path, "w") as f:
            f.writelines(new_lines)
        console.print("[SUCCESS] Prefix OS version set to Windows 10 (Fixes launcher errors).", style="bold green")
    except Exception as e:
        console.print(f"[red]Failed to set Windows version: {e}[/red]")

def update_lml_mods_xml(game_path: Path, mod_name: str, enabled: bool = True):
    lml_dir = game_path / "lml"
    mods_xml = lml_dir / "mods.xml"
    if not lml_dir.exists():
        lml_dir.mkdir(parents=True, exist_ok=True)
    if not mods_xml.exists():
        root = ET.Element("Mods")
        tree = ET.ElementTree(root)
    else:
        try:
            tree = ET.parse(mods_xml)
            root = tree.getroot()
        except Exception:
            root = ET.Element("Mods")
            tree = ET.ElementTree(root)
    mod_found = False
    for mod in root.findall("Mod"):
        if mod.get("folder") == mod_name:
            mod_found = True
            enabled_elem = mod.find("Enabled")
            if enabled_elem is not None:
                enabled_elem.text = str(enabled).lower()
            break
    if not mod_found:
        mod_elem = ET.SubElement(root, "Mod", folder=mod_name)
        enabled_elem = ET.SubElement(mod_elem, "Enabled")
        enabled_elem.text = str(enabled).lower()
        overwrite_elem = ET.SubElement(mod_elem, "Overwrite")
        overwrite_elem.text = "false"
    try:
        if hasattr(ET, "indent"):
            ET.indent(root, space="    ")
        tree.write(mods_xml, encoding='utf-8', xml_declaration=True)  # type: ignore
    except Exception as e:
        console.print(f"[red]Failed to update mods.xml: {e}[/red]")

def download_item(url: str, dest: Path, title: str) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        console.print(f"[cyan]Downloading {title}...[/cyan]")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        if "dev-c.com" in url:
            headers['Referer'] = 'http://www.dev-c.com/rdr2/scripthookrdr2/'
        elif "rdr2mods.com" in url:
            headers['Referer'] = 'https://www.rdr2mods.com/downloads/rdr2/tools/76-lennys-mod-loader-rdr/'

        if requests is not None:
            response = requests.get(url, headers=headers, timeout=30, stream=True)
            if response.status_code != 200:
                console.print(f"[red]Failed to download {title}: HTTP {response.status_code}[/red]")
                return False
            with open(dest, 'wb') as out_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        out_file.write(chunk)
        else:
            import urllib.request as request_lib
            opener = request_lib.build_opener(request_lib.HTTPCookieProcessor())
            request_lib.install_opener(opener)
            
            req = request_lib.Request(url, headers=headers)
            try:
                with opener.open(req, timeout=30) as response:
                    with open(dest, 'wb') as out_file:
                        shutil.copyfileobj(response, out_file)
            except Exception as e:
                console.print(f"[red]Urllib download failed for {title}: {e}[/red]")
                return False

        return True
    except Exception as e:
        console.print(f"[red]Failed to download {title}: {e}[/red]")
        return False

def check_and_install_scripthook(game_path: Optional[Path], interactive: bool = True):
    if not game_path:
        console.print("[red]Game path not provided. Skipping ScriptHook check.[/red]")
        return

    dinput_path = game_path / "dinput8.dll"
    scripthook_path = game_path / "ScriptHookRDR2.dll"
    if dinput_path.exists() and scripthook_path.exists():
        if not interactive:
            return
        if Prompt.ask("ScriptHookRDR2 already exists. Reinstall? (y/N)", default="n").lower() != "y":
            return
    console.print(f"[cyan]Installing ScriptHookRDR2 to: {game_path}[/cyan]")
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = Path(tmp_dir) / "scripthook.zip"
        if download_item(SCRIPTHOOK_URL, zip_path, "ScriptHookRDR2"):
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for member in zip_ref.namelist():
                    filename = os.path.basename(member)
                    if filename.lower() == "scripthookrdr2.dll":
                        with zip_ref.open(member) as source, open(scripthook_path, "wb") as target:
                            shutil.copyfileobj(source, target)
                        console.print(f"  [green]Extracted {filename}[/green]")
                    elif filename.lower() == "dinput8.dll":
                        with zip_ref.open(member) as source, open(dinput_path, "wb") as target:
                            shutil.copyfileobj(source, target)
                        console.print(f"  [green]Extracted {filename}[/green]")
            console.print("[SUCCESS] ScriptHookRDR2 and dinput8.dll installed.", style="bold green")

def install_lml(game_path: Path):
    lml_dir = game_path / "lml"
    vfs_path = game_path / "vfs.asi"
    if lml_dir.exists() and vfs_path.exists():
        console.print("[green]Lenny's Mod Loader is already installed.[/green]")
        return
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = Path(tmp_dir) / "lml.zip"
        if download_item(LML_URL, zip_path, "Lenny's Mod Loader"):
            if not zipfile.is_zipfile(zip_path):
                console.print("[red]LML download was blocked (Cloudflare). Please download it manually:[/red]")
                console.print("[cyan]https://www.rdr2mods.com/downloads/rdr2/tools/76-lennys-mod-loader-rdr/[/cyan]")
                console.print(f"[cyan]Then run option 9 and point it at the zip.[/cyan]")
                return
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                extract_path = Path(tmp_dir) / "extracted"
                zip_ref.extractall(extract_path)
            install_lml_from_path(extract_path, game_path)
            console.print("[SUCCESS] Lenny's Mod Loader installed.", style="bold green")
        else:
            console.print("[yellow]Automatic LML download failed. Please download it manually:[/yellow]")
            console.print(f"[cyan]{LML_URL}[/cyan]")
            console.print("[yellow]Then extract 'vfs.asi' and everything inside 'ModLoader' to your RDR2 folder.[/yellow]")

def install_lml_from_path(extract_path: Path, game_path: Path):
    # Pass 1: find and copy vfs.asi
    for f in extract_path.rglob("*"):
        if f.is_file() and f.name.lower() == "vfs.asi":
            shutil.copy2(f, game_path / "vfs.asi")
            if (f.parent / "lml").is_dir():
                shutil.copytree(f.parent / "lml", game_path / "lml", dirs_exist_ok=True)
            break

    # Pass 2: find and copy ModLoader contents
    for root, _, files in os.walk(extract_path):
        root_path = Path(root)
        if root_path.name == "ModLoader":
            shutil.copytree(root_path, game_path, dirs_exist_ok=True)
            break

def deploy_mods(game_path: Path, prefix_path: Optional[Path] = None):
    mods_staging = STAGING_DIR / "mods"
    if not mods_staging.exists():
        console.print("[yellow]Mods staging directory does not exist. Run Nexus Integration first.[/yellow]")
        return

    modlist_file = STAGING_DIR / "modlist.json"
    modlist: Dict[str, Any] = {}
    if modlist_file.exists():
        try:
            with open(modlist_file, "r") as f:
                content = f.read()
                if content:
                    loaded = json.loads(content)
                    if isinstance(loaded, dict):
                        modlist = loaded
        except Exception:
            modlist = {}

    discovered_mods = [d for d in mods_staging.iterdir() if d.is_dir()]
    for mod_dir in discovered_mods:
        if isinstance(modlist, dict) and mod_dir.name not in modlist:
            modlist[mod_dir.name] = {"enabled": True, "priority": 50}

    with open(modlist_file, "w") as f:
        json.dump(modlist, f, indent=4)

    deployment_queue: Dict[str, Dict[str, Any]] = {}
    lml_installed = False
    requires_scripthook = False
    for mod_name, data in sorted(modlist.items(), key=lambda x: x[1].get('priority', 50) if isinstance(x[1], dict) else 50):  # type: ignore
        if not isinstance(data, dict) or not data.get("enabled", True):
            continue
        # Ensure name is string for junction
        name_str = str(mod_name)
        mod_dir = Path(mods_staging) / name_str
        if not mod_dir.exists():
            continue

        mod_files = list(mod_dir.rglob("*"))
        is_lml_package = any(f.name.lower() == "install.xml" for f in mod_files)
        if is_lml_package and not lml_installed:
            install_lml(game_path)
            lml_installed = True

        if is_lml_package:
            update_lml_mods_xml(game_path, mod_name)

        for file_path in mod_files:
            if not file_path.is_file(): continue
            rel_path = file_path.relative_to(mod_dir)
            file_name = file_path.name.lower()

            if file_name.endswith(".asi"):
                requires_scripthook = True

            if file_name.endswith(".asi") or file_name in ["scripthookrdr2.dll", "dinput8.dll", "version.dll"]:
                target_path = game_path / file_path.name
            elif is_lml_package:
                target_path = game_path / "lml" / mod_name / rel_path
            elif file_path.suffix.lower() in [".ymt", ".ytd", ".ydr", ".dat", ".meta"]:
                target_path = game_path / "lml" / "stream" / file_path.name
            else:
                target_path = game_path / rel_path

            deployment_queue[str(target_path)] = {
                "source": str(file_path),
                "mod_id": mod_name,
                "priority": data.get('priority', 50)
            }

    if requires_scripthook and game_path:
        check_and_install_scripthook(game_path, interactive=False)
        if prefix_path:
            apply_wine_overrides(prefix_path)

    manifest = load_manifest()
    new_manifest_entries = {}
    linked_count = 0
    for target_str, info in deployment_queue.items():
        target_path = Path(target_str)
        source_path = Path(info["source"])
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if not isinstance(manifest, dict):
            manifest = {}

        if target_path.exists():
            is_managed = False
            if target_path.is_file():
                entry = manifest.get(str(target_path))
                if isinstance(entry, dict):
                    if entry.get("type") == "copy":
                        is_managed = True
                    else:
                        try:
                            if target_path.stat().st_ino == source_path.stat().st_ino:
                                is_managed = True
                        except OSError:
                            pass
                else:
                    try:
                        if target_path.stat().st_ino == source_path.stat().st_ino:
                            is_managed = True
                    except OSError:
                        pass

            if is_managed:
                try:
                    entry_type = "hardlink" if target_path.stat().st_ino == source_path.stat().st_ino else "copy"
                    new_manifest_entries[str(target_path)] = {"source": info["source"], "type": entry_type}
                    linked_count += 1
                except OSError:
                    pass
                continue
            else:
                try:
                    os.unlink(target_path)
                except OSError as e:
                    console.print(f"[red]Could not remove existing file {target_path.name}: {e}[/red]")
                    continue

        try:
            os.link(source_path, target_path)
            new_manifest_entries[str(target_path)] = {"source": info["source"], "type": "hardlink"}
            linked_count += 1
        except OSError as e:
            if e.errno == errno.EXDEV:
                try:
                    shutil.copy2(source_path, target_path)
                    new_manifest_entries[str(target_path)] = {"source": info["source"], "type": "copy"}
                    linked_count += 1
                except OSError as copy_err:
                    console.print(f"[red]Fallback copy failed for {target_path.name}: {copy_err}[/red]")
            else:
                console.print(f"[red]Failed to link {target_path.name}: {e}[/red]")

    manifest.update(new_manifest_entries)
    save_manifest(manifest)
    console.print(f"[SUCCESS] Deployed {linked_count} files.", style="bold green")

def clean_purge():
    console.print("\n[cyan]Starting Clean Purge...[/cyan]")
    manifest = load_manifest()
    removed_count = 0
    failed_entries = []

    for target_path_str, source_info in manifest.items():
        target_path = Path(target_path_str)
        source_path_str = source_info["source"] if isinstance(source_info, dict) else source_info
        source_path = Path(source_path_str)
        
        is_managed = False
        if target_path.exists() and target_path.is_file():
            if isinstance(source_info, dict) and source_info.get("type") == "copy":
                if source_path.exists() and target_path.stat().st_size == source_path.stat().st_size:
                    is_managed = True
            elif source_path.exists():
                try:
                    if target_path.stat().st_ino == source_path.stat().st_ino:
                        is_managed = True
                except OSError:
                    pass

        if is_managed:
            try:
                os.unlink(target_path)
                removed_count += 1
            except Exception as e:
                console.print(f"[red]Failed to unlink {target_path}: {e}[/red]")
                failed_entries.append(target_path_str)
        else:
            if not target_path.exists():
                console.print(f"[yellow]Skipping unlink: {target_path} not found.[/yellow]")
            else:
                console.print(f"[yellow]Skipping {target_path.name}: not a managed hardlink.[/yellow]")

    updated_manifest = {k: manifest[k] for k in failed_entries}
    save_manifest(updated_manifest)

    if len(failed_entries) == 0:
        console.print(f"[SUCCESS] Purged {removed_count} modded files. Directory restored vanilla.", style="bold green")
    else:
        console.print(f"[WARNING] Purged {removed_count} files, but {len(failed_entries)} failed. Review manifest.", style="bold yellow")


# ---------------------------------------------------------------------------
# Maintenance helpers
# ---------------------------------------------------------------------------

def fix_sh_black_screen(game_path: Path):
    ini_path = game_path / "ScriptHookConfig.ini"
    if ini_path.exists():
        content = ini_path.read_text()
        content = re.sub(r"^Enable\s*=\s*true", "Enable = false", content, flags=re.MULTILINE | re.IGNORECASE)
        ini_path.write_text(content)
        console.print("[success]ScriptHookConfig.ini patched to prevent black screen (EnableUI=false).[/success]")
    else:
        ini_path.write_text("[Interface]\nEnable = false\n[Overlay]\nEnable = false\n")
        console.print("[success]Created ScriptHookConfig.ini with black screen prevention settings.[/success]")

def ensure_vulkan(prefix_path: Optional[Path]):
    if not prefix_path: return
    xml_path = (prefix_path /
                "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Settings/system.xml")
    if not xml_path.exists():
        console.print("[yellow]Settings system.xml not found. Skipping Vulkan check.[/yellow]")
        return
    try:
        content = xml_path.read_bytes()
        encoding = 'utf-16' if content[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'  # type: ignore
        text = content.decode(encoding)  # type: ignore
        if b'<API>kSettingAPI_Vulkan</API>' in text.encode(encoding):
            console.print("[SUCCESS] API already set to Vulkan.", style="bold green")
            return
        new_text = re.sub(r'<API>kSettingAPI_\w+</API>', '<API>kSettingAPI_Vulkan</API>', text, count=1)
        if new_text != text:
             xml_path.write_bytes(new_text.encode(encoding))  # type: ignore
             console.print(f"[SUCCESS] API switched to Vulkan in system.xml (format preserved, encoding: {encoding}).", style="bold green")
        else:
             console.print("[warning]Could not find <API> tag in system.xml[/warning]")
    except Exception as e:
        console.print(f"[red]Failed to patch system.xml: {e}[/red]")

def patch_xml_tag(text: str, key: str, new_val: str) -> str:
    """Case-insensitive XML tag/attribute patcher. Preserves original tag casing."""
    def sub_tag(m):
        tag = m.group(1)
        return f'<{tag}>{new_val}</{tag}>'

    def sub_attr(m):
        tag = m.group(1)
        return f'<{tag} value="{new_val}" />'

    pattern_tag = re.compile(rf'<({key})>(.*?)</\1>', re.IGNORECASE | re.DOTALL)
    if pattern_tag.search(text):
        return pattern_tag.sub(sub_tag, text)

    pattern_attr = re.compile(rf'<({key}) value="(.*?)" />', re.IGNORECASE)
    if pattern_attr.search(text):
        return pattern_attr.sub(sub_attr, text)

    return text

def set_adapter_index(prefix_path: Optional[Path]):
    if not prefix_path: return
    xml_path = (prefix_path /
                "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Settings/system.xml")
    if not xml_path.exists():
        console.print("[yellow]Settings system.xml not found.[/yellow]")
        return
    try:
        content = xml_path.read_bytes()
        encoding = 'utf-16' if content[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'  # type: ignore
        text = content.decode(encoding)  # type: ignore
        new_text = patch_xml_tag(text, "adapterIndex", "0")
        if new_text != text:
            xml_path.write_bytes(new_text.encode(encoding))  # type: ignore
            console.print("[SUCCESS] Adapter Index set to 0 (Primary).", style="bold green")
        else:
            if 'adapterIndex' not in text.lower():
                 console.print("[warning]Could not find <adapterIndex> tag in system.xml[/warning]")
            else:
                 console.print("[info]Adapter Index is already 0.", style="bold green")
    except Exception as e:
        console.print(f"[red]Failed to patch system.xml: {e}[/red]")

# ---------------------------------------------------------------------------
# Maintenance Helpers
# ---------------------------------------------------------------------------

def cleanup_all_backups(game_path: Optional[Path], prefix_path: Optional[Path]):
    """Purges .bak and _CLONED_ save files from the prefix."""
    if not prefix_path:
        console.print("[red]Prefix path unknown.[/red]")
        return
        
    profiles_dir = prefix_path / "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Profiles"
    if not profiles_dir.exists():
        console.print("[red]Profiles directory not found.[/red]")
        return
        
    purged_count = 0
    for profile in profiles_dir.iterdir():
        if not profile.is_dir():
            continue
        targets = list(profile.glob("*.bak")) + list(profile.glob("*_CLONED_*"))
        for t in targets:
            try:
                t.unlink()
                purged_count += 1
            except OSError as e:
                console.print(f"[yellow]Failed to delete {t.name}: {e}[/yellow]")
                
    console.print(f"[bold green]Purged {purged_count} backup/cloned save files.[/bold green]")

def open_game_folder(game_path: Optional[Path]):
    if not game_path:
        console.print("[red]Game path unknown.[/red]")
        return
    console.print(f"[cyan]Opening game folder: {game_path}[/cyan]")
    subprocess.run(["xdg-open", str(game_path)], check=False)

def open_save_folder(prefix_path: Optional[Path]):
    if not prefix_path:
        console.print("[red]Prefix path unknown.[/red]")
        return
    profiles_dir = prefix_path / "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Profiles"
    if not profiles_dir.exists():
        console.print("[red]Profiles directory not found.[/red]")
        return
    console.print(f"[cyan]Opening save profiles folder: {profiles_dir}[/cyan]")
    subprocess.run(["xdg-open", str(profiles_dir)], check=False)

def apply_deepsurface_fix(prefix_path: Optional[Path]):
    if not prefix_path: return
    xml_path = (prefix_path /
                "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Settings/system.xml")
    if not xml_path.exists():
        console.print("[yellow]Settings system.xml not found.[/yellow]")
        return
    try:
        content = xml_path.read_bytes()
        encoding = 'utf-16' if content[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'  # type: ignore
        text = content.decode(encoding)  # type: ignore
        new_text = patch_xml_tag(text, "deepsurfaceQuality", "kSettingLevel_Ultra")
        if new_text == text:
            new_text = patch_xml_tag(text, "DeepSurfaceQuality", "kSettingLevel_Ultra")
        if new_text != text:
            xml_path.write_bytes(new_text.encode(encoding))  # type: ignore
            console.print("[SUCCESS] DeepSurfaceQuality set to Ultra.", style="bold green")
        else:
            if 'deepsurfacequality' not in text.lower():
                 console.print("[warning]Could not find <deepsurfaceQuality> tag in system.xml[/warning]")
            else:
                 console.print("[info]DeepSurfaceQuality is already Ultra.", style="bold green")
    except Exception as e:
        console.print(f"[red]Failed to patch system.xml: {e}[/red]")

def apply_graphics_matrix(prefix_path: Optional[Path]):
    if not prefix_path: return
    xml_path = (prefix_path /
                "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Settings/system.xml")
    if not xml_path.exists():
        console.print("[yellow]Settings system.xml not found.[/yellow]")
        return

    recommended = {
        "textureQuality": "texQual_Ultra",
        "lightingQuality": "kSettingLevel_Medium",
        "globalIlluminationQuality": "kSettingLevel_Low",
        "shadowQuality": "kSettingLevel_Medium",
        "farShadowQuality": "kSettingLevel_Low",
        "ssao": "kSettingLevel_Medium",
        "reflectionQuality": "kSettingLevel_Low",
        "waterReflectionQuality": "kSettingLevel_Low",
        "waterRefractionQuality": "kSettingLevel_Medium",
        "waterPhysics": "1",
        "volumetricsQuality": "kSettingLevel_Low",
        "shadowSoftShadows": "kSettingLevel_Off",
        "particleQuality": "kSettingLevel_Medium",
        "tessellation": "kSettingLevel_Medium",
    }

    try:
        content = xml_path.read_bytes()
        encoding = 'utf-16' if content[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'  # type: ignore
        text = content.decode(encoding)  # type: ignore

        updated_text = text
        changes_count = 0

        for key, val in recommended.items():
            new_text = patch_xml_tag(updated_text, key, val)
            if new_text != updated_text:
                updated_text = new_text
                changes_count += 1

        if changes_count > 0:
            xml_path.write_bytes(updated_text.encode(encoding))  # type: ignore
            console.print(f"[SUCCESS] Applied {changes_count} recommended graphics settings.", style="bold green")
        else:
            console.print("[info]Settings are already at recommended values or tags not found.", style="bold green")

    except Exception as e:
        console.print(f"[red]Failed to patch system.xml: {e}[/red]")

def run_health_check(game_path: Optional[Path], prefix_path: Optional[Path]):
    console.print("\n[bold cyan]--- Optimization Health Check ---[/bold cyan]")

    try:
        with open("/proc/swaps", "r") as f:
            swaps = f.readlines()
        for line in swaps[1:]:  # type: ignore
            parts = line.split()
            if len(parts) >= 3:
                size_kb = int(parts[2])
                if size_kb > 4000000:
                    console.print("[WARNING] Large swap file detected (likely CryoUtilities).", style="bold yellow")
                    console.print("  [italic]Guides suggest CryoUtilities can hinder RDR2's streaming engine.[/italic]")
                else:
                    console.print("[SUCCESS] Swap size is within recommended range for RDR2.", style="green")
    except Exception:
        console.print("[dim]Note: Could not read /proc/swaps.[/dim]")

    if prefix_path:
        xml_path = prefix_path / "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Settings/system.xml"
        if xml_path.exists():
            content = xml_path.read_bytes()
            encoding = 'utf-16' if content[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'  # type: ignore
            text = content.decode(encoding)  # type: ignore

            if re.search(r'<API>kSettingAPI_Vulkan</API>', text, re.IGNORECASE):
                console.print("[SUCCESS] Graphics API: Vulkan", style="green")
            else:
                console.print("[WARNING] Graphics API is NOT Vulkan. (Recommended for Steam Deck)", style="bold yellow")

            if re.search(r'<[Dd]eep[Ss]urface[Qq]uality>kSettingLevel_Ultra</', text):
                console.print("[SUCCESS] Ground Geometry (DeepSurfaceQuality): Ultra", style="green")
            else:
                console.print("[info] Ground Geometry is not set to Ultra. (Visual enhancement recommended)", style="blue")

            if re.search(r'<adapterIndex[^>]*>0<|<adapterIndex[^>]*value="0"', text):
                console.print("[SUCCESS] Adapter Index: 0 (Primary)", style="green")
            else:
                console.print("[WARNING] Adapter Index is not 0. You may experience Safe Mode loops.", style="bold yellow")
        else:
            console.print("[yellow]Settings system.xml not found. Run the game once first.[/yellow]")

    if prefix_path:
        user_reg = prefix_path / "user.reg"
        if user_reg.exists():
            text = user_reg.read_text()
            if '"dinput8"="native,builtin"' in text:
                console.print("[SUCCESS] DLL Overrides: Active (Required for ScriptHook/LML)", style="green")
            else:
                console.print("[WARNING] DLL Overrides missing or incomplete.", style="bold yellow")

            if '"Version"="win10"' in text:
                console.print("[SUCCESS] OS Version: Windows 10", style="green")
            else:
                console.print("[WARNING] OS Version is not win10. You may experience launcher errors.", style="bold yellow")

    console.print("[bold cyan]--- End of Health Check ---[/bold cyan]\n")

def handle_steam_deck_optimizations(game_path: Optional[Path], prefix_path: Optional[Path]):
    while True:
        console.print("\n[bold magenta]Steam Deck Optimizations[/bold magenta]")
        console.print("1. Run Optimization Health Check")
        console.print("2. Apply Recommended Graphics Matrix (Vulkan)")
        console.print("3. Fix Ground Geometry (Set DeepSurface -> Ultra)")
        console.print("4. Fix Safe Mode Boot Loop (Set Adapter Index -> 0)")
        console.print("5. Fix Launcher Error (Set OS Version -> Win10)")
        console.print("6. Refresh Shader Cache (Purge sga_ files)")
        console.print("7. Force Vulkan API & DLL Overrides")
        console.print("8. Show Recommended Launch Options")
        console.print("0. Back")

        choice = Prompt.ask("Select", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8"])

        if choice == "0":
            break
        elif choice == "1":
            run_health_check(game_path, prefix_path)
        elif choice == "2":
            apply_graphics_matrix(prefix_path)
        elif choice == "3":
            apply_deepsurface_fix(prefix_path)
        elif choice == "4":
            set_adapter_index(prefix_path)
        elif choice == "5":
            set_windows_version(prefix_path)
        elif choice == "6":
            if prefix_path and Prompt.ask("Purge all shader caches? This removes sga_* files and pipelinestate.cache. (y/N)", default="n").lower() == "y":
                count = clear_graphics_cache(prefix_path)
                console.print(f"[bold green]Cleared {count} cache files.[/bold green]")
            elif not prefix_path:
                console.print("[red]Proton prefix not found.[/red]")
        elif choice == "7":
            ensure_vulkan(prefix_path)
            apply_wine_overrides(prefix_path)
        elif choice == "8":
            console.print("\n[bold cyan]--- Recommended Steam Launch Options ---[/bold cyan]")
            console.print("Copy and paste these into the game's properties in Steam:")
            console.print(Panel("-width 1280 -height 800 -fullscreen -ignorepipelinecache", border_style="green"))
            console.print("[italic]Note: -ignorepipelinecache fixes massive stutters upon loading saves.[/italic]\n")

def safety_checks(game_path: Optional[Path]):
    if not game_path: return
    if (game_path / "version.dll").exists():
        console.print("[warning]ADVISORY: 'version.dll' detected. This often causes the 'Spawn Bug' and world desolation. Consider using 'dinput8.dll' instead.[/warning]")
    stream_path = Path(game_path) / "lml" / "stream"
    if stream_path.exists():
        try:
            count = len(list(stream_path.iterdir()))
            if count > 30:
                console.print(f"[warning]ADVISORY: {count} mods in 'lml/stream'. Over 30 often causes 'Heartburn' (memory overload/spawn bugs).[/warning]")
        except (OSError, PermissionError) as e:
            console.print(f"[yellow]Could not perform LML safety check: {e}[/yellow]")

def install_local_mod_zip(zip_path: str, game_path: Path, prefix_path: Optional[Path] = None, deploy: bool = False) -> bool:
    zip_path_obj = Path(zip_path).expanduser().resolve()
    zip_path_name = zip_path_obj.name
    if not zip_path_obj.exists():
        console.print(f"[red]Error: Zip file not found at {zip_path_obj}[/red]")
        return False
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        console.print(f"[info]Extracting {zip_path_name}...[/info]")
        try:
            with zipfile.ZipFile(zip_path_obj, 'r') as zip_ref:
                zip_ref.extractall(tmp_path)
        except Exception as e:
            console.print(f"[red]Failed to extract zip: {e}[/red]")
            return False

        has_lml = (tmp_path / "lml").exists()
        asis = list(tmp_path.rglob("*.asi"))
        has_asi = len(asis) > 0
        has_stream = any(p.is_dir() for p in tmp_path.rglob("stream"))

        staging_mod_dir = STAGING_DIR / "mods" / zip_path_obj.stem
        if staging_mod_dir.exists():
            console.print(f"[yellow]Mod '{zip_path_obj.stem}' already exists in staging.[/yellow]")
            if Prompt.ask("Overwrite? (y/N)", default="n").lower() != "y":
                return False
            shutil.rmtree(staging_mod_dir)
        staging_mod_dir.mkdir(parents=True)

        if has_lml:
             shutil.copytree(tmp_path / "lml", staging_mod_dir / "lml", dirs_exist_ok=True)
             console.print(f"[info]Detected 'lml' structure. Copied to staging.[/info]")
        
        if has_asi:
             for asi in asis:
                 try:
                     asi.relative_to(tmp_path / "lml")
                 except ValueError:
                     shutil.copy2(asi, staging_mod_dir)
             console.print(f"[info]Detected {len(asis)} .asi file(s). Copied to staging.[/info]")
             
        if has_stream and not has_lml:
             stream_target = staging_mod_dir / "lml" / "stream"
             stream_target.mkdir(parents=True, exist_ok=True)
             for stream_dir in tmp_path.rglob("stream"):
                 if stream_dir.is_dir():
                     shutil.copytree(stream_dir, stream_target, dirs_exist_ok=True)
             console.print(f"[info]Detected 'stream' folder. Copied to lml/stream.[/info]")

        if not has_lml and not has_asi and not has_stream:
             shutil.copytree(tmp_path, staging_mod_dir, dirs_exist_ok=True)
             console.print(f"[yellow]Warning: no recognisable mod structure found in zip.[/yellow]")
             if Prompt.ask("This zip lacks asi/lml/stream. Proceed with generic extraction? (y/N)", default="n").lower() != "y":
                 shutil.rmtree(staging_mod_dir, ignore_errors=True)
                 return False
             console.print(f"[info]No clear structure found. Copied all files to mod root.[/info]")

        if deploy:
            deploy_mods(game_path, prefix_path)
            
        console.print(f"[bold green]Successfully installed {zip_path_name}![/bold green]")
        return True


# ---------------------------------------------------------------------------
# Nexus integration
# ---------------------------------------------------------------------------

class NexusManager:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key_file = Path.home() / ".nexus_key"
        self.api_key: Optional[str] = api_key if api_key else self.load_api_key()
        if api_key:
            self.save_api_key(api_key)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def load_api_key(self) -> Optional[str]:
        if self.api_key_file.exists():
            return self.api_key_file.read_text().strip()
        return None

    def save_api_key(self, key: str):
        fd = os.open(self.api_key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(key)
        self.api_key = key

    def search(self, query: str) -> List[Dict[str, str]]:
        if not requests or not BeautifulSoup:
            console.print("[yellow]Nexus Search requires 'requests' and 'beautifulsoup4' libraries.[/yellow]")
            return []
        from urllib.parse import quote
        url = f"https://www.nexusmods.com/reddeadredemption2/mods/search/?RH_ModList=navmenu_reasons&search%5Bfilename%5D={quote(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.nexusmods.com/reddeadredemption2/mods/"
        }
        try:
            response = (requests.get(url, headers=headers, timeout=15) if requests else None)
            if not response or response.status_code != 200:
                sc = response.status_code if response else "Unknown"
                console.print(f"[warning]Nexus Search failed (Status {sc}).[/warning]")
                return []
            
            resp_text = getattr(response, 'text', '')
            soup = BeautifulSoup(resp_text, 'html.parser')  # type: ignore
            results: List[Dict[str, str]] = []
            
            mod_items = soup.select('ul.mod-list > li.mod-tile')
            if not mod_items:
                mod_items = soup.select('li.mod-tile') 
                
            if response and response.status_code == 200 and not mod_items:
                if any(sig in resp_text for sig in ["Just a moment", "cf-browser-verification", "Ray ID:"]):
                    console.print("[red]Cloudflare block detected. Please download manually or check your connection.[/red]")
                else:
                    console.print("[yellow]Scraper returned 0 results — Nexus DOM may have changed.[/yellow]")
                return []
            for item in mod_items:
                name_elem = item.select_one('p.tile-name a')
                if not name_elem:
                    continue
                    
                desc_elem = item.select_one('p.tile-desc')
                stat_elems = item.select('li.stat')
                downloads = "N/A"
                for stat in stat_elems:
                    if "Downloads" in stat.text:
                        match = re.search(r'[\d,]+', stat.text)
                        if match:
                            downloads = match.group(0)
                        else:
                            downloads = stat.text.split("Downloads")[-1].strip()
                
                results.append({
                    "name": name_elem.text.strip(),
                    "id": name_elem.get('href', '').split('/')[-1] if name_elem.get('href') else "0",
                    "url": name_elem.get('href', ''),
                    "desc": desc_elem.text.strip() if desc_elem else "",
                    "downloads": downloads
                })
            return results[:5]  # type: ignore
        except Exception as e:
            console.print(f"[red]Error during Nexus search: {e}[/red]")
            return []

    def get_download_url(self, mod_id: str) -> Tuple[Optional[str], Optional[str]]:
        if not requests:
            console.print("[red]Nexus API requires 'requests' library.[/red]")
            return None, None
        if not self.api_key:
            console.print("[yellow]Nexus API Key missing. Please provide one for stable downloads.[/yellow]")
            key = Prompt.ask("Enter your Nexus Mods Personal API Key")
            self.save_api_key(key)
        if not self.api_key:
            console.print("[red]API Key not provided. Cannot proceed with download.[/red]")
            return None, None

        api_url = f"https://api.nexusmods.com/v1/games/reddeadredemption2/mods/{mod_id}/files.json"
        headers = {"apikey": self.api_key}
        try:
            res = requests.get(api_url, headers=headers, timeout=15)
            if res.status_code == 200:
                files = res.json().get('files', [])
                if files and isinstance(files, list):
                    main_files = [f for f in files if isinstance(f, dict) and f.get("category_name") == "MAIN"]
                    if not main_files:
                        main_files = [f for f in files if isinstance(f, dict)]
                    if main_files:
                        main_file = sorted(main_files, key=lambda x: x.get("uploaded_timestamp", 0) or 0, reverse=True)[0]
                        file_id = main_file.get('file_id')
                        if file_id:
                            link_url = f"https://api.nexusmods.com/v1/games/reddeadredemption2/mods/{mod_id}/files/{file_id}/download_link.json"
                            link_res = requests.get(link_url, headers=headers, timeout=15)
                            if link_res.status_code == 200:
                                res_json = link_res.json()
                                if isinstance(res_json, list) and res_json:
                                    return res_json[0].get('URI'), main_file.get('name')
            else:
                console.print(f"[red]API Error: {res.json().get('message', 'Unknown Error')}[/red]")
        except Exception as e:
            console.print(f"[red]Failed to get download URL: {e}[/red]")
        return None, None

    def install_mod(self, mod_id: str, game_path: Path, prefix_path: Optional[Path] = None):
        url, filename = self.get_download_url(mod_id)
        if not url or not filename:
            console.print("[red]Could not retrieve download URL. Check your API key or install 'requests'.[/red]")
            return
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            download_path = tmp_path / filename  # type: ignore
            console.print(f"[info]Downloading {filename}...[/info]")
            try:
                if requests:
                    res = requests.get(url, stream=True, timeout=30)
                    if res.status_code != 200:
                        console.print(f"[red]Download failed: HTTP {res.status_code} from {url}[/red]")
                        return
                    with open(download_path, 'wb') as f:
                        for chunk in res.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                else:
                    req_url = str(url)
                    with urllib.request.urlopen(req_url, timeout=30) as response:
                        with open(download_path, 'wb') as out_file:
                            shutil.copyfileobj(response, out_file)
            except Exception as e:
                console.print(f"[red]Download failed: {e}[/red]")
                return

            if game_path:
                install_local_mod_zip(str(download_path), game_path, prefix_path, deploy=True)

def handle_nexus_integration(game_path: Path, prefix_path: Optional[Path] = None):
    if not game_path:
        console.print("[red]Game path not known. Run option 1 first.[/red]")
        return
    nexus = NexusManager()
    while True:
        console.print("\n[bold magenta]Nexus Mods Integration[/bold magenta]")
        console.print("1. Search Mods")
        console.print("2. Install by ID")
        console.print("0. Back")
        choice = Prompt.ask("Select", choices=["0", "1", "2"])
        if choice == "0": break
        elif choice == "1":
            query = Prompt.ask("Search Red Dead Redemption 2 Mods")
            results = nexus.search(query)
            if not results:
                console.print("[yellow]No results found.[/yellow]")
                continue
            table = Table(title=f"Results for '{query}'")
            table.add_column("ID", style="cyan")
            table.add_column("Name", style="green")
            table.add_column("Downloads", style="magenta")
            for r in results:
                table.add_row(r['id'], r['name'], r['downloads'])  # type: ignore
            console.print(table)
            mod_id = Prompt.ask("Enter ID to install (or '0' to cancel)")
            if mod_id != "0":
                nexus.install_mod(mod_id, game_path, prefix_path)
        elif choice == "2":
            mod_id = Prompt.ask("Enter Nexus Mod ID")
            nexus.install_mod(mod_id, game_path, prefix_path)

def handle_mod_manager(game_path: Optional[Path], prefix_path: Optional[Path]):
    modlist_file = STAGING_DIR / "modlist.json"
    mods_staging = STAGING_DIR / "mods"

    if not mods_staging.exists() or not any(mods_staging.iterdir()):
        console.print("[yellow]No mods found in staging. Install some first.[/yellow]")
        return

    while True:
        modlist: Dict[str, Any] = {}
        if modlist_file.exists():
            try:
                with open(modlist_file, "r") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        modlist = loaded
            except Exception:
                pass

        discovered = [d for d in mods_staging.iterdir() if d.is_dir()]
        for mod_dir in discovered:
            if isinstance(modlist, dict) and mod_dir.name not in modlist:
                modlist[mod_dir.name] = {"enabled": True, "priority": 50}

        if not modlist:
            console.print("[yellow]No mods found.[/yellow]")
            return

        mod_names = sorted(modlist.keys())

        table = Table(title="Installed Mods")
        table.add_column("#", style="cyan", width=4)
        table.add_column("Name", style="white")
        table.add_column("Status", width=10)
        table.add_column("Priority", width=8)
        table.add_column("Files", width=6)

        for i, name in enumerate(mod_names, 1):
            data = modlist[name] if isinstance(modlist[name], dict) else {"enabled": True, "priority": 50}  # type: ignore
            enabled = data.get("enabled", True)
            priority = str(data.get("priority", 50))
            mod_dir = mods_staging / name
            entries = list(mod_dir.rglob("*")) if mod_dir.exists() else []
            file_count = str(sum(1 for e in entries if e.is_file())) if entries else "?"
            status = "[green]Enabled[/green]" if enabled else "[red]Disabled[/red]"
            table.add_row(str(i), name, status, priority, file_count)  # type: ignore

        console.print(table)
        console.print("\n[bold magenta]Mod Manager[/bold magenta]")
        console.print("Enter a mod number to manage it, or [bold]0[/bold] to go back.")
        choice = Prompt.ask("Select")

        if choice == "0":
            break

        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(mod_names):
                raise ValueError
        except ValueError:
            console.print("[red]Invalid selection.[/red]")
            continue

        selected = mod_names[idx]
        data = modlist[selected] if isinstance(modlist[selected], dict) else {"enabled": True, "priority": 50}  # type: ignore
        enabled = data.get("enabled", True)

        console.print(f"\n[bold cyan]{selected}[/bold cyan] is currently [{'green]Enabled' if enabled else 'red]Disabled'}[/]")
        console.print("1. Toggle Enable/Disable")
        console.print("2. Delete Mod")
        console.print("0. Back")
        action = Prompt.ask("Action", choices=["0", "1", "2"])

        if action == "0":
            continue
        elif action == "1":
            modlist[selected]["enabled"] = not enabled  # type: ignore
            with open(modlist_file, "w") as f:
                json.dump(modlist, f, indent=4)
            new_state = "Enabled" if not enabled else "Disabled"
            console.print(f"[green]{selected} is now {new_state}.[/green]")
            if game_path:
                console.print("[cyan]Re-deploying mods to apply changes...[/cyan]")
                clean_purge()
                deploy_mods(game_path, prefix_path)  # type: ignore  # type: ignore
        elif action == "2":
            confirm = Prompt.ask(f"Delete [bold]{selected}[/bold] permanently? (y/N)", default="n")
            if confirm.lower() == "y":
                mod_dir = mods_staging / selected
                if mod_dir.exists():
                    shutil.rmtree(mod_dir)
                modlist.pop(selected, None)  # type: ignore
                with open(modlist_file, "w") as f:
                    json.dump(modlist, f, indent=4)
                console.print(f"[green]{selected} deleted.[/green]")
                if game_path:
                    console.print("[cyan]Re-deploying mods to apply changes...[/cyan]")
                    clean_purge()
                    deploy_mods(game_path, prefix_path)  # type: ignore  # type: ignore


# ---------------------------------------------------------------------------
# Backup & Restore manager
# ---------------------------------------------------------------------------

def _print_backup_table(backups: List[Dict]):
    """Renders a Rich table of available .rdr2cfg snapshots."""
    table = Table(title="Mod Config Snapshots", show_lines=True)
    table.add_column("#",        style="bold cyan",  width=4,  justify="right")
    table.add_column("File",     style="white",      min_width=28)
    table.add_column("Mods",     style="yellow",     width=6,  justify="right")
    table.add_column("Size",     style="dim",        width=9,  justify="right")
    table.add_column("Created",  style="green",      width=20)
    table.add_column("Schema",   style="magenta",    width=8)

    for i, b in enumerate(backups, 1):
        table.add_row(  # type: ignore
            str(i),
            b["name"],
            str(b["mod_count"]) if b["mod_count"] >= 0 else "[red]corrupt[/red]",
            f"{b['size_kb']} KB",
            b["created_at"].strftime("%Y-%m-%d  %H:%M"),
            b["schema_version"],
        )
    console.print(table)


def _pick_backup(mode: str = "restore") -> Optional[Path]:
    """Lists available backups and returns user-selected Path, or None on cancel."""
    backups = list_backups(BACKUP_DIR)
    if not backups:
        console.print("[yellow]No snapshots found in backups directory.[/yellow]")
        return None

    _print_backup_table(backups)
    valid = [str(i) for i in range(1, len(backups) + 1)] + ["0"]
    choice = Prompt.ask(
        f"Select a snapshot to {mode} [bold](1–{len(backups)})[/bold], or [bold]0[/bold] to cancel",
        choices=valid,
    )
    if choice == "0":
        return None
    return backups[int(choice) - 1]["path"]


def handle_profile_manager(game_path: Optional[Path], prefix_path: Optional[Path]):
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        console.print("\n[bold cyan]--- Mod Profiles ---[/bold cyan]")
        profiles = sorted([d.name for d in PROFILES_DIR.iterdir() if d.is_dir()])
        
        current_profile = "None (Active)"
        
        table = Table(title="Available Profiles")
        table.add_column("#", style="dim")
        table.add_column("Profile Name")
        for i, p in enumerate(profiles):
            table.add_row(str(i+1), p)
        console.print(table)
        
        console.print("Options: [bold]s[/bold] <num> to switch, [bold]c[/bold] <name> to create from active, [bold]d[/bold] <num> to delete, [bold]0[/bold] to back")
        cmd = Prompt.ask("Action").lower().strip()
        
        if cmd == "0" or not cmd:
            break
            
        try:
            if cmd.startswith("c "):
                name = re.sub(r'[^\w\-]', '_', cmd[2:].strip())  # type: ignore
                if not name: continue
                target = PROFILES_DIR / name
                if target.exists():
                    console.print(f"[red]Profile '{name}' already exists.[/red]")
                    continue
                console.print(f"[cyan]Creating profile '{name}' from current staging...[/cyan]")
                target.mkdir(parents=True)
                mods_src = STAGING_DIR / "mods"
                if mods_src.exists():
                    shutil.copytree(mods_src, target / "mods", dirs_exist_ok=True)
                ml_src = STAGING_DIR / "modlist.json"
                if ml_src.exists():
                    shutil.copy2(ml_src, target / "modlist.json")
                console.print(f"[SUCCESS] Profile '{name}' created.", style="bold green")
                
            elif cmd.startswith("s "):
                idx = int(cmd.split()[1]) - 1
                name = profiles[idx]
                source = PROFILES_DIR / name
                
                if Prompt.ask(f"Switch to profile '{name}'? This will replace your current staging! (y/N)", default="n").lower() != "y":
                    continue
                
                console.print(f"[cyan]Switching to profile '{name}'...[/cyan]")
                if game_path:
                    clean_purge()
                
                if STAGING_DIR.exists():
                    shutil.rmtree(STAGING_DIR)
                STAGING_DIR.mkdir(parents=True)
                
                mods_src = source / "mods"
                if mods_src.exists():
                    shutil.copytree(mods_src, STAGING_DIR / "mods", dirs_exist_ok=True)
                ml_src = source / "modlist.json"
                if ml_src.exists():
                    shutil.copy2(ml_src, STAGING_DIR / "modlist.json")
                
                console.print(f"[SUCCESS] Switched to profile '{name}'.", style="bold green")
                if game_path:
                    if Prompt.ask("Deploy mods now? (y/N)", default="y").lower() == "y":
                        deploy_mods(game_path, prefix_path)  # type: ignore  # type: ignore

            elif cmd.startswith("d "):
                idx = int(cmd.split()[1]) - 1
                name = profiles[idx]
                if Prompt.ask(f"Delete profile '{name}'? (y/N)", default="n").lower() == "y":
                    shutil.rmtree(PROFILES_DIR / name)
                    console.print(f"[SUCCESS] Deleted profile '{name}'.")
                    
        except (ValueError, IndexError) as e:
            console.print(f"[red]Error: {e}[/red]")

def install_skip_intro(game_path: Path, prefix_path: Optional[Path] = None):
    console.print("\n[bold cyan]--- Skip Intro Auto-Install ---[/bold cyan]")
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = Path(tmp_dir) / "skip_intro.zip"
        if download_item(SKIP_INTRO_URL, zip_path, "Skip Intro Mod"):
            if not zipfile.is_zipfile(zip_path):
                console.print("[red]Download failed or blocked by Cloudflare (detected invalid ZIP).[/red]")
                console.print(f"[yellow]Please download it manually from:[/yellow]\n[cyan]{SKIP_INTRO_URL}[/cyan]")
                return
                
            with zipfile.ZipFile(zip_path, 'r') as z:
                target_dir = STAGING_DIR / "mods" / "SkipIntro"
                target_dir.mkdir(parents=True, exist_ok=True)
                z.extractall(target_dir)
            
            console.print("[SUCCESS] Skip Intro mod staged. Deploying...", style="bold green")
            deploy_mods(game_path, prefix_path)  # type: ignore
        else:
            console.print("[red]Failed to download Skip Intro mod automatically.[/red]")
            console.print(f"[yellow]Please download it manually from:[/yellow]\n[cyan]{SKIP_INTRO_URL}[/cyan]")

def handle_backup_manager(game_path: Optional[Path], prefix_path: Optional[Path]):
    """Sub-menu for mod config backup & restore operations."""
    while True:
        console.print("\n[bold magenta]Backup & Restore Mod Config[/bold magenta]")
        console.print("1. Create snapshot of current mod config")
        console.print("2. List saved snapshots")
        console.print("3. Restore a snapshot")
        console.print("4. Import snapshot from custom path")
        console.print("0. Back")

        choice = Prompt.ask("Select", choices=["0", "1", "2", "3", "4"])

        if choice == "0":
            break

        elif choice == "1":
            default_name = f"rdr2_mods_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
            raw = Prompt.ask("Snapshot name (no extension)", default=default_name)
            safe_name = re.sub(r'[^\w\-.]', '_', raw.strip()) or default_name
            if not safe_name.endswith(BACKUP_EXTENSION):
                safe_name += BACKUP_EXTENSION
            output_path = BACKUP_DIR / safe_name
            if output_path.exists():
                console.print(f"[red]A snapshot named '{safe_name}' already exists. Choose a different name.[/red]")
                continue
            backup_mod_config(STAGING_DIR, output_path)

        elif choice == "2":
            backups = list_backups(BACKUP_DIR)
            if not backups:
                console.print("[yellow]No snapshots found.[/yellow]")
            else:
                _print_backup_table(backups)

        elif choice == "3":
            selected = _pick_backup(mode="restore")
            if not selected:
                continue
            success, restored, missing = restore_mod_config(selected, STAGING_DIR)
            if success and restored and game_path:
                if Prompt.ask(
                    f"\nRe-deploy mods now to apply restored config? (y/N)", default="n"
                ).lower() == "y":
                    clean_purge()
                    deploy_mods(game_path, prefix_path)  # type: ignore  # type: ignore

        elif choice == "4":
            raw_path = Prompt.ask("Enter full path to the .rdr2cfg file")
            import_path = Path(raw_path).expanduser().resolve()
            if not import_path.exists():
                console.print(f"[red]File not found: {import_path}[/red]")
                continue
            if import_path.suffix.lower() != BACKUP_EXTENSION:
                console.print(f"[yellow]Warning: file does not have a {BACKUP_EXTENSION} extension.[/yellow]")
            try:
                with open(import_path, "r", encoding="utf-8") as f:
                    probe = json.load(f)
                if "mod_registry" not in probe:
                    console.print("[red]File does not look like a valid mod config snapshot (missing 'mod_registry').[/red]")
                    continue
            except Exception as e:
                console.print(f"[red]Could not parse file: {e}[/red]")
                continue
            success, restored, missing = restore_mod_config(import_path, STAGING_DIR)
            if success and restored and game_path:
                if Prompt.ask(
                    f"\nRe-deploy mods now to apply imported config? (y/N)", default="n"
                ).lower() == "y":
                    clean_purge()
                    deploy_mods(game_path, prefix_path)  # type: ignore  # type: ignore


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

def handle_recommended_mods(game_path: Optional[Path], prefix_path: Optional[Path]):
    while True:
        console.print("\n[bold magenta]--- Recommended Essential Mods ---[/bold magenta]")
        console.print("[italic]Curated list of mods for the best Steam Deck experience.[/italic]")
        console.print("[yellow]Note: Some of these mods require complex installation steps that can not be end-to-end handeld by the app in it's current state, please refer to the originial author's instructions.[/yellow]\n")
        
        recommended = [
            {"id": "skipintro", "name": "Skip Intro", "desc": "Bypass Rockstar startup logos.", "url": "https://www.nexusmods.com/reddeadredemption2/mods/36"},
            {"id": "legalskip", "name": "Legal Screen Skip", "desc": "Removes the legal text at startup.", "url": "https://www.nexusmods.com/reddeadredemption2/mods/28"},
            {"id": "stutterfix", "name": "Stutter Fix (Integrated)", "desc": "Clear caches & fix stutters.", "action": "cache"},
            {"id": "seasons", "name": "Dynamic Seasons (3-Part Series)", "desc": "Full seasons mod with weather & extras.", "urls": [
                "https://www.nexusmods.com/reddeadredemption2/mods/1557",
                "https://www.nexusmods.com/reddeadredemption2/mods/6631?tab=files&file_id=21372",
                "https://www.nexusmods.com/reddeadredemption2/mods/5281"
            ]},
            {"id": "nexus2122", "name": "No honor loss when masked", "desc": "Title says it all.", "url": "https://www.nexusmods.com/reddeadredemption2/mods/2122"},
            {"id": "nexus2213", "name": "Bandit Hideouts", "desc": "Several bandit hideouts spawn across the map", "url": "https://www.nexusmods.com/reddeadredemption2/mods/2213?tab=posts&BH=0"},
            {"id": "lennytrainer", "name": "Lenny's Simple Trainer", "desc": "Powerful trainer and script tool.", "url": "https://www.rdr2mods.com/downloads/rdr2/scripts/7-lennys-simple-trainer/"},
        ]
        
        table = Table(title="Essential Mods")
        table.add_column("#", style="dim")
        table.add_column("Mod Name")
        table.add_column("Description")
        for i, mod in enumerate(recommended):
            table.add_row(str(i+1), mod["name"], mod["desc"])
        console.print(table)
        
        console.print("Select [bold]<num>[/bold] to visit mod page, or [bold]0[/bold] to back")
        choice = Prompt.ask("Action").lower().strip()
        
        if choice == "0" or not choice:
            break
            
        try:
            idx = int(choice) - 1
            selected = recommended[idx]
            if "url" in selected or "urls" in selected:
                console.print(f"\n[cyan]Opening/Providing link for {selected['name']}...[/cyan]")
                if "url" in selected:
                    console.print(f"[bold underline]{selected['url']}[/bold underline]")
                else:
                    for url in selected["urls"]:
                        console.print(f"[bold underline]{url}[/bold underline]")
                
                console.print("[italic]Note: Most Nexus mods require manual download & login.[/italic]")
                if selected["id"] == "skipintro":
                    if Prompt.ask("Try automatic install for Skip Intro? (y/N)", default="n").lower() == "y":
                        if game_path: install_skip_intro(game_path, prefix_path)  # type: ignore
                        else: console.print("[red]Game path unknown.[/red]")
            elif selected.get("action") == "cache":
                if prefix_path:
                    count = clear_graphics_cache(prefix_path)
                    console.print(f"[SUCCESS] Cleared {count} cache files.")
                else:
                    console.print("[red]Prefix path unknown.[/red]")
        except (ValueError, IndexError):
            console.print("[red]Invalid selection.[/red]")

def handle_mods_menu(game_path: Optional[Path], prefix_path: Optional[Path]):
    while True:
        console.print("\n[bold cyan]--- Mod Management ---[/bold cyan]")
        options = [
            ("1", "Recommended Mods (Essential)"),
            ("2", "Deploy Mods (Vortex-style)"),
            ("3", "Manage Installed Mods"),
            ("4", "Mod Profiles (Save/Switch)"),
            ("5", "Install Core Tools (ScriptHook/LML)"),
            ("6", "Search & Install Mods (Nexus)"),
            ("7", "Install Local Mod (.zip)"),
            ("8", "Backup & Restore Mod Config"),
            ("9", "Clean Purge Mods"),
            ("10", "Quick Mod Editor (Legacy)"),
            ("0", "Back")
        ]
        for opt, desc in options:
            console.print(f"  {opt.ljust(3)} {desc}")
        
        choice = Prompt.ask("Select", choices=[o[0] for o in options])
        if choice == "0": break
        elif choice == "10":
            handle_mod_list_editor(game_path, prefix_path)
        elif choice == "1":
            handle_recommended_mods(game_path, prefix_path)
        elif choice == "2":
            if game_path: deploy_mods(game_path, prefix_path)  # type: ignore
            else: console.print("[red]Game path unknown.[/red]")
        elif choice == "3":
            handle_mod_manager(game_path, prefix_path)
        elif choice == "4":
            handle_profile_manager(game_path, prefix_path)
        elif choice == "5":
            if game_path:
                check_and_install_scripthook(game_path)
                install_lml(game_path)  # type: ignore
                if prefix_path: apply_wine_overrides(prefix_path)
            else: console.print("[red]Game path unknown.[/red]")
        elif choice == "6":
            if game_path: handle_nexus_integration(game_path, prefix_path)  # type: ignore
            else: console.print("[red]Game path unknown.[/red]")
        elif choice == "7":
            if game_path:
                zip_paths_str = Prompt.ask("Enter the absolute path to your .zip mod (separate multiple with [bold];[/bold])")
                paths = [p.strip() for p in zip_paths_str.split(";") if p.strip()]
                if paths:
                    staged_any = False
                    for path in paths:
                        if install_local_mod_zip(path, game_path, prefix_path, deploy=False):  # type: ignore
                            staged_any = True
                    if staged_any and game_path:
                        deploy_mods(game_path, prefix_path)  # type: ignore  # type: ignore
            else: console.print("[red]Game path unknown.[/red]")
        elif choice == "8":
            handle_backup_manager(game_path, prefix_path)
        elif choice == "9":
            if Prompt.ask("PURGE all deployed mods? (y/N)", default="n").lower() == "y":
                clean_purge()

def handle_saves_menu(prefix_path: Optional[Path]):
    while True:
        console.print("\n[bold cyan]--- Save Editor & Manager ---[/bold cyan]")
        options = [
            ("1", "Edit Save File (Money/Honor)"),
            ("2", "Farm Honor"),
            ("3", "Save Snapshot Manager"),
            ("0", "Back")
        ]
        for opt, desc in options:
            console.print(f"  {opt.ljust(3)} {desc}")
        
        choice = Prompt.ask("Select", choices=[o[0] for o in options])
        if choice == "0": break
        elif choice == "1": handle_option_4(prefix_path)
        elif choice == "2":
            if prefix_path: farm_honor(prefix_path)
            else: console.print("[red]Prefix path unknown.[/red]")
        elif choice == "3":
            if prefix_path:
                console.print("\n[bold cyan]--- Save Snapshot Manager ---[/bold cyan]")
                console.print("1. Create New Snapshot")
                console.print("2. Restore from Snapshot")
                console.print("0. Back")
                snap_choice = Prompt.ask("Select", choices=["1", "2", "0"], default="0")
                if snap_choice == "1": create_save_snapshot(prefix_path, BACKUP_DIR)
                elif snap_choice == "2": restore_save_snapshot(prefix_path, BACKUP_DIR)
            else: console.print("[red]Proton prefix not found.[/red]")

def handle_maintenance_menu(game_path: Optional[Path], prefix_path: Optional[Path]):
    while True:
        console.print("\n[bold cyan]--- Maintenance & Fixes ---[/bold cyan]")
        options = [
            ("1", "Steam Deck Optimizations"),
            ("2", "Quick Fixes (Black Screen/API/DLL)"),
            ("3", "Clear Graphics Cache (Stutter Fix)"),
            ("4", "Clear Launcher Cache (Social Club Fix)"),
            ("5", "Cleanup All Toolbox Backups (.bak/clones)"),
            ("0", "Back")
        ]
        for opt, desc in options:
            console.print(f"  {opt.ljust(3)} {desc}")
        
        choice = Prompt.ask("Select", choices=[o[0] for o in options])
        if choice == "0": break
        elif choice == "1": handle_steam_deck_optimizations(game_path, prefix_path)
        elif choice == "2":
            if game_path and prefix_path:
                ensure_vulkan(prefix_path)
                fix_sh_black_screen(game_path)  # type: ignore
                apply_wine_overrides(prefix_path)
                safety_checks(game_path)  # type: ignore
                console.print("[SUCCESS] Applied collection of quick fixes.", style="bold green")
            else: console.print("[red]Paths unknown.[/red]")
        elif choice == "3":
            if prefix_path:
                count = clear_graphics_cache(prefix_path)
                console.print(f"[bold green]Cleared {count} graphics cache files.[/bold green]")
            else: console.print("[red]Proton prefix not found.[/red]")
        elif choice == "4":
            if prefix_path:
                count = clear_launcher_cache(prefix_path)
                console.print(f"[bold green]Cleared {count} launcher cache items.[/bold green]")
            else: console.print("[red]Proton prefix not found.[/red]")
        elif choice == "5":
            cleanup_all_backups(game_path, prefix_path)

def create_desktop_shortcut():
    """Generates a .desktop file for the RDR2 Steam Deck Toolbox."""
    desktop_dir = Path.home() / "Desktop"
    applications_dir = Path.home() / ".local/share/applications"
    
    # Ensure applications dir exists
    applications_dir.mkdir(parents=True, exist_ok=True)
    
    # Path to the current script
    script_path = Path(__file__).resolve()
    # Path to the python interpreter in the venv
    venv_python = script_path.parent / "venv" / "bin" / "python"
    
    if not venv_python.exists():
        venv_python = sys.executable # Fallback to current python
        
    terminal = next(
        (t for t in ["konsole", "gnome-terminal", "xterm", "foot"] if shutil.which(t)),
        None
    )
    if not terminal:
        console.print("[red]No terminal emulator found. Cannot create desktop shortcut.[/red]")
        return
        
    desktop_content = f"""[Desktop Entry]
Name=RDR2 Toolbox
Comment=Red Dead Redemption 2 Steam Deck/Linux toolbox
Exec={terminal} -e "{venv_python}" "{script_path}"
Icon=steam
Terminal=false
Type=Application
Categories=Game;Utility;
"""
    
    desktop_file_name = "RDR2_Toolbox.desktop"
    
    try:
        # Create in applications menu
        app_file = applications_dir / desktop_file_name
        app_file.write_text(desktop_content)
        app_file.chmod(0o755)
        
        # Create on desktop if it exists
        if desktop_dir.exists():
            desktop_file = desktop_dir / desktop_file_name
            desktop_file.write_text(desktop_content)
            desktop_file.chmod(0o755)
            console.print(f"[success]Desktop shortcut created at {desktop_file}[/success]")
        
        console.print(f"[success]Application menu entry created at {app_file}[/success]")
        console.print("[info]You can now find 'RDR2 Toolbox' in your application launcher or on the desktop.[/info]")
    except Exception as e:
        console.print(f"[red]Failed to create desktop shortcut: {e}[/red]")

def handle_utilities_menu(game_path: Optional[Path], prefix_path: Optional[Path]):
    while True:
        console.print("\n[bold cyan]--- Utilities & Info ---[/bold cyan]")
        options = [
            ("1", "Check Environment & Paths"),
            ("2", "Export Photo Mode Images"),
            ("3", "Create Desktop Shortcut"),
            ("4", "Open Game Install Folder"),
            ("5", "Open Save Profiles Folder"),
            ("0", "Back")
        ]
        for opt, desc in options:
            console.print(f"  {opt.ljust(3)} {desc}")
        
        choice = Prompt.ask("Select", choices=[o[0] for o in options])
        if choice == "0": break
        elif choice == "1": handle_option_1(game_path, prefix_path)
        elif choice == "2":
            if prefix_path:
                photos_dir = TOOLBOX_DIR / "photos"
                count = export_photo_mode_images(prefix_path, photos_dir)
                console.print(f"[bold green]Exported {count} Photo Mode images to {photos_dir}[/bold green]")
            else: console.print("[red]Proton prefix not found.[/red]")
        elif choice == "3":
            create_desktop_shortcut()
        elif choice == "4":
            open_game_folder(game_path)
        elif choice == "5":
            open_save_folder(prefix_path)

def display_menu():
    console.print(Panel("[bold yellow]RDR2-Deck-Master Menu[/bold yellow]", expand=False))
    width = 45
    console.print(f"┌{'─' * width}┐")
    menu_items = [
        ("1", "Mod Management"),
        ("2", "Save Editor"),
        ("3", "Maintenance & Fixes"),
        ("4", "Utilities & Info"),
        ("0", "Exit Toolbox"),
    ]
    for opt, desc in menu_items:
        console.print(f"│ {opt.ljust(8)} │ {desc.ljust(32)} │")
    console.print(f"└{'─' * width}┘")

def handle_option_1(game_path, prefix_path) -> Tuple[Optional[Path], Optional[Path]]:
    console.print("\n[bold]Checking Environment...[/bold]")
    if not game_path:
        game_path, prefix_path = manual_fallback_prompt()
    if game_path:
        console.print(f"[SUCCESS] Game located at: {game_path}", style="bold green")
        print_proton_setup(prefix_path)
    return game_path, prefix_path

def main():
    console.print(Panel(
        "[bold green]Welcome to RDR2-Deck-Master[/bold green]\n[italic]The ultimate offline toolkit for the Steam Deck.[/italic]",
        expand=False
    ))
    setup_directories()
    game: Optional[Path]
    prefix: Optional[Path]
    game, prefix = find_rdr2_installation()
    if not game:
        console.print("[yellow]Could not auto-detect RDR2. Some features may require manual pathing.[/yellow]")
    while True:
        display_menu()
        choice = Prompt.ask(
            "Select an operation",
            choices=["0", "1", "2", "3", "4"],
        )
        if choice == "0":
            console.print("[bold green]I told you i had a plan.[/bold green]")
            break
        elif choice == "1":
            handle_mods_menu(game, prefix)
        elif choice == "2":
            handle_saves_menu(prefix)
        elif choice == "3":
            handle_maintenance_menu(game, prefix)
        elif choice == "4":
            handle_utilities_menu(game, prefix)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold green]I told you i had a plan.[/bold green]")
        sys.exit(0)