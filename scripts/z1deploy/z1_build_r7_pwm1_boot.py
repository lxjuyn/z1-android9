#!/usr/bin/env python3
"""Put the Z1 PWM1 LED module into an R7 Android boot image."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import z1_prepare_recovery_boot as boot

PWM = '/soc/pwm@11008000'
PMIC = '/soc/pwrap@1000f000/mt6323/leds'
COMPAT = 'z1,mt6572-lk-pwm1-backlight'
MODULE = 'z1_pwm1_backlight.ko'
ANCHOR = b'    chown system system /sys/class/leds/lcd-backlight/brightness\n'


def build(base, module):
    header, kh, kernel, dtb, root = boot.split_boot(base)
    boot.require(boot.sha(kernel) == 'b6b5174e8f394a70e8bde22bf2ff472a60aeb500d31ed1088090a0abb654c63e',
                 'unexpected R7 kernel ABI')
    boot.require(COMPAT.encode() in module.read_bytes() and
                 b'vermagic=7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8' in module.read_bytes(),
                 'module compatibility mismatch')
    entries = boot.contents(*boot.cpio(root))
    init = entries['init.rc']
    boot.require(init.count(ANCHOR) == 1 and MODULE not in entries and
                 b'chown system system /sys/class/leds/lcd-backlight/display_blank' in init,
                 'unexpected init or module state')
    init = init.replace(ANCHOR, f'    insmod /{MODULE}\n'.encode() + ANCHOR)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'root').write_bytes(root)
        (tmp / 'init.rc').write_bytes(init)
        subprocess.run([sys.executable, SCRIPTS / 'z1_ramdisk_edit.py', tmp / 'root',
                        tmp / 'new', '--replace', f'init.rc={tmp}/init.rc',
                        '--add', f'{MODULE}={module}', '--mode', f'{MODULE}=0644'], check=True)
        changed = (tmp / 'new').read_bytes()
        path = tmp / 'appended.dtb'
        path.write_bytes(dtb)
        boot.require(boot.dt_read(path, PWM, 'status', 's') == 'disabled' and
                     boot.dt_read(path, PWM, 'compatible', 's') == 'mediatek,mt7623-pwm' and
                     boot.dt_read(path, PMIC + '/isink0', 'label', 's') == 'lcd-backlight' and
                     boot.dt_read(path, PWM, 'reg') == '11008000 1000',
                     'unexpected backlight DT layout')
        boot.require(subprocess.check_output(['fdtget', '-l', path, PMIC], text=True).split()
                     == ['isink0'], 'other PMIC LEDs require controller')
        for node, key, val in [(PMIC, 'status', 'disabled'), (PWM, 'compatible', COMPAT),
                               (PWM, 'status', 'okay')]:
            subprocess.run(['fdtput', '-t', 's', path, node, key, val], check=True)
        start = int(boot.dt_read(path, '/chosen', 'linux,initrd-start'), 16)
        boot.require(start + len(root) - 512 ==
                     int(boot.dt_read(path, '/chosen', 'linux,initrd-end'), 16),
                     'incorrect input initrd bounds')
        subprocess.run(['fdtput', '-t', 'x', path, '/chosen', 'linux,initrd-end',
                        f'{start + len(changed) - 512:x}'], check=True)
        dtb = path.read_bytes()
    image = boot.make_image(header, kh, kernel, dtb, changed)
    _, _, k, d, r = boot.split_boot(image)
    boot.require((k, d, r) == (kernel, dtb, changed) and len(image) <= 0x600000,
                 'invalid boot image readback')
    boot.require(boot.contents(*boot.cpio(r)) == {**entries, 'init.rc': init, MODULE: module.read_bytes()},
                 'unexpected cpio content delta')
    return image


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('base', 'module', 'output'):
        parser.add_argument(field, type=Path)
    args = parser.parse_args()
    data = build(args.base.read_bytes(), args.module)
    with args.output.open('xb') as output:
        output.write(data)
    print(boot.sha(data), args.output, len(data))
