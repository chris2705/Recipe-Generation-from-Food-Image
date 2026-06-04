"""
Video Recipe Generator Service
================================
Orchestrates the full pipeline:
  1. Gemini → per-step narration script
  2. edge-tts (via TTSService) → per-step MP3 audio
  3. Pillow → per-step 1080×1920 slide PNG images
  4. MoviePy → assembled MP4 with Ken Burns zoom + cross-fade transitions

All heavy lifting happens inside generate_video(), which is designed to be
called from a background thread.  A progress_cb(stage, pct) callback lets the
caller stream real-time progress back to the browser via SSE.

Font strategy
-------------
Every text element belongs to exactly one *role*:

  "ui"    – branding, step numbers, badges, labels (always Latin/NotoSans)
  "title" – recipe name header (always Latin/NotoSans; food names stay English)
  "body"  – per-step instruction text (language-specific Noto Sans)

Emoji / icon strategy
---------------------
Emoji are NOT rendered via ImageDraw.text().  Instead every icon position is
drawn using pure Pillow geometric primitives (circles, polygons, lines).
This guarantees pixel-perfect rendering on every platform with zero font
dependency.
"""

import os
import json
import math
import logging
import traceback
from pathlib import Path
from typing import List, Optional, Callable

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SLIDE_W, SLIDE_H = 1080, 1920          # Portrait / Reels-ready
FPS = 30
MIN_SLIDE_DURATION = 3.5               # seconds – minimum per slide
TRANSITION_DURATION = 0.4              # cross-dissolve seconds

# Colour palette (matches app branding)
C_BG_TOP    = (15, 15, 35)            # near-black navy
C_BG_BOT    = (30, 10, 10)            # near-black reddish
C_ACCENT    = (255, 107, 107)         # --primary-color
C_ACCENT2   = (255, 154, 158)
C_WHITE     = (255, 255, 255)
C_LIGHT     = (220, 220, 235)
C_DIM       = (160, 160, 190)
C_BADGE_BG  = (255, 107, 107)

# ===========================================================================
# FONT SYSTEM
# ===========================================================================

# Directory where project-bundled fonts live
_BUNDLED_FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static", "fonts"
)
# Windows system font directory (fallback)
_SYS_FONT_DIR = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Fonts")

# ---------------------------------------------------------------------------
# Language → body font mapping
# Bold variants fall back to Regular where a separate Bold file is unavailable
# ---------------------------------------------------------------------------
_BODY_FONTS: dict = {
    "malayalam": ("NotoSansMalayalam-Regular.ttf", "NotoSansMalayalam-Bold.ttf"),
    "hindi":     ("NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari-Regular.ttf"),
    "tamil":     ("NotoSansTamil-Regular.ttf",      "NotoSansTamil-Regular.ttf"),
    "telugu":    ("NotoSansTelugu-Regular.ttf",     "NotoSansTelugu-Regular.ttf"),
    "kannada":   ("NotoSansKannada-Regular.ttf",    "NotoSansKannada-Regular.ttf"),
    # English / Latin default
    "default":   ("NotoSans-Regular.ttf",           "NotoSans-Bold.ttf"),
}

# UI / Title fonts — ALWAYS Latin NotoSans regardless of language
_UI_FONT_REG  = "NotoSans-Regular.ttf"
_UI_FONT_BOLD = "NotoSans-Bold.ttf"
# Ultimate system-font fallbacks (in order of preference)
_SYS_FONT_FALLBACKS = ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
                        "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]

# Languages whose scripts lack reliable word-space boundaries → char-wrap
_CHAR_WRAP_LANGS = {"malayalam", "tamil", "telugu", "kannada"}

# In-memory font cache
_font_cache: dict = {}


def _resolve_font_path(filename: str) -> Optional[str]:
    """Return absolute path of a font file (bundled dir → system dir)."""
    for directory in (_BUNDLED_FONT_DIR, _SYS_FONT_DIR):
        path = os.path.join(directory, filename)
        if os.path.exists(path):
            return path
    return None


def get_font(role: str, size: int, language: str = "english", bold: bool = False) -> ImageFont.ImageFont:
    """
    Centralised font loader.

    Parameters
    ----------
    role     : "ui"    → always Latin/NotoSans (step numbers, badges, labels)
               "title" → always Latin/NotoSans (recipe name — stays in English)
               "body"  → language-specific Noto Sans (instruction text)
    size     : point size
    language : target language key (only meaningful for role="body")
    bold     : request bold weight

    Returns
    -------
    PIL ImageFont object, guaranteed non-None (falls back to Pillow default).
    """
    lang_key = language.lower().strip()
    cache_key = (role, lang_key, size, bold)
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    candidates: List[str] = []

    if role in ("ui", "title"):
        # Always use Latin NotoSans — never a script-specific font
        candidates = [_UI_FONT_BOLD if bold else _UI_FONT_REG,
                      _UI_FONT_REG] + _SYS_FONT_FALLBACKS
    else:
        # role == "body": language-specific first, then Latin fallback
        reg_name, bld_name = _BODY_FONTS.get(lang_key, _BODY_FONTS["default"])
        def_reg, def_bld   = _BODY_FONTS["default"]
        if bold:
            candidates = [bld_name, reg_name, def_bld, def_reg] + _SYS_FONT_FALLBACKS
        else:
            candidates = [reg_name, def_reg] + _SYS_FONT_FALLBACKS

    for fname in candidates:
        path = _resolve_font_path(fname)
        if path:
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[cache_key] = font
                logger.debug(f"Font loaded: role={role} lang={lang_key} size={size} → {fname}")
                return font
            except Exception:
                continue

    # Absolute last resort: Pillow built-in bitmap font
    try:
        font = ImageFont.load_default(size=size)
    except Exception:
        font = ImageFont.load_default()
    _font_cache[cache_key] = font
    return font


# ===========================================================================
# TEXT HELPERS
# ===========================================================================

def _draw_wrapped_text(
    draw: ImageDraw.Draw,
    text: str,
    font: ImageFont.ImageFont,
    x_center: int,
    y_start: int,
    max_width: int,
    fill,
    line_spacing: int = 10,
    language: str = "english",
) -> int:
    """
    Draw centred, auto-wrapped text.

    Indic scripts without reliable word spaces (Malayalam, Tamil, Telugu,
    Kannada) use character-level greedy wrapping.  All others use word-level.

    Returns the y coordinate immediately after the last rendered line.
    """
    lang_key = language.lower().strip()
    use_char_wrap = lang_key in _CHAR_WRAP_LANGS

    lines: List[str] = []

    if use_char_wrap:
        current = ""
        for char in text:
            test = current + char
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(current)
                current = char
            else:
                current = test
        if current:
            lines.append(current)
    else:
        words = text.split()
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(current)
                current = word
            else:
                current = test
        if current:
            lines.append(current)

    y = y_start
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        draw.text((x_center - lw // 2, y), line, font=font, fill=fill)
        y += bbox[3] - bbox[1] + line_spacing
    return y


# ===========================================================================
# ICON DRAWING (pure Pillow – zero emoji / font dependency)
# ===========================================================================

# Icon themes cycle per step number (0-indexed)
# Each entry is a short Latin label drawn beneath the geometric shape
_STEP_ICON_LABELS = [
    "PREP", "HEAT", "MIX",  "SEASON", "PLATE",
    "TIME", "COOK", "CHEF", "SERVE",  "DONE",
]


def _draw_step_icon(draw: ImageDraw.Draw, cx: int, cy: int,
                    step_idx: int, size: int = 140) -> None:
    """
    Draw a pure-Pillow step icon centred at (cx, cy).

    Cycles through 10 distinct geometric designs.  No emoji, no font glyphs,
    no Unicode — guaranteed to render identically on every OS.

    Parameters
    ----------
    draw     : active ImageDraw.Draw for the slide
    cx, cy   : centre coordinate of the icon region
    step_idx : 0-based step index (used to select icon design)
    size     : bounding box half-size (icon fits inside a square of 2×size)
    """
    theme = step_idx % len(_STEP_ICON_LABELS)
    r = size                 # outer radius / half-size
    r2 = int(r * 0.55)      # inner element radius

    if theme == 0:
        # PREP – circle with crosshair
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)],
                     outline=C_ACCENT, width=8)
        draw.line([(cx - r2, cy), (cx + r2, cy)], fill=C_ACCENT, width=6)
        draw.line([(cx, cy - r2), (cx, cy + r2)], fill=C_ACCENT, width=6)

    elif theme == 1:
        # HEAT – three upward flame arcs
        for dx in (-30, 0, 30):
            arc_box = [(cx + dx - 20, cy - r + 10),
                       (cx + dx + 20, cy + r2 - 10)]
            draw.arc(arc_box, start=200, end=340, fill=C_ACCENT, width=8)

    elif theme == 2:
        # MIX – rotating arrows (two arcs)
        draw.arc([(cx - r, cy - r2), (cx, cy + r2)],
                 start=270, end=90, fill=C_ACCENT, width=8)
        draw.arc([(cx, cy - r2), (cx + r, cy + r2)],
                 start=90, end=270, fill=C_ACCENT2, width=8)

    elif theme == 3:
        # SEASON – sprinkle dots grid
        for gx in range(-1, 2):
            for gy in range(-1, 2):
                dot_cx = cx + gx * 36
                dot_cy = cy + gy * 36
                draw.ellipse([(dot_cx - 10, dot_cy - 10),
                              (dot_cx + 10, dot_cy + 10)],
                             fill=C_ACCENT)

    elif theme == 4:
        # PLATE – concentric circles
        for ri in [r, r2, int(r2 * 0.5)]:
            draw.ellipse([(cx - ri, cy - ri), (cx + ri, cy + ri)],
                         outline=C_ACCENT, width=5)

    elif theme == 5:
        # TIME – clock face with hands
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)],
                     outline=C_ACCENT, width=8)
        # hour hand  (pointing to ~10 o'clock)
        draw.line([(cx, cy),
                   (cx + int(r2 * 0.6 * math.cos(math.radians(120))),
                    cy - int(r2 * 0.6 * math.sin(math.radians(120))))],
                  fill=C_ACCENT, width=7)
        # minute hand (pointing to ~12 o'clock)
        draw.line([(cx, cy), (cx, cy - r2)], fill=C_ACCENT2, width=5)
        # centre dot
        draw.ellipse([(cx - 8, cy - 8), (cx + 8, cy + 8)], fill=C_ACCENT)

    elif theme == 6:
        # COOK – stylised pan
        # pan body (filled ellipse)
        draw.ellipse([(cx - r, cy - r2 // 2), (cx + r, cy + r2 // 2)],
                     outline=C_ACCENT, width=8)
        # handle
        draw.line([(cx + r, cy), (cx + r + 50, cy - 25)],
                  fill=C_ACCENT, width=10)
        # steam lines
        for dx in (-30, 0, 30):
            steam_x = cx + dx
            draw.arc([(steam_x - 15, cy - r - 55),
                      (steam_x + 15, cy - r - 10)],
                     start=0, end=180, fill=C_ACCENT2, width=5)

    elif theme == 7:
        # CHEF – simple chef hat silhouette
        hat_w, hat_h = r, int(r * 1.2)
        brim_y = cy + r2 // 2
        # brim rectangle
        draw.rounded_rectangle(
            [(cx - hat_w, brim_y - 18), (cx + hat_w, brim_y + 18)],
            radius=12, fill=C_ACCENT,
        )
        # puff (top bulge)
        draw.ellipse([(cx - hat_w + 10, brim_y - hat_h),
                      (cx + hat_w - 10, brim_y)],
                     fill=C_ACCENT)
        # body
        draw.rectangle([(cx - hat_w + 10, brim_y - hat_h + 40),
                        (cx + hat_w - 10, brim_y - 18)],
                       fill=C_ACCENT)

    elif theme == 8:
        # SERVE – plate with dome lid
        draw.ellipse([(cx - r, cy + r // 4), (cx + r, cy + r)],
                     outline=C_ACCENT, width=8)   # plate
        draw.arc([(cx - r + 10, cy - r + 10), (cx + r - 10, cy + r // 2)],
                 start=180, end=0, fill=C_ACCENT, width=8)   # dome
        draw.line([(cx - r, cy + r // 4), (cx + r, cy + r // 4)],
                  fill=C_ACCENT, width=8)   # rim line

    else:
        # DONE – bold tick mark
        pts = [
            (cx - int(r * 0.55), cy),
            (cx - int(r * 0.15), cy + int(r * 0.45)),
            (cx + int(r * 0.55), cy - int(r * 0.45)),
        ]
        draw.line(pts, fill=C_ACCENT, width=16, joint="curve")


def _draw_chef_hat(draw: ImageDraw.Draw, cx: int, cy: int, size: int = 160) -> None:
    """
    Draw a filled chef hat centred at (cx, cy).
    Used on the intro slide as a visual anchor.
    """
    r = size
    brim_y = cy + r // 3

    # Brim
    draw.rounded_rectangle(
        [(cx - r, brim_y), (cx + r, brim_y + int(r * 0.35))],
        radius=14, fill=C_ACCENT,
    )
    # Puff
    draw.ellipse(
        [(cx - int(r * 0.8), brim_y - int(r * 0.9)),
         (cx + int(r * 0.8), brim_y + 10)],
        fill=C_WHITE,
    )
    # Body (rectangle connecting puff to brim)
    draw.rectangle(
        [(cx - int(r * 0.75), brim_y - int(r * 0.5)),
         (cx + int(r * 0.75), brim_y)],
        fill=C_WHITE,
    )
    # Brim stripe on top of white
    draw.rectangle(
        [(cx - r, brim_y), (cx + r, brim_y + 12)],
        fill=C_ACCENT,
    )
    # Centre button dot
    draw.ellipse(
        [(cx - 14, brim_y - 14), (cx + 14, brim_y + 14)],
        fill=C_ACCENT,
    )


def _draw_celebration(draw: ImageDraw.Draw, cx: int, cy: int, size: int = 180) -> None:
    """
    Draw a celebration burst (star / radial lines) for the outro slide.
    Replaces the 🎉 emoji.
    """
    r_outer = size
    r_inner = int(size * 0.45)
    n_points = 12

    for i in range(n_points):
        angle_deg = i * (360 / n_points)
        angle_rad = math.radians(angle_deg)
        # alternating long / short spokes
        r = r_outer if i % 2 == 0 else r_inner
        x_end = cx + int(r * math.cos(angle_rad))
        y_end = cy + int(r * math.sin(angle_rad))
        colour = C_ACCENT if i % 3 == 0 else C_ACCENT2
        draw.line([(cx, cy), (x_end, y_end)], fill=colour, width=9)

    # Centre circle
    draw.ellipse([(cx - 28, cy - 28), (cx + 28, cy + 28)], fill=C_WHITE)


# ===========================================================================
# GRADIENT BACKGROUND
# ===========================================================================

def _make_gradient_bg(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        r = int(C_BG_TOP[0] * (1 - t) + C_BG_BOT[0] * t)
        g = int(C_BG_TOP[1] * (1 - t) + C_BG_BOT[1] * t)
        b = int(C_BG_TOP[2] * (1 - t) + C_BG_BOT[2] * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return img


# ===========================================================================
# SLIDE GENERATOR
# ===========================================================================

class SlideGenerator:
    """Creates 1080×1920 PNG slides using Pillow.

    Font roles used
    ---------------
    "ui"    → get_font("ui", …)    – branding, step numbers, labels
    "title" → get_font("title", …) – recipe name (always English/Latin)
    "body"  → get_font("body", …, language=<lang>) – instruction text
    """

    # ------------------------------------------------------------------
    # Intro slide
    # ------------------------------------------------------------------
    def make_intro_slide(
        self,
        food_name: str,
        ingredients: List[str],
        language: str,
        output_path: str,
    ) -> str:
        """Generate an intro / title slide."""
        img  = _make_gradient_bg(SLIDE_W, SLIDE_H)
        draw = ImageDraw.Draw(img)

        # ── Top accent bar ──────────────────────────────────────────────
        draw.rectangle([(0, 0), (SLIDE_W, 12)], fill=C_ACCENT)

        # ── Branding badge ──────────────────────────────────────────────
        f_logo = get_font("ui", 36, bold=True)
        draw.text((SLIDE_W // 2, 78), "AI RECIPE", font=f_logo,
                  fill=C_ACCENT, anchor="mm")

        # ── Chef hat icon (pure Pillow, no emoji) ───────────────────────
        _draw_chef_hat(draw, SLIDE_W // 2, SLIDE_H // 2 - 220, size=140)

        # ── Recipe title (ALWAYS rendered in English/NotoSans) ──────────
        # Food names are English; we use the "title" role to guarantee
        # Latin glyphs even when language="malayalam" etc.
        f_title = get_font("title", 82, bold=True)
        _draw_wrapped_text(
            draw, food_name.upper(), f_title,
            SLIDE_W // 2, SLIDE_H // 2 + 20,
            SLIDE_W - 120, C_WHITE, line_spacing=14,
            language="english",            # food name is always Latin
        )

        # ── Ingredient count (always English) ───────────────────────────
        f_sub = get_font("ui", 48)
        ing_text = f"{len(ingredients)} ingredients  \u2022  Tap to start"
        _draw_wrapped_text(
            draw, ing_text, f_sub,
            SLIDE_W // 2, SLIDE_H // 2 + 220,
            SLIDE_W - 200, C_DIM, language="english",
        )

        # ── Language badge ───────────────────────────────────────────────
        f_lang = get_font("ui", 38)
        lang_label = f"[ {language.capitalize()} ]"
        _draw_wrapped_text(
            draw, lang_label, f_lang,
            SLIDE_W // 2, SLIDE_H - 210, SLIDE_W - 200,
            C_ACCENT, language="english",
        )

        # ── Bottom bar ───────────────────────────────────────────────────
        draw.rectangle([(0, SLIDE_H - 12), (SLIDE_W, SLIDE_H)], fill=C_ACCENT)

        img.save(output_path, "PNG", optimize=True)
        return output_path

    # ------------------------------------------------------------------
    # Step slide
    # ------------------------------------------------------------------
    def make_step_slide(
        self,
        step_number: int,
        total_steps: int,
        step_text: str,
        food_name: str,
        output_path: str,
        language: str = "english",
    ) -> str:
        """Generate a single cooking-step slide."""
        img  = _make_gradient_bg(SLIDE_W, SLIDE_H)
        draw = ImageDraw.Draw(img)

        # ── Top accent bar ──────────────────────────────────────────────
        draw.rectangle([(0, 0), (SLIDE_W, 12)], fill=C_ACCENT)

        # ── Recipe name header (ALWAYS Latin/NotoSans – "title" role) ───
        # food_name is the original English recipe name passed in.
        # Rendered with "title" role → NotoSans Latin → no boxes ever.
        f_title = get_font("title", 40, bold=True)
        draw.text((SLIDE_W // 2, 68), food_name.title(), font=f_title,
                  fill=C_ACCENT2, anchor="mm")

        # ── Progress dots ───────────────────────────────────────────────
        dot_r = 12
        dot_spacing = 34
        total_width = total_steps * dot_spacing
        dot_x_start = (SLIDE_W - total_width) // 2
        for i in range(total_steps):
            cx = dot_x_start + i * dot_spacing + dot_r
            cy = 138
            colour = C_ACCENT if i < step_number else C_DIM
            draw.ellipse([(cx - dot_r, cy - dot_r), (cx + dot_r, cy + dot_r)],
                         fill=colour)

        # ── Step badge (number circle) ──────────────────────────────────
        badge_cx, badge_cy = SLIDE_W // 2, 340
        badge_r = 100
        draw.ellipse(
            [(badge_cx - badge_r, badge_cy - badge_r),
             (badge_cx + badge_r, badge_cy + badge_r)],
            fill=C_BADGE_BG,
        )
        f_num = get_font("ui", 110, bold=True)   # "ui" role – always Latin digit
        draw.text((badge_cx, badge_cy), str(step_number),
                  font=f_num, fill=C_WHITE, anchor="mm")

        # ── "STEP X of Y" label ─────────────────────────────────────────
        f_label = get_font("ui", 42)
        draw.text((SLIDE_W // 2, 482),
                  f"STEP  {step_number}  of  {total_steps}",
                  font=f_label, fill=C_DIM, anchor="mm")

        # ── Step icon (pure Pillow geometry – zero emoji dependency) ────
        _draw_step_icon(draw, SLIDE_W // 2, 680, step_idx=step_number - 1, size=120)

        # ── Icon label (short English tag below the icon) ───────────────
        label = _STEP_ICON_LABELS[(step_number - 1) % len(_STEP_ICON_LABELS)]
        f_icon_label = get_font("ui", 32)
        draw.text((SLIDE_W // 2, 780), label, font=f_icon_label,
                  fill=C_DIM, anchor="mm")

        # ── Separator line ──────────────────────────────────────────────
        pad = 80
        draw.rectangle([(pad, 820), (SLIDE_W - pad, 826)], fill=C_ACCENT)

        # ── Step instruction (language-specific body font) ───────────────
        f_body = get_font("body", 54, language=language)
        _draw_wrapped_text(
            draw, step_text, f_body,
            SLIDE_W // 2, 870,
            SLIDE_W - 160, C_WHITE,
            line_spacing=20,
            language=language,
        )

        # ── Bottom bar ───────────────────────────────────────────────────
        draw.rectangle([(0, SLIDE_H - 12), (SLIDE_W, SLIDE_H)], fill=C_ACCENT)

        img.save(output_path, "PNG", optimize=True)
        return output_path

    # ------------------------------------------------------------------
    # Outro slide
    # ------------------------------------------------------------------
    def make_outro_slide(
        self,
        food_name: str,
        output_path: str,
    ) -> str:
        """Generate a final 'Enjoy!' slide."""
        img  = _make_gradient_bg(SLIDE_W, SLIDE_H)
        draw = ImageDraw.Draw(img)

        draw.rectangle([(0, 0), (SLIDE_W, 12)], fill=C_ACCENT)

        # ── Celebration burst (pure Pillow – replaces 🎉) ───────────────
        _draw_celebration(draw, SLIDE_W // 2, SLIDE_H // 2 - 200, size=170)

        # ── "Enjoy!" heading ─────────────────────────────────────────────
        f_enjoy = get_font("title", 100, bold=True)
        draw.text((SLIDE_W // 2, SLIDE_H // 2 + 60), "Enjoy!",
                  font=f_enjoy, fill=C_ACCENT, anchor="mm")

        # ── Food name subtitle ───────────────────────────────────────────
        f_sub = get_font("title", 52)
        _draw_wrapped_text(
            draw, f"Your {food_name} is ready.", f_sub,
            SLIDE_W // 2, SLIDE_H // 2 + 190,
            SLIDE_W - 200, C_LIGHT, language="english",
        )

        # ── Brand tag ────────────────────────────────────────────────────
        f_brand = get_font("ui", 38)
        draw.text((SLIDE_W // 2, SLIDE_H - 120), "AI Recipe Generator",
                  font=f_brand, fill=C_DIM, anchor="mm")

        draw.rectangle([(0, SLIDE_H - 12), (SLIDE_W, SLIDE_H)], fill=C_ACCENT)

        img.save(output_path, "PNG", optimize=True)
        return output_path


# ===========================================================================
# MAIN PIPELINE CLASS
# ===========================================================================

class VideoRecipeGenerator:
    """
    Orchestrates the full video generation pipeline.

    Usage:
        gen = VideoRecipeGenerator()
        mp4_path = gen.generate_video(recipe_data, language, output_dir,
                                      progress_cb=my_callback)
    """

    def __init__(self):
        self._slide_gen = SlideGenerator()

    # ------------------------------------------------------------------ #
    # Step 1 – Narration via Gemini                                        #
    # ------------------------------------------------------------------ #
    def generate_narration(
        self,
        food_name: str,
        ingredients: List[str],
        recipe_steps: List[str],
        language: str,
    ) -> List[str]:
        """
        Ask Gemini to produce a friendly narration script.

        Returns a list of strings (one per step).  Falls back to the raw
        recipe steps if Gemini is unavailable.
        """
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key or api_key == "YOUR_KEY_HERE":
            logger.warning("No Gemini API key — using raw recipe steps as narration.")
            return recipe_steps

        ingredients_str = ", ".join(ingredients[:15])  # cap to avoid token overflow
        steps_str = "\n".join(
            f"Step {i}: {s}" for i, s in enumerate(recipe_steps, 1)
        )

        lang_note = (
            f"Write the narration in {language.capitalize()} language."
            if language.lower() != "english"
            else "Write the narration in English."
        )

        prompt = f"""You are a professional cooking instructor creating a video narration.

Recipe: {food_name}
Ingredients: {ingredients_str}

Cooking steps:
{steps_str}

Task:
Create a friendly, beginner-friendly narration for each step.
{lang_note}

Requirements:
- Exactly one narration sentence or two SHORT sentences per step.
- Warm, encouraging tone.
- Mention specific ingredients when relevant.
- Do NOT include step numbers in the narration text itself.
- Return ONLY a JSON array of strings, one per step. No markdown, no explanation.

Example output:
["Gather your ingredients and set up your workspace.", "Heat the pan over medium flame until it starts to shimmer.", ...]"""

        try:
            from google import genai  # noqa: PLC0415

            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            text = (response.text or "").strip()

            # Strip markdown fences if present
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(l for l in lines if not l.strip().startswith("```"))

            narration = json.loads(text)
            if isinstance(narration, list) and len(narration) == len(recipe_steps):
                logger.info(f"Narration generated by Gemini: {len(narration)} steps")
                return [str(n) for n in narration]

            # Partial match — pad or trim
            if isinstance(narration, list):
                while len(narration) < len(recipe_steps):
                    narration.append(recipe_steps[len(narration)])
                return [str(n) for n in narration[: len(recipe_steps)]]

        except Exception as exc:
            logger.error(f"Gemini narration failed: {exc}")

        # Fallback
        return recipe_steps

    # ------------------------------------------------------------------ #
    # Step 2 – Audio via TTS                                               #
    # ------------------------------------------------------------------ #
    def generate_audio(
        self,
        narration_steps: List[str],
        language: str,
        output_dir: str,
        progress_cb: Optional[Callable] = None,
    ) -> List[Optional[str]]:
        """
        Generate per-step MP3 files using TTSService.

        Raises TTSService.AudioGenerationError if 0 out of N steps succeed.
        Callers (generate_video) must catch this and surface it to the user.
        """
        from Foodimg2Ing.services.tts_service import TTSService  # noqa: PLC0415

        svc = TTSService()
        audio_dir = os.path.join(output_dir, "audio")
        return svc.synthesize_all(
            narration_steps,
            language,
            audio_dir,
            progress_cb=progress_cb,
        )

    # ------------------------------------------------------------------ #
    # Step 3 – Slides via Pillow                                           #
    # ------------------------------------------------------------------ #
    def generate_slides(
        self,
        food_name: str,
        narration_steps: List[str],
        ingredients: List[str],
        language: str,
        output_dir: str,
    ) -> List[str]:
        """Render PNG slides for intro, each step, and outro."""
        slides_dir = os.path.join(output_dir, "slides")
        os.makedirs(slides_dir, exist_ok=True)

        paths: List[str] = []

        # Intro slide
        intro_path = os.path.join(slides_dir, "slide_000_intro.png")
        self._slide_gen.make_intro_slide(food_name, ingredients, language, intro_path)
        paths.append(intro_path)

        # Step slides
        total = len(narration_steps)
        for i, step_text in enumerate(narration_steps, 1):
            step_path = os.path.join(slides_dir, f"slide_{i:03d}_step.png")
            self._slide_gen.make_step_slide(
                i, total, step_text, food_name, step_path, language=language
            )
            paths.append(step_path)

        # Outro slide
        outro_path = os.path.join(slides_dir, f"slide_{total + 1:03d}_outro.png")
        self._slide_gen.make_outro_slide(food_name, outro_path)
        paths.append(outro_path)

        logger.info(f"Generated {len(paths)} slides in '{slides_dir}'")
        return paths

    # ------------------------------------------------------------------ #
    # Step 4 – Video assembly via MoviePy                                  #
    # ------------------------------------------------------------------ #
    def build_video(
        self,
        slide_paths: List[str],
        audio_paths: List[Optional[str]],
        output_path: str,
    ) -> str:
        """
        Assemble slides + audio into an MP4 using MoviePy.

        slide_paths:  [intro_slide, step1_slide, ..., outro_slide]
        audio_paths:  [None, step1_audio, ..., None]  (None = no audio clip)
        """
        try:
            from moviepy import ImageClip, AudioFileClip, concatenate_videoclips  # noqa: PLC0415
        except ImportError:
            try:
                from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(
                    "moviepy is not installed. Run: pip install moviepy"
                ) from exc

        clips = []

        for idx, slide_path in enumerate(slide_paths):
            audio_idx = idx  # audio_paths aligned: None for intro/outro slots

            audio_clip = None
            duration = MIN_SLIDE_DURATION

            if audio_idx < len(audio_paths) and audio_paths[audio_idx]:
                audio_file = audio_paths[audio_idx]
                if os.path.exists(audio_file):
                    try:
                        audio_clip = AudioFileClip(audio_file)
                        duration = max(audio_clip.duration + 0.5, MIN_SLIDE_DURATION)
                    except Exception as exc:
                        logger.warning(f"Could not load audio {audio_file}: {exc}")
                        audio_clip = None

            # Ken Burns zoom effect: subtle 1.0 → 1.06 over clip duration
            def _make_zoom_clip(sp=slide_path, dur=duration):
                clip = ImageClip(sp).with_duration(dur)
                try:
                    zoomed = clip.resized(lambda t: 1.0 + 0.06 * (t / dur))
                    return zoomed
                except Exception:
                    return clip

            video_clip = _make_zoom_clip()

            if audio_clip is not None:
                try:
                    video_clip = video_clip.with_audio(audio_clip)
                except Exception as exc:
                    logger.warning(f"Could not attach audio to clip {idx}: {exc}")

            try:
                video_clip = video_clip.fadein(0.3).fadeout(0.3)
            except Exception:
                try:
                    from moviepy.video.fx import FadeIn, FadeOut  # noqa: PLC0415
                    video_clip = video_clip.with_effects([FadeIn(0.3), FadeOut(0.3)])
                except Exception:
                    pass

            clips.append(video_clip)

        if not clips:
            raise RuntimeError("No video clips generated.")

        final = concatenate_videoclips(clips, method="compose")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        final.write_videofile(
            output_path,
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=output_path + ".temp_audio.m4a",
            remove_temp=True,
            logger=None,  # suppress verbose MoviePy output
        )

        logger.info(f"Video written to: {output_path}")
        return output_path

    # ------------------------------------------------------------------ #
    # Main entry point                                                     #
    # ------------------------------------------------------------------ #
    def generate_video(
        self,
        recipe_data: dict,
        language: str,
        output_dir: str,
        progress_cb: Optional[Callable[[str, int], None]] = None,
    ) -> str:
        """
        Full pipeline: narration → audio → slides → MP4.

        Args:
            recipe_data: dict with keys food_name, ingredients, recipe_steps.
            language:    Language key (e.g. 'english', 'hindi').
            output_dir:  Directory to write all intermediates + final MP4.
            progress_cb: Optional callable(stage_label: str, pct: int).

        Returns:
            Absolute path to the generated MP4 file.
        """
        food_name    = recipe_data.get("food_name", "Recipe")
        ingredients  = recipe_data.get("ingredients", [])
        recipe_steps = recipe_data.get("recipe_steps", [])

        if not recipe_steps:
            raise ValueError("recipe_steps cannot be empty.")

        os.makedirs(output_dir, exist_ok=True)

        def _progress(stage: str, pct: int):
            if progress_cb:
                progress_cb(stage, pct)

        # ------ Stage 1: Narration ------
        _progress("Creating narration script...", 5)
        narration = self.generate_narration(food_name, ingredients, recipe_steps, language)
        _progress("Narration ready", 20)

        # ------ Stage 2: Audio ------
        _progress("Generating voice audio...", 22)

        def _audio_progress(current, total):
            pct = 22 + int(18 * current / total)
            _progress(f"Generating voice... step {current}/{total}", pct)

        try:
            audio_paths_raw = self.generate_audio(narration, language, output_dir, _audio_progress)
        except Exception as audio_exc:
            logger.error(f"Audio generation failed: {audio_exc}")
            raise RuntimeError(
                f"Audio generation failed — {audio_exc}. "
                "Video was NOT built to avoid a silent video. "
                "Please check edge-tts and gTTS installation."
            ) from audio_exc

        # Prepend None for intro slide, append None for outro slide
        audio_paths = [None] + audio_paths_raw + [None]
        _progress("Voice audio complete", 40)

        # ------ Stage 3: Slides ------
        _progress("Building slides...", 42)
        slide_paths = self.generate_slides(food_name, narration, ingredients, language, output_dir)
        _progress("Slides ready", 65)

        # ------ Stage 4: Video ------
        _progress("Assembling video...", 68)
        output_mp4 = os.path.join(output_dir, "recipe_video.mp4")
        self.build_video(slide_paths, audio_paths, output_mp4)
        _progress("Finalizing...", 95)

        if not os.path.exists(output_mp4):
            raise RuntimeError("Video file was not created.")

        _progress("Done!", 100)
        return output_mp4
