"""
TTS Service – AI Video Recipe Generator
========================================
Synthesises narration audio for cooking steps with a 3-tier fallback chain:

  Tier 1 – edge-tts  (Microsoft Neural voices, offline/free, best quality)
  Tier 2 – gTTS      (Google TTS, requires internet, good quality)
  Tier 3 – (future)  pyttsx3 / system TTS

Windows Python 3.10+ note
--------------------------
On Windows the default asyncio event loop is ProactorEventLoop, which works
in the main thread but can behave unexpectedly when a *new* event loop is
created inside a background daemon thread (Flask video worker).  We
explicitly use SelectorEventLoop for TTS synthesis threads to avoid any
ProactorEventLoop / subprocess-in-thread issues.

Language → Voice mapping (Neural voices, free, no API key required):
  English   → en-US-AriaNeural
  Malayalam → ml-IN-SobhanaNeural
  Hindi     → hi-IN-SwaraNeural
  Tamil     → ta-IN-PallaviNeural
  Telugu    → te-IN-ShrutiNeural
  Kannada   → kn-IN-SapnaNeural
"""

import asyncio
import logging
import os
import platform
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Voice registry
# ---------------------------------------------------------------------------
LANGUAGE_VOICES: dict[str, dict] = {
    "english":   {"voice": "en-US-AriaNeural",     "label": "English",   "code": "en-US", "gtts_lang": "en"},
    "malayalam": {"voice": "ml-IN-SobhanaNeural",   "label": "Malayalam", "code": "ml-IN", "gtts_lang": "ml"},
    "hindi":     {"voice": "hi-IN-SwaraNeural",     "label": "Hindi",     "code": "hi-IN", "gtts_lang": "hi"},
    "tamil":     {"voice": "ta-IN-PallaviNeural",   "label": "Tamil",     "code": "ta-IN", "gtts_lang": "ta"},
    "telugu":    {"voice": "te-IN-ShrutiNeural",    "label": "Telugu",    "code": "te-IN", "gtts_lang": "te"},
    "kannada":   {"voice": "kn-IN-SapnaNeural",     "label": "Kannada",   "code": "kn-IN", "gtts_lang": "kn"},
}

DEFAULT_LANGUAGE = "english"


def get_voice(language: str) -> str:
    """Return the edge-tts voice name for the given language key."""
    key = language.lower().strip()
    return LANGUAGE_VOICES.get(key, LANGUAGE_VOICES[DEFAULT_LANGUAGE])["voice"]


def get_gtts_lang(language: str) -> str:
    """Return the gTTS language code for the given language key."""
    key = language.lower().strip()
    return LANGUAGE_VOICES.get(key, LANGUAGE_VOICES[DEFAULT_LANGUAGE])["gtts_lang"]


def get_supported_languages() -> list[dict]:
    """Return list of {key, label} dicts for UI dropdowns."""
    return [
        {"key": k, "label": v["label"]}
        for k, v in LANGUAGE_VOICES.items()
    ]


# ---------------------------------------------------------------------------
# Helper: create a safe event loop for background threads on Windows
# ---------------------------------------------------------------------------
def _make_thread_event_loop() -> asyncio.AbstractEventLoop:
    """
    Create an event loop that works safely inside a daemon thread on Windows.

    On Windows, asyncio.new_event_loop() returns a ProactorEventLoop by
    default (Python 3.8+).  When called from inside a background thread
    (not the main thread) this can cause issues with asyncio internals.
    We explicitly use SelectorEventLoop which is the cross-platform safe
    choice for use in threads.
    """
    if platform.system() == "Windows":
        loop = asyncio.SelectorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    return loop


# ---------------------------------------------------------------------------
# Tier 1: edge-tts (async)
# ---------------------------------------------------------------------------
async def _edge_tts_async(text: str, voice: str, output_path: str) -> bool:
    """Async edge-tts synthesis for a single text chunk."""
    try:
        import edge_tts  # noqa: PLC0415

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        # Verify file was actually written with content
        if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
            return True
        logger.warning(f"edge-tts produced an empty or tiny file: {output_path}")
        return False
    except Exception as exc:
        logger.error(f"edge-tts synthesis failed: {exc}", exc_info=True)
        return False


def _synthesize_edge_tts(text: str, voice: str, output_path: str) -> bool:
    """Run edge-tts synthesis synchronously (safe for background threads)."""
    try:
        loop = _make_thread_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_edge_tts_async(text, voice, output_path))
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    except Exception as exc:
        logger.error(f"edge-tts event-loop error: {exc}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Tier 2: gTTS fallback
# ---------------------------------------------------------------------------
def _synthesize_gtts(text: str, lang_code: str, output_path: str) -> bool:
    """
    Fallback TTS using gTTS (Google Text-to-Speech).
    Requires internet. Returns True on success.
    """
    try:
        from gtts import gTTS  # noqa: PLC0415

        tts = gTTS(text=text, lang=lang_code, slow=False)
        tts.save(output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
            logger.info(f"gTTS fallback succeeded: {output_path}")
            return True
        logger.warning(f"gTTS produced an empty file: {output_path}")
        return False
    except ImportError:
        logger.warning("gTTS is not installed — cannot use fallback.")
        return False
    except Exception as exc:
        logger.error(f"gTTS synthesis failed: {exc}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
class TTSService:
    """
    Synthesises narration audio for cooking steps.

    Tier 1 → edge-tts (Microsoft Neural, best quality)
    Tier 2 → gTTS     (Google TTS, internet required)

    synthesize_all() raises AudioGenerationError if ALL steps fail (0/N),
    so the caller can handle the failure explicitly instead of silently
    building a muted video.
    """

    class AudioGenerationError(RuntimeError):
        """Raised when no audio files could be generated for any step."""
        pass

    def synthesize_step(
        self,
        text: str,
        language: str,
        output_path: str,
        voice: Optional[str] = None,
    ) -> bool:
        """
        Generate a single MP3 audio clip from *text* and write to *output_path*.
        Tries edge-tts first; falls back to gTTS on failure.
        Returns True on success, False if all tiers failed.
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        if not voice:
            voice = get_voice(language)
        gtts_lang = get_gtts_lang(language)

        logger.info(f"TTS | voice={voice} | chars={len(text)} | → {output_path}")

        # --- Tier 1: edge-tts ---
        try:
            import edge_tts  # noqa: PLC0415 — availability check
            ok = _synthesize_edge_tts(text, voice, output_path)
            if ok:
                logger.debug(f"TTS (edge-tts) ✅ {os.path.basename(output_path)}")
                return True
            logger.warning("edge-tts returned False — trying gTTS fallback.")
        except ImportError:
            logger.warning(
                f"edge-tts not available in {sys.executable} — falling back to gTTS."
            )

        # --- Tier 2: gTTS ---
        ok = _synthesize_gtts(text, gtts_lang, output_path)
        if ok:
            return True

        logger.error(
            f"All TTS tiers failed for step text: '{text[:60]}...' — no audio file written."
        )
        return False

    def synthesize_all(
        self,
        steps: List[str],
        language: str,
        output_dir: str,
        progress_cb=None,
    ) -> List[Optional[str]]:
        """
        Generate one MP3 file per step.

        Args:
            steps:       List of narration sentences (one per cooking step).
            language:    Language key (e.g. 'english', 'malayalam').
            output_dir:  Directory to write MP3 files into.
            progress_cb: Optional callable(current, total) for progress updates.

        Returns:
            List of absolute file paths (None entries mark failures).

        Raises:
            TTSService.AudioGenerationError: if 0 out of N steps succeeded.
            This prevents silently building a muted video.
        """
        os.makedirs(output_dir, exist_ok=True)
        paths: List[Optional[str]] = []

        for i, step_text in enumerate(steps, 1):
            out_path = os.path.join(output_dir, f"step_{i:03d}.mp3")
            ok = self.synthesize_step(step_text, language, out_path)
            paths.append(out_path if ok else None)

            if progress_cb:
                progress_cb(i, len(steps))

        succeeded = sum(1 for p in paths if p is not None)
        total = len(steps)
        logger.info(f"TTS complete: {succeeded}/{total} steps synthesised in '{language}'")

        # ── Hard failure: no audio at all → never build a silent video ──
        if succeeded == 0 and total > 0:
            raise TTSService.AudioGenerationError(
                f"Audio generation failed: 0/{total} steps produced audio in '{language}'. "
                "Check edge-tts installation and internet connectivity for gTTS fallback."
            )

        if succeeded < total:
            logger.warning(
                f"Partial audio: {total - succeeded} step(s) have no audio — "
                "those slides will be silent."
            )

        return paths


# ---------------------------------------------------------------------------
# Quick standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    test_steps = [
        "Welcome to this cooking tutorial. Today we will make a delicious burger.",
        "First, gather all your ingredients: ground beef, buns, lettuce, and tomato.",
        "Season the beef with salt and pepper, then shape into patties.",
        "Cook the patties on a hot grill for 4 minutes per side.",
        "Assemble your burger and enjoy your meal!",
    ]

    svc = TTSService()
    with tempfile.TemporaryDirectory() as tmpdir:
        result_paths = svc.synthesize_all(
            test_steps, "english", tmpdir,
            progress_cb=lambda c, t: print(f"  TTS {c}/{t}"),
        )
        for p in result_paths:
            size = os.path.getsize(p) if p and os.path.exists(p) else 0
            print(f"  {'✓' if size > 0 else '✗'}  {p}  ({size} bytes)")
