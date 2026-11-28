# The fixed image

## What a fixed image is

The fix is not a program and not a build. It is six bytes in a file the distribution already
ships. A fixed image is therefore an ordinary distribution image — the same partitions, kernel,
bootloader and root filesystem, with those six bytes changed. Nothing else is installed, and no
vendor tool is needed.

What is reproduced is the change itself, byte for byte, from a copy of the vendor's own file.
That is what the script does, and it is why this repository holds no binary: the input comes from
the project that ships it, the output is verified against a fixed checksum, and a third party can
confirm a build was made correctly without trusting whoever made it.

## The whole procedure

Take the release the analysis was made on, and patch it. Nothing else is required: the script
is one file, Python 3 only, no third-party modules. Any download tool will do in place of `curl`.

```
curl -LO https://github.com/armbian/community/releases/download/26.11.0-trunk.130/Armbian_community_26.11.0-trunk.130_Rk322x-box_trixie_current_6.18.63_minimal.img.xz
curl -LO https://raw.githubusercontent.com/806064218/rk3228-60s-freeze/main/scripts/patch-tee.py
python3 patch-tee.py image Armbian_community_26.11.0-trunk.130_Rk322x-box_trixie_current_6.18.63_minimal.img.xz --out rk322x-tee-fixed.img --write
```

The download is 332,886,452 bytes. The release publishes a SHA-256 for it; checking that before
running anything costs one line and catches a truncated or substituted file:

```
echo "fb31b067872aa0b90abbd09925fdfa80b5f1a6d1907f62bef136fcc09983ffd1  Armbian_community_26.11.0-trunk.130_Rk322x-box_trixie_current_6.18.63_minimal.img.xz" | sha256sum -c -
```

The third line expands the image, patches every copy of the TEE it contains, and prints what it
did (abridged here — the file name is printed in full):

```
  file     : Armbian_..._minimal.img.xz (xz compressed)
  expanding: rk322x-tee-fixed.img
  expanded : 1463812096 bytes  md5 9d5f76cebeb48447c5b79a6d18b3f223
  copy     : blob at 0xd8a00 (anchor 0x118474)  original
  copy     : blob at 0x368d0a00 (anchor 0x36910474)  original

  written  : three no-ops in 2 copy/copies
  verified : segment at 0xd8a00  md5 a836a633bc8b7e43ed126ccf7b5a9ca6  ok
  verified : segment at 0x368d0a00  md5 a836a633bc8b7e43ed126ccf7b5a9ca6  ok
  output   : rk322x-tee-fixed.img (1463812096 bytes)
```

`rk322x-tee-fixed.img` is a normal Armbian image. Write it with the tool you would have used for
the original. There is no extra step, nothing to install on the board, and no change to the way
it boots.

The image as a whole has no checksum that survives across releases, but this pair is what was
measured here, and it is the cheapest way to tell whether your starting file is the same one:

| | Size | MD5 |
|---|---|---|
| the published image, expanded | 1,463,812,096 | `9d5f76cebeb48447c5b79a6d18b3f223` |
| after patching | 1,463,812,096 | `dbf6fdb1b9ed02929783ad63f4853bbb` |

If the input does not match the first row, it is a different release or a different variant. The
run will still refuse unless the TEE inside is exactly the build described here, which is the
check that decides.

## Why it works on an image it has never seen

No offset is hard-coded. The blob is found by a version string that occurs exactly once per copy
of it, at a fixed distance from the start of the blob; the script computes the start from that
match, then verifies all 702,396 bytes against a known checksum before touching a byte. Move the
blob, change the bootloader layout, change the partition table: the anchor still works. Change the
blob: the checksum stops the run.

**Two copies, not one.** On both images tested, the TEE appears twice: once in the boot area, and
once inside the root filesystem as an ordinary file. Both are checked and both are patched.
Patching only the boot area would leave that second copy behind, and anything that later wrote it
back to the boot area would restore the defect.

**The offsets differ between releases** — `0xd8a00` here, `0xc03c0` on the 6.6.22 image used
during the analysis — which is exactly why the anchor is a string and not a constant.

## What the three edits are

| # | Instruction | Routine | What removing it does |
|---|---|---|---|
| 1 | `blx` | timer handler | the handler no longer calls the park routine |
| 2 | `str` | trim routine | the feedback-divider walk stops |
| 3 | `str` | trim routine | the second PHY gate write disappears with it |

The park routine itself is left alone. It has a second caller — the boot-time path that parks the
DRAM when the SoC is not recognised — and that behaviour stays as its author wrote it. The trim
routine has no other caller, so its two stores can go. `README.md` explains why these three and
not the timer; `docs/mechanism.md` has the register-level detail.

## How we know the output is right

Three independent checks, in increasing strength:

- the script compares every byte it is about to edit, and the checksum of the whole blob before
  and after;
- the patched segment must hash to `a836a633…`, which it prints and re-reads from the file;
- the same procedure run on the vendor's unpatched boot image (the 1.6 MB IDB) produced a file
  **byte-for-byte identical** to the one that ran on hardware for 3,622 seconds across 60 expiries
  of the timer (`1accf79c5f40186386b7e28acc371947`, zero differing bytes).

The last one is the reason to trust the first two: the change is small enough that "identical"
is a complete statement, not a spot check.

## If you already have the pieces

**A bare blob.** If you obtained the 702,396-byte blob some other way, `blob` mode checks its size
and checksum, keeps a `.orig` copy and patches it in place. `fetch` mode downloads the same file
from the distribution's build repository at a pinned commit, without the image around it.

**At packaging time.** A distribution that ships the blob can replace the file in its build tree
before the boot images are assembled:

```
patch-tee.py fetch --write
cp rk322x_tee.patched.bin <build-tree>/packages/blobs/rockchip/rk322x_tee.bin
```

That is the same shape as the one-byte `dd` patch the distribution already applies to its
bootloader during packaging: a build-time edit to a file it redistributes, with an assertion that
stops the build if the input is not what was expected. It is also the route that reaches
users without asking each of them to patch their own copy, and the one that needs the
distributor's decision rather than this document's.

## Limits

- **One build only.** A different TEE fails the checksum and the script stops. That is deliberate:
  the three offsets mean nothing in another binary.
- **It removes an effect, not code.** The DRAM scaling routines are still in the firmware; the
  timer no longer reaches them. A caller that drives a frequency change on purpose is unaffected,
  and so is any future path that calls the park routine directly.
- **The package copy changes.** After patching, the file inside the root filesystem no longer
  matches the md5 its package recorded. Nothing reads that at boot; an integrity checker will
  notice.
- **Any `.sha` shipped beside the image no longer matches**, because the image changed. That is
  expected: the file you end up with is not the file that was signed.

## Risks, stated plainly

- **This modifies EL3 firmware.** A wrong image written to the wrong place does not boot, and
  recovery needs the SoC's mask-ROM mode. Verify the segment checksum before writing anything to
  a device.
- **The patch removes a power-management action.** DRAM frequency scaling driven from EL3 will no
  longer change the clock. On the systems measured here nothing was using it, but that is a
  statement about those systems, not a guarantee about yours.
- **`--write` is irreversible in the sense that matters.** It overwrites the target, or the copy
  named by `--out`. Work on a copy when you are not sure; only `blob` mode keeps a `.orig`.

## Legal

`README.md` has the position in one paragraph. In short: the change is made to a copy on your
own device, distributing the result needs rights this project does not have, and nothing here
ships the binary or a patched image.
