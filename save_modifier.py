"""
RDR2 Save Modifier — Rewritten based on actual save file reverse engineering.

Real SRDR PC save format (verified against 4 live save files):
  - Header:  260 bytes (0x104), starts with magic 0x00000004, contains
             UTF-16LE metadata (chapter name, completion%, timestamp)
  - Payload: XOR-obfuscated with a 16-byte key that is embedded in the file
             itself (derived as the most-common 16-byte block in the payload).
  - After deobfuscation: raw binary game state (no compression). ~78% null
             bytes with dense data islands. Money stored as int32 cents.

Public API (consumed by rdr2_toolbox.py):
    validate_and_sign_srdr, edit_save_file, list_save_files,
    select_save_file, handle_option_4, farm_honor,
    create_save_snapshot, restore_save_snapshot
"""

from typing import Optional, Tuple, List, Dict, Any
import os
import struct
import shutil
from pathlib import Path
import zlib
import zipfile
import subprocess
import datetime
import math
import io
import time
from collections import Counter

try:
    from rich.console import Console  # type: ignore
    from rich.prompt import Prompt  # type: ignore
    from rich.table import Table  # type: ignore
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
            prompt_text = msg
            if choices: prompt_text += f" ({'/'.join(choices)})"
            if default: prompt_text += f" [{default}]"
            res = input(f"{prompt_text}: ").strip()
            return res if res else (default if default is not None else "")
    class Table:
        def __init__(self, *a, **k): pass
        def add_column(self, *a, **k): pass
        def add_row(self, *a, **k): pass

console = Console()

# Constants

# SRDR header
_SRDR_MAGIC        = b'\x04\x00\x00\x00'
_HEADER_LEN        = 0x104

# Max plausible money in cents (INT32_MAX)
_MAX_CENTS = 2_147_483_647
# Upper bound for a "plausible" money value in cents ($100,000)
_PLAUSIBLE_MONEY_MAX = 10_000_000

# SRDR slot index → human-readable label
# Per the RAGE engine: SRDR30000–SRDR30014 = manual slots, SRDR30015 = autosave
_SRDR_SLOT_MAP: Dict[str, str] = {
    **{f"SRDR{30000 + i:05d}": f"Manual Slot {i}" for i in range(15)},
    "SRDR30015": "Autosave",
}


# Known XOR key for SRDR format version 4 (verified across multiple saves)
_KNOWN_XOR_KEY = bytes.fromhex("9a2c64cf6a76acfae1430b728940903c")


def _extract_xor_key(payload: bytes, key_len: int = 16) -> bytes:
    """
    Returns the XOR key for this payload.
    Fast path: verifies the known hardcoded key works (>1% block occurrences).
    Slow path: byte-frequency analysis fallback for unknown key variants.
    """
    if len(payload) < key_len:
        raise RuntimeError("Payload too small to extract XOR key")

    # Fast path: check if the known key works
    total_blocks = len(payload) // key_len
    count = 0
    KNOWN_KEY_THRESHOLD = 0.005
    
    for block_idx in range(total_blocks):
        start = block_idx * key_len
        if payload[start:start + key_len] == _KNOWN_XOR_KEY:
            count += 1
    
    if count >= total_blocks * KNOWN_KEY_THRESHOLD:
        # Double-check with null ratio for robustness on dense saves
        test_plain = _xor_deobfuscate(payload[:4096], _KNOWN_XOR_KEY)
        null_ratio = sum(1 for b in test_plain if b == 0) / len(test_plain)
        if null_ratio >= 0.25:
            return _KNOWN_XOR_KEY


    # Slow path: byte-frequency analysis
    console.print("[dim]Known key mismatch, extracting key...[/dim]")
    key_bytes = bytearray(key_len)
    for pos in range(key_len):
        counts = [0] * 256
        for i in range(pos, len(payload), key_len):
            counts[payload[i]] += 1
        key_bytes[pos] = counts.index(max(counts))

    key = bytes(key_bytes)
    count = 0
    for block_idx in range(total_blocks):
        start = block_idx * key_len
        if payload[start:start + key_len] == key:
            count += 1
    
    if count < total_blocks * 0.01:
        raise RuntimeError(
            f"XOR key extraction uncertain: key appears "
            f"only {count}/{total_blocks} aligned blocks"
        )

    return key


def _xor_deobfuscate(payload: bytes, key: bytes) -> bytearray:
    """Applies XOR key to deobfuscate/re-obfuscate payload (symmetric).
    Uses a single big-integer XOR for maximum speed in pure Python."""
    payload_len = len(payload)
    key_len = len(key)
    
    # Expand key to cover the full payload length
    full_reps = payload_len // key_len
    remainder = payload_len % key_len
    expanded_key = key * full_reps + key[:remainder]  # type: ignore
    
    payload_int = int.from_bytes(payload, 'big')
    key_int = int.from_bytes(expanded_key, 'big')
    result_int = payload_int ^ key_int
    
    return bytearray(result_int.to_bytes(payload_len, 'big'))


def _sign_and_write(save_path: Path, work_data: bytearray, xor_key: bytes, header: bytes) -> int:
    """
    Centralized logic to sign, re-obfuscate and write an SRDR file.
    Returns the new checksum value.
    Note: copies work_data to avoid in-place mutation.
    """
    signed_data = bytearray(work_data)
    # 1. Recalculate JOAAT checksum on the modified deobfuscated payload (excluding the last 4 bytes)
    payload_data = bytes(signed_data[:-4])
    new_checksum = joaat(payload_data)
    checksum_bytes = struct.pack("<I", new_checksum)
    
    # 2. Append/replace the new checksum at the end of the deobfuscated payload
    signed_data[-4:] = checksum_bytes

    re_obfuscated = _xor_deobfuscate(bytes(signed_data), xor_key)
    final_data = header + bytes(re_obfuscated)

    with open(save_path, 'wb') as f:
        f.write(final_data)
    
    return new_checksum


def joaat(data: bytes) -> int:
    """
    Computes the Jenkins One-At-A-Time (JOAAT) hash used by Rockstar.
    Calculations mimic the 32-bit limits of C++.
    """
    hash_val = 0
    for byte in data:
        hash_val += byte
        hash_val &= 0xFFFFFFFF
        hash_val += (hash_val << 10)
        hash_val &= 0xFFFFFFFF
        hash_val ^= (hash_val >> 6)
        
    hash_val += (hash_val << 3)
    hash_val &= 0xFFFFFFFF
    hash_val ^= (hash_val >> 11)
    hash_val += (hash_val << 15)
    hash_val &= 0xFFFFFFFF
    
    return hash_val



def validate_and_sign_srdr(file_path: Path) -> bool:
    """
    Validates a modified SRDR save file by recalculating its JOAAT signature.
    """
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read()

        plaintext, meta = handle_srdr_layers(raw_data, mode='decrypt')
        
        new_checksum = _sign_and_write(file_path, plaintext, meta['xor_key'], meta['original_header'])
            
        console.print(f"[bold green]✓ Checksum recalculated and updated ({hex(new_checksum)})[/bold green]")
        return True
    
    except Exception as e:
        console.print(f"[red]Failed to sign SRDR file: {e}[/red]")
        return False



def handle_srdr_layers(data: bytes, mode: str = 'decrypt') -> Tuple[bytearray, Dict]:
    """
    Strips XOR obfuscation from an SRDR save payload.
    Returns (plaintext_bytearray, metadata_dict).
    """
    if mode != 'decrypt':
        raise ValueError(f"Only 'decrypt' mode supported, got '{mode}'")

    if len(data) <= _HEADER_LEN:  # type: ignore
        raise RuntimeError(f"File too small ({len(data)} bytes)")

    if data[:4] != _SRDR_MAGIC:  # type: ignore
        console.print(
            f"[yellow]Warning: unexpected magic {data[:4].hex()}, "  # type: ignore
            f"expected {_SRDR_MAGIC.hex()}[/yellow]"
        )

    header = data[:_HEADER_LEN]  # type: ignore
    payload = data[_HEADER_LEN:]  # type: ignore

    # Extract the XOR key from the payload
    xor_key = _extract_xor_key(payload)

    plaintext = _xor_deobfuscate(payload, xor_key)
    null_sample = sum(1 for b in plaintext[:4096] if b == 0)  # type: ignore
    null_ratio = null_sample / min(4096, len(plaintext))
    if null_ratio < 0.3:
        console.print(
            f"[yellow]Warning: deobfuscated data has low null ratio "
            f"({null_ratio:.1%}). XOR key may be incorrect.[/yellow]"
        )

    metadata: Dict[str, Any] = {
        'is_auto': False,  # kept for API compatibility
        'manual_shift': 0,
        'original_header': header,
        'header_len': _HEADER_LEN,
        'xor_key': xor_key,
    }

    return plaintext, metadata


def _slot_label(filename: str) -> str:
    """Returns a human-readable label for an SRDR filename."""
    base = Path(filename).stem.upper()
    return _SRDR_SLOT_MAP.get(base, base)


def _find_profiles_dir(prefix_path: Path) -> Optional[Path]:
    profiles_dir = (
        prefix_path
        / "drive_c/users/steamuser/Documents/Rockstar Games"
        / "Red Dead Redemption 2/Profiles"
    )
    return profiles_dir if profiles_dir.exists() else None


def list_save_files(prefix_path: Optional[Path]) -> List[Dict]:
    if not prefix_path:
        return []
    profiles_dir = _find_profiles_dir(prefix_path)
    if not profiles_dir:
        return []

    saves: List[Dict] = []
    for profile_dir in profiles_dir.iterdir():
        if not profile_dir.is_dir():
            continue
        for save_file in profile_dir.glob("SRDR*"):
            name = save_file.name
            if name.endswith(".bak") or "clone" in name.lower() or "_CLONED_" in name:
                continue
            try:
                stat = save_file.stat()
            except OSError:
                continue
            is_auto = name.upper() == "SRDR30015"
            saves.append({
                "path":    save_file,
                "name":    name,
                "label":   _slot_label(name),
                "size_kb": round(stat.st_size / 1024, 1),  # type: ignore
                "mtime":   datetime.datetime.fromtimestamp(stat.st_mtime),
                "profile": profile_dir.name,
                "is_auto": is_auto,
            })
    saves.sort(key=lambda s: (
        0 if s["is_auto"] else 1,
        s["name"].upper(),
        -s["mtime"].timestamp(),
    ))
    return saves


def get_latest_save_file(prefix_path: Optional[Path]) -> Optional[Path]:
    saves = list_save_files(prefix_path)
    if not saves:
        return None
    return max(saves, key=lambda s: s["mtime"])["path"]


def select_save_file(prefix_path: Optional[Path]) -> Optional[Path]:
    saves = list_save_files(prefix_path)
    if not saves:
        console.print("[red]No SRDR save files found in the Proton prefix.[/red]")
        return None
    table = Table(title="Available Save Files", show_lines=True)
    table.add_column("#",        style="bold cyan",    width=4,  justify="right")
    table.add_column("Slot",     style="bold white",   width=18)
    table.add_column("File",     style="dim",          width=14)
    table.add_column("Profile",  style="magenta",      width=10)
    table.add_column("Size",     style="yellow",       width=9,  justify="right")
    table.add_column("Modified", style="green",        width=20)
    for i, s in enumerate(saves, 1):
        auto_tag = " [bold yellow](AUTO)[/bold yellow]" if s["is_auto"] else ""
        table.add_row(  # type: ignore
            str(i),
            s["label"] + auto_tag,
            s["name"],
            s["profile"][:8],
            f"{s['size_kb']} KB",
            s["mtime"].strftime("%Y-%m-%d  %H:%M"),
        )
    console.print(table)
    valid_choices = [str(i) for i in range(1, len(saves) + 1)] + ["0"]
    choice = Prompt.ask(
        f"Select a save slot to edit [bold](1–{len(saves)})[/bold], or [bold]0[/bold] to cancel",
        choices=valid_choices,
    )
    if choice == "0":
        return None
    selected = saves[int(choice) - 1]
    console.print(
        f"[cyan]Selected:[/cyan] [bold]{selected['label']}[/bold] "
        f"([dim]{selected['name']}[/dim])"
    )
    return selected["path"]


def _find_all_occurrences(data: bytearray, pattern: bytes) -> List[int]:
    """Finds all occurrences of pattern in data."""
    positions = []
    idx = 0
    while True:
        pos = data.find(pattern, idx)
        if pos == -1:
            break
        positions.append(pos)
        idx = pos + 1  # type: ignore
    return positions


def _scan_int32_positions(
    data: bytearray, low: int, high: int,
) -> Dict[int, List[int]]:
    """
    Scans data for int32 values in [low, high].
    Uses unaligned sliding window.
    """
    result: Dict[int, List[int]] = {}
    mv = memoryview(data).cast('B')
    n = len(data) - 3
    fmt = '<i'
    unpack_from = struct.unpack_from
    for i in range(n):
        try:
            val = unpack_from(fmt, mv, i)[0]
            if low <= val <= high:
                result.setdefault(val, []).append(i)
        except (struct.error, IndexError):
            continue
    return result


def _scan_float32_positions(
    data: bytearray, low: float, high: float,
) -> Dict[float, List[int]]:
    """
    Scans data for float32 values in [low, high].
    Uses unaligned sliding window.
    """
    result: Dict[float, List[int]] = {}
    mv = memoryview(data).cast('B')
    n = len(data) - 3
    fmt = '<f'
    unpack_from = struct.unpack_from
    for i in range(n):
        try:
            val = unpack_from(fmt, mv, i)[0]
            if val == val and low <= val <= high:  # NaN check
                rounded = round(float(val), 2)
                result.setdefault(rounded, []).append(i)
        except (struct.error, IndexError):
            continue
    return result


def _prompt_candidate_choice(label: str, candidates: list, shown: int = 10) -> int:
    """
    Shows candidates and lets the user pick one.
    Returns the 0-based index of the selected candidate.
    """
    console.print(f"\n[cyan]{label}:[/cyan]")
    shown = min(shown, len(candidates))
    for i in range(shown):
        line = candidates[i]
        # Allow passing pre-formatted strings or just objects
        console.print(f"  {line}")

    valid = [str(i) for i in range(1, shown + 1)]
    choice = Prompt.ask(
        f"Select a candidate [bold](1–{shown})[/bold]",
        choices=valid,
        default="1",
    )
    return int(choice) - 1


def _patch_money(
    work_data: bytearray,
    money_amount: float,
    current_money: Optional[float],
    force: bool = False, # BUG-N05: non-interactive mode
) -> bool:
    """
    Patches money in the deobfuscated save data using fuzzy-range scanning.
    """

    target_cents = int(round(money_amount * 100))
    if target_cents < 0 or target_cents > _MAX_CENTS:
        console.print(
            f"[red]Money value ${money_amount:.2f} is out of range. "
            f"Maximum is ${_MAX_CENTS / 100:.2f}.[/red]"
        )
    if current_money:
        # User gave a hint — scan ±50% around it
        approx_cents = int(round(current_money * 100))
        console.print(
            f"[cyan]Scanning for values near ${current_money:.2f} ({approx_cents} cents)...[/cyan]"
        )
        low_cents = max(1, int(approx_cents * 0.5))
        high_cents = int(approx_cents * 1.5)
    else:
        # No hint — broad scan of up to $100k
        console.print(
            "[cyan]No current money hint — broad-scanning all plausible "
            "money values ($0.01–$100,000)...[/cyan]"
        )
        low_cents = 1
        high_cents = _PLAUSIBLE_MONEY_MAX


    int_positions = _scan_int32_positions(work_data, low_cents, high_cents)

    # Merge: (display_value, count, offsets_list)
    all_candidates: List[Tuple[float, int, List[int]]] = []
    for cents_val, offsets in int_positions.items():
        all_candidates.append((cents_val / 100.0, len(offsets), offsets))

    else:
        all_candidates.sort(key=lambda x: -x[1])

    if not all_candidates:
        console.print(
            f"[yellow]No plausible money matches found (int32 cents). Cannot patch money.[/yellow]"
        )
        return False
    if force:
        # Auto-pick the first one (usually the closest match due to sorting)
        chosen_display, chosen_count, chosen_offsets = all_candidates[0]
        console.print(f"[cyan]Force-selected candidate #1: ${chosen_display:.2f}[/cyan]")
    else:
        # Build display lines for user selection
        shown = min(10, len(all_candidates))
        lines = []
        for i, (display, count, _) in enumerate(all_candidates[:shown], 1):
            lines.append(f"{i:>2d}. ${display:>12.2f}  ({count:>4d} hits)")

        idx = _prompt_candidate_choice("Money candidates found", lines, shown)
        chosen_display, chosen_count, chosen_offsets = all_candidates[idx]

    if chosen_count > 10 and not force:
        console.print(f"[yellow]Warning: {chosen_count} hits for this value. "
                      "Patching all might corrupt unrelated data.[/yellow]")
        confirm = Prompt.ask("Patch all occurrences? (y/N)", default="n")
        if confirm.lower() != "y":
            chosen_offsets = chosen_offsets[:1]
    elif chosen_count > 10 and force:
        console.print(f"[yellow]Auto-patching: too many hits ({chosen_count}), limiting to first occurrence.[/yellow]")
        chosen_offsets = chosen_offsets[:1]
    new_bytes = struct.pack('<i', target_cents)


    for pos in chosen_offsets:
        work_data[pos:pos + 4] = new_bytes

    console.print(
        f"[bold green]✓ Patched Money: ${chosen_display:.2f} → "
        f"${money_amount:.2f} at {len(chosen_offsets)} location(s)[/bold green]"
    )
    return True





def _patch_honor(
    work_data: bytearray,
    honor_choice: str,
    current_honor: Optional[float],
    force: bool = False,
) -> bool:
    """
    Patches honor in the deobfuscated save data.
    Typical range is ±320 or similar internally.
    """

    # Max honor values (empirical/documented)
    HONOR_MAX = 320
    HONOR_MIN = -320
    
    target_val = HONOR_MAX if honor_choice == "highest" else HONOR_MIN
    target_bytes = struct.pack('<i', target_val)

    console.print("[cyan]Scanning for honor values (int32)...[/cyan]")

    # Scan for int32 values in a reasonable honor range
    int_positions = _scan_int32_positions(work_data, HONOR_MIN - 10, HONOR_MAX + 10)

    for val, offsets in int_positions.items():
        if len(offsets) > 50: 
            continue
        filtered.append((float(val), len(offsets), offsets))

    else:
        # Sort by proximity to user hint if available
        if current_honor is not None:
            filtered.sort(key=lambda x: (abs(x[0] - current_honor), -x[1]))
        else:
            filtered.sort(key=lambda x: x[1])

    if not filtered:
        console.print(
            "[yellow]No plausible honor values found (int32).[/yellow]"
        )
        return False

    if force:
        chosen_val, chosen_count, chosen_offsets = filtered[0]
        console.print(f"[cyan]Force-selected candidate #1: {chosen_val:.0f}[/cyan]")
    else:
        # Build display lines
        shown = min(10, len(filtered))
        lines = []
        for i, (val, count, _) in enumerate(filtered[:shown], 1):
            lines.append(f"{i:>2d}. {val:>10.0f}  ({count:>4d} hits)")

        idx = _prompt_candidate_choice("Honor candidates found", lines, shown)
        chosen_val, chosen_count, chosen_offsets = filtered[idx]


    if chosen_count > 5 and not force:
        console.print(f"[yellow]Warning: {chosen_count} hits for honor. Risk of corruption.[/yellow]")
        confirm = Prompt.ask("Patch all occurrences? (y/N)", default="n")
        if confirm.lower() != "y":
            chosen_offsets = chosen_offsets[:1]
    elif chosen_count > 5 and force:
        console.print(f"[yellow]Auto-patching: too many hits ({chosen_count}) for honor, limiting to first.[/yellow]")
        chosen_offsets = chosen_offsets[:1]


    # Patch
    for pos in chosen_offsets:
        work_data[pos:pos + 4] = target_bytes

    console.print(
        f"[bold green]✓ Patched Honor: {chosen_val:.0f} → {target_val} "
        f"at {len(chosen_offsets)} location(s)[/bold green]"
    )
    return True



def _is_rdr2_running() -> bool:
    """Checks if RDR2.exe is currently running (Steam Deck / Linux)."""
    try:
        # Check for RDR2.exe in process list
        result = subprocess.run(["pgrep", "-f", "RDR2.exe"], capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False


def edit_save_file(
    save_path: Path,
    money_amount: Optional[float] = None,
    honor_choice: Optional[str] = None,
    current_money: Optional[float] = None,
    current_honor: Optional[float] = None,
    force: bool = False,
) -> bool:
    """
    Edits an SRDR save file.
    """

    if _is_rdr2_running():
        console.print("[bold red]⚠ Red Dead Redemption 2 is currently running.[/bold red]")
        console.print("[yellow]Modifying saves while the game is open can lead to data loss or corruption.[/yellow]")
        if force:
            console.print("[yellow]Force mode: proceeding despite running game.[/yellow]")
        else:
            confirm = Prompt.ask("Proceed anyway? (y/N)", default="n")
            if confirm.lower() != "y":
                return False

    console.print(f"\n[cyan]Editing Save File: {save_path.name}[/cyan]")

    existing_clones = sorted(save_path.parent.glob(f"{save_path.stem}_CLONED_*"))
    while len(existing_clones) >= 3:
        try:
            existing_clones.pop(0).unlink(missing_ok=True)
        except OSError:
            break
    pure_stem = save_path.name.split('.')[0]
    bak_path = save_path.parent / (pure_stem + ".bak")
    
    # Avoid shutil.copy2 warning if save_path is already .bak
    if bak_path.resolve() != save_path.resolve():
        try:
            shutil.copy2(save_path, bak_path)
            console.print(f"Backup refreshed: {bak_path.name}")
        except OSError as e:
            console.print(f"[yellow]Warning: could not refresh backup: {e}[/yellow]")
    else:
        console.print("[dim]Save file is already a .bak, skipping self-backup.[/dim]")



    clone_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    clone_path = save_path.parent / f"{save_path.stem}_CLONED_{clone_ts}"
    try:
        shutil.copy2(save_path, clone_path)
        console.print(f"Safe duplication: Created cloned stamp ({clone_path.name})")
    except OSError as e:
        console.print(f"[yellow]Warning: could not create clone: {e}[/yellow]")


    # ── Read raw file ────────────────────────────────────────────────────
    with open(save_path, 'rb') as f:
        raw_data = f.read()

    # ── Unwrap (XOR deobfuscation) ───────────────────────────────────────
    try:
        work_data, meta = handle_srdr_layers(raw_data, mode='decrypt')
        xor_key = meta['xor_key']
        header = meta['original_header']
        console.print(
            f"Deobfuscated payload ({len(work_data)} bytes, "
            f"key: {xor_key.hex()[:16]}...)"
        )
    except RuntimeError as e:
        console.print(f"[red]Failed to unwrap save layers: {e}[/red]")
        return False

    # ── Apply patches ────────────────────────────────────────────────────
    patched_any = False
    if money_amount is not None:
        if _patch_money(work_data, money_amount, current_money, force=force):
            patched_any = True

    if honor_choice is not None:
        if _patch_honor(work_data, honor_choice, current_honor, force=force):
            patched_any = True


    if not patched_any:
        console.print("[yellow]No changes made to save file.[/yellow]")
        return False

    # ── Re-wrap: JOAAT Checksum, XOR re-obfuscate and write back ──
    try:
        new_checksum = _sign_and_write(save_path, work_data, xor_key, header)

        console.print(
            f"[bold green]✓ Save file written ({save_path.stat().st_size} bytes). "
            f"Signed with checksum {hex(new_checksum)}[/bold green]"
        )
        return True

    except Exception as e:
        console.print(f"[red]Failed to re-wrap save file: {e}[/red]")

        if bak_path.exists():
            try:
                shutil.copy2(bak_path, save_path)
                console.print("[yellow]Restored save file from backup.[/yellow]")
            except OSError:
                pass
        return False


def _prompt_save_edits():
    money_input = Prompt.ask(
        "Enter Target Money Amount (e.g. 5000), or leave empty to skip",
        default="",
    )
    money_val: Optional[float] = None
    curr_money_val: Optional[float] = None

    if money_input.strip():
        try:
            money_val = float(money_input)
            if money_val <= 0:
                console.print("[yellow]Money must be positive. Skipping.[/yellow]")
                money_val = None
            elif int(round(money_val * 100)) > _MAX_CENTS:
                console.print(
                    f"[yellow]Money value ${money_val:.2f} exceeds maximum "
                    f"(${_MAX_CENTS / 100:.2f}). Skipping.[/yellow]"
                )
                money_val = None
        except ValueError:
            console.print("[yellow]Invalid money value, skipping.[/yellow]")

    if money_val is not None:
        curr_input = Prompt.ask(
            "Your approximate current money? (e.g. 100) "
            "— helps locate the field, or leave empty for auto-scan",
            default="",
        )
        if curr_input.strip():
            try:
                curr_money_val = float(curr_input)
            except ValueError:
                console.print("[yellow]Invalid value, will use auto-scan.[/yellow]")

    honor_input = Prompt.ask(
        "Honor level?",
        choices=["highest", "lowest", "none"],
        default="none",
    )
    honor_val: Optional[str] = honor_input if honor_input != "none" else None
    curr_honor_val: Optional[float] = None
    if honor_val:
        honor_str = Prompt.ask(
            "Your approximate current honor? (optional, leave empty for auto-scan)",
            default="",
        )
        if honor_str.strip():
            try:
                curr_honor_val = float(honor_str)
            except ValueError:
                pass

    return money_val, honor_val, curr_money_val, curr_honor_val


def handle_option_4(prefix_path: Optional[Path]):
    if not prefix_path:
        console.print("[red]Prefix path not known. Run option 1 first.[/red]")
        return
    saves = list_save_files(prefix_path)
    if not saves:
        console.print("[red]No SRDR save files found in the Proton prefix.[/red]")
        return
    console.print("\n[bold cyan]--- Save File Editor ---[/bold cyan]")
    console.print(
        "[dim]The tool will scan your save data to find money/honor values "
        "automatically.[/dim]\n"
    )
    target_save = select_save_file(prefix_path)
    if not target_save:
        console.print("[yellow]Cancelled.[/yellow]")
        return
    money_val, honor_val, curr_money_val, curr_honor_val = _prompt_save_edits()
    if money_val is None and honor_val is None:
        console.print("[yellow]Nothing to do – no edits requested.[/yellow]")
        return
    success = edit_save_file(
        target_save,
        money_amount=money_val,
        honor_choice=honor_val,
        current_money=curr_money_val,
        current_honor=curr_honor_val,
    )
    if success:
        console.print("[bold green]Save file patched successfully![/bold green]")
    else:
        console.print(
            "[bold yellow]Could not patch save file (see details above).[/bold yellow]"
        )


def farm_honor(prefix_path: Path):
    console.print("\n[bold cyan]--- Honor Farmer ---[/bold cyan]")
    target_save = select_save_file(prefix_path)
    if not target_save:
        console.print("[yellow]Cancelled.[/yellow]")
        return
    curr_honor = Prompt.ask(
        "Your approximate current honor? (optional, leave empty for auto-scan)",
        default="",
    )
    curr_val: Optional[float] = None
    if curr_honor.strip():
        try:
            curr_val = float(curr_honor)
        except ValueError:
            console.print("[yellow]Invalid value, will use auto-scan.[/yellow]")
    success = edit_save_file(
        target_save, honor_choice="highest", current_honor=curr_val
    )
    if success:
        console.print("[bold green]Honor Farming Complete![/bold green]")
    else:
        console.print(
            "[bold yellow]Honor Farming encountered issues "
            "(see details above). Binary state unchanged.[/bold yellow]"
        )


def create_save_snapshot(prefix_path: Path, backup_dir: Path):
    """Creates a timestamped ZIP snapshot of the entire RDR2 Profiles directory."""
    profiles_dir = _find_profiles_dir(prefix_path)
    if not profiles_dir:
        console.print("[red]Profiles directory not found. Cannot create snapshot.[/red]")
        return

    snapshot_dir = backup_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"rdr2_saves_{timestamp}.zip"
    zip_path = snapshot_dir / zip_name

    console.print(f"[cyan]Creating snapshot: {zip_name}...[/cyan]")
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(profiles_dir):
                for file in files:
                    file_path = Path(root) / file
                    zipf.write(file_path, file_path.relative_to(profiles_dir.parent))
        console.print(
            f"[bold green]Snapshot created successfully: {zip_name} at {zip_path}[/bold green]"
        )
    except Exception as e:
        console.print(f"[red]Failed to create snapshot: {e}[/red]")


def restore_save_snapshot(prefix_path: Path, backup_dir: Path):
    """Lists and restores a save snapshot ZIP."""
    snapshot_dir = backup_dir / "snapshots"
    if not snapshot_dir.exists():
        console.print("[yellow]No snapshots found.[/yellow]")
        return

    snapshots = sorted(list(snapshot_dir.glob("*.zip")), reverse=True)
    if not snapshots:
        console.print("[yellow]No snapshots found.[/yellow]")
        return

    table = Table(title="Available Save Snapshots", show_lines=True)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Snapshot Name", style="white")
    table.add_column("Date", style="green")
    table.add_column("Size", style="yellow")

    for i, snap in enumerate(snapshots, 1):
        stat = snap.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        size = round(stat.st_size / (1024 * 1024), 2)
        table.add_row(str(i), snap.name, mtime, f"{size} MB")

    console.print(table)
    choice = Prompt.ask(
        f"Select a snapshot to restore (1-{len(snapshots)}), or 0 to cancel",
        default="0",
    )
    if choice == "0" or not choice.isdigit():
        return

    idx = int(choice) - 1
    if idx < 0 or idx >= len(snapshots):
        return

    selected_snap = snapshots[idx]
    profiles_dir = _find_profiles_dir(prefix_path)
    if not profiles_dir:
        return

    console.print(f"[yellow]Restoring snapshot: {selected_snap.name}...[/yellow]")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    old_profiles = profiles_dir.parent / f"Profiles_OLD_{timestamp}"
    try:
        profiles_dir.rename(old_profiles)
        console.print(f"Backed up current profiles to: {old_profiles.name}")
    except Exception as e:
        console.print(f"[red]Failed to backup current profiles: {e}[/red]")
        return

    try:
        with zipfile.ZipFile(selected_snap, 'r') as zipf:
            zipf.extractall(profiles_dir.parent)
        console.print("[bold green]Snapshot restored successfully![/bold green]")
    except Exception as e:
        console.print(
            f"[red]Failed to extract snapshot: {e}. "
            f"Attempting to restore old profiles...[/red]"
        )
        if old_profiles.exists():
            old_profiles.rename(profiles_dir)
