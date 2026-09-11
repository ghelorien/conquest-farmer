"""Read-only local input investigation; captured client bytes stay in reports."""
import ctypes
import json
import re
import struct
import base64
from pathlib import Path

from capstone import Cs, CS_ARCH_X86, CS_MODE_64

root = Path(__file__).resolve().parents[1]
from conquest.worker import request
info = root / '.runtime/memory-worker.json'
health = request(info, 'health')
base = health['modules'][0]['base']
data_rva = 0x552000
blocks = []
for offset in range(0, 0xef000, 65536):
    size = min(65536, 0xef000-offset)
    reply = request(info, 'read-block', {'address': hex(base+data_rva+offset), 'size': size})
    block = base64.b64decode(reply['data'], validate=True)
    if len(block) != size:
        raise ValueError('Incomplete read')
    blocks.append(block)
if request(info, 'health')['target'] != health['target']:
    raise ValueError('Client changed')
data = b''.join(blocks)
code = (root / 'reports/runtime-code.bin').read_bytes()
user = ctypes.WinDLL('user32', use_last_error=True)
names = ['GetCursorPos', 'GetAsyncKeyState', 'GetKeyState', 'ScreenToClient',
         'GetForegroundWindow', 'SetCursorPos', 'GetKeyboardState']
slots = {}
for name in names:
    address = ctypes.cast(getattr(user, name), ctypes.c_void_p).value
    needle = struct.pack('<Q', address)
    for i in range(len(data) - 7):
        if data[i:i+8] == needle:
            slots[data_rva + i] = name
cs = Cs(CS_ARCH_X86, CS_MODE_64)
cs.skipdata = True
calls = []
for address, size, mnemonic, operand in cs.disasm_lite(code, 0x1000):
    match = re.search(r'\[rip ([+-]) (0x[0-9a-f]+)\]', operand)
    if match:
        target = address + size + int(match[2], 16) * (1 if match[1] == '+' else -1)
        if target in slots:
            calls.append({'rva': hex(address), 'api': slots[target], 'operation': mnemonic})
result = {'api_slots': {hex(k): v for k,v in slots.items()}, 'references': calls}
(root / 'reports/input-api-references.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
