#!/usr/bin/env python3
"""
generate_cards.py

Renders Pranav Patil's GitHub profile README as a stack of animated SVG
cards, written into cards/. Each card is a self-contained SVG (inline CSS
keyframes + SMIL <animate> + embedded fonts) so it works when loaded
through a plain <img> tag, which is how GitHub renders README images.

Usage:
    python generate_cards.py              # animated cards
    STATIC=1 python generate_cards.py     # final frame only, for screenshots

Data comes from two JSON files that a GitHub Action keeps fresh:
    data/profile.json  -> name, tagline, link tiles, featured repos (edited by hand)
    data/stats.json    -> contribution stats (written daily by fetch_stats.py)

Note on links: an <a> tag INSIDE an svg does nothing once that svg is
loaded as a plain <img> in a README (GitHub just paints the pixels).
So every card here is still wrapped in a real <a href="..."> at the
markdown level — see README_cards_snippet.md. The <a> tags inside the
SVGs are kept too, in case someone opens the raw .svg file directly.
"""

import base64
import io
import json
import os
import random
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools import subset
from PIL import Image

# ---------------------------------------------------------------------------
# Config — colours and fonts live here so the whole look can be tweaked in
# one place.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "cards"

SANS_FONT_PATH = ROOT / "fonts" / "Inter-Variable.ttf"
SERIF_ITALIC_FONT_PATH = ROOT / "fonts" / "Lora-Italic-Variable.ttf"
SERIF_ROMAN_FONT_PATH = ROOT / "fonts" / "Lora-Roman-Variable.ttf"
SANS_FAMILY = "Inter"
SERIF_FAMILY = "Lora"

BG = "#000000"        # matches the portfolio's theme-color
CREAM = "#F5F1E8"      # hero light-mode card background
PANEL = "#0a0a0a"
BORDER = "#262626"
TEXT = "#ffffff"
MUTED = "#9ca3af"
INK = "#15171c"        # dark text used on the cream hero card
INK_MUTED = "#55585f"

BLUE = "#2F55FF"
RED = "#EF3B2D"
YELLOW = "#FFC81A"
PALETTE = [BLUE, RED, YELLOW]  # cycled across tiles / repo cards

STATIC = os.environ.get("STATIC") == "1"
STAGGER_STEP = 0.5  # extra start-delay given to each card further down the page
EASE = "0.16 1 0.3 1"  # ease-out spline, used on every animate() call


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def text_on(bg_hex: str) -> str:
    """Pick black or white text for readable contrast on a given background."""
    r, g, b = int(bg_hex[1:3], 16), int(bg_hex[3:5], 16), int(bg_hex[5:7], 16)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#111111" if luminance > 150 else "#ffffff"


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
    cache_key = ("photo", path_str, size)
    if cache_key in _image_cache:
        return _image_cache[cache_key]

    img = Image.open(path).convert("RGBA")
    w, h = img.size
    s = min(w, h)
    left, top = (w - s) // 2, (h - s) // 2
    img = img.crop((left, top, left + s, top + s)).resize((size, size), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    _image_cache[cache_key] = data_uri
    return data_uri


def embed_ascii_art(path_str: str, ink_hex: str, size: int = 360) -> str:
    """
    Recolour a black-ink-on-white ASCII art image into a single ink colour
    on a transparent background, so it drops cleanly onto either the cream
    or the black hero card. Returns a base64 PNG data URI.
    """
    if not path_str:
        return ""
    path = ROOT / path_str
    if not path.exists():
        return ""
    cache_key = ("ascii", path_str, ink_hex, size)
    if cache_key in _image_cache:
        return _image_cache[cache_key]

    r, g, b = int(ink_hex[1:3], 16), int(ink_hex[3:5], 16), int(ink_hex[5:7], 16)
    im = Image.open(path).convert("L").resize((size, size), Image.LANCZOS)
    arr = np.array(im).astype(np.uint8)
    alpha = 255 - arr  # dark ink in the source -> opaque; white background -> transparent

    out = np.zeros((size, size, 4), dtype=np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = r, g, b
    out[..., 3] = alpha
    img = Image.fromarray(out, "RGBA")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    _image_cache[cache_key] = data_uri
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
    """One SMIL <animate> tag, eased with a gentle ease-out spline. Skipped in STATIC mode."""
    if STATIC:
        return ""
    return (
        f'<animate attributeName="{attr}" from="{frm}" to="{to}" '
        f'dur="{dur}s" begin="{begin}s" fill="{fill}" '
        f'calcMode="spline" keyTimes="0;1" keySplines="{EASE}" />'
    )


def animate_values(attr: str, values: str, key_times: str, dur: float, begin: float, fill="freeze") -> str:
    """Multi-keyframe SMIL <animate> (e.g. an overshoot-then-settle bounce). Skipped in STATIC mode."""
    if STATIC:
        return ""
    return (
        f'<animate attributeName="{attr}" values="{values}" keyTimes="{key_times}" '
        f'dur="{dur}s" begin="{begin}s" fill="{fill}" '
        f'calcMode="spline" keySplines="0.33 1 0.68 1;0.33 1 0.68 1" />'
    )


def static_or(final, during):
    """Return the already-settled value in STATIC mode, else the mid-animation one."""
    return final if STATIC else during


def reveal_wrap(inner: str, delay: float, rise: int = 16, dur: float = 0.7) -> str:
    """
    Wrap a whole card's content in one fade+rise entrance. Used so every
    card gets the same smooth reveal, on top of whatever extra per-element
    animation that card adds.
    """
    opacity = static_or("1", "0")
    fade = animate("opacity", "0", "1", dur, delay)
    move = animate(
        "transform", f"translate(0,{rise})", "translate(0,0)", dur, delay
    )
    return (
        f'<g class="card-reveal" opacity="{opacity}">{fade}{move}{inner}</g>'
    )


CARD_REVEAL_PIN = """
      .card-reveal { opacity: 1 !important; transform: none !important; }
    """


# ---------------------------------------------------------------------------
# Card: hero — a two-tone logotype card with the ASCII portrait on the
# right, in the style of a "profile card" rather than a plain banner.
# Rendered twice (light / dark) so the README can swap with
# prefers-color-scheme the same way the old README did.
# ---------------------------------------------------------------------------

def render_hero(profile: dict, mode: str, base_delay: float = 0.0) -> str:
    """
    Deliberately minimal, matching a reference profile card: a plain
    cream/black panel, a small dot+label kicker, two tight-set serif name
    lines (roman then italic, same colour, same size), a short
    description, a small text link, and the ASCII portrait sitting large
    on the right. No extra ornament — the type and the art carry it.
    """
    w, h = 800, 300
    left_x = 50
    is_light = mode == "light"

    bg = CREAM if is_light else BG
    text_color = INK if is_light else TEXT
    muted_color = INK_MUTED if is_light else MUTED
    link_color = BLUE
    ascii_ink = INK if is_light else "#e8eaf0"

    chip_label = profile["chip_label"]
    name1 = profile["name_line1"]
    name2 = profile["name_line2"]
    tagline = profile["tagline"]
    website_label = profile["website_label"]
    website_url = profile["website_url"]

    all_text = chip_label + name1 + name2 + tagline + website_label
    font_css = font_face_css(all_text, SANS_FONT_PATH, SANS_FAMILY, 700)
    font_css += font_face_css(all_text, SANS_FONT_PATH, SANS_FAMILY, 500)
    font_css += font_face_css(name1, SERIF_ROMAN_FONT_PATH, SERIF_FAMILY + "-Roman", 500)
    font_css += font_face_css(name2, SERIF_ITALIC_FONT_PATH, SERIF_FAMILY + "-Italic", 500)

    ascii_data = embed_ascii_art(profile.get("ascii_art", ""), ascii_ink)

    # simple staggered reveal, top to bottom — no extra ornament
    d_chip = base_delay
    d_name1 = base_delay + 0.15
    d_name2 = base_delay + 0.32
    d_tag = base_delay + 0.55
    d_link = base_delay + 0.72
    d_art = base_delay + 0.25

    def fade_rise(delay, rise=12, dur=0.6):
        op = static_or("1", "0")
        f = animate("opacity", "0", "1", dur, delay)
        m = animate("transform", f"translate(0,{rise})", "translate(0,0)", dur, delay)
        return op, f, m

    chip_op, chip_f, chip_m = fade_rise(d_chip, 6)
    n1_op, n1_f, n1_m = fade_rise(d_name1, 14)
    n2_op, n2_f, n2_m = fade_rise(d_name2, 14)
    tag_op, tag_f, tag_m = fade_rise(d_tag, 8)
    link_op, link_f, link_m = fade_rise(d_link, 6)

    art_size = 240
    art_x, art_y = w - art_size - 44, (h - art_size) // 2
    art_cx, art_cy = art_x + art_size / 2, art_y + art_size / 2
    art_op = static_or("1", "0")
    art_fade = animate("opacity", "0", "1", 0.8, d_art)
    art_scale_now = static_or("1", "0.96")
    art_anim = animate("transform", "scale(0.96)", "scale(1)", 0.8, d_art)

    svg = []
    svg.append(svg_open(w, h))
    svg.append(f"<style>{font_css}")
    svg.append(
        """
        .chip-dot { fill: %s; }
        .chip-label { font-size: 12px; font-weight: 700; letter-spacing: 2px; fill: %s; }
        .name1 { font-family: '%s-Roman', Georgia, serif; font-size: 54px; font-weight: 500; fill: %s; }
        .name2 { font-family: '%s-Italic', Georgia, serif; font-style: italic; font-size: 54px; font-weight: 500; fill: %s; }
        .tagline { font-size: 15px; font-weight: 500; fill: %s; }
        .website { font-size: 14px; font-weight: 700; fill: %s; }
        @keyframes nudge {
          0%%, 100%% { transform: translate(0,0); }
          50%% { transform: translate(4px,0); }
        }
        .link-arrow { animation: nudge 1.6s ease-in-out infinite; animation-delay: %ss; }
        """
        % (
            link_color, link_color,
            SERIF_FAMILY, text_color,
            SERIF_FAMILY, text_color,
            muted_color,
            link_color,
            d_link + 0.6,
        )
    )
    svg.append(
        reduced_motion_css(
            """
      .reveal-g { opacity: 1 !important; transform: none !important; }
      .link-arrow { transform: none !important; }
      .art-g { opacity: 1 !important; transform: none !important; }
    """
        )
    )
    svg.append("</style>")

    svg.append(f'<rect width="{w}" height="{h}" fill="{bg}" rx="18" />')

    svg.append(
        f'<g class="reveal-g" opacity="{chip_op}">{chip_f}{chip_m}'
        f'<rect class="chip-dot" x="{left_x}" y="40" width="7" height="7" rx="2" />'
        f'<text class="chip-label" x="{left_x+16}" y="47">{esc(chip_label)}</text>'
        f"</g>"
    )
    svg.append(
        f'<text class="name1 reveal-g" x="{left_x-3}" y="112" opacity="{n1_op}">'
        f"{esc(name1)}{n1_f}{n1_m}</text>"
    )
    svg.append(
        f'<text class="name2 reveal-g" x="{left_x-3}" y="168" opacity="{n2_op}">'
        f"{esc(name2)}{n2_f}{n2_m}</text>"
    )
    svg.append(
        f'<text class="tagline reveal-g" x="{left_x}" y="205" opacity="{tag_op}">'
        f"{wrap_tspan(tagline, left_x, 205, 46, 2)}{tag_f}{tag_m}</text>"
    )
    svg.append(
        f'<a href="{esc(website_url)}" target="_blank">'
        f'<g class="reveal-g" opacity="{link_op}">{link_f}{link_m}'
        f'<text class="website" x="{left_x}" y="248">{esc(website_label)}'
        f'<tspan class="link-arrow"> \u2192</tspan></text>'
        f"</g></a>"
    )

    if ascii_data:
        svg.append(
            f'<g class="art-g" opacity="{art_op}" '
            f'style="transform-origin: {art_cx}px {art_cy}px;" '
            f'transform="scale({art_scale_now})">'
            f"{art_fade}{art_anim}"
            f'<image href="{ascii_data}" x="{art_x}" y="{art_y}" '
            f'width="{art_size}" height="{art_size}" />'
            f"</g>"
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
    delay = base_delay + index * 0.15

    font_css = font_face_css(label + desc, SANS_FONT_PATH, SANS_FAMILY, 700)
    font_css += font_face_css(label + desc, SANS_FONT_PATH, SANS_FAMILY, 500)
    font_css += font_face_css(headline, SERIF_ITALIC_FONT_PATH, SERIF_FAMILY, 500)

    shimmer_dur = 2.8
    shimmer_begin = delay + 1.0
    shimmer = "" if STATIC else (
        f'<clipPath id="tileclip-{index}"><rect width="{w}" height="{h}" rx="18" /></clipPath>'
        f'<g clip-path="url(#tileclip-{index})">'
        f'<g class="shimmer-move">'
        f'<animateTransform attributeName="transform" type="translate" '
        f'from="-140 0" to="{w+140} 0" dur="{shimmer_dur}s" begin="{shimmer_begin}s" '
        f'repeatCount="indefinite" />'
        f'<rect class="shimmer" x="0" y="-30" width="60" height="{h+60}" transform="skewX(-18)" />'
        f"</g></g>"
    )

    body = (
        f'<rect class="tile-bg" x="0" y="0" width="{w}" height="{h}" rx="18" />'
        f'<rect class="tile-glow" x="1.5" y="1.5" width="{w-3}" height="{h-3}" rx="16.5" />'
        + shimmer
        + f'<text class="tile-label" x="24" y="34">{esc(label)}</text>'
        f'<g class="arrow-g"><text class="arrow" x="{w-36}" y="34">\u2192</text></g>'
        f'<text class="tile-headline" x="24" y="100">{esc(headline)}</text>'
        f'<text class="tile-desc" x="24" y="{h-40}">'
        + wrap_tspan(desc, 24, h - 40, 34, 2)
        + "</text>"
    )

    svg = []
    svg.append(svg_open(w, h))
    svg.append(f"<style>{font_css}")
    svg.append(
        """
        .tile-bg { fill: %s; }
        .tile-glow { fill: none; stroke: #ffffff; stroke-opacity: 0; stroke-width: 2; }
        .shimmer { fill: #ffffff; opacity: 0.18; }
        .tile-label { font-size: 12px; font-weight: 700; letter-spacing: 1.5px; fill: %s; }
        .tile-headline { font-family: '%s', Georgia, serif; font-style: italic; font-size: 26px; font-weight: 500; fill: %s; }
        .tile-desc { font-size: 13px; font-weight: 500; fill: %s; opacity: 0.85; }
        .arrow { font-size: 16px; font-weight: 700; fill: %s; }
        @keyframes nudge {
          0%%, 100%% { transform: translate(0,0); }
          50%% { transform: translate(4px,0); }
        }
        @keyframes glowpulse {
          0%%, 100%% { stroke-opacity: 0; }
          50%% { stroke-opacity: 0.45; }
        }
        .arrow-g { animation: nudge 1.6s ease-in-out infinite; animation-delay: %ss; }
        .tile-glow { animation: glowpulse 2.4s ease-in-out infinite; animation-delay: %ss; }
        """
        % (color, fg, SERIF_FAMILY, fg, fg, fg, delay + 0.8, delay + 0.9)
    )
    svg.append(
        reduced_motion_css(
            CARD_REVEAL_PIN
            + """
      .arrow-g { transform: none !important; }
      .tile-glow { stroke-opacity: 0 !important; }
      .shimmer-move { display: none !important; }
    """
        )
    )
    svg.append("</style>")

    svg.append(f'<a href="{esc(url)}" target="_blank">' + reveal_wrap(body, delay) + "</a>")
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
    bar_pins = []  # per-bar reduced-motion overrides (final height/y differ per bar)
    for i, (m, v) in enumerate(zip(months, values)):
        x = chart_left + i * (bar_w + bar_gap)
        bar_h = (v / max_v) * (chart_bottom - chart_top)
        y = chart_bottom - bar_h
        delay = base_delay + 0.3 + i * 0.06
        h_final = static_or(str(bar_h), "0")
        y_final = static_or(str(y), str(chart_bottom))

        # grow past the final height slightly, then settle — a small
        # bounce instead of a flat linear grow
        overshoot_h = bar_h * 1.1
        overshoot_y = chart_bottom - overshoot_h
        h_anim = animate_values(
            "height", f"0;{overshoot_h:.1f};{bar_h:.1f}", "0;0.7;1", 0.65, delay
        )
        y_anim = animate_values(
            "y", f"{chart_bottom};{overshoot_y:.1f};{y:.1f}", "0;0.7;1", 0.65, delay
        )
        bar_id = f"bar-{i}"
        bars.append(
            f'<rect id="{bar_id}" class="bar" x="{x:.1f}" y="{y_final}" width="{bar_w:.1f}" '
            f'height="{h_final}" rx="3">{h_anim}{y_anim}</rect>'
        )
        bar_pins.append(
            f'#{bar_id} {{ height: {bar_h:.1f}px !important; y: {y:.1f}px !important; }}'
        )
        labels.append(
            f'<text class="month" x="{x + bar_w/2:.1f}" y="{chart_bottom + 18}" '
            f'text-anchor="middle">{esc(m)}</text>'
        )

    stat_delay = base_delay + 0.9
    stat_opacity = static_or("1", "0")
    stat_fade = animate("opacity", "0", "1", 0.6, stat_delay)
    stat_rise = animate("transform", "translate(0,10)", "translate(0,0)", 0.6, stat_delay)

    title_op = static_or("1", "0")
    title_fade = animate("opacity", "0", "1", 0.5, base_delay)

    body = []
    body.append(
        f'<text class="card-title" x="40" y="28" opacity="{title_op}">'
        f"Contributions per month{title_fade}</text>"
    )
    body.extend(bars)
    body.extend(labels)

    stat_x = [40, 300, 560]
    stat_titles = ["Current streak", "Active days", "Busiest day"]
    stat_values = [f"{streak} days \U0001f525", str(active_days), busiest]
    stat_colors = [RED, YELLOW, BLUE]
    body.append(f'<g class="stat-group" opacity="{stat_opacity}">{stat_fade}{stat_rise}')
    for x, title, val, color in zip(stat_x, stat_titles, stat_values, stat_colors):
        cls = "stat-num streak-flame" if "streak" in title.lower() else "stat-num"
        body.append(f'<text class="{cls}" x="{x}" y="250" fill="{color}">{esc(val)}</text>')
        body.append(f'<text class="stat-label" x="{x}" y="270">{esc(title)}</text>')
    body.append("</g>")

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
        % (BLUE, MUTED, MUTED, TEXT, stat_delay + 0.8)
    )
    svg.append(
        reduced_motion_css(
            """
      .stat-group { opacity: 1 !important; transform: none !important; }
      .streak-flame { opacity: 1 !important; }
      .card-title { opacity: 1 !important; }
    """
            + "\n      " + "\n      ".join(bar_pins)
        )
    )
    svg.append("</style>")
    svg.append(f'<rect width="{w}" height="{h}" fill="{BG}" rx="14" />')
    svg.extend(body)
    svg.append("</svg>")
    return "".join(svg)


# ---------------------------------------------------------------------------
# Card: one per featured repo
# ---------------------------------------------------------------------------

def render_repo_card(repo: dict, index: int, base_delay: float = 0.0) -> str:
    """
    Content is drawn fully-formed and static. A solid colour bar covers
    the whole card at the start and then shrinks down into the slim left
    accent strip, "unwrapping" the repo like a curtain pulling back —
    rather than the text itself fading or sliding.
    """
    w, h = 800, 150
    name = repo["name"]
    desc = repo["description"]
    stack = repo["stack"]
    url = repo["url"]
    color = PALETTE[index % len(PALETTE)]
    delay = base_delay + index * 0.28

    font_css = font_face_css(name + desc + stack, SANS_FONT_PATH, SANS_FAMILY, 700)
    font_css += font_face_css(name + desc + stack, SANS_FONT_PATH, SANS_FAMILY, 400)

    final_w, final_rx = 6, 3
    curtain_w_now = static_or(str(final_w), str(w - 4))
    curtain_rx_now = static_or(str(final_rx), "14")
    curtain_w_anim = animate("width", str(w - 4), str(final_w), 0.75, delay)
    curtain_rx_anim = animate("rx", "14", str(final_rx), 0.75, delay)

    svg = []
    svg.append(svg_open(w, h))
    svg.append(f"<style>{font_css}")
    svg.append(
        """
        .repo-bg { fill: %s; stroke: %s; stroke-width: 1.5; }
        .repo-name { font-size: 20px; font-weight: 700; fill: %s; }
        .repo-desc { font-size: 13px; fill: %s; }
        .repo-stack { font-size: 12px; fill: %s; }
        .curtain { fill: %s; }
        @keyframes drift {
          0%%, 100%% { opacity: 0.5; }
          50%% { opacity: 1; }
        }
        .curtain { animation: drift 2.6s ease-in-out infinite; animation-delay: %ss; }
        """
        % (PANEL, BORDER, TEXT, MUTED, color, color, delay + 1.0)
    )
    svg.append(
        reduced_motion_css(
            """
      .curtain { opacity: 1 !important; width: %spx !important; rx: %spx !important; }
    """
            % (final_w, final_rx)
        )
    )
    svg.append("</style>")

    svg.append(f'<a href="{esc(url)}" target="_blank"><g>')
    svg.append(f'<rect class="repo-bg" x="2" y="2" width="{w-4}" height="{h-4}" rx="14" />')
    svg.append(f'<text class="repo-name" x="40" y="48">{esc(name)}</text>')
    svg.append(
        f'<text class="repo-desc" x="40" y="78">' + wrap_tspan(desc, 40, 78, 95, 2) + "</text>"
    )
    svg.append(f'<text class="repo-stack" x="40" y="124">{esc(stack)}</text>')
    # curtain painted last so it sits on top of everything above, then shrinks away
    svg.append(
        f'<rect class="curtain" x="2" y="2" width="{curtain_w_now}" height="{h-4}" '
        f'rx="{curtain_rx_now}">{curtain_w_anim}{curtain_rx_anim}</rect>'
    )
    svg.append("</g></a>")
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
        delay = base_delay + 0.3 + week * 0.018
        opacity = static_or("1", "0")
        fade = animate("opacity", "0", "1", 0.45, delay)
        cells.append(
            f'<rect class="cell lvl{level}" x="{x}" y="{y}" width="{cell}" '
            f'height="{cell}" rx="2" opacity="{opacity}">{fade}</rect>'
        )

    title_op = static_or("1", "0")
    title_fade = animate("opacity", "0", "1", 0.5, base_delay)

    # one-shot diagonal light sweep across the grid, once the cell wave lands
    grid_w = weeks * (cell + gap) - gap
    grid_h = days * (cell + gap) - gap
    sweep_delay = base_delay + 0.3 + weeks * 0.018 + 0.25
    sweep = "" if STATIC else (
        f'<clipPath id="gridclip"><rect x="{origin_x}" y="{origin_y}" '
        f'width="{grid_w}" height="{grid_h}" rx="2" /></clipPath>'
        f'<g clip-path="url(#gridclip)"><g class="grid-sweep-move">'
        f'<animateTransform attributeName="transform" type="translate" '
        f'from="0 0" to="{grid_w + 120} 0" dur="0.9s" begin="{sweep_delay}s" '
        f'fill="freeze" calcMode="spline" keyTimes="0;1" keySplines="{EASE}" />'
        f'<rect class="grid-sweep" x="{origin_x-60}" y="{origin_y-20}" '
        f'width="50" height="{grid_h+40}" transform="skewX(-18)" />'
        f"</g></g>"
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
        .grid-sweep { fill: #ffffff; opacity: 0.25; }
        """
        % (TEXT, MUTED, PANEL, BLUE, BLUE, BLUE, BLUE)
    )
    svg.append(
        reduced_motion_css(
            """
      .cell { opacity: 1 !important; }
      .cg-title { opacity: 1 !important; }
      .grid-sweep-move { display: none !important; }
    """
        )
    )
    svg.append("</style>")
    svg.append(f'<rect width="{w}" height="{h}" fill="{BG}" rx="14" />')
    svg.append(
        f'<text class="cg-title" x="30" y="18" opacity="{title_op}">'
        f"Contribution activity{title_fade}</text>"
    )
    svg.extend(cells)
    svg.append(sweep)

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
    (OUT_DIR / "hero_light.svg").write_text(
        render_hero(profile, "light", delay), encoding="utf-8"
    )
    (OUT_DIR / "hero_dark.svg").write_text(
        render_hero(profile, "dark", delay), encoding="utf-8"
    )

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

    delay += STAGGER_STEP + 0.22 * len(profile["featured_repos"])
    (OUT_DIR / "contrib_graph.svg").write_text(
        render_contrib_graph(stats, delay), encoding="utf-8"
    )

    mode = "static" if STATIC else "animated"
    print(f"wrote {len(list(OUT_DIR.glob('*.svg')))} {mode} cards to {OUT_DIR}/")


if __name__ == "__main__":
    main()
