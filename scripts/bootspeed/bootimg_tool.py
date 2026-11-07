#!/usr/bin/env python3
"""Extract / repack the Z1 MTK boot.img (page 2048, MTK KERNEL+ROOTFS 512B headers).

split:  bootimg -> zImage (no dtb), dtb, rootfs blob(512+gzip), cmdline/name
usage:  bootimg_tool.py split  <boot.img> <workdir>
        bootimg_tool.py build  <template.img> <zimage> <dtb> <rootfs_blob> <out.img>
"""
import struct, sys, os, hashlib
PAGE=2048; MAGIC=bytes.fromhex('88168858'); FDT=b'\xd0\x0d\xfe\xed'

def split(img, wd):
    d=open(img,'rb').read()
    assert d[:8]==b'ANDROID!', 'not ANDROID! boot image'
    ksz, rsz = struct.unpack_from('<I',d,8)[0], struct.unpack_from('<I',d,16)[0]
    cmdline=d[64:64+512].split(b'\0')[0].decode()
    name=d[48:64].split(b'\0')[0].decode()
    # MTK KERNEL header at 2048
    kh=d[PAGE:PAGE+512]
    assert kh[:4]==MAGIC and kh[8:14]==b'KERNEL', 'no MTK KERNEL header'
    klen=struct.unpack_from('<I',kh,4)[0]
    kstart=PAGE+512
    kernel=d[kstart:kstart+klen]
    # ramdisk: find MTK ROOTFS header page-aligned after kernel
    pos=(kstart+klen+PAGE-1)//PAGE*PAGE
    while pos < len(d):
        if d[pos:pos+4]==MAGIC and d[pos+8:pos+14]==b'ROOTFS':
            break
        pos+=PAGE
    assert pos < len(d), 'ROOTFS header not found'
    rlen=struct.unpack_from('<I',d,pos+4)[0]
    assert rlen==rsz-512, 'MTK ROOTFS length %d != android ramdisk_size-512 %d'%(rlen,rsz-512)
    rootfs=d[pos:pos+rsz]
    # split appended dtb: FDT magic at end
    j=kernel.rfind(FDT)
    assert j>0, 'dtb magic not found in kernel payload'
    dtb=kernel[j:]
    assert struct.unpack_from('>I',dtb,4)[0]==len(dtb), 'dtb totalsize mismatch'
    zimg=kernel[:j]
    os.makedirs(wd,exist_ok=True)
    open(os.path.join(wd,'zImage'),'wb').write(zimg)
    open(os.path.join(wd,'dtb'),'wb').write(dtb)
    open(os.path.join(wd,'rootfs.img'),'wb').write(rootfs)
    open(os.path.join(wd,'cmdline.txt'),'w').write(cmdline)
    print('kernel payload = %d (zImage %d + dtb %d)'%(len(kernel),len(zimg),len(dtb)))
    print('rootfs blob    = %d (gzip %d)'%(len(rootfs),len(rootfs)-512))
    print('cmdline        = %s'%cmdline)
    print('asserts: android kernel_size=%d == payload+512=%d ; ramdisk_size=%d == rootfs=%d'%(ksz,len(kernel)+512,rsz,len(rootfs)))
    assert ksz==len(kernel)+512 and rsz==len(rootfs)

def build(tpl, zimg, dtb, rootfs, out):
    t=open(tpl,'rb').read(); z=open(zimg,'rb').read(); dt=open(dtb,'rb').read(); rf=open(rootfs,'rb').read()
    assert t[:8]==b'ANDROID!'
    assert z[-4:]!=FDT, 'zImage already has appended dtb'
    assert dt[:4]==FDT, 'dtb magic missing'
    assert rf[:4]==MAGIC and rf[8:16].startswith(b'ROOTFS'), 'rootfs not MTK ROOTFS blob'
    kernel=z+dt; gz=rf[512:]
    hdr=bytearray(t[:PAGE])
    struct.pack_into('<I',hdr,8,len(kernel)+512)
    struct.pack_into('<I',hdr,16,len(rf))
    hdr[576:584]=hashlib.sha1(kernel+rf).digest()[:8]
    kh=bytearray(t[PAGE:PAGE+512]); assert kh[:4]==MAGIC and kh[8:14]==b'KERNEL'
    struct.pack_into('<I',kh,4,len(kernel))
    rh=bytearray(rf[:512]); struct.pack_into('<I',rh,4,len(gz))
    o=bytes(hdr)+bytes(kh)+kernel
    o+=b'\0'*((PAGE-len(o)%PAGE)%PAGE)
    o+=bytes(rh)+gz
    o+=b'\0'*((PAGE-len(o)%PAGE)%PAGE)
    open(out,'wb').write(o)
    print('built %s: %d bytes (limit 6291456)'%(out,len(o)))
    if len(o)>6291456: sys.exit('ERROR: exceeds 6 MiB boot partition')

if __name__=='__main__':
    if sys.argv[1]=='split': split(sys.argv[2],sys.argv[3])
    elif sys.argv[1]=='build': build(*sys.argv[2:7])
    else: sys.exit(__doc__)
