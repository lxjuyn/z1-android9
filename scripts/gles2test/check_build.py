#!/usr/bin/env python3
"""Read-only ELF/host evidence checks. Never executes an ARM target binary."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("build",type=Path)
parser.add_argument("--host",action="store_true")
parser.add_argument("--archive",type=Path)
args=parser.parse_args()
build=args.build
binary=build/"gles2probe_android28_armv7"
data=binary.read_bytes()
assert data[:6]==b"\x7fELF\x01\x01", "not little-endian ELF32"
etype,machine=struct.unpack_from("<HH",data,16)
assert (etype,machine)==(3,40), "not ARM PIE"
flags=struct.unpack_from("<I",data,36)[0]
assert flags & 0xff000000 == 0x05000000, "not EABI5"
assert flags & 0x200 and not flags & 0x400, "not Android softfp ABI"
elf=(build/"target_elf.txt").read_text()
symbols=(build/"target_symbols.txt").read_text()
assert "Requesting program interpreter: /system/bin/linker" in elf, "wrong libc loader"
assert "musl" not in elf.lower(), "musl binary rejected"
needed=set(re.findall(r"\(NEEDED\).*?\[(.*?)\]",elf))
assert {"libc.so","libEGL.so","libGLESv2.so"}<=needed, needed
assert needed <= {"libc.so","libm.so","libdl.so","libEGL.so","libGLESv2.so"}, needed
for forbidden in ("eglSwapBuffers", "eglCreateWindowSurface", "eglCreatePixmapSurface", "drmSetMaster", "ioctl"):
    assert forbidden not in symbols, forbidden
for symbol in ("eglCreatePbufferSurface","glCompileShader","glLinkProgram","glReadPixels","eglGetDisplay"):
    assert symbol in symbols, symbol
# Parse Android identification note, instead of assuming the clang prefix suffices.
phoff=struct.unpack_from("<I",data,28)[0]
phentsize,phnum=struct.unpack_from("<HH",data,42)
android_api=None
for index in range(phnum):
    ptype,offset,_,_,filesize,_,_,_=struct.unpack_from("<8I",data,phoff+index*phentsize)
    if ptype!=4: continue
    end=offset+filesize
    while offset+12<=end:
        namesz,descsz,ntype=struct.unpack_from("<3I",data,offset)
        offset+=12
        name=data[offset:offset+namesz].rstrip(b"\0");offset+=(namesz+3)&~3
        desc=data[offset:offset+descsz];offset+=(descsz+3)&~3
        if name==b"Android" and ntype==1:
            android_api=struct.unpack_from("<I",desc)[0]
assert android_api==28, f"API note mismatch: {android_api}"
status={"target_build":"PASS","target":"Android API28 ARMv7 softfp PIE bionic",
        "target_sha256":hashlib.sha256(data).hexdigest(),"target_bytes":len(data),
        "needed":sorted(needed),"board_status":"NOT_RUN","hardware_status":"UNPROVEN",
        "on_device_execution":False,"installation":False,"firmware_changes":False}
if args.host:
    report=json.loads((build/"host_analysis.json").read_text())
    assert report["pixel_status"]=="PASS", report["mismatched_pixels"]
    assert report["files_checked"]==60 and report["pixels_checked"]==1336350
    assert report["board_status"]=="NOT_RUN" and report["hardware_status"]=="UNPROVEN"
    assert any("llvmpipe" in line.lower() for line in report["identity_lines"]), "host software renderer not identified"
    host=str(build/"gles2probe_host")
    cases=[["--frames","0"],["--frames","17"],["--frames","garbage"],
           ["--width","0","--height","64"],["--width","513","--height","64"],
           ["--width","64"],["--height","64"],["--frames","-1"],
           ["--width","64","--height","no"],["--no-such-option","1"]]
    with tempfile.TemporaryDirectory(dir=build,prefix="cli_test_") as tmp:
        out=Path(tmp)/"must_not_exist"
        for options in cases:
            run=subprocess.run([host,"--out",str(out),*options],capture_output=True,timeout=5)
            assert run.returncode==2, (options,run.returncode)
            assert not out.exists(), "invalid arguments created outputs"
        # Existing directories must not be overwritten, even on a second run.
        out.mkdir();(out/"sentinel").write_text("keep")
        run=subprocess.run([host,"--out",str(out)],capture_output=True,timeout=5)
        assert run.returncode==2 and (out/"sentinel").read_text()=="keep"
        assert list(out.iterdir())==[out/"sentinel"]
    status.update(host_end_to_end="PASS (llvmpipe software only)",host_files_checked=60,
                  host_pixels_checked=1336350,host_mismatched_pixels=0,cli_negative_cases=11)
    (build/"status.json").write_text(json.dumps(status,indent=2)+"\n")
if args.archive:
    archived=(args.archive/binary.name).read_bytes()
    assert archived==data, "archive target differs"
    saved=json.loads((build/'status.json').read_text())
    assert saved['target_sha256']==status['target_sha256'], 'status binary mismatch'
    status.update(saved)
    manifest={}
    for path in sorted(args.archive.rglob('*')):
        relative=path.relative_to(args.archive).as_posix()
        assert not path.is_symlink(), f'unexpected archive symlink: {relative}'
        if path.is_file() and relative!='archive_sha256.json':
            manifest[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path=args.archive/'archive_sha256.json'
    manifest_path.write_text(json.dumps(manifest,indent=2)+chr(10))
    for relative,digest in json.loads(manifest_path.read_text()).items():
        assert hashlib.sha256((args.archive/relative).read_bytes()).hexdigest()==digest, relative
    status['archive_files_verified']=len(manifest)
    status["archive"]=str(args.archive)
    status["archive_target_readback"]="SHA256_MATCH"
print(json.dumps(status,indent=2))
