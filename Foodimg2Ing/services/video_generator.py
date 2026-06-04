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

Architecture is future-ready:
  - Avatar/chef overlay: inject as an overlay clip in build_video()
  - AI cooking images: swap Pillow slide backgrounds in generate_slides()
  - Social export: change output codec/container in build_video()
"""

import os
import json
import math
import logging
import textwrap
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

# Cooking step emojis (cycled over steps)
STEP_EMOJIS = ["🍳", "🔥", "🥄", "🧂", "🍴", "⏱️", "🫕", "🧑‍🍳", "🥘", "✅"]

# Font paths – fall back to PIL default if not found
_FONT_DIR = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "Fonts")


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Load a system font at the given size; fall back to PIL default."""
    candidates = (
        ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"] if bold
        else ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
    )
    for name in candidates:
        path = os.path.join(_FONT_DIR, name)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    # Pillow 10+ default font accepts size
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Helper: vertical gradient background
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Helper: draw centred wrapped text, return y after last line
# ---------------------------------------------------------------------------
def _draw_wrapped_text(
    draw: ImageDraw.Draw,
    text: str,
    font: ImageFont.ImageFont,
    x_center: int,
    y_start: int,
    max_width: int,
    fill,
    line_spacing: int = 10,
) -> int:
    words = text.split()
    lines = []
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


# ---------------------------------------------------------------------------
# Slide generator
# ---------------------------------------------------------------------------
class SlideGenerator:
    """Creates 1080×1920 PNG slides from cooking steps using Pillow."""

    def make_intro_slide(
        self,
        food_name: str,
        ingredients: List[str],
        language: str,
        output_path: str,
    ) -> str:
        """Generate an intro / title slide."""
        img = _make_gradient_bg(SLIDE_W, SLIDE_H)
        draw = ImageDraw.Draw(img)

        # Top accent bar
        draw.rectangle([(0, 0), (SLIDE_W, 12)], fill=C_ACCENT)

        # Logo text / badge
        f_logo = _load_font(36, bold=True)
        draw.text((SLIDE_W // 2, 80), "🍽️  AI RECIPE", font=f_logo, fill=C_ACCENT,
                  anchor="mm")

        # Big emoji
        f_emoji = _load_font(200)
        draw.text((SLIDE_W // 2, SLIDE_H // 2 - 200), "👨‍🍳", font=f_emoji,
                  fill=C_WHITE, anchor="mm")

        # Food name
        f_title = _load_font(86, bold=True)
        _draw_wrapped_text(draw, food_name.upper(), f_title,
                           SLIDE_W // 2, SLIDE_H // 2 - 60,
                           SLIDE_W - 120, C_WHITE, line_spacing=12)

        # Ingredient count badge
        f_sub = _load_font(48)
        ing_text = f"{len(ingredients)} ingredients  •  Tap to start"
        _draw_wrapped_text(draw, ing_text, f_sub, SLIDE_W // 2, SLIDE_H // 2 + 180,
                           SLIDE_W - 200, C_DIM)

        # Language badge
        f_lang = _load_font(38)
        lang_label = language.capitalize()
        _draw_wrapped_text(draw, f"🌐 {lang_label}", f_lang, SLIDE_W // 2,
                           SLIDE_H - 200, SLIDE_W - 200, C_ACCENT)

        # Bottom bar
        draw.rectangle([(0, SLIDE_H - 12), (SLIDE_W, SLIDE_H)], fill=C_ACCENT)

        img.save(output_path, "PNG", optimize=True)
        return output_path

    def make_step_slide(
        self,
        step_number: int,
        total_steps: int,
        step_text: str,
        food_name: str,
        output_path: str,
    ) -> str:
        """Generate a single cooking-step slide."""
        img = _make_gradient_bg(SLIDE_W, SLIDE_H)
        draw = ImageDraw.Draw(img)

        # Top accent bar
        draw.rectangle([(0, 0), (SLIDE_W, 12)], fill=C_ACCENT)

        # Recipe name at top
        f_recipe = _load_font(44, bold=True)
        draw.text((SLIDE_W // 2, 70), food_name.title(), font=f_recipe,
                  fill=C_ACCENT2, anchor="mm")

        # Progress indicator (small dots)
        dot_r = 12
        dot_spacing = 34
        total_width = total_steps * dot_spacing
        dot_x_start = (SLIDE_W - total_width) // 2
        for i in range(total_steps):
            cx = dot_x_start + i * dot_spacing + dot_r
            cy = 140
            colour = C_ACCENT if i < step_number else C_DIM
            draw.ellipse([(cx - dot_r, cy - dot_r), (cx + dot_r, cy + dot_r)],
                         fill=colour)

        # Step badge circle
        badge_cx, badge_cy = SLIDE_W // 2, 350
        badge_r = 100
        draw.ellipse(
            [(badge_cx - badge_r, badge_cy - badge_r),
             (badge_cx + badge_r, badge_cy + badge_r)],
            fill=C_BADGE_BG,
        )
        f_step_num = _load_font(110, bold=True)
        draw.text((badge_cx, badge_cy), str(step_number), font=f_step_num,
                  fill=C_WHITE, anchor="mm")

        # "STEP X of Y" label
        f_label = _load_font(44)
        draw.text((SLIDE_W // 2, 490),
                  f"STEP  {step_number}  of  {total_steps}",
                  font=f_label, fill=C_DIM, anchor="mm")

        # Cooking emoji (cycles by step)
        emoji = STEP_EMOJIS[(step_number - 1) % len(STEP_EMOJIS)]
        f_emoji = _load_font(160)
        draw.text((SLIDE_W // 2, 700), emoji, font=f_emoji, fill=C_WHITE, anchor="mm")

        # Separator line
        pad = 80
        draw.rectangle([(pad, 830), (SLIDE_W - pad, 836)], fill=C_ACCENT)

        # Step instruction text
        f_instr = _load_font(56, bold=False)
        _draw_wrapped_text(
            draw, step_text, f_instr,
            SLIDE_W // 2, 880,
            SLIDE_W - 160, C_WHITE,
            line_spacing=18,
        )

        # Bottom bar
        draw.rectangle([(0, SLIDE_H - 12), (SLIDE_W, SLIDE_H)], fill=C_ACCENT)

        img.save(output_path, "PNG", optimize=True)
        return output_path

    def make_outro_slide(self, food_name: str, output_path: str) -> str:
        """Generate a final 'Enjoy!' slide."""
        img = _make_gradient_bg(SLIDE_W, SLIDE_H)
        draw = ImageDraw.Draw(img)

        draw.rectangle([(0, 0), (SLIDE_W, 12)], fill=C_ACCENT)

        f_emoji = _load_font(240)
        draw.text((SLIDE_W // 2, SLIDE_H // 2 - 220), "🎉", font=f_emoji,
                  fill=C_WHITE, anchor="mm")

        f_title = _load_font(100, bold=True)
        draw.text((SLIDE_W // 2, SLIDE_H // 2 + 60), "Enjoy!", font=f_title,
                  fill=C_ACCENT, anchor="mm")

        f_sub = _load_font(52)
        _draw_wrapped_text(draw, f"Your {food_name} is ready.", f_sub,
                           SLIDE_W // 2, SLIDE_H // 2 + 200,
                           SLIDE_W - 200, C_LIGHT)

        f_brand = _load_font(38)
        draw.text((SLIDE_W // 2, SLIDE_H - 120), "AI Recipe Generator",
                  font=f_brand, fill=C_DIM, anchor="mm")

        draw.rectangle([(0, SLIDE_H - 12), (SLIDE_W, SLIDE_H)], fill=C_ACCENT)

        img.save(output_path, "PNG", optimize=True)
        return output_path


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------
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
            self._slide_gen.make_step_slide(i, total, step_text, food_name, step_path)
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
            # Determine duration from matching audio clip
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

            # Ken Burns zoom effect: subtle 1.0 → 1.06 zoom over clip duration
            def _make_zoom_clip(sp=slide_path, dur=duration):
                clip = ImageClip(sp).with_duration(dur)

                def zoom(t):
                    scale = 1.0 + 0.06 * (t / dur)
                    return scale

                try:
                    from moviepy.video.fx import Resize  # noqa: PLC0415
                    # Apply zoom via resize effect per frame
                    zoomed = clip.resized(lambda t: 1.0 + 0.06 * (t / dur))
                    return zoomed
                except Exception:
                    return clip

            video_clip = _make_zoom_clip()

            # Attach audio if available
            if audio_clip is not None:
                try:
                    video_clip = video_clip.with_audio(audio_clip)
                except Exception as exc:
                    logger.warning(f"Could not attach audio to clip {idx}: {exc}")

            # Fade in/out
            try:
                video_clip = video_clip.fadein(0.3).fadeout(0.3)
            except Exception:
                try:
                    from moviepy.video.fx import FadeIn, FadeOut  # noqa: PLC0415
                    video_clip = video_clip.with_effects([FadeIn(0.3), FadeOut(0.3)])
                except Exception:
                    pass  # fades are decorative; skip if API differs

            clips.append(video_clip)

        if not clips:
            raise RuntimeError("No video clips generated.")

        # Concatenate all clips
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

        step_count = len(narration)

        def _audio_progress(current, total):
            pct = 22 + int(18 * current / total)
            _progress(f"Generating voice... step {current}/{total}", pct)

        try:
            audio_paths_raw = self.generate_audio(narration, language, output_dir, _audio_progress)
        except Exception as audio_exc:
            # AudioGenerationError or any unexpected TTS failure
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
