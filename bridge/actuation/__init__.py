"""The pieces of `bridge/act.py`.

Everything here is re-exported from `bridge.act`, which stays the
import surface and the CLI: about thirty of its module attributes
are touched by name in `tests/bridge/test_act.py`, and
`bridge/menus.py` and `bridge/reset.py` import from it at module
scope. Import from `bridge.act`, not from here.
"""
