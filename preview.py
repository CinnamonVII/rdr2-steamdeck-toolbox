#!/home/deck/rdr2-steamdeck-toolbox/venv/bin/python
import os
import sys
import json
import struct
import shutil
from pathlib import Path

try:
    from rich.console import Console  # type: ignore
    from rich.panel import Panel      # type: ignore
    from rich.prompt import Prompt    # type: ignore
except ImportError:
    class Console:
        def print(self, msg, style=None):
            print(msg)
    class Panel:
        def __init__(self, content, **kwargs):
            self.content = content
        def __rich__(self):
            return self.content
    class Prompt:
        @staticmethod
        def ask(msg, **kwargs):
            return input(f"{msg}: ")

import rdr2_toolbox  # type: ignore

console = Console()
SIM_ROOT = Path("simulation_box").resolve()
GAME_MOCK = SIM_ROOT / "Red Dead Redemption 2"
PREFIX_MOCK = SIM_ROOT / "compatdata/1174180/pfx"

def setup_mock_environment():
    if not SIM_ROOT.exists():
        console.print(f"[info]Creating fresh Simulation Sandbox at {SIM_ROOT}...[/info]")
        SIM_ROOT.mkdir(exist_ok=True)
    
    GAME_MOCK.mkdir(parents=True, exist_ok=True)
    (GAME_MOCK / "RDR2.exe").touch() 
    (GAME_MOCK / "lml").mkdir(exist_ok=True)
    PREFIX_MOCK.mkdir(parents=True, exist_ok=True)
    
    save_dir = PREFIX_MOCK / "drive_c/users/steamuser/Documents/Rockstar Games/Red Dead Redemption 2/Profiles/MOCK123"
    save_dir.mkdir(parents=True, exist_ok=True)
    save_file = save_dir / "SRDR30015"
    
    if not save_file.exists():
        console.print("[info]Initializing Mock Save File...[/info]")
        with open(save_file, "wb") as f:
            # Structurally valid SRDR header (260 bytes)
            # Magic (0x00000004) + UTF-16LE placeholder text + padding
            header = bytearray(0x104)
            # RDR2 saves start with 0x04 0x00 0x00 0x00 (Version 4)
            struct.pack_into("<I", header, 0, 4)
            desc = "Chapter 2 - 12.5%".encode("utf-16-le")
            for i, b in enumerate(desc):
                header[4+i] = b
            f.write(header)
            
            # Zeroed payload (0x2000 - 0x104 bytes)
            # Note: real saves are XORed. This mock is plain for easier patching in preview.
            f.write(b"\x00" * (0x2000 - 0x104))
            
            # Injecting test values at aligned offsets
            f.seek(0x400)
            f.write(b"\x13\x00\xD4\x00") # Pattern for money $432.18
            f.write(struct.pack("<i", 43218)) 
            
            f.seek(0x800)
            f.write(struct.pack("<f", 150.0))
        
        # Call stub (no-op in save_modifier.py, but required by API contract)
        rdr2_toolbox.validate_and_sign_srdr(save_file)

    rdr2_toolbox.set_simulation_mode(SIM_ROOT)

def main():
    console.print(Panel(
        "[bold cyan]RDR2-Deck-Master PREVIEW (DRY-RUN)[/bold cyan]\n[italic]All operations are redirected to ./simulation_box/[/italic]", 
        expand=False
    ))
    
    setup_mock_environment()
    
    rdr2_toolbox.find_rdr2_installation = lambda: (GAME_MOCK, PREFIX_MOCK)
    
    try:
        rdr2_toolbox.main()
    except KeyboardInterrupt:
        console.print("\n[bold green]Preview terminated. Simulation box preserved.[/bold green]")
        sys.exit(0)

if __name__ == "__main__":
    main()

