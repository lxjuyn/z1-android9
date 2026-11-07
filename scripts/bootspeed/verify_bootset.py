#!/usr/bin/env python3
"""Offline pre-flash gate: does /system/framework/<arch>/ boot image set match libart?

Checks (all against the *actual files in the image*):
  1. image magic/version == art\\n / 056   (ImageHeader::kImageVersion in libart)
  2. oat   magic/version == oat\\n / 138   (OatHeader::kOatVersion in libart)
  3. per-component: ImageHeader.oat_checksum == OatHeader.adler32
     (this is the check ART itself performs when opening the oat from the image)
  4. address layout: image ranges and oat ranges do not overlap
  5. boot-framework: fraction of compiled-method entry points (oat_file_begin +
     symbol) that really occur in the image; ~95% for a same-run pair,
     near 0 for a stale/foreign pair.

usage: verify_bootset.py <system.img> [--dir /framework/arm]
"""
import sys, os, struct, subprocess, tempfile, lzma, shutil

IMG_VERSION=b'056'; OAT_VERSION=b'138'

def dbg(img, cmd):
    return subprocess.run(['debugfs','-R',cmd,img],capture_output=True,text=True).stdout

def dump(img, path, dst):
    subprocess.run(['debugfs','-R','dump %s %s'%(path,dst),img],capture_output=True)

def read_kv(oat):
    d=open(oat,'rb').read(); o=d.find(b'oat\n')
    if o<0: return None
    # OatHeader: 4 magic +4 ver +4 adler +13 more uint32 -> kv_size is the 16th uint32 after adler? scan
    for off in range(o+12,o+12+0x80,4):
        v=struct.unpack_from('<I',d,off)[0]
        if v and 16 <= v < 0x10000:
            kv=d[off+4:off+4+v]
            if b'compiler-filter\0' in kv:
                out={}
                parts=kv.split(b'\0'); i=0
                while i+1 < len(parts):
                    if parts[i]: out[parts[i].decode('latin1')]=parts[i+1].decode('latin1')
                    i+=2
                return out, d, o
    return None

def syms_of_oat(oat):
    """Parse .gnu_debugdata (xz mini-ELF) -> [(value,size,type,name)] or None."""
    d=open(oat,'rb').read()
    if d[:4]!=b'\x7fELF': return None
    shoff=struct.unpack_from('<I',d,0x20)[0]; shentsize=struct.unpack_from('<H',d,0x2e)[0]
    shnum=struct.unpack_from('<H',d,0x30)[0]; shstr=struct.unpack_from('<H',d,0x32)[0]
    secs=[]
    for i in range(shnum):
        o=shoff+i*shentsize
        secs.append(struct.unpack_from('<10I',d,o))
    stroff=secs[shstr][4]
    def nm(n):
        e=d.index(b'\0',stroff+n); return d[stroff+n:e].decode('latin1')
    for s in secs:
        if nm(s[0])=='.gnu_debugdata':
            raw=lzma.decompress(d[s[4]:s[4]+s[5]])
            e_shoff=struct.unpack_from('<I',raw,0x20)[0]
            e_shentsize=struct.unpack_from('<H',raw,0x2e)[0]
            e_shnum=struct.unpack_from('<H',raw,0x30)[0]
            esecs=[struct.unpack_from('<10I',raw,e_shoff+k*e_shentsize) for k in range(e_shnum)]
            for t in esecs:
                if t[1]==2:  # SHT_SYMTAB
                    st=t[4]; n=t[5]//16
                    out=[]
                    for k in range(n):
                        nam,val,size,info,other,shndx=struct.unpack_from('<IIIBBH',raw,st+k*16)
                        out.append((val,size,info&0xf))
                    return out
    return None

def main(img, fwdir='/framework/arm'):
    ls=dbg(img,'ls %s'%fwdir)
    if 'not found' in ls or not ls.strip():
        print('FAIL: %s not present in image'%fwdir); return 1
    names=set()
    for tok in ls.split():
        if tok.endswith('.art') or tok.endswith('.oat'): names.add(tok)
    arts=sorted(n for n in names if n.endswith('.art') and not n.endswith('.art.rel'))
    if not arts:
        print('FAIL: no .art files in %s'%fwdir); return 1
    tmp=tempfile.mkdtemp(prefix='bootset.')
    fails=[]
    infos={}
    print('%-46s %10s %10s %10s %10s'%('component','img_size','oat_ck','oat_adler','match'))
    for a in arts:
        base=a[:-4]
        ap=os.path.join(tmp,a); op=os.path.join(tmp,base+'.oat')
        dump(img,'%s/%s'%(fwdir,a),ap)
        dump(img,'%s/%s'%(fwdir,base+'.oat'),op)
        d=open(ap,'rb').read()
        if d[:4]!=b'art\n' or d[4:8].rstrip(b'\0')!=IMG_VERSION:
            fails.append('%s: image magic/version %r'%(a,d[:8])); continue
        w=struct.unpack_from('<16I',d,8)
        infos[base]={'begin':w[0],'size':w[1],'ck':w[2],'oatb':w[3],'oatd':w[4],'oate':w[5],'compic':w[14],'ispic':w[15]}
        if not os.path.exists(op) or os.path.getsize(op)<0x1000:
            print('%-46s %10d %10s %10s %s'%(base,w[1],hex(w[2]),'-','no .oat')); continue
        kv=read_kv(op)
        if not kv:
            fails.append('%s: no oat KV store'%op); continue
        kvd, od, oo = kv
        adler=struct.unpack_from('<I',od,oo+8)[0]
        ov=od[oo+4:oo+8].rstrip(b'\0')
        if ov!=OAT_VERSION: fails.append('%s: oat version %r'%(base,ov))
        ok = (adler==w[2])
        if not ok: fails.append('%s: image.oat_checksum %#x != oat.adler32 %#x'%(base,w[2],adler))
        infos[base]['adler']=adler
        print('%-46s %10d %#10x %#10x %s'%(base,w[1],w[2],adler,'YES' if ok else 'NO'))
        if base=='boot-framework':
            infos[base]['oat_path']=op; infos[base]['kv']=kvd
    # address layout
    imgs=[(v['begin'],v['begin']+v['size'],k) for k,v in infos.items()]
    oats=[(v['oatd'],v['oate'],k) for k,v in infos.items() if 'oatd' in v]
    def overlaps(a):
        for i in range(len(a)):
            for j in range(i+1,len(a)):
                if a[i][0] < a[j][1] and a[j][0] < a[i][1]:
                    return (a[i][2],a[j][2],a[i][0],a[i][1],a[j][0],a[j][1])
        return None
    oi=overlaps(imgs); oo=overlaps(oats)
    if oi: fails.append('image address overlap: %s'%(oi,))
    if oo: fails.append('oat address overlap: %s'%(oo,))
    ix=[(i[0],i[1]) for i in imgs]; ox=[(o[0],o[1]) for o in oats]
    for a0,a1,k in imgs:
        for b0,b1,j in oats:
            if a0<b1 and b0<a1: fails.append('image %s overlaps oat %s'%(k,j))
    print('address layout: images %d, oats %d, overlaps: image=%s oat=%s'%(len(imgs),len(oats),bool(oi),bool(oo)))
    # multi-image bootclasspath
    fw=infos.get('boot-framework')
    if fw and 'kv' in fw:
        bc=fw['kv'].get('bootclasspath','')
        n=len([x for x in bc.split(':') if x])
        print('bootclasspath entries = %d (expect %d components)'%(n,len(arts)))
        if n != len(arts): fails.append('bootclasspath has %d entries, dir has %d .art'%(n,len(arts)))
    else:
        fails.append('boot-framework.oat KV missing')
    # entry audit
    if fw and 'oat_path' in fw:
        syms=syms_of_oat(fw['oat_path'])
        if syms:
            img=open(os.path.join(tmp,'boot-framework.art'),'rb').read()
            ofb=fw['oatb']; tot=hit=0
            for val,size,typ in syms:
                if typ!=2 or size<=0: continue
                tot+=1
                if struct.pack('<I',ofb+val) in img: hit+=1
            pct=100.0*hit/tot if tot else 0
            print('entry audit: %d/%d (%.2f%%) method entries present in image'%(hit,tot,pct))
            if pct < 90: fails.append('entry audit only %.1f%% (image/oat are not a same-run pair)'%pct)
    print()
    if fails:
        print('FAIL (%d):'%len(fails))
        for f in fails: print('  -',f)
        shutil.rmtree(tmp,ignore_errors=True); return 1
    print('PASS: boot image set is version-matched and internally consistent')
    shutil.rmtree(tmp,ignore_errors=True); return 0

if __name__=='__main__':
    args=[a for a in sys.argv[1:] if not a.startswith('--')]
    fw='/framework/arm'
    for i,a in enumerate(sys.argv):
        if a=='--dir': fw=sys.argv[i+1]
    sys.exit(main(args[0], fw))
