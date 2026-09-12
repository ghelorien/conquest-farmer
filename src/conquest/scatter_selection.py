"""Restore the right-click skill using read-only memory and ordinary UI clicks."""
import hashlib
import math
import struct
import time

from conquest.addressing import checked_address
from conquest.memory_shop import MemoryGui


class SelectionReader:
    def __init__(self,session):
        self.session=session
        self.gui=MemoryGui(session)
        self.base=self.gui.base
        self.qualified=False

    def qualify(self):
        if self.qualified:return
        for rva,size,digest in (
            (0x9acc2,314,'0da14be41a59bd653ed11d0f576ebebfeb477d3a5aa9679fa5f0b30046b2ab5a'),
            (0x1097a5,19,'213a60b72ae5bd5566a3c7af1e7106d80ce2291a7134c87584c03d5653d35075'),
            (0x109845,190,'8db295b764220983700cdc0e9d6f69c95fef86c22a191d7e3a8800a5ecd44527'),
            (0x10995c,19,'7e6b636f9cbe2d438fe2b3737dd4fe941c7098ba4e38ef75eba0df8bd3d96366')):
            if hashlib.sha256(self.session.read_block(self.base+rva,size)).hexdigest()!=digest:
                raise ValueError('Skill selection renderer changed')
        self.qualified=True

    def selected(self):
        self.session.assert_identity()
        self.qualify()
        s=self.session
        head,count=struct.unpack('<QQ',s.read_block(self.base+0x6986c0,16))
        if not 1<=count<=128:raise ValueError('Skill window registry bounds changed')
        node=struct.unpack('<Q',s.read_block(checked_address(head)+8,8))[0]
        seen=set()
        for _ in range(32):
            if node==head or node in seen:break
            seen.add(node)
            raw=s.read_block(checked_address(node),0x38)
            key=struct.unpack_from('<I',raw,0x20)[0]
            if key==1:
                pointer=checked_address(struct.unpack_from('<Q',raw,0x28)[0])
                data=s.read_block(pointer,0xfc)
                if struct.unpack_from('<Q',data)[0]!=self.base+0x5c5a38:
                    raise ValueError('Selected skill control identity changed')
                value=struct.unpack_from('<I',data,0xf8)[0]
                if value>100000:raise ValueError('Selected skill ID outside bounds')
                return value
            node=struct.unpack_from('<Q',raw,0 if key>1 else 16)[0]
        raise ValueError('Selected skill control is unavailable')

    def table(self,window,columns=None):
        s=self.session
        context=checked_address(struct.unpack('<Q',s.read_block(self.base+0x6966f0,8))[0])
        count,capacity,pointer=struct.unpack('<IIQ',s.read_block(context+0x4338,16))
        if not 1<=count<=capacity<=128:raise ValueError('Skill table pool bounds changed')
        raw=s.read_block(checked_address(pointer,count*0x218),count*0x218)
        frame=struct.unpack('<I',s.read_block(context+0x3e38,4))[0]
        matches=[]
        wx,wy=window.position;ww,wh=window.size
        for index in range(count):
            row=raw[index*0x218:(index+1)*0x218]
            active,n=struct.unpack_from('<II',row,0x70)
            left,top,right,bottom=struct.unpack_from('<4f',row,0xf0)
            if (not 0<=frame-active<=3 or not 1<=n<=64
                    or columns is not None and n!=columns):continue
            if (all(math.isfinite(v) for v in (left,top,right,bottom))
                    and wx<=left<right<=wx+ww and wy<=top<bottom<=wy+wh):
                cp=checked_address(struct.unpack_from('<Q',row,0x18)[0],n*0x68)
                matches.append((n,(left,top,right,bottom),s.read_block(cp,n*0x68)))
        if len(matches)!=1:raise ValueError('Skill table is absent or ambiguous')
        if self.gui.read(window.name)!=window:raise ValueError('Skill window moved')
        return matches[0]

    def menu_point(self):
        window=self.gui.read('##Control')
        n,rect,columns=self.table(window,6)
        width=struct.unpack_from('<f',columns,5*0x68+0x10)[0]
        left=struct.unpack_from('<f',columns,5*0x68+0x34)[0]
        if width!=40 or rect[3]-rect[1]!=40:
            raise ValueError('Skill menu button layout changed')
        return self.checked_point(window,(left+20,rect[1]+20))

    def entries(self,actor):
        s=self.session;result=[]
        for offset in (0x1980,0x19b0):
            header=s.read_block(actor+offset,24)
            start,end,capacity=struct.unpack('<3Q',header)
            if not 0<=end-start<=64*16 or (end-start)%16 or not end<=capacity<=start+128*16:
                raise ValueError('Selectable skill vector bounds changed')
            if start==end:continue
            entries=s.read_block(checked_address(start,end-start),end-start)
            for index in range(0,len(entries),16):
                pointer=struct.unpack_from('<Q',entries,index)[0]
                if not pointer:continue
                raw=s.read_block(checked_address(pointer),0x38)
                if struct.unpack_from('<Q',raw)[0]!=self.base+0x5cff78:
                    raise ValueError('Selectable skill identity changed')
                enabled=struct.unpack_from('<I',raw,8)[0]
                kind=struct.unpack_from('<I',raw,0x10)[0]
                if kind==8001 and (raw[0x18:0x20]!=b'Scatter\0' or not enabled):
                    raise ValueError('Scatter is unavailable')
                result.append(kind)
            if s.read_block(actor+offset,24)!=header or s.read_block(start,end-start)!=entries:
                raise ValueError('Selectable skills changed')
        return result

    @staticmethod
    def checked_point(window,point):
        x,y=point;wx,wy=window.position;ww,wh=window.size
        if not (math.isfinite(x) and math.isfinite(y) and wx<x<wx+ww and wy<y<wy+wh):
            raise ValueError('Skill button lies outside its window')
        return round(x),round(y)

    def scatter_point(self,actor):
        entries=self.entries(actor)
        if entries.count(8001)!=1:raise ValueError('Exactly one selectable Scatter is required')
        window=self.gui.read('Skills')
        if window.scroll!=(0.,0.):raise ValueError('Skill popup must not be scrolled')
        n,rect,columns=self.table(window)
        index=entries.index(8001);row,column=divmod(index,n)
        if rect[3]-rect[1]!=44*math.ceil(len(entries)/n):
            raise ValueError('Skill icon row geometry changed')
        left=struct.unpack_from('<f',columns,column*0x68+0x34)[0]
        if struct.unpack_from('<f',columns,column*0x68+0x10)[0]!=40:
            raise ValueError('Skill icon width changed')
        return self.checked_point(window,(left+20,rect[1]+row*44+20))


class ScatterSelection:
    def __init__(self,observer,notify):
        self.reader=SelectionReader(observer.adapter)
        self.observer,self.notify=observer,notify
        self.pending=False
        self.next_attempt=0.

    def step(self,dispatch):
        selected=self.reader.selected()
        if selected==8001:
            if self.pending:self.notify('scatter_selection_verified',{'skill_id':8001,'source':'read_only_memory'})
            self.pending=False
            return False
        if time.monotonic()<self.next_attempt:return True
        from conquest.memory_life import read_life
        life=read_life(self.observer.adapter,self.observer.health_layout,self.observer.character)
        if life.dead_candidate:raise ValueError('Living character required for skill selection')
        try:self.reader.gui.read('Skills')
        except ValueError:
            point=self.reader.menu_point();stage='opening_skill_menu'
        else:
            point=self.reader.scatter_point(life.object_address);stage='selecting_scatter'
        # Reread selection immediately before ordinary guarded input.
        if self.reader.selected()==8001:return False
        dispatch(point)
        self.pending=True;self.next_attempt=time.monotonic()+.2
        self.notify('scatter_selection_attempt',{'stage':stage,'point':point,'previous_skill_id':selected})
        return True
