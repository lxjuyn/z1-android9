#!/usr/bin/env python3
"""Unpack / patch / repack the Z1 ramdisk (gzip newc cpio) with the boot-speedup edits.

usage: patch_ramdisk.py <rootfs_blob_in> <rootfs_blob_out> <workdir>
Edits (idempotent):
  1. init.zygote32.rc : add -Xnorelocate -Xnoimage-dex2oat to the zygote service line
  2. default.prop     : pm.dexopt.first-boot=quicken -> verify
  3. add /z1_bootimg_clean.sh and an exec hook in init.z1.rc (post-fs-data)
"""
import os, sys, gzip, struct, subprocess, shutil, io

CLEAN_SH = r'''#!/system/bin/sh
# z1 bootspeed: delete stale boot-image copies (image/oat/vdex/rel) from dalvik-cache.
# ART's CreateBootImage Step 1.a accepts a cache image if only its recorded oat
# checksum matches the system one -- but that checksum does not cover generated
# code, so a stale image can be paired with the wrong oat (=> SIGILL). Removing
# these makes ART use /system/framework/arm directly (Step 2.a, with -Xnorelocate).
for d in /data/dalvik-cache /data/dalvik-cache/*; do
  [ -d "$d" ] || continue
  rm -f "$d"/boot*.art "$d"/boot*.oat "$d"/boot*.vdex "$d"/boot*.rel \
        "$d"/system@framework@boot*.art "$d"/system@framework@boot*.oat \
        "$d"/system@framework@boot*.vdex "$d"/system@framework@boot*.rel 2>/dev/null
done
exit 0
'''

HOOK = '''
# z1 bootspeed: remove stale boot-image copies before zygote can load them.
on post-fs-data
    exec - root root -- /system/bin/sh /z1_bootimg_clean.sh
'''

def nf(v): return ('%08x' % v).encode()
def pad4(b): return b + b'\0'*((-len(b))%4)

def unpack(d, out):
    os.makedirs(out, exist_ok=True); i=0; n=0; modes={}
    while i < len(d):
        if d[i:i+6] not in (b'070701', b'070702'):
            raise SystemExit('bad cpio magic at %d' % i)
        hdr=d[i:i+110]; vals=[int(hdr[6+8*k:14+8*k],16) for k in range(13)]
        mode, filesize, namesize = vals[1], vals[6], vals[11]
        name=d[i+110:i+110+namesize-1].decode()
        h=(110+namesize+3)&~3
        data=d[i+h:i+h+filesize]
        i=i+h+filesize; i=(i+3)&~3
        if name=='TRAILER!!!': break
        if name in ('.', './', ''): continue
        rel=name[2:] if name.startswith('./') else name.lstrip('/')
        p=os.path.join(out, rel.rstrip('/'))
        fmt = mode & 0o170000
        if fmt == 0o040000:
            os.makedirs(p, exist_ok=True)
        elif fmt == 0o120000:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if os.path.lexists(p): os.unlink(p)
            os.symlink(data.decode(), p)
        elif fmt == 0o100000:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p,'wb').write(data); n+=1
        else:
            raise SystemExit('unsupported cpio entry type %o: %s' % (fmt, name))
        modes[rel.rstrip('/')]=mode & 0o7777
    for rel,m in modes.items():
        try: os.chmod(os.path.join(out, rel), m)
        except OSError: pass
    return n

def repack(root, outpath):
    # same recipe as tools/rebuild_ramdisk_v13_7.sh: find | LC_ALL=C sort | cpio -o -H newc | gzip -9n
    names=subprocess.run(['find','.'],cwd=root,capture_output=True,check=True).stdout.split(b'\n')
    names=[x for x in names if x]
    names.sort()
    p=subprocess.run(['cpio','-o','-H','newc','--quiet'],cwd=root,input=b'\n'.join(names)+b'\n',
                     capture_output=True)
    assert p.returncode==0, p.stderr
    body=gzip.compress(p.stdout, compresslevel=9, mtime=0)
    open(outpath,'wb').write(body)
    return len(body)

def main(blob_in, blob_out, wd):
    src=open(blob_in,'rb').read()
    assert src[:4]==bytes.fromhex('88168858') and src[8:14]==b'ROOTFS', 'not MTK ROOTFS blob'
    body=src[512:]
    hdr=bytearray(src[:512])
    root=os.path.join(wd,'root')
    shutil.rmtree(wd, ignore_errors=True); os.makedirs(root)
    n=unpack(gzip.decompress(body), root)
    print('unpacked %d files -> %s' % (n, root))

    zrc=os.path.join(root,'init.zygote32.rc')
    z=open(zrc).read()
    old='-Xzygote /system/bin --zygote --start-system-server'
    new='-Xzygote -Xnorelocate -Xnoimage-dex2oat /system/bin --zygote --start-system-server'
    if new not in z:
        assert old in z, 'unexpected zygote service line: %r' % z
        z=z.replace(old,new)
        open(zrc,'w').write(z)
    print('init.zygote32.rc:', [l for l in open(zrc).read().splitlines() if l.startswith('service zygote')][0])

    dp=os.path.join(root,'default.prop')
    d=open(dp).read()
    assert 'pm.dexopt.first-boot=quicken' in d, 'pm.dexopt.first-boot not found'
    d=d.replace('pm.dexopt.first-boot=quicken','pm.dexopt.first-boot=verify')
    open(dp,'w').write(d)
    print('default.prop:', [l for l in open(dp).read().splitlines() if l.startswith('pm.dexopt.first-boot')][0])

    z1=os.path.join(root,'init.z1.rc')
    t=open(z1).read()
    if 'z1_bootimg_clean.sh' not in t:
        t += HOOK
        open(z1,'w').write(t)
    cs=os.path.join(root,'z1_bootimg_clean.sh')
    open(cs,'w').write(CLEAN_SH); os.chmod(cs,0o755)
    print('added /z1_bootimg_clean.sh + post-fs-data exec hook')

    nbytes=repack(root, os.path.join(wd,'body_new.gz'))
    struct.pack_into('<I',hdr,4,nbytes)
    open(blob_out,'wb').write(bytes(hdr)+open(os.path.join(wd,'body_new.gz'),'rb').read())
    print('new rootfs blob = %d (gzip %d)' % (os.path.getsize(blob_out), nbytes))

if __name__=='__main__':
    main(*sys.argv[1:4])
