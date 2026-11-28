# Experiment record

Each run below changed exactly one thing. The point of listing them is not the results
themselves but what each one ruled out. Several were designed so that the theory could be
falsified, and two of them duly falsified it.

## How the observations were made

The board is headless and dies without output, so the evidence has to be written somewhere
that survives the death. A small launcher was used: it runs as the first process, mounts
what it needs, records the current uptime, the set of online CPUs and a few markers into
unused sectors of a removable card, and repeats that on a fixed interval. After a run the card
is read on another machine and the timestamps are inspected.

Two properties make this reliable: the writes are single-sector and flushed, and the launcher
can hand over to the real init once a run is over, so the same image can also be used to test
normal userland.

It also produced one false conclusion, recorded here because it cost time: writing several
sectors in one operation on this medium blocked the init process, which looked exactly like a
kernel hang. Every later run used single-sector writes.

A second false lead: raising the kernel log level moved one boot timestamp by 2.75 s and
produced RCU stalls. A timestamp that moves when verbosity changes is not a hardware property.

## The timing anchor

**Baseline, four cores from the first instruction.** The last recorded heartbeat is at 39.5 s
and the next interval is empty, so the freeze lands between 39.5 and 42.5 s; with a finer
cadence it converges on **41.5 s of uptime**. This number is stable across kernel versions,
bootloaders, device trees, CPU frequencies, supply voltages and ambient temperature.

**Single core.** Left with one core online, the same image runs past 300 s with no failure.

**A second core added late.** If the second core is brought online after the 41.5 s point has
been passed, the board does not die immediately — it dies at the *next* grid point, roughly
60 s later. This separates an absolute periodic timer from a per-CPU budget — the alternative
reading, under which each core arms its own ~30 s allowance when it comes online. A budget would
have produced a death about 30 s after the late core. That is not what happens.

Together these three runs say: a 60 s periodic action, harmless while only one core is online,
fatal when more than one is.

## Removing pieces of the handler

**The whole periodic body disabled.** The board runs past 320 s with four cores online. The
failure is inside the body of the periodic handler, not in the timer itself and not in the
interrupt delivery.

**The handshake disabled only.** Freezes at 41.5 s, unchanged. The handshake is not the killer.
This run mattered because the handshake had been the leading suspect: it contains the same PHY
gate write as the other two paths, and disabling it looked like it should help. It did not,
because two other paths still perform the same write.

**The park call disabled only.** Freezes at 41.5 s, unchanged. Without that call the handler no
longer selects the 24 MHz oscillator by itself — but the trim routine still ends by closing both
PHY gates, and that alone is fatal. One call is not sufficient.

**The park call and the trim's gate store removed.** The board now survives the first three
ticks and dies at the fourth, around 221 s. With the gates left open, what remains is the divider
walk: one step per tick, +7.3 % by the fourth, outside what the link tolerates. This is the most
informative run in the set — it turned "one bad instruction" into "a cumulative action", and it
predicted where the next death would be before the run was made.

**All three sites removed.** Survives past 320 s with four cores online, and the recorded
markers show that the real userland came up afterwards — the init handover worked and systemd
started services. At that point the same binary had crossed five consecutive 60 s grid points.

## The soak run

Surviving five grid points is not the same as being stable, because the run above never had to
carry a workload. So the same binary was run once more, this time for an hour, handing over to
the real init instead of looping in a stand-in script.

Two things ran alongside it: a marker every 30 s recording uptime, the online-core mask and the
load average, and a job every ten minutes that allocates a 128 MB buffer, fills it, reads it
back and compares. The second one is the part that matters — it is real traffic to the DRAM
whose clock the handler had been stopping.

Result: **3622 s of uptime, four cores online at every sample**, 60 consecutive expiries of the
60 s timer with no missed beat, and the last memory pass reporting success at 3592 s. No panic,
no watchdog, no reboot — the boot stayed the same boot throughout.

One incidental observation is worth recording, because it confirms the handover rather than the
timing: the launcher was re-entered once at 13.9 s by the init system itself, since the file it
was installed as is one the boot sequence executes. That can only happen if a real userland is
running.

## The instruction-level model

The handler was also loaded into an instruction-level emulator and executed, rather than only
read. That work is described separately in `model.md`; what matters here is that it was used as
a predictor: each of the five patch runs above had its outcome called in advance, including the
two that were counter-intuitive (disabling the handshake changes nothing, disabling the park
call alone changes nothing).

## What the runs collectively exclude

Kernel version and configuration, bootloader version, device tree contents, DRAM training
parameters, supply voltage and its regulator settings, CPU clock, die temperature, the watchdog,
the interrupt controller, and the choice between open-source and proprietary TEE as such. Each
was varied alone; none moved the 41.5 s point.

What did move it: removing writes to the clock controller from inside the periodic handler.
That is the whole result.
