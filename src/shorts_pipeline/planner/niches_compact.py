"""Compact niche prompts — preserve niche voice + key rules in ~250 words.

This module produces drop-in replacements for the full 2,500-word niche
prompts in `niches/`. Each compact version keeps:
  - Voice / tone reference
  - Subject scope
  - Niche-specific banned phrases (top 5-8)
  - Per-niche transformation (rule 0)
  - Visual hint (palette / arena / lighting)
  - Per-niche word + syllable budget

Designed for 8 GB VRAM laptops running local LM Studio with structured JSON
output — total prompt drops from ~16.5 K tokens to ~700-1,000 tokens.

Use:
    from shorts_pipeline.planner.niches_compact import make_system_prompt, make_user_prompt
    sys_p = make_system_prompt("history")
    user_p = make_user_prompt("history", "The defenestration of Prague, 1618")
"""

from __future__ import annotations

from shorts_pipeline.planner.niche_caps import caps_for


COMPACT_DATA: dict[str, dict] = {
    "business": {
        "voice": "HBO opening sequence crossed with a Financial Times investigation — tight, dangerous, never preachy",
        "scope": "documented mergers, collapses, takeovers, IPOs, sanctions, embargoes, coups, treaties; deceased or retired figures only",
        "transformation": "founder→monopolist, startup→empire, deal→trap, loophole→weapon, handshake→hostile takeover",
        "hook_style": "How does [tiny entity] take down [giant]? Why would [powerful actor] sign a deal that destroys them?",
        "banned": [
            "game-changer", "disruptor", "took the world by storm",
            "billion-dollar idea", "rags to riches", "self-made",
            "blew up overnight", "moved the needle", "leveraged synergies",
        ],
        "visual": "boardroom + trading floor + handshake at signing-table; cobalt/oil-black/brass palette; named light: single conference-room lamp, trading-screen blue, mahogany-amber",
        "color_grade": "dark_thriller for power moves, epic_warm for founder origins",
        "constraint": "Never invent quotes, share prices, vote counts, or sanctioned amounts. Never accuse living private individuals beyond public record.",
    },
    "cosmic": {
        "voice": "Carl Sagan with cosmic horror — awe at scale, the smallness of the viewer, the end of all things",
        "scope": "deep-time geology, heat death, proton decay, Boltzmann brains, void geometry, far-future cosmology (10^10 yrs+); Fermi paradox philosophy",
        "transformation": "now→nothing, full universe→cold dark, structure→entropy, observer→observed-by-nothing",
        "hook_style": "What will be the last [thing] in the universe? When does the cosmos forget [familiar concept]?",
        "banned": [
            "mind-blowing", "this will blow your mind", "scientists are baffled",
            "trillions and trillions", "imagine a universe where",
            "we are all made of stardust", "the universe is 99% empty space",
        ],
        "visual": "deep-field telescope plate + void + dying-star silhouettes; obsidian-black/neutron-blue/red-shift amber; named light: cosmic-microwave-background dim glow, dying-star ember, void-edge starshine",
        "color_grade": "tragic_cold for entropy, dark_thriller for void, ancient_sepia for deep-past",
        "constraint": "Defer mechanism / instrument-driven stories to science niche. Cosmic owns SCALE not MECHANISM.",
    },
    "crime": {
        "voice": "Serial podcast meets a courtroom transcript — quiet, exact, the document does the haunting",
        "scope": "documented cases, indictments, court rulings, criminal organisations on the public record; named victims handled with restraint",
        "transformation": "alibi→confession, witness→suspect, evidence→reversal, jurisdictional gap→escape, badge→accomplice",
        "hook_style": "Who actually [verb] [outcome]? Why does the case file say [counterintuitive fact]?",
        "banned": [
            "shocking truth", "you won't believe", "case that gripped the nation",
            "truth was darker than fiction", "killer was hiding in plain sight",
            "the worst part is", "twisted", "monster", "evil mastermind",
        ],
        "visual": "case-file binder + crime-scene tape + interrogation room; fluorescent green-grey + manila + tungsten amber; named light: single interrogation-room lamp, courtroom skylight, evidence-locker bulb",
        "color_grade": "dark_thriller default, tragic_cold for unsolved",
        "constraint": "Never name living suspects beyond court record. Never sensationalise victims. Use court documents as primary source.",
    },
    "cults": {
        "voice": "Wild Wild Country meets The Master — patient, clinical, the dynamics earn their own dread",
        "scope": "documented cults, high-control groups, manipulation methodologies; survivor testimony and court records as primary sources",
        "transformation": "joiner→loyalist→prisoner, charisma→control→collapse, family→barrier→nothing",
        "hook_style": "What does it cost to leave [the group]? Why did the loyalists believe [the impossible thing]?",
        "banned": [
            "creepy", "weird", "messed up", "OMG",
            "cult leader was crazy", "members were brainwashed sheep",
            "they were all gullible", "you'd never fall for this",
        ],
        "visual": "compound dormitory + lectern at front + ritual interior; bleached cream + acolyte-white + ember + dim sodium overhead",
        "color_grade": "dark_thriller for control, tragic_cold for aftermath, golden_hour ironic for ritual",
        "constraint": "Living members get archetypal silhouettes only. No mockery of religious practice — distinguish cult dynamics from sacred tradition.",
    },
    "documentary": {
        "voice": "BBC Storyville opening — patient, biographical, time has settled the verdict",
        "scope": "fully documented biographical figures with peer-reviewed sources; deceased or with historical distance",
        "transformation": "obscurity→fame→ruin, ally→rival, decision→cost, private letter→public reckoning",
        "hook_style": "Why did [specific person] [specific small act] on [dated morning]?",
        "banned": [
            "changed history", "shaped the world", "still echoes today",
            "rose to power", "the rest is history", "ahead of his time",
            "history will remember", "ushered in a new era",
        ],
        "visual": "period-accurate portrait + biographical-artefact close-up + arena of decision; sepia/ivory/cobalt; named light: study-lamp, candle, period interior",
        "color_grade": "ancient_sepia for pre-1900, epic_warm for triumph, tragic_cold for fall",
        "constraint": "Zero fabricated quotes. Zero alternate-history. Zero presentist verdicts.",
    },
    "edutainment": {
        "voice": "Tom Scott meets Vsauce — friendly curiosity, mechanism-led, no condescension",
        "scope": "true-but-weird phenomena, edge-case engineering, real institutional oddities, traffic-law quirks, decision-of-the-day; published or verifiable sources",
        "transformation": "obvious→counterintuitive, simple→cascading, ignored→central, design→accident, accident→standard",
        "hook_style": "Why does [familiar thing] secretly [unexpected mechanism]? What happens if [edge case] meets [edge case]?",
        "banned": [
            "you won't believe", "mind blown", "this changes everything",
            "did you know", "fun fact", "actually [smug fact]",
            "1-in-a-million", "wait til you hear this",
        ],
        "visual": "diagram on whiteboard + cross-section of the mechanism + workplace at moment of discovery; tungsten + clean white + accent saturated colour",
        "color_grade": "golden_hour for discovery, epic_warm for mechanism reveal",
        "constraint": "Mechanism > anecdote. If the explanation isn't traceable to a real source or paper, don't include the claim.",
    },
    "facts": {
        "voice": "trivia delivered like a careful museum docent — one true thing at a time, no jokes",
        "scope": "verifiable single-fact units, each tied to a named source, place, or measured outcome",
        "transformation": "common-knowledge→corrected, surface→layered, anecdote→numbered fact",
        "hook_style": "What is the only [thing] that [counterintuitive property]? Where on Earth does [familiar law] not apply?",
        "banned": [
            "shocking fact", "mind-blowing fact", "you won't believe",
            "fact you didn't know", "1 in a million",
            "scientists were stunned", "experts can't explain",
        ],
        "visual": "single object + plain background + clean macro; museum-card lighting; named light: vitrine LED, top-down softbox, single bulb",
        "color_grade": "golden_hour, epic_warm — clean and clear",
        "constraint": "One fact per clause. Each fact cite-able. Never approximate when the figure is knowable. Default niche caps (160-210 words).",
    },
    "health": {
        "voice": "a sober doctor on rounds — exact, careful, the body's own language",
        "scope": "published medical findings, documented disease mechanisms, surgical history, mental health (with sensitivity), public-health events",
        "transformation": "diagnosis→reversal, healthy→fragile, drug→side-effect→standard-of-care, single patient→population effect",
        "hook_style": "What does [disease] actually do to [organ]? Why did the cure for [X] kill [Y]?",
        "banned": [
            "doctors hate this trick", "miracle cure", "they don't want you to know",
            "natural alternative", "big pharma", "100% effective",
            "this kills more than [comparison]", "shocking medical truth",
        ],
        "visual": "clinical interior + diagnostic instrument + body-scale anatomy; sterile blue-white + chrome + flesh-warm; named light: surgical lamp, MRI room cool, hospital corridor fluorescent",
        "color_grade": "tragic_cold for terminal stories, dark_thriller for malpractice, epic_warm for breakthroughs",
        "constraint": "Never prescribe. Never claim cure where evidence is preliminary. Suicide / self-harm topics: redirect, use crisis-line framing.",
    },
    "history": {
        "voice": "Dan Carlin cold-open written by Mary Beard — patient, material, irreducibly specific",
        "scope": "documented historical figures, events, treaties, battles, decrees, institutions, dynasties, objects (charters, tombs, ledgers) with primary or peer-reviewed sources",
        "transformation": "obscurity→power, power→exile, one decision→a counted dead, private letter→public reckoning, one document→a redrawn map",
        "hook_style": "Why did [specific figure] [specific small act] on [specific dated morning]? What was inside the box that emptied a treasury?",
        "banned": [
            "rose to power", "history will remember", "ahead of his time",
            "the dark ages", "civilization as we know it", "ushered in a new era",
            "barbarian hordes", "primitive savage", "and the rest is history",
        ],
        "visual": "period-accurate arena (Roman forum, Byzantine throne room, gaslit factory) + period props (wax seals, ledgers, brass astrolabes, quills); pre-1900 sepia/cream/amber, 20th-c map-table tan/ash-grey",
        "color_grade": "ancient_sepia for antique, epic_warm for empire, dark_thriller for political",
        "constraint": "Zero fabricated quotes. Zero alternate-history. Zero presentist verdicts. Anachronism = automatic fail.",
    },
    "lost_tech": {
        "voice": "patient archaeologist + electrical-engineering historian — what they actually built, how it actually worked",
        "scope": "documented archaeological mechanisms (Antikythera, aqueducts, Roman concrete, Greek fire, Damascus steel, Inca rope bridges); historically attested craft processes",
        "transformation": "lost→recovered, primitive-assumed→sophisticated, single artefact→re-engineered system",
        "hook_style": "How did they actually [build/cast/calculate] without [modern thing]? Which piece of [machine] survives — and which is gone?",
        "banned": [
            "ancient aliens", "advanced lost civilisation", "technology we still can't explain",
            "they were more advanced than us", "secret knowledge",
            "the pyramids were built by", "we've lost the secret",
        ],
        "visual": "excavation cross-section + reconstructed mechanism + craft workshop; clay/bronze/stone palette + dig-site sun + workshop-fire ember",
        "color_grade": "ancient_sepia, epic_warm for craft, golden_hour for discovery",
        "constraint": "If the reconstruction is speculative, label it so. Cite the paper / archaeologist. No pseudoscience.",
    },
    "military": {
        "voice": "a former operations planner over coffee — quiet, exact, the doctrine is the story",
        "scope": "documented operations, battles, weapons systems, doctrinal shifts, declassified intelligence; named survivors handled with restraint",
        "transformation": "plan→contact→improvise, doctrine→reformed-after-disaster, weapon→counter-weapon",
        "hook_style": "What does [seemingly-minor unit / decision] cost an entire army? Why did the doctrine survive the disaster?",
        "banned": [
            "elite warrior", "ultimate killing machine", "deadliest soldier",
            "badass", "warriors of old", "this weapon will shock you",
            "they were unstoppable",
        ],
        "visual": "war-room map-table + soldier silhouette + period uniform/equipment; map-table tan, ash-grey, lamp-amber, cigarette smoke; named light: war-room ceiling lamp, command-tent lantern, radar green",
        "color_grade": "dark_thriller, tragic_cold for losses, epic_warm for command-room",
        "constraint": "Never glorify atrocity. Living veterans / families: faceless archetype. Avoid speculative casualty counts.",
    },
    "mythology": {
        "voice": "Neil Gaiman page — patient, image-led, the divine made intimate; sacred is law, not curiosity",
        "scope": "documented myths from world traditions (Norse, Greek, Egyptian, Hindu epics, Yoruba, Polynesian, etc.); always framed 'the story tells', 'in the [tradition] telling'",
        "transformation": "mortal→god, promise→curse, lover→constellation, hospitality refused→generations of punishment, name learned→power yielded",
        "hook_style": "What does it cost to [bargain with a god]? Whose blood made [the river / the constellation]?",
        "banned": [
            "10 weirdest myths", "you won't believe what they believed",
            "ancient peoples believed", "primitive religion", "creepy", "twisted",
            "the OG god of", "main character", "epic showdown",
        ],
        "visual": "sacred-arena interior (temple, underworld threshold, cosmic loom) + iconic divine emblem + mortal supplicant; tradition-specific palette (Norse storm-grey + iron, Egyptian gold + lapis, Hindu sandalwood + saffron + indigo)",
        "color_grade": "epic_warm for divine, tragic_cold for underworld, ancient_sepia for origin",
        "constraint": "Always 'the story tells'. Living religions: respect, no debunking, no parody. Indigenous Dreamtime: only public-shared stories. Mythology max words 206-230 (longer for lyrical voice).",
    },
    "psychology": {
        "voice": "an honest experimentalist — what the study actually found, what the writing-up overstated, what replicates",
        "scope": "published psychology / neuroscience findings, classical experiments (with replication context), documented case studies",
        "transformation": "intuition→counterexample, study→replication-failure, anecdote→mechanism",
        "hook_style": "Why does [obvious behaviour] flip in [specific condition]? What does [experiment] actually measure?",
        "banned": [
            "we only use 10% of our brain", "left-brained / right-brained",
            "alpha male", "men are from mars",
            "this experiment will change how you see", "shocking psychological truth",
            "manifest", "rewire your brain in 7 days",
        ],
        "visual": "lab interior + one-way mirror + subject in chair + clipboard data; cream-grey + lab-coat white + tungsten interior; named light: observation-room two-way mirror glow, clipboard desk lamp",
        "color_grade": "tragic_cold for ethics violations, dark_thriller for manipulation, golden_hour for breakthrough",
        "constraint": "Always cite the replication status. Stanford prison / Milgram / Bobo etc: include the controversy, not just the dramatic finding.",
    },
    "science": {
        "voice": "a working physicist at 2 am after the data came back — specific, slightly haunted, awe lets itself land",
        "scope": "peer-reviewed findings, real instruments and missions (LIGO, JWST, Voyager, LHC), documented mechanisms; near-future projections grounded in published research",
        "transformation": "invisible→measurable, certainty→wrong, silent universe→noisy, theory→reality, natural→built",
        "hook_style": "What happens when [familiar concept] meets [impossible condition]? How do you measure [unmeasurable thing]?",
        "banned": [
            "mind blown", "scientists are baffled", "they don't want you to know",
            "rewrites the textbooks", "we only use 10% of our brain",
            "imagine a universe where", "we are all stardust", "trillions and trillions",
        ],
        "visual": "instrument close-up (mirror coatings, control-room console, detector pit) + Earth-scale or particle-scale subject + working scientist silhouette; clean blue-white + tungsten interior + screen-cyan",
        "color_grade": "epic_warm for breakthrough, tragic_cold for null result, dark_thriller for limits",
        "constraint": "Never invent statistics, dates, distances. Never claim unpublished findings. Pseudoscience = instant reject. Defer end-of-universe scale to cosmic.",
    },
    "sports": {
        "voice": "the broadcast booth meets the dressing-room post-game — exact, slightly resigned, the loss is the story",
        "scope": "documented matches, records, scandals, doping cases, federation rulings, athlete biographies (deceased or retired)",
        "transformation": "favourite→loser, sanction→suspension→exile, single bad call→a championship lost",
        "hook_style": "How does [one second / one play] cost a [championship]? Which call is the reason the record never stood?",
        "banned": [
            "greatest of all time", "GOAT", "unstoppable",
            "they will never be forgotten", "legend of legends",
            "shocked the world", "miracle on [surface]", "out of nowhere",
        ],
        "visual": "stadium tunnel + locker-room bench + scoreboard at decisive moment; chalk-white + grass-green + sodium-floodlight amber; named light: stadium floodlight cone, locker-room overhead, tunnel-mouth backlight",
        "color_grade": "golden_hour for triumph, tragic_cold for collapse, dark_thriller for scandal",
        "constraint": "Living athletes: no defamation beyond public record. Doping / scandal: court documents only. Sports max 191-216 words (lyrical broadcaster voice).",
    },
    "survival": {
        "voice": "the debriefing interview six months later — calm, exact, the body did what it did",
        "scope": "documented survival incidents (mountaineering, maritime, polar, plane crash, urban-disaster); named survivors with their public consent on record",
        "transformation": "comfort→ordeal→rescue, group→solo, plan→improvise, hour 1→hour 240",
        "hook_style": "What does the [hour 72 / day 11] of [ordeal] actually look like? Which decision saved them — and which one nearly killed them?",
        "banned": [
            "incredible survival story", "miracle", "you won't believe they made it",
            "ultimate badass", "against all odds", "the human spirit",
            "death-defying", "1-in-a-million",
        ],
        "visual": "exposure environment + the kit they had + the body marker (frostbite, sun-blister) + the rescue silhouette; bleached snow-white, alpine cobalt, dehydration-grey + dawn-orange rescue",
        "color_grade": "tragic_cold for extremes, epic_warm for rescue, dark_thriller for the night-of-decision",
        "constraint": "Never sensationalise the body. Living survivors: respect their stated narrative. No 'lessons' — let the facts stand.",
    },
    "tech_hackers": {
        "voice": "an incident-response analyst on a closed channel — exact, terse, the timeline is the story",
        "scope": "documented breaches, malware families, CVEs, court-named threat actors, declassified ops; criminal indictments and post-mortems as primary sources",
        "transformation": "patch→exploit→worm, credential→pivot→domain-admin, single click→global outage",
        "hook_style": "How does [one phish] become [a country's payroll]? What did the SOC actually see at minute zero?",
        "banned": [
            "hacker in a hoodie", "they hacked the mainframe",
            "scary easy to hack", "nothing is safe online",
            "1337 elite hacker", "this is illegal don't try",
            "ultimate hacker", "shocking cyber truth",
        ],
        "visual": "terminal scrollback + network diagram + dimly lit SOC + server-room cold aisle; terminal-green + Bakelite-black + LED-blue + server-rack indicator amber",
        "color_grade": "dark_thriller default, tragic_cold for ransomware aftermath, epic_warm for the patch",
        "constraint": "Never publish working exploit code or detailed bypass. Living individuals only via court documents. Threat-actor naming: vendor-attribution only.",
    },
    "wealth": {
        "voice": "estate-lawyer reading the will + macro-economist over whisky — quiet, exact, the inheritance is the story",
        "scope": "documented fortunes, trusts, tax-avoidance structures, inheritance disputes, dynastic wealth; deceased or public-record only",
        "transformation": "earned→inherited→evaporated, profit→trust→three generations later→nothing",
        "hook_style": "How does a [fortune] survive [outcome] for [duration]? Which clause in the trust did everyone miss?",
        "banned": [
            "self-made billionaire", "rags to riches", "they earned every penny",
            "richest in the world", "lifestyles of the rich",
            "secret to wealth", "this one trick", "they got greedy",
        ],
        "visual": "estate library + ledger on rosewood desk + offshore-island bank + signature on a deed; oak/brass/ledger-cream + signature-ink black + offshore-blue + estate-amber",
        "color_grade": "epic_warm for accumulation, dark_thriller for the will-reading, tragic_cold for the collapse",
        "constraint": "Living heirs: only what's in public filings. No defamation. Cite the will / court docket where available. Wealth budget 157-180 words (dry voice).",
    },
}


SCHEMA_REMINDER = """JSON schema (top-level keys):
  historical_figure: string
  cold_open_object: string (one concrete object, no person)
  decision_lever: {lever_type: "law"|"geography"|"politics", description, consequence}
  clauses: 14 items, each {text, image_prompt, motion_prompt, figure_present (bool),
    beat: {emotion, intensity, camera, transition_in, duration_hint, color_grade,
           audio_event, emphasis_words, visual_tier, subtitle_position, cut_target}}
  full_script: string (clause texts joined)
  lut_choice: one of epic_warm|tragic_cold|ancient_sepia|dark_thriller|golden_hour
  end_plate_question: string (the ONLY other question)

emotion ∈ hook|tense_buildup|suspense|reveal|triumphant|tragic|climactic|reflective|shock
camera ∈ ken_burns|pan|zoom_out|hold|parallax
transition_in ∈ hard_cut|xfade|dip_to_black|smash_white
audio_event ∈ none|low_rumble|impact|paper_flutter|crowd_cheer|sword_clash|horse_gallop|fire_crackle|thunder_crack|crowd_murmur
visual_tier ∈ grounded|cinematic|legendary
subtitle_position ∈ top|middle|bottom

Camera verbs in motion_prompt: push-in, pull-back, dolly, pan, tilt, orbit, tracks, zoom, static shot, handheld."""


def make_system_prompt(niche: str) -> str:
    """Build the compact SYSTEM prompt for one niche (~200-300 words)."""
    if niche not in COMPACT_DATA:
        raise ValueError(f"Unknown niche: {niche}. Available: {sorted(COMPACT_DATA)}")
    d = COMPACT_DATA[niche]
    min_w, max_w, max_syl = caps_for(niche)
    banned_lines = "\n".join(f"  - {b}" for b in d["banned"])
    return f"""You write {d['voice']}. Output STRICT JSON only — no markdown fences, no prose.

A YouTube Shorts narration plan: 14 clauses, ~58 seconds, full_script {min_w}-{max_w} words (≤{max_syl} syllables).

SCOPE: {d['scope']}

TRANSFORMATION (decision_lever.description): {d['transformation']} — the SHAPE of the story.

OPENING: Clause 1's first sentence is a curiosity-gap question under 14 words.
  Style: {d['hook_style']}
  Image: dominant hero portrait/object.
CLOSING: end_plate_question is the ONLY other question mark in the entire output.

BANNED PHRASES (any appearance = automatic fail):
{banned_lines}
  - "changed history" / "shaped the world" / "still echoes today"

VISUAL LANGUAGE:
  {d['visual']}
  Each image_prompt MUST contain (a) a SHOT TYPE word — one of: close-up, wide shot, medium shot,
  low-angle, high-angle, dutch angle, establishing, over-the-shoulder, hero shot, hero portrait,
  tracking shot, aerial shot, top-down, profile silhouette, extreme close-up, portrait shot.
  AND (b) a LIGHTING word — one of: light, shadow, candle, lamp, torch, dawn, dusk, glow, dim,
  sunlit, moonlit, backlit, torchlit, chiaroscuro, fluorescent, tungsten, golden hour, blue hour,
  flicker, haze, mist, harsh, soft light, natural light, twilight, sunrise, sunset.
  Each motion_prompt: one camera verb (push-in, pull-back, dolly, pan, tilt, orbit, tracks,
  zoom, static shot, handheld) + subject motion + atmosphere. No two motion_prompts identical.
  Color grade preference: {d['color_grade']}.

CONSTRAINTS: {d['constraint']}

{SCHEMA_REMINDER}"""


def make_user_prompt(niche: str, topic: str, *, use_figure_name: bool = False) -> str:
    """Build the compact USER prompt for one niche × one topic (~150 words)."""
    if niche not in COMPACT_DATA:
        raise ValueError(f"Unknown niche: {niche}")
    name_rule = (
        f'Every image_prompt that shows the hero MUST begin with "{topic}".'
        if use_figure_name
        else f'Never include the subject name inside image_prompt; describe by role + period dress + ONE distinctive feature.'
    )
    return f"""Write a YouTube Short about: {topic!r}.

Anchor every claim in the documented record for this niche.

{name_rule}
≥8 of 14 clauses show a human acting. ≥3 silhouette-first compositions.
Each motion_prompt unique. No banned phrases. Exactly 2 question marks total.

Return one JSON object matching the schema. Start with {{ and end with }}."""


__all__ = ["COMPACT_DATA", "make_system_prompt", "make_user_prompt"]
