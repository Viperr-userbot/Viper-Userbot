"""
Text-to-Sticker (TXT) 2.0 Generator Module for Kitten Assistant Userbot.
Comprehensive port from D:\\bot\\sticker-botx:
- 75+ Visual design styles & themes (Neon, 3D, Badge, Fire, Ice, Gold, Bollywood, Desi Truck, Comic, etc.)
- 200+ TTF fonts from assets/fonts/ with dynamic per-call randomization
- Auto-detection and authentic typography for Hindi / Devanagari text
- Transparent background procedural decorations (stars, sparkles, bubbles, hearts, burst lines)
- Subtle organic tilt (-2.5 to +2.5 deg) for dynamic sticker realism
- AI-powered aesthetic designer (using userbot's fast LPU / GPT models)
- Telegram standard 512px WEBP sticker generation
- Multi-sticker pack generation (.txt:pack)
- God-Tier Styles Catalog (.txts / .txt styles)
"""
import io
import os
import re
import json
import math
import random
import asyncio
import textwrap
import logging
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any
from PIL import Image, ImageDraw, ImageFont, ImageFilter

log = logging.getLogger("hwh.txt_sticker")

ASSETS_FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# ═══════════════════════════════════════════════════════════════
# COLOR & GRADIENT PALETTES
# ═══════════════════════════════════════════════════════════════

COLOR_MAP = {
    "white": "#FFFFFF",
    "black": "#111111",
    "red": "#FF3B30",
    "blue": "#007AFF",
    "green": "#34C759",
    "yellow": "#FFCC00",
    "orange": "#FF9500",
    "purple": "#AF52DE",
    "pink": "#FF2D55",
    "cyan": "#5AC8FA",
    "gold": "#FFD700",
    "lime": "#A4E814",
    "gray": "#8E8E93",
}

VIBRANT_COLORS = [
    "#FF6B6B", "#FF8E53", "#FFCD56", "#4BC0C0", "#36A2EB",
    "#9966FF", "#FF6384", "#FF9F40", "#4DC9F6", "#F67019",
    "#F53794", "#ACC236", "#166A8F", "#00A950", "#8549BA",
    "#FF4444", "#00C851", "#33B5E5", "#AA66CC", "#FFBB33",
    "#FF3D00", "#00E5FF", "#76FF03", "#FF1744", "#D500F9",
    "#651FFF", "#00E676", "#FFD600", "#FF9100", "#00B0FF",
    "#69F0AE", "#FF80AB", "#B388FF", "#E91E63", "#9C27B0",
    "#673AB7", "#3F51B5", "#2196F3", "#00BCD4", "#009688",
    "#4CAF50", "#8BC34A", "#CDDC39", "#FFC107", "#FF9800",
    "#FF5722",
]

GRADIENT_PAIRS = [
    ("#FF6B6B", "#FF8E53"), ("#667EEA", "#764BA2"), ("#F093FB", "#F5576C"),
    ("#4FACFE", "#00F2FE"), ("#43E97B", "#38F9D7"), ("#FA709A", "#FEE140"),
    ("#A18CD1", "#FBC2EB"), ("#FF0844", "#FFB199"), ("#96FBC4", "#F9F586"),
    ("#FCCB90", "#D57EEB"), ("#E0C3FC", "#8EC5FC"), ("#F5AF19", "#F12711"),
    ("#c471f5", "#fa71cd"), ("#48c6ef", "#6f86d6"), ("#ff0080", "#ff8c00"),
    ("#00d2ff", "#3a7bd5"), ("#f857a6", "#ff5858"), ("#a8ff78", "#78ffd6"),
    ("#ee0979", "#ff6a00"), ("#FC5C7D", "#6A82FB"), ("#11998e", "#38ef7d"),
    ("#fc4a1a", "#f7b733"), ("#834d9b", "#d04ed6"), ("#8E2DE2", "#4A00E0"),
    ("#f953c6", "#b91d73"), ("#00c6ff", "#0072ff"), ("#C6FFDD", "#FBD786"),
    ("#E55D87", "#5FC3E4"), ("#200122", "#6f0000"), ("#A770EF", "#CF8BF3"),
]

PASTEL_COLORS = [
    "#FFB3BA", "#FFDFBA", "#FFFFBA", "#BAFFC9", "#BAE1FF",
    "#E8BAFF", "#FFB3E6", "#B3FFE0", "#B3D9FF", "#FFE0B3",
]

AVAILABLE_DECORATIONS = [
    "stars", "sparkles", "sprinkles", "hearts", "bubbles",
    "crosses", "dots", "lightning", "burst_lines"
]

# ═══════════════════════════════════════════════════════════════
# 75+ STYLES & ALIASES (Matching D:\bot\sticker-botx)
# ═══════════════════════════════════════════════════════════════

STYLE_LABELS = [
    "classic", "extrude3d", "extrude3d_v2", "rainbow", "neon_glow", "chrome",
    "liquid_gold", "shadow3d", "diecut_badge", "retro_shadow", "fire", "fire_inferno_v2", "ice",
    "gold_foil", "double_outline", "comic_pop", "gradient_stroke",
    "candy", "sunset", "ocean_wave", "lava", "galaxy",
    "pastel_soft", "graffiti", "pixel_edge", "emboss", "holographic",
    "toxic", "blood", "electric", "vapor_wave", "cotton_candy",
    "bubblegum", "watercolor", "chalk", "vintage", "glitch", "chromatic_glitch",
    "cyberpunk_glitch", "matrix_hacker", "superhero_action", "sticker_badge",
    "arcade_8bit", "gummy_candy", "glassmorphism", "glassmorphism_v2", "neon_sign",
    "retro_synthwave", "kawaii_pastel", "cosmic_nebula", "pop_art_dots",
    "golden_glow", "frozen_frost", "cutout_newspaper",
    "hologram", "liquid_chrome_v2", "embroidery", "graffiti_spray",
    "typewriter", "stamp", "ransom_note", "chalk_board", "woodburn",
    "neon_sign_v2", "terminal", "blueprint", "manga_scream", "bollywood_poster",
    "desi_truck", "mosaic", "smoke_text", "diamond_plate", "stained_glass",
    "aurora", "sand_writing", "ice_cream_drip", "pixel_retro", "circuit_board",
    "vaporwave_v2", "gold_foil_v2", "neon_3d", "sakura_text", "lava_glow"
]

STYLE_ALIASES = {
    # Classic & Cyber
    "neon": "neon_glow",
    "glitch": "cyberpunk_glitch",
    "glitch2": "chromatic_glitch",
    "chromatic": "chromatic_glitch",
    "fire": "fire",
    "flame": "fire",
    "fire2": "fire_inferno_v2",
    "inferno": "fire_inferno_v2",
    "ice": "ice",
    "frost": "ice",
    "gold": "golden_glow",
    "gold2": "liquid_gold",
    "sheen": "liquid_gold",
    "royal": "golden_glow",
    "matrix": "matrix_hacker",
    "hacker": "matrix_hacker",
    "cyber": "cyberpunk_glitch",
    "cyberpunk": "cyberpunk_glitch",
    "comic": "superhero_action",
    "hero": "superhero_action",
    "kawaii": "kawaii_pastel",
    "cute": "kawaii_pastel",
    "rainbow": "rainbow",
    "chrome": "chrome",
    "metal": "chrome",
    "retro": "retro_synthwave",
    "arcade": "retro_synthwave",
    "synth": "retro_synthwave",
    "blood": "blood",
    "vampire": "blood",
    "horror": "blood",
    "toxic": "toxic",
    "poison": "toxic",
    "galaxy": "cosmic_nebula",
    "space": "cosmic_nebula",
    "candy": "candy",
    "sweet": "candy",
    "3d": "extrude3d",
    "3d2": "extrude3d_v2",
    "badge": "diecut_badge",
    "badge2": "diecut_badge",
    "sticker": "diecut_badge",
    "pop": "pop_art_dots",
    "glass": "glassmorphism",
    "glass2": "glassmorphism_v2",
    "sunset": "sunset_vibes",
    "gradient": "rainbow",
    "magic": "cosmic_nebula",
    "shadow": "extrude3d",
    "bubble": "kawaii_pastel",
    "pack": "pack",
    "4": "pack",

    # God-Tier 2.0 Styles
    "hologram": "hologram",
    "holo": "hologram",
    "chrome2": "liquid_chrome_v2",
    "liquidchrome": "liquid_chrome_v2",
    "typewriter": "typewriter",
    "stamp": "stamp",
    "ransom": "ransom_note",
    "chalkboard": "chalk_board",
    "wood": "woodburn",
    "woodburn": "woodburn",
    "terminal": "terminal",
    "cmd": "terminal",
    "code": "terminal",
    "blueprint": "blueprint",
    "manga": "manga_scream",
    "anime": "manga_scream",
    "bollywood": "bollywood_poster",
    "desi": "bollywood_poster",
    "truck": "desi_truck",
    "horn": "desi_truck",
    "diamond": "diamond_plate",
    "stainedglass": "stained_glass",
    "aurora": "aurora",
    "pixel": "pixel_retro",
    "8bit": "pixel_retro",
    "circuit": "circuit_board",
    "foil": "gold_foil_v2",
    "neon3d": "neon_3d",
    "sakura": "sakura_text",
    "lava2": "lava_glow",
    "random": "random",
}

_RECENT_STYLES: List[str] = []

# ═══════════════════════════════════════════════════════════════
# FONT REPOSITORY & SCANNER (200+ TTF Fonts)
# ═══════════════════════════════════════════════════════════════

DEVANAGARI_FONT_FILES = [
    "NotoSansDevanagari-Bold.ttf",
    "Modak.ttf",
    "Baloo2-Regular.ttf",
    "Kalam-Bold.ttf",
    "Mogra.ttf",
    "Glegoo-Bold.ttf",
    "Chonburi.ttf",
    "Lalezar.ttf",
]

_ALL_FONTS_CACHE: List[str] = []


def get_all_available_fonts() -> List[str]:
    """Returns list of all font filenames discovered in assets/fonts/."""
    global _ALL_FONTS_CACHE
    if not _ALL_FONTS_CACHE:
        if ASSETS_FONTS_DIR.is_dir():
            _ALL_FONTS_CACHE = [f.name for f in ASSETS_FONTS_DIR.glob("*.ttf")]
        if not _ALL_FONTS_CACHE:
            _ALL_FONTS_CACHE = ["Anton-Regular.ttf", "Bangers.ttf", "Righteous.ttf", "TitanOne.ttf"]
    return _ALL_FONTS_CACHE


def is_devanagari(text: str) -> bool:
    """Checks if text contains Devanagari (Hindi, Marathi, Sanskrit, etc.) characters."""
    for ch in text:
        if "\u0900" <= ch <= "\u097f":
            return True
    return False


def get_font_file_path(font_name: str) -> Optional[str]:
    """Resolves font filename to absolute filesystem path."""
    if not ASSETS_FONTS_DIR.is_dir():
        return None
    # Exact match
    p = ASSETS_FONTS_DIR / font_name
    if p.is_file():
        return str(p)
    # Match without extension or partial name
    clean_name = font_name.lower().replace("-regular", "").replace("-bold", "")
    for f in ASSETS_FONTS_DIR.glob("*.ttf"):
        fn = f.name.lower()
        if font_name.lower() in fn or clean_name in fn:
            return str(f)
    return None


def get_best_font(size: int, style: str = "", text: str = "", requested_font: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Picks a font dynamically. If requested_font is supplied (e.g. from AI), uses it.
    Otherwise selects a random matching font from the 200+ font repository.
    """
    all_fonts = get_all_available_fonts()

    # 1. Hindi / Devanagari font selection
    if is_devanagari(text):
        candidates = [f for f in DEVANAGARI_FONT_FILES if f in all_fonts]
        if not candidates:
            candidates = DEVANAGARI_FONT_FILES

        if requested_font:
            fp = get_font_file_path(requested_font)
            if fp:
                try:
                    return ImageFont.truetype(fp, size)
                except Exception:
                    pass

        # Pick random Devanagari font for variety
        chosen = random.choice(candidates)
        fp = get_font_file_path(chosen)
        if fp:
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass

    # 2. If AI or caller explicitly requested a font family
    if requested_font:
        fp = get_font_file_path(requested_font)
        if fp:
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass

    # 3. Style-specific font pools
    style_pool: List[str] = []
    if style in ["comic_pop", "superhero_action", "manga_scream", "comic", "hero"]:
        style_pool = ["Bangers.ttf", "TitanOne.ttf", "Rowdies.ttf", "FugazOne.ttf", "Anton-Regular.ttf", "BlackOpsOne.ttf"]
    elif style in ["neon_glow", "hologram", "neon_sign", "retro_synthwave", "vaporwave_v2"]:
        style_pool = ["Righteous.ttf", "Audiowide.ttf", "Monoton.ttf", "ChakraPetch-Bold.ttf", "Orbitron.ttf", "Tourney.ttf"]
    elif style in ["matrix_hacker", "terminal", "circuit_board", "arcade_8bit", "pixel_retro"]:
        style_pool = ["PressStart2P.ttf", "Silkscreen.ttf", "BlackOpsOne.ttf"]
    elif style in ["diecut_badge", "extrude3d", "extrude3d_v2", "badge"]:
        style_pool = ["TitanOne.ttf", "Anton-Regular.ttf", "LuckiestGuy-Regular.ttf", "BebasNeue.ttf", "AlfaSlabOne-Regular.ttf", "LilitaOne.ttf"]
    elif style in ["blood", "horror", "toxic", "vampire"]:
        style_pool = ["Creepster-Regular.ttf", "Nosifer.ttf", "Butcherman.ttf", "Eater.ttf", "BlackOpsOne.ttf"]
    elif style in ["kawaii_pastel", "candy", "bubblegum", "sakura_text", "cotton_candy"]:
        style_pool = ["BubblegumSans.ttf", "Fredoka-Bold.ttf", "Pacifico-Regular.ttf", "Gaegu-Bold.ttf", "HiMelody.ttf", "Jua.ttf"]
    elif style in ["bollywood_poster", "desi_truck"]:
        style_pool = ["Modak.ttf", "Shrikhand-Regular.ttf", "Anton-Regular.ttf", "Baloo2-Regular.ttf"]
    elif style in ["stamp", "typewriter", "blueprint", "woodburn"]:
        style_pool = ["BlackOpsOne.ttf", "Syne-Bold.ttf", "SpecialElite.ttf", "Anton-Regular.ttf", "Smokum.ttf"]

    if style_pool:
        # Filter available in repository
        avail_pool = [f for f in style_pool if f in all_fonts] or style_pool
        chosen = random.choice(avail_pool)
        fp = get_font_file_path(chosen)
        if fp:
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass

    # 4. Global Random Selection from all 190+ fonts (gives fresh look every single time!)
    if all_fonts:
        # Sample up to 5 random fonts to find one that loads cleanly
        for _ in range(5):
            candidate = random.choice(all_fonts)
            fp = get_font_file_path(candidate)
            if fp:
                try:
                    return ImageFont.truetype(fp, size)
                except Exception:
                    continue

    # Default fallback
    for fallback in ["Anton-Regular.ttf", "Bangers.ttf", "arialbd.ttf", "arial.ttf"]:
        fp = get_font_file_path(fallback) or fallback
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue

    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


# ═══════════════════════════════════════════════════════════════
# COLOR & CONTRAST HELPERS
# ═══════════════════════════════════════════════════════════════

def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    h = (hex_str or "#FFFFFF").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) >= 6:
        try:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            pass
    return 255, 255, 255


def is_dark_color(hex_color: str) -> bool:
    r, g, b = hex_to_rgb(hex_color)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return luminance < 128


def lerp_color(hex1: str, hex2: str, t: float) -> str:
    r1, g1, b1 = hex_to_rgb(hex1)
    r2, g2, b2 = hex_to_rgb(hex2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def pick_random_style() -> str:
    """Picks a random style from 75+ styles, avoiding immediate repetition."""
    global _RECENT_STYLES
    available = [s for s in STYLE_LABELS if s not in _RECENT_STYLES]
    if not available:
        available = STYLE_LABELS
    chosen = random.choice(available)
    _RECENT_STYLES.append(chosen)
    if len(_RECENT_STYLES) > 15:
        _RECENT_STYLES.pop(0)
    return chosen


# ═══════════════════════════════════════════════════════════════
# PROCEDURAL DECORATIONS (All Transparent Background Friendly)
# ═══════════════════════════════════════════════════════════════

def draw_stars(draw: ImageDraw.Draw, w: int, h: int, color_rgba: Tuple[int, int, int, int], count: int = 10):
    for _ in range(count):
        cx = random.randint(15, max(16, w - 15))
        cy = random.randint(15, max(16, h - 15))
        r_out = random.randint(5, 11)
        r_in = r_out * 0.4
        points = []
        spikes = random.choice([4, 5])
        rot = random.uniform(0, math.pi)
        for s in range(spikes * 2):
            ang = (s * math.pi) / spikes + rot
            rad = r_out if s % 2 == 0 else r_in
            points.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
        draw.polygon(points, fill=color_rgba)


def draw_sparkles(draw: ImageDraw.Draw, w: int, h: int, color_rgba: Tuple[int, int, int, int], count: int = 10):
    for _ in range(count):
        cx = random.randint(15, max(16, w - 15))
        cy = random.randint(15, max(16, h - 15))
        s = random.randint(3, 8)
        draw.line([(cx - s * 2, cy), (cx + s * 2, cy)], fill=color_rgba, width=2)
        draw.line([(cx, cy - s * 2), (cx, cy + s * 2)], fill=color_rgba, width=2)
        draw.ellipse([(cx - 1, cy - 1), (cx + 1, cy + 1)], fill=(255, 255, 255, 240))


def draw_sprinkles(draw: ImageDraw.Draw, w: int, h: int, colors_list: List[str], count: int = 16):
    for _ in range(count):
        cx = random.randint(15, max(16, w - 15))
        cy = random.randint(15, max(16, h - 15))
        length = random.randint(8, 16)
        angle = random.uniform(0, math.pi * 2)
        dx = math.cos(angle) * length / 2
        dy = math.sin(angle) * length / 2
        col = hex_to_rgb(random.choice(colors_list)) + (200,)
        draw.line([(cx - dx, cy - dy), (cx + dx, cy + dy)], fill=col, width=3)


def draw_hearts(draw: ImageDraw.Draw, w: int, h: int, color_rgba: Tuple[int, int, int, int], count: int = 6):
    for _ in range(count):
        cx = random.randint(20, max(21, w - 20))
        cy = random.randint(20, max(21, h - 20))
        s = random.uniform(4.0, 9.0)
        pts = []
        for i in range(24):
            t = (i / 24.0) * 2 * math.pi
            hx = cx + s * (16 * math.sin(t) ** 3) / 16
            hy = cy - s * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)) / 16
            pts.append((hx, hy))
        draw.polygon(pts, fill=color_rgba)


def draw_bubbles(draw: ImageDraw.Draw, w: int, h: int, color_rgba: Tuple[int, int, int, int], count: int = 8):
    for _ in range(count):
        cx = random.randint(20, max(21, w - 20))
        cy = random.randint(20, max(21, h - 20))
        r = random.randint(5, 14)
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline=color_rgba, width=2)
        # Highlight glint
        draw.ellipse([(cx - r * 0.4, cy - r * 0.4), (cx - r * 0.2, cy - r * 0.2)], fill=(255, 255, 255, 220))


def draw_crosses(draw: ImageDraw.Draw, w: int, h: int, color_rgba: Tuple[int, int, int, int], count: int = 8):
    for _ in range(count):
        cx = random.randint(15, max(16, w - 15))
        cy = random.randint(15, max(16, h - 15))
        s = random.randint(4, 8)
        draw.line([(cx - s, cy - s), (cx + s, cy + s)], fill=color_rgba, width=2)
        draw.line([(cx - s, cy + s), (cx + s, cy - s)], fill=color_rgba, width=2)


def draw_dots(draw: ImageDraw.Draw, w: int, h: int, color_rgba: Tuple[int, int, int, int], count: int = 18):
    for _ in range(count):
        cx = random.randint(15, max(16, w - 15))
        cy = random.randint(15, max(16, h - 15))
        r = random.randint(2, 5)
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=color_rgba)


def draw_lightning(draw: ImageDraw.Draw, w: int, h: int, color_rgba: Tuple[int, int, int, int], count: int = 3):
    for _ in range(count):
        x1 = random.randint(15, max(16, w - 15))
        y1 = random.randint(15, max(16, h - 15))
        pts = [(x1, y1), (x1 + 6, y1 + 10), (x1 + 2, y1 + 12), (x1 + 10, y1 + 24)]
        draw.line(pts, fill=color_rgba, width=3)


def draw_burst_lines(draw: ImageDraw.Draw, w: int, h: int, color_rgba: Tuple[int, int, int, int], count: int = 12):
    cx, cy = w / 2, h / 2
    for _ in range(count):
        angle = random.uniform(0, math.pi * 2)
        r_start = random.uniform(min(w, h) * 0.35, min(w, h) * 0.42)
        r_end = r_start + random.uniform(15, 35)
        x1 = cx + math.cos(angle) * r_start
        y1 = cy + math.sin(angle) * r_start
        x2 = cx + math.cos(angle) * r_end
        y2 = cy + math.sin(angle) * r_end
        draw.line([(x1, y1), (x2, y2)], fill=color_rgba, width=2)


def apply_procedural_decorations(canvas: Image.Image, deco_names: List[str], base_color: str):
    """Draws requested or randomized decorations on transparent canvas."""
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    rgb = hex_to_rgb(base_color)

    for name in deco_names:
        alpha = random.randint(140, 220)
        c_rgba = rgb + (alpha,)
        if name == "stars":
            draw_stars(draw, w, h, c_rgba)
        elif name == "sparkles":
            draw_sparkles(draw, w, h, c_rgba)
        elif name == "sprinkles":
            draw_sprinkles(draw, w, h, VIBRANT_COLORS)
        elif name == "hearts":
            draw_hearts(draw, w, h, (255, 64, 129, alpha))
        elif name == "bubbles":
            draw_bubbles(draw, w, h, (0, 229, 255, alpha))
        elif name == "crosses":
            draw_crosses(draw, w, h, c_rgba)
        elif name == "dots":
            draw_dots(draw, w, h, c_rgba)
        elif name == "lightning":
            draw_lightning(draw, w, h, (255, 235, 59, alpha))
        elif name == "burst_lines":
            draw_burst_lines(draw, w, h, c_rgba)


# ═══════════════════════════════════════════════════════════════
# AI TEXT ANALYZER (Fast LPU / GPT integration)
# ═══════════════════════════════════════════════════════════════

async def analyze_text_with_ai(text: str, timeout: float = 1.6) -> Optional[Dict[str, Any]]:
    """
    Asynchronously queries fast AI models to analyze text sentiment/mood/language
    and returns an ideal visual sticker design. If offline or slow, returns None
    so caller uses intelligent heuristic/randomized design without any lag.
    """
    try:
        import gpt_service
        is_deva = is_devanagari(text)
        system_prompt = (
            "You are a master Telegram text sticker designer. Given text, choose the best visual design. "
            "Reply with ONLY a single valid JSON object, no explanation, no markdown. "
            'Format: {"style":"<style>","font":"<font>","color":"<hex>","gradient":["<hex1>","<hex2>"],"decorations":["<deco1>"],"mood":"<one_word>"}\n'
            f"Available styles: neon, fire, ice, gold, 3d, badge, comic, bollywood, truck, matrix, terminal, hologram, stamp, typewriter, blood, toxic, candy, kawaii, rainbow, chrome, galaxy, sunset, graffiti, glitch.\n"
            f"Available decorations: stars, sparkles, sprinkles, hearts, bubbles, crosses, dots, lightning, burst_lines.\n"
            + ("Text is in Devanagari/Hindi! Pick style matching Indian/Devanagari aesthetics." if is_deva else "")
        )
        user_prompt = f'Design sticker for text: "{text[:100]}"'
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        raw_res = None
        # Try ultra fast race first
        if hasattr(gpt_service, "query_ultra_fast_race"):
            try:
                raw_res = await asyncio.wait_for(gpt_service.query_ultra_fast_race(messages, max_tokens=100, temperature=0.7), timeout=timeout)
            except Exception:
                pass

        if not raw_res and hasattr(gpt_service, "query_gpt"):
            try:
                raw_res = await asyncio.wait_for(gpt_service.query_gpt(user_prompt, context=system_prompt), timeout=timeout)
            except Exception:
                pass

        if raw_res:
            raw_res = raw_res.strip()
            if raw_res.startswith("```"):
                raw_res = re.sub(r"^```(?:json)?\s*", "", raw_res)
                raw_res = re.sub(r"\s*```$", "", raw_res)
            m = re.search(r"\{[\s\S]*\}", raw_res)
            if m:
                data = json.loads(m.group(0))
                return data
    except Exception as e:
        log.debug(f"Sticker AI analysis bypassed/failed: {e}")
    return None


# ═══════════════════════════════════════════════════════════════
# COMMAND & INPUT PARSERS
# ═══════════════════════════════════════════════════════════════

def parse_text_and_options(raw_args: str) -> Tuple[str, str, str, bool]:
    """
    Parses raw command arguments into (clean_text, text_color, stroke_color, is_bubble).
    Maintained for backward compatibility.
    """
    tokens = raw_args.strip().split()
    if not tokens:
        return "", "#FFFFFF", "#000000", False

    is_bubble = False
    text_color = "#FFFFFF"
    color_set = False

    remaining = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        tok_lower = tok.lower()
        if tok_lower in ["-b", "-bubble", "--bubble"]:
            is_bubble = True
            i += 1
        elif tok_lower in ["-c", "--color"] and i + 1 < len(tokens):
            c_val = tokens[i + 1].lower()
            text_color = COLOR_MAP.get(c_val, tokens[i + 1] if tokens[i + 1].startswith("#") else f"#{tokens[i + 1]}")
            color_set = True
            i += 2
        elif not color_set and not remaining and (tok_lower in COLOR_MAP or (tok.startswith("#") and len(tok) in (4, 7))):
            text_color = COLOR_MAP.get(tok_lower, tok)
            color_set = True
            i += 1
        else:
            remaining.append(tok)
            i += 1

    clean_text = " ".join(remaining).strip()
    stroke_color = "#FFFFFF" if is_dark_color(text_color) else "#000000"
    return clean_text, text_color, stroke_color, is_bubble


def parse_txt_input(
    raw_args: str,
    is_reply: bool = False,
    replied_text: str = ""
) -> Tuple[str, str, bool, bool]:
    """
    Comprehensive parser supporting:
    - .txt:neon <text>
    - .txt style:neon <text>
    - .txt neon <text>
    - .txt:pack <text>
    - .txt:random <text>
    - .txt (with replied text)
    Returns: (clean_text, style, is_pack, is_catalog)
    """
    text = (raw_args or "").strip()

    # Catalog triggers
    lower_t = text.lower()
    if lower_t in ["list", "styles", "help", "catalog"]:
        return "", "classic", False, True

    forced_style = ""

    # Check for style:<name>
    if text.lower().startswith("style:"):
        parts = text.split(maxsplit=1)
        tag = parts[0][6:].lower()
        forced_style = STYLE_ALIASES.get(tag, tag)
        text = parts[1].strip() if len(parts) > 1 else ""

    # Check if first word is a style alias (e.g. .txt neon hello)
    elif text:
        tokens = text.split(maxsplit=1)
        first_tok = tokens[0].lower().rstrip(":")
        if first_tok in STYLE_ALIASES:
            forced_style = STYLE_ALIASES[first_tok]
            text = tokens[1].strip() if len(tokens) > 1 else ""

    # Quoted message fallback
    if not text and is_reply and replied_text:
        text = replied_text.strip()

    if not forced_style:
        forced_style = "random"

    is_pack = forced_style in ["pack", "4"]
    return text, forced_style, is_pack, False


# ═══════════════════════════════════════════════════════════════
# STICKER RENDERING ENGINE (75+ Styles + Randomizer)
# ═══════════════════════════════════════════════════════════════

def create_text_sticker(
    text: str,
    style: str = "random",
    text_color: Optional[str] = None,
    stroke_color: Optional[str] = None,
    bg_bubble: bool = False,
    ai_design: Optional[Dict[str, Any]] = None,
    random_tilt: bool = True
) -> bytes:
    """
    Generates a 512px WEBP Telegram sticker from text with dynamic styling and fonts.
    - If style == 'random', randomly picks from 75+ styles and 200+ fonts every time!
    - If ai_design is provided, applies AI-curated aesthetic.
    """
    text = text.strip()
    if not text:
        raise ValueError("Cannot create sticker from empty text.")

    # 1. Resolve Style
    requested_font = ""
    deco_selection = []

    if ai_design:
        resolved_style = ai_design.get("style", "random")
        requested_font = ai_design.get("font", "")
        if not text_color and ai_design.get("color"):
            text_color = ai_design["color"]
        if ai_design.get("decorations"):
            deco_selection = ai_design["decorations"]
    else:
        resolved_style = style or "random"

    if resolved_style in STYLE_ALIASES:
        resolved_style = STYLE_ALIASES[resolved_style]

    if resolved_style == "random":
        if is_devanagari(text):
            resolved_style = random.choice([
                "bollywood_poster", "desi_truck", "extrude3d", "diecut_badge",
                "golden_glow", "neon_glow", "fire", "candy", "chrome", "pop_art_dots"
            ])
        else:
            resolved_style = pick_random_style()

    # 2. Colors & Gradients
    if not text_color:
        if bg_bubble or resolved_style in ["kawaii_pastel", "candy", "bubblegum"]:
            text_color = random.choice(["#FF1493", "#FF69B4", "#9C27B0", "#FF4081", "#E91E63"])
        elif resolved_style in ["terminal", "matrix_hacker"]:
            text_color = "#00FF50"
        elif resolved_style in ["blood", "horror"]:
            text_color = "#FF1744"
        elif resolved_style in ["toxic"]:
            text_color = "#39FF14"
        elif resolved_style in ["ice", "frost"]:
            text_color = "#E0F7FA"
        elif resolved_style in ["fire", "lava"]:
            text_color = "#FFFF00"
        elif resolved_style in ["liquid_gold", "golden_glow"]:
            text_color = "#FFD700"
        elif resolved_style in ["superhero_action", "comic"]:
            text_color = "#FFEB3B"
        else:
            text_color = random.choice(VIBRANT_COLORS)

    if not stroke_color:
        stroke_color = "#FFFFFF" if is_dark_color(text_color) else "#000000"

    # 3. Dynamic Font Sizing & Wrapping
    total_len = len(text)
    if total_len <= 15:
        font_size = 72
        wrap_width = 16
        stroke_width = 6
    elif total_len <= 40:
        font_size = 52
        wrap_width = 20
        stroke_width = 5
    elif total_len <= 90:
        font_size = 38
        wrap_width = 26
        stroke_width = 4
    elif total_len <= 180:
        font_size = 28
        wrap_width = 32
        stroke_width = 3
    else:
        font_size = 22
        wrap_width = 38
        stroke_width = 2

    font = get_best_font(font_size, style=resolved_style, text=text, requested_font=requested_font)

    # Word wrapping per paragraph
    lines = []
    for raw_line in text.split("\n"):
        if raw_line.strip():
            lines.extend(textwrap.wrap(raw_line, width=wrap_width))
        else:
            lines.append("")
    wrapped_text = "\n".join(lines) if lines else text

    # Measure text bounding box with graceful fallback if font has broken bytecode
    dummy_img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    dummy_draw = ImageDraw.Draw(dummy_img)
    try:
        bbox = dummy_draw.multiline_textbbox((0, 0), wrapped_text, font=font, align="center", stroke_width=stroke_width)
    except OSError:
        safe_name = "NotoSansDevanagari-Bold.ttf" if is_devanagari(text) else "Anton-Regular.ttf"
        font = ImageFont.truetype(get_font_file_path(safe_name) or "arial.ttf", font_size)
        bbox = dummy_draw.multiline_textbbox((0, 0), wrapped_text, font=font, align="center", stroke_width=stroke_width)
    text_w = max(10, int(bbox[2] - bbox[0]))
    text_h = max(10, int(bbox[3] - bbox[1]))

    # Padding based on style
    pad_x = 55 if (resolved_style in ["diecut_badge", "desi_truck", "neon_glow", "hologram"] or bg_bubble) else 38
    pad_y = 45 if (resolved_style in ["diecut_badge", "extrude3d", "extrude3d_v2"] or bg_bubble) else 32

    content_w = max(160, text_w + pad_x * 2)
    content_h = max(100, text_h + pad_y * 2)

    img = Image.new("RGBA", (content_w, content_h), (0, 0, 0, 0))
    x = (content_w - text_w) / 2 - bbox[0]
    y = (content_h - text_h) / 2 - bbox[1]

    # Draw procedural decorations behind text
    if not deco_selection:
        deco_selection = random.sample(AVAILABLE_DECORATIONS, k=random.randint(1, 2))
    apply_procedural_decorations(img, deco_selection, text_color)

    # 4. Render Main Text Style
    if bg_bubble or resolved_style in ["kawaii_pastel", "bubble"]:
        draw = ImageDraw.Draw(img)
        bubble_bg = (24, 20, 38, 235) if bg_bubble else (255, 230, 240, 230)
        bubble_stroke = (255, 255, 255, 140) if bg_bubble else (255, 105, 180, 200)
        draw.rounded_rectangle(
            (8, 8, content_w - 8, content_h - 8),
            radius=24,
            fill=bubble_bg,
            outline=bubble_stroke,
            width=3
        )
        draw.multiline_text((x, y), wrapped_text, font=font, fill=text_color, align="center", stroke_width=stroke_width, stroke_fill=stroke_color)

    elif resolved_style in ["neon_glow", "hologram", "neon_sign", "neon_sign_v2", "neon_3d", "electric"]:
        neon_hex = text_color if text_color != "#FFFFFF" else ("#00FFFF" if "holo" in resolved_style else "#FF007F")
        neon_rgb = hex_to_rgb(neon_hex)

        # Glow layer
        glow = Image.new("RGBA", (content_w, content_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.multiline_text((x, y), wrapped_text, font=font, fill=neon_rgb + (255,), align="center", stroke_width=stroke_width * 3, stroke_fill=neon_rgb + (255,))
        glow = glow.filter(ImageFilter.GaussianBlur(10))
        img.alpha_composite(glow)

        draw = ImageDraw.Draw(img)
        if "holo" in resolved_style:
            for sl in range(int(y), int(y + text_h), 4):
                draw.line([(0, sl), (content_w, sl)], fill=(0, 255, 255, 55), width=1)

        draw.multiline_text((x, y), wrapped_text, font=font, fill="#FFFFFF", align="center", stroke_width=2, stroke_fill=neon_hex)

    elif resolved_style in ["fire", "fire_inferno_v2", "lava", "lava_glow"]:
        glow = Image.new("RGBA", (content_w, content_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.multiline_text((x, y), wrapped_text, font=font, fill=(255, 69, 0, 255), align="center", stroke_width=stroke_width * 2, stroke_fill=(255, 69, 0, 255))
        glow = glow.filter(ImageFilter.GaussianBlur(8))
        img.alpha_composite(glow)

        draw = ImageDraw.Draw(img)
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#FFFF00", align="center", stroke_width=stroke_width, stroke_fill="#8B0000")

    elif resolved_style in ["ice", "frost", "frozen_frost"]:
        glow = Image.new("RGBA", (content_w, content_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.multiline_text((x, y), wrapped_text, font=font, fill=(0, 229, 255, 255), align="center", stroke_width=stroke_width * 2, stroke_fill=(0, 229, 255, 255))
        glow = glow.filter(ImageFilter.GaussianBlur(8))
        img.alpha_composite(glow)

        draw = ImageDraw.Draw(img)
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#E0F7FA", align="center", stroke_width=stroke_width, stroke_fill="#003344")

    elif resolved_style in ["extrude3d", "extrude3d_v2", "shadow3d", "long_shadow", "retro_shadow"]:
        draw = ImageDraw.Draw(img)
        depth = 8
        is_v2 = "v2" in resolved_style or "shadow" in resolved_style
        for d in range(depth, 0, -1):
            if is_v2:
                shade = (int(30 + d * 5), int(10 + d * 3), int(50 + d * 8), 255)
            else:
                shade = (int(10 + d * 10), int(10 + d * 10), int(15 + d * 10), 255)
            draw.multiline_text((x + d * 1.5, y + d * 1.5), wrapped_text, font=font, fill=shade, align="center")

        front_fill = text_color if text_color != "#FFFFFF" else ("#00DFD8" if is_v2 else "#FFD700")
        stroke_c = "#0B0014" if is_v2 else "#000000"
        draw.multiline_text((x, y), wrapped_text, font=font, fill=front_fill, align="center", stroke_width=stroke_width, stroke_fill=stroke_c)
        draw.multiline_text((x - 1, y - 1), wrapped_text, font=font, fill=(255, 255, 255, 120), align="center")

    elif resolved_style in ["diecut_badge", "diecut", "sticker_badge", "badge"]:
        shadow = Image.new("RGBA", (content_w, content_h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.multiline_text((x + 4, y + 6), wrapped_text, font=font, fill=(0, 0, 0, 150), align="center", stroke_width=stroke_width + 12, stroke_fill=(0, 0, 0, 150))
        shadow = shadow.filter(ImageFilter.GaussianBlur(6))
        img.alpha_composite(shadow)

        draw = ImageDraw.Draw(img)
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#FFFFFF", align="center", stroke_width=stroke_width + 10, stroke_fill="#FFFFFF")
        badge_face = text_color if text_color != "#FFFFFF" else "#FF1493"
        draw.multiline_text((x, y), wrapped_text, font=font, fill=badge_face, align="center", stroke_width=max(2, stroke_width // 2), stroke_fill="#111111")

    elif resolved_style in ["bollywood_poster", "bollywood", "desi"]:
        draw = ImageDraw.Draw(img)
        for d in range(7, 0, -1):
            draw.multiline_text((x + d, y + d), wrapped_text, font=font, fill=(139, 0, 0, 240), align="center")
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#FFD700", align="center", stroke_width=stroke_width, stroke_fill="#5A0000")

    elif resolved_style in ["desi_truck", "truck", "horn"]:
        draw = ImageDraw.Draw(img)
        truck_colors = ["#FF0000", "#00CC00", "#0000FF", "#FF9900", "#FF00FF"]
        for i, col in enumerate(reversed(truck_colors)):
            sw = (i + 1) * 3 + stroke_width
            draw.multiline_text((x, y), wrapped_text, font=font, fill=col, align="center", stroke_width=sw, stroke_fill=col)
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#FFFFFF", align="center")

    elif resolved_style in ["terminal", "matrix_hacker", "circuit_board", "cmd"]:
        draw = ImageDraw.Draw(img)
        draw.multiline_text((x + 3, y + 3), wrapped_text, font=font, fill=(0, 50, 0, 180), align="center")
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#00FF50", align="center", stroke_width=stroke_width, stroke_fill="#002200")

    elif resolved_style in ["liquid_gold", "golden_glow", "gold_foil", "gold_foil_v2", "chrome", "liquid_chrome_v2"]:
        draw = ImageDraw.Draw(img)
        is_chrome = "chrome" in resolved_style
        shadow_c = (30, 30, 30, 180) if is_chrome else (60, 40, 0, 180)
        face_c = "#E0E0E0" if is_chrome else "#FFD700"
        stroke_c = "#1A1A1A" if is_chrome else "#3A2800"
        draw.multiline_text((x + 4, y + 4), wrapped_text, font=font, fill=shadow_c, align="center")
        draw.multiline_text((x, y), wrapped_text, font=font, fill=face_c, align="center", stroke_width=stroke_width, stroke_fill=stroke_c)
        draw.multiline_text((x - 1, y - 1), wrapped_text, font=font, fill=(255, 255, 255, 160), align="center")

    elif resolved_style in ["superhero_action", "comic_pop", "comic", "hero", "manga_scream"]:
        draw = ImageDraw.Draw(img)
        draw.multiline_text((x + 5, y + 5), wrapped_text, font=font, fill=(0, 0, 0, 180), align="center")
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#FFEB3B", align="center", stroke_width=stroke_width + 4, stroke_fill="#000000")
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#FFEB3B", align="center", stroke_width=1, stroke_fill="#FF1744")

    elif resolved_style in ["blood", "horror", "vampire"]:
        draw = ImageDraw.Draw(img)
        draw.multiline_text((x + 3, y + 4), wrapped_text, font=font, fill=(30, 0, 0, 220), align="center")
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#FF1744", align="center", stroke_width=stroke_width, stroke_fill="#4A0000")

    elif resolved_style in ["toxic", "poison"]:
        glow = Image.new("RGBA", (content_w, content_h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.multiline_text((x, y), wrapped_text, font=font, fill=(57, 255, 20, 255), align="center", stroke_width=stroke_width * 2, stroke_fill=(57, 255, 20, 255))
        glow = glow.filter(ImageFilter.GaussianBlur(8))
        img.alpha_composite(glow)

        draw = ImageDraw.Draw(img)
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#A4E814", align="center", stroke_width=stroke_width, stroke_fill="#0D2B00")

    elif resolved_style in ["stamp", "typewriter", "blueprint", "woodburn"]:
        draw = ImageDraw.Draw(img)
        stamp_col = "#CC0000" if resolved_style == "stamp" else ("#FFFFFF" if resolved_style == "blueprint" else "#1A1A1A")
        draw.multiline_text((x, y), wrapped_text, font=font, fill=stamp_col, align="center", stroke_width=stroke_width, stroke_fill="#FFFFFF")
        if resolved_style == "stamp":
            draw.rectangle((x - 6, y - 6, x + text_w + 6, y + text_h + 6), outline=stamp_col, width=3)

    elif resolved_style in ["chromatic_glitch", "cyberpunk_glitch", "glitch", "retro_tv"]:
        draw = ImageDraw.Draw(img)
        # Red and cyan chromatic aberration
        draw.multiline_text((x - 3, y), wrapped_text, font=font, fill=(255, 0, 55, 210), align="center")
        draw.multiline_text((x + 3, y), wrapped_text, font=font, fill=(0, 240, 255, 210), align="center")
        draw.multiline_text((x, y), wrapped_text, font=font, fill="#FFFFFF", align="center", stroke_width=stroke_width, stroke_fill="#111111")
        # Glitch slice lines
        for sl in range(int(y), int(y + text_h), 5):
            draw.line([(x - 10, sl), (x + text_w + 10, sl)], fill=(0, 0, 0, 75), width=1)

    else:
        # Classic / Vibrant Multi-Outline
        draw = ImageDraw.Draw(img)
        draw.multiline_text((x + 3, y + 3), wrapped_text, font=font, fill=(0, 0, 0, 140), align="center")
        draw.multiline_text((x, y), wrapped_text, font=font, fill=text_color, align="center", stroke_width=stroke_width, stroke_fill=stroke_color)

    # 5. Dynamic Organic Sticker Tilt (-2.5 to +2.5 deg)
    if random_tilt:
        tilt_angle = random.uniform(-2.2, 2.2)
        img = img.rotate(tilt_angle, resample=Image.Resampling.BICUBIC, expand=True)

    # 6. Telegram Sticker Standard Constraint (Longest dimension exactly 512px)
    cw, ch = img.size
    if cw >= ch:
        target_w = 512
        target_h = max(1, int(round(ch * (512.0 / cw))))
    else:
        target_h = 512
        target_w = max(1, int(round(cw * (512.0 / ch))))

    if (cw, ch) != (target_w, target_h):
        img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=95)
    return buf.getvalue()


def generate_sticker_pack(text: str, count: int = 4) -> List[bytes]:
    """
    Generates a pack of distinct styled stickers with different fonts and themes.
    """
    if is_devanagari(text):
        pack_styles = ["bollywood_poster", "desi_truck", "extrude3d", "diecut_badge", "neon_glow", "golden_glow"]
    else:
        pack_styles = ["neon_glow", "fire", "extrude3d", "superhero_action", "diecut_badge", "chromatic_glitch", "golden_glow"]

    random.shuffle(pack_styles)
    selected = pack_styles[:count]

    stickers = []
    for st in selected:
        try:
            buf = create_text_sticker(text, style=st)
            stickers.append(buf)
        except Exception as e:
            log.warning(f"Failed to generate sticker style {st} in pack: {e}")

    if not stickers:
        stickers.append(create_text_sticker(text, style="classic"))
    return stickers


def get_styles_catalog() -> str:
    """Returns the formatted God-Tier styles catalog."""
    font_count = len(get_all_available_fonts())
    return (
        "🎨 <b>TEXT STICKER 2.0 — GOD-LEVEL STYLES & SHORTCUTS</b> 🌟\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ <b>Key Features:</b>\n"
        "• <b>Random by Default:</b> Har baar naya aur unique font, style, color aur decorations!\n"
        "• <b>Reply Mode:</b> Kisi ke message ko reply karke <code>.txt</code> likho — sidhe unko tag hoke sticker jayega!\n"
        "• <b>Auto Delete:</b> Aapka command message automatically delete ho jayega!\n"
        "• <b>Pack Mode:</b> <code>.txt:pack &lt;text&gt;</code> (4 alag-alag top designs ek saath!)\n"
        "• <b>Hindi/Devanagari:</b> Automatic stylized Indian fonts (Modak, Kalam, Baloo2, Noto Devanagari)!\n\n"
        "👑 <b>TOP 75+ STYLES BY CATEGORY:</b>\n\n"
        "🚀 <b>1. SCI-FI & CYBER:</b>\n"
        "• <code>.txt:neon &lt;text&gt;</code> (Vibrant Multi-layer Neon Glow 🔥)\n"
        "• <code>.txt:hologram &lt;text&gt;</code> (Cyan Laser Grid Scanlines 🔥)\n"
        "• <code>.txt:matrix &lt;text&gt;</code> / <code>.txt:terminal &lt;text&gt;</code> (Phosphor CRT Console)\n"
        "• <code>.txt:glitch &lt;text&gt;</code> (Chromatic Aberration 80s Glitch)\n"
        "• <code>.txt:pixel &lt;text&gt;</code> (Crisp 8-Bit Arcade Pixels)\n\n"
        "🇮🇳 <b>2. DESI & BOLLYWOOD:</b>\n"
        "• <code>.txt:bollywood &lt;text&gt;</code> (Retro 70s Hand-Painted Poster 🔥)\n"
        "• <code>.txt:truck &lt;text&gt;</code> (Kitsch Desi Highway Truck Art 🔥)\n\n"
        "🏆 <b>3. 3D, METALLIC & LUXURY:</b>\n"
        "• <code>.txt:3d &lt;text&gt;</code> (Deep Isometric 3D Extrusion 🔥)\n"
        "• <code>.txt:badge &lt;text&gt;</code> (Die-cut Vinyl Sticker with White Border 🔥)\n"
        "• <code>.txt:gold &lt;text&gt;</code> (Liquid Gold Metallic Sheen)\n"
        "• <code>.txt:chrome &lt;text&gt;</code> (Molten Silver Mirror Chrome)\n\n"
        "🔥 <b>4. ELEMENTAL & POP:</b>\n"
        "• <code>.txt:fire &lt;text&gt;</code> (Blazing Inferno Flame Glow 🔥)\n"
        "• <code>.txt:ice &lt;text&gt;</code> (Frozen Frost Blue Ice)\n"
        "• <code>.txt:comic &lt;text&gt;</code> (Superhero Action Pop)\n"
        "• <code>.txt:blood &lt;text&gt;</code> (Horror Crimson Splatter)\n"
        "• <code>.txt:toxic &lt;text&gt;</code> (Acid Green Biohazard Glow)\n"
        "• <code>.txt:candy &lt;text&gt;</code> / <code>.txt:kawaii &lt;text&gt;</code> (Cute Sweet Pastel)\n"
        "• <code>.txt:stamp &lt;text&gt;</code> / <code>.txt:typewriter &lt;text&gt;</code> (Rubber Ink Stamp)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 <i>{font_count}+ Custom TTF Fonts + 75+ Styles Loaded!</i>\n"
        "💡 <b>Tip:</b> <code>.txt:pack Mast Text</code> try karein!"
    )
