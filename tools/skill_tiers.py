"""One expert's skill tier list, read as a RULER and never as a target.

Fran, 2026-08-28:

    "i've curated a tierlist made by an expert with 9000+ hours on the game,
    the skills are not ordered between tiers, again, this is not to bias the
    model, rather as a judge to evaluate if a model is playing good"

Same standing rule as `tools/check_class_play.py`: nothing here may become a
reward term, an action mask or a training target. A policy fitted to a tier list
is a policy that has memorised one person's opinion of nineteen classes, which
is exactly the un-scalable player the mechanics-not-identity work exists to
avoid -- and it would transfer to a modded class as nothing at all, since the
list does not cover one.

As a JUDGE it is the best instrument this project has for "is it playing well".
Every other ruler here measures outcomes (`run_score`, SUCCESS) or agreement
with recorded play; this one measures the QUALITY of a choice directly, against
someone who has 9,000 hours and no stake in the model.

## What it covers, and what it does not

**17 classes x 7 skills = 119, exactly.** Musketeer, Duelist and Runaway are
absent from the source list, not untiered by accident, and Musketeer is
deliberately NOT folded into Arbalest despite the shared icon set. Camp skills
and modded classes are not tiered. Everything uncovered resolves to `None` and
must be EXCLUDED from a rate rather than counted as any tier -- scoring an
untiered skill as average would quietly flatter or punish whichever classes the
list happens to omit.

The source is one image transcribed by icon-matching, so it is one person's
opinion rather than anything the game asserts, and the tiers are unordered
WITHIN themselves. Read a gap of one tier as noise and a gap of three as signal.

The file lives in Fran's team-builder project rather than in this repo, and is
read from an absolute path the same way `GAME_DIR` is. Missing file is not an
error: every caller degrades to "no tier known".
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

# Fran's `dd-team-builder`. Absolute, like GAME_DIR, and for the same reason:
# it is external data this repo reads and does not own.
SKILL_TIERS_JS = Path(r"F:\Personal_Fran\Programas\Softwares\nextjs_projects"
                      r"\dd-team-builder\src\data\skillTiers.js")

# The expert talking through every placement, transcribed from the video. This
# is the REASONING behind the tiers, and it is worth more than the tiers: it
# says what a skill is FOR, and in what context. It lives in the repo because
# Fran put it there.
TIER_TRANSCRIPT = (Path(__file__).resolve().parent.parent / "metadata" /
                   "HEROES" / "tier_explanation.txt")

# Words too common to identify a skill by. Kept tiny on purpose -- the matcher
# prefers the RAREST token it can find, so a long stop list would only remove
# evidence.
_STOP = {"of", "the", "to", "a", "and", "for"}

# Worst last, matching the source's own order. The labels are the tier list's
# own wording, which is worth keeping: "Game-breaking" and "Niche or worse" say
# more about the intended reading than S and D do.
TIER_ORDER = ("S", "A", "B", "C", "D")
TIER_LABEL = {"S": "Game-breaking", "A": "Very powerful",
              "B": "Would be criminal to call bad", "C": "Possibly decent",
              "D": "Niche or worse"}
TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}

_CLASS_RE = re.compile(r'"([^"]+)":\s*\{([^}]*)\}', re.S)
_ENTRY_RE = re.compile(r'"([^"]+)":\s*"([SABCD])"')


@lru_cache(maxsize=4)
def load_tiers(path: str | None = None) -> dict[str, dict[str, str]]:
    """{class display name: {skill display name: tier}}, or {} if absent.

    Parsed with a regex rather than a JS engine: the source is a flat literal of
    string-to-string maps and has been since it was written. A shape this simple
    does not justify a dependency, and a parse that silently returned nothing
    would be caught by `--tier-report` printing zero coverage.
    """
    src = Path(path) if path else SKILL_TIERS_JS
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return {}
    body = text.split("SKILL_TIER_BY_CLASS", 1)
    if len(body) < 2:
        return {}
    out: dict[str, dict[str, str]] = {}
    for cls, block in _CLASS_RE.findall(body[1]):
        entries = dict(_ENTRY_RE.findall(block))
        if entries:
            out[cls] = entries
    return out


def tier_index(store, game_dir, path: str | None = None) -> dict[tuple[str, str], str]:
    """{(hero_id, skill_id): tier}.

    The list is keyed by DISPLAY name, and display names are resolved through
    the same localization maps the comp loader uses -- never by guessing at the
    id, because the game says "First Aid" where the string table says "Wound
    Care" and both are `first_aid`.
    """
    from data.comp_loadout_loader import (HERO_DISPLAY_NAME_TO_ID,
                                          CompLoadoutLoader)

    tiers = load_tiers(path)
    if not tiers:
        return {}
    skill_maps = CompLoadoutLoader._build_skill_name_maps(
        Path(game_dir), set(store.heroes))
    class_map = CompLoadoutLoader._hero_class_map(store, Path(game_dir))
    out: dict[tuple[str, str], str] = {}
    for cls_display, entries in tiers.items():
        hero_id = class_map.get(cls_display) or HERO_DISPLAY_NAME_TO_ID.get(cls_display)
        if hero_id is None or hero_id not in store.heroes:
            continue
        by_name = skill_maps.get(hero_id, {})
        for skill_display, tier in entries.items():
            sid = CompLoadoutLoader._resolve_display(
                skill_display, by_name, CompLoadoutLoader._fuzzy_index(by_name))
            if sid:
                out[(hero_id, sid)] = tier
    return out


@lru_cache(maxsize=2)
def _transcript(path: str | None = None) -> str:
    try:
        return (Path(path) if path else TIER_TRANSCRIPT).read_text(encoding="utf-8")
    except OSError:
        return ""


def explanations(store, game_dir, path: str | None = None) -> dict[tuple[str, str], str]:
    """{(hero_id, skill_id): the passage where the expert discusses it}.

    The transcript is speech-to-text and the names are mangled accordingly --
    "Calrops" for Caltrops, "minles" for Manacles, "lens" for Holy Lance,
    "hither" for Come Hither, "a bomb" throughout for the Abomination. Matching
    on the full display name finds 62% of the 119 skills; matching on each
    skill's rarest token with a fuzzy fallback reaches 92%, and **scoring every
    occurrence rather than taking the first brings it back to 82% and makes
    those 82% right**.

    That trade is the whole design. The first mention of a word is usually not
    where he discusses that skill: Open Vein's first "vein" sat inside a
    Suppressing Fire explanation, and the quote came out attached to the wrong
    skill. Every occurrence is now scored by how many of the skill's other
    words sit near it, and a skill named by a single common word is left
    unexplained rather than guessed at. **A misattributed quote is worse than
    no quote** -- these appear next to a claim about the model, and putting an
    expert's name on words he said about something else would discredit the
    whole instrument.

    **This is quotation, not parsing.** Nothing reads meaning out of the text;
    it locates where he talks about a skill so a diagnostic can show his words
    next to a number.
    """
    text = _transcript(path)
    if not text:
        return {}
    low = text.lower()
    words = set(re.findall(r"[a-z']+", low))

    tiers = load_tiers()
    index = tier_index(store, game_dir)
    # How often each word occurs in the TRANSCRIPT, which is what decides
    # whether a word can identify a passage -- distinct from how often it
    # occurs across skill NAMES, which decides which word is worth trying.
    freq_in_text: Counter = Counter(re.findall(r"[a-z']+", low))
    id_by_display: dict[tuple[str, str], tuple[str, str]] = {}
    freq: Counter = Counter()
    entries = []
    for cls_display, skills in tiers.items():
        for skill_display in skills:
            toks = [t for t in re.findall(r"[a-z']+", skill_display.lower())
                    if t not in _STOP and len(t) >= 4]
            entries.append((cls_display, skill_display, toks))
            freq.update(set(toks))

    # display name -> (hero_id, skill_id), so a located passage can be keyed the
    # way every other ruler here is.
    from data.comp_loadout_loader import CompLoadoutLoader
    class_map = CompLoadoutLoader._hero_class_map(store, Path(game_dir))
    skill_maps = CompLoadoutLoader._build_skill_name_maps(
        Path(game_dir), set(store.heroes))
    for cls_display, skill_display, _toks in entries:
        hero_id = class_map.get(cls_display)
        if hero_id is None:
            continue
        by_name = skill_maps.get(hero_id, {})
        sid = CompLoadoutLoader._resolve_display(
            skill_display, by_name, CompLoadoutLoader._fuzzy_index(by_name))
        if sid and (hero_id, sid) in index:
            id_by_display[(cls_display, skill_display)] = (hero_id, sid)

    out: dict[tuple[str, str], str] = {}
    for cls_display, skill_display, toks in entries:
        key = id_by_display.get((cls_display, skill_display))
        if key is None or not toks:
            continue
        # Resolve each of the skill's tokens to how it is actually spelled in
        # the transcript (fuzzily, because the speech-to-text mangles names).
        spelled = []
        for tok in toks:
            if tok in words:
                spelled.append(tok)
                continue
            close = difflib.get_close_matches(tok, words, n=1, cutoff=0.8)
            if close:
                spelled.append(close[0])
        if not spelled:
            continue

        # THE FIRST OCCURRENCE IS THE WRONG ONE, often. He mentions skills in
        # passing all the time -- Open Vein's first "vein" landed in the middle
        # of a Suppressing Fire explanation, and Heroic End's "heroic" inside a
        # passage about Solo. So score every occurrence of the rarest token by
        # how many of the skill's OTHER tokens sit near it, and take the best
        # window. A misattributed quote is worse than no quote: the whole point
        # is to show the expert's words next to a number about the model.
        anchor = min(spelled, key=lambda t: freq_in_text.get(t, 0))
        best, best_score = None, 0
        start = 0
        while True:
            at = low.find(anchor, start)
            if at < 0:
                break
            start = at + 1
            window = low[max(0, at - 260):at + 320]
            score = sum(1 for t in set(spelled) if t in window)
            # A skill named by ONE common word is not identified by it.
            if score > best_score:
                best, best_score = at, score
        if best is None:
            continue
        if len(spelled) > 1 and best_score < 2:
            continue          # only ever found one of its words: not confident
        if len(spelled) == 1 and freq_in_text.get(anchor, 0) > 8:
            continue          # a single, common word cannot pin a passage
        out[key] = _passage(text, best)
    return out


def _passage(text: str, at: int, width: int = 420) -> str:
    """A readable window around `at`, snapped to sentence ends where it can.

    The transcript has no paragraphs and its sentence breaks are whatever the
    speech-to-text guessed, so this is a best effort at not cutting a word in
    half rather than real sentence segmentation.
    """
    start = max(0, at - width // 3)
    end = min(len(text), at + width)
    dot = text.rfind(". ", start, at)
    if dot != -1:
        start = dot + 2
    dot = text.find(". ", end - 60, end + 120)
    if dot != -1:
        end = dot + 1
    return " ".join(text[start:end].split())


def coverage(store, index: dict[tuple[str, str], str]) -> tuple[int, int, list[str]]:
    """(tiered skills, total hero skills, classes with no tier at all).

    Worth printing beside any rate this drives: a rate over 60% of the roster
    means something different from a rate over 95%, and the three missing
    classes are exactly the ones recent work has been about.
    """
    total = tiered = 0
    untiered_classes = []
    for hero_id, hero in store.heroes.items():
        ids = [s.id for s in hero.skills if s.id != "move"]
        hits = sum(1 for sid in ids if (hero_id, sid) in index)
        total += len(ids)
        tiered += hits
        if ids and not hits:
            untiered_classes.append(hero_id)
    return tiered, total, sorted(untiered_classes)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.store_cache import load_store_cached
    from env.gym_env import CSV_DIR, GAME_DIR

    st = load_store_cached(CSV_DIR, GAME_DIR)
    idx = tier_index(st, GAME_DIR)
    n, total, missing = coverage(st, idx)
    print(f"tiered {n} of {total} hero skills ({100 * n / max(total, 1):.0f}%)")
    print(f"classes with no tier: {missing}")
    from collections import Counter
    dist = Counter(idx.values())
    for t in TIER_ORDER:
        print(f"  {t} {TIER_LABEL[t]:32s} {dist.get(t, 0):3d}")
