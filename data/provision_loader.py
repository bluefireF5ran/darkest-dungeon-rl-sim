from __future__ import annotations
import json
from pathlib import Path
from .darkest_parser import DarkestParser
from .types import ProvisionDefinition


# Provisions modeled in this simulator (v1 scope) — bandage/antivenom/
# holy_water/laudanum as free in-combat actions, food as a between-combat
# heal. Torch/shovel/skeleton_key/medicinal_herbs/dog_treats/firewood are
# real items too but tied to exploration/curios/camping, not modeled here.
MODELED_PROVISION_IDS = {"bandage", "antivenom", "holy_water", "laudanum", "food"}


class ProvisionLoader:
    """Loads inventory/base.supply.inventory.items.darkest (item id, stack
    limit, gold cost) and resolves each to its real mechanical effect in
    effects/base.effects.darkest (already parsed by EffectLoader/
    store.effects — reused here, not re-parsed) via ProvisionDefinition.effect_name."""

    @staticmethod
    def load_all(game_dir: str | Path) -> dict[str, ProvisionDefinition]:
        game_dir = Path(game_dir)
        path = game_dir / "inventory" / "base.supply.inventory.items.darkest"
        if not path.exists():
            return {}
        records = DarkestParser.parse_file(path)
        provisions: dict[str, ProvisionDefinition] = {}
        for rec in records:
            if rec.get("_type") != "inventory_item":
                continue
            item_id = rec.get("id", "")
            # The "food" item has a real id of "" (type "provision" instead
            # of "supply") — verified directly against the file.
            if not item_id and rec.get("type") == "provision":
                item_id = "food"
            if item_id not in MODELED_PROVISION_IDS:
                continue
            provisions[item_id] = ProvisionDefinition(
                id=item_id,
                stack_limit=int(rec.get("base_stack_limit", 0)),
                purchase_gold_value=int(rec.get("purchase_gold_value", 0)),
                effect_name=item_id,
            )
        return provisions

    @staticmethod
    def load_hero_class_starting_items(game_dir: str | Path) -> dict[str, str]:
        """Each hero class starts an expedition with 1 free unit of a
        specific provision — real data from campaign/provision/provision.json's
        raid_starting_hero_class_item_lists (plain JSON, not .darkest). Only
        8 of 18 classes have one; only 3 of those 8 (plague_doctor->antivenom,
        arbalest->bandage, crusader->holy_water) are within this simulator's
        v1 modeled provision set — callers should ignore any id not in
        MODELED_PROVISION_IDS. hero_class strings already match this
        project's internal hero ids exactly (verified: no naming mismatch,
        unlike some other data sources)."""
        game_dir = Path(game_dir)
        path = game_dir / "campaign" / "provision" / "provision.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        result: dict[str, str] = {}
        for entry in data.get("raid_starting_hero_class_item_lists", []):
            hero_class = entry.get("hero_class", "")
            items = entry.get("item_lists", [])
            if hero_class and items:
                result[hero_class] = items[0].get("id", "")
        return result
