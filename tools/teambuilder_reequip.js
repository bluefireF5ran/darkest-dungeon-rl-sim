#!/usr/bin/env node
/**
 * Fran's team-builder trinket re-equip, called from the bridge.
 *
 * The valuation lives in dd-team-builder (`src/utils/trinketReequip.js`,
 * `trinketValue.js`, `heroNeeds.js`, `bisIndex.js`) and encodes his rules.
 * Porting it to Python would copy it out of date, so the bridge calls it here
 * through the builder's own ESM loader. JSON in on stdin, JSON out on stdout:
 *
 *   in : {"party": [{"heroClass": "crusader", "activeSkills": [...]}, ...],
 *         "owned": ["agile_talon", ...]}
 *   out: {"heroes": [{"trinket1": "...", "trinket2": "..."}, ...], "unfilled": n}
 *
 * DD_TEAM_BUILDER overrides where the builder lives.
 */
const fs = require('fs');
const path = require('path');

const ROOT = process.env.DD_TEAM_BUILDER ||
  'F:/Personal_Fran/Programas/Softwares/nextjs_projects/dd-team-builder';
const { loadEsm } = require(path.join(ROOT, 'scripts', 'lib', 'loadEsm'));
const src = (file) => loadEsm(path.join(ROOT, 'src', file));
const { reequipParty } = src('utils/trinketReequip.js');
const { bisLoadout } = src('data/bisIndex.js');
const { HERO_CLASSES } = src('data/heroes.js');
const { nameKey, canonicalizeTrinket, canonicalizeSkill } = src('utils/nameNormalizer.js');

// Game ids (`plague_doctor`, `ancestors_pistol`) to the builder's names, by
// the builder's own normaliser: underscores become spaces, nameKey does the rest.
const CLASS_BY_KEY = new Map(Object.keys(HERO_CLASSES).map((c) => [nameKey(c), c]));
const className = (id) => CLASS_BY_KEY.get(nameKey(String(id).replace(/_/g, ' '))) || id;
const trinketName = (id, cls) =>
  canonicalizeTrinket(String(id).replace(/_/g, ' '), cls) || String(id).replace(/_/g, ' ');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const party = (input.party || []).map((hero, index) => ({
  heroClass: className(hero.heroClass),
  name: hero.name || hero.heroClass,
  activeSkills: (hero.activeSkills && hero.activeSkills.length)
    ? hero.activeSkills.map((id) => canonicalizeSkill(String(id).replace(/_/g, ' '), className(hero.heroClass)) || id)
    : (bisLoadout(hero.heroClass, index + 1)?.activeSkills || []),
  activeCampSkills: [],
  trinket1: '',
  trinket2: '',
  quirks: { positive: [], negative: [] },
  diseases: [],
}));
const owned = (input.owned || []).map((id) => trinketName(id));
const result = reequipParty(party, owned, { estate: true });
process.stdout.write(JSON.stringify({
  heroes: result.heroes.map((h) => ({ trinket1: h.trinket1 || '', trinket2: h.trinket2 || '' })),
  picks: result.picks,
  unfilled: result.unfilled,
}));
