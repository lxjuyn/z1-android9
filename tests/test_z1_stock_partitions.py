from pathlib import Path
import subprocess,tempfile,struct,json,time,threading
source=Path('tools/z1_android9_bootdiag.c').resolve()
with tempfile.TemporaryDirectory(prefix='stock-partitions-host-') as d:
 root=Path(d);dev=root/'dev';dev.mkdir();sys=root/'sys';sys.mkdir();table=root/'dumchar'
 for i,start in [(4,4096),(5,8192),(6,12288)]:
  node=f'mmcblk0p{i}';p=sys/node;p.mkdir();(p/'dev').write_text(f'179:{i}\n');(p/'size').write_text('2048\n');(p/'start').write_text(f'{start}\n')
  sb=bytearray(2048);sb[1080:1082]=b'\x53\xef';struct.pack_into('<I',sb,1028,512);struct.pack_into('<I',sb,1048,0);(dev/node).write_bytes(sb)
 h=root/'test.c';h.write_text('''#define _GNU_SOURCE
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <string.h>
#include <stdlib.h>
static int host_lstat(const char *path, struct stat *s) {
 int result=lstat(path,s); const char *p=strrchr(path,'/');
 if(!result && p && !strncmp(p+1,"mmcblk0p",8) && S_ISREG(s->st_mode)) { s->st_mode=(s->st_mode&0777)|S_IFBLK; s->st_rdev=makedev(179,atoi(p+9)); }
 return result;
}
#define lstat host_lstat
#define STOCK_DUMCHAR_INFO "'''+str(table)+'''"
#define STOCK_BLOCK_SYSFS "'''+str(sys)+'''"
#define STOCK_BLOCK_DEV "'''+str(dev)+'''"
#define main bootdiag_main
#include "'''+str(source)+'''"
#undef main
int main(int argc, char **argv) { return argc > 1 && !strcmp(argv[1],"retry") ? stock_partitions_retry() : stock_partitions_worker(); }
''')
 subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror',str(h),'-o',str(root/'test')],check=True,timeout=60)
 good='Part_Name\tSize\tStartAddr\tType\tMapTo\nandroid 0x100000 0x200000 2 /dev/block/mmcblk0p4\ncache 0x100000 0x400000 2 /dev/block/mmcblk0p5\nusrdata 0x100000 0x600000 2 /dev/block/mmcblk0p6\nPart_Name:Partition name you should open;\n'
 results=[]
 def run(name,text,expected):
  for p in dev.glob('z1-stock-*'):p.unlink()
  table.write_text(text)
  r=subprocess.run([str(root/'test')],capture_output=True,text=True,timeout=5)
  assert (r.returncode==0)==expected,(name,r.returncode,r.stderr)
  aliases=list(dev.glob('z1-stock-*'));assert len(aliases)==(3 if expected else 0),(name,aliases)
  if expected:
   for alias,i in [('system',4),('cache',5),('data',6)]:assert (dev/f'z1-stock-{alias}').readlink()==dev/f'mmcblk0p{i}'
  results.append({'case':name,'result':'PASS','exit':r.returncode})
 run('valid-five-column',good,True)
 run('duplicate-android',good+'android 0x100000 0x200000 2 /dev/block/mmcblk0p4\n',False)
 run('wrong-type',good.replace('android 0x100000 0x200000 2','android 0x100000 0x200000 1'),False)
 run('zero-partition',good.replace('mmcblk0p4','mmcblk0p0'),False)
 run('path-traversal',good.replace('mmcblk0p4','mmcblk0p4/../mmcblk0p5'),False)
 run('wrong-start',good.replace('android 0x100000 0x200000','android 0x100000 0x200200'),False)
 run('wrong-system-size',good.replace('android 0x100000','android 0x200000'),False)
 run('userdata-EOD-truncation-fits',good.replace('usrdata 0x100000','usrdata 0x200000'),True)
 run('userdata-advertised-too-small',good.replace('usrdata 0x100000','usrdata 0x80000'),False)
 sb=bytearray((dev/'mmcblk0p6').read_bytes());struct.pack_into('<I',sb,1028,2048);(dev/'mmcblk0p6').write_bytes(sb)
 run('userdata-filesystem-too-large',good,False)
 struct.pack_into('<I',sb,1028,512);(dev/'mmcblk0p6').write_bytes(sb)
 run('wrong-header',good.replace('StartAddr','Start'),False)
 run('missing-cache','\n'.join(s for s in good.splitlines() if not s.startswith('cache '))+'\n',False)
 run('scan-limit',good+' '*65537,False)
 run('two-names-one-device',good.replace('usrdata 0x100000 0x600000 2 /dev/block/mmcblk0p6','usrdata 0x100000 0x200000 2 /dev/block/mmcblk0p4'),False)
 # Reproduce late MMC sysfs registration without touching real devices.
 for p in dev.glob('z1-stock-*'):p.unlink()
 table.write_text(good)
 held=root/'late-cache';(sys/'mmcblk0p5').rename(held)
 original=subprocess.run([str(root/'test')],capture_output=True,text=True,timeout=2)
 assert original.returncode!=0 and 'errno=2' in original.stderr and not list(dev.glob('z1-stock-*')),original.stderr
 results.append({'case':'one-shot-worker-reproduces-registration-ENOENT','result':'PASS','exit':original.returncode})
 timer=threading.Timer(.25,lambda:held.rename(sys/'mmcblk0p5'));timer.start()
 started=time.monotonic()
 try:r=subprocess.run([str(root/'test'),'retry'],capture_output=True,text=True,timeout=5)
 finally:timer.join()
 elapsed=time.monotonic()-started
 assert r.returncode==0,(r.returncode,r.stderr)
 assert .15 <= elapsed < 4.5,elapsed
 assert 'bounded retry' in r.stderr and len(list(dev.glob('z1-stock-*')))==3,r.stderr
 results.append({'case':'delayed-MMC-registration-retries-and-succeeds','result':'PASS','elapsed_s':elapsed})
 for badname,text in [('invalid-type-never-retried',good.replace('android 0x100000 0x200000 2','android 0x100000 0x200000 1')),('wrong-geometry-never-retried',good.replace('android 0x100000 0x200000','android 0x100000 0x200200')),('missing-schema-entry-never-retried','\n'.join(x for x in good.splitlines() if not x.startswith('cache '))+'\n')]:
  for p in dev.glob('z1-stock-*'):p.unlink()
  table.write_text(text);started=time.monotonic()
  r=subprocess.run([str(root/'test'),'retry'],capture_output=True,text=True,timeout=2)
  elapsed=time.monotonic()-started
  assert r.returncode!=0 and 'bounded retry' not in r.stderr and not list(dev.glob('z1-stock-*')),(badname,r.stderr)
  assert elapsed<1,elapsed
  results.append({'case':badname,'result':'PASS','elapsed_s':elapsed})
 print(json.dumps({'status':'PASS','checks':results},indent=2))
