# Mechanism

Everything below was read out of the vendor TEE binary itself (702,396 bytes, version string
`51.1.0-333-gc9d95d1`) and cross-checked against the SoC technical reference manual and
against register dumps taken from a running board.

## 1. The timer

The binary loads the physical timer's reload value with `1,440,000,000` and enables the
interrupt. The timer counts down at the 24 MHz reference — the chain behind that number is
set out in §2 — so the period is **60.000000 s**, and the handler reloads the same value at
the end of every run. There is exactly one write that enables the timer and none that
disables it, so the tick is unconditional and endless.

The 60 s period is not a timeout and not a watchdog. It is a scan interval.

## 2. Measured state

The values below were read from the affected board while it was running normally, before any
patch. They are what the handler operates on, and they are what makes the failure quantitative
rather than hand-waving.

| Register | Value | Meaning |
|---|---|---|
| PLL work-mode register | `0x1111` | all four PLLs in normal mode, i.e. running from their PLL |
| DDR PLL CON0 | `0x2037` | post divider 1 = 2, feedback divider = 55 |
| DDR PLL CON1 | `0x1441` | reference divider = 1, post divider 2 = 1, integer mode, locked |
| DDR clock select | `0x0000` | source = DDR PLL, divider 1:1 |
| PHY clock gate register | `0x4000` | GPU gate only — **both PHY clocks running** |
| GRF scratch register (OS_REG4) | `0x00000000` | chip id read each tick; zero because nothing writes it (§3) |

From those, the DDR PLL runs at 24 MHz × 55 / (1 × 2 × 1) = **660 MHz**, and one step of the
feedback divider is 24 / 2 = **12 MHz**, or +1.82 %.

### How the 24 MHz and 660 MHz figures are established

Neither number was measured on the clock with an instrument; both are a chain of documented
facts, plus observations that would fail if the chain were wrong.

- The clock chapter of the TRM states that the SoC has a single crystal input of 24 MHz, and
  that every PLL can only receive that. There is no alternative reference to select.
- The DRAM clock selector measured `0x0000`: source = DDR PLL, divider 1:1. The DRAM clock
  therefore equals the PLL output, with no further division.
- The PLL registers measured `CON0 = 0x2037` and `CON1 = 0x1441`. Decoded with the TRM's own
  field layout — `fbdiv` bits 11:0, `postdiv1` 14:12, `postdiv2` 8:6, `refdiv` 5:0 — that is
  55, 2, 1, 1, and `FOUTPOSTDIV = 24 MHz × 55 / 1 / 2 / 1 = 660 MHz`. One step of `fbdiv` is
  12 MHz, or +1.82 %.
- Corroboration from outside the binary: the DDR init blob shipped beside this TEE in the
  same directory is named `rk322x_ddr3_660MHz_ddr2_330MHz_v1.10.bin`.
- Corroboration from the defect itself: the handler reloads 1,440,000,000 counts and the
  measured grid is 60.00 s apart (41.5, 101.5, 161.5, 221.5 s in successive runs, and 60
  ticks inside 3,622 s). At 25 MHz the same constant would give 57.6 s, which is not what
  was observed.

What is *not* claimed: that the DRAM clock was ever seen on a scope. The slow-mode value
follows from the TRM's definition of the field — "0: slow mode, clock from external 24 MHz
OSC" — plus the 1:1 selector, not from a frequency measurement.

## 3. The handler

In pseudo-code, each tick does this:

```
if chip_id == UNKNOWN_SENTINEL:                    # sentinel only; UNKNOWN does not re-read
    ungate the GRF clock
    chip_id = read(GRF_OS_REG4)
    chip_id = 3228A / 3228B / 3229 / UNKNOWN      # by comparing against three constants
    store chip_id

if cores_online() == 1:                            # early-out
    chip_id = UNKNOWN_SENTINEL
    skip everything below

if board_id == UNKNOWN or (chip_id == UNKNOWN and board_id != UNKNOWN_SENTINEL):
    park_dram()                                    # function A

if chip_id != board_id and neither is UNKNOWN_SENTINEL:
    trim_and_park()                                # function B

if board_id == 3228A:
    if service_cpu_bits() == 0b10:
        handshake()                                # function C
    if cpu_clock_mhz() > 1300 or already_flagged:  # one-shot
        flag = 1
        trim_and_park()                            # function B again, same tick
elif board_id == 3228B:
    if service_cpu_bits() == 0b10:
        handshake()                                # function C

reload timer with 60 s
```

Four details matter.

**The runtime identification reads a register nobody writes.** `GRF_OS_REG4` (`0x05d8`) is an
ordinary read/write scratch register. That is measured, not assumed: a payload of ours wrote a
microsecond timestamp to it — `0x004C921F`, with `0xE5` in the next register as the experiment
tag — and a later register dump read both values back, after a watchdog reset. So the register
holds what is written to it, and it keeps that value until power is removed. The TRM is of two
minds about the attribute (summary table `W`, per-register page `RW`); the hardware agrees
with `RW`.

That does not help the handler, because no code in the TEE or in the boot chain ever puts a chip
identifier there. The address literal occurs exactly once in the whole binary, at the read site.
After power-on the register therefore holds zero, which is not one of the three constants the
handler compares against. The comparisons fail, and `chip_id` becomes UNKNOWN on the very first
tick. The handler then treats a perfectly well-known SoC as an unknown one.

**The single-core early-out is what saves a one-core boot.** With one core online the handler
overwrites the identifier with the sentinel and returns before any of the DRAM work. That is why
a board limited to a single core never freezes, and why the failure needs at least two cores.

**Two failures are not the same as one.** The board identifier is cached once at boot by the
same identification routine. If that one succeeded, the runtime read fails, the chip looks
unknown, and the result is destructive. If the boot-time read failed as well, both values are
the sentinel, the first two branches are skipped, and the handler is inert — it still ticks, it
just does nothing. That asymmetry is why the same firmware can be harmless on one board and
fatal on another.

**The last two branches are per-variant, and they are not symmetric.** The handshake and the
second trim exist only on the 3228A and 3228B paths; a 3229 falls straight through to the timer
reload. This matters for reading the decision table in `model.md`: the `3229` column never
carries an `H`, and the `H` entries in the 3228 rows disappear entirely when the service-CPU
status bits do not read `0b10` — which is the case on the board measured here.

## 4. Function A: park

```
delay(500 us)
CRU_MODE_CON = 0x00100000       # upper half = write mask, lower half = data
delay(500 us)
CRU_CLKGATE7 = 0x00030003
```

The clock registers are written in masked form: bits 31:16 select which of bits 15:0 are
being written — mask bit 0 means "leave alone", 1 means "write this bit" — and the data sits
in the low half. `0x00100000` is therefore not "the value 1,048,576 stored in the register";
it is "write bit 4, to zero", which clears `dpll_work_mode` and leaves every other field
alone. The same encoding makes `0x00030003` "write bits 1:0, both to one", i.e. set both PHY
clock gates. Reading the constants without the mask semantics gives the wrong picture twice
over: the write looks like a large number, and it looks like it writes many fields.

The TRM names the field's two states *slow mode* ("clock from external 24 MHz OSC") and
*normal mode* ("clock from PLL output"). It resets to slow mode; the bootloader is what
selects normal mode so that DRAM runs from the PLL at all. Function A therefore does not
"scale down" — it returns the DRAM clock to its power-on state, 24 MHz, a factor of 27.5
below the 660 MHz the PLL was producing.

The two gate bits are documented as "when HIGH, disable clock". Both PHY clocks stop.

Function A contains no restore path. It is a one-way door by construction.

## 5. Function B: trim and park

```
delay(500 us)
i = CNTPCT & 0x1f
delta = table[i]                       # 32 entries, all of them 0 or 1
value = CRU_DPLL_CON0                  # read the live register
CRU_DPLL_CON0 = (value + delta) | write_mask(fbdiv)
CRU_CLKGATE7 = both gates on
```

The measurement that makes this fatal rather than cosmetic: **60 s at 24 MHz is
1,440,000,000 counts, which is an exact multiple of 32.** The index never changes between
ticks. Whichever entry the phase happens to select, it selects forever.

Combined with the read-modify-write, the feedback divider therefore moves in one direction
only. On the measured board the PLL runs at 660 MHz (`fbdiv = 55`, `refdiv = 1`,
`postdiv1 = 2`, `postdiv2 = 1`, divider 1:1 to the PHY), so one step is 12 MHz, or +1.82 %.
Three steps are survivable; the fourth is not, and that is exactly where a partially patched
binary died. On a 3228A the second branch can call this function twice in one tick, which only
makes it worse.

## 6. Why nothing recovers

A complete DRAM frequency change is a four-step sequence: park the PHY, change the PLL, wait
for lock, un-park. The blob implements the first two and never the last two. A sweep for
stores to the three fields that matter finds six store instructions in total:

- one to the PLL work-mode field (slow mode), in the park routine;
- three to the PHY gate register — one in the park routine, one in the handshake routine,
  one in the trim routine — every one of them "gate on";
- one to the PLL feedback divider, in the trim routine;
- one to a GRF clock gate, used to un-gate the register file so the identification can be read.

There is no write that sets the work-mode field back, and no write that clears the PHY gates,
anywhere in the binary.

Read that way, the three edits are not three steps of one routine, they are the two places
where the timer handler can reach the DRAM clock. The park routine has two callers — the timer
handler, and the boot path that parks the DRAM when the SoC is not recognised and then returns
an error. Editing inside it would change that error path too, so the *call* is what is removed:
the routine keeps doing what its author intended for the caller that was meant to use it. The
trim routine has exactly one caller, the same handler, so its two stores can be removed with
no effect anywhere else. The handshake routine keeps its gate write: it is only reachable when
the operating system participates in a frequency change, status bits that do not read `0b10`
on the board measured here.

The shape of the defect follows from that. The initialisation path parks the DRAM when
identification fails at boot and returns an error — "this SoC is not recognised, park it and
give up". The 60 s handler reuses that same error path as if it were a power-management
feature.

## 7. What the vendor intended

Rockchip's DRAM frequency scaling assumes an operating system that participates: the kernel
tells EL3 that the DRAM controller is idle, EL3 parks the PHY, changes the PLL, un-parks it and
reports completion over a shared-memory SMC interface. On a mainline kernel none of that
coordination exists, so the autonomous 60 s scan runs against a live, busy DRAM interface.

The open-source TEE for this platform contains no DRAM scaling code at all, which is why it
never shows this failure — it simply does not do the thing that is broken.

## 8. Other builds of the same firmware

Seven TEE images from the same vendor family were scanned for the two markers that have to be
present together: the 60 s timer constant, and the store that gates the PHY clocks.

| Build | 60 s timer | PHY gate writes | Verdict |
|---|---|---|---|
| upstream 3.7.0 / 4.5.0 (source builds) | no | no | no freeze |
| vendor 1.0.1-65 / -72 | yes | **no** | no freeze |
| vendor 1.1.0-120 (original Android firmware) | **no** | no | no freeze |
| vendor 1.1.0-297 | yes | yes | freeze |
| vendor 51.1.0-333 (the one shipped for this target) | yes | yes | freeze (measured) |

That is the answer to "why only some boards": the timer alone is harmless, and the gate writes
alone do not exist in the older builds. Only the later builds combine the two. Only the last row
was run on hardware; the others were classified statically, and the 1.1.0-297 code was also
executed in the model, where its park and trim functions produced the same register writes.

## 9. What remains unknown

Two things, stated rather than guessed:

- The value the identification register is meant to hold, and who was expected to write it.
  Nothing in this platform's boot chain writes it, and no public document describes it.
- Whether the DRAM frequency interface that motivated shipping this binary was ever functional
  here. The kernel driver uses two call numbers that do not appear in the binary. That is a
  static observation and has not been confirmed on hardware.

## 10. Terms

- **PLL** — the on-chip frequency multiplier that derives a clock from the 24 MHz reference.
- **DDR PLL** — the PLL dedicated to the DRAM interface; its output is the DRAM clock.
- **Work mode** — a per-PLL mux bit: normal (run from the PLL) or slow (run directly from the
  24 MHz oscillator). It resets to slow, and the bootloader is what selects normal.
- **PHY** — the analogue front end between the DRAM controller and the DRAM chips. It needs its
  own clocks, referred to here as 1x and 4x.
- **Clock gate** — a bit that stops a clock tree. "Gate enabled" means the clock is off, which
  is the opposite of how the phrase reads at first glance.
- **DFS** — dynamic frequency scaling: changing a clock rate while the system runs.
- **Sentinel** — a reserved value used as a state marker rather than as real data; here, "this
  chip has already been identified" and "this chip is unknown".
- **Board identifier** — the value cached at boot from eFuse or the bootloader, as opposed to
  the value re-read at runtime by the periodic handler.
