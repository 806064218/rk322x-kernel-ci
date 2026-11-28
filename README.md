# A 60-second silent freeze on RK3228/RK3228B

**What this is.** A record of a reproducible, completely silent system freeze on
RK3228/RK3228B SoCs, the root cause inside Rockchip's proprietary TEE firmware, and the
three-instruction change that removes it without giving up any working feature.

**What it is not.** Not a supported procedure, not a redistribution of the firmware, and
not legal advice. The change is described, not shipped: `docs/rebuild.md` shows how to
produce a fixed image from the distribution's own published file.

It is written as a reference, in case it saves someone else the same detour. The analysis
covers one specific firmware build, identified by its checksum rather than by its name.

**Who it applies to.** Any RK3228/RK3228B board running a mainline Linux system that loads
Rockchip's proprietary TEE binary (version string `51.1.0-333-gc9d95d1`) as the EL3 stage.
On Armbian's `rk322x` target this has been the default since July 2021, when the open-source
OP-TEE build was replaced by this binary for its DRAM frequency support. The build identifies
itself by that version string, which is embedded in the binary, and by the size and checksum
given further down.

## Status at the time of writing

| | |
|---|---|
| Analysed | November 2026 |
| SoC | RK3228B (RK3228 / RK3228B family) |
| TEE firmware | `51.1.0-333-gc9d95d1`, built 2018-08-17 |
| Kernel | mainline 6.18.63 and 6.6.22 |
| Distribution | Armbian 26.11 (trixie), `rk322x-box`; 24.2.5 on 6.6.22 |
| Bootloader | mainline u-boot 2022.04 and 2026.04 |
| Fix upstream | none, in firmware or distribution |

The firmware binary itself has not changed in the distribution since July 2021, so the version
above covers every release in that span.

This page therefore records an **open defect**, not history. If you are reading it later and the
binary you have no longer matches the checksum below, the defect has most likely been fixed
where it belongs; in that case read this as a record of how the failure was found, rather than
as a description of how the hardware behaves today.

---

## Symptoms this explains

If you arrived here looking for a freeze or a hang on this SoC, these are the exact behaviours
described below:

- an RK3228 or RK3228B TV box running Armbian freezes silently about 40 seconds after boot
  (41.5 s on the board measured here), with nothing in the kernel log, no panic, and no reboot;
- the same image is stable when it boots with a single CPU, and unstable with more than one;
- the box needs a full power cycle: it does not come back on its own, and the boot ID does not
  change;
- the failure follows a timer rather than load — bringing a CPU online later simply moves it to
  the next minute;
- nothing is logged anywhere, because the CPUs stop fetching instructions.

It does **not** explain random reboots, kernel oopses, storage errors, or freezes that only
happen under load. Those have other causes.

## The pattern

The board boots normally, all four cores come online, the system works for a while, and then
stops. The timing is the useful part: it is not "some time after boot", it lands on a strict
grid. With four cores present from the first instruction, the freeze happens at roughly 41.5
seconds of uptime. With a single core it never happens, and a core brought online after the
first grid point dies at the next one.

The spacing is 60 s; the offset is boot time. The timer is armed when control enters the TEE,
and on this boot path that happens about 18.5 s before the kernel starts counting uptime — the
loader still has to decompress and start the kernel after the TEE is resident. Hence the first
expiry lands at 60 - 18.5 = 41.5 s of uptime. Change the boot medium or the compression and the
first number moves, which is why field reports say "about a minute" or "about forty seconds"
while the spacing never changes.

## Root cause

The TEE binary loads the physical timer's reload value with `1,440,000,000`. The timer counts
down at the 24 MHz reference, so that is exactly 60.000000 seconds, and the handler reloads the
same value every time it runs. On every tick it tries to identify the SoC by reading a General
Register File scratch register that nothing in the boot chain writes — after power-on it holds
zero, and zero is not one of the three identifiers compared against. (The register itself is
read/write: the TRM's summary calls it write-only, its own bit-field page and our read-back test
disagree.) Identification therefore fails, the chip is
classified as unknown, and the handler takes its "unknown SoC" path, which parks the DRAM
interface:

1. it puts the DDR PLL into slow mode — the 24 MHz oscillator instead of the PLL output;
2. it gates both DDR PHY clock outputs.

A second routine on the same tick is cumulative rather than immediate: it adds 0 or 1 to the
PLL feedback divider and then closes the same gates again. On its own it would take four ticks
to break the link; together with the first routine there is no tick to spare.

Nothing ever un-parks it: the binary contains no code that writes those fields back. The CPUs
are executing from DRAM at that moment, so the next instruction fetch never returns. That is
the whole failure: a one-way door, no diagnostics, no recovery.

## What the handler does to the DRAM clock

Two operations, in two routines:

| Operation | Effect |
|---|---|
| park: slow mode, then gate both PHY clocks | DRAM clock falls from 660 MHz to 24 MHz; the PHY stops receiving clocks |
| trim: divider += 1, then gate again | +1.82 % per tick, monotonic, on an already gated link |

The trim is the subtle one. It looks like spread-spectrum dithering, but it is not: the
modulation rate is 1/60 Hz where spread spectrum needs tens of kHz, the table is `{0, +1}`
with a DC bias where spread spectrum is zero-mean, and the value is read back from the live
register and written again, so it can only ratchet upwards. It also explains why the index
never changes: 60 s at 24 MHz is 1,440,000,000 counts, an exact multiple of the table's 32
entries, so whichever entry the phase selects, it selects forever.

The ratchet is measurable. With the park call and the trim's gate write removed, leaving only
the divider walk, the board survives the first three ticks and dies on the fourth, at +7.3 % —
which is what a monotonic divider walk does to a DRAM link.

## The three edits

The patch is three instructions replaced by no-ops, six bytes in total. They are not three
steps of one sequence; they are the two entry points through which the timer handler can
reach the DRAM clock:

| # | Instruction | Where | What removing it does |
|---|---|---|---|
| 1 | `blx` into the park routine | timer handler | the handler cannot park the DRAM; the routine is untouched |
| 2 | `str` to the PLL feedback divider | trim routine | the divider stops being walked |
| 3 | `str` to the PHY clock gates | trim routine | the second gate write disappears with it |

The distinction matters for what the patch is allowed to touch. The park routine has two
callers: the timer handler, and the boot-time path that parks the DRAM when the SoC is not
recognised and then returns an error. Editing inside the routine would change that boot
path as well, so the *call* is what is removed — the routine keeps behaving as its author
intended for the caller that was meant to use it. The trim routine has exactly one caller,
the same handler, so its two stores can be removed with no effect anywhere else.

Everything else is left intact: the timer still fires, the identification still runs and
still fails, the per-lane PHY calibration and the handshake are untouched. Nothing stops
running as a result of the patch — the only difference is that the handler can no longer
reach the DRAM clock. That is what separates this from disabling the timer.

## Why it only happens here

Four conditions have to hold at once:

- the firmware contains both the 60 s timer **and** the PHY gate writes;
- the identification register reads back zero (nothing ever writes it), so the "unknown chip"
  path is taken;
- the identifier cached at boot is a real chip rather than the "not known yet" sentinel — if
  that one had failed too, the handler would skip both branches and do nothing at all;
- more than one core is online, so the single-core early-out does not trigger.

The third one is why "same firmware, no freeze" is a possible report: two failed identifications
silence the handler, and only one failed identification makes it destructive.

Two earlier vendor builds carry the timer but never write the PHY gates, and the build shipped
with the original Android firmware has no 60 s timer at all. The builds that added DRAM
frequency scaling are the ones that combine both — see the table in `docs/mechanism.md`.

## What is deliberately left alone

A rate change requested by a kernel driver goes through a separate interface in the
firmware, implemented by code that does not reference these three sites, so it is
unaffected (whether that interface is present in this particular build is a separate
question, noted in `docs/mechanism.md`). The handshake routine that coordinates a real
DRAM frequency change with the operating system is likewise untouched: it is only
reachable when the operating system participates, which on the board measured here it does
not.

The binary is not redistributed here. The change is described in terms of its three sites and
the exact build it applies to, so that it can be reproduced on a copy of that build: 702,396
bytes, MD5 `831bae354e224da7df319407eebf6365`, producing `a836a633bc8b7e43ed126ccf7b5a9ca6`.

Those two values are also how you tell what you are holding. The first means the defect is
present. The second means it has been fixed. Anything else is a different build, to which the
sites described here do not apply.

## Verification

Five patch runs, each changing one thing, plus an independent instruction-level model of the
handler that predicted every outcome before the run was made, plus one soak run. The runs that
established the timing are in the same document. Details are in `docs/experiments.md`.

| Change | Observed |
|---|---|
| periodic handler body disabled | survives beyond 320 s, four cores online |
| handshake disabled only | freezes at ~41.5 s |
| park call disabled only | freezes at ~41.5 s |
| park call and the trim's gate store removed | survives three ticks, freezes at the fourth (~221 s) |
| all three sites removed | survives beyond 320 s, four cores online, then boots a normal userland |
| all three sites removed, then one hour of normal operation | **3622 s, four cores online, 60 consecutive ticks, no anomaly** |

The soak run is the one that decides the question. It ran the real init, not a stand-in loop:
systemd reached its service start-up, and a background job wrote and read back a 128 MB buffer
every ten minutes against the DRAM that the handler was no longer touching. The last of those
passes completed at 3592 s, and the marker written at 3622 s records all four cores still
online.

Everything else was excluded by single-variable runs: kernel version and configuration,
bootloader, device tree, DRAM training parameters, supply voltage, CPU clock, temperature and
watchdog. None of them moved the failure point; only removing the clock writes did. The detail
is in `docs/experiments.md`.

## What this means in practice

Three things can be done about it, and they are not exclusive:

- **Patch the binary.** `scripts/patch-tee.py` reproduces the change from a copy you obtain
  yourself. The short route is one command on the distribution's published image, which
  expands it, patches every copy of the TEE inside it, and verifies the result; the same
  script also works on a bare blob or at packaging time. Details, and what it does not do,
  are in `docs/rebuild.md`.
- **Replace the binary.** A build that contains no DRAM scaling code has no defect to trigger —
  and no DRAM frequency scaling either.
- **Run on one core.** The handler returns before any DRAM work when only one core is online, so
  a single-core boot does not freeze. The cost is three quarters of the CPU, and it is the only
  workaround that needs no binary change at all.

The patch itself belongs to whoever ships the binary: a distribution that packages it can apply
the change at build time, on a file it already redistributes, without touching anything about
the running system.

This write-up does not recommend one over the others. It records what the failure is, so that
the choice can be made with the mechanism in view rather than from the symptom.

## Repository layout

```
README.md              this page
docs/mechanism.md      the handler, step by step; measured state; other builds
docs/experiments.md    the runs, one variable each, and what they ruled out
docs/model.md          the instruction-level model and its results
docs/rebuild.md        the fixed image: what it is, and how it is produced in one command
docs/methodology.md    the debugging method, generalised
scripts/patch-tee.py   the patcher described in rebuild.md
```

No binaries, no CI configuration. The single piece of executable code is the patcher, which
exists so that a build can be reproduced from the vendor's own file rather than trusted from
somebody's download.

## References

- Rockchip RK3228A/RK3228B Technical Reference Manual — the CRU chapter, for the PLL work-mode
  field, the PHY clock-gate bits, the PLL programming and lock procedure, and the reset values
  used above.
- Linux kernel, Rockchip RK3228 clock driver (`drivers/clk/rockchip/clk-rk3228.c`) — PLL rate
  table and the kernel's model of the same PLLs.
- Upstream OP-TEE, `core/arch/arm/plat-rockchip/` — the open-source counterpart for this
  platform, which contains no DRAM scaling code.
- Armbian's build repository, which ships the binary for its `rk322x` target
  (`packages/blobs/rockchip/rk322x_tee.bin`); the change that introduced it is public and dated
  July 2021.
- Public forum reports for this board family, from 2019 onwards, describing a shutdown after
  "exactly 60 seconds" — the same behaviour, without an explanation at the time.

## Notice

This is a failure-analysis write-up about a binary that is not distributed here. No licence
text accompanies that binary in the distribution, and the person who buys the hardware accepts
no terms for it. The change described is made to a copy on your own device; redistributing the
result needs rights this project does not have and cannot grant. Nothing here ships the binary,
a patched image, a key, or a way around a signature or any other protection mechanism, and none
of it is legal advice.

Text and script are MIT. The firmware binary is Rockchip's, is not distributed here, and is
not covered by that licence.
