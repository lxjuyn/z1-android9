#!/usr/bin/env python3
"""Independent CPU oracle. Strict full-frame RGBA comparison, never HW proof.

GL lower-left row order. Triangle boundary pixels are NOT skipped: within
0.1 pixel of an edge either clear or the calculated barycentric color is
accepted, allowing the ES2 minimum 4-bit subpixel rasterization precision.
Every other pixel has exactly one expected RGBA value. No mismatch quota.
Official semantics: Khronos ES 2.0.25 sections 3.5, 4.1.6 and 4.3.1;
EGL 1.5 section 3.5.2. Reference fixtures cannot prove a real GPU executed.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

CASES = ("clear", "triangle", "texture", "blend", "depth")
SIZES = ((63, 63), (64, 64), (65, 65), (240, 320))
TOLERANCE = {"clear": 1, "triangle": 3, "texture": 1, "blend": 2, "depth": 1}
MAX_LOG = 4 * 1024 * 1024


def rotate(color, frame):
    rgb = tuple(color[:3])
    shift = frame % 3
    return rgb[shift:] + rgb[:shift] + (color[3],)


def cross(a, b, p):
    return (b[0]-a[0])*(p[1]-a[1]) - (b[1]-a[1])*(p[0]-a[0])


def expected(w, h, frame, case, x, y):
    """Return acceptable colors. Coordinates are pixel indices, not NDC."""
    clear = rotate((17, 34, 51, 255), frame)
    if case == "clear":
        return (clear,)
    if case == "blend":
        return (rotate((112, 64, 112, 255), frame),)
    if case == "texture":
        colors = ((240,32,16,255), (24,224,48,192), (40,64,208,128), (232,192,56,64))
        return (rotate(colors[(2 if y >= h//2 else 0) + (1 if x >= w//2 else 0)], frame),)
    if case == "depth":
        near = w//4 <= x < 3*w//4 and h//4 <= y < 3*h//4
        return (rotate((32,208,64,255) if near else (24,48,216,255), frame),)
    if case != "triangle":
        raise ValueError("unknown case")
    a, b, c = (w//8,h//8), (7*w//8,h//4), (w//3,7*h//8)
    p = (x+0.5, y+0.5)
    area = cross(a,b,c)
    edges = (cross(b,c,p), cross(c,a,p), cross(a,b,p))
    lengths = (math.dist(b,c), math.dist(c,a), math.dist(a,b))
    distances = tuple(e/l for e,l in zip(edges,lengths))
    interpolated = rotate(tuple(max(0,min(255,int(e/area*255+0.5))) for e in edges)+(255,), frame)
    if min(distances) > 0.1:
        return (interpolated,)
    if min(distances) < -0.1:
        return (clear,)
    return (clear, interpolated)  # limited raster-edge choice, still color checked


def reference(w, h, frame, case):
    """Synthetic CPU fixture, NOT an actual readback. For analyzer tests only."""
    return bytes(channel for y in range(h) for x in range(w)
                 for channel in expected(w,h,frame,case,x,y)[-1])


def verify_pixels(data, w, h, frame, case):
    if not (8 <= w <= 512 and 8 <= h <= 512 and 0 <= frame < 16 and case in CASES):
        raise ValueError("dimensions/frame/case outside bounds")
    if len(data) != w*h*4:
        raise ValueError(f"wrong RGBA size: {len(data)} != {w*h*4}")
    mismatch = 0
    samples = []
    edge_choices = 0
    max_error = 0
    tol = TOLERANCE[case]
    for y in range(h):
        for x in range(w):
            at = (y*w+x)*4
            got = tuple(data[at:at+4])
            choices = expected(w,h,frame,case,x,y)
            edge_choices += len(choices) > 1
            error = min(max(abs(a-b) for a,b in zip(got,color)) for color in choices)
            max_error = max(max_error,error)
            if error > tol:
                mismatch += 1
                if len(samples) < 8:
                    samples.append({"x":x,"y":y,"actual":got,"expected":choices,"error":error})
    return {"status":"PASS" if not mismatch else "FAIL", "pixels_checked":w*h,
            "mismatched_pixels":mismatch, "boundary_choice_pixels":edge_choices,
            "channel_tolerance":tol, "max_nearest_channel_error":max_error,
            "sha256":hashlib.sha256(data).hexdigest(), "examples":samples}


def bounded_file(directory, name, maximum):
    path = directory / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError(f"missing, symbolic or oversized file: {name}")
    return path.read_bytes()


def analyze_directory(directory, expected_frames=2, custom_size=None):
    directory = Path(directory)
    if not 1 <= expected_frames <= 16:
        raise ValueError("expected frames outside 1..16")
    sizes = (custom_size,) if custom_size else SIZES
    if any(not (8 <= w <= 512 and 8 <= h <= 512) for w,h in sizes):
        raise ValueError("dimensions outside 8..512")
    if sum(w*h for w,h in sizes)*expected_frames > 4*1024*1024:
        raise ValueError("pixel-frame budget exceeded")
    log = bounded_file(directory,"run.log",MAX_LOG).decode("utf-8",errors="strict")
    required = ["SCHEMA=z1-gles2-pbuffer-v1", "API_ERRORS=0", "PROBE_STATUS=READBACK_COMPLETE",
                "PIXEL_ORIGIN=bottom-left", "HARDWARE_ATTRIBUTION=UNPROVEN",
                f"FRAMES={expected_frames}", "REQUESTED_SIZES="+",".join(f"{w}x{h}" for w,h in sizes)]
    lines = log.splitlines()
    if any(item not in lines for item in required) or any(
            line.startswith(("EGL_ERROR", "GL_ERROR", "PROBE_STATUS=FAIL")) for line in lines):
        raise ValueError("missing completion metadata, size/frame mismatch or API failure")
    target = [line.partition("=")[2] for line in lines if line.startswith("BUILD_TARGET=")]
    if target not in (["android-api28-armv7"], ["host-reference-NOT-BOARD"]):
        raise ValueError("missing/ambiguous build target")
    for key in ("EGL_VENDOR", "EGL_VERSION", "EGL_EXTENSIONS", "GL_VENDOR", "GL_RENDERER",
                "GL_VERSION", "GL_SHADING_LANGUAGE_VERSION", "GL_EXTENSIONS"):
        if not any(line.startswith(key+"=") and len(line)>len(key)+1 for line in lines):
            raise ValueError(f"missing {key}")
    expected_rows = {f"{w}x{h}_f{f:02d}_{case}.rgba":(w,h,f,case)
                     for w,h in sizes for f in range(expected_frames) for case in CASES}
    text = bounded_file(directory,"manifest.tsv",256*1024).decode("ascii")
    rows = list(csv.reader(text.splitlines(), delimiter="\t"))
    if len(rows) != len(expected_rows):
        raise ValueError("incomplete/oversized manifest")
    seen = set()
    result = []
    for row in rows:
        if len(row)!=5:
            raise ValueError("malformed manifest row")
        name,sw,sh,sf,case = row
        if name not in expected_rows or name in seen:
            raise ValueError("unexpected or duplicate filename")
        w,h,f = int(sw),int(sh),int(sf)
        if (w,h,f,case) != expected_rows[name]:
            raise ValueError("manifest metadata disagrees with expected filename")
        seen.add(name)
        data = bounded_file(directory,name,w*h*4)
        result.append({"file":name,**verify_pixels(data,w,h,f,case)})
    if {p.name for p in directory.glob("*.rgba")} != set(expected_rows):
        raise ValueError("unexpected RGBA file set")
    maps_names = ["maps_before_egl.txt"] + [f"maps_{w}x{h}_{stage}.txt" for w,h in sizes
                                          for stage in ("current","after_readback")]
    maps = []
    for name in maps_names:
        raw = bounded_file(directory,name,MAX_LOG)
        paths = sorted({line.split(maxsplit=5)[5] for line in raw.decode("utf-8").splitlines()
                        if len(line.split(maxsplit=5))==6 and line.split(maxsplit=5)[5].startswith("/")})
        if not paths:
            raise ValueError(f"empty process mapping evidence: {name}")
        maps.append({"file":name,"sha256":hashlib.sha256(raw).hexdigest(),"mapped_paths":paths})
    passed = all(item["status"]=="PASS" for item in result)
    return {"pixel_status":"PASS" if passed else "FAIL", "build_target":target[0],
            "board_status":"NOT_RUN" if target[0].startswith("host-") else "SUPPLIED_CAPTURE_NOT_AUTHENTICATED",
            "hardware_status":"UNPROVEN", "full_pixel_coverage":True,
            "files_checked":len(result), "pixels_checked":sum(r["pixels_checked"] for r in result),
            "mismatched_pixels":sum(r["mismatched_pixels"] for r in result),
            "notes":["No renderer string, mapping list, hash or synthetic fixture alone proves hardware submission.",
                     "Map evidence is recorded separately; platform wrapper may select globally despite LD_LIBRARY_PATH.",
                     "Triangle edge choices are bounded to 0.1 pixel; no pixels are ignored.",
                     "PASS describes captured colors only; this tool cannot authenticate capture provenance."],
            "identity_lines":[line for line in lines if line.startswith(("EGL_","GL_","BUILD_TARGET=","BUFFER_BITS="))],
            "maps":maps,"results":result}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    parser.add_argument("--expected-frames",type=int,default=2)
    parser.add_argument("--custom-size",nargs=2,type=int,metavar=("WIDTH","HEIGHT"),help="explicitly verify a partial size suite")
    args=parser.parse_args()
    try:
        report=analyze_directory(args.directory,args.expected_frames,tuple(args.custom_size) if args.custom_size else None)
    except (OSError,ValueError,UnicodeError) as error:
        print(json.dumps({"pixel_status":"FAIL","hardware_status":"UNPROVEN","error":str(error)},indent=2))
        return 1
    print(json.dumps(report,indent=2))
    return 0 if report["pixel_status"]=="PASS" else 1

if __name__=="__main__":
    sys.exit(main())
