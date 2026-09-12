"""Read qualified item names from the client's immutable definition hash table."""
import struct
from conquest.addressing import checked_address
from conquest.memory_life import CLIENT_SHA256

MANAGER_RVA = 0x699860


def item_hash(type_id):
    if type(type_id) is not int or not 0 < type_id < 2**32:
        raise ValueError('Invalid item type ID')
    value = 0xcbf29ce484222325
    for byte in struct.pack('<I', type_id):
        value = ((value ^ byte) * 0x100000001b3) & 0xffffffffffffffff
    return value


def read_definition(session, type_id):
    hashed = item_hash(type_id)
    if session.expected_sha256 != CLIENT_SHA256:
        raise ValueError('Unqualified item definition build')
    modules = [m for m in session.modules if m['name'].casefold()=='imconquer.exe']
    if len(modules)!=1 or modules[0]['size'] <= MANAGER_RVA+0x50:
        raise ValueError('Invalid item definition module')
    base = modules[0]['base']
    address = base+MANAGER_RVA
    session.assert_identity()
    header = session.read_block(address, 0x50)
    vtable, initialized, _, sentinel, count, buckets, end, capacity, mask, size = struct.unpack('<10Q', header)
    if (vtable!=base+0x5cf590 or initialized!=1 or not 1<=count<=100000
            or not 16<=size<=131072 or size&(size-1) or mask!=size-1
            or end-buckets!=size*16 or capacity!=end):
        raise ValueError('Invalid item definition table')
    checked_address(sentinel)
    bucket = checked_address(buckets+(hashed&mask)*16)
    bounds = session.read_block(bucket, 16)
    first, node = struct.unpack('<2Q', bounds)
    visited = set()
    result = None
    samples = []
    while node!=sentinel:
        if node in visited or len(visited)>=64:
            raise ValueError('Invalid item definition chain')
        visited.add(node)
        raw = session.read_block(checked_address(node), 0x40)
        samples.append((node, raw))
        key = struct.unpack_from('<I', raw, 0x10)[0]
        if key==type_id:
            definition_id = struct.unpack_from('<I', raw, 0x18)[0]
            length, allocated = struct.unpack_from('<2Q', raw, 0x30)
            if definition_id!=type_id or not 1<=length<=128 or not max(15,length)<=allocated<=4096:
                raise ValueError('Invalid item definition identity/name')
            name_bytes = raw[0x20:0x20+length] if allocated<=15 else session.read_block(
                checked_address(struct.unpack_from('<Q',raw,0x20)[0]),length)
            name = name_bytes.decode('utf-8')
            if not name.isprintable():
                raise ValueError('Invalid item definition name')
            result = {'type_id':type_id, 'name':name, 'address':node+0x18}
            if allocated>15 and session.read_block(struct.unpack_from('<Q',raw,0x20)[0],length)!=name_bytes:
                raise ValueError('Item definition name changed')
            break
        if node==first:
            break
        node = struct.unpack_from('<Q',raw,8)[0]
    if (session.read_block(address,0x50)!=header or session.read_block(bucket,16)!=bounds
            or any(session.read_block(p,0x40)!=raw for p,raw in samples)):
        raise ValueError('Item definition table changed')
    session.assert_identity()
    if result is None:
        raise ValueError('Item definition not found')
    return result
