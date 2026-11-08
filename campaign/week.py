"""One campaign week: the town visit, the embark, the raid, the return.

The decisions are the bridge's (`townplan.advise`, `embarkplan.plan_embark`);
this module only APPLIES them and the raid's outcome to `CampaignState`.

ASSUMPTIONS (the rules the files do not state), each recorded in the
week's log so a campaign can be audited:
- a treated hero spends the week in the building, heals the activity's
  `heals`, and loses an affliction (`affliction_id`) on the way out;
- an idle hero heals `idle_hero_stress_heal` (5, `roster.variables.json`,
  "town_visit_town_progression") per week;
- a failed quest keeps the raid's loot (`estate.json` `quest_fail_keep_rates`:
  gold, trinket and heirloom 1.0) and pays no quest reward, XP or dungeon XP;
- dungeon XP is `quest_completion_xp_table[length]` (`progression.json`), and
  plot quests pay none (`completion_dungeon_xp: false` on every one);
- a trinket reward of rarity R is a uniform draw from the store's trinkets
  of that rarity;
- a survivor's quest-end quirk and disease rolls are `campaign.quirks`;
- not modelled: quirk loss, the combat's own diseases, town events,
  the Sanitarium, activity side effects (gambling, going missing), the
  Courtyard and the Darkest Dungeon (no `Quest.simulable` quest is taken).
"""
from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import bridge.townplan as townplan
from bridge.embarkplan import plan_embark
from bridge.town_state import Quest, load_resolve_thresholds
from bridge.trinketplan import plan_trinkets
from bridge.townplan import advise, quirk_kinds
from campaign.board import BoardQuest, generate_board, plot_board
from campaign.expedition import ExpeditionRunner
import campaign.quirks as quirks
from campaign.loot import raid_loot
from campaign.recruits import camping_skills, draw_recruits, generation
from campaign.state import CampaignState, new_campaign
from data.paths import GAME_DIR
from env.expedition_env import UNRESTRICTED_HEROES
from data.quest_gen_loader import PlotQuest, load, load_plot_quests

IDLE_STRESS_HEAL = 5.0


@dataclass
class WeekLog:
    week: int
    quest: str = ""
    comp: str = ""
    #: which `_decide_embark` branch: "comp" (an affordable curated comp),
    #: "partial" (the cheapest, part-equipped), "none" (no comp fieldable).
    branch: str = ""
    equipped_unlocked: int = 0
    trinkets_equipped: int = 0
    gear_bought: int = 0
    plot_id: str = ""
    party: tuple = ()
    success: Optional[bool] = None
    deaths: tuple = ()
    #: per death, `(guid, hero_class, resolve_level)` -- the chronicle's names
    fallen: tuple = ()
    gold_reward: int = 0
    loot_gold: int = 0
    hires: tuple = ()
    dismissals: tuple = ()
    treatments: int = 0
    #: Sanitarium visits: (action, quirk, gold, guid, hero_class) per hero sent.
    sanitarium: tuple = ()
    upgrades: tuple = ()
    level_ups: tuple = ()
    #: quirk and disease ids the survivors came home with (`campaign.quirks`)
    quirks_gained: tuple = ()
    roster: int = 0
    gold_after: int = 0
    #: The raid exactly as it was run, so it can be re-run with one factor
    #: changed (`campaign/ablate.py`): quest, seed, and per seat the hero's
    #: state plus the comp's own four skills.
    raid: dict = field(default_factory=dict)
    notes: tuple = ()


def _plot_quest(p: PlotQuest, drawn: tuple = ()) -> Quest:
    """`drawn` is this visit's prize for the plot's `extra_trinket_rarities`:
    the board names the very rare a boss pays, and that named trinket is what
    `embarkplan.boss_worth_it` judges (Fran waits for a good one)."""
    return Quest(key=p.plot_id, quest_id=p.plot_id, dungeon=p.dungeon,
                 quest_type="kill_boss", difficulty=p.difficulty, length=p.length,
                 gold_reward=p.gold, reward_trinkets=tuple(p.trinkets) + tuple(drawn))


def equip(state: CampaignState, hero, wanted) -> list[str]:
    """The four skills a hero can actually take into the raid: the comp's
    ones they have UNLOCKED, then their own, up to four.

    The plan names the comp's four for every seat, including a fodder hero
    the Guild never pays for (Fran, 2026-09-25) -- and the game only lets an
    unlocked skill be equipped. Taking `seat.skills` as given made every
    level-0 recruit fight with a curated kit it did not own (found
    2026-10-10, before any F2 measurement)."""
    owned = {t.split(".", 1)[1] for (g, t), n in state.ranks.items()
             if g == hero.guid and n > 0 and t.startswith(f"{hero.hero_class}.")}
    take = [k for k in wanted if k in owned]
    rest = [k for k in hero.selected_skills if k in owned and k not in take]
    return (take + rest)[:4]


def _level(xp: int, thresholds) -> int:
    th = list(thresholds) or [0, 2, 8, 14, 24, 36, 48]
    return max(i for i, t in enumerate(th) if xp >= t)


#: **A boss party goes fully equipped** (Fran, 2026-10-12: "go with fully
#: equipped level 2 heroes if possible"): its seats may spend the whole gold
#: above the provisions reserve on weapon and armour ranks, against the
#: `gear_budget` fraction an ordinary raid gets. The ranks stay capped by the
#: hero's level and the Blacksmith's, as `nurture_buys` enforces.
BOSS_GEAR_BUDGET = 1.0


class Campaign:
    def __init__(self, runner: ExpeditionRunner, seed: int = 0,
                 game_dir: Path = GAME_DIR, state: Optional[CampaignState] = None,
                 veto=None, gear_budget: float = 0.0):
        # Vanilla + DLC, never the Workshop: see townplan.INCLUDE_WORKSHOP.
        townplan.INCLUDE_WORKSHOP = False
        self.runner = runner
        self.store = runner.store
        self.game_dir = Path(game_dir)
        self.rng = random.Random(seed)
        self.seed = seed
        self.state = state or new_campaign(self.game_dir)
        self.tables = load(self.game_dir)
        self.plots = load_plot_quests(self.game_dir)
        self.thresholds = load_resolve_thresholds(self.game_dir)
        self.profile = Path(tempfile.mkdtemp(prefix="ddsim_campaign_"))
        self.log: list[WeekLog] = []
        #: `bridge.embark_veto.make_veto(...)`, as the live town passes it
        #: (`bridge/town.py`); None = no veto.
        self.veto = veto
        #: EXPERIMENT knob, off by default (the bridge's own rules apply):
        #: before each raid, buy the seated heroes' gear with up to this
        #: fraction of the gold, through `townplan.nurture_buys`. It breaks
        #: Fran's 2026-09-25/26 rulings on purpose -- it prices them.
        self.gear_budget = gear_budget
        self._by_rarity: dict[str, list[str]] = {}
        for t in self.store.trinkets.values():
            self._by_rarity.setdefault(t.rarity, []).append(t.id)
        #: The plot prizes draw from their own stream, so a campaign that
        #: never takes a boss draws every other number exactly as before.
        self._prize_rng = random.Random(f"prize-{seed}")
        #: The quest-end quirk rolls draw from their own stream too, so
        #: switching them off moves no other number.
        self._quirk_rng = random.Random(f"quirks-{seed}")
        # Its own stream, so the Sanitarium's cure-all rolls shift no other draw.
        self._sanitarium_rng = random.Random(f"sanitarium-{seed}")
        self._prizes: dict[str, tuple] = {}

    # -- helpers -----------------------------------------------------------
    def _rank(self, tree: str) -> int:
        return self.state.ranks.get((0, tree), 0)

    def _trinket(self, rarity: str, rng: Optional[random.Random] = None) -> Optional[str]:
        pool = sorted(self._by_rarity.get(rarity, ()))
        return (rng or self.rng).choice(pool) if pool else None

    def _town(self, quests):
        town = self.state.to_town_state(quests, self.game_dir)
        # The Survivalist opens at a dungeon level of 2 (`townplan.closed`).
        town.highest_dungeon_level = max(
            (self.tables.dungeon_level(xp) for xp in self.state.dungeon_xp.values()), default=0)
        self.state.write_profile(self.profile)
        return town

    # -- the week ----------------------------------------------------------
    def week(self) -> WeekLog:
        s, rng = self.state, self.rng
        s.week += 1
        s.visits += 1
        log = WeekLog(week=s.week)
        s.buildings.clear()                     # last week's patients are back

        s.recruits = draw_recruits(rng, self.store,
                                   coach_ranks=self._rank("stage_coach.numrecruits"),
                                   upgraded_ranks=self._rank("stage_coach.upgraded_recruits"),
                                   first_visit=s.visits == 1, game_dir=str(self.game_dir))
        levels = [h.resolve_level for h in s.heroes]
        board = generate_board(rng, self.tables, visit_index=s.visits,
                               quests_finished=s.quests_finished, dungeon_xp=s.dungeon_xp,
                               roster_levels=levels, completed_plots=s.completed_plots)
        plots = plot_board(self.plots, self.tables, dungeon_xp=s.dungeon_xp,
                           completed_plots=s.completed_plots)
        # ASSUMPTION: a plot's random prize is re-drawn every visit, as the
        # board itself is (the game regenerates it per visit).
        self._prizes = {p.plot_id: tuple(t for r in p.extra_trinket_rarities
                                         if (t := self._trinket(r, self._prize_rng)))
                        for p in plots}
        quests = [b.quest for b in board] + [_plot_quest(p, self._prizes[p.plot_id])
                                             for p in plots]
        by_key = {b.quest.key: b for b in board}
        plot_by_key = {p.plot_id: p for p in plots}

        # 1. the town
        town = self._town(quests)
        adv = advise(town, store=self.store, upgrades_file=self.profile / "persist.upgrades.json",
                     game_dir=self.game_dir)
        for p in adv.purchases:
            s.ranks[(0, p.node.tree_id)] = s.ranks.get((0, p.node.tree_id), 0) + 1
        log.upgrades = tuple(f"{p.node.tree_id}.{p.node.code}" for p in adv.purchases)
        s.wallet = {k: int(v) for k, v in adv.wallet_after.items()}
        gone = {h.guid for h in adv.dismissals}
        for g in gone:
            s.heroes = [h for h in s.heroes if h.guid != g]
        log.dismissals = tuple(sorted(gone))
        for hire in adv.hires:
            r = hire.recruit
            gen = generation(r.hero_class, str(self.game_dir))
            camp = camping_skills(rng, self.store, r.hero_class, gen.class_camping, gen.shared_camping)
            s.add_hero(r.hero_class, resolve_xp=r.resolve_xp,
                       resolve_level=_level(r.resolve_xp, self.thresholds),
                       quirks=r.quirks, combat_skills=r.skills, camping_skills=camp,
                       game_dir=self.game_dir)
        log.hires = tuple(h.recruit.hero_class for h in adv.hires)
        heroes = {h.guid: h for h in s.heroes}
        for t in adv.treatments:
            h = heroes.get(t.guid)
            if h is None:
                continue
            s.buildings[h.guid] = t.building
            h.stress = max(0.0, h.stress - float(t.heals))
            h.affliction_id = None
        log.treatments = len(adv.treatments)
        # The Sanitarium (Fran's option 4, 2026-10-12): the hero spends the
        # week there. A cure removes the disease chosen and, at the slot's
        # shipped chance, every other disease too.
        kinds = quirk_kinds(str(self.game_dir))
        for v in adv.sanitarium:
            h = heroes.get(v.guid)
            if h is None:
                continue
            s.buildings[h.guid] = "sanitarium"
            if v.action == "cure":
                h.quirks = [q for q in h.quirks if q != v.quirk]
                if self._sanitarium_rng.random() < v.cure_all_chance:
                    h.quirks = [q for q in h.quirks if kinds.get(q) != "disease"]
            elif v.action == "remove":
                h.quirks = [q for q in h.quirks if q != v.quirk]
                s.locked.get(h.guid, set()).discard(v.quirk)
            elif v.action == "lock":
                s.locked.setdefault(h.guid, set()).add(v.quirk)
        log.sanitarium = tuple((v.action, v.quirk, v.cost, v.guid, v.hero_class)
                               for v in adv.sanitarium)
        for n in adv.nurture:
            h = heroes.get(n.guid)
            if h is None:
                continue
            key = (h.guid, f"{h.hero_class}.{n.tree}")
            s.ranks[key] = s.ranks.get(key, 0) + 1
            if n.tree == "weapon":
                h.weapon_rank += 1
            elif n.tree == "armour":
                h.armour_rank += 1

        # 2. the embark
        town = self._town(quests)
        plan = plan_embark(town, self.store, self.profile, rng, game_dir=str(self.game_dir),
                           veto=self.veto)
        party_guids: set = set()
        if plan is not None and plan.quest is not None and plan.party:
            log.quest, quest = plan.quest.label(), plan.quest
            log.comp = plan.comp
            log.notes = tuple(plan.notes)
            text = " ".join(plan.notes)
            log.branch = ("none" if plan.comp == "(no comp)"
                          else "partial" if "part-equipped" in text else "comp")
            log.equipped_unlocked = sum(len(seat.unlocks) for seat in plan.party)
            party = [seat.hero.hero for seat in plan.party]
            party_guids = {h.guid for h in party}
            log.party = tuple(h.hero_class for h in party)
            for seat in plan.party:
                h = seat.hero.hero
                for skill in seat.unlocks:
                    s.ranks[(h.guid, f"{h.hero_class}.{skill}")] = 1
                if seat.skills and h.hero_class not in UNRESTRICTED_HEROES:
                    h.selected_skills = equip(s, h, seat.skills)
            s.pay("gold", -(int(plan.basket_cost) + int(plan.unlocks_cost)))
            # Trinkets, as the live town does it (`town.equip_trinkets`):
            # everything back in the chest, then Fran's team-builder picks
            # for the seated party (`bridge.trinketplan`). Equipped ones leave
            # the chest for the raid.
            picks = plan_trinkets([(h.hero_class, list(h.selected_skills)) for h in party],
                                  list(s.trinkets)) if s.trinkets else []
            for h in s.heroes:
                h.trinkets = []
            for h, mine in zip(party, picks):
                h.trinkets = list(mine)
                for t in mine:
                    s.trinkets.remove(t)
            log.trinkets_equipped = sum(len(h.trinkets) for h in party)
            if self.gear_budget > 0:
                boss = quest.quest_type == "kill_boss"
                log.gear_bought = self._buy_gear(
                    party, max(self.gear_budget, BOSS_GEAR_BUDGET) if boss else None)
            plot = plot_by_key.get(quest.key)
            log.plot_id = plot.plot_id if plot else ""
            raid_seed = rng.randrange(2**31)
            loadout = self.store.comp_loadout_by_name(plan.comp) if plan.comp else None
            comp_heroes = list(loadout.heroes) if loadout is not None else []
            log.raid = {
                "comp": plan.comp,
                "dungeon": quest.dungeon, "difficulty": quest.difficulty,
                "length": quest.length, "seed": raid_seed,
                "boss_monsters": list(plot.boss_monsters) if plot else [],
                "heroes": [{"guid": h.guid, "hero_class": h.hero_class,
                            "resolve_level": h.resolve_level, "stress": h.stress,
                            "weapon_rank": h.weapon_rank, "armour_rank": h.armour_rank,
                            "selected_skills": list(h.selected_skills),
                            "camping_skills": list(h.camping_skills),
                            "quirks": list(h.quirks), "trinkets": list(h.trinkets),
                            "affliction_id": h.affliction_id,
                            "virtue_id": h.virtue_id,
                            "comp_skills": list(seat.skills),
                            "comp_trinkets": (list(comp_heroes[i].trinket_ids)
                                              if i < len(comp_heroes) else [])}
                           for i, (h, seat) in enumerate(zip(party, plan.party))],
            }
            res = self.runner.run(party, dungeon=quest.dungeon, difficulty=quest.difficulty,
                                  length=quest.length, seed=raid_seed,
                                  boss_monsters=plot.boss_monsters if plot else ())
            log.raid["deaths"] = res.deaths
            self._return(log, res, quest, by_key.get(quest.key), plot,
                         supplies=dict(plan.basket))
        else:
            log.notes = ("no embark",)

        # 3. the week passes for everyone who stayed home untreated
        for h in s.heroes:
            if h.guid not in party_guids and h.guid not in s.buildings:
                h.stress = max(0.0, h.stress - IDLE_STRESS_HEAL)
        log.roster = len(s.heroes)
        log.gold_after = int(s.wallet.get("gold", 0))
        self.log.append(log)
        return log

    def _buy_gear(self, party, fraction=None) -> int:
        """Weapon and armour ranks for the seated heroes, front rank first,
        within `gear_budget` of the gold ABOVE the provisions reserve.
        Returns the gold spent.

        **Something is always kept for provisions** (Fran, 2026-10-10): the
        dearest basket any simulable quest can ask for next week -- a Long
        one -- is held back first, so even a budget of 1.0 never leaves the
        next embark short. The fraction applies to what is above it."""
        from bridge.townplan import UPGRADE_PRIORITY, _owned_ranks, closed, nurture_buys
        from data.heirlooms import load_building_upgrades, read_purchased
        s = self.state
        if "blacksmith" in closed(s.to_town_state(game_dir=self.game_dir), self.game_dir):
            return 0                            # opens after 3 quests
        s.write_profile(self.profile)
        nodes = [n for b in {b for b, _t in UPGRADE_PRIORITY} | {"blacksmith", "guild"}
                 for n in load_building_upgrades(str(self.game_dir), b)]
        purchased = read_purchased(self.profile / "persist.upgrades.json", nodes)
        fraction = self.gear_budget if fraction is None else fraction
        budget = int(fraction * max(0, int(s.wallet.get("gold", 0))
                                            - self.provisions_reserve()))
        spent = 0
        for h in party:
            owned = _owned_ranks(self.profile, h, self.game_dir)
            for b in nurture_buys(h, owned, purchased, self.game_dir, budget=budget - spent):
                if b.tree not in ("weapon", "armour") or spent + b.gold > budget:
                    continue
                key = (h.guid, f"{h.hero_class}.{b.tree}")
                s.ranks[key] = s.ranks.get(key, 0) + 1
                if b.tree == "weapon":
                    h.weapon_rank += 1
                else:
                    h.armour_rank += 1
                spent += b.gold
        s.pay("gold", -spent)
        return spent

    def provisions_reserve(self) -> int:
        """The dearest Long basket over the simulable zones (7,750 gold at
        the game's prices)."""
        from data import provisions
        if getattr(self, "_reserve", None) is None:
            from bridge.town_state import SIMULABLE_DUNGEONS
            self._reserve = max(provisions.cost(provisions.basket(z, 3, game_dir=str(self.game_dir)),
                                                str(self.game_dir))
                                for z in SIMULABLE_DUNGEONS)
        return self._reserve

    def _return(self, log: WeekLog, res, quest: Quest, bq: Optional[BoardQuest],
                plot: Optional[PlotQuest], supplies: Optional[dict] = None) -> None:
        s = self.state
        heroes = {h.guid: h for h in s.heroes}
        for f in res.heroes:
            h = heroes.get(f.guid)
            if h is None:
                continue
            if f.died:
                # A dead hero's trinkets are lost with them.
                s.kill(f.guid)
                log.deaths += (h.hero_class,)
                log.fallen += ((h.guid, h.hero_class, h.resolve_level),)
                continue
            h.stress = f.stress
            h.affliction_id, h.virtue_id = f.affliction_id, f.virtue_id
            s.trinkets.extend(h.trinkets)          # back to the chest
            h.trinkets = []
        won = res.combats_reached if res.completed else max(0, res.combats_reached - 1)
        loot = raid_loot(self.store, dungeon=quest.dungeon, difficulty=quest.difficulty,
                         length=quest.length, quest_type=quest.quest_type,
                         combats_won=won, combats_total=res.num_combats,
                         supplies=supplies)
        s.pay("gold", round(loot.gold))
        for kind, n in loot.heirlooms.items():
            s.pay(kind, round(n))
        for _ in range(round(loot.trinkets)):
            t = self._trinket("common")
            if t:
                s.trinkets.append(t)
        log.loot_gold = round(loot.gold)
        log.success = res.success
        survivors = [heroes[f.guid] for f in res.heroes if not f.died and f.guid in heroes]
        if quirks.QUEST_QUIRKS:
            for h in survivors:
                log.quirks_gained += tuple(quirks.quest_end(self._quirk_rng, self.store, h,
                                                            success=bool(res.success),
                                                            locked=frozenset(s.locked.get(h.guid, ()))))
        if not res.success:
            return
        if plot is not None:
            s.finish_plot(plot.plot_id, self.game_dir)
            xp = plot.resolve_xp
            s.trinkets.extend(self._prizes.get(plot.plot_id, ()))
            log.gold_reward = plot.gold
        elif bq is not None:
            s.quests_finished += 1
            s.pay("gold", bq.quest.gold_reward)
            if bq.heirloom:
                s.pay(bq.heirloom, bq.heirloom_amount)
            t = self._trinket(bq.trinket_rarity)
            if t:
                s.trinkets.append(t)
            xp = bq.resolve_xp
            table = self.tables.dungeon_xp_per_quest
            s.dungeon_xp[quest.dungeon] = (s.dungeon_xp.get(quest.dungeon, 0)
                                           + (table[min(quest.length, len(table) - 1)] if table else 0))
            log.gold_reward = bq.quest.gold_reward
        else:
            xp = 0
        for h in survivors:
            h.resolve_xp += xp
            new = _level(h.resolve_xp, self.thresholds)
            if new > h.resolve_level:
                log.level_ups += (f"{h.hero_class}->{new}",)
            h.resolve_level = new
