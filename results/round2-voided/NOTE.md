# The first round 2, and why it is not round 2

These rows were measured on 7 September 2026 against the same builds, the same
title set and the same parity settings as the round published in
[`../round2`](../round2). They are here because they are the evidence for a
claim made in the README, not because they are a result.

`parity.txt` is the whole of it. Every target was asked for the set one at a
time, and the socket samples say two of them were not alone:

    zurg          199     20  zurg[3056375]=15, zurg[3049138]=5
    aiostreams    199     34  zurg[3056375]=15, sab-aiostreams=15, zurg[3049138]=4

`zurg[3056375]` is not this round's zurg. It is an instance an operator started
by hand to read the build's version and believed they had stopped, holding the
port and fifteen connections to an account whose whole parity rule is fifteen.
The round's own zurg could not bind, exited at once, and `wait_ready` took its
manifest from the stranger: the zurg phase measured a process the round never
started, and the phase after it ran against an account already spending its
budget.

Nothing in the report showed this. The tables rendered, the coverage numbers
were plausible, and one target was three seconds slower than it should have
been. Only the socket sampler dissented.

`harness/round.py` now refuses to start a bare-process target when something
already answers its manifest, and fails if the process it started exits within
three seconds. Either check would have stopped this pass in its first minute.
