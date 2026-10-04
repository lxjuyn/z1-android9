#!/usr/bin/env python3
"""Compile staged patched Lima files with existing Android ARM object rules.

Redirects objects/dependencies into the stage; does not rebuild the image.
"""
from pathlib import Path
import argparse,re,subprocess,shutil
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--los',type=Path,required=True)
ap.add_argument('--stage',type=Path,required=True)
args=ap.parse_args();los=args.los.resolve();root=args.stage.resolve()
folder=root/'src/gallium/drivers/lima'
shutil.copy2(Path(__file__).resolve().parent/'lima_syncobj_compat.h',folder/'lima_syncobj_compat.h')
ninja=los/'out/build-lineage_z1.ninja'
rules={}; current=''; wanted={}
with ninja.open() as f:
 for line in f:
  if line.startswith('rule '):current=line.split()[1]
  elif line.startswith(' command = '):rules[current]=line[len(' command = '):].rstrip()
  elif line.startswith('build ') and 'libmesa_pipe_lima_intermediates/lima_' in line and '.o: ' in line:
   m=re.match(r'build (.+?): (\S+) (\S+)',line)
   if m and Path(m[3]).name in ['lima_draw.c','lima_submit.c','lima_fence.c','lima_screen.c']:
    wanted[Path(m[3]).name]=(m[1],m[2],m[3])
if len(wanted)!=4:raise ValueError('missing Android Lima object rules')
for name,(obj,rule,src) in wanted.items():
 cmd=rules[rule].replace('$$','$')
 # Keep identical Android/ARM defines, but redirect source, output and depfile.
 cmd=cmd.replace(src,str(folder/name)).replace(obj,str(root/(name+'.o'))).replace(obj[:-2]+'.d',str(root/(name+'.d')))
 cmd=cmd.replace(' -I .cache/z1-mesa-lima/mesa-19.1.8/src/gallium/include',' -include '+str(folder/'lima_context.h')+' -I .cache/z1-mesa-lima/mesa-19.1.8/src/gallium/include',1)
 p=subprocess.run(cmd,shell=True,cwd=los,text=True,capture_output=True,timeout=60)
 print(name,'EXIT',p.returncode);print(p.stderr[-4500:])
 if p.returncode:raise SystemExit(p.returncode)
print('PASS real Android ARM commands: lima_draw.c, lima_submit.c, lima_fence.c, lima_screen.c; .cache unchanged')
