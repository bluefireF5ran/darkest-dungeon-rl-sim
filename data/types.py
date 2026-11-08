from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:                      # avoids a cycle: the loader imports types
    from .district_loader import DistrictDefinition


class HeroClass(Enum):
    VANILLA = auto()
    DLC = auto()
    MODDED = auto()


class SkillType(Enum):
    MELEE = "melee"
    RANGED = "ranged"
    SELF = "self"
    ALLY_TEAM = "ally/team"


@dataclass
class HeroStats:
    hp: int
    dodge: float
    prot: float
    speed: int
    stun_resist: float
    blight_resist: float
    bleed_resist: float
    debuff_resist: float
    move_resist: float
    disease_resist: float
    death_blow_resist: float
    trap_resist: float


@dataclass
class WeaponTier:
    level: int
    name: str
    atk_bonus: float
    dmg_min: int
    dmg_max: int
    crit_bonus: float
    spd_bonus: int


@dataclass
class ArmourTier:
    level: int
    name: str
    dodge: float
    prot: float
    hp: int
    spd_bonus: int


@dataclass
class SkillLevelData:
    level: int
    atk: float
    dmg_mod: float
    crit: float
    launch: list[int]
    target: list[int]
    is_aoe: bool
    is_crit_valid: bool
    is_continue_turn: bool
    effects: list[str]
    self_effects: list[str]
    heal_min: Optional[int]
    heal_max: Optional[int]
    move_self: int
    move_target: int
    push: int
    pull: int
    valid_modes: list[str]
    per_battle_limit: Optional[int]
    per_turn_limit: Optional[int]
    is_stall_invalidating: bool
    refresh_after_each_wave: bool = False
    """`.refresh_after_each_wave` -- a per-battle-limited skill gets its uses
    back when a new wave of enemies arrives. Ten shipped skills carry it, and
    it only means anything alongside `per_battle_limit`."""
    extra_damage_vs_marked: float = 0.0
    extra_damage_vs_stunned: float = 0.0
    extra_damage_vs_bleeding: float = 0.0
    extra_damage_vs_blighted: float = 0.0
    extra_damage_vs_human: float = 0.0
    extra_damage_vs_unholy: float = 0.0
    extra_damage_vs_eldritch: float = 0.0
    extra_damage_vs_beast: float = 0.0
    is_mark_skill: bool = False
    # Whether raw .target notation had the '@' prefix (target list includes
    # the performer's own rank) — the real signal for "this skill targets
    # allies", not SkillType. Verified against heals (divine_grace: no .type
    # field at all -> defaults to SkillType.MELEE) and support skills
    # (Man-at-Arms' defender/Guard: .type "melee" but .target @1234) — both
    # would be misclassified as enemy-targeting by a SkillType-based check.
    targets_allies: bool = False
    # Abomination's transform carries two separate effect lists gated by the
    # performer's CURRENT mode (.human_effects applied when transforming out
    # of human, .beast_effects when transforming out of beast) — a distinct
    # mechanism from the regular .effect list. Space-separated bare-word
    # identifiers in the raw file, not quoted.
    human_effects: list[str] = field(default_factory=list)
    beast_effects: list[str] = field(default_factory=list)
    # ...and the general form of the same thing. The Abomination is not a
    # special case, he is the only VANILLA case: the convention is one
    # `.<mode>_effects` list per entry in `.valid_modes`, and The Fire's Edge
    # Duelist uses it with `.stance_defensive_effects` /
    # `.stance_aggressive_effects`. Keyed by mode id, so a lookup is
    # `mode_effects.get(performer.mode, [])` and adding a stance hero needs no
    # engine change. The two named fields above remain populated for the
    # Abomination because existing tests and call sites read them by name.
    mode_effects: dict[str, list[str]] = field(default_factory=dict)
    # Monster attack damage range, parsed from the skill's real .dmg field.
    # Distinct from heal_min/heal_max: before this field existed, monster
    # skills had no dedicated damage-range field at all — _build_enemy
    # (over)loaded heal_min/heal_max with the .dmg-derived range and
    # _execute_enemy_turn read it back out as damage, while the skill's own
    # real .heal field (verified present on real heal skills, e.g. Collector
    # Shaman's head_heal ".heal 6 12") was never parsed at all, so it was
    # always None regardless of whether the skill actually healed. That made
    # every monster heal skill execute as a (usually harmless, floor-1-damage)
    # attack against a hero instead of ever healing an ally.
    atk_dmg_min: Optional[int] = None
    atk_dmg_max: Optional[int] = None
    # Real chance-based "also hit N extra target(s)" mechanic on ~30
    # monster skills (bosses/named monsters — Crone's curse_vulnerability/
    # embrace_the_dark, Prophet's prophet_mark/prophet_attack/eye_on_you,
    # Ancestor's ancestor_fuse_two, Brigand Sapper's sapper_detonate,
    # Crow's distract, Formless's attacks). extra_targets_count is always 1
    # in every real occurrence found; extra_targets_chance ranges 0-1 and
    # varies per tier (some A-tier variants are 0, scaling up for B/C).
    # Distinct from the already-implemented '~'-prefix deterministic AoE
    # notation (hits every rank in .target, no roll) — this is a
    # probabilistic ADDITIONAL target beyond whichever one the skill's
    # normal (single-target) selection already picked.
    extra_targets_chance: float = 0.0
    extra_targets_count: int = 0
    # Real flag on a handful of monster self-destruct/flee skills (Bloated
    # Corpse's explode, Crow's escape — both paired with kill_self_queued) —
    # the real game doesn't let a Riposte-primed hero counter these.
    # Defaults True (no known real hero-skill occurrence), so absent this
    # key behaves exactly as before it existed.
    can_be_riposted: bool = True
    # Wiki-verified real semantics (darkestdungeon.wiki.gg): Grave Robber's
    # "Pick to the Face" is described as "Armor Piercing" -- ignore_
    # protection means this attack's damage bypasses the target's PROT
    # entirely. Highwayman's "Tracking Shot" (id take_aim) is described as
    # "Bypass/Remove Stealth" -- ignore_stealth means this skill can target
    # (and its own "Destealth" effect then reveals) a unit that would
    # otherwise be excluded from single-target selection for being
    # stealthed. Also appears on several MONSTER skills (Crow's distract,
    # Cyst's beholder_gaze/burn, Prophet's prophet_mark/prophet_attack/
    # eye_on_you, Brigand Sapper's sapper_detonate) targeting stealthed
    # HEROES -- the real, concrete bug this surfaced: _execute_enemy_skill
    # excluded stealthed heroes from targeting unconditionally, with no
    # bypass for skills that should be able to reveal one.
    # `.self_target_valid false` -- 55 skills carry it and every one is an
    # ally-targeting skill whose `.target` ALSO carries the `@` prefix that
    # opens the performer's own rank. The two are not redundant: `@` says the
    # rank is in the list, this says the performer specifically is not a legal
    # pick. It is what stops the three guard skills (Man-at-Arms Defender,
    # Houndmaster Guard Dog, Antiquarian Protect Me) from guarding their own
    # caster, and what makes the Flagellant's kit coherent -- Reclaim, Redeem,
    # Endure and Suffer all pair an effect on the ALLY with a self-effect on
    # him, so aiming them at himself would be stealing his own stress.
    self_target_valid: bool = True
    # `.required_performer_hp_range <lo> <hi>` as a FRACTION of max HP, or None
    # for no gate. Two skills in the game, both the Flagellant's (Redeem and
    # Exsanguinate at `0 0.40`): he cannot use them until he is under 40% HP,
    # which is the whole shape of the class.
    required_performer_hp_range: Optional[tuple[float, float]] = None
    # `.ignore_guard` -- the attack lands on who it was aimed at even if a
    # guardian has stepped in front. Three skills carry it: the Jester's
    # `dirk_stab`, the Runaway's `backdraft`, and the Shieldbreaker's
    # `break_guard`, which is named for the mechanic and whose whole purpose
    # this is.
    ignore_guard: bool = False
    ignore_protection: bool = False
    ignore_stealth: bool = False
    # -- Backdraft, and the three keys that ARE the skill (2026-08-27) --------
    #
    # The Runaway's `backdraft` names no `.effect` at all: its parsed effect
    # list is literally `['']`, and for months that read as "an inert skill".
    # It is not. The whole payload is these three keys, and nothing parsed
    # them, so the skill really did apply nothing:
    #
    #   .dmg -50% .requires_burning true .dmg_per_burn_stack 0.25
    #   .copy_burn_behind true .ignore_guard true .launch 321 .target 12
    #
    # Half damage that scales +25% per burn stack on the target, only usable
    # against something already burning, and it spreads that burn to whatever
    # stands behind. Fran: *"backdraft makes the burn damage from an enemy hit
    # 2 enemies, essentially doubling burn damage, specially potent with 2"*.
    #
    # No other shipped skill carries any of the three, so every default here is
    # the pre-2026-08-27 behaviour for everything else.
    requires_burning: bool = False
    dmg_per_burn_stack: float = 0.0
    copy_burn_behind: bool = False


@dataclass
class SkillDefinition:
    id: str
    name: str
    skill_type: SkillType
    levels: dict[int, SkillLevelData] = field(default_factory=dict)


@dataclass
class HeroMode:
    id: str
    is_raid_default: bool
    stress_damage_per_turn: int = 0
    affliction_combat_skill_id: Optional[str] = None
    battle_start_effect_ids: list[str] = field(default_factory=list)
    """`.battle_start_effect_ids` -- effects applied to the hero at the start
    of a combat *because of the stance they are standing in*. Two carriers,
    both stance heroes, so this is dormant for the other eighteen classes."""


@dataclass
class MoveSkillData:
    """A hero's innate voluntary reposition ability, from the real
    `combat_move_skill: .id "move" .move <backward> <forward> .launch 4321`
    record — distinct from any skill's own `.move` push/pull/move_self
    field. Verified per-hero (backward, forward): e.g. crusader(1,1),
    leper(0,1), arbalest(2,0), jester(3,3) — varies by class, not universal."""
    backward: int
    forward: int
    launch: list[int]


@dataclass
class HeroDefinition:
    id: str
    name: str
    hero_class: HeroClass
    tags: list[str]
    base_stats: HeroStats
    weapon_tiers: list[WeaponTier]
    armour_tiers: list[ArmourTier]
    skills: list[SkillDefinition]
    deaths_door_buffs: list[str]
    modes: list[HeroMode]
    controlled_rank: Optional[int]
    # Per-class passive that fires whenever this hero lands ANY crit (any
    # skill) — from the real `crit: .effects "on_crit_X"` record, separate
    # from a skill's own .effect list. None if absent (shouldn't happen for
    # a real hero, but defensive).
    crit_effect_id: Optional[str] = None
    move_skill: Optional[MoveSkillData] = None
    # `skill_selection: .can_select_combat_skills` -- the game's own statement
    # that this class has NO 4-of-7 to choose: it carries all seven always and
    # a MODE decides which are usable. Shipped `false` on exactly the
    # Abomination and the Duelist, `true` on the other eighteen, and `false` on
    # the Sibyl -- which is the point. `env.expedition_env.UNRESTRICTED_HEROES`
    # was a hand-kept copy of that fact, so a modded stance class could never
    # join it; `unrestricted_heroes(store)` derives it from here instead.
    # Defaults True: a hero file with no `skill_selection` line picks four.
    can_select_combat_skills: bool = True
    # Death's Door has 3 real debuff tiers, not 1: `.buffs` (active while at
    # Death's Door, existing `deaths_door_buffs` field) is the strongest;
    # `.recovery_buffs` (weaker, "mortality") applies for the rest of the
    # expedition after being healed off Death's Door once;
    # `.recovery_heart_attack_buffs` (a different, not-strictly-worse tier —
    # verified via real buff amounts, e.g. its stress term is NEGATIVE where
    # mortality's is positive) replaces (not stacks with) the mortality tier
    # if the Death's Door visit was caused by a stress-200 heart attack.
    recovery_buffs: list[str] = field(default_factory=list)
    recovery_heart_attack_buffs: list[str] = field(default_factory=list)
    # Real `deaths_door: .enter_effects X .enter_effect_round_cooldown N`
    # fields — a hero-specific trigger fired once on ENTERING Death's Door
    # (distinct from the passive .buffs applied while there), gated by a
    # round-based cooldown to prevent re-firing every time death's-door
    # state is re-entered in quick succession. Only Flagellant uses this in
    # the real game (his "gain buffs + heal the rest of the party 10% max
    # HP" reaction) — confirmed via a project-wide grep, both fields empty/
    # zero for every other hero.
    death_door_enter_effects: list[str] = field(default_factory=list)
    death_door_enter_round_cooldown: int = 0
    # Real `death_reaction: .target_allies bool .target_enemies bool
    # .effects "X"` records — fire once when this hero actually DIES (not
    # just Death's Door). Only Flagellant has these in the real game: "Flag
    # Last Gift" (targets allies, heals the rest of the party 10 HP) and
    # "Flag Last Insult" (targets enemies, 125% guaranteed stun on all of
    # them) — his "On Death" reactions per HEROES/Heroes' own doc.
    death_reactions: list["DeathReaction"] = field(default_factory=list)
    # Real `riposte_skill: .id "riposte1" .atk X% .crit Y%` record — the
    # counter-attack's OWN fixed base ACC/crit, only present for the 3 real
    # riposte-capable classes (Man-at-Arms 90%/0%, Bounty Hunter/Highwayman
    # 85%/5% each) and genuinely doesn't scale with the setup skill's own
    # level (HEROES/Heroes/Man-at-Arms' own doc: "Riposte's ACC stays fixed
    # at 90 and DOES NOT scale with Retribution's Skill Level ... IS
    # affected by ACC modifiers such as quirks/trinkets/Buffs/Debuffs").
    # None for every other hero (no native Riposte).
    riposte_acc: Optional[float] = None
    riposte_crit_mod: Optional[float] = None
    # Real `overstressed_modifier: .override_trait_type_ids <id...>
    # .override_trait_type_chances <w...>` record. When present, a failed
    # Resolve Check does NOT draw from the rollable pool -- this hero's
    # outcome is one of these ids, picked by weight. One carrier in the whole
    # install: the Flagellant, `berserk` at 1.0, which is the Rapturous the
    # wiki lists as his personal affliction.
    #
    # The pair shape is the game's own: `.random_monster_class_ids` /
    # `.random_monster_class_chances` sit four entries away in the same field
    # table and are a weighted draw, not a gate. So the chances are WEIGHTS,
    # and a present record always fires.
    overstress_override_ids: list[str] = field(default_factory=list)
    overstress_override_chances: list[float] = field(default_factory=list)


@dataclass
class DeathReaction:
    target_allies: bool
    target_enemies: bool
    effects: list[str]


@dataclass
class BuffDefinition:
    id: str
    stat_type: str
    stat_sub_type: str
    amount: float
    duration: Optional[int]
    duration_type: str
    rule_type: str
    is_false_rule: bool
    remove_on_battle_complete: bool
    remove_if_not_active: bool
    # Real .rule_data payload (base.buffs.json) -- the threshold/selector a
    # non-"always" rule_type checks against: a rank index for "in_rank", a
    # max_hp fraction for "hpbelow"/"hpabove", a raw stress value for
    # "stress_above", a status name ("tagged"/"bleeding"/...) for
    # "actorStatus"/"is_actor_status". Unused (stays 0.0/"") for "always".
    rule_data_float: float = 0.0
    rule_data_string: str = ""


@dataclass
class EffectDefinition:
    name: str
    target: str
    chance: float
    on_hit: bool
    on_miss: bool
    is_apply_once: bool
    is_queued: bool
    can_apply_on_death: bool
    # Damage over time
    bleed_amount: Optional[int] = None
    bleed_rounds: Optional[int] = None
    blight_amount: Optional[int] = None
    blight_rounds: Optional[int] = None
    # Burn (`.dotBurn N`) is a THIRD DoT and does not share bleed/blight's
    # shape: it carries no `.duration`, because it is a STACK COUNT that
    # decays rather than a per-tick amount with a tick budget. Every shipped
    # burn effect also sets `.dot_irresistible`, so there is no resist roll —
    # see engine/combat_engine.py::_tick_burn and DECOMP.md F6.
    burn_amount: Optional[int] = None
    dot_irresistible: bool = False
    # Status
    stun_chance: Optional[float] = None
    # Movement
    # `.shuffletarget` moves the effect's target to a random rank;
    # `.shuffleparty` scrambles that target's whole side. 24 effects between
    # them and neither was parsed, so Bounty Hunter Flashbang and Plague
    # Doctor Disorienting Blast -- two skills whose ENTIRE payload is the
    # shuffle -- resolved to a plain attack.
    #
    # No resist key appears on any of the 24, which is the evidence that a
    # shuffle is not gated by MOVE RES: push and pull carry the distance the
    # move-resist roll reads, and these carry nothing to roll against.
    shuffle_target: bool = False
    shuffle_party: bool = False
    push_distance: Optional[int] = None
    pull_distance: Optional[int] = None
    # Healing
    heal_min: Optional[int] = None
    heal_max: Optional[int] = None
    # Percent-of-max-HP heal (real key .heal_percent, e.g. Flagellant's
    # Redeem -- "Flag Super Heal N"/"Flag HealSelf N", ~33-50% of max HP --
    # and his death's-door-enter party heal, "flagellant_death_door_enter"
    # at 10%). Distinct from heal_min/heal_max (a flat amount); never
    # appears alongside them on the same real effect.
    heal_percent: Optional[float] = None
    # Heal-over-time / regeneration (real key .dotHpHeal, e.g. Flagellant's
    # Reclaim -- "Flag Healdot N") -- same shape as bleed/blight DoTs but
    # restoring HP each tick instead of dealing damage.
    heal_dot_amount: Optional[int] = None
    heal_dot_rounds: Optional[int] = None
    # Stress
    stress_damage: Optional[int] = None
    stress_heal: Optional[int] = None
    # Horror (a real status effect, darkestdungeon.wiki.gg/wiki/Horror_
    # (Darkest_Dungeon)) — stress-damage-over-time, e.g. Ghoul's howl
    # ("Ghoul Stress DOT"), Madman's accusation ("Madman Horror 1/2"),
    # Drowned Pirate's offkilter_jig ("Stress AOE DoT 1/2"). Previously
    # unimplemented due to a parser key mismatch: the real .darkest field is
    # .dotStress, not stress_damage_per_round (a key that never appears in
    # any real file) — same bug class as the historic dmg_min/dmg_max and
    # unquoted-trailing-decimal parser bugs. Fixed in data/effect_loader.py;
    # this in turn means laudanum's .clearDotStress is no longer inert (see
    # clears_stress_dot below).
    stress_damage_per_round: Optional[int] = None
    stress_dot_rounds: Optional[int] = None
    # Cure (removes bleed and blight, e.g. Plague Doctor's Battlefield Medicine)
    cures_dots: bool = False
    # Provision-specific, DoT-type-specific cures (bandage/antivenom) — real
    # keys are .cure_bleed / .cure_poison, distinct from the bare .cure
    # above which clears both at once.
    cures_bleed: bool = False
    cures_blight: bool = False
    # `.cure_burn` is its OWN key, sitting beside .cure_bleed/.cure_poison in
    # the binary's string table — which is the evidence that a bandage or an
    # antivenom must NOT clear burn. The bare `.cure` clears bleed+blight only.
    cures_burn: bool = False
    # -- Controlled Burn's rank ZONE (2026-08-27) ----------------------------
    #
    # `.controlled_burn_amount N .controlled_burn_duration N` -- five effects,
    # all the Runaway's. The burn is attached to the RANK, not to the monster
    # standing in it, which is what makes the skill pair with pull and push:
    # Fran, *"making all the enemies that ocupy that rank gain burn damage"*.
    # `engine/combat_engine.py` holds the painted ranks and applies them at
    # end of round to whoever is standing there THEN.
    controlled_burn_amount: Optional[int] = None
    controlled_burn_duration: Optional[int] = None
    # laudanum's real .clearDotStress field — clears the Horror stress-DoT
    # status (CombatantState.stress_dots, engine/combat_engine.py).
    clears_stress_dot: bool = False

    # --- 2026-08-23 audit. Keys the shipped data carries on effects this
    # simulator FIELDS (named by a hero skill or a loadable monster) and that
    # nothing read. Ranked by live carriers, the same method the buff sweep
    # used -- not a guess at what matters.
    torch_change: int = 0
    """Signed torch delta, from `.torch_increase` / `.torch_decrease`.

    Nine hero skills raise the torch and four monsters drain it, and 104 of
    the 141 comps in the pool equip at least one. Everything DOWNSTREAM was
    already wired -- the 234 `lightbelow`/`lightabove` buffs, the surprise
    draw, the band table -- so a Crusader's +24 was resolving a whole party's
    conditional buffs against a number that could never move."""

    clears_mark: bool = False
    """`.untag`. Arbalest/Musketeer's Flare clears it off the whole ally side;
    Flagellant's Suffer clears it off the ally he takes it from."""

    clears_stun: bool = False
    """`.unstun`. Flare and Skeet Shot only."""

    steal_dot_stat_type: str = ""
    """`.steal_buff_stat_type`, one of `hp_dot_bleed` / `hp_dot_poison`.
    Flagellant's Suffer -- the skill's whole point is pulling an ally's DoTs
    onto himself, and without this it was a plain mark-swap."""

    grants_bonus_action: bool = False
    """`.bonus_action_next_turn`. Duelist's Coup de Grace only, and gated by
    `requires_kill_target`. `.initiative_change 1` rides the same effect and
    is subsumed: the free action IS the initiative change."""

    requires_kill_target: bool = False
    """`.requires_kill_target` -- the payload applies only if the skill KILLED
    its primary target. Checked after the `.chance` roll, deliberately, so
    adding it does not shift the RNG stream of any skill that already drew."""
    # Kill
    is_kill_effect: bool = False
    # Stat buff/debuff (from combat_stat_buff effects)
    is_stat_buff: bool = False
    buff_stat_type: Optional[str] = None
    damage_low_multiply: Optional[float] = None
    damage_high_multiply: Optional[float] = None
    damage_low_add: Optional[float] = None
    damage_high_add: Optional[float] = None
    attack_rating_add: Optional[float] = None
    crit_chance_add: Optional[float] = None
    protection_rating_add: Optional[float] = None
    defense_rating_add: Optional[float] = None
    speed_add: Optional[int] = None
    max_hp_percent: Optional[float] = None
    # Status key management (mark, stealth, etc.)
    key_status: Optional[str] = None
    remove_status: Optional[str] = None
    # Real .tag 1 field -- the actual "apply Marked/Vulnerable status"
    # signal (e.g. "Mark Target"/"Vulnerability Curse N"/"BH Mark Target"/
    # "HM Mark Target", and boss "X Mark" effects), NOT key_status (which is
    # a same-named-but-unrelated *conditional damage bonus* mechanism --
    # see CombatEngine._compute_mark_damage_mult). .tag 1 is also reused by
    # several unrelated self-buffs (Bulwark, "Crusader Bulwark Mark",
    # "Leper Mark Self", "Solo Mark Self") that all target "performer" --
    # verified every real Marked/Vulnerable-application effect targets
    # "target" instead, so is_mark_effect is only actually consulted
    # against that target shape (see _apply_effect_to_target).
    is_mark_effect: bool = False
    # Real .monsterType field ("man"/"beast"/"unholy"/"eldritch", 25 real
    # occurrences -- Bounty Hunter's "Man Killer N" on collect_bounty,
    # "+DMG vs Human" per his own doc, plus similarly-shaped Beast/Unholy/
    # Eldritch Killer effects on other classes): same shape as key_status
    # (target=="performer" conditional damage bonus, checked in
    # CombatEngine._compute_mark_damage_mult) but gated on the attack
    # TARGET's permanent monster-type tag (EnemyDefinition.enemy_types)
    # instead of a transient status.
    monster_type_condition: Optional[str] = None
    # Real .health_damage_blocks N field (Shieldbreaker's "SB Aegis", from
    # Serpent's Sway -- "Adds up to 2 Aegis Blocks... When hit by any non-
    # zero direct damage, an Aegis Block is consumed, preventing damage/
    # Deathblow check" per her own doc). Genuinely new mechanic, not seen
    # anywhere else in the engine before this -- real rules.json caps the
    # stack at health_damage_blocks_max (2), see CombatRules. Doesn't
    # protect against Stress/DoT/Debuff/KB/Pull/Stun (only the raw HP
    # damage a direct attack roll deals, in engine/resolution.py).
    health_damage_blocks: Optional[int] = None
    # Guard / Riposte
    is_guard: bool = False
    is_clear_guarding: bool = False   # performer stops guarding whoever it guards
    is_clear_guarded: bool = False    # target stops being guarded
    swap_source_and_target: bool = False  # e.g. Antiquarian's Protect Me!: target ends up guarding performer
    is_riposte: bool = False
    riposte_on_hit_chance_add: Optional[float] = None
    riposte_on_miss_chance_add: Optional[float] = None
    riposte_effect: Optional[str] = None
    riposte_duration: Optional[int] = None
    is_clear_riposte: bool = False
    # Stealth
    stealth_duration: Optional[int] = None
    is_unstealth: bool = False
    # Generic buff references (.buff_ids "X" "Y") — e.g. blight_debuff_N /
    # bleed_debuff_N consecutive-DoT stacking penalties, stealthed_crit_chance_buff,
    # MAA's "Command" effects. Resolved against store.buffs and applied via
    # _apply_buff_to_stats; reversed via duration below if set (None = permanent
    # for the rest of combat, same as trinkets/quirks).
    buff_ids: list[str] = field(default_factory=list)
    duration: Optional[int] = None
    # Real .buff_duration_type field (52 real occurrences, always the value
    # "combat_end" when present -- e.g. Grave Robber's Toxin Trickery
    # buffs "Shadow Blood N"/"GR Self Speed N", Crusader's "Bulwark N"/
    # "Crusader Bulwark Mark", Leper's "Leper Mark Self"/"Leper Intimidate
    # Mark", Jester's "Solo Mark Self"): overrides .duration entirely --
    # the buff lasts for the rest of combat regardless of the (often small,
    # e.g. 1-2) numeric .duration value, not a real N-round countdown.
    # Default "rounds" preserves normal countdown behavior for every effect
    # that doesn't set this.
    buff_duration_type: str = "rounds"
    # Real .buff_type/.buff_amount pair (29 occurrences) -- a THIRD, distinct
    # inline-buff mechanism from buff_ids and the combat_stat_buff+specific-
    # field pattern (INLINE_STAT_FIELD_MAP in engine/combat_engine.py):
    # buff_type names the stat_type directly (verified real values:
    # hp_heal_received_percent -- e.g. Arbalest's "Arb Stacking Heal N"/
    # base "Stacking Heal N", several monster heal-received debuffs;
    # crit_received_chance -- e.g. Shieldbreaker's "Expose Crit Debuff N",
    # Arbalest/Musketeer's "Grapeshot Vulnerability N"). Applied via
    # _apply_buff_to_stats through a synthesized BuffDefinition, same
    # pattern as the inline combat_stat_buff fields.
    inline_buff_type: Optional[str] = None
    inline_buff_amount: Optional[float] = None
    # .set_mode human/beast — the real effect field driving Abomination's
    # transform (switch_mode_beast_self/switch_mode_human_self in
    # base.effects.darkest). Not a guess: found by grepping those exact
    # effect names.
    set_mode: Optional[str] = None
    # .kill_enemy_types corpse — the real "clear_corpses" effect (Leper's
    # Focus, Occultist's Daemon's Pull, Plague Doctor's Disorienting Blast
    # all use it alongside a push/pull). Removes corpses from the skill's
    # own target ranks on the same side as the primary target.
    is_clear_corpses: bool = False
    # Reinforcements — real .summon_count/.summon_monsters/.summon_chances
    # fields on stall_summon_A/B/C (effects/base.effects.darkest). Were
    # previously dropped silently (not in this dataclass at all), even
    # though the effect records themselves parsed fine as generic effects.
    summon_count: Optional[int] = None
    summon_monster_ids: list[str] = field(default_factory=list)
    summon_chances: list[float] = field(default_factory=list)
    # `.summon_ranks` (1-indexed, preferred ranks) and `.summon_limits` (how
    # many of that monster may be alive at once). Only 12 of the summon
    # effects carry ranks and fewer carry limits, so both are optional and
    # absent means "any open rank" / "no cap beyond board space".
    # `.summon_erase_data_on_roll` -- draw WITHOUT replacement. Carried by
    # exactly three effects, the `stall_summon_A/B/C` reinforcement waves, and
    # it is what reconciles two otherwise contradictory shapes: those spawn one
    # of each candidate, while `SummonTentacle A` (count 2, ONE candidate) must
    # be able to draw the same monster twice.
    summon_erase_on_roll: bool = False
    summon_ranks: list[int] = field(default_factory=list)
    summon_limits: list[int] = field(default_factory=list)


@dataclass
class EnemyDefinition:
    id: str
    name: str
    variant: str
    stats: HeroStats
    size: int
    actions_per_round: int
    skills: list[SkillDefinition]
    enemy_types: list[str]
    monster_brain_id: str
    loot_code: str
    corpse_id: Optional[str]
    location: str
    # Real `wave_spawning: .prefers_front True` record — only present on
    # the subset of monsters used as mid-combat reinforcements/summons
    # (verified: cultist_brawler has it, cultist_witch doesn't). Consulted
    # by _spawn_reinforcements to prefer a front-most open rank when set.
    prefers_front: bool = False
    # The same `battle_modifier:` record's four surprise flags, present on
    # every one of the 383 monster files and read by nobody until 2026-08-21 --
    # so surprise, added the same day, was rolled against monsters the game
    # says cannot be surprised and monsters it says ALWAYS surprise you.
    #
    #   can_surprise / can_be_surprised    True,True on 205 monsters
    #                                      False,False on 162 -- the Ancestor
    #                                      forms, the Crimson Court bodies, the
    #                                      Baron. This is how the game encodes
    #                                      the wiki's "impossible in the Darkest
    #                                      Dungeon and the Farmstead": per
    #                                      monster, not per region.
    #   always_surprise                    exactly the Shambler line and the
    #                                      Fanatic -- the wiki's "unavoidable"
    #                                      list, which is what confirms these
    #                                      flags mean what they say.
    #   always_be_surprised                shipped False everywhere.
    #
    # Defaults are permissive (True/True/False/False) so a monster file without
    # the record behaves exactly as before.
    # The monster DIRECTORY name -- `necromancer_B` lives in `monsters/
    # necromancer/`, so its base class is `necromancer`. Taken from the path
    # rather than by stripping a `_[A-E]` suffix off the id, because the suffix
    # convention is not universal (`brigand_barrel_D`, `com_crocodile`) and a
    # regex that is wrong here silently mis-links a boss to its adds.
    base_class: str = ""
    # `life_link: .base_class "X"` -- 177 monsters carry it, across 19 parents.
    # It means **this monster dies when a monster of class X dies**, and it is
    # inert when no such monster is on the board. That guard is the whole
    # reading: `skeleton_arbalist_A` is linked to `necromancer`, and skeletons
    # fill ordinary Ruins corridors with no Necromancer anywhere -- 1239 of the
    # 1266 comp slots holding a linked add have no parent present, against 27
    # that do, and all 27 are boss or named comps.
    life_link_base_class: "str | None" = None
    # `battle_modifier: .can_be_damaged_directly false` -- 12 monsters, and the
    # one that matters is the Hag's **empty** cauldron: a 100 HP, size-2 prop
    # that fills ranks 1-2 of her boss fight and cannot be attacked at all.
    # `cauldron_full` (the one holding a captured hero) does NOT carry the
    # flag, which is the whole design: you break the pot your friend is in, not
    # the empty one.
    can_be_damaged_directly: bool = True
    # `loot: .code "T" .count 2` -- how many draws of `loot_code` the
    # monster pays on death. The Necromancer draws twice; most monsters
    # ship no `.count` and draw once. Read by the campaign simulator
    # (campaign/loot.py) to price a battle; nothing in combat reads it.
    loot_count: int = 1
    can_surprise: bool = True
    can_be_surprised: bool = True
    always_surprise: bool = False
    always_be_surprised: bool = False
    # Real `battle_modifier: .accelerate_stall_penalty bool
    # .disable_stall_penalty bool` record. accelerate_stall_penalty (wiki-
    # confirmed: Bone Arbalist/Courtier/Rabble/Soldier, Carrion Eater,
    # Crone, Fungal Artillery, Madman, Pelagic Guardian/Shaman, Swine
    # Drummer/Skiver, Webber, and others) — when EVERY living enemy has
    # this, stalling penalties escalate much faster (reinforcements after
    # 2 stall rounds instead of 4, no stress effect). disable_stall_penalty
    # (broad: most boss/Ancestor forms, brigand_blood at every tier, etc.)
    # — when ANY living enemy has this, the fight is exempt from stalling
    # entirely. See CombatEngine._check_stall.
    accelerate_stall_penalty: bool = False
    disable_stall_penalty: bool = False


@dataclass
class TrinketDefinition:
    id: str
    name: str
    rarity: str
    class_requirements: list[str]
    buffs: list[str]
    limit: int
    attack_effects: list[str] = field(default_factory=list)
    """`attack_skill_additional_effects` -- effect names this trinket adds to
    its wearer's attack, on top of whatever the skill already applies.

    Fifteen `*_additional_effects` proc hooks ship; this is the only one any
    trinket in the comp pool carries (28 equipped slots across Keening Bolts,
    Thirsting Blade and Sunstone Searing). The other fourteen -- riposte kill,
    on-dodge, was-hit, turn-end and so on -- have zero pool carriers and are
    recorded in ASSUMPTIONS.md rather than shipped as dead code."""
    set_id: str = ""
    """Which trinket SET this is a member of, from the entry's own `set_id`.

    Wearing two members of one set grants that set's extra buffs -- see
    `DataStore.trinket_sets` and `CombatEngine.apply_trinkets_and_quirks`.
    Empty for the 98% of trinkets that belong to no set."""


@dataclass
class QuirkDefinition:
    id: str
    name: str
    is_positive: bool
    is_disease: bool
    classification: str
    buffs: list[str]
    incompatible_quirks: list[str]


@dataclass
class CampingSkillEffect:
    """One effect of one camping skill, from `raid/camping/default.camping_skills.json`.

    `target` is the game's `selection`: self | individual | party_other | party.
    `stat_type` is its `type` — the real vocabulary across all 64 skills is
    buff (89), stress_heal_amount (28), stress_damage_amount (13),
    health_heal_max_health_percent (9), reduce_ambush_chance (5),
    remove_disease (4), remove_bleeding (3), remove_poison (3), loot (3),
    remove_deaths_door_recovery_buffs (2), reduce_torch (1),
    health_damage_max_health_percent (1).

    `requirements` gates the effect on hero state (e.g. Zealous Vigil heals 25
    stress unconditionally and a further 15 only when `afflicted`); an effect
    with unmet requirements does not fire. `duration` has no counterpart in the
    JSON and stays None — a camp `buff` lasts until the end of the next battle,
    which is duration handling the consumer owns, not the data.
    """
    target: str
    amount: float
    stat_type: str
    stat_sub_type: str
    duration: Optional[int]
    requirements: list[str] = field(default_factory=list)
    chance: float = 1.0


@dataclass
class CampingSkillDefinition:
    id: str
    cost: int
    use_limit: int
    hero_classes: list[str]
    effects: list[CampingSkillEffect]


@dataclass
class TraitActOut:
    """One row of a trait's `combat_start_turn_act_outs` table — the real
    per-personality behaviour distribution, rolled once at the start of an
    overstressed hero's turn.

    `chance` is a WEIGHT, not a probability: the game normalises the rows of a
    trait against their own sum (Fearful is nothing 26 / bark_stress 4 /
    change_pos 4 / ignore_command 4 = 38, so it acts out 12/38 = 31.6% of turns
    and barks on only 4/38 = 10.5%).

    `number_value`/`string_value` come from the row's `data` object and mean
    different things per `kind`: for `attack_friendly`/`attack_self` the number
    is the fraction of the hero's own damage dealt to the victim and the string
    names an effect in `effects/base.effects.darkest`; for `change_pos` the
    number is how many ranks to shuffle; for the stress/buff kinds the string is
    the whole effect.

    `valid_hero_class_ids` restricts the row to specific classes — every
    `attack_friendly` row is per-class, which is how the real game gives an
    Abusive Crusader a stunning ally-hit and an Abusive Leper a 33% one.
    """
    kind: str
    chance: int
    number_value: float = 0.0
    string_value: str = ""
    valid_hero_class_ids: list[str] = field(default_factory=list)


@dataclass
class TraitDefinition:
    """An overstress personality from `shared/trait/trait_library.json` (plus
    the DLC libraries) — what a hero actually becomes on a failed or critically
    passed Resolve Check.

    `overstress_type` is "affliction" or "virtue". `buff_ids` is the trait's
    COMPLETE effect list, including what looked like a separate baseline: the
    -15 resists and MAXHP-10 that every affliction shares are just entries in
    each affliction's own list, and the six +25 resists are entries in every
    virtue's. There is no baseline to add on top.
    """
    id: str
    overstress_type: str
    buff_ids: list[str] = field(default_factory=list)
    act_outs: list[TraitActOut] = field(default_factory=list)
    reactions: dict[str, float] = field(default_factory=dict)
    """`reaction_act_outs`, as {kind: chance}, non-zero rows only.

    Unlike `act_outs` these are PROBABILITIES, not weights: each row is an
    independent roll against its own chance when the situation arises, and
    they do not compete with one another or normalise. The shipped afflictions
    use 0.33 throughout.

    `comment_*` rows are dropped at load -- their payload is a `BarkStress`
    voice line, not behaviour."""
    # The game's own gate on "can ordinary overstress roll this?". `test` and
    # the Flagellant's `berserk` are both is_generated False — berserk comes
    # from a specific Crimson Court trigger, not from a failed Resolve Check.
    is_generated: bool = True
    # `color_madness` is the only trait with a non-zero value here (-1.0).
    # Semantics are not documented in the file; treated as "never rolled by
    # ordinary overstress", which is what the real game shows — Refracted comes
    # from Farmstead content, not from a normal raid. See is_rollable.
    generate_chance_modifier: float = 0.0

    @property
    def is_virtue(self) -> bool:
        return self.overstress_type == "virtue"

    @property
    def is_rollable(self) -> bool:
        """Whether a failed (or critically passed) Resolve Check can produce
        this trait. Leaves 7 afflictions and 5 virtues — the roster the wiki
        documents."""
        return self.is_generated and self.generate_chance_modifier > -1.0

    def act_outs_for(self, hero_class: str) -> list[TraitActOut]:
        """The rows eligible for one hero, which is the ONLY set the weights
        may be normalised over.

        `attack_friendly` ships as one row per hero class (Abusive has 19 of
        them, each weight 1), so normalising over the raw table would make
        Abusive act out 64.7% of turns instead of its real 29.4% — the 18 rows
        belonging to other classes would all count as live outcomes. Filtering
        first gives an Abusive Crusader nothing 12 / bark 4 / attack_friendly 1
        = 17, i.e. the 29.4% the wiki reports."""
        return [a for a in self.act_outs
                if not a.valid_hero_class_ids or hero_class in a.valid_hero_class_ids]


@dataclass
class CompositionDefinition:
    name: str
    heroes: list[str]
    location: str


@dataclass
class HeroLoadout:
    """One hero's real equipped loadout within a specific comp — the 4 (of
    ~7) active skills a player would actually bring, plus camp skills,
    trinkets, and quirks. Trinket/quirk/camp-skill ids are captured for
    reference but not applied by CombatEngine (no trinket-buff or
    camping mechanics in this simulator); skill_ids is the part that
    matters for combat, restricting CombatEngine.get_hero_skill_ids."""
    hero_id: str
    skill_ids: list[str]
    camp_skill_ids: list[str] = field(default_factory=list)
    trinket_ids: list[str] = field(default_factory=list)
    quirk_ids: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class CompLoadoutEntry:
    name: str
    location: str
    heroes: list[HeroLoadout] = field(default_factory=list)
    # The comp's PREVIOUS name, when the source export carries one. Comps get
    # renamed wholesale (2026-08-23 renamed 66 of 138), and `The Old Road` --
    # pinned by `data/tutorial_loader.py` and by `tools/eval_tutorial.py`, the
    # one exactly reproducible ruler here -- became `Ragged Band: Heist` with
    # the old name preserved only here. Resolve a comp by name through
    # `DataStore.comp_loadout_by_name`, never by indexing `comp_loadouts`
    # directly, or a rename silently returns None.
    alias: str = ""
    # How many hero slots the source JSON declared with a NON-EMPTY heroClass.
    # `heroes` holds only the ones that RESOLVED, and the loader drops the rest
    # silently, so without this a comp fielding a fan-made class this project
    # cannot load is indistinguishable from a genuinely smaller party -- and it
    # would train as one. An EMPTY heroClass is not counted: the export pads
    # every comp to four slots, so the 2-hero tutorial party legitimately
    # carries two blanks.
    declared_heroes: int = 0
    # The raw `heroClass` strings the source JSON asked for, in file order.
    # `declared_heroes` alone says a slot was lost; this says WHICH, which is
    # the difference between "this comp fields a class we cannot load" (fine,
    # and `build_comp_pool` excludes it) and "a name map dropped a class we
    # DO load" (a silent bug). `tests/data/test_loader.py` uses it to tell them
    # apart.
    declared_classes: tuple[str, ...] = ()
    # Which folder under `metadata/COMPS/` this comp came out of --
    # `"curated_comps"` (Fran played it and vouches for it) or
    # `"composed_comps"` (a machine recombined human-authored hero blocks for
    # training coverage; nobody has played it). Both are legitimate TRAINING
    # material and `build_comp_pool` reads both, which is why the field exists:
    # a caller that needs the *human* claim -- `bridge/embarkplan.py`, choosing
    # a party to send into a live campaign -- cannot otherwise tell them apart,
    # and inferring provenance from a comp's contents is exactly what the
    # two-folder split was created to avoid. Empty for the legacy layout.
    source: str = ""


@dataclass
class ProvisionDefinition:
    """A purchasable provision item (inventory/base.supply.inventory.items.darkest),
    resolved against its real mechanical effect in store.effects via
    effect_name (same id as the item id — e.g. 'bandage' names both the item
    and its effect record)."""
    id: str
    stack_limit: int
    purchase_gold_value: int
    effect_name: str


@dataclass
class EnemyCompEntry:
    """One enemy encounter group from a real dungeons/<zone>/<zone>.<tier>.mash.darkest
    file — the game's own weighted-random encounter tables, not hand-curated.
    `category` is the mash section it came from: 'hall' (hallway battle,
    typically 2-4 enemies), 'room' (room battle, typically 3-4), 'boss'
    (usually 1, sometimes 2), 'stall' (smaller battle used when the party
    stalls), 'named' (scripted/special encounters, e.g. quest bosses or
    conditional summons — `name` is set for these, else None). `enemy_ids`
    preserves the file's own ordering (used as position 1..N). `chance` is
    the real relative weight the game uses when randomly picking among
    entries in the same category for that zone/tier."""
    zone_id: str
    zone_name: str
    tier: int
    category: str
    chance: float
    enemy_ids: list[str]
    name: Optional[str] = None


@dataclass
class MapGenSettings:
    """One `map:` record from `scripts/map_generator.darkest` — the real
    dungeon layout-generation parameters, keyed by (size, quest_type,
    dungeon_type).

    This file was long assumed not to exist (both CLAUDE.md and ASSUMPTIONS.md
    stated that per-length room/battle counts were hardcoded in the game
    binary, which is why `EXPEDITION_LENGTH_BANDS` was invented as a simulator
    convention). It is a plain-text `.darkest` file shipped with the game, in
    the multi-line block layout (see `DarkestParser.parse_blocks`).

    `hallway_battles` and `room_battles` are inclusive (min, max) ranges. The
    number of combats an expedition of this shape contains is their sum —
    hallway battles and room battles are disjoint sets of fights.
    """
    size: str            # short | medium | long
    quest_type: str      # explore | kill_boss | cleanse | gather | activate | ...
    dungeon_type: str    # crypts | weald | warrens | cove
    base_room_number: int
    base_corridor_number: int
    hallway_battles: tuple[int, int]
    room_battles: tuple[int, int]

    @property
    def total_battles(self) -> tuple[int, int]:
        """Inclusive (min, max) number of combats in a raid of this shape."""
        return (self.hallway_battles[0] + self.room_battles[0],
                self.hallway_battles[1] + self.room_battles[1])


@dataclass
class SkillDesire:
    """One skill_selection_desires entry from base.monster_brains.json.

    All types are modeled except fill_ally_captor_empty_skill (The Hag's
    cauldron-capture mechanic — a distinct hero-abduction subsystem this
    combat-only simulator doesn't implement at all, declared out of scope
    like camping/Crimson Curse; see ASSUMPTIONS.md). preferred_skill is
    treated identically to random_skill — verified they always co-occur
    with the exact same shape (flat base_chance, no combat_skill_id) in
    every real brain that uses either, and no per-skill "preferred" flag
    exists anywhere in the game's monster skill files.
    """
    type: str
    base_chance: float
    combat_skill_id: str = ""
    # performing_turn_skill: 1-indexed action slot within a multi-action
    # monster's own turn (matches the action_idx loop in _execute_enemy_turn).
    performing_turn: Optional[int] = None
    # stun_skill: only bother stunning if there's a meaningful number of
    # non-stunned/marked heroes to justify it.
    non_stunned_heroes_min: Optional[int] = None
    marked_heroes_min: Optional[int] = None
    marked_heroes_max: Optional[int] = None
    # ally_alive_skill / ally_dead_skill: gate by whether an ally enemy of
    # this base class (definition_id with its _A/_B/_C/_D variant suffix
    # stripped) is alive/dead among the current enemy party.
    ally_base_class_id: str = ""
    # heal_skill: gate by whether any qualifying ally (including self) is
    # below this HP fraction, current enemy party size, and (rarely) whether
    # this performer is the fastest-acting enemy this round.
    hp_ratio_threshold: Optional[float] = None
    monsters_size_min: Optional[int] = None
    monsters_size_max: Optional[int] = None
    first_initiative_only: bool = False
    # effect_key_status_skill: gate by whether any hero currently carries
    # this key status (only "tagged" i.e. marked appears in real data).
    effect_key_status: str = ""
    # ---- gates that are NOT type-specific -------------------------------
    # Everything above this line was read only inside the one desire-type
    # branch it was first seen on. In the shipped data the same gates sit on
    # `specific_skill` (20 distinct kinds, 130+ instances) and
    # `performing_turn_skill` (44), where the engine read none of them --
    # 21.8% of the hall/room monster pool carries at least one. A gate is a
    # property of the DESIRE, not of its type, so `_skill_desire_gates_pass`
    # applies all of them uniformly.
    guarded_monsters_min: Optional[int] = None
    guarded_monsters_max: Optional[int] = None
    # `monsters_size_*` and `monsters_*` appear on the SAME desire (the
    # Siren's conch_summon), so they cannot be one quantity: the one named
    # for size is total rank occupancy, the other a head count. DECLARED
    # READING -- see ASSUMPTIONS.md for the falsifier.
    monsters_min: Optional[int] = None
    monsters_max: Optional[int] = None
    virtued_heroes_max: Optional[int] = None
    heroes_min: Optional[int] = None
    non_deaths_door_heroes_min: Optional[int] = None
    non_marked_heroes_min: Optional[int] = None
    performing_turn_min: Optional[int] = None
    # False = this skill belongs to the game's no-corpse ruleset. Corpses are
    # always enabled here, so such a desire can never apply.
    is_valid_when_corpse_enabled: bool = True


@dataclass
class TargetDesire:
    """One target_selection_desires entry.

    All types are modeled except fill_ally_captor_empty_target (see
    SkillDesire's docstring — The Hag's capture mechanic, out of scope) and
    next_initiative_target (real data has exactly one occurrence, in a brain
    literally named "test" that zero real enemies reference — confirmed
    unreachable, not worth modeling). rank_target is treated identically to
    random_target — verified all 4 real occurrences carry no rank-specific
    field at all (only base_chance/specific_combat_skill_id/exclusivity/
    enemy-or-friendly), so there's no data-derived "which rank" to act on.
    """
    type: str
    base_chance: float
    specific_combat_skill_id: str = ""
    is_greater_comparison: bool = False
    resistance_type_id: str = ""
    # False = targets the hero party (default, matches every previously-
    # modeled type); True = targets the performer's own side (other living
    # enemies) — e.g. Collector Shaman healing a wounded Collector.
    #
    # The two flags are NOT complements: 25 desires (every one of them the
    # brain's baseline `random_target`) set BOTH, meaning the desire applies
    # whichever side the skill points at. Filtering on `is_friendly_target_
    # desire` alone therefore dropped the baseline weight from enemy
    # targeting entirely for 9 monsters in the hall/room pool, which turned
    # a 3:1 preference for a marked hero into an absolute one.
    is_friendly_target_desire: bool = False
    is_enemy_target_desire: bool = True
    # When any applicable desire is exclusive, ONLY exclusive desires
    # contribute — the Fishman Shaman's heal picks the most wounded ally
    # outright instead of competing with a flat random weight.
    is_exclusive_desire: bool = False
    # Per-target exclusions. Absent means no restriction, so all default
    # True. `can_target_last_hero` refers to the last LIVING hero, not the
    # rearmost rank: the Hag's `hag_grab` carries it, and stuffing the last
    # hero into the cauldron would leave nobody able to act.
    can_target_stunned: bool = True
    can_target_deaths_door: bool = True
    can_target_last_hero: bool = True
    # The stress-state trio, unread until 2026-08-23. `not_overstressed` is
    # the odd one: it excludes a hero who is NOT overstressed, so the desire
    # it sits on is one that deliberately hunts a hero already near a Resolve
    # Check. Overstressed here means the affliction/virtue roll has happened
    # -- a hero at 100 stress who has already rolled is afflicted or virtuous,
    # and that is what the other two read.
    can_target_afflicted: bool = True
    can_target_virtued: bool = True
    can_target_not_overstressed: bool = True
    is_guarded: bool = True
    is_guarding: bool = True
    # ally_class_target: filter the (enemy or friendly) candidate pool to a
    # specific base class id, same stripped-suffix convention as SkillDesire.
    ally_base_class_id: str = ""


@dataclass
class BonusInitiativeDesire:
    """One bonus_initiative_desires entry — despite the name, verified
    against base.monster_brains.json to NOT be an initiative-roll modifier.
    It's a scripted bonus-action/skill-override system: 14/165 brains, all
    named/boss-tier, get an extra guaranteed action at a fixed round-timing
    hook, optionally overriding which skill they use. Every real occurrence
    fires at exactly one of two moments: round-start+pre_turn (before normal
    turn order) or round-finish+post_turn (after it) — is_round_in_progress
    always co-occurs with round_finish+post_turn in real data, so it's
    folded into the round-finish hook rather than tracked continuously.

    Modeled: guaranteed, ally_actor_class_count, hp_ratio_threshold,
    last_skill. Not modeled: death (Ancestor small/big forms' phase
    transition — spawning the next Ancestor form is a boss-specific
    mechanic this simulator has no other support for; firing just the
    trigger without that payload would produce incorrect behavior, not
    approximate-but-reasonable behavior) and ally_last_damaged (swine_prince
    only, 1 occurrence — a redundant secondary trigger for the same rage
    mechanic already covered by its regular ally_dead_skill desire). See
    ASSUMPTIONS.md.
    """
    type: str
    is_round_start: bool
    is_pre_turn: bool
    is_round_finish: bool
    is_post_turn: bool
    combat_skill_id_override: str = ""
    ally_base_class_id: str = ""
    ally_count_min: Optional[int] = None
    ally_count_max: Optional[int] = None
    monsters_min: Optional[int] = None
    monsters_max: Optional[int] = None
    monsters_size_limit: Optional[int] = None
    last_combat_skill_id: str = ""
    health_ratio_threshold: Optional[float] = None
    is_under_threshold: bool = True
    heroes_min: Optional[int] = None
    heroes_max: Optional[int] = None


@dataclass
class MonsterBrain:
    id: str
    skill_desires: list[SkillDesire] = field(default_factory=list)
    target_desires: list[TargetDesire] = field(default_factory=list)
    skill_cooldowns: dict[str, int] = field(default_factory=dict)
    bonus_initiative_desires: list[BonusInitiativeDesire] = field(default_factory=list)


@dataclass
class DarknessBand:
    """One row of rules.json's darkness.range_table — torch-level-dependent
    combat modifiers. Values here are raw percentage-point numbers as they
    appear in rules.json (e.g. 4.0 meaning +4%), not yet converted to the
    0-1 fraction scale the engine uses for crit/dodge/atk_bonus/dmg — that
    conversion happens where the band is applied (CombatEngine).

    The loot, scouting and surprise columns used to be dropped here, as
    "dungeon mechanics with no analog in this single-encounter simulator".
    They are parsed now — **read, never applied**: `CombatEngine` still touches
    only the combat columns, so engine behaviour is unchanged, but
    `bridge/torchlight.py` needs the loot side to answer PLAYBOOK 2.3's torch
    shutdown, and there is no reason for the bridge to re-read rules.json to
    get at numbers the loader already walks past.
    """
    lower: float
    upper: float
    stress_chance_increase: float = 0.0
    stress_damage_increase: float = 0.0
    monster_attack_increase: float = 0.0
    monster_damage_increase: float = 0.0
    monster_crit_increase: float = 0.0
    player_crit_increase: float = 0.0
    player_def_increase: float = 0.0
    # Read by bridge/torchlight.py; deliberately NOT applied by the engine.
    loot_increase_gold: float = 0.0
    loot_increase_treasure_draws: float = 0.0
    player_scouting_increase: float = 0.0
    monsters_surprised_increase: float = 0.0
    heroes_surprised_increase: float = 0.0


@dataclass
class CombatRules:
    cutoff_always_miss: float = 0.0
    cutoff_always_hit: float = 0.95
    excess_crit_mult: float = 0.0
    excess_crit_max: float = 0.20
    player_crit_multiplier: float = 1.5
    monster_crit_multiplier: float = 1.5
    passed_turn_stress_dmg: float = 5.0
    initiative_base: int = 8
    deaths_door_self_stress_chance: float = 1.0
    deaths_door_self_stress_dmg: float = 8.0
    deaths_door_self_stress_chance_repeat: float = 1.0
    deaths_door_self_stress_dmg_repeat: float = 4.0
    # What the SURVIVORS pay when a hero actually dies. Read by the game at
    # 0x1404ea351 / 0x1404ea3d1 and by nothing here until 2026-08-21.
    death_party_stress_chance: float = 0.5
    death_party_stress_damage: float = 12.0
    # A crit's bonus to any status it applies, as the game states it: a
    # modifier on the ROLL, not on the chance. Negative, so it lowers the roll.
    # `roll + mod < chance` and `roll < chance - mod` are the same predicate
    # here -- unlike the accuracy cutoffs (DECOMP F4), nothing clamps either
    # side, so the two forms cannot diverge.
    effect_crit_roll_mod: float = -0.20
    deaths_door_companion_stress_chance: float = 0.25
    deaths_door_companion_stress_dmg: float = 4.0
    deathblow_chance: float = 1.0
    affliction_base_onset_chance: float = 0.75
    affliction_critical_success_chance: float = 0.25
    affliction_min_chance: float = 0.05
    affliction_max_chance: float = 0.99
    # `effectiveDifficultyAfflictionOnsetModifiers`, indexed by the hero's
    # LEVEL SHORTFALL against the quest, not by the quest's difficulty. Seven
    # entries for shortfalls 0..6, and they decode exactly to the rule Fran
    # states: **-5% Virtue Chance per level of difference, except at a
    # difference of 6 where it is -33%** (a resolve-0 hero in the Darkest
    # Dungeon). Shipped as [0, .05, .10, .15, .20, .25, .33].
    #
    # Indexing it by DIFFICULTY instead is what made this look unimplementable
    # for a day -- it puts virtue at zero for every Champion party, which
    # cannot be right. By shortfall, a resolve-5 hero on a Champion quest is
    # short by nothing and pays nothing.
    difficulty_affliction_onset_modifiers: tuple = ()
    stress_affliction_resisted: float = 45.0
    # Stress a hero is left on after a Virtue (a "critical success" on the
    # Resolve Check). Shipped rules.json sets this and stress_affliction_
    # resisted to the same 45 — see engine/stress.py's resolve_check.
    stress_affliction_critically_resisted: float = 45.0
    killing_blow_relief_chance: float = 0.5
    killing_blow_stress_relief: float = 3.0
    stun_ends_turn: bool = True
    stall_enemy_size_threshold: int = 2
    stall_extra_round_size_threshold: int = 2
    stall_extra_round_size_amount: int = 1
    stall_round_accelerated_only_increment: int = 1
    stall_round_accelerated_only_consecutive_increment: int = 5
    stall_round_stress_effect_threshold: int = 3
    stall_round_summon_effect_threshold: int = 4
    stall_round_reset_threshold: int = 4
    # . How much of a DoT tick one hallway TILE is worth,
    # so a 6-tile corridor spends 3 ticks of a bleed. The default matches the
    # shipped value, because a rules.json that failed to load should not
    # silently switch the mechanic off.
    dot_hallway_step_ratio: float = 0.5
    # Real fields previously loaded by nobody -- see CombatEngine._check_stall
    # for the fix these enable. stall_party_size_threshold: alive-hero count
    # BELOW this exempts the round from counting as a stall (a near-wiped
    # party isn't "farming" the fight). stall_number_of_invalidating_skills_
    # threshold: the party needs at least this many real (SkillLevelData.
    # is_stall_invalidating) actions in a round to avoid it counting as a
    # stall round -- the actual real-game condition ("2+ damage moves per
    # round" per the wiki), previously not checked at all.
    stall_party_size_threshold: int = 3
    stall_number_of_invalidating_skills_threshold: int = 2
    # Real cap on Shieldbreaker's Aegis Block stack (Serpent's Sway's "SB
    # Aegis" effect) -- see CombatantState.damage_blocks / EffectDefinition.
    # health_damage_blocks.
    health_damage_blocks_max: int = 2
    consecutive_miss_reduction_acc_buff: float = 0.04
    # Camping (real shipped rules.json values; see RulesLoader.KEY_MAP).
    camp_start_camping_points: float = 12.0
    camp_rest_point_threshold: float = 6.0
    camp_rest_number_of_hero_threshold: float = 1.0
    camp_relieve_stress: float = 0.0
    camp_restore_torch: float = 100.0
    # `meals_table`, the camp FOOD phase -- shipped, not a convention.
    #
    #   type   rations_per  healing  stress
    #   none        0.0      -0.20     +15
    #   half        0.5       0.00       0
    #   full        1.0      +0.10       0
    #   feast       2.0      +0.25     -10
    #
    # **`rations_per` is PER LIVING HERO**, which is what makes the wiki's
    # "food costs reduce by one-quarter per dead party member" fall out rather
    # than needing a rule: a feast is 2 x 4 = 8 food for a full party and
    # 2 x 3 = 6 for a party of three. `healing` is a fraction of MAX HP and
    # `stress` is a flat delta, both applied to every living hero.
    #
    # Stored as `(type, rations_per, healing, stress)`, richest first.
    meals_table: tuple = ()
    ambush_camping_base_chance: float = 0.33
    # What one food restores, as a fraction of max HP. Real shipped value; it
    # was a literal in env/expedition_sequence_env.py with a comment pointing
    # at this key, which is the drift this project keeps finding.
    provision_hp_heal: float = 0.05
    # How many provisions ONE hero can eat before it stops helping them. Real
    # shipped value, and the game's own answer to "how much may the party eat
    # between fights" -- which this simulator used to answer with an invented
    # per-combat rate instead. See EAT_MAX_MEALS_PER_STOP.
    max_provisions_before_full: int = 4
    # What a quest does to quirks, rolled per surviving hero as it comes
    # home (`Inventory::System` ctor in the binary, which reads these and
    # nothing else for it): one NEGATIVE and one POSITIVE roll, each chance
    # interpolated on the hero's stress 0 -> 100 between the `*Stress0*` and
    # `*Stress100*` value of the quest's outcome. Then, from resolve
    # `disease_after_quest_min_resolve_level` on, a disease roll at
    # `disease_max_chance - disease_resist * disease_hero_disease_resist_weight`,
    # floored at `disease_after_quest_min_chance`. Defaults are the shipped
    # base rules.json. Read by `campaign/quirks.py`.
    neg_stress0_success: float = 0.30
    pos_stress0_success: float = 0.50
    neg_stress100_success: float = 0.50
    pos_stress100_success: float = 0.40
    neg_stress0_failure: float = 0.40
    pos_stress0_failure: float = 0.40
    neg_stress100_failure: float = 0.70
    pos_stress100_failure: float = 0.25
    disease_after_quest_min_resolve_level: float = 2
    disease_after_quest_min_chance: float = 0.05
    disease_max_chance: float = 0.32
    disease_hero_disease_resist_weight: float = 0.33
    quirks_max_positive: float = 5
    quirks_max_negative: float = 5
    quirks_max_diseases: float = 3
    # Read by bridge/retreat.py, not by the engine — retreating from a combat is
    # not in the action space (ASSUMPTIONS.md). `combat_retreat_stress` is the
    # 20 points PLAYBOOK 2.6 pays to pull out, and the chance pair says the
    # attempt can fail, so retreat advice must never read as a free action.
    combat_retreat_stress: float = 20.0
    combat_retreat_chance: float = 0.7
    combat_retreat_bonus_chance_per_attempt: float = 0.05
    # Surprise. Ten keys, and the game reads every one of them -- each appears
    # exactly once in `Darkest.exe`, at a parse site in one contiguous run
    # (0x1404ea8b2 .. 0x1404ead73, stride 0x80, ten keys in order). The two
    # `surprise_ambush_*` keys rules.json also ships are NOT in that run and
    # are absent from the image entirely: an ambush forces surprise rather
    # than rolling for it, which is why the wiki calls a Shambler ambush
    # unavoidable. See DECOMP.md F7.
    #
    # `-1.0` for a scouted room is a hard "never", not a zero: it has to
    # survive the darkness table ADDING up to +0.40 to it, and -1.0 + 0.40
    # still clamps to 0. A base of 0.0 would let the dark surprise a party in
    # a room it had already scouted.
    surprise_corridor_party_base_chance: float = 0.1
    surprise_corridor_monsters_base_chance: float = 0.1
    surprise_room_party_base_chance: float = 0.1
    surprise_room_monsters_base_chance: float = 0.1
    surprise_known_corridor_party_base_chance: float = -1.0
    surprise_known_corridor_monsters_base_chance: float = 0.25
    surprise_known_room_party_base_chance: float = -1.0
    surprise_known_room_monsters_base_chance: float = 0.25
    surprise_max_party_surprised_chance: float = 0.65
    surprise_max_monsters_surprised_chance: float = 0.65
    stun_recovery_buff_id: str = "STUNRECOVERYBUFF"
    monster_crit_stress_chance: float = 1.0
    monster_crit_stress_damage: float = 10.0
    monster_crit_companion_stress_chance: float = 0.5
    monster_crit_companion_stress_damage: float = 5.0
    player_crit_relief_chance: float = 1.0
    player_crit_stress_relief: float = 3.0
    # Party-wide relief when a hero crits — real rules.json player_crit also
    # has companion_relief_chance/companion_stress_relief, not just the
    # self-relief fields above; was never read before this.
    player_crit_companion_relief_chance: float = 0.25
    player_crit_companion_stress_relief: float = 3.0
    darkness_table: list["DarknessBand"] = field(default_factory=list)


@dataclass
class DataStore:
    heroes: dict[str, HeroDefinition] = field(default_factory=dict)
    enemies: dict[str, EnemyDefinition] = field(default_factory=dict)
    # The corpse monster classes (corpse_A..D, corpse_large_A..D), keyed by the
    # id a slain monster's `death_class:` names. Separate from `enemies` on
    # purpose: a corpse is a real, attackable stat block (7 HP at Apprentice,
    # `.can_be_missed False`) but must never be sampleable as an encounter.
    corpses: dict[str, EnemyDefinition] = field(default_factory=dict)
    skills: dict[str, SkillDefinition] = field(default_factory=dict)
    buffs: dict[str, BuffDefinition] = field(default_factory=dict)
    effects: dict[str, EffectDefinition] = field(default_factory=dict)
    trinkets: dict[str, TrinketDefinition] = field(default_factory=dict)
    trinket_sets: dict[str, list[str]] = field(default_factory=dict)
    """set_id -> the buff ids granted for wearing TWO members of that set.

    Read from the four `*.sets.trinkets.json` files (Crimson Court,
    Shieldbreaker, Duelist, Runaway). The buffs themselves were already in
    `store.buffs` -- 38 ids beginning `SET_` -- so this dict is only the
    binding that was missing."""
    quirks: dict[str, QuirkDefinition] = field(default_factory=dict)
    rules: CombatRules = field(default_factory=CombatRules)
    camping_skills: dict[str, CampingSkillDefinition] = field(default_factory=dict)
    # Crimson Court / Fires Edge / Color of Madness town districts, by name.
    # The town is out of scope but a district's payoff is not paid in town:
    # nine of them grant ordinary buff ids that land in combat. Tier-gated at
    # the consumer (`districts_at_tier`), never here. See data/district_loader.
    districts: dict[str, "DistrictDefinition"] = field(default_factory=dict)
    # Overstress personalities from shared/trait/trait_library.json (+ DLC) —
    # the real per-trait stat effects and act-out probability tables. Consumed
    # by CombatEngine._apply_affliction_effects / _maybe_act_out, which
    # previously used a hardcoded convention because this file was believed not
    # to exist. See data/trait_loader.py and CHANGELOG.md.
    traits: dict[str, TraitDefinition] = field(default_factory=dict)
    comps: list[CompositionDefinition] = field(default_factory=list)
    comp_loadouts: dict[str, CompLoadoutEntry] = field(default_factory=dict)
    # Comp names (matching store.comps[i].name) that include one or more
    # fan-made modded hero classes this project's data has no support for
    # (Monk, Black Mage, Spartan, ...) -- see CompsCsvLoader.
    # load_modded_hero_comp_names for the full rationale. Consumed by
    # env/expedition_env.py's _build_hero_pool and eval_expedition.py's
    # pick_representative_comps to exclude these from training/eval.
    modded_hero_comp_names: set[str] = field(default_factory=set)

    def comp_loadout_by_name(self, name: str):
        """A comp loadout by its current name OR its pre-rename `alias`.

        Aliases are NOT registered as extra keys in `comp_loadouts`: anything
        iterating that dict to build a sampling pool would then draw the same
        comp twice under two names.
        """
        entry = self.comp_loadouts.get(name)
        if entry is not None:
            return entry
        for candidate in self.comp_loadouts.values():
            if candidate.alias and candidate.alias == name:
                return candidate
        return None
    provisions: dict[str, ProvisionDefinition] = field(default_factory=dict)
    hero_class_starting_provisions: dict[str, str] = field(default_factory=dict)
    monster_brains: dict[str, MonsterBrain] = field(default_factory=dict)
    enemy_comps: list["EnemyCompEntry"] = field(default_factory=list)
    # Real dungeon layout-generation parameters from
    # scripts/map_generator.darkest — the source of truth for how many combats
    # a raid of a given size actually contains. Consumed by
    # env/expedition_sequence_env.py's EXPEDITION_LENGTH_BANDS.
    map_gen_settings: list["MapGenSettings"] = field(default_factory=list)

    # ── Lookup indexes over the skill catalogue ──
    #
    # `CombatEngine.get_skill_data` and `_is_ally_skill` used to answer by
    # scanning EVERY skill definition in the game (18 hero classes x ~7 skills
    # plus 246 monsters x ~2, so ~678 defs) comparing string ids. Between
    # `step()`, `_execute_action()`, `get_valid_actions()` and the observation's
    # `_skill_slot_vecs()`, that ran ~15.6 times per env step -- measured as
    # ~13% of engine step time, for a lookup that is O(1) by construction.
    #
    # Built lazily and memoised on the instance rather than as a dataclass
    # field: the loader populates the store field by field after construction,
    # so there is no point at which a field default could see a finished
    # catalogue. Once built it pickles along with the store, so a
    # SubprocVecEnv worker inherits it from data/store_cache.py's pickle
    # instead of rebuilding.
    #
    # These assume the skill catalogue is immutable after load, which it is --
    # the only post-load writer into the store is CombatEngine's synthesised
    # BUFF memo (`__inline__*`/`__buff_type__*`), which never touches skills.
    # `tests/engine/test_skill_index.py` pins each index against a brute-force scan
    # for every id in the game, so a divergence fails loudly rather than
    # silently returning a different skill's numbers.

    def skill_index(self) -> dict[tuple[str, int], "SkillLevelData"]:
        """`(skill_id, level) -> SkillLevelData`, hero definitions winning.

        Uses `setdefault` in the ORIGINAL scan order -- all heroes, then all
        enemies -- so the entry kept is the one the old linear scan would have
        returned first. Inserting with plain assignment instead would make the
        LAST duplicate win and quietly change which skill a colliding id
        resolves to.
        """
        idx = getattr(self, "_skill_index", None)
        if idx is None:
            idx = {}
            for hd in self.heroes.values():
                for sk in hd.skills:
                    for lvl, data in sk.levels.items():
                        idx.setdefault((sk.id, lvl), data)
            for ed in self.enemies.values():
                for sk in ed.skills:
                    for lvl, data in sk.levels.items():
                        idx.setdefault((sk.id, lvl), data)
            self._skill_index = idx
        return idx

    def ally_skill_index(self) -> dict[str, bool]:
        """`skill_id -> targets_allies`, over HERO skills only.

        Hero-only and first-level-only, matching `_is_ally_skill` exactly: it
        never consulted monster skills, and read `targets_allies` off whichever
        level came first. A missing id means False there, so callers use
        `.get(skill_id, False)`.
        """
        idx = getattr(self, "_ally_skill_index", None)
        if idx is None:
            idx = {}
            for hd in self.heroes.values():
                for sk in hd.skills:
                    if sk.levels:
                        idx.setdefault(
                            sk.id, next(iter(sk.levels.values())).targets_allies)
            self._ally_skill_index = idx
        return idx
