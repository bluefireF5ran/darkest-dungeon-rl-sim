from __future__ import annotations
from pathlib import Path
from .types import (
    HeroDefinition, HeroClass, HeroStats, SkillDefinition, SkillLevelData,
    SkillType, WeaponTier, ArmourTier, EnemyDefinition, HeroMode, MoveSkillData,
    DeathReaction,
)
from .darkest_parser import DarkestParser


def _hp_range(rec) -> "tuple[float, float] | None":
    """`.required_performer_hp_range <lo> <hi>` -> a (lo, hi) fraction pair.

    Multi-value numerics come back from the parser as one STRING ("0 0.40"),
    never a list, so this splits at the call site like every other multi-value
    key in this loader. Two skills in the game carry it, both the
    Flagellant's, and one of them writes it with no space after the previous
    value (`.self_target_valid false.required_performer_hp_range 0 0.40`) --
    which the parser's lookahead already tolerates.
    """
    raw = rec.get("required_performer_hp_range")
    if raw is None:
        return None
    parts = raw if isinstance(raw, list) else str(raw).split()
    nums = []
    for p in parts:
        try:
            nums.append(float(p))
        except ValueError:
            break
    return (nums[0], nums[1]) if len(nums) >= 2 else None


#: What makes a `combat_skill:` record an actual skill LEVEL rather than an
#: annotation about one. A record carrying none of these defines nothing.
#:
#: Twenty Workshop classes ship extra records that repeat an existing
#: `(id, level)` and carry only `.valid_modes` with tooltip ids. They come
#: after the real record, and last-write-wins let them REPLACE a fully
#: specified skill with an empty one -- the skill still existed, still had five
#: levels, and did nothing. That is the single largest cause of modded skills
#: reading as blank to the policy.
#:
#: Measured before relying on it: **0 of the 700 shipped vanilla
#: `combat_skill` records lack all of these**, so nothing in the base game or
#: its DLC can be affected.
SKILL_LEVEL_PAYLOAD_KEYS = frozenset(
    {"atk", "dmg", "effect", "heal", "target", "launch", "type"})


def merge_skill_records(records: list[dict]) -> list[dict]:
    """Fold repeated `(id, level)` records into one, field by field.

    A second record for a level that already exists is an ANNOTATION -- it adds
    `.valid_modes` or a tooltip id to something defined above it. Taking the
    last record whole therefore throws away everything the annotation does not
    happen to repeat, which is how four classes lost every effect they had:
    `EosOceanDragonMaid` collides on 30 `(id, level)` pairs, `iris2B` on 25,
    and for both, EVERY pair carries an effect in some record while the one
    kept carried none.

    Later values win for the fields they actually specify, so a mod that really
    does restate `.atk` still overrides. Order is preserved, and the first
    record for a level is the one that stays in place.

    **Unreachable for vanilla**: 0 of its 700 `(id, level)` pairs have a second
    record, so this cannot alter the base game or any DLC.
    """
    out: list[dict] = []
    index: dict[tuple, int] = {}
    for rec in records:
        if rec.get("_type") != "combat_skill":
            out.append(rec)
            continue
        key = (rec.get("id", ""), rec.get("level", 0))
        seen = index.get(key)
        if seen is None:
            index[key] = len(out)
            out.append(dict(rec))
            continue
        merged = out[seen]
        for field, value in rec.items():
            # An empty value is the annotation saying nothing about that field,
            # not saying it is empty. `.effect ""` parses to `['']`, which is
            # the same trap `_named_effects` exists for.
            if value in ("", [], None) or value == [""]:
                continue
            merged[field] = value
    return out


class HeroLoader:
    """Load hero definitions from the game install .info.darkest files."""

    HERO_DIR_MAP = {
        "abomination": "abomination",
        "antiquarian": "antiquarian",
        "arbalest": "arbalest",
        "bounty_hunter": "bounty_hunter",
        "crusader": "crusader",
        # The Fire's Edge (dlc/4964110_fires_edge). Both live one level
        # deeper than the older DLC heroes -- features/<name>/heroes/<name>/ --
        # which the recursive glob below already reaches.
        "duelist": "duelist",
        "flagellant": "flagellant",
        "grave_robber": "grave_robber",
        "hellion": "hellion",
        "highwayman": "highwayman",
        "houndmaster": "houndmaster",
        "jester": "jester",
        "leper": "leper",
        "man_at_arms": "man_at_arms",
        "musketeer": "musketeer",
        "occultist": "occultist",
        "plague_doctor": "plague_doctor",
        "runaway": "runaway",
        "shieldbreaker": "shieldbreaker",
        "vestal": "vestal",
    }

    # Descriptive only -- nothing in engine/, env/ or bridge/ branches on it.
    # The Fire's Edge is a workshop package rather than an official DLC, but it
    # installs under dlc/ and loads exactly like one, and it is labelled DLC
    # rather than MODDED deliberately: MODDED has no reader anywhere, so the
    # first rule written against it would silently drop these two from training
    # -- which is the opposite of why they were added.
    HERO_CLASS_MAP = {
        "duelist": HeroClass.DLC,
        "flagellant": HeroClass.DLC,
        "musketeer": HeroClass.DLC,
        "runaway": HeroClass.DLC,
        "shieldbreaker": HeroClass.DLC,
    }

    SKILL_INTERNAL_NAMES_WITH_LEVELS = {
        "abomination": ["transform", "manacles", "vomit", "absolution", "rake", "rage", "slam"],
    }

    @staticmethod
    def load_all(game_dir: Path) -> dict[str, HeroDefinition]:
        heroes: dict[str, HeroDefinition] = {}
        heroes_path = game_dir / "heroes"
        dlc_path = game_dir / "dlc"

        for hero_id, subdir in HeroLoader.HERO_DIR_MAP.items():
            info_path = heroes_path / subdir / f"{subdir}.info.darkest"
            if not info_path.exists():
                # DLC heroes (shieldbreaker, musketeer, flagellant) live under
                # dlc/<pkg>/heroes/<subdir>/ or, for Crimson Court's
                # Flagellant, nested one level deeper under
                # dlc/<pkg>/features/<name>/heroes/<subdir>/ — search
                # recursively rather than assuming a fixed depth.
                info_path = None
                if dlc_path.exists():
                    matches = list(dlc_path.glob(f"**/heroes/{subdir}/{subdir}.info.darkest"))
                    if matches:
                        info_path = matches[0]
                if info_path is None:
                    continue
            records = DarkestParser.parse_file(info_path)
            hero = HeroLoader._build_hero(hero_id, subdir, records)
            if hero:
                heroes[hero_id] = hero

        heroes.update(HeroLoader._load_mod_heroes(heroes))
        return heroes

    # Extra roots to search for hero classes, as an os.pathsep-separated list
    # in `DDSIM_MOD_DIRS`. OFF unless set, so nothing about the default store
    # changes and every archived measurement stays reproducible.
    MOD_DIRS_ENV = "DDSIM_MOD_DIRS"

    @staticmethod
    def _load_mod_heroes(existing: dict) -> dict[str, HeroDefinition]:
        """Third-party classes, DISCOVERED rather than enumerated.

        `HERO_DIR_MAP` is an explicit list of the 20 shipped ids, so a modded
        class is skipped by a loader that never heard of it — which is the real
        reason "modded heroes don't load", not the missing-file reason
        `CLAUDE.md` gave. Fran has five class mods subscribed and their hero
        files parse fine with the unmodified parser; they simply live in
        `steamapps/workshop/content/262060/`, outside `game_dir`.

        Why an env var and not a constructor argument: `DataStore` is built
        through `load_store_cached` from a dozen call sites (tests, tools,
        bridge, both envs), and threading a new parameter through all of them
        to enable an experiment would touch far more than the experiment is
        worth. `DDSIM_NO_STORE_CACHE` set the precedent.

        **The store cache keys on `data/*.py` and the game subtrees, NOT on
        this variable.** So flipping it does not by itself invalidate a cached
        store. Set `DDSIM_NO_STORE_CACHE=1` alongside it, or the first run
        after enabling mods may serve a store built without them.

        A discovered id must not collide with a shipped one — a mod that
        replaces the Crusader would otherwise silently become the Crusader
        everywhere, including in comps that mean the real one.
        """
        import os

        raw = os.environ.get(HeroLoader.MOD_DIRS_ENV, "").strip()
        if not raw:
            return {}
        out: dict[str, HeroDefinition] = {}
        for entry in raw.split(os.pathsep):
            root = Path(entry.strip())
            if not entry.strip() or not root.is_dir():
                continue
            for info in sorted(root.glob("**/heroes/*/*.info.darkest")):
                subdir = info.parent.name
                if info.name != f"{subdir}.info.darkest":
                    continue
                if subdir in existing or subdir in out:
                    continue          # never shadow a shipped class
                hero = HeroLoader._build_hero(subdir, subdir,
                                              DarkestParser.parse_file(info))
                if hero:
                    out[subdir] = hero
        return out

    @staticmethod
    def _build_hero(hero_id: str, subdir: str, records: list[dict]) -> HeroDefinition | None:
        if not records:
            return None

        # A repeated `(id, level)` is an ANNOTATION on a level defined above
        # it, not a redefinition of it. Folding them field by field here is
        # what stops a tooltip record erasing a real skill; see
        # `merge_skill_records`. A no-op for vanilla, which has no collisions.
        records = merge_skill_records(records)

        resistances = {}
        weapon_tiers: list[WeaponTier] = []
        armour_tiers: list[ArmourTier] = []
        skills: dict[str, dict[int, SkillLevelData]] = {}
        skill_meta: dict[str, str] = {}
        tags: list[str] = []
        modes: list[HeroMode] = []
        deaths_door_buffs: list[str] = []
        recovery_buffs: list[str] = []
        recovery_heart_attack_buffs: list[str] = []
        death_door_enter_effects: list[str] = []
        death_door_enter_round_cooldown = 0
        death_reactions: list[DeathReaction] = []
        overstress_override_ids: list[str] = []
        overstress_override_chances: list[float] = []
        can_select_combat_skills = True
        riposte_acc: float | None = None
        riposte_crit_mod: float | None = None
        controlled_rank: int | None = None
        crit_effect_id: str | None = None
        move_skill: MoveSkillData | None = None

        for rec in records:
            rtype = rec.get("_type", "")
            if rtype == "resistances":
                resistances = {
                    "stun": rec.get("stun", 0.0),
                    "poison": rec.get("poison", 0.0),
                    "bleed": rec.get("bleed", 0.0),
                    "disease": rec.get("disease", 0.0),
                    "move": rec.get("move", 0.0),
                    "debuff": rec.get("debuff", 0.0),
                    "death_blow": rec.get("death_blow", 0.0),
                    "trap": rec.get("trap", 0.0),
                }
            elif rtype == "weapon":
                # Real key is a single "dmg" field with a two-part value
                # ("6 12"), not separate "dmg_min"/"dmg_max" keys — verified
                # directly against the parsed record
                # ({'dmg': '6 12', ...}). The old code checked for keys that
                # never exist in any real weapon record, so dmg_min/dmg_max
                # silently defaulted to 1/1 for every hero at every tier —
                # every hero's weapon-derived base damage has been ~1 per
                # hit this entire project, undetected because
                # tests/engine/test_resolution.py's fixtures construct
                # CombatantState directly (bypassing this loader) instead of
                # going through real HeroDefinition data. Same split
                # convention as _build_enemy's parse_monster_dmg.
                dmg_min, dmg_max = 1, 1
                dmg_raw = rec.get("dmg")
                if dmg_raw is not None:
                    dmg_parts = str(dmg_raw).split()
                    if len(dmg_parts) >= 2:
                        dmg_min, dmg_max = int(dmg_parts[0]), int(dmg_parts[1])
                    elif len(dmg_parts) == 1 and dmg_parts[0]:
                        dmg_min = dmg_max = int(dmg_parts[0])
                tier = WeaponTier(
                    level=rec.get("level", 0),
                    name=rec.get("name", ""),
                    atk_bonus=float(rec.get("atk", 0.0)),
                    dmg_min=dmg_min,
                    dmg_max=dmg_max,
                    crit_bonus=float(rec.get("crit", 0.0)),
                    spd_bonus=int(rec.get("spd", 0)),
                )
                weapon_tiers.append(tier)
            elif rtype == "armour":
                tier = ArmourTier(
                    level=rec.get("level", 0),
                    name=rec.get("name", ""),
                    dodge=float(rec.get("def", 0.0)),
                    prot=float(rec.get("prot", 0.0)),
                    hp=int(rec.get("hp", 1)),
                    spd_bonus=int(rec.get("spd", 0)),
                )
                armour_tiers.append(tier)
            elif rtype == "combat_skill":
                # An annotation, not a level -- see SKILL_LEVEL_PAYLOAD_KEYS.
                # Records are merged before this loop, so anything still
                # carrying no payload defines nothing at all.
                if not (set(rec) & SKILL_LEVEL_PAYLOAD_KEYS):
                    continue
                skill_id = rec.get("id", "")
                level = rec.get("level", 0)
                launch_raw = str(rec.get("launch", ""))
                target_raw = str(rec.get("target", ""))
                is_aoe = isinstance(target_raw, str) and target_raw.replace("@", "").startswith("~")
                # '@' is the real signal for "targets allies/own side", not
                # SkillType — verified against divine_grace (no .type field
                # at all, defaults to SkillType.MELEE) and Man-at-Arms'
                # defender/Guard (.type "melee" but .target @1234). A
                # SkillType-based check would misclassify both as
                # enemy-targeting.
                targets_allies = "@" in target_raw
                target_positions = DarkestParser.decode_position_notation(target_raw)

                # `.move <backward> <forward>` on a combat_skill describes the
                # PERFORMER's own displacement -- both numbers, not one each
                # for performer and target. Confirmed against four skills whose
                # real behaviour is unambiguous and opposed:
                #
                #   shadow_fade      .move 2 0   Grave Robber retreats 2 back
                #   holy_lance       .move 0 1   Crusader charges 1 forward
                #   lunge            .move 0 2   Grave Robber leaps 2 forward
                #   duelist_advance  .move 0 1   Highwayman advances 1 forward
                #
                # This used to read parts[1] as a PUSH ON THE TARGET, and
                # `SkillLevelData.push` is never read by the engine -- so every
                # forward self-move in the game was parsed into a field nothing
                # consumes and silently discarded. That is 55 of the 90 hero
                # combat skills carrying a `.move` (40x `0 1`, 10x `0 2`,
                # 5x `0 3`); the 35 backward ones happened to work because
                # parts[0] was kept.
                #
                # Net displacement, positive = toward the back (higher rank
                # index), which is the sign `_apply_movement` already adds.
                # A combat_skill never has both numbers non-zero -- only
                # `combat_move_skill:` does, where they are a RANGE, and that
                # is parsed elsewhere.
                move_raw = rec.get("move", "")
                push = 0
                pull = 0
                move_self = 0
                if move_raw:
                    parts = str(move_raw).split()
                    def _n(x):
                        return int(x) if x.lstrip("-").isdigit() else 0
                    if len(parts) == 2:
                        move_self = _n(parts[0]) - _n(parts[1])
                    else:
                        move_self = _n(parts[0])

                effects_raw = rec.get("effect", "")
                if isinstance(effects_raw, str):
                    effects_list = [e.strip() for e in effects_raw.split('"') if e.strip() and e.strip() != ' ']
                    if not effects_list:
                        effects_list = [effects_raw]
                elif isinstance(effects_raw, list):
                    effects_list = effects_raw
                else:
                    effects_list = []

                # Abomination's transform: .human_effects/.beast_effects are
                # space-separated bare-word identifiers (not quoted), gated
                # by the performer's current mode at the time it's used.
                def parse_bareword_list(raw) -> list[str]:
                    if isinstance(raw, list):
                        return [str(x) for x in raw]
                    if raw:
                        return str(raw).split()
                    return []
                human_effects_list = parse_bareword_list(rec.get("human_effects"))
                beast_effects_list = parse_bareword_list(rec.get("beast_effects"))

                # The general form. Gated on `.valid_modes` rather than on any
                # key ending in `_effects`, which would swallow `.self_effects`
                # and invent a mode called "self".
                valid_modes_list = parse_bareword_list(rec.get("valid_modes")) or ["human"]
                mode_effects_map = {
                    mode: parse_bareword_list(rec.get(f"{mode}_effects"))
                    for mode in valid_modes_list
                    if rec.get(f"{mode}_effects")
                }

                # .heal N or .heal N M — direct HP heal range on the skill line
                # itself (e.g. battle_heal, divine_grace). Distinct from healing
                # applied via named effects (e.g. inspiring_cry's stress heal).
                heal_min = None
                heal_max = None
                heal_raw = rec.get("heal")
                if heal_raw is not None:
                    heal_parts = str(heal_raw).replace(",", ".").split()
                    if len(heal_parts) >= 2:
                        heal_min, heal_max = int(float(heal_parts[0])), int(float(heal_parts[1]))
                    elif len(heal_parts) == 1 and heal_parts[0]:
                        heal_min = heal_max = int(float(heal_parts[0]))

                sld = SkillLevelData(
                    level=level,
                    atk=float(rec.get("atk", 0.0)),
                    dmg_mod=float(rec.get("dmg", 0.0)),
                    crit=float(rec.get("crit", 0.0)),
                    launch=DarkestParser.decode_position_notation(launch_raw),
                    target=target_positions,
                    is_aoe=is_aoe,
                    targets_allies=targets_allies,
                    human_effects=human_effects_list,
                    beast_effects=beast_effects_list,
                    mode_effects=mode_effects_map,
                    is_crit_valid=rec.get("is_crit_valid", True),
                    is_continue_turn=rec.get("is_continue_turn", False),
                    effects=effects_list,
                    self_effects=[],
                    heal_min=heal_min,
                    heal_max=heal_max,
                    move_self=move_self,
                    move_target=0,
                    push=push,
                    pull=pull,
                    # .valid_modes human beast is an unquoted space-separated
                    # bare-word list (same shape as .human_effects/
                    # .beast_effects) — was being kept as one unsplit string
                    # ("human beast") whenever more than one mode was listed,
                    # so `"human" in skill.valid_modes` was False even for
                    # transform itself, which needs both. Only a single-mode
                    # skill (e.g. "human" alone) ever parsed correctly before.
                    valid_modes=valid_modes_list,
                    per_battle_limit=rec.get("per_battle_limit"),
                    per_turn_limit=rec.get("per_turn_limit"),
                    is_stall_invalidating=rec.get("is_stall_invalidating", True),
                    refresh_after_each_wave=bool(
                        rec.get("refresh_after_each_wave", False)),
                    self_target_valid=bool(rec.get("self_target_valid", True)),
                    required_performer_hp_range=_hp_range(rec),
                    ignore_guard=bool(rec.get("ignore_guard", False)),
                    ignore_protection=bool(rec.get("ignore_protection", False)),
                    ignore_stealth=bool(rec.get("ignore_stealth", False)),
                    # Backdraft's whole payload -- see SkillLevelData. Parsed at
                    # BOTH build sites, which this file already duplicates for
                    # `ignore_guard`; the modded-hero path is the second one and
                    # a Fire's Edge class arrives through it.
                    requires_burning=bool(rec.get("requires_burning", False)),
                    dmg_per_burn_stack=float(rec.get("dmg_per_burn_stack", 0.0) or 0.0),
                    copy_burn_behind=bool(rec.get("copy_burn_behind", False)),
                )

                if skill_id not in skills:
                    skills[skill_id] = {}
                skills[skill_id][level] = sld

            elif rtype == "tag":
                tag_id = rec.get("id", "")
                if tag_id:
                    tags.append(tag_id)
            elif rtype == "mode":
                mode = HeroMode(
                    id=rec.get("id", ""),
                    is_raid_default=rec.get("is_raid_default", False),
                    stress_damage_per_turn=int(rec.get("stress_damage_per_turn", 0)),
                    affliction_combat_skill_id=rec.get("affliction_combat_skill_id"),
                    battle_start_effect_ids=(
                        [rec["battle_start_effect_ids"]]
                        if isinstance(rec.get("battle_start_effect_ids"), str)
                        else list(rec.get("battle_start_effect_ids", []) or [])),
                )
                modes.append(mode)
            elif rtype == "deaths_door":
                db = rec.get("buffs", "")
                if isinstance(db, str):
                    deaths_door_buffs = db.split()
                elif isinstance(db, list):
                    deaths_door_buffs = db
                # 2 further real tiers beyond the active .buffs above: a
                # weaker "mortality" debuff that applies for the rest of the
                # expedition after being healed off Death's Door once, and a
                # separate (not strictly worse — its stress term is
                # negative where mortality's is positive) tier after a
                # stress-200 heart attack. Neither was read before this.
                rb = rec.get("recovery_buffs", "")
                if isinstance(rb, str):
                    recovery_buffs = rb.split()
                elif isinstance(rb, list):
                    recovery_buffs = rb
                rhab = rec.get("recovery_heart_attack_buffs", "")
                if isinstance(rhab, str):
                    recovery_heart_attack_buffs = rhab.split()
                elif isinstance(rhab, list):
                    recovery_heart_attack_buffs = rhab
                # Real .enter_effects/.enter_effect_round_cooldown fields —
                # a hero-specific trigger fired once on ENTERING Death's
                # Door (Flagellant-only in practice: "flagellant_death_
                # door_enter", his real party-heal-10%-max-HP reaction).
                # Never read before this.
                dde = rec.get("enter_effects", "")
                if isinstance(dde, str) and dde:
                    death_door_enter_effects = dde.split()
                elif isinstance(dde, list):
                    death_door_enter_effects = dde
                death_door_enter_round_cooldown = int(rec.get("enter_effect_round_cooldown", 0))
            elif rtype == "death_reaction":
                # Real `death_reaction: .target_allies bool .target_enemies
                # bool .effects "X"` records, fired once when this hero
                # actually dies — Flagellant-only in the real game ("Flag
                # Last Gift"/"Flag Last Insult", his "On Death" reactions).
                # Never read before this — a separate record type from
                # combat_skill/deaths_door, not just an unread field on one
                # of them.
                raw_effects = rec.get("effects", "")
                if isinstance(raw_effects, list):
                    reaction_effects = raw_effects
                elif raw_effects:
                    reaction_effects = [str(raw_effects)]
                else:
                    reaction_effects = []
                death_reactions.append(DeathReaction(
                    target_allies=bool(rec.get("target_allies", False)),
                    target_enemies=bool(rec.get("target_enemies", False)),
                    effects=reaction_effects,
                ))
            elif rtype == "overstressed_modifier":
                # `.override_trait_type_ids berserk .override_trait_type_
                # chances 1.0` -- this hero's Resolve Check does not draw from
                # the rollable pool. Both values are BAREWORDS, not quoted, so
                # a multi-id record arrives as one space-separated string
                # rather than a list (the same shape `.hallway_battle 2 4`
                # has); split at the call site, which is here.
                raw_ids = rec.get("override_trait_type_ids", "")
                if isinstance(raw_ids, list):
                    overstress_override_ids = [str(x) for x in raw_ids]
                elif raw_ids:
                    overstress_override_ids = str(raw_ids).split()
                raw_w = rec.get("override_trait_type_chances", "")
                if isinstance(raw_w, list):
                    overstress_override_chances = [float(x) for x in raw_w]
                elif raw_w not in ("", None):
                    overstress_override_chances = [float(x)
                                                   for x in str(raw_w).split()]
                # A weight list that does not line up with the ids is data we
                # cannot read; fall back to uniform rather than mis-weight it.
                if len(overstress_override_chances) != len(overstress_override_ids):
                    overstress_override_chances = [1.0] * len(overstress_override_ids)
            elif rtype == "riposte_skill":
                # Real `riposte_skill: .id "riposte1" .atk X% .crit Y%`
                # record — the counter-attack's OWN fixed base ACC/crit,
                # distinct from (and NOT scaling with) the setup skill's own
                # level (Retribution/Duelist's Advance/Collect Bounty).
                # Never read before this: _maybe_trigger_riposte applied
                # riposte damage completely unconditionally, no accuracy
                # roll at all -- every triggered riposte was a guaranteed
                # hit. Only present for the 3 real riposte-capable classes.
                riposte_acc = float(rec.get("atk", 0.0))
                riposte_crit_mod = float(rec.get("crit", 0.0))
            elif rtype == "skill_selection":
                # A VALUELESS-FLAG TRAP in reverse: this one has a value, and
                # the parser gives it as the bareword `False`/`True` (or the
                # strings, depending on spelling), so compare loosely rather
                # than trusting `bool()` -- `bool("false")` is True.
                raw_sel = rec.get("can_select_combat_skills")
                if raw_sel is not None:
                    can_select_combat_skills = str(raw_sel).strip().lower() not in (
                        "false", "0", "no")
            elif rtype == "controlled":
                controlled_rank = rec.get("target_rank")
            elif rtype == "crit":
                # Per-class passive that fires on ANY crit with ANY skill
                # (crit: .effects "on_crit_X") — separate from a skill's own
                # .effect list. Never read before this; every hero has
                # exactly one.
                ce = rec.get("effects")
                if ce:
                    crit_effect_id = str(ce)
            elif rtype == "combat_move_skill":
                # Real per-hero voluntary reposition ability
                # (combat_move_skill: .move <backward> <forward> .launch N)
                # — never read before this; distinct from any skill's own
                # push/pull/move_self field.
                move_raw = str(rec.get("move", ""))
                move_parts = move_raw.split()
                backward = int(move_parts[0]) if len(move_parts) >= 1 and move_parts[0].lstrip("-").isdigit() else 0
                forward = int(move_parts[1]) if len(move_parts) >= 2 and move_parts[1].lstrip("-").isdigit() else 0
                move_skill = MoveSkillData(
                    backward=backward,
                    forward=forward,
                    launch=DarkestParser.decode_position_notation(str(rec.get("launch", ""))),
                )

        if not weapon_tiers or not armour_tiers:
            return None

        max_tier_weapon = weapon_tiers[-1]
        max_tier_armour = armour_tiers[-1]
        base_stats = HeroStats(
            hp=max_tier_armour.hp,
            dodge=max_tier_armour.dodge,
            prot=max_tier_armour.prot,
            speed=max_tier_weapon.spd_bonus,
            stun_resist=resistances.get("stun", 0.0),
            blight_resist=resistances.get("poison", 0.0),
            bleed_resist=resistances.get("bleed", 0.0),
            debuff_resist=resistances.get("debuff", 0.0),
            move_resist=resistances.get("move", 0.0),
            disease_resist=resistances.get("disease", 0.0),
            death_blow_resist=resistances.get("death_blow", 0.0),
            trap_resist=resistances.get("trap", 0.0),
        )

        hero_class = HeroLoader.HERO_CLASS_MAP.get(hero_id, HeroClass.VANILLA)
        skill_type_map: dict[str, SkillType] = {}
        for rec in records:
            if rec.get("_type") == "combat_skill":
                # A `.type` the enum does not know falls back to MELEE rather
                # than raising. Vanilla ships none, but a Workshop class does
                # (`iriscrewmate` writes `.type ""`, as do several modded
                # monsters), and a discovered mod must never be able to break
                # the load -- the same contract that keeps it from shadowing a
                # shipped id. `.type` only picks melee-vs-ranged here; the
                # ranks a skill is usable from come from `.launch`.
                raw_type = rec.get("type", "melee")
                try:
                    st = SkillType(raw_type)
                except ValueError:
                    st = SkillType.MELEE
                skill_type_map[rec.get("id", "")] = st

        hero_skills: list[SkillDefinition] = []
        for sk_id, levels in skills.items():
            first_level = levels[min(levels.keys())]
            st = skill_type_map.get(sk_id, SkillType.MELEE)
            hero_skills.append(SkillDefinition(
                id=sk_id,
                name=sk_id,
                skill_type=st,
                levels=levels,
            ))

        return HeroDefinition(
            id=hero_id,
            name=subdir.replace("_", " ").title(),
            hero_class=hero_class,
            tags=tags,
            base_stats=base_stats,
            weapon_tiers=weapon_tiers,
            armour_tiers=armour_tiers,
            skills=hero_skills,
            deaths_door_buffs=deaths_door_buffs,
            modes=modes if modes else [HeroMode(id="human", is_raid_default=True)],
            controlled_rank=controlled_rank,
            crit_effect_id=crit_effect_id,
            move_skill=move_skill,
            can_select_combat_skills=can_select_combat_skills,
            recovery_buffs=recovery_buffs,
            recovery_heart_attack_buffs=recovery_heart_attack_buffs,
            death_door_enter_effects=death_door_enter_effects,
            death_door_enter_round_cooldown=death_door_enter_round_cooldown,
            death_reactions=death_reactions,
            overstress_override_ids=overstress_override_ids,
            overstress_override_chances=overstress_override_chances,
            riposte_acc=riposte_acc,
            riposte_crit_mod=riposte_crit_mod,
        )

    # The corpse monster classes, kept in their own dict rather than merged
    # into store.enemies. They must never be reachable by encounter sampling
    # (nothing in mash data references them, but store.enemies is also the pool
    # eval/diagnostics iterate over), yet their stat block is real, needed data:
    # a corpse is an attackable 7-HP object, and CombatEngine reads its HP and
    # size from here when a monster dies. `corpse_id` on the slain monster
    # (`death_class: .monster_class_id corpse_A`) names the exact entry.
    _CORPSE_DIRS = ("corpse", "corpse_large")

    @staticmethod
    def _is_corpse_dir(name: str) -> bool:
        """A corpse directory, by PREFIX rather than by an exact name.

        **A corpse must never be samplable as an encounter**, which is why
        `store.corpses` is a separate dict from `store.enemies` in the first
        place. The exact-name list was complete while only the base game was
        read; the moment the DLC trees were added (2026-09-02) Color of
        Madness's `corpse_crystal` walked straight into the enemy pool, where
        an encounter table could have drawn four of them as a fight.
        """
        return name.startswith("corpse")

    @staticmethod
    def _load_corpses(game_dir: Path) -> list[EnemyDefinition]:
        corpses: list[EnemyDefinition] = []
        for monsters_path in HeroLoader._monster_roots(game_dir):
            corpses += HeroLoader._load_corpses_from(monsters_path)
        return corpses

    @staticmethod
    def _load_corpses_from(monsters_path: Path) -> list[EnemyDefinition]:
        corpses: list[EnemyDefinition] = []
        if not monsters_path.exists():
            return corpses
        for monster_dir in sorted(monsters_path.iterdir()):
            if not monster_dir.is_dir() or not HeroLoader._is_corpse_dir(monster_dir.name):
                continue
            for variant_dir in monster_dir.iterdir():
                if not variant_dir.is_dir():
                    continue
                info_files = list(variant_dir.glob("*.info.darkest"))
                if not info_files:
                    continue
                records = DarkestParser.parse_file(info_files[0])
                corpse = HeroLoader._build_enemy(variant_dir.name, records,
                                                 base_class=monster_dir.name)
                if corpse:
                    corpses.append(corpse)
        return corpses

    @staticmethod
    def _monster_roots(game_dir: Path) -> list[Path]:
        """Every `monsters/` tree, base first and then the DLC ones.

        **The DLC trees were never read, and a live run found it.** The loader
        walked `game_dir / "monsters"` alone, so `snake_cobra_A` and
        `snake_rattler_A` -- the Shieldbreaker's three snakes -- did not exist
        as far as this project was concerned. `bat/play.log` reports
        `cannot seed: unknown monster id(s)` **thirty times** across one
        session: thirty combat turns where the bridge could offer no advice at
        all, in ordinary Cove and Crypts runs.

        They are not special-encounter content that can be waved off. The
        Shieldbreaker ships mash files for **all four normal regions**, and
        two kinds of row:

        * `flashback.<zone>.<tier>.mash.darkest` -- `named:` rows at
          `.chance 1`, gated on `.flashback_id shieldbreaker_N`, which is her
          scripted first several raids;
        * `flashback.<zone>.conditional.<tier>.mash.darkest` -- `hall:` rows at
          `.chance .01` gated on `.completed_flashback_id shieldbreaker_6`, so
          once her flashbacks are done every ordinary hallway fight in the game
          carries a 1% chance of a snake ambush.

        Base first and merged first-wins, the same rule `DataLoader`'s DLC
        merge follows everywhere else: a DLC must be able to ADD a monster and
        must never silently redefine one.
        """
        roots = []
        base = game_dir / "monsters"
        if base.is_dir():
            roots.append(base)
        dlc = game_dir / "dlc"
        if dlc.is_dir():
            roots += sorted(p for p in dlc.glob("*/monsters") if p.is_dir())
            roots += sorted(p for p in dlc.glob("*/*/monsters") if p.is_dir())
        return roots

    @staticmethod
    def _load_enemies(game_dir: Path) -> list[EnemyDefinition]:
        enemies: list[EnemyDefinition] = []
        seen: set = set()

        for monsters_path in HeroLoader._monster_roots(game_dir):
            enemies += HeroLoader._load_enemies_from(monsters_path, seen)
        return enemies

    @staticmethod
    def _load_enemies_from(monsters_path: Path, seen: set) -> list[EnemyDefinition]:
        enemies: list[EnemyDefinition] = []
        if not monsters_path.exists():
            return enemies

        for monster_dir in monsters_path.iterdir():
            if not monster_dir.is_dir():
                continue
            if HeroLoader._is_corpse_dir(monster_dir.name):
                continue  # loaded separately by _load_corpses

            for variant_dir in monster_dir.iterdir():
                if not variant_dir.is_dir():
                    continue
                info_files = list(variant_dir.glob("*.info.darkest"))
                if not info_files:
                    continue
                if variant_dir.name in seen:
                    continue          # base wins on an id collision
                records = DarkestParser.parse_file(info_files[0])
                enemy = HeroLoader._build_enemy(variant_dir.name, records,
                                                base_class=monster_dir.name)
                if enemy:
                    seen.add(variant_dir.name)
                    enemies.append(enemy)
        return enemies

    @staticmethod
    def _build_enemy(enemy_id: str, records: list[dict],
                     base_class: str = "") -> EnemyDefinition | None:
        if not records:
            return None

        stats_data = {}
        enemy_type_list: list[str] = []
        skills: dict[str, dict[int, SkillLevelData]] = {}
        size = 1
        actions = 1
        monster_brain_id = "default"
        loot_code = "A"
        corpse_id = None
        variant = "Apprentice"
        name = enemy_id
        prefers_front = False
        accelerate_stall_penalty = False
        disable_stall_penalty = False
        life_link_base_class = None
        can_be_damaged_directly = True
        can_surprise = True
        can_be_surprised = True
        always_surprise = False
        always_be_surprised = False

        def parse_monster_dmg(dmg_val) -> tuple[float, int, int]:
            """Parse dmg field: either a float modifier ('-0.25') or a range ('2 4')."""
            dmg_str = str(dmg_val).strip()
            parts = dmg_str.split()
            if len(parts) == 2:
                try:
                    return 0.0, int(parts[0]), int(parts[1])
                except ValueError:
                    pass
            try:
                return float(dmg_str), 0, 0
            except ValueError:
                return 0.0, 0, 0

        for rec in records:
            rtype = rec.get("_type", "")
            if rtype == "stats":
                stats_data = {
                    "hp": int(rec.get("hp", 1)),
                    "def": float(rec.get("def", 0.0)),
                    "prot": float(rec.get("prot", 0.0)),
                    "spd": int(rec.get("spd", 0)),
                    "stun_resist": float(rec.get("stun_resist", 0.0)),
                    "poison_resist": float(rec.get("poison_resist", 0.0)),
                    "bleed_resist": float(rec.get("bleed_resist", 0.0)),
                    "debuff_resist": float(rec.get("debuff_resist", 0.0)),
                    "move_resist": float(rec.get("move_resist", 0.0)),
                }
            elif rtype == "enemy_type":
                enemy_type_list = [rec.get("id", "")]
            elif rtype == "display":
                size = int(rec.get("size", 1))
            elif rtype == "initiative":
                actions = int(rec.get("number_of_turns_per_round", 1))
            elif rtype == "monster_brain":
                brain_id = rec.get("id", "default")
                if brain_id:
                    monster_brain_id = str(brain_id)
            elif rtype == "loot":
                loot_code = rec.get("code", "A")
            elif rtype == "death_class":
                corpse_id = rec.get("monster_class_id")
            elif rtype == "wave_spawning":
                prefers_front = bool(rec.get("prefers_front", False))
            elif rtype == "life_link":
                life_link_base_class = rec.get("base_class") or None
            elif rtype == "battle_modifier":
                accelerate_stall_penalty = bool(rec.get("accelerate_stall_penalty", False))
                disable_stall_penalty = bool(rec.get("disable_stall_penalty", False))
                can_be_damaged_directly = bool(rec.get("can_be_damaged_directly", True))
                can_surprise = bool(rec.get("can_surprise", True))
                can_be_surprised = bool(rec.get("can_be_surprised", True))
                always_surprise = bool(rec.get("always_surprise", False))
                always_be_surprised = bool(rec.get("always_be_surprised", False))
            elif rtype in ("skill", "combat_skill"):
                sk_id = rec.get("id", "")
                level = rec.get("level", 0)
                dmg_mod, dmg_min, dmg_max = parse_monster_dmg(rec.get("dmg", 0))
                effects_raw = rec.get("effect", "")
                effects_list = []
                if effects_raw:
                    if isinstance(effects_raw, list):
                        effects_list = [str(e) for e in effects_raw]
                    else:
                        effects_list = [str(effects_raw)]
                target_raw = str(rec.get("target", ""))
                # Same @/~ detection as the hero path (hero_loader._build_hero)
                # — was hardcoded False here, so real AoE monster attacks
                # (e.g. cleaves) only ever hit a single hero.
                is_aoe = target_raw.replace("@", "").startswith("~")
                # '@' = targets the performer's own side (verified: Collector
                # Shaman's head_heal is ".target @1234", identical convention
                # to hero heals) — was never checked at all for monster
                # skills before, so a monster's own heal/buff skill had no
                # way to route to an ally instead of a hero.
                targets_allies = "@" in target_raw

                # Real .heal N M field (verified: head_heal ".heal 6 12",
                # beholder_healself ".heal 10 20") — was never read here at
                # all, only the hero path (_build_hero) read it. Distinct
                # from .dmg, which is a monster's attack damage range and is
                # now stored separately in atk_dmg_min/atk_dmg_max instead of
                # overloading heal_min/heal_max (that overload meant a real
                # heal skill's heal_min/heal_max was always None, since only
                # .dmg was ever consulted, and a fixed .heal value would have
                # collided with the attack-damage-range meaning at the
                # execution site).
                heal_min = None
                heal_max = None
                heal_raw = rec.get("heal")
                if heal_raw is not None:
                    heal_parts = str(heal_raw).replace(",", ".").split()
                    if len(heal_parts) >= 2:
                        heal_min, heal_max = int(float(heal_parts[0])), int(float(heal_parts[1]))
                    elif len(heal_parts) == 1 and heal_parts[0]:
                        heal_min = heal_max = int(float(heal_parts[0]))

                sld = SkillLevelData(
                    level=level,
                    atk=float(rec.get("atk", 0.0)),
                    dmg_mod=dmg_mod,
                    crit=float(rec.get("crit", 0.0)),
                    launch=DarkestParser.decode_position_notation(str(rec.get("launch", ""))),
                    target=DarkestParser.decode_position_notation(target_raw),
                    is_aoe=is_aoe,
                    targets_allies=targets_allies,
                    is_crit_valid=rec.get("is_crit_valid", True),
                    is_continue_turn=rec.get("is_continue_turn", False),
                    effects=effects_list,
                    self_effects=[],
                    heal_min=heal_min,
                    heal_max=heal_max,
                    atk_dmg_min=dmg_min if dmg_min else None,
                    atk_dmg_max=dmg_max if dmg_max else None,
                    move_self=0,
                    move_target=0,
                    push=0,
                    pull=0,
                    valid_modes=["human"],
                    per_battle_limit=None,
                    per_turn_limit=None,
                    is_stall_invalidating=True,
                    extra_targets_chance=float(rec.get("extra_targets_chance", 0.0)),
                    extra_targets_count=int(rec.get("extra_targets_count", 0)),
                    can_be_riposted=bool(rec.get("can_be_riposted", True)),
                    self_target_valid=bool(rec.get("self_target_valid", True)),
                    required_performer_hp_range=_hp_range(rec),
                    ignore_guard=bool(rec.get("ignore_guard", False)),
                    ignore_protection=bool(rec.get("ignore_protection", False)),
                    ignore_stealth=bool(rec.get("ignore_stealth", False)),
                    # Backdraft's whole payload -- see SkillLevelData. Parsed at
                    # BOTH build sites, which this file already duplicates for
                    # `ignore_guard`; the modded-hero path is the second one and
                    # a Fire's Edge class arrives through it.
                    requires_burning=bool(rec.get("requires_burning", False)),
                    dmg_per_burn_stack=float(rec.get("dmg_per_burn_stack", 0.0) or 0.0),
                    copy_burn_behind=bool(rec.get("copy_burn_behind", False)),
                )
                if sk_id not in skills:
                    skills[sk_id] = {}
                skills[sk_id][level] = sld

        name_parts = enemy_id.split("_")
        variant_letter = name_parts[-1] if name_parts[-1] in ("A", "B", "C") else "A"
        variant_map = {"A": "Apprentice", "B": "Veteran", "C": "Champion"}
        variant = variant_map.get(variant_letter, "Apprentice")

        hero_stats = HeroStats(
            hp=int(stats_data.get("hp", 1)),
            dodge=float(stats_data.get("def", 0.0)),
            prot=float(stats_data.get("prot", 0.0)),
            speed=int(stats_data.get("spd", 0)),
            stun_resist=float(stats_data.get("stun_resist", 0.0)),
            blight_resist=float(stats_data.get("poison_resist", 0.0)),
            bleed_resist=float(stats_data.get("bleed_resist", 0.0)),
            debuff_resist=float(stats_data.get("debuff_resist", 0.0)),
            move_resist=float(stats_data.get("move_resist", 0.0)),
            disease_resist=0.0,
            death_blow_resist=0.0,
            trap_resist=0.0,
        )

        enemy_skills = [
            SkillDefinition(id=sk_id, name=sk_id, skill_type=SkillType.MELEE, levels=lvs)
            for sk_id, lvs in skills.items()
        ]

        return EnemyDefinition(
            id=enemy_id,
            name=enemy_id,
            variant=variant,
            stats=hero_stats,
            size=size,
            actions_per_round=actions,
            skills=enemy_skills,
            enemy_types=enemy_type_list,
            monster_brain_id=monster_brain_id,
            loot_code=loot_code,
            corpse_id=corpse_id,
            location="Every Region",
            prefers_front=prefers_front,
            accelerate_stall_penalty=accelerate_stall_penalty,
            disable_stall_penalty=disable_stall_penalty,
            base_class=base_class or enemy_id,
            can_be_damaged_directly=can_be_damaged_directly,
            life_link_base_class=life_link_base_class,
            can_surprise=can_surprise,
            can_be_surprised=can_be_surprised,
            always_surprise=always_surprise,
            always_be_surprised=always_be_surprised,
        )
