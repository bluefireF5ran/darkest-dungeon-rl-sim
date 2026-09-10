from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from data.types import BuffDefinition as _BuffDefinition


# Fraction of a burn stack LOST after it ticks. Burn halves: 7 -> 3 -> 1 -> 0.
#
# This is the ONE number in the burn subsystem that is not in a shipped file.
# `hp_dot_burn_decay_percent` is a real buff stat_type and Fire's Edge's
# Firefly applies +0.5 of it, which means there is a base for it to modify --
# but no file states that base. 0.5 is adopted on Fran's own play observation
# ("the numbers go halving each turn"), and it is the reading that makes the
# stat_type coherent: the game's own tooltip is "%+d%% Burn Decay" (es: "a la
# disminucion de Quemadura"), so a POSITIVE amount makes burn expire sooner
# and Firefly turns its big 7-9 stack into a single burst tick.
#
# FALSIFIER, cheap and decisive: capture one burn tick in a live run with no
# Firefly debuff up. `tools/dbwin_capture.py` logs the damage numbers, so a
# 7 -> 3 sequence confirms this and 7 -> 7 refutes it. See DECOMP.md F6.
BURN_BASE_DECAY = 0.5


def burn_next_stack(stacks: int, decay: float) -> int:
    """What is left of a burn stack after it has ticked once."""
    return int(stacks * (1.0 - max(0.0, min(1.0, decay))))


def burn_total_outstanding(stacks: int, decay: float = BURN_BASE_DECAY) -> int:
    """Every point of damage this burn will still deal if left alone.

    Used by the observation's `_is_doomed`, which asks whether a target is
    already dead to its own DoTs. Burn belongs in that sum for the same
    reason bleed and blight do -- but it cannot be `amount * ticks`, because
    a decaying stack deals a different amount on every tick.
    """
    total, s = 0, int(stacks)
    # Terminates for any decay > 0; the bound covers a hypothetical buff
    # stack that cancels decay entirely, which nothing shipped does.
    for _ in range(64):
        if s <= 0:
            break
        total += s
        s = burn_next_stack(s, decay)
    return total


@dataclass
class DotInstance:
    effect_name: str
    damage_per_tick: int
    remaining_ticks: int
    chance: float = 1.0


@dataclass
class ActiveBuff:
    buff_id: str
    remaining_rounds: int | None = None
    # Exact integer delta _apply_buff_to_stats applied, recorded only for
    # combat_stat_multiply on an int-typed stat (max_hp/damage_low/
    # damage_high) — lets _reverse_buff_from_stats undo it precisely instead
    # of re-deriving the delta by dividing back through the multiplier,
    # which can drift by rounding on repeated apply/reverse cycles.
    applied_delta: int | None = None


@dataclass
class RiposteState:
    remaining_rounds: int
    dmg_mod: float = 0.0
    crit_mod: float = 0.0
    on_hit_chance: float = 0.0
    on_miss_chance: float = 0.0
    effect_name: str | None = None


@dataclass
class CarryOverHeroState:
    """A snapshot of one hero's cross-combat-persistent state, fed into
    CombatEngine.reset(carry_over=...) to continue an expedition's next
    combat where the last one left off. HP, stress, Death's-Door status,
    mortality tier and the overstress personality persist; buffs, DoTs,
    guard, riposte, stun and cooldowns do not (skill buffs/debuffs expire in
    the corridor after combat).

    **`affliction_id`/`virtue_id` used to be excluded**, documented as
    "matches the real game: affliction is a per-battle mental state". It does
    not. The game's own data proves the opposite: `zealous_vigil` and
    `turn_back_time` each grant an extra 15 stress heal gated on
    `requirements: ["afflicted"]`, and those are CAMPING skills — camping
    happens between combats, so an afflicted-at-the-campfire hero can only
    exist if the affliction outlived the fight that caused it. Clearing it at
    every combat boundary let a hero at 150 stress start each fight mentally
    fresh, drew a new 25% virtue lottery ticket every time they were stressed
    again, and left the act-out machinery firing on 0.5% of env steps —
    measured. Affliction is the defining hazard of a long expedition, which is
    the band this simulator fails in.

    `camp_buff_ids` exists for the same reason as the personality: camping
    sits BETWEEN combats, and a camp buff lasts until the end of the next
    battle, so it has to survive the reset that starts that battle. Combat
    buffs still do not carry — only buffs granted at a campfire.

    **The DoTs carry too, and the game says so in one number.**
    `rules.json` ships `dotHallwayStepRatio = 0.5`: a damage-over-time
    effect keeps ticking as the party WALKS, at half rate per hallway
    tile. A ratio for ticking a DoT in a corridor is only meaningful if
    the DoT reaches the corridor, so dropping bleeds and blights at the
    combat boundary was a fidelity gap rather than the faithful choice
    the paragraph above claims for buffs. It is also what makes a
    bandage worth carrying: the whole point of curing a bleed between
    rooms is that it would otherwise follow the party into the next one.
    Burn carries on the same evidence — the ratio names no DoT type, and
    singling burn out would be a convention with nothing behind it.

    **The hallway TICK itself is deliberately not modelled**, because
    `ExpeditionSequenceEnv` has no tiles to count — see
    `docs/ASSUMPTIONS.md`. The simulator is therefore gentler than the
    game by whatever half a hallway's ticks are worth, and that is a
    stated bias rather than an invented tile count."""
    hp: int
    stress: int
    is_deaths_door: bool
    mortality_tier: str
    camp_buff_ids: list[str] = field(default_factory=list)
    affliction_id: Optional[str] = None
    virtue_id: Optional[str] = None
    bleeds: list[DotInstance] = field(default_factory=list)
    blights: list[DotInstance] = field(default_factory=list)
    stress_dots: list[DotInstance] = field(default_factory=list)
    burn_stacks: int = 0


@dataclass
class CombatantState:
    id: int
    definition_id: str
    is_hero: bool
    name: str
    position: int

    # Core stats (current, may differ from base due to weapon/armour/buffs)
    hp: int
    max_hp: int
    dodge: float
    prot: float
    speed: int
    crit: float
    atk_bonus: float
    dmg_min: int = 1
    dmg_max: int = 1

    # Resistances
    stun_resist: float = 0.0
    blight_resist: float = 0.0
    bleed_resist: float = 0.0
    debuff_resist: float = 0.0
    move_resist: float = 0.0
    death_blow_resist: float = 0.0

    # Status
    is_stunned: bool = False
    is_deaths_door: bool = False
    is_dead: bool = False
    is_corpse: bool = False
    # Corpses rot away on their own after CombatEngine.CORPSE_ALIVE_ROUND_LIMIT
    # rounds -- real `life_time: .alive_round_limit 3` on the game's corpse
    # monster class. Ticked by CombatEngine._tick_corpse_durations.
    corpse_rounds_remaining: int | None = None
    # The round the corpse appeared in. That round does NOT count against the
    # limit -- see CombatEngine._tick_corpse_durations for why, and for the
    # worked example that fixes the off-by-one.
    corpse_spawn_round: int | None = None
    is_marked: bool = False
    # Real Mark/Vulnerable effects (e.g. "Mark Target"/"Vulnerability Curse
    # N") carry a genuine .duration (2-4 rounds, never permanent) -- ticked
    # by CombatEngine._tick_mark_durations, same shape as
    # stealth_rounds_remaining/guard_rounds_remaining below.
    mark_rounds_remaining: int | None = None
    is_stealthed: bool = False
    stealth_rounds_remaining: int | None = None
    # Sticky, monotonic Death's Door recovery tier — "none" -> "mortality"
    # (after a normal Death's Door recovery) -> "heart_attack" (after a
    # stress-200-while-afflicted heart attack), never downgrades. Real
    # buff amounts (opposite-signed stress terms between the two tiers)
    # confirm these replace, not stack. Carries across combats within one
    # expedition (see CombatEngine.reset()'s carry_over param).
    mortality_tier: str = "none"
    mortality_deltas: dict = field(default_factory=dict)
    """`buff_id -> the exact int delta _apply_buff_to_stats returned`, for the
    mortality tier only.

    Needed because `combat_stat_multiply` on an int-typed stat is LOSSY to
    reverse: `mortalityDMGLowDebuff` is -5%, so damage 10 applies to
    `int(10*0.95) = 9` and dividing back gives `int(9/0.95) = 9`. Without the
    recorded delta a hero loses a point of damage every time the tier is
    applied and cleared. `ActiveBuff.applied_delta` solves the same problem for
    duration-tracked buffs; the mortality tier is not ActiveBuff-tracked
    because it is meant to outlive the combat.
    """
    # Set at the moment a heart attack puts this hero on Death's Door;
    # consumed (and cleared) the next time they're healed back above 0 HP,
    # to decide which recovery tier's buffs to apply. Separate from
    # mortality_tier itself, which should only update at that same
    # heal-triggered edge, matching how the active Death's Door buffs
    # already only apply/reverse at the HP-crossing-zero/recovering edges.
    next_recovery_is_heart_attack: bool = False
    # Round this hero last fired their HeroDefinition.death_door_enter_
    # effects (Flagellant-only in practice) — gates re-firing by the real
    # .enter_effect_round_cooldown so re-entering Death's Door within that
    # many rounds doesn't re-trigger the reaction.
    last_death_door_enter_round: int | None = None

    # Positional
    size: int = 1

    # Guard — remaining_rounds tracked on the guardian (mirrors how
    # RiposteState.remaining_rounds is tracked on whoever has the riposte)
    guard_target_id: int | None = None
    guard_source_id: int | None = None
    guard_rounds_remaining: int | None = None

    # Riposte
    active_riposte: RiposteState | None = None

    # Burn. NOT a DotInstance list, and deliberately so: burn is a single
    # STACK COUNT that decays after it ticks, with no duration and no
    # per-tick amount of its own. Stacking is additive -- a second
    # application adds to whatever is left rather than queueing a second
    # instance the way a second bleed does. See DECOMP.md F6.
    burn_stacks: int = 0

    # DoTs
    bleeds: list[DotInstance] = field(default_factory=list)
    blights: list[DotInstance] = field(default_factory=list)
    # Horror (stress DoT, e.g. Ghoul's howl) — hero-only in practice, same
    # as stress itself; see engine/combat_engine.py's _tick_stress_dots.
    stress_dots: list[DotInstance] = field(default_factory=list)
    # Heal-over-time / regeneration (e.g. Flagellant's Reclaim, "Flag
    # Healdot N") — same shape as bleeds/blights but restores HP each tick;
    # see engine/combat_engine.py's _tick_heal_dots.
    heal_dots: list[DotInstance] = field(default_factory=list)

    # Buffs
    buffs: list[ActiveBuff] = field(default_factory=list)
    # Trinket/quirk buffs whose real rule_type isn't "always" (front/back
    # rank, melee/ranged-only, HP-below/above-X%, at Death's Door, guarded,
    # first-round-only, stress-above, target-status-conditional, ...) --
    # stored raw rather than baked into stats at equip time, since whether
    # they're active can change over the course of combat (rank, HP, guard,
    # round). CombatEngine._sync_conditional_buffs re-evaluates and
    # applies/reverses these against active_conditional_buffs (buff id ->
    # the exact delta _apply_buff_to_stats returned, for exact reversal).
    conditional_buffs: list["_BuffDefinition"] = field(default_factory=list)
    active_conditional_buffs: dict[str, Optional[int]] = field(default_factory=dict)

    # Hero-only
    stress: int = 0
    # Multiplier applied to incoming stress damage (stress_dmg_received_percent
    # buffs, e.g. deathsdoorSRDebuff at +33%, quirks/trinkets at -10%..-30%).
    # Stored as a running multiplier (1.0 = no modifier) since the buff
    # amounts are additive percentages that stack: mult = 1.0 + sum(amounts).
    stress_dmg_received_mult: float = 1.0
    # How many resolve levels short of the quest this hero is. Set at build
    # time from `quest_difficulty_level`; drives the entry stress and the
    # multiplier above, and -- since 2026-09-02 -- the Virtue Chance penalty
    # in `stress.resolve_check`.
    level_shortfall: int = 0
    # The INTEGER stats as they stood BEFORE an affliction's buffs were
    # applied, so the cure can restore them exactly.
    #
    # A multiplicative buff does not round-trip through integer arithmetic:
    # MAXHP-10 on 61 gives `int(61*0.9)` = 54, and reversing gives
    # `int(54/0.9)` = 60. Paranoid's -25% DMG does the same to 10..19, which
    # comes back 9..18. One point per cycle, and a hero can break and recover
    # several times in a long raid.
    #
    # Exactly the three `combat_stat_multiply` handles as ints -- max_hp,
    # damage_low, damage_high. Every other stat a trait touches is a float and
    # reverses cleanly.
    overstress_stats: Optional[dict] = None
    # HP/stress heal modifiers (running multipliers, same additive-percent-
    # stacking convention as stress_dmg_received_mult). Real game stat_types
    # (verified in shared/buffs/base.buffs.json): hp_heal_received_percent
    # (54 buffs, e.g. HPRECOVERY10/-10) and hp_heal_percent (51 buffs, e.g.
    # on_crit_healbuff +25%) apply to the healer's target and performer
    # respectively; stress_heal_received_percent (32 buffs) and
    # stress_heal_percent (3 buffs) are the stress-relief analogs. None of
    # these match the "heal_received_percent" key the engine used to check
    # for (that string appears in zero game files) — it was silently dead.
    # Surprise-chance modifiers this combatant contributes to its SIDE, not
    # to itself: `party_surprise_chance` (9 buffs) and `monsters_surprise_chance`
    # (19) are the game's own stat_types, and a trinket like Book of Intuition
    # (-20% party surprised) protects the whole party from one hero's slot.
    # Summed across living heroes at combat start -- see
    # CombatEngine._roll_surprise.
    party_surprise_chance_mod: float = 0.0
    monsters_surprise_chance_mod: float = 0.0
    hp_heal_received_mult: float = 1.0
    # `hp_heal_received_percent` with `stat_sub_type "eat"` -- healing from
    # FOOD only. `districts_granary_1` is the sole shipped carrier; a test
    # pins that count so a second one cannot arrive unnoticed.
    eat_heal_received_mult: float = 1.0
    hp_heal_produced_mult: float = 1.0
    stress_heal_received_mult: float = 1.0
    stress_heal_produced_mult: float = 1.0
    # Running additive bonus (0.0 = no modifier) to this combatant's own
    # chance to inflict bleed/blight as an attacker — real stat_types
    # "bleed_chance"/"poison_chance" (e.g. Houndmaster/Flagellant's
    # on_crit_bleedchance +20%, Plague Doctor's on_crit_blightchance +20%).
    # Consulted in _resolve_effect's bleed/blight resist rolls as a
    # reduction to the target's effective resist floor (the engine only has
    # one resist-check roll per DoT type, no separate "chance to apply" roll
    # to add a bonus to).
    bleed_chance_bonus: float = 0.0
    # Burn modifiers, all real shipped stat_types (`hp_dot_burn_*`).
    # decay is the fraction of the stack LOST per tick -- the game's own
    # tooltip reads "%+d%% Burn Decay" (es: "a la disminucion de Quemadura"),
    # so a POSITIVE amount makes burn expire sooner. `_amount` is the
    # applier's bonus, `_amount_received` the victim's vulnerability.
    burn_decay_bonus: float = 0.0
    burn_amount_bonus: float = 0.0
    burn_amount_received_bonus: float = 0.0
    blight_chance_bonus: float = 0.0
    # The same shape for the other two resisted payloads. `stun_chance` is the
    # single most-equipped unhandled buff type in the game -- 1.85 of them per
    # sampled expedition, every one inert -- and `move_chance` is the push/pull
    # analogue. Both read as a reduction to the TARGET's effective resist, for
    # the same reason the two above do: the engine has one resist roll per
    # payload and no separate "chance to apply" roll to add a bonus to.
    stun_chance_bonus: float = 0.0
    move_chance_bonus: float = 0.0
    # The attacker-side half of DEBUFF RES. Same shape as the four above: it
    # lowers the TARGET's effective resist rather than raising a separate
    # chance, because there is one roll and the resist is subtracted from it.
    debuff_chance_bonus: float = 0.0
    # `resolve_check_percent` -- a modifier on the VIRTUE side of the Resolve
    # Check. Positive is better for the hero: `mercurial` (a positive quirk)
    # carries +, `ennui` and `never_again_affliction` carry -.
    resolve_check_mod: float = 0.0
    # Real Shieldbreaker mechanic (Serpent's Sway's "SB Aegis" effect,
    # real .health_damage_blocks field): a stacking charge (capped by
    # CombatRules.health_damage_blocks_max, 2) that negates the next
    # non-zero direct-damage hit entirely (no HP loss, no Deathblow check)
    # -- doesn't protect against Stress/DoT/Debuff/KB/Pull/Stun. Naturally
    # resets to 0 each combat (CombatantState is rebuilt fresh by
    # CombatEngine.reset()), matching the real "removed at end of battle".
    damage_blocks: int = 0
    # Real stat_type "crit_received_chance" (e.g. Man-at-Arms' Bellow,
    # conditionally active only while the target is marked/tagged --
    # see CombatEngine._is_conditional_rule_active's actorStatus handling) --
    # raises THIS combatant's chance of being crit against, checked on the
    # attacker's crit roll (engine/resolution.py's check_crit).
    crit_received_bonus: float = 0.0
    is_afflicted: bool = False
    is_virtuous: bool = False
    affliction_id: str | None = None
    virtue_id: str | None = None
    overstress_override: list[tuple[str, float, bool]] = field(default_factory=list)
    """`(trait_id, weight, is_virtue)` from this hero's own
    `overstressed_modifier:` record, resolved against the store at reset.

    Carried on the STATE rather than looked up when it fires, because
    `engine/stress.py` has no store -- and giving it one to read a per-hero
    constant would hand the stress module the whole data layer.
    """
    forced_trait_id: str | None = None
    """Which override the Resolve Check picked, so `_draw_trait` returns that
    instead of drawing. Set by `resolve_check`, read once, never cleared: the
    outcome is permanent for the raid, same as `affliction_id`."""

    # Abomination mode
    mode: str = "human"

    # Per-battle skill limits
    trinket_ids: list[str] = field(default_factory=list)
    """What this hero is WEARING, recorded so an attack can find its trinkets'
    proc effects. The buffs were already flattened into the stat block at
    equip time; this keeps the ids themselves, which procs need."""
    skill_uses: dict[str, int] = field(default_factory=dict)

    # Monster AI skill cooldowns (rounds remaining before reuse) — from
    # MonsterBrain.skill_cooldowns, separate from skill_uses' per-battle/
    # per-turn counters.
    skill_cooldowns: dict[str, int] = field(default_factory=dict)

    # Resolve Level (1-6) and the derived 0-4 tier index used to pick
    # weapon/armour/skill level data. Hero-only in practice (enemies use
    # their own Apprentice/Veteran/Champion variant stat block instead of a
    # resolve level); defaults to 6/4 (max) so combatants built without an
    # explicit level behave exactly as before this was added.
    level: int = 6
    skill_tier: int = 4

    @property
    def occupied_positions(self) -> list[int]:
        """Every rank this combatant stands on, front-most first.

        Was hardcoded to at most two ranks, which silently truncated the 8
        size-3 and 2 size-4 monsters in the loaded data (a Swine Prince or the
        Brigand Pounder covered one rank fewer than it really does). Heroes are
        always size 1."""
        return list(range(self.position, self.position + max(1, self.size)))

    def is_alive(self) -> bool:
        return not self.is_dead and not self.is_corpse
