import unittest
import struct
import os
import shutil
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from save_modifier import joaat, _xor_deobfuscate, _extract_xor_key, _patch_money, _patch_honor, handle_srdr_layers

class TestSaveModifierLogic(unittest.TestCase):
    def test_joaat(self):

        self.assertEqual(joaat(b"test"), 0x3f75ccc1)
        self.assertEqual(joaat(b"rdr2"), 0xc6624185)

    def test_xor_roundtrip(self):
        key = b"1234567890abcdef"
        payload = b"this is a secret message"
        obf = _xor_deobfuscate(payload, key)
        deobf = _xor_deobfuscate(bytes(obf), key)
        self.assertEqual(payload, deobf)

    def test_extract_xor_key(self):
        key = b"1234567890abcdef"

        payload = key * 20 + b"some other data"
        extracted = _extract_xor_key(payload)
        self.assertEqual(key, extracted)

    def test_patch_money_limit(self):
        val = struct.pack('<i', 10000)
        buffer = bytearray(b"01234567" + val + b"middle" + val + b"suffix")
        
        _patch_money(buffer, 500.00, current_money=100.00, force=True)
        
        new_val = struct.pack('<i', 50000)
        self.assertEqual(buffer[8:12], new_val)
        self.assertIn(b"middle" + val, buffer)

    def test_patch_honor_limit(self):
        val = struct.pack('<i', 100)
        buffer = bytearray(b"01234567" + val + b"middle" + val + b"suffix")
        
        _patch_honor(buffer, 320.0, current_honor=100.0, force=True)
        
        new_val = struct.pack('<i', 320)
        self.assertEqual(buffer[8:12], new_val)
        self.assertIn(b"middle" + val, buffer)

    def test_patch_honor_out_of_range(self):
        val_outside = struct.pack('<i', 500)
        buffer = bytearray(b"prefix" + val_outside + b"suffix")
        
        result = _patch_honor(buffer, 320.0, current_honor=500.0, force=True)
        self.assertFalse(result)

    def test_xor_padding_stripping(self):
        header = b"\x04\x00\x00\x00" + b"\x00" * 256
        key = bytes.fromhex("9a2c64cf6a76acfae1430b728940903c")
        real_data = b"this is the real payload data!!!"
        obf_real_data = _xor_deobfuscate(real_data, key)
        payload = obf_real_data + (key * 5)
        
        file_data = header + payload
        plaintext, meta = handle_srdr_layers(file_data)
        
        # After BUG-03 fix, we no longer strip padding. 
        # The plaintext should include the trailing bytes matching the XOR key.
        self.assertEqual(bytes(plaintext), real_data + (b"\x00" * 16 * 5))
        self.assertEqual(meta['xor_key'], key)

if __name__ == '__main__':
    unittest.main()
