#!/usr/bin/env python3
"""
patch-tee.py - produce a patched rk322x TEE from an official copy.

The reproducible artefact is the byte-level change. This script applies it to a copy you
obtain yourself, and refuses to touch anything it cannot identify exactly.

Three modes:

    patch-tee.py fetch [--write]                     download the official blob, verify, patch
    patch-tee.py blob  <file> [--write]              patch a stand-alone 702,396-byte blob
    patch-tee.py image <file> [--out <path>] [--write]
                                                     locate the blob inside an image and patch it

`image` accepts a raw image or a compressed one (.xz, as distributed), and `--out` writes a
patched copy instead of modifying the input. An image that ships the blob more than once has
every copy checked and every original copy patched.

Nothing is modified without --write. Every step asserts; on any mismatch the script stops
and says why. Output is always verified against a known checksum.

Why a checksum and not an offset: the blob is located inside an image by a version string
that occurs exactly once per copy of the blob, so the same code works for an image, an IDB, or
a bare blob even when the surrounding layout differs between releases.
"""

import hashlib
import lzma
import mmap
import os
import shutil
import struct
import sys

BLOB_SIZE = 702396
MD5_ORIGINAL = "831bae354e224da7df319407eebf6365"
MD5_PATCHED = "a836a633bc8b7e43ed126ccf7b5a9ca6"

# The version string occurs exactly once in the blob, and once per copy of the blob in a file.
# Its offset inside the blob is fixed, so the blob start is (anchor - ANCHOR_OFFSET).
ANCHOR = b"51.1.0-333"
ANCHOR_OFFSET = 0x3FA74

# Three two-byte instructions, relative to the start of the blob.
SITES = [
    (0x1A764, b"\x98\x47", "call into the park routine (work mode + PHY gates)"),
    (0x1A4F4, b"\x0b\x60", "store that walks the PLL feedback divider"),
    (0x1A4F8, b"\x1a\x60", "store that gates both DDR PHY clocks"),
]
NOP = b"\x00\xbf"

# Pinned to the commit that introduced this blob, so the download cannot drift.
PINNED_COMMIT = "baf9815f8b72f3f3c97f79e481cfa83768378e76"
URLS = [
    "https://raw.githubusercontent.com/armbian/build/%s/packages/blobs/rockchip/rk322x_tee.bin" % PINNED_COMMIT,
    "https://api.github.com/repos/armbian/build/contents/packages/blobs/rockchip/rk322x_tee.bin?ref=%s" % PINNED_COMMIT,
]

DEFAULT_OUT = "rk322x_tee.patched.bin"


def md5(data):
    return hashlib.md5(data).hexdigest()


def die(msg):
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(1)


def describe(data):
    """Classify a 702,396-byte candidate without modifying it."""
    got = md5(data)
    if got == MD5_PATCHED:
        return "patched"
    if got == MD5_ORIGINAL:
        return "original"
    return "unknown"


def check_sites(data):
    """Every site must hold the expected two bytes before anything is written."""
    for off, want, what in SITES:
        got = data[off:off + 2]
        if got != want:
            die("byte check failed at 0x%06x (%s): expected %s, found %s"
                % (off, what, want.hex(" "), got.hex(" ")))
    return True


def apply(data):
    """Return a patched copy after verifying both ends."""
    check_sites(data)
    out = bytearray(data)
    for off, _want, _what in SITES:
        out[off:off + 2] = NOP
    out = bytes(out)
    if md5(out) != MD5_PATCHED:
        die("post-patch checksum mismatch: %s (expected %s)" % (md5(out), MD5_PATCHED))
    return out


def report(state, where):
    print("  location : %s" % where)
    print("  state    : %s" % state)
    if state == "patched":
        print("  nothing to do - this copy already carries the fix")
    elif state == "original":
        print("  carries the defect; the three sites are present and match")
    else:
        print("  unrecognised build - this script only handles the version named in the write-up")
    return state


# ---------------------------------------------------------------- modes

def mode_fetch(write):
    data = None
    for url in URLS:
        try:
            import urllib.request
            req = urllib.request.Request(url)
            req.add_header("Accept", "application/vnd.github.raw")
            req.add_header("User-Agent", "patch-tee")
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            print("  fetched  : %s (%d bytes)" % (url.split("?")[0], len(data)))
            break
        except Exception as exc:
            print("  fetch failed from %s: %s" % (url.split("?")[0], exc))
    if data is None:
        die("could not download the blob; fetch it by hand and use 'blob <file>' instead")
    if len(data) != BLOB_SIZE:
        die("downloaded %d bytes, expected %d - the pinned commit may have moved"
            % (len(data), BLOB_SIZE))
    state = report(describe(data), "download")
    if state != "original":
        die("downloaded copy is not the expected original")
    patched = apply(data)
    if not write:
        print("\n  dry run - would write %s (%d bytes, md5 %s)" % (DEFAULT_OUT, len(patched), md5(patched)))
        print("  re-run with --write to create it")
        return
    with open(DEFAULT_OUT, "wb") as f:
        f.write(patched)
    print("\n  wrote %s (%d bytes, md5 %s)" % (DEFAULT_OUT, len(patched), md5(patched)))


def mode_blob(path, write):
    if not os.path.isfile(path):
        die("no such file: %s" % path)
    data = open(path, "rb").read()
    if len(data) != BLOB_SIZE:
        die("%s is %d bytes; a bare blob is %d - use 'image' mode for a whole image"
            % (path, len(data), BLOB_SIZE))
    state = report(describe(data), path)
    if state == "patched":
        return
    if state != "original":
        die("not the build this write-up describes")
    check_sites(data)
    patched = apply(data)
    if not write:
        print("\n  dry run - would rewrite %s in place" % path)
        print("  re-run with --write to apply")
        return
    backup = path + ".orig"
    if not os.path.exists(backup):
        open(backup, "wb").write(data)
        print("  backup   : %s" % backup)
    open(path, "wb").write(patched)
    back = open(path, "rb").read()
    print("  written  : %s  md5 %s  %s" % (path, md5(back), "verified" if md5(back) == MD5_PATCHED else "MISMATCH"))


def is_xz(path):
    if path.lower().endswith(".xz"):
        return True
    with open(path, "rb") as f:
        return f.read(6) == b"\xfd7zXZ\x00"


def decompress(src, dst):
    """Stream an .xz image to dst, returning (bytes written, md5 of the result)."""
    h = hashlib.md5()
    n = 0
    with lzma.open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            block = fin.read(1 << 20)
            if not block:
                break
            h.update(block)
            fout.write(block)
            n += len(block)
    return n, h.hexdigest()


def candidates(mm, size):
    """Every place in the file where a copy of this build starts."""
    found = []
    pos = mm.find(ANCHOR)
    while pos >= 0:
        start = pos - ANCHOR_OFFSET
        if 0 <= start and start + BLOB_SIZE <= size:
            data = mm[start:start + BLOB_SIZE]
            found.append((start, pos, describe(data)))
        else:
            found.append((-1, pos, "outside"))
        pos = mm.find(ANCHOR, pos + 1)
    return found


def patch_in_place(path, starts):
    with open(path, "r+b") as f:
        for start in starts:
            for off, _want, _what in SITES:
                f.seek(start + off)
                f.write(NOP)
        f.flush()
        os.fsync(f.fileno())


def verify_file(path, starts):
    ok = True
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        for start in starts:
            got = md5(mm[start:start + BLOB_SIZE])
            print("  verified : segment at 0x%x  md5 %s  %s"
                  % (start, got, "ok" if got == MD5_PATCHED else "MISMATCH"))
            ok = ok and got == MD5_PATCHED
        mm.close()
    return ok


def mode_image(path, write, out):
    if not os.path.isfile(path):
        die("no such file: %s" % path)

    created = None
    if is_xz(path):
        print("  file     : %s (xz compressed)" % path)
        if not write:
            # Expanding is not free, and a dry run must not leave a 1.4 GB file behind.
            print("  note     : a compressed image has to be expanded before it can be inspected.")
            print("             run again with --out <name.img> --write to produce the patched image")
            return
        target = out or (path[:-3] if path.lower().endswith(".xz") else path + ".img")
        if os.path.abspath(target) == os.path.abspath(path):
            die("an .xz image cannot be patched in place - pass --out <name.img>")
        print("  expanding: %s" % target)
        n, digest = decompress(path, target)
        print("  expanded : %d bytes  md5 %s" % (n, digest))
        work = target
        created = target
    else:
        size = os.path.getsize(path)
        print("  file     : %s (%d bytes)" % (path, size))
        work = path
        if out and os.path.abspath(out) != os.path.abspath(path):
            if write:
                print("  copying  : %s" % out)
                shutil.copyfile(path, out)
                work = out
                created = out
            else:
                print("  dry run  : would copy to %s and patch the copy" % out)

    temp = work
    size = os.path.getsize(temp)
    with open(temp, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        found = candidates(mm, size)
        mm.close()

    if not found:
        if created and os.path.exists(created):
            os.unlink(created)
        die("version string not found - not a file that carries this build")

    known = [(s, p, st) for s, p, st in found if st in ("original", "patched")]
    for start, pos, state in found:
        if state == "outside":
            print("  anchor   : 0x%x - too close to the start to be this blob, ignored" % pos)
        elif state == "unknown":
            print("  anchor   : 0x%x - the bytes there are not this build, ignored" % pos)
        else:
            print("  copy     : blob at 0x%x (anchor 0x%x)  %s" % (start, pos, state))

    if not known:
        if created and os.path.exists(created):
            os.unlink(created)
        die("no copy of this build found; the bytes found are some other firmware")

    todo = [s for s, _p, st in known if st == "original"]
    if not todo:
        print("\n  nothing to do - every copy already carries the fix")
        return

    for start in todo:
        with open(temp, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            check_sites(mm[start:start + BLOB_SIZE])
            mm.close()

    if not write:
        print("\n  dry run - %d copy/copies to patch: %s"
              % (len(todo), ", ".join("0x%x" % s for s in todo)))
        for start in todo:
            print("             would write three no-ops at 0x%x, 0x%x, 0x%x"
                  % (start + SITES[0][0], start + SITES[1][0], start + SITES[2][0]))
        print("  re-run with --write to apply")
        if created and os.path.exists(created):
            os.unlink(created)
        return

    patch_in_place(temp, todo)
    print("\n  written  : three no-ops in %d copy/copies" % len(todo))
    if not verify_file(temp, todo):
        die("verification failed after writing")
    if created:
        print("  output   : %s (%d bytes)" % (created, os.path.getsize(created)))


def main(argv):
    args = []
    out = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            i += 1
            if i >= len(argv):
                die("--out needs a path")
            out = argv[i]
        elif a.startswith("--"):
            pass
        else:
            args.append(a)
        i += 1
    write = "--write" in argv

    if not args:
        print(__doc__)
        return 0

    mode = args[0]
    if mode == "fetch":
        mode_fetch(write)
    elif mode == "blob" and len(args) == 2:
        mode_blob(args[1], write)
    elif mode == "image" and len(args) == 2:
        mode_image(args[1], write, out)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
