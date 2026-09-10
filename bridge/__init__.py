"""The bridge to the real game: read its state, decide, (eventually) act.

This is the player, not a diagnostic — `tools/` holds the diagnostics. Three
layers, built in this order:

1. **read** — `live_state.py` decodes the running game's save files into a
   `LiveState` in this repo's own vocabulary.
2. **decide** — `seed.py` seeds a `CombatEngine` from that state and
   `advisor.py` runs a trained checkpoint on it, printing what it would do.
   The human clicks. That validates the whole pipeline with zero compute and
   is the project's first real evidence of transfer.
3. **act** — input injection. Deliberately not started: automating the clicks
   fixes nothing if the recommendation is wrong, so it waits on layer 2's
   acceptance test.
"""
