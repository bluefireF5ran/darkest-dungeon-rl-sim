"""How well does a checkpoint play THE OLD ROAD?

The tutorial is the one expedition that is not generated — same two heroes, same
four skills each, same two encounters, every run (`data/tutorial_loader.py`). So
unlike `eval_expedition.py --full-expedition`, which draws a different dungeon
every time it is called, this harness is **exactly reproducible**: the only thing
left varying is the combat RNG, and that is seeded per episode.

That makes it a paired comparison by construction. Two checkpoints face the same
list of seeds, so a difference between them is a difference in play, not in luck.

    python tools/eval_tutorial.py --a models/dd_combat_ppo_full_expedition.zip
    python tools/eval_tutorial.py --a <general> --b models/dd_combat_ppo_tutorial.zip
    python tools/eval_tutorial.py --a <ckpt> --self-check   # must be all zeros

SUCCESS is both fights cleared with no hero dead and no retreat — the same bar
`eval_expedition.py` uses, which is the bar the bridge is judged by in a real run.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.store_cache import load_store_cached
from data.tutorial_loader import load_tutorial_scenario
from env.expedition_sequence_env import ExpeditionSequenceEnv
from env.gym_env import CSV_DIR, GAME_DIR


def _load(path: str):
    from sb3_contrib import MaskablePPO
    try:
        import torch
        torch.set_num_threads(1)
    except Exception:                              # noqa: BLE001
        pass
    return MaskablePPO.load(path, device="cpu")


def play(model, env, seed: int) -> dict:
    """One episode. The seed reaches the combat RNG; the scenario is fixed."""
    obs, _ = env.reset(seed=seed)
    done = False
    total = 0.0
    steps = 0
    info: dict = {}
    while not done:
        masks = env.action_masks()
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, reward, terminated, truncated, info = env.step(int(action))
        total += float(reward)
        steps += 1
        done = terminated or truncated
    alive = [h for h in env.engine.heroes if h.is_alive()]
    deaths = len(env._original_hero_ids) - len(alive)
    completed = (env._combat_idx >= env._num_combats - 1
                 and env.engine.winner == "heroes")
    return {
        "success": bool(completed and deaths == 0 and env._combats_retreated == 0),
        "completed": bool(completed),
        "deaths": deaths,
        "retreats": env._combats_retreated,
        "stress": statistics.fmean([h.stress for h in alive]) if alive else 200.0,
        "reward": total,
        "steps": steps,
        "truncated": bool(truncated),
    }


def run(path: str, seeds: list[int], store, scenario) -> list[dict]:
    model = _load(path)
    env = ExpeditionSequenceEnv(store=store, sampler_seed=0,
                                fixed_scenario=scenario.reset_options())
    return [play(model, env, s) for s in seeds]


def summarise(name: str, rows: list[dict]) -> None:
    n = len(rows)
    rate = lambda k: 100.0 * sum(1 for r in rows if r[k]) / n
    mean = lambda k: statistics.fmean([r[k] for r in rows])
    print(f"  {name:<34s} SUCCESS {rate('success'):5.1f}%  "
          f"completed {rate('completed'):5.1f}%  "
          f"deaths/run {mean('deaths'):.2f}  retreats/run {mean('retreats'):.2f}  "
          f"stress {mean('stress'):5.1f}  reward {mean('reward'):6.1f}  "
          f"steps {mean('steps'):4.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True, help="checkpoint to grade")
    ap.add_argument("--b", default=None, help="second checkpoint, paired on the same seeds")
    ap.add_argument("--episodes", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0, help="first episode seed")
    ap.add_argument("--self-check", action="store_true",
                    help="play --a as BOTH arms; every delta must be 0.0 or no "
                         "comparison this tool prints means anything")
    args = ap.parse_args()

    store = load_store_cached(CSV_DIR, GAME_DIR)
    scenario = load_tutorial_scenario(GAME_DIR, store)
    seeds = [args.seed + i for i in range(args.episodes)]

    print(f"\nTHE OLD ROAD — {scenario.hero_ids[0]} / {scenario.hero_ids[1]} "
          f"at resolve {scenario.hero_levels[0]}, gear {scenario.gear_ranks[0]}, "
          f"torch {scenario.torch_level:.0f}")
    print(f"  {' then '.join(scenario.enemy_comp_names)}, "
          f"{len(seeds)} episodes, seeds {seeds[0]}..{seeds[-1]}\n")

    a_rows = run(args.a, seeds, store, scenario)
    summarise(Path(args.a).name, a_rows)

    other = args.a if args.self_check else args.b
    if other is None:
        return 0
    b_rows = run(other, seeds, store, scenario)
    summarise(Path(other).name + (" (self-check)" if args.self_check else ""), b_rows)

    print()
    for key in ("success", "completed", "deaths", "retreats", "stress", "reward"):
        a = statistics.fmean([float(r[key]) for r in a_rows])
        b = statistics.fmean([float(r[key]) for r in b_rows])
        scale = 100.0 if key in ("success", "completed") else 1.0
        print(f"  delta {key:<10s} {scale * (b - a):+7.2f}"
              + ("  <-- must be 0.00" if args.self_check else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
