# The instruction-level model

## Why it exists

Reading a few hundred instructions by hand is slow, and with bit-fields and write masks it is
quietly wrong: the mistake does not announce itself, it produces a plausible story. So the
handler was executed instead of read. The firmware segment is mapped at its link address inside
an instruction-level ARM emulator, the memory-mapped register pages are backed by plain
variables that record every write, and the handful of external calls are stubbed out.

The point is not to "simulate the SoC". It is to answer a narrower question — *which registers
can this code write, and with what values* — mechanically rather than by eye.

## What is modelled

- the firmware segment, unmodified, at its link address, executed as Thumb-2;
- the clock-controller page, the register-file page and the service-CPU status word;
- write semantics: a 16-bit write mask in the upper half of the word, data in the lower half,
  and reads that return only the data half;
- the chip-id register reading zero (nothing writes it; the hardware is read/write, see
  `mechanism.md` §3);
- the free-running counter value that feeds the divide-by-32 table lookup.

## What is stubbed

- the delay routine — no time passes, calls return immediately;
- the PHY per-lane calibration — returns immediately, no lanes are modelled;
- the core count — a parameter, swept over 0 to 4;
- the handshake — returns a value that is treated as a parameter, not as a measurement.

## Result 1: the park function has no inputs

Whatever the SoC, the board identifier or the core count, the function emits exactly two writes:
the PLL work-mode field is cleared, and both PHY clock gates are set. There is no branch and no
input that changes the outcome.

## Result 2: the trim table is thirty-two zeros and ones

The table indexed by the counter's low bits is not a spread-spectrum profile. Every entry is
either 0 or 1, so the only possible effect is "add nothing" or "add one".

## Result 3: the index can never change

60 s at 24 MHz is 1,440,000,000 counts, which is 32 × 45,000,000 with a remainder of zero. The
index is therefore a constant of the system: whichever entry the phase selects at boot, it
selects for every subsequent tick.

Combined with a read-modify-write of the divider, that makes the trim monotonic. The model
reproduces the measured behaviour exactly: a divider that walks one step per minute, surviving
three steps and failing on the fourth.

Without the write-mask semantics this is invisible — the mask bits appear to be stored in the
register, so the value looks stable. That single detail is the difference between "harmless
dither" and "+7 % in four minutes".

## Result 4: the handler's decision table

Sweeping the two cached identifiers and the core count gives the following. `A` is the park
function, `B` is the trim function, `H` is the handshake.

| board id ↓ / chip id → | sentinel | unknown | 3228A | 3228B | 3229 |
|---|---|---|---|---|---|
| **sentinel** | — | — | — | — | — |
| **unknown** | A | A | AB | AB | AB |
| **3228A** | ABH | ABH | H | BH | BH |
| **3228B** | ABH | ABH | BH | H | BH |
| **3229** | AB | AB | B | B | — |

Two things fall out of it:

- the "sentinel" row and column are inert: a forced sentinel suppresses both functions. The
  single-core early-out uses exactly that — it overwrites the identifier with the sentinel and
  jumps past both calls;
- only the diagonal — chip correctly identified and equal to the board identifier — skips both
  functions. Every off-diagonal combination runs at least one of them.

`H` appears only on the two 3228 rows, and only when the service-CPU status bits read `0b10`
— it is absent from the whole `3229` row, and from every row once those bits differ. On the
measured board they do not read `0b10`. That was tested rather than assumed: a run with the
handshake removed altogether froze at exactly the same point as the unmodified build.

## Result 5: predictions

The model was asked what each candidate change would do before the change was built:

| Change | Predicted | Observed |
|---|---|---|
| whole handler body disabled | no writes, survives | survived |
| handshake disabled | A and B still run, dies at the first tick | died at ~41.5 s |
| park call disabled | B still gates, dies at the first tick | died at ~41.5 s |
| park call and gate removed | divider walks, dies at the fourth tick | died at ~221 s |
| all three sites removed | no writes, survives | survived |

Five for five, including the two counter-intuitive ones.

The last row was then carried further than a prediction: the same build ran for an hour under
the real init with periodic memory pressure, covering 60 expiries of the timer. The model's
claim — that with the three sites gone there is nothing left in the handler that touches the
DRAM clock — is a claim about every tick, so an hour is the shortest run that could falsify it.
It did not.

## Limits

The return values of the core-count and handshake calls are assumptions, and the PHY calibration
is stubbed rather than emulated. Anything that depends on those values is a hypothesis, not a
measurement. The register writes, the branch decisions and the table lookups are real executions
of the shipped code and are not affected by those assumptions.
