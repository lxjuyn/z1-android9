#!/usr/bin/env python3
"""Host CPU tests only; fabricated references are explicitly not device evidence."""
import tempfile
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
import analyze as a

class PixelTests(unittest.TestCase):
    def test_references_all_required_sizes_frames_cases(self):
        for w,h in a.SIZES:
            for frame in range(3):
                for case in a.CASES:
                    with self.subTest(size=(w,h),frame=frame,case=case):
                        r=a.verify_pixels(a.reference(w,h,frame,case),w,h,frame,case)
                        self.assertEqual(r["status"],"PASS")
                        self.assertEqual(r["pixels_checked"],w*h)

    def test_independent_known_values(self):
        self.assertEqual(a.expected(64,64,0,"texture",0,0),((240,32,16,255),))
        self.assertEqual(a.expected(63,65,0,"texture",62,64),((232,192,56,64),))
        self.assertEqual(a.expected(64,64,0,"blend",0,0),((112,64,112,255),))
        self.assertEqual(a.expected(64,64,0,"depth",16,16),((32,208,64,255),))
        self.assertEqual(a.expected(64,64,0,"depth",48,48),((24,48,216,255),))
        self.assertEqual(a.expected(64,64,1,"clear",0,0),((34,51,17,255),))
        # triangle a=(8,8), b=(56,16), c=(21,56), twice-area=2200.
        self.assertEqual(a.expected(64,64,0,"triangle",24,24),((112,67,77,255),))

    def test_zero_blank_poison_and_clear_only_rejected(self):
        for case in a.CASES:
            for byte in (0,255,0xa5):
                with self.subTest(case=case,byte=byte):
                    self.assertEqual(a.verify_pixels(bytes([byte])*(64*64*4),64,64,0,case)["status"],"FAIL")
        for case in a.CASES:
            self.assertEqual(a.verify_pixels(bytes((0,0,0,255))*4096,64,64,0,case)["status"],"FAIL")
        clear=a.reference(64,64,0,"clear")
        for case in a.CASES[1:]:
            self.assertEqual(a.verify_pixels(clear,64,64,0,case)["status"],"FAIL")

    def test_old_center_corner_fake_fails(self):
        data=bytearray(a.reference(64,64,0,"clear"))
        data[(32*64+32)*4:(32*64+32)*4+4]=bytes((255,0,0,255))
        r=a.verify_pixels(data,64,64,0,"triangle")
        self.assertEqual(r["status"],"FAIL")
        self.assertGreater(r["mismatched_pixels"],500)

    def test_single_corrupt_pixel_and_channel_alpha(self):
        for case in a.CASES:
            for channel in range(4):
                data=bytearray(a.reference(64,64,0,case))
                offset=(30*64+30)*4+channel
                data[offset]=(data[offset]+100)%256
                r=a.verify_pixels(data,64,64,0,case)
                self.assertEqual(r["status"],"FAIL")
                self.assertEqual(r["mismatched_pixels"],1)

    def test_frame_stale_vertical_flip_swizzle_and_depth_disabled(self):
        for case in a.CASES:
            self.assertEqual(a.verify_pixels(a.reference(65,65,0,case),65,65,1,case)["status"],"FAIL")
        data=a.reference(63,65,0,"texture")
        flipped=b"".join(data[y*63*4:(y+1)*63*4] for y in reversed(range(65)))
        self.assertEqual(a.verify_pixels(flipped,63,65,0,"texture")["status"],"FAIL")
        data=bytearray(data)
        for i in range(0,len(data),4): data[i],data[i+2]=data[i+2],data[i]
        self.assertEqual(a.verify_pixels(data,63,65,0,"texture")["status"],"FAIL")
        self.assertEqual(a.verify_pixels(bytes((224,16,32,255))*4096,64,64,0,"depth")["status"],"FAIL")

    def test_exact_size_and_bounds(self):
        for size in (0,4096*4-1,4096*4+1):
            with self.assertRaises(ValueError): a.verify_pixels(bytes(size),64,64,0,"clear")
        for w,h,f,c in ((0,64,0,"clear"),(513,64,0,"clear"),(64,64,16,"clear"),(64,64,0,"fake")):
            with self.assertRaises(ValueError): a.verify_pixels(b"",w,h,f,c)

    def test_triangle_boundaries_never_unchecked(self):
        w=h=64
        boundaries=[(x,y) for y in range(h) for x in range(w) if len(a.expected(w,h,0,"triangle",x,y))>1]
        self.assertGreater(len(boundaries),0)
        data=bytearray(a.reference(w,h,0,"triangle"))
        for x,y in boundaries: data[(y*w+x)*4:(y*w+x)*4+4]=bytes((255,0,255,0))
        self.assertEqual(a.verify_pixels(data,w,h,0,"triangle")["mismatched_pixels"],len(boundaries))

class CaptureTests(unittest.TestCase):
    def make_fake_capture(self,directory):
        """Explicit synthetic metadata fixture, not a board result."""
        lines=["SCHEMA=z1-gles2-pbuffer-v1","API_ERRORS=0","PROBE_STATUS=READBACK_COMPLETE",
               "PIXEL_ORIGIN=bottom-left","HARDWARE_ATTRIBUTION=UNPROVEN","FRAMES=1","REQUESTED_SIZES=8x8",
               "BUILD_TARGET=host-reference-NOT-BOARD"]
        lines += [key+"=SYNTHETIC_TEST_ONLY" for key in ("EGL_VENDOR","EGL_VERSION","EGL_EXTENSIONS",
                  "GL_VENDOR","GL_RENDERER","GL_VERSION","GL_SHADING_LANGUAGE_VERSION","GL_EXTENSIONS")]
        (directory/"run.log").write_text("\n".join(lines)+"\n")
        rows=[]
        for case in a.CASES:
            name=f"8x8_f00_{case}.rgba"
            (directory/name).write_bytes(a.reference(8,8,0,case))
            rows.append(f"{name}\t8\t8\t0\t{case}\n")
        (directory/"manifest.tsv").write_text("".join(rows))
        for name in ("maps_before_egl.txt","maps_8x8_current.txt","maps_8x8_after_readback.txt"):
            (directory/name).write_text("00001000-00002000 r-xp 00000000 00:00 1 /SYNTHETIC/libEGL.so\n")

    def test_synthetic_pass_never_hardware_or_board_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);self.make_fake_capture(d)
            result=a.analyze_directory(d,1,(8,8))
            self.assertEqual(result["pixel_status"],"PASS")
            self.assertEqual(result["hardware_status"],"UNPROVEN")
            self.assertEqual(result["board_status"],"NOT_RUN")
            with self.assertRaises(ValueError): a.analyze_directory(d,1)

    def test_missing_duplicate_traversal_incomplete_maps_and_api_fail(self):
        for mutation in ("missing", "duplicate", "traversal", "incomplete", "maps", "api_fail", "symlink", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                d=Path(tmp);self.make_fake_capture(d)
                mf=d/"manifest.tsv"
                if mutation=="missing": (d/"8x8_f00_clear.rgba").unlink()
                elif mutation=="duplicate":
                    rows=mf.read_text().splitlines();rows[-1]=rows[0];mf.write_text("\n".join(rows))
                elif mutation=="traversal": mf.write_text(mf.read_text().replace("8x8_f00_clear.rgba","../clear.rgba"))
                elif mutation=="incomplete": (d/"run.log").write_text("PROBE_STATUS=STARTED\n")
                elif mutation=="maps": (d/"maps_8x8_after_readback.txt").unlink()
                elif mutation=="api_fail":
                    p=d/"run.log";p.write_text(p.read_text()+"GL_ERROR stage=test\n")
                elif mutation=="symlink":
                    p=d/"8x8_f00_clear.rgba";p.unlink();p.symlink_to(d/"8x8_f00_depth.rgba")
                elif mutation=="extra": (d/"extra.rgba").write_bytes(b"x")
                with self.assertRaises(ValueError):a.analyze_directory(d,1,(8,8))

    def test_cli_black_forged_and_corrupt_captures_fail(self):
        for mutation in ('black', 'opaque_black', 'center_fake', 'one_pixel', 'api_fail', 'truncated'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                d=Path(tmp);self.make_fake_capture(d)
                if mutation in ('black', 'opaque_black'):
                    pixel=bytes((0,0,0,255)) if mutation=='opaque_black' else bytes(4)
                    for p in d.glob('*.rgba'): p.write_bytes(pixel*64)
                elif mutation=='center_fake':
                    p=d/'8x8_f00_triangle.rgba'
                    data=bytearray(a.reference(8,8,0,'clear'))
                    data[(4*8+4)*4:(4*8+4)*4+4]=bytes((255,0,0,255))
                    p.write_bytes(data)
                elif mutation=='one_pixel':
                    p=d/'8x8_f00_depth.rgba';data=bytearray(p.read_bytes());data[3]=0;p.write_bytes(data)
                elif mutation=='api_fail':
                    p=d/'run.log';p.write_text(p.read_text()+'GL_ERROR stage=forged'+chr(10))
                elif mutation=='truncated':
                    p=d/'8x8_f00_clear.rgba';p.write_bytes(p.read_bytes()[:-1])
                run=subprocess.run([sys.executable,str(Path(a.__file__)),str(d),
                                    '--expected-frames','1','--custom-size','8','8'],
                                   capture_output=True,text=True,timeout=10,
                                   env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                self.assertEqual(run.returncode,1,run.stdout+run.stderr)
                report=json.loads(run.stdout)
                self.assertEqual(report['pixel_status'],'FAIL')
                self.assertEqual(report['hardware_status'],'UNPROVEN')

    def test_forged_android_identity_cannot_prove_hardware(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);self.make_fake_capture(d)
            p=d/'run.log'
            p.write_text(p.read_text().replace('host-reference-NOT-BOARD','android-api28-armv7')
                         .replace('GL_RENDERER=SYNTHETIC_TEST_ONLY','GL_RENDERER=Mali-400'))
            report=a.analyze_directory(d,1,(8,8))
            self.assertEqual(report['pixel_status'],'PASS')
            self.assertEqual(report['board_status'],'SUPPLIED_CAPTURE_NOT_AUTHENTICATED')
            self.assertEqual(report['hardware_status'],'UNPROVEN')

if __name__=="__main__": unittest.main(verbosity=2)
