#!/usr/bin/env python3
"""Host fault tests for the actual staging shell functions; never opens a device."""
import os
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest

SOURCE = Path(__file__).with_name('z1stage_boot.sh').read_text()
FS_FUNCS = SOURCE[SOURCE.index('mount_record()'):SOURCE.index('# --storage-only belongs')]
INSTALL_FUNCS = SOURCE[SOURCE.index('SYSTEM_WRITE_FAILED=0'):SOURCE.index('# Best-effort RO restoration')]
MOCK = r'''#!/usr/bin/env python3
import os, pathlib, shutil, subprocess, sys, json
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['TRACE'],'a') as f: f.write(name+' '+' '.join(args)+'\n')
if os.environ.get('FAIL') == name: sys.exit(8)
p=pathlib.Path(os.environ['PROC_MOUNTS'])
if name=='readlink': print(args[-1]); sys.exit(0)
if name=='setprop':
    props=pathlib.Path(os.environ['PROPS']); values=json.loads(props.read_text()) if props.exists() else {}
    values[args[0]]=args[1];props.write_text(json.dumps(values));sys.exit(0)
if name=='getprop':
    props=pathlib.Path(os.environ['PROPS']); values=json.loads(props.read_text()) if props.exists() else {}
    print(values.get(args[0],os.environ.get('READY','0')));sys.exit(0)
if name=='mkdir': pathlib.Path(args[-1]).mkdir(parents=True,exist_ok=True); sys.exit(0)
if name=='dmesg': print(os.environ.get('DMESG_TEXT','')); sys.exit(0)
if name=='e2fsck': print('mock fsck result'); sys.exit(int(os.environ.get('FSCK_RC','0')))
if name=='umount':
    p.write_text(''.join(l for l in p.read_text().splitlines(True) if l.split()[1]!=args[-1])); sys.exit(0)
if name=='mount':
    if args[-1]=='/system':
        if os.environ.get('FAIL_RO')=='1' and args[1]=='remount,ro': sys.exit(8)
        mode='rw' if args[1]=='remount,rw' else 'ro'
        p.write_text('\n'.join((f'/dev/system /system ext4 {mode} 0 0' if l.split()[1]=='/system' else l) for l in p.read_text().splitlines())+'\n')
    else:
        with p.open('a') as f: f.write(f'{args[-2]} {args[-1]} ext4 rw 0 0\n')
    sys.exit(0)
if name=='cp':
    shutil.copyfile(args[0],args[1])
    if os.environ.get('CORRUPT_COPY')=='1': pathlib.Path(args[1]).write_bytes(b'bad')
    sys.exit(0)
if name=='mv': os.replace(args[-2],args[-1]); sys.exit(0)
if name=='rm':
    pathlib.Path(args[-1]).unlink(missing_ok=True); sys.exit(0)
if name in ('chmod','chown'): sys.exit(0)
sys.exit(99)
'''

class StageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        self.mounts = self.p/'mounts'
        self.mounts.write_text('/dev/data /data ext4 rw 0 0\n/dev/system /system ext4 ro 0 0\n')
        self.trace = self.p/'trace'; self.trace.touch()
        for name in ['mount','umount','e2fsck','readlink','dmesg','cp','mv','rm','chmod','chown','setprop','getprop','mkdir']:
            f=self.p/name; f.write_text(MOCK); f.chmod(0o755)
        self.env=dict(os.environ, TRACE=str(self.trace), PROC_MOUNTS=str(self.mounts),
                      PROPS=str(self.p/'properties'), FSCK_RC='0', SB='61267 3', FAIL='', CORRUPT_COPY='', FAIL_RO='')
        self.prefix = '\n'.join(f'{var}={self.p/name}' for var,name in [('MOUNT','mount'),('UMOUNT','umount'),('FSCK','e2fsck'),('READLINK','readlink'),('DMESG','dmesg'),('CP','cp'),('MV','mv'),('RM','rm'),('CHMOD','chmod'),('CHOWN','chown')]) + r'''
CMP=/usr/bin/cmp
ERRORS=0
APPLIED=0
REMOVED=0
log() { printf 'LOG %s\n' "$*" >> "$TRACE"; }
set_prop() { printf 'PROP %s\n' "$*" >> "$TRACE"; }
sha_of() { sha256sum "$1" | cut -d' ' -f1; }
sb_state() { printf '%s\n' "$SB"; }
'''
    def tearDown(self): self.tmp.cleanup()
    def run_shell(self, body, funcs=FS_FUNCS):
        r=subprocess.run(['/bin/dash','-c',self.prefix+'\n'+funcs+'\n'+body],env=self.env,text=True,capture_output=True)
        self.assertEqual(r.stderr,'',r.stderr)
        return r.returncode,self.trace.read_text()
    def test_fsck_return_codes(self):
        for code in [0,1,2,3,4,5,8,16,32,128]:
            with self.subTest(code=code):
                self.mounts.write_text('/dev/data /data ext4 rw 0 0\n')
                self.trace.write_text(''); self.env['FSCK_RC']=str(code)
                rc,t=self.run_shell('prepare_fs /data /dev/data')
                self.assertEqual(rc,0 if code in (0,1) else 2 if code in (2,3) else 1)
                self.assertEqual('mount -o rw,' in t,code in (0,1),t)
                if code in (2,3): self.assertIn('PROP z1stage.fsck reboot-required',t)
    def test_busy_unmount_does_not_fsck_or_mount(self):
        self.env['FAIL']='umount';rc,t=self.run_shell('prepare_fs /data /dev/data')
        self.assertNotEqual(rc,0); self.assertNotIn('e2fsck ',t);self.assertNotIn('mount -o ',t)
    def test_device_alias_still_mounted_does_not_fsck(self):
        with self.mounts.open('a') as f:f.write('/dev/data /alias ext4 rw 0 0\n')
        rc,t=self.run_shell('prepare_fs /data /dev/data')
        self.assertNotEqual(rc,0);self.assertNotIn('e2fsck ',t)
    def test_wrong_mount_blocks(self):
        self.mounts.write_text('/dev/other /data ext4 rw 0 0\n')
        rc,t=self.run_shell('prepare_fs /data /dev/data')
        self.assertNotEqual(rc,0);self.assertNotIn('umount ',t)
    def test_invalid_superblock_blocks(self):
        self.env['SB']='';rc,t=self.run_shell('prepare_fs /data /dev/data')
        self.assertNotEqual(rc,0);self.assertNotIn('e2fsck ',t)
    def test_unreadable_dmesg_is_not_clean(self):
        self.env.update(SB='61267 1',FAIL='dmesg')
        rc,t=self.run_shell('prepare_fs /data /dev/data');self.assertNotEqual(rc,0)
        self.assertNotIn('e2fsck ',t)
    def test_clean_mounted_has_no_fsck_or_mount(self):
        self.env['SB']='61267 1';rc,t=self.run_shell('prepare_fs /data /dev/data')
        self.assertEqual(rc,0);self.assertNotIn('e2fsck ',t);self.assertNotIn('mount -o ',t)
    def test_journal_replay_repairs(self):
        self.env.update(SB='61267 1',DMESG_TEXT='EXT4-fs (data): recovery complete')
        rc,t=self.run_shell('prepare_fs /data /dev/data');self.assertEqual(rc,0);self.assertIn('e2fsck ',t)
    def test_unmounted_clean_fs_checked_before_mount(self):
        self.mounts.write_text('');self.env['SB']='61267 1'
        rc,t=self.run_shell('prepare_fs /data /dev/data');self.assertEqual(rc,0);self.assertIn('e2fsck ',t)
    def test_readonly_clean_mount_blocks(self):
        self.mounts.write_text('/dev/data /data ext4 ro 0 0\n');self.env['SB']='61267 1'
        rc,t=self.run_shell('prepare_fs /data /dev/data');self.assertNotEqual(rc,0);self.assertNotIn('mount -o ',t)
    def test_mount_failure_blocks(self):
        self.env['FAIL']='mount';rc,t=self.run_shell('prepare_fs /data /dev/data');self.assertNotEqual(rc,0)
    def install(self, same=False, extra=''):
        src=self.p/'source';dst=self.p/'target';src.write_bytes(b'candidate');dst.write_bytes(b'candidate' if same else b'baseline')
        rc,t=self.run_shell(f'atomic_install {src} {dst} test; result=$?; restore_system_ro; restore=$?; {extra}\n[ "$result" -eq 0 ] && [ "$restore" -eq 0 ]',FS_FUNCS+INSTALL_FUNCS)
        return rc,t,dst
    def test_identical_has_no_write(self):
        rc,t,dst=self.install(same=True);self.assertEqual(rc,0)
        self.assertNotIn('cp ',t);self.assertNotIn('mv ',t);self.assertNotIn('mount ',t)
        self.assertEqual(dst.read_bytes(),b'candidate')
    def test_changed_verified_and_restored_ro(self):
        rc,t,dst=self.install();self.assertEqual(rc,0);self.assertEqual(dst.read_bytes(),b'candidate')
        self.assertIn('mount -o remount,rw',t);self.assertIn('mount -o remount,ro',t)
        self.assertIn('/system ext4 ro',self.mounts.read_text())
    def test_copy_corruption_preserves_destination(self):
        self.env['CORRUPT_COPY']='1';rc,t,dst=self.install();self.assertNotEqual(rc,0)
        self.assertEqual(dst.read_bytes(),b'baseline');self.assertNotIn('mv ',t)
        self.assertIn('/system ext4 ro',self.mounts.read_text())
    def test_failed_metadata_or_rename_preserves_destination(self):
        for name in ('chmod','chown','mv','cp'):
            with self.subTest(name=name):
                self.env['FAIL']=name;self.trace.write_text('')
                rc,t,dst=self.install();self.assertNotEqual(rc,0);self.assertEqual(dst.read_bytes(),b'baseline')
                self.assertIn('/system ext4 ro',self.mounts.read_text())
    def test_failed_rw_never_copies(self):
        self.env['FAIL']='mount';rc,t,dst=self.install();self.assertNotEqual(rc,0)
        self.assertNotIn('cp ',t);self.assertEqual(dst.read_bytes(),b'baseline')
    def test_failed_ro_is_reported(self):
        self.env['FAIL_RO']='1';rc,t,dst=self.install();self.assertNotEqual(rc,0)
        self.assertIn('RO remount failed',t)
    def test_compare_error_does_not_copy(self):
        dst=self.p/'target';dst.write_bytes(b'baseline')
        rc,t=self.run_shell(f'atomic_install {self.p}/missing {dst} test',FS_FUNCS+INSTALL_FUNCS)
        self.assertNotEqual(rc,0);self.assertNotIn('cp ',t);self.assertIn('cannot compare',t)
    def test_failure_stops_next_install(self):
        self.env['CORRUPT_COPY']='1'
        src=self.p/'source';dst=self.p/'target';dst2=self.p/'target2';src.write_bytes(b'new');dst.write_bytes(b'old');dst2.write_bytes(b'old2')
        rc,t=self.run_shell(f'atomic_install {src} {dst} first; atomic_install {src} {dst2} second',FS_FUNCS+INSTALL_FUNCS)
        self.assertNotEqual(rc,0);self.assertEqual(t.count('cp '),1);self.assertEqual(dst2.read_bytes(),b'old2')

    def full_storage_run(self, bad_cache=False, mode='--storage-only'):
        data=self.p/'data-dev';cache=self.p/'cache-dev'
        for dev in (data,cache):
            body=bytearray(1084);struct.pack_into('<HH',body,1080,61267,1);dev.write_bytes(body)
        if bad_cache:cache.write_bytes(b'')
        self.mounts.write_text(f'{data} /data ext4 rw 0 0\n{cache} /cache ext4 rw 0 0\n/dev/system /system ext4 ro 0 0\n')
        script=SOURCE.replace('PROC_MOUNTS=/proc/mounts',f'PROC_MOUNTS={self.mounts}')
        script=script.replace('STAGE=/data/local/tmp/z1stage',f'STAGE={self.p}/stage')
        script=script.replace('/dev/block/mmcblk0p6',str(data)).replace('/dev/block/mmcblk0p5',str(cache))
        script=script.replace('/dev/kmsg',str(self.trace))
        for name in ('mount','umount','e2fsck','readlink','dmesg','setprop','getprop','mkdir','cp','mv','rm','chmod','chown'):
            script=script.replace('/system/bin/'+name,str(self.p/name))
        r=subprocess.run(['/bin/dash','-c',script,'z1stage-test',mode],env=self.env,text=True,capture_output=True)
        self.assertEqual(r.stderr,'')
        return r.returncode,self.trace.read_text()
    def test_storage_only_never_creates_stage(self):
        rc,t=self.full_storage_run();self.assertEqual(rc,0);self.assertFalse((self.p/'stage').exists())
        self.assertIn('setprop sys.z1.storage_ready 0',t);self.assertIn('setprop sys.z1.storage_ready 1',t)
        self.assertNotIn('cp ',t);self.assertNotIn('mkdir ',t)
    def test_failed_cache_cannot_release_gate(self):
        rc,t=self.full_storage_run(bad_cache=True);self.assertNotEqual(rc,0)
        self.assertNotIn('setprop sys.z1.storage_ready 1',t);self.assertNotIn('mkdir ',t)
        self.assertFalse((self.p/'stage').exists())
    def test_skip_storage_requires_early_gate(self):
        rc,t=self.full_storage_run(mode='--skip-storage');self.assertNotEqual(rc,0)
        self.assertNotIn('mkdir ',t);self.assertNotIn('e2fsck ',t)
    def test_kill_switch_removes_only_override(self):
        stage=self.p/'stage';(stage/'active').mkdir(parents=True);(stage/'.disable_hwcomposer').touch()
        vendor=self.p/'vendor';vendor.mkdir();dst=vendor/'hwcomposer.z1.so';dst.write_bytes(b'old')
        rc,t=self.run_shell(f'STAGE={stage}; VENDOR_HW={vendor}; install_one hwcomposer.z1.so hwcomposer; restore_system_ro',FS_FUNCS+INSTALL_FUNCS)
        self.assertEqual(rc,0);self.assertFalse(dst.exists());self.assertNotIn('cp ',t)
    def test_active_override_wins(self):
        stage=self.p/'stage';(stage/'active').mkdir(parents=True);(stage/'active/hwcomposer.z1.so').write_bytes(b'active')
        vendor=self.p/'vendor';vendor.mkdir();dst=vendor/'hwcomposer.z1.so';dst.write_bytes(b'old')
        rc,t=self.run_shell(f'STAGE={stage}; VENDOR_HW={vendor}; install_one hwcomposer.z1.so hwcomposer; restore_system_ro',FS_FUNCS+INSTALL_FUNCS)
        self.assertEqual(rc,0);self.assertEqual(dst.read_bytes(),b'active')
    def frequency_setup(self):
        stage=self.p/'stage';stage.mkdir();(stage/'oc_level').write_text('oc\n')
        cpu=self.p/'cpu';cpu.mkdir();gpu=self.p/'gpu';gpu.mkdir()
        for name,value in {'scaling_available_frequencies':'598000 806000 1001000 1209000 1300000',
                'scaling_available_governors':'ondemand schedutil', 'scaling_driver':'mediatek-cpufreq',
                'scaling_max_freq':'1300000','scaling_min_freq':'598000','scaling_governor':'ondemand','scaling_cur_freq':'806000'}.items():
            (cpu/name).write_text(value+'\n')
        for name,value in {'available_frequencies':'26000000 416000000','available_governors':'simple_ondemand performance',
                'governor':'simple_ondemand','cur_freq':'416000000','max_freq':'416000000'}.items():
            (gpu/name).write_text(value+'\n')
        section=SOURCE[SOURCE.index('# --- 2c. CPU / GPU frequency policy'):SOURCE.index('# --- 2b2. dma-heap device node')]
        section=section.replace('/sys/devices/system/cpu/cpu0/cpufreq',str(cpu)).replace('/sys/class/devfreq/13010000.gpu',str(gpu))
        return f'STAGE={stage}\n'+section,cpu,gpu,stage
    def test_supported_frequency_policy_readback(self):
        section,cpu,gpu,stage=self.frequency_setup();rc,t=self.run_shell(section,funcs='')
        self.assertEqual(rc,0);self.assertIn('unsupported oc_level=oc',t)
        self.assertEqual((stage/'oc_level').read_text(),'safe\n')
        self.assertEqual((cpu/'scaling_min_freq').read_text(),'806000\n')
        self.assertEqual((cpu/'scaling_max_freq').read_text(),'1300000\n')
        self.assertEqual((gpu/'max_freq').read_text(),'416000000\n')
        self.assertIn('PROP z1stage.cpufreq ready',t);self.assertIn('PROP z1stage.devfreq ready',t)
    def test_unsupported_frequency_does_not_write(self):
        section,cpu,gpu,stage=self.frequency_setup();(cpu/'scaling_available_frequencies').write_text('598000\n')
        (gpu/'available_frequencies').write_text('26000000\n')
        rc,t=self.run_shell(section,funcs='');self.assertEqual(rc,0)
        self.assertEqual((cpu/'scaling_min_freq').read_text(),'598000\n')
        self.assertEqual((gpu/'governor').read_text(),'simple_ondemand\n')
        self.assertIn('PROP z1stage.cpufreq unsupported',t);self.assertIn('PROP z1stage.devfreq unsupported',t)
    def test_gpu_clock_readback_failure_is_reported(self):
        section,cpu,gpu,stage=self.frequency_setup();(gpu/'cur_freq').write_text('26000000\n')
        rc,t=self.run_shell(section,funcs='');self.assertEqual(rc,0)
        self.assertIn('PROP z1stage.devfreq failed',t);self.assertNotIn('PROP z1stage.devfreq ready',t)

    def test_cpu_sysfs_write_failure_is_reported(self):
        section,cpu,gpu,stage=self.frequency_setup()
        (cpu/'scaling_max_freq').unlink();(cpu/'scaling_max_freq').mkdir()
        r=subprocess.run(['/bin/dash','-c',self.prefix+'\n'+section],env=self.env,text=True,capture_output=True)
        t=self.trace.read_text()
        self.assertIn('PROP z1stage.cpufreq failed',t)
        self.assertNotIn('PROP z1stage.cpufreq ready',t)
        self.assertEqual((cpu/'scaling_min_freq').read_text(),'598000\n')

    def test_journal_replay_does_not_depend_on_grep(self):
        self.env.update(SB='61267 1',DMESG_TEXT='EXT4-fs (data): recovery complete')
        rc,t=self.run_shell('grep() { return 2; }; prepare_fs /data /dev/data')
        self.assertEqual(rc,0);self.assertIn('e2fsck ',t)
    def test_seed_metadata_failure_is_counted(self):
        stage=self.p/'stage';stage.mkdir();source=self.p/'hwcomposer.z1.so';source.write_bytes(b'new')
        functions=SOURCE[SOURCE.index('seed()'):SOURCE.index('log "begin @')]
        functions=functions.replace('_src="/$_name"',f'_src="{self.p}/$_name"')
        self.env['FAIL']='chmod'
        rc,t=self.run_shell(f'STAGE={stage}; SHELL_UID=2000; SHELL_GID=2000; SEEDED=0; COPIED=0; seed hwcomposer.z1.so 0644; [ "$ERRORS" -eq 2 ] && [ "$SEEDED" -eq 0 ] && [ "$COPIED" -eq 0 ]',funcs=functions)
        self.assertEqual(rc,0);self.assertNotIn('LOG seeded ',t)

    def test_gate_property_write_failure_blocks(self):
        self.env['FAIL']='setprop';rc,t=self.full_storage_run()
        self.assertNotEqual(rc,0);self.assertNotIn('mkdir ',t);self.assertNotIn('storage gate ready',t)
    def test_gate_property_readback_failure_blocks(self):
        self.env['FAIL']='getprop';rc,t=self.full_storage_run()
        self.assertNotEqual(rc,0);self.assertNotIn('mkdir ',t);self.assertNotIn('storage gate ready',t)

if __name__=='__main__': unittest.main(verbosity=2)
