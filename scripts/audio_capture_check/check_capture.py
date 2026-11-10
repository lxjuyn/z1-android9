#!/usr/bin/env python3
'''Offline prerequisites. Never opens PCM or invokes target tools.
Exit 0: evidence incomplete without definite blockers, NOT capture verified.
Exit 3: blockers found. Exit 2: unreadable/malformed evidence.
'''
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import xml.etree.ElementTree as ET

MIC_RATES = {8000, 16000, 32000, 48000}
SOURCE_PATHS = {
 'hal': 'z1a9_gpt/scripts/r5g_audio/audio_primary_z1_poc.c',
 'afe': 'fork_linux/sound/soc/mediatek/mt6572/mt6572-afe-pcm.c',
 'codec': 'fork_linux/sound/soc/codecs/mt6323.c',
 'machine': 'fork_linux/sound/soc/mediatek/mt6572/mt6572-mt6323.c',
 'dapm': 'fork_linux/sound/soc/soc-dapm.c',
}

def read_regular(path):
 p = Path(os.path.abspath(path))
 if any(p == root or root in p.parents for root in map(Path, ('/dev', '/sys', '/proc'))):
  raise ValueError('live input rejected; supply an offline snapshot')
 fd = os.open('/', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
 read_fd = None
 try:
  for component in p.parts[1:-1]:
   nxt = os.open(component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
   os.close(fd); fd = nxt
  nxt = os.open(p.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
  os.close(fd); fd = nxt
  before = os.fstat(fd)
  if not stat.S_ISREG(before.st_mode) or before.st_size > 2097152:
   raise ValueError('input must be bounded regular file')
  # Reopen the pinned regular inode, not its replaceable pathname.
  read_fd = os.open('/proc/self/fd/' + str(fd), os.O_RDONLY | os.O_NONBLOCK)
  if not os.path.samestat(before, os.fstat(read_fd)):
   raise ValueError('input inode changed')
  with os.fdopen(read_fd, 'rb', closefd=False) as stream:
   raw = stream.read(2097153)
  if len(raw) > 2097152: raise ValueError('input too large')
  return raw.decode('utf-8')
 except OSError as error:
  raise ValueError('unsafe or unavailable offline input') from error
 finally:
  if read_fd is not None: os.close(read_fd)
  os.close(fd)

def item(code, status, detail):
 return dict(code=code, status=status, detail=detail)

def profile(rate, channels, bits=16, period_frames=1024, periods=4):
 errors = []
 if rate not in MIC_RATES: errors.append('MIC rate must be 8000/16000/32000/48000')
 if channels not in (1, 2): errors.append('only 1 or 2 channels supported')
 if bits != 16: errors.append('only S16_LE supported')
 size = period_frames * channels * bits // 8
 if period_frames <= 0 or size < 1024 or size > 32768 or size % 8:
  errors.append('period bytes must be 1024..32768 and 8-byte aligned')
 if not 2 <= periods <= 32: errors.append('period count must be 2..32')
 if size * periods > 131072: errors.append('ring exceeds 128 KiB')
 return dict(errors=errors, period_bytes=size, ring_bytes=size*periods,
             period_ms=round(period_frames*1000/rate, 3) if rate > 0 else None)

def snapshot_checks(text):
 lines = text.splitlines()
 if not lines or lines[0] != 'Z1_CAPTURE_SNAPSHOT_V1': raise ValueError('header missing')
 values, pcms = {}, []
 for line in lines[1:]:
  if not line or '=' not in line: raise ValueError('invalid line')
  key, value = line.split('=', 1)
  if key == 'PCM':
   if not re.fullmatch(r'[0-9]{1,2}:[0-9]{1,2}', value) or value in pcms:
    raise ValueError('invalid/duplicate PCM')
   pcms.append(value)
  else:
   pattern = r'(MODE|PROC_PCM|AWB|TOOL_(system|vendor)_bin_(tinycap|tinymix)|(?:NODE|ACCESS|STATE)_[0-9]{1,2}_[0-9]{1,2})'
   if not re.fullmatch(pattern, key) or key in values or not re.fullmatch('[A-Za-z_]+', value):
    raise ValueError('invalid/duplicate metadata')
   values[key] = value
 if values.get('MODE') not in ('fixture_metadata', 'live_metadata'): raise ValueError('invalid mode')
 checks = [item('evidence_scope', 'observed', values['MODE'] + '; no recording performed')]
 if values.get('PROC_PCM') != 'readable':
  checks.append(item('proc_pcm', 'unknown', 'Registration not determined: proc metadata unavailable'))
 elif not pcms:
  checks.append(item('awb_registration', 'blocked', 'No AWB capture entry observed'))
 elif len(pcms) != 1:
  checks.append(item('awb_ambiguous', 'blocked', 'Multiple AWB entries require explicit device selection'))
 else:
  key = pcms[0].replace(':', '_')
  checks.append(item('awb_identity', 'observed', 'ALSA card:device ' + pcms[0]))
  checks.append(item('capture_node', 'observed' if values.get('NODE_'+key) == 'character' else 'blocked',
                     'Node metadata: ' + values.get('NODE_'+key, 'unknown')))
  checks.append(item('shell_access', 'observed' if values.get('ACCESS_'+key) == 'rw_by_shell_identity' else 'unknown',
                     'Shell access cannot establish audioserver DAC/SELinux access'))
  state = values.get('STATE_'+key, 'unknown')
  checks.append(item('pcm_ownership', 'observed' if state == 'closed' else 'unknown' if state == 'unknown' else 'blocked',
                     'Snapshot state=' + state + '; time-of-check only'))
 has_tool = any(values.get('TOOL_'+p+'_bin_tinycap') == 'present_not_executed' for p in ('system', 'vendor'))
 checks.append(item('tinycap_file', 'observed' if has_tool else 'unknown',
                    'File exists; ABI/dependencies/CLI unverified' if has_tool else 'No executable tinycap observed; not a HAL prerequisite'))
 return checks

def policy_checks(text):
 if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper(): raise ValueError('DTD rejected')
 root = ET.fromstring(text)
 modules = [root] if root.tag == 'module' else list(root.findall('.//module'))
 modules = [m for m in modules if m.get('name') == 'primary']
 if not modules: return [item('policy_primary', 'unknown', 'Primary module absent/unresolved XInclude; supply primary XML')]
 if len(modules) != 1: raise ValueError('multiple primary modules')
 module = modules[0]
 ins = [m for m in module.findall('./mixPorts/mixPort') if m.get('role') == 'sink']
 if not ins: return [item('policy_input', 'blocked', 'No input sink mixPort')]
 checks = []
 ports = {p.get('tagName'): p for p in module.findall('./devicePorts/devicePort')}
 attached = {p.text.strip() for p in module.findall('./attachedDevices/item') if p.text}
 found_builtin = False
 for port in ins:
  profiles = port.findall('./profile')
  if not profiles: checks.append(item('policy_profiles', 'unknown', 'No static input profile'))
  for p in profiles:
   if p.get('format') != 'AUDIO_FORMAT_PCM_16_BIT':
    checks.append(item('policy_format', 'blocked', 'Input profile is not PCM_16_BIT'))
   rates = p.get('samplingRates', '').split(',')
   if not rates or not all(r.strip().isdigit() for r in rates):
    checks.append(item('policy_rates', 'unknown', 'Dynamic/missing rates require negotiation'))
   else:
    extra = sorted({int(r) for r in rates} - MIC_RATES)
    checks.append(item('policy_rates', 'blocked' if extra else 'observed',
                       'MIC-incompatible rates: '+','.join(map(str, extra)) if extra else 'Rates within source MIC subset'))
   masks = set(p.get('channelMasks', '').split(','))
   if not masks <= {'AUDIO_CHANNEL_IN_MONO', 'AUDIO_CHANNEL_IN_STEREO'}:
    checks.append(item('policy_channels', 'blocked', 'Unsupported/unspecified channel mask'))
  for route in module.findall('./routes/route'):
   if route.get('sink') != port.get('name'): continue
   for source in route.get('sources', '').split(','):
    source = source.strip(); device = ports.get(source)
    if device is None or device.get('role') != 'source':
     checks.append(item('policy_route', 'blocked', 'Missing/non-source input device'))
    elif device.get('type') == 'AUDIO_DEVICE_IN_BUILTIN_MIC':
     found_builtin = found_builtin or source in attached
    elif device.get('type') == 'AUDIO_DEVICE_IN_WIRED_HEADSET':
     checks.append(item('headset_mic', 'unknown', 'Physical headset microphone route and detection unverified'))
 checks.append(item('builtin_route', 'observed' if found_builtin else 'blocked',
                    'Attached built-in mic declared' if found_builtin else 'Missing attached built-in mic route'))
 return checks

def source_checks(base):
 files = {key: read_regular(Path(base)/path) for key,path in SOURCE_PATHS.items()}
 checks = []
 hal = files['hal']
 m = re.search(r'static int adev_open_input_stream[\s\S]*?(?=static void adev_close_input_stream)', hal)
 checks.append(item('hal_capture', 'blocked' if m and 'return -ENOSYS' in m.group(0) else 'unknown',
                    'Review open_input_stream; kernel capture cannot substitute for an Android input stream'))
 mute = re.search(r'static int adev_set_mic_mute[^\n]+', hal)
 if mute and '(void)s; return 0' in mute.group(0):
  checks.append(item('mic_mute', 'blocked', 'Mute callback discards state; enforce mute before exposing recording'))
 checks.append(item('kernel_awb', 'observed' if 'mt6572_afe_awb_prepare' in files['afe'] and '.capture_only = 1' in files['machine'] else 'unknown',
                    'Source-level AWB path only; loaded module identity unverified'))
 codec = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*', '', files['codec'])
 routes = re.search(r'mt6323_dapm_routes\[\]\s*=\s*\{([\s\S]*?)\n\};', codec)
 q = chr(34)
 supplies = set(re.findall(r'SND_SOC_DAPM_SUPPLY\(\s*'+q+'([^'+q+']+)'+q, codec))
 if routes:
  pattern = r'\{\s*'+q+'([^'+q+']+)'+q+r'\s*,\s*(?:'+q+'[^'+q+']*'+q+r'|NULL)\s*,\s*'+q+'([^'+q+']+)'+q+r'\s*\}'
  edges = re.findall(pattern, routes.group(1)); reachable = {'MIC Input'}
  for _ in range(len(edges)+1):
   previous = set(reachable)
   reachable |= {sink for sink,src in edges if src in reachable and src not in supplies and sink not in supplies}
   if previous == reachable: break
  ok = 'AIF1 Capture' in reachable
  checks.append(item('codec_signal_path', 'observed' if ok else 'blocked',
                     'MIC signal route reaches AIF1' if ok else 'No non-supply MIC Input -> AIF1 Capture route; DAPM supply edges do not carry signal connectivity'))
 else: checks.append(item('codec_signal_path', 'unknown', 'Route table parser did not match'))
 return checks, [dict(path=str(Path(base)/SOURCE_PATHS[k]),sha256=hashlib.sha256(v.encode()).hexdigest()) for k,v in files.items()]

def main(argv=None):
 parser = argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--snapshot'); parser.add_argument('--policy'); parser.add_argument('--source-root')
 args = parser.parse_args(argv)
 if not any((args.snapshot,args.policy,args.source_root)): parser.error('supply offline evidence')
 checks, sources = [], []
 try:
  if args.snapshot: checks.extend(snapshot_checks(read_regular(args.snapshot)))
  if args.policy: checks.extend(policy_checks(read_regular(args.policy)))
  if args.source_root:
   more,sources = source_checks(args.source_root); checks.extend(more)
 except (OSError,ValueError,ET.ParseError) as exc:
  print(json.dumps(dict(schema=1,result='invalid_input',error=type(exc).__name__),indent=2)); return 2
 checks.extend([item('mic_pin','unknown','MIC1/MIC2 board mapping not established by defaults'),
                item('loaded_identity','unknown','Match runtime kernel/modules/HAL/policy to audited hashes'),
                item('access_policy','unknown','audioserver DAC/SELinux access untested'),
                item('end_to_end','unknown','No hardware capture, sample, timestamp, XRUN, mute or standby validation performed')])
 blocked = any(c['status']=='blocked' for c in checks)
 print(json.dumps(dict(schema=1,result='blocked' if blocked else 'incomplete',recording_verified=False,checks=checks,sources=sources,
                       proposed_first_profile=dict(format='S16_LE',rate=16000,channels=1,period_frames=1024,period_count=4,**profile(16000,1))),indent=2))
 return 3 if blocked else 0

if __name__ == '__main__': sys.exit(main())
