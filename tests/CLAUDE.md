# Contracts for `tests/`

How to run the suite without taking the machine down, and the two ways a
test here can stop running without anything going red.

**Layout.** Tests mirror the source areas: `engine/`, `env/`, `data/`,
`bridge/`, `tools/`, `training/`, and `suite/` for the meta-tests that police
the suite itself. A test goes where its SUBJECT is, not where its imports
point -- most files import from three or four layers, and `test_hit_oracle`
imports `engine` twice while being a test of `tools/hit_oracle.py`.

**Every file inserts the repo root at `parents[2]`**, and `_fixtures.py` /
`_captures.py` are imported as `from tests._fixtures import get_store`. They
stay at the root of `tests/`, which resolves as a namespace package from the
repo root.

**`tests/` itself is deliberately NOT on `sys.path`.** Putting it there would
make `tests/engine/`, `tests/data/` and `tests/tools/` importable as bare
top-level names. PEP 420 says a regular package still wins over a namespace
portion, so `import engine` would keep finding the real one -- but that is
not a thing to make `import engine` depend on, and `tools/` has no
`__init__.py`, so it is a namespace package on both sides.

**`tests/` must also stay free of `__init__.py`**: pytest's prepend import
mode then requires test module basenames to be unique across the whole tree.
All 167 are, and `tests/suite/test_layout.py` keeps it that way.

**`_fixtures.py` is a module and not a `conftest.py` on purpose**: every
test file here is also runnable as a plain script
(`python tests/engine/test_combat.py`), and pytest fixtures are invisible to
that. `tests/suite/test_layout.py` checks the bootstrap statically, because a
wrong depth fails ONLY in script mode -- the path pytest never exercises.

The rest was moved verbatim from the root `CLAUDE.md` on 2026-09-07.


## Memory: the two ways to take this machine down

Split out of the ablation section in the root `CLAUDE.md` on 2026-09-07 --
it had grown two topics under one heading, and these rules are needed
whenever the suite runs, not only when a feature is being ablated.

**Do not run the xdist suite or heavy tools alongside a 12-env training job.**
It OOM'd a 3M run at the rollout-buffer allocation (153 MiB). Two arms must
match on `--n-envs`, so the fix is to keep the machine quiet, not to shrink the
run.

**AND NEVER RUN TWO SUITES AT ONCE — ONE AT A TIME, ALWAYS (2026-09-04).**
This took the machine down hard enough to cost Fran four hours, and no
training job was involved, which is exactly what makes it worth writing down:
`pytest tests/` looks harmless.

**THE REAL CAUSE WAS A MUTATED ENGINE, NOT THE SUITE — and the 24 GB figure
first written here is RETRACTED.** It was measured while `engine/combatant.py`
still carried a leftover mutation-testing edit:

```python
def is_alive(self) -> bool:
    return not self.is_dead and not self.is_corpse    # correct
    return not self.is_dead or  not self.is_corpse    # what was in the tree
```

`or` makes that True for anything not *both* dead and a corpse, so **a combat
never ends**. Every test that drives a fight to its conclusion looped and
allocated until it was killed — one serial process reached 13 GB and 888 s of
CPU. Three concurrent suites of those is what took the machine down.

**Measured after restoring it, same command, same files: 156 MB and 24
seconds.** The suite is fine. Per-file peaks are `test_live_bridge` 678 MB
(70 tests), `test_pe_anchors` 407, everything else under 150; six files in one
serial process peak at 156 MB, so **nothing accumulates across files** and
growth in the data costs TIME, not peak memory.

Two rules survive the retraction, and one is new:

- **Still one suite at a time.** Cheap insurance, and it is what turned a bad
  line into a four-hour outage rather than a red test.
- **A leftover mutation is a MEMORY bug before it is a test failure.** The
  symptom is not a red X, it is a hang that grows. `git status` after any
  mutation run, and `git diff --ignore-all-space` to see a one-line edit
  hiding inside a whole-file line-ending flip — which is exactly how this one
  stayed invisible in an 818-line diff.
- **The failure mode is the PAGE FILE, not an OOM kill.** Windows swaps rather
  than killing anything, so nothing dies, nothing is printed, and the only
  symptom is the whole machine going treacle. Watch free physical memory, not
  the test log:

```powershell
(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1KB   # MB free
@(Get-Process python -ErrorAction SilentlyContinue).Count        # must be 0 before starting
```

The trap is the shape of the mistake, not the memory number. A suite run
exceeds a 120 s foreground limit, gets moved to the background, and the
obvious next move -- start it again so you can watch it -- is the fatal one.
The first run is still going. So:

- **One suite process at a time.** Before starting a run, confirm none is
  already alive: `Get-Process python -ErrorAction SilentlyContinue`. An empty
  result is the only green light.
- **Never restart a suite because it "seems stuck".** `pytest -q` under xdist
  buffers almost everything until the end, so a 0-byte output file at 60 s is
  normal, not a hang. Wait, or read the file it is writing.
- **`[gwN] node down: Not properly terminated` means you already did this.**
  Workers dying that way is contention between concurrent runs, not a flaky
  test. Stop everything and start one run.
- The same arithmetic covers store builds: a mods-on `load_all` carries 9,075
  effects and is not free either. Serialise those too.
