"""Quirk tiers from PizzaRat's review, keyed by the GAME's quirk ids.

Source: https://pizzarat105.github.io/PizzaRatReview/dd_map_test.html, the
tier list Fran uses (2026-09-22 and 2026-09-23: *"this site has a really good
quirk tier list"*). Fetched 2026-09-23. The site uses its own ids and display
names, so each entry is mapped to the id `quirk_library.json` uses: Tippler is
`alcoholism`, "X-Phobe" is the four zone phobes, Corvid's is `corvids_*`, and
so on. Site entries with no game id (the Color of Madness "prismatic" quirks,
Weak Grip on Life) are left out rather than guessed.

The VALUES per tier are this project's convention, not the site's: the site
ranks, and a score needs a number. They are monotone in the rank and bounded
by +-1, which is all a curio's expected polarity uses.
"""
from __future__ import annotations

POSITIVE_TIERS = {
    "S": ("luminous", "corvids_grace", "corvids_eye", "slayer_of_eldritch",
          "slayer_of_man", "natural_swing", "quick_reflexes", "hippocratic",
          "precision_striker", "eagle_eye"),
    "A": ("slayer_of_beast", "early_riser", "evasive", "hard_skinned", "slugger",
          "unerring", "natural_eye", "on_guard", "quickdraw", "hot_to_trot",
          "slayer_of_unholy", "tough"),
    "B": ("hatred_of_unholy", "daredevil", "hard_noggin", "clotter",
          "thick_blooded", "photomania", "improved_balance"),
    # The site's C tier also names four FAMILIES -- explorer, adventurer,
    # scrounger, tactician -- one quirk per zone in the game.
    "C": ("steady", "fairweather_fighter", "second_wind", "clutch_hitter",
          "corvids_resilience", "robust")
         + tuple(f"{zone}_{kind}" for zone in ("ruins", "warren", "weald", "cove")
                 for kind in ("explorer", "adventurer", "scrounger", "tactician")),
    "D": ("irrepressible", "unyielding", "resilient", "gifted",
          "gift_of_the_healer", "stout", "fast_healer"),
    "E": ("gothic", "spiritual", "musical", "back_tracker", "fated",
          "nymphomania", "meditator", "skilled_gambler"),
    "F": ("last_gasp",),
}
POSITIVE_VALUE = {"S": 1.0, "A": 0.8, "B": 0.6, "C": 0.35, "D": 0.15,
                  "E": 0.05, "F": -0.3}

#: Priority 1 = "The Worst of the Worst" ... 10 = "Unnoticable at Best".
NEGATIVE_PRIORITY = {
    1: ('corvids_curiosity', 'compulsive', 'curious'),
    2: ('corvids_appetite', 'ablutomania', 'bloodthirsty', 'dacnomania', 'dark_temptation', 'demonomania', 'dipsomania', 'egomania', 'guilty_conscience', 'hagiomania', 'hieromania', 'hylomania'),
    3: ('fading', 'off_guard', 'slowdraw', 'corvids_blindness', 'fear_of_eldritch', 'fear_of_man', 'fear_of_beast', 'fear_of_unholy', 'the_yips', 'nocturnal', 'fragile', 'imposter_syndrome'),
    4: ('slow_reflexes', 'ashen', 'antsy', 'sensitive_to_light', 'phengophobia', 'claustrophobia'),
    5: ('weak_grip', 'flawed_release', 'lazy_eye', 'torn_rotator_cuff', 'scattering', 'bad_healer', 'risktaker'),
    6: ('automatonophobia', 'zoophobia', 'satanophobia', 'soft', 'infirm', 'anemic', 'thin_blooded', 'shocker', 'sickly', 'calm', 'clumsy'),
    7: ('dud_hitter', 'tuckered_out', 'inaccurate', 'thanatophobia'),
    8: ('lygophobia', 'ruins_phobe', 'warren_phobe', 'weald_phobe', 'cove_phobe', 'germophobe', 'nervous_bleeder', 'alcoholism', 'gambler', 'love_interest', 'stress_eater', 'night_blindness', 'diurnal'),
    9: ('mercurial', 'perfectionist', 'ruminator', 'enlightened', 'god_fearing', 'flagellant'),
    10: ('shard_hungry', 'ascetic', 'scientific', 'unquiet_mind', 'witness', 'faithless', 'tone_deaf', 'resolution', 'known_cheat', 'deviant_tastes', 'bad_gambler', 'winded', 'husk_slayer', 'scythemaster'),
}


def negative_value(priority: int) -> float:
    """-1.0 at priority 1 rising to -0.1 at priority 10."""
    return -1.0 + 0.1 * (int(priority) - 1)


QUIRK_VALUE: dict = {}
for _tier, _ids in POSITIVE_TIERS.items():
    for _q in _ids:
        QUIRK_VALUE[_q] = POSITIVE_VALUE[_tier]
for _p, _ids in NEGATIVE_PRIORITY.items():
    for _q in _ids:
        QUIRK_VALUE.setdefault(_q, negative_value(_p))


def quirk_value(quirk_id: str, default: float = 0.0) -> float:
    """The tier value of one quirk, in [-1, 1]; `default` when unrated."""
    return QUIRK_VALUE.get(quirk_id, default)
