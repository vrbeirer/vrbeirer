#!/usr/bin/env python3
"""
generate_cards.py

Renders Pranav Patil's GitHub profile README as a stack of animated SVG
cards, written into cards/. Each card is a self-contained SVG (inline CSS
keyframes + SMIL <animate> + two embedded fonts) so it works when loaded
through a plain <img> tag, which is how GitHub renders README images.

Usage:
    python generate_cards.py              # animated cards
    STATIC=1 python generate_cards.py     # final frame only, for screenshots

Data comes from two JSON files that a GitHub Action keeps fresh:
    data/profile.json  -> name, tagline, link tiles, featured repos (edited by hand)
    data/stats.json    -> contribution stats (written daily by fetch_stats.py)
"""

import base64
import io
import json
import os
import random
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools import subset
from PIL import Image

# ---------------------------------------------------------------------------
# Config — colours and fonts live here so the whole look can be tweaked in
# one place. Pulled from Pranav's "build / brand / learn" stat-tile reference
# and his pranavp-portfolio.vercel.app theme (pure black background).
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "cards"

SANS_FONT_PATH = ROOT / "fonts" / "Inter-Variable.ttf"
SERIF_FONT_PATH = ROOT / "fonts" / "Lora-Italic-Variable.ttf"
SANS_FAMILY = "Inter"
SERIF_FAMILY = "Lora"

BG = "#000000"        # matches the portfolio's theme-color
PANEL = "#0a0a0a"
BORDER = "#262626"
TEXT = "#ffffff"
MUTED = "#9ca3af"

BLUE = "#2F55FF"
RED = "#EF3B2D"
YELLOW = "#FFC81A"
PALETTE = [BLUE, RED, YELLOW]  # cycled across tiles / repo cards

STATIC = os.environ.get("STATIC") == "1"
STAGGER_STEP = 0.35  # extra start-delay given to each card further down the page


def text_on(bg_hex: str) -> str:
    """Pick black or white text for readable contrast on a given background."""
    r, g, b = int(bg_hex[1:3], 16), int(bg_hex[3:5], 16), int(bg_hex[5:7], 16)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#111111" if luminance > 150 else "#ffffff"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def esc(text: str) -> str:
    """Escape text for safe use inside SVG/XML."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def load_json(path: Path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


_font_cache = {}


def font_face_css(text: str, font_path: Path, family: str, weight: int = 600) -> str:
    """
    Subset a variable font down to just the characters a card needs, pin it
    to one weight, and return a @font-face block with the font embedded as
    a base64 woff data URI. Embedding is what lets the font load inside a
    sandboxed <img>, where external font files can't be fetched.
    """
    if not font_path.exists():
        return ""  # falls back to the generic font-family list in svg_open()

    cache_key = (str(font_path), weight, "".join(sorted(set(text))))
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    font = TTFont(str(font_path))
    if "fvar" in font:
        axes = {a.axisTag: weight for a in font["fvar"].axes if a.axisTag == "wght"}
        if axes:
            font = instantiateVariableFont(font, axes)

    options = subset.Options()
    options.flavor = "woff"
    options.desubroutinize = True
    options.name_IDs = []
    options.notdef_glyph = False
    options.notdef_outline = False
    options.recalc_bounds = True
    options.recalc_timestamp = False
    options.layout_features = []

    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=text + " .,:-/%!'\u2019\u2192")
    subsetter.subset(font)

    buf = io.BytesIO()
    font.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    css = f"""
    @font-face {{
      font-family: '{family}';
      font-weight: {weight};
      src: url(data:font/woff;base64,{b64}) format('woff');
    }}"""
    _font_cache[cache_key] = css
    return css


_image_cache = {}


def embed_image_b64(path_str: str, size: int = 220) -> str:
    """Centre-crop to a square, downsize, and return a base64 PNG data URI."""
    if not path_str:
        return ""
    path = ROOT / path_str
    if not path.exists():
        return ""
    if path_str in _image_cache:
        return _image_cache[path_str]

    img = Image.open(path).convert("RGBA")
    w, h = img.size
    s = min(w, h)
    left, top = (w - s) // 2, (h - s) // 2
    img = img.crop((left, top, left + s, top + s)).resize((size, size), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    _image_cache[path_str] = data_uri
    return data_uri


def svg_open(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'font-family="{SANS_FAMILY}, Segoe UI, sans-serif">'
    )


def reduced_motion_css(pins: str) -> str:
    """
    `pins` is a block of CSS rules that fix every animated class to its
    resting (final) value. We wrap it in a prefers-reduced-motion query
    and also kill every CSS keyframe loop. CSS declared !important here
    overrides SMIL-driven attribute values too, so this switches both
    animation systems off at once.
    """
    return f"""
    @media (prefers-reduced-motion: reduce) {{
      * {{ animation: none !important; animation-duration: 0s !important; }}
      {pins}
    }}"""


def animate(attr: str, frm: str, to: str, dur: float, begin: float, fill="freeze") -> str:
    """One SMIL <animate> tag. Skipped entirely in STATIC mode."""
    if STATIC:
        return ""
    return (
        f'<animate attributeName="{attr}" from="{frm}" to="{to}" '
        f'dur="{dur}s" begin="{begin}s" fill="{fill}" />'
    )


def static_or(final, during):
    """Return the already-settled value in STATIC mode, else the mid-animation one."""
    return final if STATIC else during


# ---------------------------------------------------------------------------
# Card: hero
# ---------------------------------------------------------------------------

def render_hero(profile: dict, base_delay: float = 0.0) -> str:
    name = profile["name"]
    tagline = profile["tagline"]
    w, h = 800, 220

    font_css = font_face_css(name + tagline, SANS_FONT_PATH, SANS_FAMILY, 700)
    font_css += font_face_css(name + tagline, SANS_FONT_PATH, SANS_FAMILY, 400)

    title_opacity = static_or("1", "0")
    title_anim = animate("opacity", "0", "1", 0.6, base_delay)
    title_dy = animate("transform", "translate(0,10)", "translate(0,0)", 0.6, base_delay)

    tag_opacity = static_or("1", "0")
    tag_anim = animate("opacity", "0", "1", 0.6, base_delay + 0.25)

    underline_len = static_or("0", "120")
    underline_anim = animate("stroke-dashoffset", "120", "0", 0.7, base_delay + 0.15)

    portrait_r = static_or("46", "0")
    portrait_anim = animate("r", "0", "46", 0.5, base_delay + 0.1)

    portrait_data = embed_image_b64(profile.get("portrait", ""))

    svg = []
    svg.append(svg_open(w, h))
    svg.append(f"<style>{font_css}")
    svg.append(
        """
        .title { font-size: 34px; font-weight: 700; fill: %s; }
        .tagline { font-size: 16px; font-weight: 400; fill: %s; }
        .underline { stroke: %s; stroke-width: 3; }
        .ring { stroke: %s; stroke-width: 2; fill: none; }
        """
        % (TEXT, MUTED, BLUE, YELLOW)
    )
    svg.append(
        reduced_motion_css(
            f"""
      .title {{ opacity: 1 !important; transform: none !important; }}
      .tagline {{ opacity: 1 !important; }}
      .underline {{ stroke-dashoffset: 0 !important; }}
      .portrait-ring {{ r: 46 !important; }}
    """
        )
    )
    svg.append("</style>")

    svg.append(f'<rect width="{w}" height="{h}" fill="{BG}" rx="14" />')

    text_x = 40
    if portrait_data:
        cx, cy = 100, h // 2
        svg.append(f'<clipPath id="pclip"><circle cx="{cx}" cy="{cy}" r="44" /></clipPath>')
        svg.append(
            f'<image href="{portrait_data}" x="{cx-44}" y="{cy-44}" '
            f'width="88" height="88" clip-path="url(#pclip)" preserveAspectRatio="xMidYMid slice" />'
        )
        svg.append(
            f'<circle class="ring portrait-ring" cx="{cx}" cy="{cy}" r="{portrait_r}">'
            f"{portrait_anim}</circle>"
        )
        text_x = 180

    svg.append(
        f'<text class="title" x="{text_x}" y="90" opacity="{title_opacity}">'
        f"{esc(name)}{title_anim}{title_dy}</text>"
    )
    svg.append(
        f'<line class="underline" x1="{text_x}" y1="108" x2="{text_x+120}" y2="108" '
        f'stroke-dasharray="120" stroke-dashoffset="{underline_len}">{underline_anim}</line>'
    )
    svg.append(
        f'<text class="tagline" x="{text_x}" y="140" opacity="{tag_opacity}">'
        f"{esc(tagline)}{tag_anim}</text>"
    )
    svg.append("</svg>")
    return "".join(svg)


# ---------------------------------------------------------------------------
# Card: linked tiles (rendered one file per tile, meant for width="32.5%")
# Styled after Pranav's "build / brand / learn" stat-tile reference: a
# solid colour block, a small caps label + arrow top, a big italic
# headline, and a one-line description.
# ---------------------------------------------------------------------------

def render_tile(link: dict, index: int, base_delay: float = 0.0) -> str:
    w, h = 300, 190
    label = link["label"]
    headline = link["headline"]
    desc = link["description"]
    url = link["url"]
    color = PALETTE[index % len(PALETTE)]
    fg = text_on(color)
    delay = base_delay + index * 0.12

    font_css = font_face_css(label + desc, SANS_FONT_PATH, SANS_FAMILY, 700)
    font_css += font_face_css(label + desc, SANS_FONT_PATH, SANS_FAMILY, 500)
    font_css += font_face_css(headline, SERIF_FONT_PATH, SERIF_FAMILY, 500)

    opacity = static_or("1", "0")
    rise = animate("transform", "translate(0,14)", "translate(0,0)", 0.5, delay)
    fade = animate("opacity", "0", "1", 0.5, delay)

    svg = []
    svg.append(svg_open(w, h))
    svg.append(f"<style>{font_css}")
    svg.append(
        """
        .tile-bg { fill: %s; }
        .tile-label { font-size: 12px; font-weight: 700; letter-spacing: 1.5px; fill: %s; }
        .tile-headline { font-family: '%s', Georgia, serif; font-style: italic; font-size: 26px; font-weight: 500; fill: %s; }
        .tile-desc { font-size: 13px; font-weight: 500; fill: %s; opacity: 0.85; }
        .arrow { font-size: 16px; font-weight: 700; fill: %s; }
        @keyframes nudge {
          0%%, 100%% { transform: translate(0,0); }
          50%% { transform: translate(4px,0); }
        }
        .arrow-g { animation: nudge 1.6s ease-in-out infinite; animation-delay: %ss; }
        """
        % (color, fg, SERIF_FAMILY, fg, fg, fg, delay)
    )
    svg.append(
        reduced_motion_css(
            """
      .tile-wrap { opacity: 1 !important; transform: none !important; }
      .arrow-g { transform: none !important; }
    """
        )
    )
    svg.append("</style>")

    svg.append(
        f'<a href="{esc(url)}" target="_blank">'
        f'<g class="tile-wrap" opacity="{opacity}">{fade}{rise}'
        f'<rect class="tile-bg" x="0" y="0" width="{w}" height="{h}" rx="18" />'
        f'<text class="tile-label" x="24" y="34">{esc(label)}</text>'
        f'<g class="arrow-g"><text class="arrow" x="{w-36}" y="34">\u2192</text></g>'
        f'<text class="tile-headline" x="24" y="100">{esc(headline)}</text>'
        f'<text class="tile-desc" x="24" y="{h-40}">'
        + wrap_tspan(desc, 24, h - 40, 34, 2)
        + "</text>"
        f"</g></a>"
    )
    svg.append("</svg>")
    return "".join(svg)


def wrap_tspan(text: str, x: int, y: int, max_chars: int, max_lines: int) -> str:
    """Very small word-wrapper: splits `text` into <tspan> lines by width-in-characters."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = (current + " " + word).strip()
        if len(trial) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    lines = lines[:max_lines]
    tspans = []
    for i, line in enumerate(lines):
        tspans.append(f'<tspan x="{x}" dy="{0 if i == 0 else 18}">{esc(line)}</tspan>')
    return "".join(tspans)


# ---------------------------------------------------------------------------
# Card: contribution chart + streak stats
# ---------------------------------------------------------------------------

def render_chart(stats: dict, base_delay: float = 0.0) -> str:
    w, h = 800, 300
    months = stats["month_labels"]
    values = stats["monthly_contributions"]
    streak = stats["current_streak"]
    active_days = stats["active_days"]
    busiest = stats["busiest_day"]

    chart_left, chart_right = 60, 740
    chart_top, chart_bottom = 40, 190
    chart_w = chart_right - chart_left
    bar_gap = 10
    bar_w = (chart_w - bar_gap * (len(values) - 1)) / len(values)
    max_v = max(values) or 1

    text_all = (
        " ".join(months) + str(streak) + str(active_days) + busiest
        + "Current streak Active days Busiest day Contributions per month"
    )
    font_css = font_face_css(text_all, SANS_FONT_PATH, SANS_FAMILY, 700)
    font_css += font_face_css(text_all, SANS_FONT_PATH, SANS_FAMILY, 400)

    bars = []
    labels = []
    for i, (m, v) in enumerate(zip(months, values)):
        x = chart_left + i * (bar_w + bar_gap)
        bar_h = (v / max_v) * (chart_bottom - chart_top)
        y = chart_bottom - bar_h
        delay = base_delay + i * 0.05
        h_final = static_or(str(bar_h), "0")
        y_final = static_or(str(y), str(chart_bottom))
        h_anim = animate("height", "0", str(bar_h), 0.5, delay)
        y_anim = animate("y", str(chart_bottom), str(y), 0.5, delay)
        bars.append(
            f'<rect class="bar" x="{x:.1f}" y="{y_final}" width="{bar_w:.1f}" '
            f'height="{h_final}" rx="3">{h_anim}{y_anim}</rect>'
        )
        labels.append(
            f'<text class="month" x="{x + bar_w/2:.1f}" y="{chart_bottom + 18}" '
            f'text-anchor="middle">{esc(m)}</text>'
        )

    stat_delay = base_delay + 0.6
    stat_opacity = static_or("1", "0")
    stat_anim = animate("opacity", "0", "1", 0.5, stat_delay)

    svg = []
    svg.append(svg_open(w, h))
    svg.append(f"<style>{font_css}")
    svg.append(
        """
        .bar { fill: %s; }
        .month { font-size: 11px; fill: %s; }
        .stat-num { font-size: 26px; font-weight: 700; }
        .stat-label { font-size: 12px; fill: %s; }
        .card-title { font-size: 16px; font-weight: 700; fill: %s; }
        @keyframes pulse {
          0%%, 100%% { opacity: 1; }
          50%% { opacity: 0.6; }
        }
        .streak-flame { animation: pulse 1.8s ease-in-out infinite; animation-delay: %ss; }
        """
        % (BLUE, MUTED, MUTED, TEXT, stat_delay)
    )
    svg.append(
        reduced_motion_css(
            """
      .stat-group { opacity: 1 !important; }
      .streak-flame { opacity: 1 !important; }
    """
        )
    )
    svg.append("</style>")
    svg.append(f'<rect width="{w}" height="{h}" fill="{BG}" rx="14" />')
    svg.append('<text class="card-title" x="40" y="28">Contributions per month</text>')
    svg.extend(bars)
    svg.extend(labels)

    stat_x = [40, 300, 560]
    stat_titles = ["Current streak", "Active days", "Busiest day"]
    stat_values = [f"{streak} days \U0001f525", str(active_days), busiest]
    stat_colors = [RED, YELLOW, BLUE]
    svg.append(f'<g class="stat-group" opacity="{stat_opacity}">{stat_anim}')
    for x, title, val, color in zip(stat_x, stat_titles, stat_values, stat_colors):
        cls = "stat-num streak-flame" if "streak" in title.lower() else "stat-num"
        svg.append(
            f'<text class="{cls}" x="{x}" y="250" fill="{color}">{esc(val)}</text>'
        )
        svg.append(f'<text class="stat-label" x="{x}" y="270">{esc(title)}</text>')
    svg.append("</g>")
    svg.append("</svg>")
    return "".join(svg)


# ---------------------------------------------------------------------------
# Card: one per featured repo
# ---------------------------------------------------------------------------

def render_repo_card(repo: dict, index: int, base_delay: float = 0.0) -> str:
    w, h = 800, 150
    name = repo["name"]
    desc = repo["description"]
    stack = repo["stack"]
    url = repo["url"]
    color = PALETTE[index % len(PALETTE)]
    delay = base_delay + index * 0.2

    font_css = font_face_css(name + desc + stack, SANS_FONT_PATH, SANS_FAMILY, 700)
    font_css += font_face_css(name + desc + stack, SANS_FONT_PATH, SANS_FAMILY, 400)

    opacity = static_or("1", "0")
    slide = animate("transform", "translate(-20,0)", "translate(0,0)", 0.5, delay)
    fade = animate("opacity", "0", "1", 0.5, delay)

    svg = []
    svg.append(svg_open(w, h))
    svg.append(f"<style>{font_css}")
    svg.append(
        """
        .repo-bg { fill: %s; stroke: %s; stroke-width: 1.5; }
        .repo-name { font-size: 20px; font-weight: 700; fill: %s; }
        .repo-desc { font-size: 13px; fill: %s; }
        .repo-stack { font-size: 12px; fill: %s; }
        .accent-bar { fill: %s; }
        @keyframes drift {
          0%%, 100%% { opacity: 0.5; }
          50%% { opacity: 1; }
        }
        .accent-bar { animation: drift 2.6s ease-in-out infinite; animation-delay: %ss; }
        """
        % (PANEL, BORDER, TEXT, MUTED, color, color, delay)
    )
    svg.append(
        reduced_motion_css(
            """
      .repo-wrap { opacity: 1 !important; transform: none !important; }
      .accent-bar { opacity: 1 !important; }
    """
        )
    )
    svg.append("</style>")

    svg.append(
        f'<a href="{esc(url)}" target="_blank">'
        f'<g class="repo-wrap" opacity="{opacity}">{fade}{slide}'
        f'<rect class="repo-bg" x="2" y="2" width="{w-4}" height="{h-4}" rx="14" />'
        f'<rect class="accent-bar" x="2" y="2" width="6" height="{h-4}" rx="3" />'
        f'<text class="repo-name" x="40" y="48">{esc(name)}</text>'
        f'<text class="repo-desc" x="40" y="78">'
        + wrap_tspan(desc, 40, 78, 95, 2)
        + "</text>"
        f'<text class="repo-stack" x="40" y="124">{esc(stack)}</text>'
        f"</g></a>"
    )
    svg.append("</svg>")
    return "".join(svg)


# ---------------------------------------------------------------------------
# Card: contribution graph (brand-coloured heatmap)
# ---------------------------------------------------------------------------

def render_contrib_graph(stats: dict, base_delay: float = 0.0) -> str:
    w, h = 800, 160
    daily = stats.get("daily_grid")
    weeks = 53
    days = 7
    if not daily:
        # placeholder grid so the card still renders before real data exists
        random.seed(7)
        daily = [random.choice([0, 0, 1, 2, 3, 4]) for _ in range(weeks * days)]

    cell = 11
    gap = 3
    origin_x, origin_y = 30, 30
    max_v = max(daily) or 1

    font_css = font_face_css(
        "Less More Contribution activity", SANS_FONT_PATH, SANS_FAMILY, 700
    )
    font_css += font_face_css(
        "Less More Contribution activity", SANS_FONT_PATH, SANS_FAMILY, 400
    )

    cells = []
    for i, v in enumerate(daily[: weeks * days]):
        week = i // days
        day = i % days
        x = origin_x + week * (cell + gap)
        y = origin_y + day * (cell + gap)
        level = min(4, round((v / max_v) * 4))
        delay = base_delay + week * 0.015
        opacity = static_or("1", "0")
        fade = animate("opacity", "0", "1", 0.4, delay)
        cells.append(
            f'<rect class="cell lvl{level}" x="{x}" y="{y}" width="{cell}" '
            f'height="{cell}" rx="2" opacity="{opacity}">{fade}</rect>'
        )

    svg = []
    svg.append(svg_open(w, h))
    svg.append(f"<style>{font_css}")
    svg.append(
        """
        .cg-title { font-size: 16px; font-weight: 700; fill: %s; }
        .legend-text { font-size: 11px; fill: %s; }
        .lvl0 { fill: %s; }
        .lvl1 { fill: %s; fill-opacity: 0.35; }
        .lvl2 { fill: %s; fill-opacity: 0.55; }
        .lvl3 { fill: %s; fill-opacity: 0.75; }
        .lvl4 { fill: %s; fill-opacity: 1; }
        """
        % (TEXT, MUTED, PANEL, BLUE, BLUE, BLUE, BLUE)
    )
    svg.append(
        reduced_motion_css(
            """
      .cell { opacity: 1 !important; }
    """
        )
    )
    svg.append("</style>")
    svg.append(f'<rect width="{w}" height="{h}" fill="{BG}" rx="14" />')
    svg.append('<text class="cg-title" x="30" y="18">Contribution activity</text>')
    svg.extend(cells)

    legend_x = w - 160
    legend_y = h - 20
    svg.append(f'<text class="legend-text" x="{legend_x}" y="{legend_y+9}">Less</text>')
    for i in range(5):
        lx = legend_x + 35 + i * (cell + gap)
        svg.append(
            f'<rect class="cell lvl{i}" x="{lx}" y="{legend_y}" width="{cell}" height="{cell}" rx="2" />'
        )
    svg.append(
        f'<text class="legend-text" x="{legend_x + 35 + 5*(cell+gap) + 6}" y="{legend_y+9}">More</text>'
    )
    svg.append("</svg>")
    return "".join(svg)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")


def main():
    profile = load_json(ROOT / "data" / "profile.json")
    stats = load_json(ROOT / "data" / "stats.json")
    if profile is None or stats is None:
        raise SystemExit("missing data/profile.json or data/stats.json")

    OUT_DIR.mkdir(exist_ok=True)

    delay = 0.0
    (OUT_DIR / "hero.svg").write_text(render_hero(profile, delay), encoding="utf-8")

    delay += STAGGER_STEP
    for i, link in enumerate(profile["links"][:3]):
        (OUT_DIR / f"tile_{i+1}.svg").write_text(
            render_tile(link, i, delay), encoding="utf-8"
        )

    delay += STAGGER_STEP
    (OUT_DIR / "chart.svg").write_text(render_chart(stats, delay), encoding="utf-8")

    delay += STAGGER_STEP
    for i, repo in enumerate(profile["featured_repos"]):
        name_slug = slugify(repo["name"])
        (OUT_DIR / f"repo_{name_slug}.svg").write_text(
            render_repo_card(repo, i, delay), encoding="utf-8"
        )

    delay += STAGGER_STEP + 0.2 * len(profile["featured_repos"])
    (OUT_DIR / "contrib_graph.svg").write_text(
        render_contrib_graph(stats, delay), encoding="utf-8"
    )

    mode = "static" if STATIC else "animated"
    print(f"wrote {len(list(OUT_DIR.glob('*.svg')))} {mode} cards to {OUT_DIR}/")


if __name__ == "__main__":
    main()
