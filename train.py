"""PPO training script for Darkest Dungeon combat simulator.

Usage:
    python train.py                          # train and save model (fixed 2xCrusader vs 2xCultist Brawler)
    python train.py --expedition              # train against randomized real hero comps x zone-matched real enemies (single combat/episode)
    python train.py --full-expedition          # train against a full 8-18 combat expedition per episode
    python train.py --play                    # watch a trained agent play (models/dd_combat_ppo.zip)
    python train.py --play --expedition        # watch the expedition model play (models/dd_combat_ppo_expedition.zip)
    python train.py --play --full-expedition   # watch the full-expedition model play (models/dd_combat_ppo_full_expedition.zip)
    python train.py --timesteps 200000         # custom training budget
    python train.py --full-expedition --resume --timesteps 3000000   # continue training the existing model instead of starting fresh
    python train.py --full-expedition --timesteps 3000000 --seed 0   # reproducible run; use the same seed for any A/B comparison
"""

import argparse
import os
import pathlib
import numpy as np
from gymnasium.wrappers import TimeLimit
# sb3_contrib/stable_baselines3 are imported LAZILY, inside the functions that
# need them. They drag torch in, and torch is ~454 MB resident PER PROCESS --
# and every xdist worker COLLECTS the whole suite, so one module-level import
# here cost 454 MB x 16 workers. Collection went 626 MB -> 92 MB. See pytest.ini.
from env import DarkestDungeonEnv, ExpeditionEnv, ExpeditionSequenceEnv, mask_fn

# Action masking (sb3-contrib's MaskablePPO/ActionMasker, needed separately
# — pip install sb3-contrib) — real bug found via training-eval replay:
# env/gym_env.py's Discrete(64) is "an index into whatever get_valid_
# actions() currently returns", not a stable encoding. Without a mask, an
# out-of-range predicted index silently clamps to whatever's LAST in that
# list (almost always __move__/a provision), which a trained policy hit
# constantly once cooldowns/per-battle-limits shrank the legal-action
# count below whatever index it was aiming for — producing a
# "shuffles positions forever instead of attacking" pathology that capped
# several eval comps regardless of training budget. See DarkestDungeonEnv.
# action_masks() and ASSUMPTIONS.md.

# DarkestDungeonEnv.step() never sets truncated=True on its own (no
# built-in episode length cap) — one gym "step" is one hero decision point
# (CombatEngine auto-resolves enemy turns/stun skips in between). A
# pathological matchup (e.g. a low-damage comp vs a tanky enemy group,
# common when sampling randomly across 134 real comps x ~1000 real enemy
# groups under --expedition) can otherwise run thousands of steps —
# confirmed directly: an early-training eval episode hit 3565 steps
# (mean_reward -74.5, almost exactly explained by 3565 * TURN_PENALTY).
# 150 comfortably covers any realistic combat (even a full 4-hero party
# over ~30-40 rounds) while cutting off true outliers. ExpeditionSequenceEnv
# is exempt — it self-truncates (MAX_STEPS_PER_COMBAT * num_combats), since
# the right cap depends on the sampled expedition length, unknown until
# reset(); wrapping it in a fixed external TimeLimit too would either cut
# long expeditions short or be too loose for short ones.
MAX_EPISODE_STEPS = 150


def tutorial_scenario():
    """The Old Road as reset options, read from the shipped starting save.

    Imported lazily so `train.py --expedition` does not pay for it, and so a
    game install missing `scripts/starting_save/` only breaks the one mode
    that needs it.
    """
    from data.tutorial_loader import load_tutorial_scenario
    from data.store_cache import load_store_cached
    from env.gym_env import CSV_DIR, GAME_DIR
    store = load_store_cached(CSV_DIR, GAME_DIR)
    return load_tutorial_scenario(GAME_DIR, store).reset_options()


def make_env(mode: str = "fixed", sampler_seed=None, loadout_random_p: float = 0.0,
             cross_region_p: float = 0.0, class_balance: float = 0.0,
             resolve_sampling: bool = False,
             kit_random_p: float = 0.0, boss_quest_p: float = 0.0,
             hold_out_classes=frozenset(), slot_permute: bool = False,
             hard_focus: bool = False, stress_weight: float = 1.0,
             affliction_penalty: float = 0.0, dd_exposure_penalty: float = 0.0,
             allow_abandon: bool = True, blind_forecast: bool = False,
             prevention_reward: bool = False,
             focus_mechanic=None, focus_p: float = 0.5):
    from sb3_contrib.common.wrappers import ActionMasker
    if mode == "tutorial":
        # One scripted expedition, replayed every episode: the real 2-hero
        # party at resolve 0 with their real four skills each, against
        # `tutorial_1` then `tutorial_2`, at torch 100. Nothing is sampled, so
        # this is a policy that can only get better at ONE fight pair -- which
        # is the point, and is why it writes its own checkpoint.
        # The tutorial party's four skills each are THE scenario, so
        # randomising them would be randomising the thing being pinned.
        env = ExpeditionSequenceEnv(render_mode="ansi", sampler_seed=sampler_seed,
                                    fixed_scenario=tutorial_scenario())
    elif mode == "full_expedition":
        env = ExpeditionSequenceEnv(render_mode="ansi", sampler_seed=sampler_seed,
                                    loadout_random_p=loadout_random_p,
                                    cross_region_p=cross_region_p,
                                    class_balance=class_balance,
                                    resolve_sampling=resolve_sampling,
                                    kit_random_p=kit_random_p,
                                    boss_quest_p=boss_quest_p,
                                    hold_out_classes=hold_out_classes,
                                    slot_permute=slot_permute,
                                    stress_weight=stress_weight,
                                    affliction_penalty=affliction_penalty,
                                    dd_exposure_penalty=dd_exposure_penalty,
                                    hard_focus=hard_focus,
                                    allow_abandon=allow_abandon,
                                    blind_forecast=blind_forecast,
                                    prevention_reward=prevention_reward,
                                    focus_mechanic=focus_mechanic,
                                    focus_p=focus_p)
    else:
        if mode == "expedition":
            env = ExpeditionEnv(render_mode="ansi", sampler_seed=sampler_seed,
                                loadout_random_p=loadout_random_p,
                                cross_region_p=cross_region_p,
                                    class_balance=class_balance,
                                resolve_sampling=resolve_sampling,
                                kit_random_p=kit_random_p,
                                hold_out_classes=hold_out_classes,
                                slot_permute=slot_permute,
                                stress_weight=stress_weight,
                                blind_forecast=blind_forecast,
                                prevention_reward=prevention_reward,
                                focus_mechanic=focus_mechanic,
                                focus_p=focus_p)
        else:
            env = DarkestDungeonEnv(render_mode="ansi")
        env = TimeLimit(env, max_episode_steps=MAX_EPISODE_STEPS)
    # `--fate-obs`, through the environment so `spawn` workers inherit it:
    # every hero decision sees the hit/crit/damage rolls it is about to make,
    # as the live bridge reads them off the save (env/fate_wrapper.py).
    if os.environ.get("DDSIM_FATE_OBS") in ("1", "2"):
        from env.fate_wrapper import FateObsWrapper
        env = FateObsWrapper(env, outcomes=os.environ.get("DDSIM_FATE_OBS") == "2")
    return ActionMasker(env, mask_fn)


def worker_sampler_seeds(sampler_seed, n_envs: int) -> list:
    """The per-worker scenario seeds. `None` stays `None`, else `seed + i`.

    Split out of `make_vec_env` so it is testable: with `SubprocVecEnv` the env
    factories run inside the worker processes, so nothing in the parent can
    observe what they were called with, and the rule this encodes is the one
    thing about parallel rollout that can be silently wrong.
    """
    if sampler_seed is None:
        return [None] * n_envs
    return [sampler_seed + i for i in range(n_envs)]


def make_vec_env(mode: str, n_envs: int, sampler_seed=None,
                 loadout_random_p: float = 0.0, cross_region_p: float = 0.0,
                 class_balance: float = 0.0,
                 resolve_sampling: bool = False, kit_random_p: float = 0.0,
                 boss_quest_p: float = 0.0, hold_out_classes=frozenset(),
                 slot_permute: bool = False, hard_focus: bool = False,
                 stress_weight: float = 1.0, affliction_penalty: float = 0.0,
                 dd_exposure_penalty: float = 0.0,
                 allow_abandon: bool = True, blind_forecast: bool = False,
                 prevention_reward: bool = False,
                 focus_mechanic=None, focus_p: float = 0.5):
    """`n_envs` copies of the env, in worker processes when it pays to.

    Rollout collection was single-env in a `DummyVecEnv` of length 1, on a
    16-core machine, at ~7k engine steps/s. That is the largest remaining
    wall-clock factor in training by a wide margin — bigger than every
    micro-optimisation in the engine put together.

    **Each worker gets a DIFFERENT `sampler_seed`.** With `sampler_seed=1`
    everywhere, all N workers would draw the same comp/enemy/torch sequence and
    N-way parallelism would buy N copies of one episode — more samples, no more
    diversity, and a quietly narrower training distribution than the single-env
    run it replaced. `None` stays `None` (each worker seeds from OS entropy);
    an int becomes `seed + i`, so a seeded run is still reproducible.

    `spawn` (the only start method on Windows) re-imports and rebuilds in each
    worker, so the store would cost ~1.0 s per worker — except
    `data/store_cache.py` turns that into a ~0.25 s pickle read. That cache is
    what makes this affordable, and it is why the two phases landed in this
    order.
    """
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv

    def factory(seed):
        def _init():
            return make_env(mode=mode, sampler_seed=seed,
                            loadout_random_p=loadout_random_p,
                            cross_region_p=cross_region_p,
                                    class_balance=class_balance,
                            resolve_sampling=resolve_sampling,
                            kit_random_p=kit_random_p,
                            boss_quest_p=boss_quest_p,
                            hold_out_classes=hold_out_classes,
                            slot_permute=slot_permute,
                            stress_weight=stress_weight,
                            affliction_penalty=affliction_penalty,
                            dd_exposure_penalty=dd_exposure_penalty,
                                    hard_focus=hard_focus,
                                    allow_abandon=allow_abandon,
                                    blind_forecast=blind_forecast,
                                    prevention_reward=prevention_reward,
                                    focus_mechanic=focus_mechanic,
                                    focus_p=focus_p)
        return _init

    fns = [factory(s) for s in worker_sampler_seeds(sampler_seed, n_envs)]
    if n_envs == 1:
        # One env in a subprocess is strictly worse than one in-process: same
        # throughput, plus IPC and a second interpreter.
        return DummyVecEnv(fns)
    return SubprocVecEnv(fns, start_method="spawn")



def _hold_out(args) -> frozenset:
    """`--hold-out-class` as a set, validated against the real class list.

    Validated rather than passed straight through: a typo would hold out
    NOTHING, and the run would look like a successful transfer experiment while
    being an ordinary training run.
    """
    name = getattr(args, "hold_out_class", None)
    if not name:
        return frozenset()
    from data.store_cache import load_store_cached
    from env.gym_env import CSV_DIR, GAME_DIR
    known = set(load_store_cached(CSV_DIR, GAME_DIR).heroes)
    if name not in known:
        raise SystemExit(f"--hold-out-class {name!r} is not a hero class; "
                         f"known: {', '.join(sorted(known))}")
    return frozenset({name})


def _resolve_device(choice: str) -> str:
    """`--device` -> what to hand SB3. Imports torch, so it is called from
    `main` and never at module import -- see tests/suite/test_suite_memory.py.

    **CPU IS THE DEFAULT BECAUSE THE GPU IS SLOWER HERE, and the honest record
    of how that was established is the point of this docstring.** SGD is ~73% of
    a `--full-expedition` run's wall clock, and an isolated benchmark of the
    real net shape (1636->256->256, batch 64, Adam) says the GPU should win
    comfortably: 3.35 ms per gradient step on CPU against 1.28 ms on an RTX
    2070, which is 12.9 s per update against 4.9 s.

    End to end it loses. Measured 2026-08-27, `--full-expedition --n-envs 4
    --timesteps 40960`, twice each:

        cpu    63.5 s / 63.3 s
        cuda   71.9 s / 71.2 s

    -- consistently ~13% slower. The isolated benchmark had the minibatch
    already resident on the device and no optimisation loop around it; real PPO
    ships every minibatch across PCIe and calls `.item()` for `approx_kl` and
    `clip_fraction` on every one of the 1,280 gradient steps in an update, and
    each of those synchronises. For a net this small the sync latency is larger
    than the matmul it is waiting on.

    This is the project's own "two instruments disagreeing" rule (CLAUDE.md,
    2026-08-25) landing on a performance question instead of a fidelity one:
    the end-to-end slope is the ruler that decides, and the microbenchmark was
    measuring a machine that does not exist. Keep the flag -- a bigger net or a
    bigger batch would flip this back, and `--resume` needs an explicit device
    anyway -- but do not switch the default without re-running the four numbers
    above.

    Falling back rather than failing is deliberate: `--device cuda` on a box
    without one should train, not crash a long run at minute zero.
    """
    if choice == "cpu":
        return "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    if choice == "cuda":
        print("  --device cuda asked for, but no CUDA device is visible; using cpu")
    return "cpu"


def main():
    from sb3_contrib import MaskablePPO
    from sb3_contrib.common.maskable.callbacks import MaskableEvalCallback
    from sb3_contrib.common.maskable.utils import get_action_masks
    from stable_baselines3.common.env_checker import check_env
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=100000)
    parser.add_argument("--play", action="store_true")
    parser.add_argument("--check", action="store_true", help="validate env spec")
    parser.add_argument("--expedition", action="store_true",
                         help="train/play against randomized real hero comps (COMPS/ CSV) "
                              "vs zone-matched real enemy encounters (store.enemy_comps), "
                              "with hero Resolve Level paired to the encounter's difficulty "
                              "band, instead of the fixed 2xCrusader-vs-2xCultist-Brawler default "
                              "(single combat per episode)")
    parser.add_argument("--tutorial", action="store_true",
                        help="train on THE OLD ROAD only: the shipped 2-hero "
                             "tutorial party against its two scripted fights. "
                             "Writes models/dd_combat_ppo_tutorial.zip. "
                             "--resume fine-tunes an existing checkpoint onto "
                             "it, which is the cheap way to get a tutorial "
                             "specialist out of the general model.")
    parser.add_argument("--full-expedition", action="store_true",
                         help="train/play a full 8-18 combat expedition per episode (real comp/zone "
                              "sampled once, HP/stress/Death's-Door/mortality-tier carried between "
                              "combats, provisions, reinforcements) instead of one isolated combat")
    parser.add_argument("--resume", action="store_true",
                         help="continue training the existing saved model at model_path (loads its "
                              "weights AND optimizer state via PPO.load, instead of initializing a "
                              "fresh model) for --timesteps more steps, rather than starting over")
    parser.add_argument("--seed", type=int, default=None,
                         help="seed the policy initialization, PPO's rollout sampling and the "
                              "training env. Pass the SAME value across runs you intend to compare: "
                              "every fidelity change here needs a from-scratch retrain, and unseeded "
                              "runs differ by +-4pp on their own. That noise floor is measured, not "
                              "assumed -- the camping retrain moved the short band -4.2pp on a band "
                              "that receives no campfire at all, which demoted a +4.0pp result "
                              "elsewhere in the same table to 'marginal'. Sharing an initialization "
                              "does not remove run-to-run variance, but it removes the largest "
                              "single source of it from an A/B.")
    parser.add_argument("--out", default=None, metavar="PATH",
                         help="where to SAVE the trained model, and where --resume loads from. "
                              "Defaults to this mode's live checkpoint under models/. Pass an "
                              "explicit path for any run you would not want to keep -- a smoke "
                              "run defaults to overwriting the live checkpoint in place, which "
                              "has already cost one (models/ is gitignored, so git cannot "
                              "restore it). See --smoke for the guard.")
    parser.add_argument("--smoke", action="store_true",
                         help="a throwaway wiring check: trains --timesteps steps and saves to "
                              "models/_smoke_<mode>.zip instead of the live checkpoint. Use this "
                              "rather than a short default-path run.")
    parser.add_argument("--loadout-random-p", type=float, default=0.0, metavar="P",
                        help="probability a hero is fielded with a random legal "
                             "4-of-7 instead of its comp's curated four. 0.0 (the "
                             "default) is byte-identical to the old behaviour, so "
                             "archived checkpoints keep their training "
                             "distribution. tools/loadout_sensitivity.py measured "
                             "-16.7pp SUCCESS (p=0.0075) from this shift alone, "
                             "which is the argument for training against it.")
    parser.add_argument("--cross-region-p", type=float, default=0.0, metavar="P",
                        help="probability an expedition is routed to a region "
                             "OTHER than the one its comp is tagged with. 0.0 "
                             "(the default) keeps every comp in its own "
                             "Location, which is how every archived checkpoint "
                             "trained -- and since 78 of 145 comps are tagged "
                             "Ruins, that means 55%% of expeditions were in the "
                             "Ruins and a Cove comp never met a skeleton.")
    parser.add_argument("--hold-out-class", default=None, metavar="CLASS",
                        help="train with this hero class REMOVED from the comp "
                             "pool entirely, so the policy never sees it. The "
                             "falsifier for the 2026-08-23 mechanic encoding: "
                             "score the checkpoint on that class cold with "
                             "tools/transfer_eval.py. A win-rate delta cannot "
                             "carry this question -- seed-to-seed spread on "
                             "success is ~1.4pp -- but a policy that has never "
                             "met a class either plays it or does not. Pick a "
                             "class with a SMALL share: duelist or runaway cost "
                             "5 of 141 comps, houndmaster would cost 66.")
    parser.add_argument("--boss-quest-p", type=float, default=0.0, metavar="P",
                        help="probability an expedition is a BOSS QUEST, whose "
                             "last combat is that region's boss (crypts: "
                             "Necromancer/Prophet, cove: Siren/Drowned Captain, "
                             "warrens: Swine Prince/Formless Flesh, weald: "
                             "Hag/Brigand Pounder). At 0.0 -- every checkpoint "
                             "so far -- the policy never sees a boss at all.")
    parser.add_argument("--class-balance", type=float, default=0.0, metavar="B",
                        help="how much of the comp pool's CLASS SKEW to remove "
                             "when drawing a comp. Houndmaster fills 102 hero "
                             "slots and Duelist 12, so at the default 0.0 "
                             "(uniform, and every archived checkpoint) the "
                             "policy practises one nine times more than the "
                             "other. 1.0 weights fully by inverse frequency. "
                             "A WEIGHT, never a filter -- every comp stays "
                             "reachable at every setting -- and a TRAINING "
                             "dial only: which classes are strong, and where, "
                             "is a fact about the game and must keep showing "
                             "up in evaluation, so no ruler reads it.")
    parser.add_argument("--kit-random-p", type=float, default=0.0, metavar="P",
                        help="probability a hero is fielded with a random legal "
                             "trinket pair and quirk sheet instead of its "
                             "comp's. Trinkets come from the universal "
                             "(actually-droppable) rarities, generic plus that "
                             "class's own; quirks are 1-3 of each polarity, no "
                             "diseases, no contradictions. Quirks are the big "
                             "change: 516 of 534 comp heroes carry none, so the "
                             "policy has never trained against the subsystem.")
    parser.add_argument("--resolve-sampling", action="store_true",
                        help="draw each hero's resolve from its difficulty "
                             "bracket (Apprentice 0/1/2 at 20/40/40, Veteran "
                             "3/4 at 40/60, Champion 5/6 at 40/60) instead of "
                             "pinning the whole party at the top of it. Gear "
                             "rank follows resolve, so this also fields mixed "
                             "equipment -- which is what a real roster looks "
                             "like.")
    parser.add_argument("--hard-focus", action="store_true",
                    help="Weight sampling toward LONG and CHAMPION expeditions "
                         "(long x5, Champion x6). Train harder than you deploy: the "
                         "policy already clears short/medium at ~90%%, and the "
                         "districts retrain lost 4.0pp to an arm that had trained in "
                         "the easier world it was evaluated in.")
    parser.add_argument("--focus-mechanic", default=None,
                        choices=("dodge", "bleed", "blight", "burn"),
                        help="Specialise on a MECHANIC family -- comps that can "
                             "play it, identified by their equipped kit's "
                             "effects rather than by name. Half the episodes "
                             "come from the family and half from the whole "
                             "pool, so it specialises without losing the rest. "
                             "Defined by capability so a modded class joins a "
                             "family automatically.")
    parser.add_argument("--focus-p", type=float, default=0.5, metavar="P",
                        help="share of episodes drawn from --focus-mechanic")
    parser.add_argument("--prevention-reward", action="store_true",
                        help="Credit PREVENTION rather than restoration. Two "
                             "terms inside the potential function, so by "
                             "Ng/Harada/Russell neither can change the optimal "
                             "policy -- they fix credit TIMING. (1) outstanding "
                             "DoT counts against an enemy, so a blight earns "
                             "credit when APPLIED rather than when it ticks; "
                             "(2) the threat forecast's incoming damage and "
                             "stress count against the party, so killing, "
                             "stunning, dodging and debuffing pay and healing "
                             "does not. Default OFF and byte-identical, so this "
                             "is the retrain's own control arm.")
    parser.add_argument("--fate-obs", action="store_true",
                        help="append the turn's known dice (hit roll, first "
                             "target's crit and damage rolls) to the observation "
                             "and make the attack use them -- what the bridge "
                             "reads from the save's RNG state (DECOMP.md F15). "
                             "Widens the observation by 3; env/fate_wrapper.py.")
    parser.add_argument("--fate-outcomes", action="store_true",
                        help="--fate-obs plus the dice RESOLVED per attack action: "
                             "will it hit / crit / kill the first target (84 more "
                             "floats). env/fate_wrapper.attack_outcomes.")
    parser.add_argument("--blind-forecast", action="store_true",
                        help="THE CONTROL ARM for the threat forecast: emit its "
                             "19 features as zero, at their source, leaving the "
                             "observation width and every other index untouched. "
                             "tools/ablate_forecast.py shows the policy READS "
                             "them (pooled McNemar p=0.0132) but cannot show they "
                             "made it BETTER -- zeroing at eval time is "
                             "off-distribution. Only a run trained without them "
                             "answers that.")
    parser.add_argument("--no-abandon-train", action="store_true",
                        help="Withhold ACTION_ABANDON during TRAINING only. The "
                             "exit may be suppressing Champion play quality by "
                             "letting the policy skip the states it most needs to "
                             "learn; evaluate WITH abandon to test that.")
    parser.add_argument("--slot-permute", action="store_true",
                        help="shuffle which skill sits at which ACTION INDEX, "
                             "redrawn per episode. The probe of why cross-class "
                             "transfer fails: an index is slot*4+rank and slot k "
                             "names a different skill per class, so the policy "
                             "memorises (class, slot) pairs and has nothing to "
                             "look up for a class it never saw. Pair with "
                             "--hold-out-class.")
    parser.add_argument("--n-envs", type=int, default=8, metavar="N",
                         help="parallel rollout environments (SubprocVecEnv). Training was "
                              "single-env on a 16-core machine; this is the largest wall-clock "
                              "factor available. Each worker gets sampler_seed+i, so N workers "
                              "widen the sampled distribution instead of replaying one episode "
                              "N times. Use 1 for a serial, in-process run when debugging.")
    parser.add_argument("--net-arch", default="256,256", metavar="A,B",
                        help="hidden layer widths for BOTH the policy and value "
                             "nets. 256,256 is what every archived checkpoint "
                             "uses -- and it was chosen when the observation was "
                             "~330 floats. It is now 1636, so the first layer is "
                             "narrower than its own input by 6.4x, which is a "
                             "worse ratio than the 5.2x that prompted the last "
                             "widening from 64,64.")
    parser.add_argument("--lr-schedule", choices=("constant", "linear"),
                        default="constant",
                        help="`linear` decays the learning rate to 0 over the "
                             "run. Constant 3e-4 is what every archived "
                             "checkpoint used, and the 12M run ended with "
                             "clip_fraction 0.205 and approx_kl 0.024 -- a fifth "
                             "of every minibatch hitting the trust-region clip, "
                             "which is what decay normally settles.")
    parser.add_argument("--n-epochs", type=int, default=10, metavar="N",
                        help="PPO epochs per rollout batch. 10 is historical and "
                             "was never revisited; at the clip fraction above, "
                             "the later epochs are optimising against data the "
                             "policy has already moved away from.")
    parser.add_argument("--batch-size", type=int, default=64, metavar="N",
                        help="PPO minibatch size. 64 is the historical value "
                             "and every archived checkpoint uses it -- but with "
                             "n_steps 2048 x 12 envs an update is 24,576 "
                             "samples, so 64 means 3,840 gradient steps per "
                             "update. SGD was ~73%% of wall clock when this "
                             "flag was added at batch 64; RE-MEASURED "
                             "2026-09-02 at batch 256 it is ~49%%, so rollout "
                             "and SGD are now about half each and neither side "
                             "alone can save more than half. Measured "
                             "on this box: 12.9 s per update at 64, 5.3 s at "
                             "256, 4.0 s at 512. Raising it changes the "
                             "optimisation, not just the speed, so two arms of "
                             "a comparison must match on it.")
    parser.add_argument("--stress-weight", type=float, default=1.0, metavar="W",
                        help="scale BOTH stress penalties -- the per-point tick "
                             "and the terminal one. 1.0 is byte-identical to "
                             "every archived checkpoint. The reason to raise "
                             "it is measured: `_run_quality`, which run_score "
                             "is built from and which Fran's objective names, "
                             "gives stress HALF a hero's score, while the "
                             "training return gives it about 9%% -- ~8 points "
                             "against ~85 of combat wins over a 15-combat run. "
                             "This is a NON-potential change and does move the "
                             "optimum; that is the point.")
    parser.add_argument("--dd-exposure-penalty", type=float, default=0.0, metavar="P",
                        help="charge P per deathblow roll -- each hit a hero "
                             "on Death's Door takes. 0.0 (the default) is "
                             "byte-identical to every archived checkpoint. "
                             "Fran, 2026-10-12 (NEXT item 3): downed heroes "
                             "die 0.36 per entry against his 0 in 9, at 0.86 "
                             "rolls per entry. A NON-potential term; see "
                             "DD_EXPOSURE_PENALTY_DEFAULT.")
    parser.add_argument("--affliction-penalty", type=float, default=0.0, metavar="P",
                        help="charge P once per hero that becomes AFFLICTED, "
                             "once per expedition. 0.0 (the default) is "
                             "byte-identical to every archived checkpoint. "
                             "Fran rates an afflicted hero \"almost the same "
                             "as a death\"; the only price the reward has ever "
                             "put on one is W_AFFLICTED=0.15 INSIDE Phi, which "
                             "by Ng/Harada/Russell cannot change the optimal "
                             "policy at all -- so the pricing has been exactly "
                             "zero. Like --stress-weight this is a NON-potential "
                             "term and does move the optimum; size it against "
                             "REWARD_HERO_DEATH (-50.0) and run it as a paired "
                             "arm on TWO seeds, because --stress-weight came "
                             "back null twice on one.")
    parser.add_argument("--equivariant", action="store_true",
                        help="replace the policy's final Linear(latent -> 39) "
                             "with a head that scores each action from what it "
                             "DOES -- the skill's own payload features, shared "
                             "across all seven slots -- instead of from which "
                             "index it occupies. The action space is the last "
                             "identity-coupled surface left after the 2026-08-23 "
                             "observation rewrite, and it is why a held-out "
                             "class transfers at +46.9pp excess gap. Changes the "
                             "policy ARCHITECTURE, so no earlier checkpoint "
                             "loads into it and --resume across the flag is "
                             "invalid.")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu",
                        help="where the policy lives. DEFAULT cpu, and that is a "
                             "MEASUREMENT, not an oversight -- CUDA is ~13%% SLOWER "
                             "end-to-end for this net (see _resolve_device). The flag "
                             "exists so the choice is explicit and so --resume stops "
                             "silently disagreeing with the run it continues. TWO ARMS OF "
                             "AN A/B MUST MATCH ON THIS, exactly as they must on --n-envs: "
                             "float32 reductions associate differently on the two devices.")
    args = parser.parse_args()
    if args.fate_obs:
        os.environ["DDSIM_FATE_OBS"] = "1"
    if args.fate_outcomes:
        os.environ["DDSIM_FATE_OBS"] = "2"
    mode = ("tutorial" if args.tutorial else
            "full_expedition" if args.full_expedition else
            "expedition" if args.expedition else "fixed")
    live_path = {
        "tutorial": "models/dd_combat_ppo_tutorial.zip",
        "full_expedition": "models/dd_combat_ppo_full_expedition.zip",
        "expedition": "models/dd_combat_ppo_expedition.zip",
        "fixed": "models/dd_combat_ppo.zip",
    }[mode]
    if args.smoke:
        model_path = f"models/_smoke_{mode}.zip"
    else:
        model_path = args.out or live_path

    # --check and --play are single-env by nature: one spec validation, one
    # watched episode. Only rollout collection wants N of them.
    if args.check or args.play:
        env = make_env(mode=mode, loadout_random_p=args.loadout_random_p,
                       cross_region_p=args.cross_region_p,
                       class_balance=args.class_balance,
                       resolve_sampling=args.resolve_sampling,
                       kit_random_p=args.kit_random_p,
                       boss_quest_p=args.boss_quest_p,
                       hold_out_classes=_hold_out(args),
                       slot_permute=args.slot_permute,
                       stress_weight=args.stress_weight,
                       affliction_penalty=args.affliction_penalty,
                       dd_exposure_penalty=args.dd_exposure_penalty,
                       hard_focus=args.hard_focus,
                       allow_abandon=not args.no_abandon_train,
                       blind_forecast=args.blind_forecast,
                       prevention_reward=args.prevention_reward,
                       focus_mechanic=args.focus_mechanic,
                       focus_p=args.focus_p)
        if args.seed is not None:
            env.reset(seed=args.seed)
    else:
        env = None

    if args.check:
        check_env(env, warn=True)
        print("Environment check passed.")
        return

    if args.play:
        model = MaskablePPO.load(model_path, env=env)
        if mode in ("expedition", "full_expedition", "tutorial"):
            obs, _ = env.reset(seed=42)
        else:
            obs, _ = env.reset(seed=42, options={
                "hero_ids": ["crusader", "crusader"],
                "enemy_ids": ["cultist_brawler_A", "cultist_brawler_A"],
            })
        total_reward = 0
        done = False
        while not done:
            action, _ = model.predict(obs, deterministic=True, action_masks=get_action_masks(env))
            obs, reward, terminated, truncated, info = env.step(int(action))
            total_reward += reward
            done = terminated or truncated
            print(env.render())
            print("---")
        print(f"Total reward: {total_reward}, winner: {info.get('winner')}")
        return

    # A smoke run must not touch the real EvalCallback output either:
    # best_model.zip/evaluations.npz there are the "measure BOTH saves" half of
    # every checkpoint decision, and 2000 steps of noise would replace them.
    # `--out` gets its OWN EvalCallback directory, keyed on the checkpoint
    # name. Without that, two runs of one mode write the same
    # `logs/<mode>/best_model.zip` and `evaluations.npz` -- so the two arms of
    # a paired experiment, which are the whole reason `--out` exists, silently
    # overwrite each other's "best" save and can no longer be run
    # concurrently. A run with no `--out` is the live checkpoint and keeps the
    # per-mode directory it has always had.
    if args.smoke:
        log_dir = f"./logs/_smoke_{mode}/"
    elif args.out:
        log_dir = f"./logs/{pathlib.Path(args.out).stem}/"
    else:
        log_dir = f"./logs/{mode}/" if mode != "fixed" else "./logs/"

    n_envs = max(1, args.n_envs)
    env = make_vec_env(mode, n_envs, sampler_seed=args.seed,
                       loadout_random_p=args.loadout_random_p,
                       cross_region_p=args.cross_region_p,
                       class_balance=args.class_balance,
                       resolve_sampling=args.resolve_sampling,
                       kit_random_p=args.kit_random_p,
                       boss_quest_p=args.boss_quest_p,
                       hold_out_classes=_hold_out(args),
                       slot_permute=args.slot_permute,
                       stress_weight=args.stress_weight,
                       affliction_penalty=args.affliction_penalty,
                       dd_exposure_penalty=args.dd_exposure_penalty,
                       hard_focus=args.hard_focus,
                       allow_abandon=not args.no_abandon_train,
                       blind_forecast=args.blind_forecast,
                       prevention_reward=args.prevention_reward,
                       focus_mechanic=args.focus_mechanic,
                       focus_p=args.focus_p)
    if args.seed is not None:
        env.seed(args.seed)
    print(f"Rollout envs: {n_envs} "
          f"({'SubprocVecEnv' if n_envs > 1 else 'DummyVecEnv, in-process'})")
    device = _resolve_device(args.device)
    print(f"Device: {device} (--device {args.device})")
    print(f"Policy head: {'EQUIVARIANT (payload-scored)' if args.equivariant else 'standard Linear(latent -> 39)'}")

    # The eval env stays SINGLE and keeps sampler_seed=1 regardless of --seed.
    # It is the measuring instrument: if it moved with the training seed, two
    # runs being compared would be scored on different scenarios, and the
    # eval-vs-eval difference would include the difference between the two
    # scenario sets. That is already the project's biggest measurement problem
    # (~3pp noise against ~3pp effects); do not make the ruler elastic.
    # The eval env carries the SAME loadout_random_p as training. The seed
    # stays pinned at 1 -- that is what the paragraph above protects, and it
    # still holds, because loadouts are drawn per scenario from `_loadout_rng`
    # and so reproduce exactly. What must NOT happen is training against
    # randomised loadouts while `EvalCallback` picks `best_model` on curated
    # ones: that selects the checkpoint best at a distribution this run is
    # deliberately no longer training for.
    eval_env = make_env(mode=mode, sampler_seed=1,
                        loadout_random_p=args.loadout_random_p,
                        cross_region_p=args.cross_region_p,
                       class_balance=args.class_balance,
                        resolve_sampling=args.resolve_sampling,
                        kit_random_p=args.kit_random_p,
                        boss_quest_p=args.boss_quest_p,
                        hold_out_classes=_hold_out(args),
                        slot_permute=args.slot_permute,
                       stress_weight=args.stress_weight,
                       affliction_penalty=args.affliction_penalty,
                       dd_exposure_penalty=args.dd_exposure_penalty,
                       hard_focus=args.hard_focus,
                       allow_abandon=not args.no_abandon_train,
                       blind_forecast=args.blind_forecast,
                       prevention_reward=args.prevention_reward,
                       focus_mechanic=args.focus_mechanic,
                       focus_p=args.focus_p)
    eval_callback = MaskableEvalCallback(
        eval_env,
        best_model_save_path=log_dir,
        log_path=log_dir,
        eval_freq=5000,
        deterministic=True,
        render=False,
    )

    if args.resume:
        print(f"Resuming training from {model_path} (weights + optimizer state)...")
        model = MaskablePPO.load(model_path, env=env, device=device)
    else:
        # THE ACTION-SPACE FIX, opt-in. An ordinary `MlpPolicy` ends in
        # `Linear(latent -> 39)`, so output k means "slot k of whichever class
        # is acting" and cross-class transfer has to be memorised per class --
        # measured at +46.9pp excess gap on a held-out class, and unmoved by
        # two observation redesigns. `--equivariant` swaps the head for one
        # that scores an action from its own payload. See
        # env/equivariant_policy.py.
        widths = [int(x) for x in args.net_arch.split(",") if x.strip()]
        assert widths, "--net-arch must name at least one layer width"
        # SB3 accepts a callable of REMAINING progress (1 -> 0), so `linear`
        # is `lr * progress`. Passing a float keeps the constant behaviour
        # every archived checkpoint was trained with.
        lr = (3e-4 if args.lr_schedule == "constant"
              else (lambda progress_remaining: 3e-4 * progress_remaining))
        policy_cls = "MlpPolicy"
        if args.equivariant:
            from env.equivariant_policy import make_policy_class, policy_kwargs_for
            policy_cls = make_policy_class()
            equivariant_kwargs = policy_kwargs_for(
                env, net_arch=dict(pi=widths, vf=list(widths)))
        model = MaskablePPO(
            policy_cls,
            env,
            verbose=1,
            device=device,
            learning_rate=lr,
            n_steps=2048,
            batch_size=args.batch_size,
            n_epochs=args.n_epochs,
            gamma=0.99,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=0.01,
            seed=args.seed,
            # SB3's default MlpPolicy net is [64, 64], sized for the old
            # 50-float observation. The observation now carries per-rank
            # identity and status for both sides plus per-skill-slot features
            # (~330 floats), so the first layer was narrower than its own
            # input — widened so the network can actually represent what it
            # is now being shown.
            #
            # DO NOT WIDEN IT AGAIN ON THAT ARGUMENT. The observation is
            # 1636 now, so the ratio is 6.4x -- worse than the 5.2x that
            # prompted this widening -- and `--net-arch 512,512` was
            # measured on 2026-08-29 at **-4.1 SUCCESS and -3.0 QUALITY**
            # on the long band, with `clip_fraction` UP (0.245 vs 0.205).
            # Capacity is not the binding constraint here; optimisation
            # stability is, and `--lr-schedule linear` is what buys it.
            policy_kwargs=(equivariant_kwargs if args.equivariant
                           else dict(net_arch=dict(pi=list(widths), vf=list(widths)))),
        )

    # Echo the WHOLE distribution, not just the mode. Two 3M arms of a paired
    # transfer experiment were run on 2026-08-23 and their logs record the env
    # count and nothing else, so which of the five randomisation knobs were on
    # is no longer recoverable from either one -- and an arm whose distribution
    # is unknown cannot be compared against anything later.
    print("Distribution: " + " ".join(
        f"{k}={getattr(args, k)}" for k in (
            "seed", "n_envs", "loadout_random_p", "cross_region_p",
            "class_balance",
            "resolve_sampling", "kit_random_p", "boss_quest_p",
            "hold_out_class", "slot_permute", "hard_focus", "no_abandon_train",
            "stress_weight", "affliction_penalty", "dd_exposure_penalty",
            "batch_size", "net_arch", "lr_schedule", "n_epochs",
            # Which ARM this checkpoint is. Without it in the run log there is
            # no way to tell a control-arm checkpoint from a sighted one after
            # the fact, and the attribution the arm exists for is lost.
            "blind_forecast", "prevention_reward",
            "focus_mechanic", "focus_p")))
    # DDSIM_MOD_DIRS is part of the distribution too: it decides whether
    # a modded class is IN THE COMP POOL AT ALL, which is a larger change
    # than any of the five knobs above. It was NOT echoed until
    # 2026-08-29, so models/sibyl_20260827.zip cannot be shown to have
    # ever seen a Sibyl comp -- exactly the unrecoverable-arm failure the
    # comment above describes, one environment variable to the left.
    from data.mod_roots import mod_roots
    _roots = mod_roots()
    print("Mod roots: " + (os.pathsep.join(str(r) for r in _roots)
                           if _roots else "(none -- vanilla pool)"))
    print(f"Training for {args.timesteps} more timesteps (mode={mode}, resume={args.resume})...")
    model.learn(
        total_timesteps=args.timesteps,
        callback=eval_callback,
        progress_bar=True,
        # False when resuming: keeps model.num_timesteps counting up from
        # where the saved model left off (correct for TensorBoard/logging
        # continuity and any LR/clip-range schedule) instead of restarting
        # at 0 as if this were a fresh run.
        reset_num_timesteps=not args.resume,
    )
    model.save(model_path)
    print(f"Model saved to {model_path}")


if __name__ == "__main__":
    main()
