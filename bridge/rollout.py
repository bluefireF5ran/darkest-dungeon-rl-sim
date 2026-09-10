"""Ask the simulator a question instead of training on it: *does THIS party
survive THAT dungeon?*

The project has a combat **policy** and no **valuation**. Every decision outside
combat — which of the four offered quests to take, which four heroes to send,
whether to push on or turn back at the next door — reduces to the same question,
and no invented heuristic answers it honestly. But `ExpeditionSequenceEnv`
already plays a whole 3-16 combat expedition in a couple of seconds, so the
answer can be *measured*: run the party forward N times and count.

That costs zero training steps. It is the same machinery `eval_expedition.py`
uses for its success table, pointed at one specific party instead of a sampled
comp — deliberately the same code path (`run_full_expedition_episode`,
`ExpeditionOutcome`), so a rollout number and an eval-table number always mean
the same thing.

## What makes a party REAL, and why it needed env changes

A scraped comp is four hero ids, uniform resolve level, and a kit looked up by
comp name. A real roster is none of those, and each difference moves the answer:

* **three independent numbers per hero** — resolve level, weapon rank, armour
  rank. Deriving gear from resolve is a convention for sampled parties and it
  already cost this project a real bug (a damaged hero reading as full, because
  the wrong tier lowered max HP and carry-over clamps against it). Handled by
  `CombatEngine.apply_gear_ranks`, shared with `bridge/seed.py`.
* **its own trinkets and quirks**, which no `comp_name` can resolve.
* **a starting state** — HP, stress, affliction, Death's-Door history. Asking
  "should we retreat?" is exactly asking about a party that is already hurt.

## How honest the number is

`P(success)` is the probability *this policy* completes the run, and the policy
plays worse than a good human. So it is a **lower bound**, and more useful as a
ranking between options than as an absolute. Two rollouts differing by 40 points
mean something; one reading 0.62 does not mean 62% for Fran. Nothing here
pretends otherwise, and `RolloutResult` reports the spread it was measured over.
"""

from __future__ import annotations

import statistics
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.combatant import CarryOverHeroState
from env.expedition_sequence_env import EXPEDITION_LENGTH_BANDS, ExpeditionSequenceEnv
from env.gym_env import mask_fn
from eval_expedition import run_full_expedition_episode

# Enough episodes for a rank to mean something, few enough that an embark
# screen answers in seconds. A full expedition is ~1-2 s, so 20 is ~30 s per
# option — the standard error on a rate near 0.5 is then ~11 points, which is
# why RolloutResult exposes the count and callers are told to read differences,
# not absolutes.
DEFAULT_EPISODES = 20


# Pass this instead of a checkpoint path to score with the scripted playbook
# player. A magic string rather than a flag because every layer between the CLI
# and here -- Evaluator, the worker pool initialiser, Rollout -- threads exactly
# one "model" value, and widening all of them to carry a second kind of arm buys
# nothing over a name no checkpoint path can collide with.
PLAYBOOK_MODEL = "playbook"


def quiet_torch() -> None:
    """One inference thread. **This is the whole reason the game stutters.**

    The policy is a small MLP and one recommendation costs 1.5 ms, so nothing
    here needs ten cores. What it costs is not the 1.5 ms: PyTorch's OpenMP
    pool spin-waits for ~200 ms after every parallel region, so a *single*
    inference bills the machine

        10 threads: 1968.8 ms CPU for 1.01 s wall (195% of one core)
         1 thread :    0.0 ms CPU for 1.00 s wall (0%)

    -- measured here, on this box, with `time.process_time()` around one
    `recommend()` plus a 1 s sleep. Two core-seconds of pure spin per piece of
    advice, on the machine that is trying to render Darkest Dungeon at the same
    time. Reported from live play as "the game is also lagging much".

    Single-threaded is also FASTER for this shape: 0.7 ms against 1.5 ms.

    Bridge-only, deliberately. `train.py` wants every core it can get, and this
    sets a PROCESS-wide limit -- so it lives here, at the bridge's model loads,
    and never in `env/` or `engine/` where a training run would import it.
    """
    try:
        import torch
        torch.set_num_threads(1)
    except Exception:                              # noqa: BLE001
        pass


@dataclass(frozen=True)
class PartyMember:
    """One real hero, as the roster holds them — not as a comp lists them.

    Defaults describe a fresh max-rank hero, so a caller who only knows the
    class still gets something runnable. `hp=None` means "start at full",
    which is not the same as a number: max HP is not known until gear and
    trinkets have been applied inside the engine.
    """
    hero_class: str
    resolve_level: int = 6
    weapon_rank: int = 5
    armour_rank: int = 5
    skill_ids: tuple[str, ...] = ()
    camp_skills: tuple[str, ...] = ()
    trinkets: tuple[str, ...] = ()
    quirks: tuple[str, ...] = ()
    hp: Optional[int] = None
    stress: int = 0
    mortality_tier: str = "none"
    affliction_id: Optional[str] = None
    virtue_id: Optional[str] = None

    @classmethod
    def from_live_hero(cls, hero) -> "PartyMember":
        """Build one from a `bridge.live_state.LiveHero` — the party that is in
        the dungeon right now, so the retreat question can be asked about it."""
        return cls(
            hero_class=hero.hero_class,
            resolve_level=hero.resolve_level,
            weapon_rank=hero.weapon_rank,
            armour_rank=hero.armour_rank,
            skill_ids=tuple(hero.selected_skills),
            camp_skills=tuple(hero.camping_skills),
            trinkets=tuple(hero.trinkets),
            quirks=tuple(hero.quirks),
            hp=max(0, int(round(hero.hp))),
            stress=int(round(hero.stress)),
            mortality_tier=("heart_attack" if hero.has_had_heart_attack
                            else "mortality" if hero.visited_deaths_door else "none"),
            affliction_id=hero.affliction_id,
            virtue_id=hero.virtue_id,
        )


@dataclass
class RolloutResult:
    """What N simulated runs of one party through one dungeon came out as.

    `success_rate` is the headline and matches `eval_expedition.py`'s SUCCESS
    exactly: completed every combat AND nobody died. The rest are broken out
    because they rank differently — a party that completes with one death is a
    real option when the alternative is not going, and a stall is a failure that
    says more about the policy than about the party.
    """
    episodes: int
    success_rate: float
    completion_rate: float
    zero_death_rate: float
    wipe_rate: float
    stall_rate: float
    mean_deaths: float
    mean_final_stress: float
    mean_combats_reached: float
    num_combats: int
    # The PER-EPISODE quality and progress, not their means.
    #
    # Kept as samples because the only consumer that matters -- `comp_ga.Score`
    # -- pools several `RolloutResult`s (one per zone) into one number, and a
    # mean-of-means is wrong the moment two zones run different episode counts.
    # It also needs the spread, and a standard deviation cannot be recovered
    # from a mean. n is at most a few dozen, so the tuple costs nothing to
    # pickle into a worker.
    qualities: tuple = ()
    progresses: tuple = ()
    # The two halves of each episode's quality, UNWEIGHTED -- (hp, calm) per
    # episode. Carried because the CONSUMER decides the weighting: the eval
    # headline keeps the declared 50/50 and `tools/comp_ga.py` ranks on 2:1
    # stress-over-HP, and that cannot be recovered from `qualities` once the
    # two halves are already mixed.
    quality_parts: tuple = ()
    mean_hp_frac: float = 1.0

    @property
    def standard_error(self) -> float:
        """Standard error of `success_rate`. Printed next to it so a 2-point
        gap between two options is visibly not a result."""
        if self.episodes <= 0:
            return 0.0
        p = self.success_rate
        return (p * (1.0 - p) / self.episodes) ** 0.5

    @property
    def mean_quality(self) -> float:
        return statistics.fmean(self.qualities) if self.qualities else 0.0

    @property
    def mean_progress(self) -> float:
        return statistics.fmean(self.progresses) if self.progresses else 0.0

    def weighted_qualities(self, stress_weight: float = 0.5) -> tuple:
        """Per-episode quality with HP and stress re-weighted, before progress.

        Kept beside `weighted_run_scores` so a caller reporting a `qual` column
        next to a `score` column can make them agree: `score` is the mean of
        `quality x progress`, and printing the 50/50 quality beside a 2:1 score
        would show a product that does not multiply out.
        """
        hp_weight = 1.0 - stress_weight
        out = []
        for i in range(len(self.progresses)):
            parts = self.quality_parts[i] if i < len(self.quality_parts) else None
            if parts and any(parts):
                out.append(hp_weight * parts[0] + stress_weight * parts[1])
            else:
                out.append(self.qualities[i] if i < len(self.qualities) else 0.0)
        return tuple(out)

    def weighted_run_scores(self, stress_weight: float = 0.5) -> tuple:
        """Per-episode `quality x progress` with HP and stress re-weighted.

        `stress_weight` is stress's share of quality; `0.5` reproduces
        `run_scores` exactly, which is what `tests/bridge/test_rollout.py` pins. Falls
        back to the mixed `qualities` when an outcome carries no components, so
        an older pickled result still reads.
        """
        hp_weight = 1.0 - stress_weight
        out = []
        for i, prog in enumerate(self.progresses):
            parts = self.quality_parts[i] if i < len(self.quality_parts) else None
            if parts and any(parts):
                qual = hp_weight * parts[0] + stress_weight * parts[1]
            else:
                qual = self.qualities[i] if i < len(self.qualities) else 0.0
            out.append(qual * prog)
        return tuple(out)

    @property
    def run_scores(self) -> tuple:
        """Per episode: how well the party came home, times how much of the
        expedition it actually did.

        `quality` alone is exploitable as a fitness. It is measured at episode
        end unconditionally, so a party that STALLS OUT at combat 2 of 14 with
        everyone healthy scores ~90 -- better than a party that fought all
        fourteen and came home bloodied. The progress factor is what makes the
        number mean "completed the expedition in good shape", which is the
        whole of the stated objective and not two thirds of it.

        A wipe is already 0 (every hero dead => quality 0). A completed run is
        its quality. A retreat does not count as a completion, so a party that
        flees its way to the end is priced by how many fights it actually
        cleared.
        """
        return tuple(q * p for q, p in zip(self.qualities, self.progresses))

    @property
    def mean_run_score(self) -> float:
        s = self.run_scores
        return statistics.fmean(s) if s else 0.0

    @property
    def run_score_se(self) -> float:
        """Sample SD / sqrt(n).

        NOT the binomial form `standard_error` uses: the run score is a
        continuous quantity on [0, 1], not a coin flip, and `p(1-p)/n` would
        overstate the error of a comp whose runs all land in the same place.
        """
        s = self.run_scores
        if len(s) < 2:
            return 0.0
        return statistics.stdev(s) / (len(s) ** 0.5)

    def line(self) -> str:
        return (f"P(success) {self.success_rate * 100:5.1f}% ±{self.standard_error * 100:4.1f} | "
                f"qual {self.mean_quality * 100:5.1f} | prog {self.mean_progress * 100:5.1f}% | "
                f"completed {self.completion_rate * 100:5.1f}% | "
                f"deaths {self.mean_deaths:4.2f} | stress {self.mean_final_stress:5.1f} | "
                f"combats {self.mean_combats_reached:4.1f}/{self.num_combats}")


def _carry_over_for(member: PartyMember) -> Optional[CarryOverHeroState]:
    """None when the hero is untouched — a party at full health with no history
    must go through the plain reset path, not through a carry_over that would
    pin HP to a number computed before gear and trinkets moved the ceiling."""
    untouched = (member.hp is None and not member.stress
                 and member.mortality_tier == "none"
                 and not member.affliction_id and not member.virtue_id)
    if untouched:
        return None
    return CarryOverHeroState(
        hp=member.hp if member.hp is not None else 999,
        stress=member.stress,
        is_deaths_door=member.hp is not None and member.hp <= 0,
        mortality_tier=member.mortality_tier,
        affliction_id=member.affliction_id,
        virtue_id=member.virtue_id,
    )


def build_options(party: Sequence[PartyMember]) -> dict:
    """The reset options that turn a sampled-comp expedition into this exact
    party's expedition. Kept separate from `Rollout` so it can be asserted on in
    a test without loading a policy."""
    options: dict = {
        "hero_ids": [m.hero_class for m in party],
        "hero_levels": [m.resolve_level for m in party],
        "gear_ranks": [(m.weapon_rank, m.armour_rank) for m in party],
        "trinkets_and_quirks": [(list(m.trinkets), list(m.quirks)) for m in party],
    }
    # Positional, never a dict keyed by class: a real party can field two
    # Highwaymen with different skills, and a dict collapses them onto whichever
    # was inserted last (the confirmed duplicate-class bug, see CLAUDE.md).
    if any(m.skill_ids for m in party):
        options["hero_skill_ids"] = [list(m.skill_ids) for m in party]
    if any(m.camp_skills for m in party):
        options["camp_skill_ids"] = [list(m.camp_skills) for m in party]
    carry = [_carry_over_for(m) for m in party]
    if any(c is not None for c in carry):
        # A partly-hurt party still needs an entry per hero; the untouched ones
        # get a carry_over that changes nothing rather than being dropped.
        options["carry_over"] = [
            c if c is not None else CarryOverHeroState(hp=999, stress=0, is_deaths_door=False,
                                                       mortality_tier="none")
            for c in carry
        ]
    return options


class Rollout:
    """Holds the env and the policy so a caller can ask many questions cheaply.

    Construction parses the whole game install (~1-2 s) and loads the
    checkpoint; after that each episode is milliseconds of Python plus the
    policy's forward passes.

    **It owns its own env and must not share the advisor's.** `bridge/advisor.py`
    installs a seeded engine into its env in place (`env.engine = engine`, plus
    the expedition-context fields); running a rollout through that same instance
    would overwrite the very board the advisor is about to be asked about.
    """

    def __init__(self, model_path: str):
        from sb3_contrib import MaskablePPO

        from sb3_contrib.common.wrappers import ActionMasker

        base = ExpeditionSequenceEnv(sampler_seed=None)
        # `mask_fn`, never an inline lambda and never the string form — both
        # of those have bitten this repo. See env.gym_env.mask_fn.
        self.env = ActionMasker(base, mask_fn)
        self.store = base.store
        quiet_torch()
        if model_path == PLAYBOOK_MODEL:
            # The one arm with no network in it. `PlaybookPolicy` quacks like a
            # MaskablePPO and reads the live engine rather than the observation,
            # so nothing downstream changes -- see tools/playbook_policy.py for
            # why a second CHECKPOINT is not a second opinion.
            from tools.playbook_policy import PlaybookPolicy
            self.model = PlaybookPolicy(self.env)
        else:
            self.model = MaskablePPO.load(model_path, env=self.env, device="cpu")

    def evaluate(self, party: Sequence[PartyMember], zone_id: str, tier: int,
                 num_combats: int, length_band: str = "medium",
                 episodes: int = DEFAULT_EPISODES, seed: int = 0) -> RolloutResult:
        """Run `party` through `num_combats` fights in `zone_id` at `tier`,
        `episodes` times, and report how it went.

        **The episode seed alone is not enough to reproduce a run.** `reset(seed=)`
        reseeds the combat RNG, but which enemy comp each combat draws comes from
        `ExpeditionEnv._sampler_rng`, an independent generator that keeps
        advancing — so the same seed twice faces different monsters. Both are
        reseeded per episode here, which is what makes two parties comparable at
        all: they are ranked over the *same* sequence of fights, not over
        whatever each happened to draw.
        """
        options = build_options(party)
        # Deliberately NOT keyed on the party: episode k must be the same
        # dungeon for every candidate, or the ranking measures the luck of the
        # draw as much as the party.
        scenario = f"{zone_id}|{tier}|{num_combats}|{length_band}|{seed}"
        outcomes = []
        for ep in range(max(1, episodes)):
            ep_seed = zlib.crc32(f"{scenario}|{ep}".encode()) % (2**31)
            self.env.unwrapped._sampler_rng.seed(ep_seed)
            outcomes.append(run_full_expedition_episode(
                self.env, self.model,
                comp_name=None, hero_ids=options["hero_ids"], zone_id=zone_id, tier=tier,
                length_band=length_band, num_combats=num_combats, seed=ep_seed,
                extra_options=options,
            ))
        n = len(outcomes)
        # `quality` arrives on 0-100 from `eval_expedition._run_quality`; it is
        # normalised here so `run_score` stays a fraction and can be multiplied
        # by progress without carrying a scale factor around.
        qualities = tuple(o.quality / 100.0 for o in outcomes)
        quality_parts = tuple(o.quality_parts for o in outcomes)
        progresses = tuple(1.0 if o.completed
                           else o.combats_reached / max(1, o.num_combats)
                           for o in outcomes)
        return RolloutResult(
            episodes=n,
            qualities=qualities,
            progresses=progresses,
            quality_parts=quality_parts,
            mean_hp_frac=statistics.fmean(o.final_hp_frac for o in outcomes),
            success_rate=sum(o.success for o in outcomes) / n,
            completion_rate=sum(o.completed for o in outcomes) / n,
            zero_death_rate=sum(o.zero_death for o in outcomes) / n,
            wipe_rate=sum(o.wiped for o in outcomes) / n,
            stall_rate=sum(o.truncated_alive for o in outcomes) / n,
            mean_deaths=statistics.mean(o.deaths for o in outcomes),
            mean_final_stress=statistics.mean(o.final_stress for o in outcomes),
            mean_combats_reached=statistics.mean(o.combats_reached for o in outcomes),
            num_combats=num_combats,
        )


# The save's quest `length` is an int, and the game's own localization names it:
# `quest_length_1..4` are Short / Medium / Long / Exhausting
# (localization/miscellaneous.string_table.xml). `scripts/map_generator.darkest`
# defines only short/medium/long sizes, so Exhausting — the Darkest Dungeon plot
# quests, which ship hand-authored maps (`DD_map1`) rather than generated ones —
# has no band of its own and is priced as long.
QUEST_LENGTH_TO_BAND = {1: "short", 2: "medium", 3: "long", 4: "long"}


def infer_zone_and_tier(store, enemy_ids: Sequence[str]) -> Optional[tuple[str, int]]:
    """Which (zone, tier) a live enemy line came from, or None.

    `persist.raid.json` records neither: it carries `in_area` (which matches no
    real dungeon id), no difficulty, and its `mash` block is empty in every
    capture. `persist.game.json` does hold `raiddungeon`, but reading it needs
    a fourth save file and it is `"none"` outside a raid, so it cannot be
    verified against the captures this repo has.

    The enemy line itself is the better source, because it is already on the
    board: the game's own encounter tables are keyed by exactly (zone, tier),
    so the monsters present identify the bracket that generated them. Scored by
    how much of the live line an entry covers rather than by exact equality —
    a fight can hold reinforcements, corpses, or a monster already killed.

    Ties break towards the LOWER tier. Overstating difficulty would make a
    retreat look more necessary than it is, and this is used to advise on
    turning back."""
    wanted = set(enemy_ids)
    if not wanted:
        return None
    best: Optional[tuple[int, str, int]] = None
    for entry in store.enemy_comps:
        overlap = len(wanted & set(entry.enemy_ids))
        if not overlap:
            continue
        key = (overlap, entry.zone_id, entry.tier)
        if best is None or overlap > best[0] or (overlap == best[0] and entry.tier < best[2]):
            best = key
    return (best[1], best[2]) if best else None


def band_for(num_combats: int) -> str:
    """Which length band a raw combat count falls in.

    A fallback, for when only a count is known — mid-raid, from the map's unspent
    encounter tiles. **When the quest itself is available, use
    `QUEST_LENGTH_TO_BAND` instead**: the band is a property of the quest, not
    something derived from how many fights it happens to hold.

    It is not cosmetic either way. The band decides firewood (0 short / 1 medium
    / 2 long) and the provision multiplier, so a 4-combat dungeon priced as long
    would be handed recovery it does not have. The real ranges overlap (short
    3-8, medium 6-10) and leave a gap at 11-13, so this picks the containing band
    whose midpoint is nearest, and the nearest band outright when none contains
    the count."""
    containing = [b for b, (lo, hi) in EXPEDITION_LENGTH_BANDS.items()
                  if lo <= num_combats <= hi]
    pool = containing or list(EXPEDITION_LENGTH_BANDS)
    return min(pool, key=lambda b: abs(num_combats - sum(EXPEDITION_LENGTH_BANDS[b]) / 2))
