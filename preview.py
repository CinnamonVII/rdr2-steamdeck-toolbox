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
        console.print("[info]Initializing Mock Save File (XOR-obfuscated)...[/info]")
        from save_modifier import _xor_deobfuscate, _KNOWN_XOR_KEY, _HEADER_LEN
        
        # 1. Create plain Header (260 bytes)
        header = bytearray(_HEADER_LEN)
        struct.pack_into("<I", header, 0, 4) # Magic LE 4
        desc = "Chapter 2 - 12.5%".encode("utf-16-le")
        for i, b in enumerate(desc):
            if i < _HEADER_LEN - 4:
                header[4+i] = b
        
        # 2. Create plain Payload
        payload_size = 0x2000 - _HEADER_LEN
        plain_payload = bytearray(payload_size)
        
        # Inject test values into plain payload
        # Offset 0x400 in file is 0x400 - 0x104 = 0x2fc in payload
        money_pos = 0x400 - _HEADER_LEN
        plain_payload[money_pos : money_pos+4] = b"\x13\x00\xD4\x00" # Money pattern
        struct.pack_into("<i", plain_payload, money_pos + 4, 43218) # $432.18
        
        honor_pos = 0x800 - _HEADER_LEN
        struct.pack_into("<i", plain_payload, honor_pos, 150) # Honor 150
        
        # 3. XOR-obfuscate the payload
        xored_payload = _xor_deobfuscate(bytes(plain_payload), _KNOWN_XOR_KEY)
        
        # 4. Write full file
        with open(save_file, "wb") as f:
            f.write(header)
            f.write(xored_payload)

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

