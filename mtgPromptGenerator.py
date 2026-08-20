#!/usr/bin/env python3
"""
MTG "High Fantasy Classic" card prompt generator.

Reads a card's Scryfall text from a plain text file (card_input.txt by
default, next to this script), and fills in the full image-generation
prompt: mana cost icons and background/title color are all derived
automatically from the mana cost.

IMPORTANT: attach the Thranduil, the Elvenking card image as "Image 1"
every time you submit the generated prompt to your image generator. The
prompt text explicitly scopes that image to style only (layout,
typography, aging, painting technique) — every color, mana icon, and
piece of card text is specified in the text itself, not the image, so
Gemini shouldn't copy Thranduil's colors or subject matter. You do not
need any other reference images; the old problem of pips leaking color
from reference photos was coming from images used for the pips
specifically, and those are gone now that pips are pure text.

Why a file instead of pasting into the terminal: pasting multi-line text
into a running Python prompt is unreliable in some terminals (in
particular Git Bash / MINGW on Windows), and can silently drop lines.
A text file avoids that entirely.

Usage:
    1. Run:  python3 mtg_prompt_generator.py
       (First run with no card_input.txt present creates a template file
       and stops.)
    2. Open card_input.txt in any text editor, replace the example with
       your card's info (as copied from Scryfall's "Copy" button), save.
    3. Run the script again. The finished prompt prints to the screen
       and is also saved into a generated_prompts folder.
    4. Submit the prompt to your image generator with the Thranduil card
       image attached as Image 1.

    To process a differently-named file:
        python3 mtg_prompt_generator.py some_other_card.txt
"""

import re
import sys
import os

# ---------------------------------------------------------------------------
# SERIES CONSTANTS — edit these once, they'll be reused for every card you
# generate. You normally don't need to touch anything else in this file.
# ---------------------------------------------------------------------------
PRICE_CODE = "75¢"
SERIES_CODE = "Z-1"
SET_ABBR = "HOB"
LANG_CODE = "EN"
COPYRIGHT_LINE = "©MEE / ™ & © 2026 Wizards of the Coast"
ARTIST_CREDIT = "J.R.R. Tolkien"
# Set to None to have the model fabricate a plausible collector number.
COLLECTOR_NUMBER_PREFIX_DEFAULT = "R"  # used if a card doesn't specify RARITY:
COLLECTOR_NUMBER_START = 246

DEFAULT_INPUT_FILENAME = "card_input.txt"

RARITY_LETTER = {
    "C": "C", "COMMON": "C",
    "U": "U", "UNCOMMON": "U",
    "R": "R", "RARE": "R",
    "M": "M", "MYTHIC": "M", "MYTHIC RARE": "M",
    "S": "S", "SPECIAL": "S",
    "B": "B", "BONUS": "B",
}

TEMPLATE_EXAMPLE = """# Paste your card's info below, replacing this example, then save this
# file and re-run the script.
#
# Format:
#   Line 1: card name, optionally followed by the mana cost, e.g.
#           "Legolas, Master Archer {1}{G}{G}"
#           (the cost can also be on its own line right after the name —
#           both work)
#   Next:   type line, e.g. "Legendary Creature — Elf Archer"
#   Next:   one or more lines of rules text
#   Last:   power/toughness (e.g. "1/4") or "Loyalty: 5" — omit for cards
#           that have neither
#
# Optional: add a line anywhere below starting with "VISUAL:" to give a
# short visual description of the character for the artwork. Skip it to
# let the model use the character's established appearance.
#
# Optional: add a line anywhere below starting with "RARITY:" (common,
# uncommon, rare, mythic, or just the single letter C/U/R/M) to set the
# collector-number letter to the card's real rarity. Skip it to default
# to Rare.
#
# Lines starting with # are ignored.

Legolas, Master Archer {1}{G}{G}
Legendary Creature — Elf Archer
Whenever you cast a spell that targets Legolas, put a +1/+1 counter on Legolas.
Whenever you cast a spell that targets a creature you don't control, Legolas deals damage equal to its power to up to one target creature.
1/4
RARITY: Rare
"""

# ---------------------------------------------------------------------------
# Mana symbol key
# ---------------------------------------------------------------------------
MANA_ICON = {
    "W": "an outlined silhouette of the Magic: The Gathering plains mana symbol (sun)",
    "U": "an outlined silhouette of the Magic: The Gathering island mana symbol (water droplet)",
    "B": "an outlined silhouette of the Magic: The Gathering swamp mana symbol (skull)",
    "R": "an outlined silhouette of the Magic: The Gathering mountain mana symbol (flame)",
    "G": "an outlined silhouette of the Magic: The Gathering forest mana symbol (tree)",
    "C": "an outlined silhouette of the Magic: The Gathering colorless mana symbol (diamond)",
}

COLOR_STYLE = {
    "W": {"bg": "dusty faded parchment tan", "title": "warm brown-black"},
    "U": {"bg": "dusty slate blue-grey", "title": "deep muted teal"},
    "B": {"bg": "charcoal ash grey", "title": "deep wine red"},
    "R": {"bg": "dusty brick clay", "title": "brick red"},
    "G": {"bg": "muted olive sage", "title": "dark forest green"},
}

COLORLESS_STYLE = {"bg": "warm stone grey", "title": "charcoal black"}

COLOR_NAME = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green", "C": "colorless"}

ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth"]


def ordinal_word(i: int) -> str:
    return ORDINALS[i] if i < len(ORDINALS) else f"{i + 1}th"


def build_mana_cost_phrase(phrases: list) -> str:
    if not phrases:
        return "(No pips — this card's cost is empty, render nothing in this area.)"
    n = len(phrases)
    lines = [f'(Exactly {n} pip{"s" if n != 1 else ""} in a single vertical column, top to bottom):']
    for i, phrase in enumerate(phrases):
        line = f"{i + 1}. {phrase}"
        if i > 0 and phrase == phrases[i - 1]:
            line += f" (identical to pip #{i} above — same icon, do not vary it)"
        lines.append(line)
    return "\n".join(lines)


def build_color_lock(colors: list) -> str:
    # Deliberately avoids naming any color (white/blue/black/red/green) — even mentioning
    # those words here was found to bias the model toward filling the pips with color.
    # It just points at the exact pip breakdown in MANA COST DISPLAY instead.
    return (
        "Do not draw any colored mana symbol shape outside the exact pip breakdown listed in MANA COST "
        "DISPLAY below. This governs SHAPE selection only — every pip is still rendered as hollow black "
        "line art with a transparent interior, never filled with color."
    )

TEMPLATE = """Create a Magic: The Gathering card using the card info below.

Image 1 (Thranduil, the Elvenking) is attached as the sole reference image — it is a STYLE reference ONLY. Match its layout, typography, printing texture, aging, and painting technique exactly. Do NOT take this card's colors, mana symbol shapes, subject matter, pose, or composition from Image 1 — this is a completely different character. Every color, icon, and piece of text for THIS card is specified only in the text below; none of it comes from the reference image.

FORMAT: Vertical portrait-orientation card, taller than it is wide, matching Image 1's exact aspect ratio and proportions (standard trading-card proportions, roughly 2.5:3.5 width:height). Do NOT generate a horizontal or landscape image, and do not generate a wide/letterbox canvas — the output must be a tall rectangle, cropped exactly like Image 1.

CARD_NAME: <<CARD_NAME>>
MANA_COST: <<MANA_COST_RAW>>
TYPE_LINE: <<TYPE_LINE>>
RULES_TEXT: <<RULES_TEXT>>
<<PT_FIELD>>

COLOR LOCK (applies everywhere on this card): <<COLOR_LOCK>>

STYLE: Loose 1970s gouache paperback fantasy-novel cover art, in the style of period Ballantine/Del Rey fantasy paperback covers — visible loaded brushstrokes, soft feathered edges, no hard vector outlines, no digital gloss, no rim lighting, no airbrush smoothness. Low contrast, faded, chalky pigment, matte finish. The main figure sits in nearly the same value range as the background field and is painted thinly enough that the field color shows through — it must not read as a crisp cutout popping off the background. The entire image reads as one soft, muted, unified wash, as if sun-faded over fifty years.

TYPOGRAPHY:
- Top-left: a small plain dark circular emblem with a minimal abstract mark, no letters, above a thin-ruled box with three centered stacked small-cap lines: HIGH / FANTASY / CLASSIC.
- Below that, left-aligned, small bold serif: "<<SERIES_CODE>>", then "<<PRICE_CODE>>", then the mana cost display (below).
- Top center: "<<TYPE_LINE>>" in letterspaced small caps.
- Below it: "J.R.R. TOLKIEN" in large bold black serif all-caps, centered.
- Below that: the card title in large blackletter-flavored serif, centered, color <<TITLE_COLOR>>, no quotation marks, broken across exactly two centered stacked lines exactly as given:
<<TITLE_LINE_1>>
<<TITLE_LINE_2>>
- Lower third, printed directly over the art, plain serif, off-white: "<<RULES_TEXT>>"
<<PT_SENTENCE>>
- Bottom edge: a thin dark solid credit strip meeting the background field as one clean straight line — no outline, seam, or soft transition. Small pale type inside: "<<COLLECTOR_NUMBER>>" at left with "<<SET_ABBR>> • <<LANG_CODE>>" beneath, a small brush icon and "<<ARTIST_CREDIT>>"; at right, "<<COPYRIGHT_LINE>>". A small oval holographic-foil seal centered just above the strip.

MANA COST DISPLAY: Render the cost as a single vertical stack of small circular pips directly beneath "<<PRICE_CODE>>" — this is the ONLY stack of pip-style icons anywhere on the card; do not draw any second column, stamp, or additional set of circular icons anywhere else.
Line Art Only: Every mana pip must be strictly black line art with zero fill.
Transparent Interiors: The inside of every circle must remain completely transparent.
Monochrome Stamp Aesthetic: Treat the whole pip stack as a single-ink line-art rubber stamp printed onto the art, using the same ink as the "<<PRICE_CODE>>" text above it. Ignore any color normally associated with any mana symbol.
Icon shapes only from this list (not whatever mana symbols appear in Image 1) — Pip Breakdown <<MANA_COST_PHRASE>>

FIGURE: <<CARD_NAME>>, centered and large in frame, cropped at mid-thigh — no legs or boots visible. Head just below the title block. <<FIGURE_DESCRIPTION>> Standing fully upright, spine straight, shoulders squared — not crouching, kneeling, or leaning. From roughly the chest down the figure progressively dissolves into the flat background field, losing detail and saturation until the bottom of the frame is nearly flat color.

BACKGROUND: A flat field of <<BACKGROUND_FIELD_COLOR>> filling the entire card — no environment, architecture, or horizon. Textured color field with visible paper grain and loose painterly variation, even ambient light. A faint oversized ornamental motif fitting the character's culture, ghosted into the field behind the figure, barely visible, a shade darker than the field.

AGING: Heavy irregular period wear: white speckled paper loss at edges and corners, foxing spots, slightly off-register printing with faint color fringing at high-contrast edges, faded inks. An uneven extra band of scuffing and small paper chips creeping inward along the left and right edges only. Do not add any crease, fold line, or bend across the card — wear should be limited to edges, corners, and surface speckling/foxing only.

No modern Magic card border, no text box, no digital UI elements — full-bleed painted art with type printed directly over it. Add no text beyond what is specified above.

FINAL CHECK before rendering: every mana pip has a completely transparent interior showing the card's background field — no solid black, grey, white, or colored fill inside any pip circle, even though real Magic mana symbols are colored. One dark ink only for all small type and the price code. Exactly one stack of mana cost pips — no second/duplicate row or column of circular icons anywhere else on the card. Title broken across exactly two lines as given. Figure upright, cropped at mid-thigh, dissolving into the field toward the bottom.
"""


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return s or "card"


def parse_mana_cost(cost_str: str):
    """Return (ordered list of icon phrases, ordered list of color letters present)."""
    tokens = re.findall(r"\{([^}]+)\}", cost_str)
    phrases = []
    colors = []
    for tok in tokens:
        if tok.isdigit():
            phrases.append(f'Hollow black ring with an outlined numeral "{tok}".')
        elif tok.upper() == "X":
            phrases.append('Hollow black ring with an outlined letter "X".')
        elif "/" in tok:
            parts = tok.split("/")
            sub_phrases = []
            for p in parts:
                p = p.upper()
                if p in MANA_ICON:
                    sub_phrases.append(MANA_ICON[p])
                    colors.append(p)
                elif p == "P":
                    sub_phrases.append("an outlined Phyrexian oil-drop icon")
                else:
                    sub_phrases.append(f"an outlined {p} icon")
            phrases.append("Hollow black ring combining " + " and ".join(sub_phrases) + ".")
        elif tok.upper() in MANA_ICON:
            phrases.append(f"Hollow black ring with {MANA_ICON[tok.upper()]}.")
            colors.append(tok.upper())
        elif tok.upper() == "S":
            phrases.append("Hollow black ring with an outlined snowflake icon.")
        else:
            phrases.append(f"Hollow black ring with an outlined {tok} icon.")
    seen = set()
    ordered_colors = []
    for c in colors:
        if c not in seen:
            seen.add(c)
            ordered_colors.append(c)
    return phrases, ordered_colors


def split_title(name: str):
    if "," in name:
        idx = name.rfind(",")
        if 0 < idx < len(name) - 1:
            line1 = name[: idx + 1].strip()
            line2 = name[idx + 1 :].strip()
            if line1 and line2:
                return line1, line2
    words = name.split()
    if len(words) <= 1:
        return name, ""
    lengths = [len(w) for w in words]
    total = sum(lengths) + len(words) - 1
    best_split, best_diff = 1, None
    for i in range(1, len(words)):
        running_len = sum(lengths[:i]) + (i - 1)
        diff = abs(running_len - total / 2)
        if best_diff is None or diff < best_diff:
            best_diff, best_split = diff, i
    return " ".join(words[:best_split]), " ".join(words[best_split:])


def parse_input_file_text(text: str):
    """Strip comments/blank lines, pull out optional VISUAL: and RARITY: lines."""
    visual = ""
    rarity = ""
    content_lines = []
    for raw_line in text.split("\n"):
        s = raw_line.strip()
        if s == "" or s.startswith("#"):
            continue
        if s.upper().startswith("VISUAL:"):
            visual = s.split(":", 1)[1].strip()
            continue
        if s.upper().startswith("RARITY:"):
            rarity = s.split(":", 1)[1].strip()
            continue
        content_lines.append(s)
    return content_lines, visual, rarity


def parse_card(lines):
    if not lines:
        raise ValueError("No card text found in the input file.")

    line0 = lines[0]
    cost_match = re.search(r"((\{[^}]+\})+)\s*$", line0)
    if cost_match:
        mana_cost_raw = cost_match.group(1)
        name = line0[: cost_match.start()].strip()
        next_idx = 1
    elif len(lines) > 1 and re.fullmatch(r"(\{[^}]+\}\s*)+", lines[1]):
        # Name and mana cost were on separate lines.
        name = line0.strip()
        mana_cost_raw = lines[1].strip()
        next_idx = 2
    else:
        name = line0.strip()
        mana_cost_raw = ""
        next_idx = 1

    type_line = lines[next_idx] if len(lines) > next_idx else ""
    rest = lines[next_idx + 1 :]

    pt = None
    loyalty = None
    if rest and re.match(r"^\d+/\d+$", rest[-1]):
        pt = rest[-1]
        rest = rest[:-1]
    elif rest and re.match(r"^Loyalty:\s*\d+$", rest[-1], re.I):
        loyalty = rest[-1]
        rest = rest[:-1]

    rules_text = " ".join(rest)
    return {
        "name": name,
        "mana_cost_raw": mana_cost_raw,
        "type_line": type_line,
        "rules_text": rules_text,
        "pt": pt,
        "loyalty": loyalty,
    }


def resolve_rarity_letter(rarity_text: str) -> str:
    key = rarity_text.strip().upper()
    return RARITY_LETTER.get(key, COLLECTOR_NUMBER_PREFIX_DEFAULT)


def next_collector_number(script_dir, rarity_letter):
    counter_file = os.path.join(script_dir, "collector_number.txt")
    n = COLLECTOR_NUMBER_START
    if os.path.exists(counter_file):
        try:
            n = int(open(counter_file).read().strip())
        except ValueError:
            pass
    with open(counter_file, "w") as f:
        f.write(str(n + 1))
    return f"{rarity_letter} {n:04d}"


def build_prompt(card: dict, visual_description: str, rarity_text: str, script_dir: str) -> str:
    name = card["name"]
    mana_cost_raw = card["mana_cost_raw"]
    type_line = card["type_line"]
    rules_text = card["rules_text"]
    pt = card["pt"]
    loyalty = card["loyalty"]

    phrases, colors = parse_mana_cost(mana_cost_raw)
    mana_cost_phrase = build_mana_cost_phrase(phrases)
    color_lock = build_color_lock(colors)

    if len(colors) == 0:
        style = COLORLESS_STYLE
    elif len(colors) == 1:
        style = COLOR_STYLE[colors[0]]
    else:
        c1, c2 = colors[0], colors[1]
        style = {
            "bg": f"a blended muted tone combining {COLOR_STYLE[c1]['bg']} and {COLOR_STYLE[c2]['bg']}",
            "title": COLOR_STYLE[c1]["title"],
        }

    title_line_1, title_line_2 = split_title(name)

    if pt:
        pt_field = f"POWER/TOUGHNESS: {pt}"
        pt_sentence = f'- Bottom right, large bold serif: "{pt}".'
    elif loyalty:
        pt_field = f"LOYALTY: {loyalty}"
        pt_sentence = f'- Bottom right, large bold serif: "{loyalty}".'
    else:
        pt_field = "PT: (none — not a creature/planeswalker)"
        pt_sentence = ""

    if visual_description.strip():
        figure_description = visual_description.strip()
        if not figure_description.endswith("."):
            figure_description += "."
    else:
        figure_description = (
            f"Depict {name} with their established, canonical appearance from Tolkien's work "
            "and its illustrations — do not invent an unrelated look."
        )

    rarity_letter = resolve_rarity_letter(rarity_text)
    collector_number = next_collector_number(script_dir, rarity_letter)

    out = TEMPLATE
    replacements = {
        "<<CARD_NAME>>": name,
        "<<MANA_COST_RAW>>": mana_cost_raw or "(none)",
        "<<TYPE_LINE>>": type_line,
        "<<RULES_TEXT>>": rules_text,
        "<<PT_FIELD>>": pt_field,
        "<<SERIES_CODE>>": SERIES_CODE,
        "<<PRICE_CODE>>": PRICE_CODE,
        "<<TITLE_COLOR>>": style["title"],
        "<<TITLE_LINE_1>>": title_line_1,
        "<<TITLE_LINE_2>>": title_line_2,
        "<<PT_SENTENCE>>": pt_sentence,
        "<<COLLECTOR_NUMBER>>": collector_number,
        "<<SET_ABBR>>": SET_ABBR,
        "<<LANG_CODE>>": LANG_CODE,
        "<<ARTIST_CREDIT>>": ARTIST_CREDIT,
        "<<COPYRIGHT_LINE>>": COPYRIGHT_LINE,
        "<<MANA_COST_PHRASE>>": mana_cost_phrase,
        "<<COLOR_LOCK>>": color_lock,
        "<<FIGURE_DESCRIPTION>>": figure_description,
        "<<BACKGROUND_FIELD_COLOR>>": style["bg"],
    }
    for placeholder, value in replacements.items():
        out = out.replace(placeholder, value)
    out = out.replace("\n\n\n", "\n\n")
    return out


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_filename = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT_FILENAME
    input_path = input_filename if os.path.isabs(input_filename) else os.path.join(script_dir, input_filename)

    print("=" * 70)
    print("MTG 'High Fantasy Classic' prompt generator")
    print("=" * 70)

    if not os.path.exists(input_path):
        with open(input_path, "w", encoding="utf-8") as f:
            f.write(TEMPLATE_EXAMPLE)
        print(f"\nNo input file found, so I created one for you:\n  {input_path}")
        print("\nOpen it in a text editor, replace the example card with your own")
        print("(paste straight from Scryfall's 'Copy' button), save it, then run")
        print("this script again.")
        return

    with open(input_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    content_lines, visual_description, rarity_text = parse_input_file_text(raw_text)

    try:
        card = parse_card(content_lines)
    except ValueError as e:
        print(f"\nCouldn't parse {input_path}: {e}")
        print("Check it against the format shown in the comments at the top of the file.")
        return

    prompt = build_prompt(card, visual_description, rarity_text, script_dir)

    print(f"\nRead card: {card['name']}")
    print(f"Rarity used for collector number: {resolve_rarity_letter(rarity_text)}"
          + (f" (from RARITY: {rarity_text})" if rarity_text else " (no RARITY: given, defaulted to Rare)"))
    print("\n" + "=" * 70)
    print("GENERATED PROMPT")
    print("=" * 70 + "\n")
    print(prompt)

    out_dir = os.path.join(script_dir, "generated_prompts")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{slugify(card['name'])}_prompt.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(prompt)
    print(f"\nSaved to: {out_path}")
    print("\nRemember: attach the Thranduil, the Elvenking card image as Image 1")
    print("when you submit this prompt — it's the style reference. Every color")
    print("and mana icon on the new card is specified in the text above, not")
    print("the image, so it shouldn't bleed Thranduil's colors in.")
    print(f"\nTo generate the next card, edit {input_filename} with the new card's")
    print("info and run this script again.")


if __name__ == "__main__":
    main()