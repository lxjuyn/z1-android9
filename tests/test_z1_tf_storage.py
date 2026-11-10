import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DETECT = ROOT / 'scripts/storage/z1_tf_detect.sh'
CHECK = ROOT / 'scripts/storage/z1_tf_check_prerequisites.py'
EXAMPLE = ROOT / 'scripts/storage/z1_tf_prerequisites.example.json'
KEYS = ('tf_slot', 'host_mapping', 'pinmux', 'card_detect', 'vmmc',
        'vqmmc', 'kernel_support', 'dts', 'fstab', 'vold')


class FixtureBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='z1-tf-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def snapshot(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}


class TFDetectorTests(FixtureBase):
    def setUp(self):
        super().setUp()
        self.write('proc/mounts', '')
        (self.root / 'sys/class/mmc_host').mkdir(parents=True)

    def host(self, host):
        path = self.root / 'sys/class/mmc_host' / host
        path.mkdir(exist_ok=True)
        return path

    def card(self, host='mmc7', node='mmcblk9', kind='SD', removable='1', devid='179:8'):
        base = 'sys/class/mmc_host/' + host + '/' + host + ':0001'
        self.host(host)
        self.write(base + '/type', kind + '\n')
        block = base + '/block/' + node
        self.write(block + '/removable', removable + '\n')
        self.write(block + '/dev', devid + '\n')
        return block

    def partition(self, block, node, devid='179:9'):
        self.write(block + '/' + node + '/partition', '1\n')
        self.write(block + '/' + node + '/dev', devid + '\n')

    def run_detect(self, *args):
        before = self.snapshot()
        proc = subprocess.run(['/bin/sh', str(DETECT), '--root', str(self.root), *args],
                              text=True, capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, '')
        self.assertEqual(before, self.snapshot(), 'Detector changed fixture files')
        return proc.stdout

    def test_emmc_identity_does_not_depend_on_number_one(self):
        self.card(host='mmc8', node='mmcblk1', kind='MMC', removable='0')
        output = self.run_detect()
        self.assertIn('block\tmmcblk1\tcard\tmmc8:0001\tidentity\temmc_candidate\tremovable\t0', output)
        self.assertIn('summary\tsd_card\tunknown', output)
        self.assertNotIn('\tkind\tsd_memory', output)

    def test_sd_can_be_mmcblk_zero_and_mmc1_is_not_required(self):
        self.card(host='mmc0', node='mmcblk0', kind='SD', removable='1')
        output = self.run_detect()
        self.assertIn('\tidentity\tsd_memory\tremovable\t1', output)
        self.assertIn('summary\tsd_card\tpresent', output)
        self.assertIn('summary\tmmc1_host\tunknown', output)
        self.assertIn('summary\ttf_slot_mapping\tunknown', output)

    def test_both_card_types_and_readonly_android_vold_mount(self):
        self.card('mmc0', 'mmcblk1', 'MMC', '0', '179:0')
        sd = self.card('mmc4', 'mmcblk0', 'SD', '1', '179:8')
        self.partition(sd, 'mmcblk0p1', '179:9')
        self.write('proc/mounts', '/dev/block/vold/public:179,9 /mnt/media_rw/CARD vfat ro,nosuid,nodev 0 0\n')
        output = self.run_detect()
        self.assertIn('summary\tcards_enumerated\t2', output)
        self.assertIn('mount\tmmcblk0p1\tcard\tmmc4:0001\ttarget\t/mnt/media_rw/CARD\tfstype\tvfat\taccess\tro', output)
        self.assertNotIn('mount\tmmcblk1', output)

    def test_readwrite_mount_and_escaped_space(self):
        sd = self.card()
        self.partition(sd, 'mmcblk9p1')
        self.write('proc/mounts', '/dev/block/mmcblk9p1 /media/My\\040Card exfat rw,nodev 0 0\n')
        self.assertIn('target\t/media/My Card\tfstype\texfat\taccess\trw', self.run_detect())

    def test_whole_disk_mount(self):
        self.card()
        self.write('proc/mounts', '/dev/mmcblk9 /media/card vfat ro 0 0\n')
        self.assertIn('mount\tmmcblk9\t', self.run_detect())

    def test_mount_flags_use_exact_tokens(self):
        self.card()
        self.write('proc/mounts', '/dev/mmcblk9 /media/card vfat errors=remount-ro,rw 0 0\n')
        self.assertIn('\taccess\trw\t', self.run_detect())
        self.write('proc/mounts', '/dev/mmcblk9 /media/card vfat ro,rw 0 0\n')
        self.assertIn('\taccess\tunknown\t', self.run_detect())

    def test_empty_host_is_not_proof_of_physical_card_absence(self):
        self.host('mmc1')
        output = self.run_detect()
        self.assertIn('host\tmmc1\tcard_state\tno_card_enumerated', output)
        self.assertIn('summary\tmmc1_host\tpresent', output)
        for key in ('sd_card', 'card_detect', 'power', 'physical_card_absence'):
            self.assertIn('summary\t' + key + '\tunknown', output)

    def test_missing_host_and_mounts_are_unknown(self):
        (self.root / 'sys/class/mmc_host').rmdir()
        (self.root / 'proc/mounts').unlink()
        output = self.run_detect()
        self.assertIn('summary\tmounts\tunknown', output)
        self.assertIn('summary\thosts_enumerated\t0', output)
        self.assertIn('summary\tmmc1_host\tunknown', output)

    def test_sdio_is_not_a_memory_card(self):
        self.card(kind='SDIO', removable='1')
        output = self.run_detect()
        self.assertIn('kind\tsdio_not_sd_memory', output)
        self.assertIn('summary\tsd_card\tunknown', output)

    def test_missing_type_never_guesses_from_removable(self):
        self.card(kind='SD')
        (self.root / 'sys/class/mmc_host/mmc7/mmc7:0001/type').unlink()
        output = self.run_detect()
        self.assertIn('type\tunknown\tkind\tunknown', output)
        self.assertIn('summary\tsd_card\tunknown', output)

    def test_missing_removable_is_unknown_even_for_mmc(self):
        block = self.card(kind='MMC')
        (self.root / block / 'removable').unlink()
        output = self.run_detect()
        self.assertIn('identity\tmmc_memory\tremovable\tunknown', output)
        self.assertNotIn('emmc_candidate', output)

    def test_mount_name_prefix_never_matches_another_card(self):
        self.card(node='mmcblk1')
        self.write('proc/mounts', '/dev/block/mmcblk10 /wrong vfat ro 0 0\n/dev/block/dm-0 /indirect vfat rw 0 0\n')
        self.assertNotIn('\nmount\t', self.run_detect())

    def test_class_symlinks_inside_snapshot_work(self):
        self.card()
        source = self.root / 'sys/class/mmc_host/mmc7'
        real = self.root / 'sys/devices/controller/mmc_host/mmc7'
        real.parent.mkdir(parents=True)
        source.rename(real)
        source.symlink_to(os.path.relpath(real, source.parent))
        self.assertIn('summary\tsd_card\tpresent', self.run_detect())

    def test_escaping_symlink_is_not_read(self):
        with tempfile.TemporaryDirectory(prefix='z1-tf-outside-') as tmp:
            outside = Path(tmp)
            (outside / 'mmc1:0001').mkdir()
            (outside / 'mmc1:0001/type').write_text('SD\n')
            (self.root / 'sys/class/mmc_host/mmc1').symlink_to(outside)
            self.assertIn('summary\thosts_enumerated\t0', self.run_detect())

    def test_only_explicit_vold_snapshot_is_reported(self):
        self.write('captured/vold.state', 'init.svc.vold=running\nother.property=ignored\n')
        self.assertIn('vold\tcaptured_service_state\tunknown', self.run_detect())
        self.assertIn('vold\tcaptured_service_state\trunning', self.run_detect('--vold-state', 'captured/vold.state'))
        self.write('captured/vold.state', 'init.svc.vold=running\ninit.svc.vold=stopped\n')
        self.assertIn('vold\tcaptured_service_state\tunknown', self.run_detect('--vold-state', 'captured/vold.state'))

    def test_no_default_live_reads_and_bad_arguments_fail(self):
        for args in ([], ['--root'], ['--root', '/'], ['--root', str(self.root), '--vold-state', '../outside']):
            with self.subTest(args=args):
                proc = subprocess.run(['/bin/sh', str(DETECT), *args], text=True, capture_output=True, timeout=5)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)


    def test_enumerated_sd_without_block_is_reported_unknown(self):
        self.host('mmc5')
        self.write('sys/class/mmc_host/mmc5/mmc5:0001/type', 'SD\n')
        output = self.run_detect()
        self.assertIn('summary\tsd_card\tpresent', output)
        self.assertIn('card\tmmc5:0001\tblock_state\tunknown', output)
        self.assertNotIn('\nmount\t', output)

    def test_uncorrelated_mount_state_is_explicit_unknown(self):
        self.card()
        output = self.run_detect()
        self.assertIn('mount_state\tunknown\tnode\tmmcblk9\treason\tno_correlated_mount', output)
        (self.root / 'proc/mounts').unlink()
        self.assertIn('mount_state\tunknown\tnode\tmmcblk9\treason\tmounts_unavailable', self.run_detect())

    def test_special_files_not_opened_as_mounts_or_type(self):
        self.card()
        for name in ('proc/mounts', 'sys/class/mmc_host/mmc7/mmc7:0001/type'):
            path = self.root / name
            path.unlink()
            os.mkfifo(path)
        output = self.run_detect()
        self.assertIn('summary\tmounts\tunknown', output)
        self.assertIn('type\tunknown\tkind\tunknown', output)

    def test_duplicate_mounts_preserve_each_access_mode(self):
        self.card()
        self.write('proc/mounts', '/dev/mmcblk9 /first vfat rw 0 0\n/dev/mmcblk9 /second vfat ro 0 0\n')
        output = self.run_detect()
        self.assertIn('target\t/first\tfstype\tvfat\taccess\trw', output)
        self.assertIn('target\t/second\tfstype\tvfat\taccess\tro', output)

    @unittest.skipUnless(shutil.which('busybox'), 'busybox unavailable')
    def test_busybox_shell_and_utilities_match_posix_shell(self):
        self.card()
        self.write('proc/mounts', '/dev/mmcblk9 /media/card vfat ro 0 0\n')
        expected = self.run_detect()
        tools = self.root / 'fixture-tools'
        tools.mkdir()
        binary = shutil.which('busybox')
        for name in ('awk', 'cat', 'readlink'):
            (tools / name).symlink_to(binary)
        env = dict(os.environ, PATH=str(tools))
        proc = subprocess.run([binary, 'sh', str(DETECT), '--root', str(self.root)],
                              text=True, capture_output=True, env=env, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, expected)

    def test_script_has_no_device_writes_or_active_probes(self):
        source = DETECT.read_text()
        for command in ('mount', 'umount', 'mkfs', 'dd', 'devmem', 'gpioset', 'vdc',
                        'getprop', 'dumpsys', 'systemctl', 'ps', 'insmod', 'modprobe'):
            self.assertNotRegex(source, r'(?m)^\s*' + command + r'\s')
        self.assertNotRegex(source.replace('2>/dev/null', ''), r'>\s*[\x22\x27]?/((dev|sys)/)')


class TFPrerequisiteTests(FixtureBase):
    def run_check(self, manifest, expected):
        path = self.write('checklist.json', json.dumps(manifest))
        before = self.snapshot()
        proc = subprocess.run([sys.executable, '-B', str(CHECK), '--manifest', str(path),
                               '--evidence-root', str(self.root)],
                              text=True, capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        self.assertEqual(before, self.snapshot(), 'Checker changed supplied evidence')
        return json.loads(proc.stdout)

    def complete(self):
        self.write('evidence.txt', 'Synthetic host fixture only; not board evidence.\n')
        digest = hashlib.sha256((self.root / 'evidence.txt').read_bytes()).hexdigest()
        return {'schema_version': 1, 'board': 'Z1', 'facts': {
            key: {'status': 'verified', 'board': 'Z1', 'value': 'Fixture finding for ' + key,
                  'evidence': [{'path': 'evidence.txt', 'sha256': digest}]} for key in KEYS}}

    def test_incomplete_example_never_implies_ready(self):
        result = self.run_check(json.loads(EXAMPLE.read_text()), 1)
        self.assertEqual({m['item'] for m in result['missing']}, set(KEYS))
        self.assertFalse(result['ready_to_modify'])

    def test_documented_checklist_only_means_evidence_complete(self):
        result = self.run_check(self.complete(), 0)
        self.assertEqual(result['status'], 'documented')
        self.assertEqual(result['missing'], [])
        self.assertFalse(result['hardware_validated'])
        self.assertFalse(result['ready_to_modify'])

    def test_missing_cd_power_and_host_reported_individually(self):
        manifest = self.complete()
        for key in ('card_detect', 'vmmc', 'vqmmc', 'host_mapping'):
            del manifest['facts'][key]
        result = self.run_check(manifest, 1)
        self.assertEqual({m['item'] for m in result['missing']}, {'card_detect', 'vmmc', 'vqmmc', 'host_mapping'})

    def test_other_board_pinmux_not_accepted(self):
        manifest = self.complete()
        manifest['facts']['pinmux']['board'] = 'another-board'
        manifest['facts']['pinmux']['value'] = 'GPIO91-96 copied from another board'
        result = self.run_check(manifest, 1)
        self.assertEqual(result['missing'][0]['item'], 'pinmux')
        self.assertIn('missing Z1-specific provenance', result['missing'][0]['issues'])

    def test_hash_mismatch_not_accepted(self):
        manifest = self.complete()
        manifest['facts']['vmmc']['evidence'][0]['sha256'] = '0' * 64
        result = self.run_check(manifest, 1)
        self.assertIn('sha256 mismatch', result['missing'][0]['issues'][0])

    def test_verified_word_without_evidence_not_accepted(self):
        manifest = self.complete()
        manifest['facts']['dts']['evidence'] = []
        result = self.run_check(manifest, 1)
        self.assertEqual(result['missing'][0]['item'], 'dts')

    def test_missing_file_and_path_escape_not_accepted(self):
        for name in ('missing.txt', '../outside', '/etc/passwd'):
            manifest = self.complete()
            manifest['facts']['fstab']['evidence'][0]['path'] = name
            with self.subTest(name=name):
                self.assertEqual(self.run_check(manifest, 1)['missing'][0]['item'], 'fstab')

    def test_symlink_outside_evidence_root_is_rejected(self):
        manifest = self.complete()
        with tempfile.TemporaryDirectory(prefix='z1-tf-evidence-outside-') as tmp:
            outside = Path(tmp) / 'proof.txt'
            outside.write_text('unrelated evidence')
            (self.root / 'escape').symlink_to(outside)
            manifest['facts']['pinmux']['evidence'][0]['path'] = 'escape'
            result = self.run_check(manifest, 1)
            self.assertIn('escaping evidence', result['missing'][0]['issues'][0])

    def test_fifo_evidence_rejected_without_open(self):
        manifest = self.complete()
        os.mkfifo(self.root / 'fifo')
        manifest['facts']['vold']['evidence'][0]['path'] = 'fifo'
        self.assertEqual(self.run_check(manifest, 1)['missing'][0]['item'], 'vold')

    def test_invalid_schema_board_and_fact_key(self):
        for field, value in (('schema_version', 2), ('schema_version', True), ('board', 'S6'), ('facts', [])):
            manifest = self.complete()
            manifest[field] = value
            self.assertEqual(self.run_check(manifest, 2)['status'], 'invalid_input')
        manifest = self.complete()
        manifest['facts']['typo'] = {}
        self.assertEqual(self.run_check(manifest, 2)['status'], 'invalid_input')


    def test_malformed_json_exits_two(self):
        path = self.write('invalid.json', '{')
        proc = subprocess.run([sys.executable, '-B', str(CHECK), '--manifest', str(path),
                               '--evidence-root', str(self.root)],
                              text=True, capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)['status'], 'invalid_input')

    def test_manifest_outside_evidence_root_exits_two(self):
        with tempfile.TemporaryDirectory(prefix='z1-tf-manifest-outside-') as tmp:
            manifest = Path(tmp) / 'manifest.json'
            manifest.write_text(json.dumps(self.complete()))
            proc = subprocess.run([sys.executable, '-B', str(CHECK), '--manifest', str(manifest),
                                   '--evidence-root', str(self.root)],
                                  text=True, capture_output=True, timeout=5)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(json.loads(proc.stdout)['status'], 'invalid_input')


if __name__ == '__main__':
    unittest.main(verbosity=2)
