# Method

This is the part worth reusing. The bug itself is one routine in one binary; the method is what
turns "it freezes sometimes" into a named instruction in a bounded number of runs.

It is written for the general case: **closed firmware, silent death, no logs**. Nothing below
depends on the SoC, the operating system or the vendor.

## The short version

1. **Write the death predicate.** Not a description — a condition that can be false.
2. **Establish the timing law.** Is the failure periodic, cumulative, or budgeted? One run
   with a deliberately delayed trigger separates them.
3. **Audit the rig before the theory.** Ask what the instrument could have invented.
4. **Eliminate layers, don't suspect them.** Version, config, supply, clock, temperature,
   load: each varied alone, each recorded as a negative.
5. **Execute the suspect code.** Reading it is the fallback, not the method.
6. **Turn the model into a predictor.** Write the expected outcome down before the run.
7. **Patch the action, not the switch** — once you know whether the actions can be separated.
   Then list what is still running.
8. **Keep the retraction log.** What was believed, what killed it, what replaced it.
9. **Soak past the period, under load.** A fix that has not been through several expiries with
   real traffic is a hypothesis with a good track record.

## Why that order

The expensive part of a case like this is not the fix. It is the oscillation: a hypothesis fits
the numbers, a run "confirms" it, a later run contradicts it, and the next hypothesis is built
on whichever half of the evidence is remembered. The order above makes that oscillation hard,
by forcing each step to produce something that cannot be reinterpreted later — a predicate, a
timing law, a negative result, a prediction.

## Each step, and what it prevents

**1. The predicate.** "Freezes after a while" cannot be wrong, so it cannot guide anything.
"Freezes if and only if the chip identifier is unknown, more than one core is online, and the
board identifier is not the early-out sentinel" can be wrong, and every clause names an
experiment. A predicate also tells you what would falsify it — descriptions never do.

**2. The timing law.** Periodic, cumulative and budgeted failures look identical in a single
trace and behave completely differently under a delayed trigger. Here, "30 seconds after the
second core comes up" and "a 60 second grid" produced the same number in every run, because
every run happened to start the second core in the same place. Moving the trigger deliberately —
bringing the core up late — separated them in one run. Do that before building any theory.

**3. The rig audit.** More wrong conclusions came from the measurement setup than from the
code: a multi-sector write that blocked the init process (looked like a kernel hang), a log
level that moved a boot timestamp by 2.75 s and produced stalls (looked like a scheduler bug),
two boot media where only one was actually used (made an ineffective patch look effective). For
every number ask: could the instrument have produced this? A timestamp that moves when
verbosity changes is not a hardware property.

**4. Eliminate layers.** Cheap negatives are worth more than clever hypotheses. Vary one thing,
record what it did *not* move, and move on. The value is not only proving a layer innocent — it
is having a written answer when someone later asks "did you check the kernel?".

**5. Execute the code.** Hand-reading a few hundred instructions with bit-fields and write
masks is slow and quietly wrong: the mistake does not announce itself, it produces a plausible
story. Map the segment, back the registers with variables, stub the external calls, then
enumerate inputs and print every write. Two rules make the result trustworthy: model the
hardware semantics (write masks, read-back behaviour, write-only registers), and for every
result ask whether it depends on a stubbed value.

**6. Predict, then run.** A model that reproduces five past runs is an explanation; a model that
calls the sixth is evidence. Write the expected outcome down first — otherwise every outcome
gets retrofitted to the theory. Two of the five predictions in this case were counter-intuitive,
and both were right, which is what made the model worth trusting.

**7. Patch the action.** Disabling a timer, a flag or a driver makes the symptom go away while
the broken action stays; the next caller triggers it again. The test is simple: after the patch,
list what is still running. If the timer still fires and the only difference is that the harmful
writes no longer happen, it is a fix. If something stopped running, it is a bypass.

The rule has a precondition, and getting it wrong cost time here: it applies when the harmful
actions are **separable** — when removing one removes an effect that is sufficient on its own.
When they combine, a single-action patch changes nothing but the death point, and the honest
reading is "this action is not sufficient alone", not "the theory is wrong". Do not assume
separability: predict what a one-action patch will do before running it, and if the prediction
misses, switch to the group experiment — apply the whole change, confirm the symptom goes,
then bisect back to the minimum set.

In this case park and trim both take the DRAM clock away, so removing either one left the
other killing the board on the same grid. Only the group worked; the minimum set was found
afterwards, by putting one action back at a time.

**Where the rule does not apply.** Three situations produce a false negative, and all three
occur in real firmware:

- *The actions are additive.* Two routines each take the resource away; removing one leaves the
  other sufficient. A null result then says nothing about the theory — replay the group first,
  bisect afterwards.
- *Removing the action also removes the instrument.* If the write you disable is what makes the
  state observable, "nothing changed" can mean "nothing can be seen".
- *Removing it moves the failure instead of ending it.* Death at 221 s rather than 41.5 s is not
  a smaller failure but a different one: the schedule moved, the cause did not.

The test that separates these from a genuine refutation: before the run, write down both
outcomes — whether the symptom goes, and, if it does not, where it lands. If the second number
cannot be predicted, separability is not established, and that run cannot settle the question.

**8. The retraction log.** Half the value of a long investigation is the list of conclusions
that died, and why. Without it, the same attractive hypothesis gets re-adopted a month later —
it is memorable, and the reason it died is exactly what was not written down.

**9. Soak at the period that matters.** Passing the first grid point proves the immediate killer
is gone; it does not prove stability. A cumulative fault shows up at the fourth or fifth
expiry, and a fault that depends on real traffic shows up only when there is traffic. So the
acceptance run has to be: several multiples of the period, the real init rather than a stand-in
loop, and a workload aimed at the resource the fix touched. Anything shorter is a smoke test
being described as a fix.

The same precondition, seen from the other side: **if the suspected cause is cumulative,
one-variable elimination fails every time.** Removing any single contributor leaves the others
supplying the effect. Then the useful experiment is the opposite one — apply the whole change,
confirm the symptom disappears, and walk it back in halves to find the minimum set.

## What this order would have saved here

| Wrong turn | Step that prevents it |
|---|---|
| "It is a 30 s per-CPU budget" vs "it is a 60 s grid" | 2 — one late-trigger run |
| "The handshake must be the killer, it writes the same register" | 5 and 6 — the model shows two other paths write it too |
| "Disabling one write changed nothing, so the theory is wrong" | 7 — the actions were not separable; one write was never enough |
| "The log level changed the boot time, so the kernel is involved" | 3 — the instrument moved the number |
| "The patch had no effect" (while the other boot medium was in use) | 3 — verify which medium actually booted |
| Three separate re-investigations of the same layer | 4 — write the negative down |
| "It survived 320 s, so it is fixed" (five expiries was not enough) | 9 — soak for many periods, under load |

## A checklist to copy

- Can I state the failure as a condition that could be false?
- Do I know whether it is periodic, cumulative or budgeted — from a run, not from a guess?
- Have I written down what the instrument could have invented?
- Which layers have I varied *alone*, and where is that written?
- Am I reading the code, or executing it?
- Did I write the expected result before the run?
- Were the harmful actions separable, or did the fix have to remove the group?
- Did I predict where the failure would move if the patch did not fix it?
- After the patch, what is still running?
- How many periods, and how much real traffic, did the acceptance run cover?
- What did I believe last week that is now dead, and why?

## Scope

This generalises to any system where a periodic action and an asynchronous state — a bus, a
clock domain, a power domain — interact without coordination. The instance here is a DRAM
interface being parked by firmware; the same shape appears in storage controllers, GPU power
domains and interconnect clock gating.
