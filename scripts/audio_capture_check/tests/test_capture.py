import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capture_check', BASE/'check_capture.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
SNAPSHOT = '''Z1_CAPTURE_SNAPSHOT_V1
MODE=fixture_metadata
PROC_PCM=readable
AWB=present
PCM=2:7
NODE_2_7=character
ACCESS_2_7=rw_by_shell_identity
STATE_2_7=closed
TOOL_system_bin_tinycap=present_not_executed
'''
POLICY = '''<module name="primary"><attachedDevices><item>Built-In Mic</item></attachedDevices><mixPorts><mixPort name="primary input" role="sink"><profile format="AUDIO_FORMAT_PCM_16_BIT" samplingRates="16000" channelMasks="AUDIO_CHANNEL_IN_MONO"/></mixPort></mixPorts><devicePorts><devicePort tagName="Built-In Mic" type="AUDIO_DEVICE_IN_BUILTIN_MIC" role="source"/></devicePorts><routes><route sink="primary input" sources="Built-In Mic"/></routes></module>'''

class CaptureCheck(unittest.TestCase):
 def test_valid_profile(self):
  p=c.profile(16000,1); self.assertEqual(p['errors'],[]); self.assertEqual((p['period_bytes'],p['ring_bytes'],p['period_ms']),(2048,8192,64))
 def test_all_mic_rates(self):
  for r in (8000,16000,32000,48000): self.assertFalse(c.profile(r,1)['errors'])
 def test_bad_rates(self):
  for r in (0,11025,12000,22050,24000,44100,96000): self.assertTrue(c.profile(r,1)['errors'])
 def test_stereo_16bit(self): self.assertEqual(c.profile(48000,2)['period_bytes'],4096)
 def test_bad_formats(self):
  for bits in (8,24,32): self.assertTrue(c.profile(16000,1,bits)['errors'])
 def test_bad_channels(self):
  for channels in (0,3,8): self.assertTrue(c.profile(16000,channels)['errors'])
 def test_period_lower_bound(self): self.assertTrue(c.profile(16000,1,period_frames=128)['errors'])
 def test_period_alignment(self): self.assertTrue(c.profile(16000,1,period_frames=513)['errors'])
 def test_period_count_bounds(self):
  for periods in (0,1,33): self.assertTrue(c.profile(16000,1,periods=periods)['errors'])
 def test_ring_bound(self): self.assertTrue(c.profile(16000,2,period_frames=8192,periods=8)['errors'])
 def test_nonzero_card_device(self):
  checks=c.snapshot_checks(SNAPSHOT);self.assertIn('2:7',next(x['detail'] for x in checks if x['code']=='awb_identity'))
 def test_missing_proc_is_unknown(self):
  self.assertEqual(c.snapshot_checks('Z1_CAPTURE_SNAPSHOT_V1\nMODE=fixture_metadata\nPROC_PCM=unavailable\n')[1]['status'],'unknown')
 def test_no_capture_is_blocker(self):
  self.assertTrue(any(x['status']=='blocked' for x in c.snapshot_checks('Z1_CAPTURE_SNAPSHOT_V1\nMODE=fixture_metadata\nPROC_PCM=readable\nAWB=absent\n')))
 def test_busy_pcm_blocked(self):
  self.assertTrue(any(x['code']=='pcm_ownership' and x['status']=='blocked' for x in c.snapshot_checks(SNAPSHOT.replace('closed','RUNNING'))))
 def test_multiple_pcm_blocked(self):
  self.assertTrue(any(x['code']=='awb_ambiguous' for x in c.snapshot_checks(SNAPSHOT+'PCM=3:1\n')))
 def test_duplicate_key_rejected(self):
  with self.assertRaises(ValueError): c.snapshot_checks(SNAPSHOT+'MODE=live_metadata\n')
 def test_unrelated_metadata_rejected(self):
  with self.assertRaises(ValueError): c.snapshot_checks(SNAPSHOT+'UNRELATED=abc\n')
 def test_policy_good(self): self.assertFalse(any(x['status']=='blocked' for x in c.policy_checks(POLICY)))
 def test_policy_rate_mismatch(self): self.assertTrue(any(x['code']=='policy_rates' and x['status']=='blocked' for x in c.policy_checks(POLICY.replace('16000','44100'))))
 def test_policy_format_mismatch(self): self.assertTrue(any(x['code']=='policy_format' for x in c.policy_checks(POLICY.replace('PCM_16_BIT','PCM_FLOAT'))))
 def test_policy_route_missing(self): self.assertTrue(any(x['code']=='builtin_route' and x['status']=='blocked' for x in c.policy_checks(POLICY.replace('sources="Built-In Mic"','sources="missing"'))))
 def test_unresolved_include_unknown(self): self.assertEqual(c.policy_checks('<audioPolicyConfiguration/>')[0]['status'],'unknown')
 def test_dtd_rejected(self):
  with self.assertRaises(ValueError): c.policy_checks('<!DOCTYPE module []>'+POLICY)
 def test_malformed_xml(self):
  with self.assertRaises(c.ET.ParseError): c.policy_checks('<module>')
 def test_symlink_rejected(self):
  with tempfile.TemporaryDirectory(dir=BASE/'tests') as tmp:
   p=Path(tmp);(p/'target').write_text('x');(p/'link').symlink_to('target')
   with self.assertRaises(ValueError): c.read_regular(p/'link')
 def test_fifo_rejected_without_blocking(self):
  with tempfile.TemporaryDirectory(dir=BASE/'tests') as tmp:
   p=Path(tmp)/'fifo';os.mkfifo(p)
   with self.assertRaises(ValueError): c.read_regular(p)
 def test_device_rejected_without_io_open(self):
  with tempfile.TemporaryDirectory(dir=BASE/'tests') as tmp:
   p=Path(tmp)/'snapshot';p.write_text('x')
   actual_open=c.os.open;actual_stat=c.os.fstat;calls=[]
   def tracked_open(path, flags, *args, **kwargs):
    calls.append((str(path),flags))
    return actual_open(path, flags, *args, **kwargs)
   def fake_device(fd):
    s=actual_stat(fd)
    if c.stat.S_ISREG(s.st_mode):
     values=list(s);values[0]=c.stat.S_IFCHR;return os.stat_result(values)
    return s
   with mock.patch.object(c.os,'open',side_effect=tracked_open), mock.patch.object(c.os,'fstat',side_effect=fake_device):
    with self.assertRaises(ValueError):c.read_regular(p)
   self.assertTrue(calls)
   self.assertTrue(all(flags & os.O_PATH for _,flags in calls))
 def test_path_swap_reads_pinned_regular_inode(self):
  with tempfile.TemporaryDirectory(dir=BASE/'tests') as tmp:
   p=Path(tmp)/'snapshot';p.write_text('original')
   real_open=c.os.open
   def swapped(path, flags, *args, **kwargs):
    fd=real_open(path,flags,*args,**kwargs)
    if str(path)==p.name and flags & os.O_PATH:
     p.rename(p.with_suffix('.before'));os.mkfifo(p)
    return fd
   with mock.patch.object(c.os,'open',side_effect=swapped):
    self.assertEqual(c.read_regular(p),'original')
 def test_live_paths_rejected_before_metadata_open(self):
  for path in ('/dev/null','/proc/self/status','/sys/power/state'):
   with mock.patch.object(c.os,'open') as opened:
    with self.assertRaises(ValueError):c.read_regular(path)
    opened.assert_not_called()
 def test_oversized_input_rejected(self):
  with tempfile.TemporaryDirectory(dir=BASE/'tests') as tmp:
   p=Path(tmp)/'large';p.write_bytes(b'x'*2097153)
   with self.assertRaises(ValueError): c.read_regular(p)
 def test_valid_evidence_never_returns_ready(self):
  with tempfile.TemporaryDirectory(dir=BASE/'tests') as tmp:
   p=Path(tmp)/'snapshot';p.write_text(SNAPSHOT);out=io.StringIO()
   with contextlib.redirect_stdout(out): rc=c.main(['--snapshot',str(p)])
   result=json.loads(out.getvalue());self.assertEqual(rc,0);self.assertEqual(result['result'],'incomplete');self.assertFalse(result['recording_verified'])
 def test_supply_graph_and_real_route(self):
  with tempfile.TemporaryDirectory(dir=BASE/'tests') as tmp:
   p=Path(tmp)
   for rel in c.SOURCE_PATHS.values(): (p/rel).parent.mkdir(parents=True,exist_ok=True);(p/rel).write_text('placeholder')
   codec=p/c.SOURCE_PATHS['codec']
   text='''SND_SOC_DAPM_SUPPLY("MIC UL", 0)
static const struct snd_soc_dapm_route mt6323_dapm_routes[] = {
 { "MIC Route", "Switch", "MIC Input" },
 { "MIC Route", NULL, "MIC UL" },
 { "AIF1 Capture", NULL, "MIC UL" },
};'''
   codec.write_text(text);checks,_=c.source_checks(p)
   self.assertEqual(next(x['status'] for x in checks if x['code']=='codec_signal_path'),'blocked')
   codec.write_text(text.replace('\n};','\n { "AIF1 Capture", NULL, "MIC Route" },\n};'))
   checks,_=c.source_checks(p);self.assertEqual(next(x['status'] for x in checks if x['code']=='codec_signal_path'),'observed')

class Collector(unittest.TestCase):
 def run_fixture(self, pcm=None, setup=None):
  with tempfile.TemporaryDirectory(dir=BASE/'tests') as tmp:
   p=Path(tmp);(p/'proc/asound').mkdir(parents=True);(p/'dev/snd').mkdir(parents=True)
   if pcm is not None: (p/'proc/asound/pcm').write_text(pcm)
   if setup: setup(p)
   return subprocess.run(['sh',str(BASE/'collect_prerequisites.sh'),'--root',str(p)],capture_output=True,text=True,timeout=3)
 def test_no_arguments_no_host_reads(self):
  r=subprocess.run(['sh',str(BASE/'collect_prerequisites.sh')],capture_output=True,text=True,timeout=3)
  self.assertEqual(r.returncode,2);self.assertNotIn('PROC_PCM=',r.stdout)
 def test_missing_proc(self): self.assertEqual(self.run_fixture().returncode,3)
 def test_playback_only(self): self.assertEqual(self.run_fixture('00-00: DL1 : playback 1\n').returncode,3)
 def test_card_discovery_no_device_open(self):
  def setup(p):
   d=p/'proc/asound/card2/pcm7c/sub0';d.mkdir(parents=True);(d/'status').write_text('closed\n')
   os.mkfifo(p/'dev/snd/pcmC2D7c')
  r=self.run_fixture('02-07: AWB : AWB Capture : capture 1\n',setup)
  self.assertEqual(r.returncode,0);self.assertIn('PCM=2:7',r.stdout);self.assertIn('STATE_2_7=closed',r.stdout);self.assertIn('NODE_2_7=missing_or_not_character',r.stdout)
 def test_target_tool_never_invoked(self):
  def setup(p):
   d=p/'system/bin';d.mkdir(parents=True);f=d/'tinycap';f.write_text('#!/bin/sh\nprintf SHOULD_NEVER_RUN\nexit 99\n');f.chmod(0o755)
  r=self.run_fixture('00-01: AWB : capture 1\n',setup)
  self.assertEqual(r.returncode,0);self.assertNotIn('SHOULD_NEVER_RUN',r.stdout);self.assertIn('present_not_executed',r.stdout)
 def test_proc_symlink_rejected(self):
  def setup(p): (p/'proc/asound/pcm').unlink();(p/'proc/asound/pcm').symlink_to('/dev/zero')
  r=self.run_fixture('00-01: AWB : capture 1\n',setup);self.assertEqual(r.returncode,3)
 def test_fifo_proc_rejected(self):
  def setup(p): (p/'proc/asound/pcm').unlink();os.mkfifo(p/'proc/asound/pcm')
  self.assertEqual(self.run_fixture('x',setup).returncode,3)

if __name__=='__main__':unittest.main(verbosity=2)
