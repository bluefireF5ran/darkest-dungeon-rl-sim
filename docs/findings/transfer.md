# Findings: cross-class transfer is CLOSED

Five mechanisms, five nulls. Few-shot per class is the answer. Moved
verbatim from the root `CLAUDE.md` on 2026-09-07.


### Cross-class ZERO-SHOT transfer is CLOSED — four mechanisms, four nulls (2026-08-28)

Fran's standing goal is a policy that "understand[s] patterns rather than
specific classes or skills, to extrapolate them into other modded classes". Four
mechanisms have now been tried against the same falsifier — hold a class out of
training, measure the gap between seen comps and that class's comps, against a
normally-trained baseline:

| attempt | excess gap | moved |
|---|---|---|
| mechanics-not-identity observation (2026-08-23) | +46.9pp | — |
| + hero capability profile (2026-08-23) | +46.6pp | 0.3pp |
| **slot-equivariant action head** (2026-08-28) | +20.6 vs +21.5 control | **0.9pp** |
| **slot-permutation augmentation** (2026-08-28) | +29.6 | **worse** |

`docs/NEXT.md` set the rule in advance: *"Still large means that theory is wrong
too and cross-class transfer closes for good — which is a real result, not a
failure."* It is still large. **Do not open a fifth attempt without a mechanism
nobody has tried and a reason it differs from these four.**

- **The head genuinely has no per-slot parameters** — `tests/training/test_equivariant_policy.py`
  asserts that structurally and then checks the consequence: permuting a hero's
  slots permutes the logits exactly and leaves the eleven non-skill actions
  untouched. So "the action INDEX is the blocker" is refuted, not untested.
- **Slot-permutation is incoherent with a per-index head**, in hindsight.
  Shuffling which skill sits at which index per episode hands `Linear(latent ->
  39)` an action space whose meaning changes between episodes, and its weights
  ARE the mapping. It scored 9.0% on the classes it trained on against the
  control's 32.1%; its smaller gap is a floor, not transfer.
- **`--equivariant` ships anyway, off by default.** It costs ~13% throughput and
  3.8pp on seen classes at 3M (which fine-tuning then closed), and it is the
  only head here that *cannot* memorise an action index — the right substrate if
  anyone returns to this.

**FEW-SHOT works, and that is the answer for modded classes.** 600k steps of
exposure — a fifth of a run — recovered **+12.7pp** on a class the policy had
never seen, and the two architectures were indistinguishable (+12.7 control,
+12.9 equivariant). The Sibyl result the same week says the same thing from the
other direction: three composed comps took her from a −28% swap cost to −1%,
generalising to eight comps that run never saw. **A modded class needs a little
data, not a better architecture.**
