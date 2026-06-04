"""
TTS Service – AI Video Recipe Generator
========================================
Uses Microsoft Edge TTS (edge-tts) to synthesise narration audio for each
cooking step.  edge-tts is async; we wrap it with asyncio.run() so it is
safe to call from synchronous Flask route handlers / threads.

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
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Voice registry
# ---------------------------------------------------------------------------
LANGUAGE_VOICES: dict[str, dict] = {
    "english":   {"voice": "en-US-AriaNeural",     "label": "English",   "code": "en-US"},
    "malayalam": {"voice": "ml-IN-SobhanaNeural",   "label": "Malayalam", "code": "ml-IN"},
    "hindi":     {"voice": "hi-IN-SwaraNeural",     "label": "Hindi",     "code": "hi-IN"},
    "tamil":     {"voice": "ta-IN-PallaviNeural",   "label": "Tamil",     "code": "ta-IN"},
    "telugu":    {"voice": "te-IN-ShrutiNeural",    "label": "Telugu",    "code": "te-IN"},
    "kannada":   {"voice": "kn-IN-SapnaNeural",     "label": "Kannada",   "code": "kn-IN"},
}

DEFAULT_LANGUAGE = "english"


def get_voice(language: str) -> str:
    """Return the edge-tts voice name for the given language key."""
    key = language.lower().strip()
    return LANGUAGE_VOICES.get(key, LANGUAGE_VOICES[DEFAULT_LANGUAGE])["voice"]


def get_supported_languages() -> list[dict]:
    """Return list of {key, label} dicts for UI dropdowns."""
    return [
        {"key": k, "label": v["label"]}
        for k, v in LANGUAGE_VOICES.items()
    ]


# ---------------------------------------------------------------------------
# Core async TTS helper
# ---------------------------------------------------------------------------
async def _synthesize_async(text: str, voice: str, output_path: str) -> bool:
    """Async edge-tts synthesis for a single text chunk."""
    try:
        import edge_tts  # noqa: PLC0415

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        return True
    except Exception as exc:
        logger.error(f"edge-tts synthesis failed: {exc}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
class TTSService:
    """Synthesises narration audio for cooking steps using edge-tts."""

    def synthesize_step(
        self,
        text: str,
        language: str,
        output_path: str,
    ) -> bool:
        """
        Generate a single MP3 audio clip from *text* using the voice for
        *language* and write it to *output_path*.

        Returns True on success, False on failure.
        """
        voice = get_voice(language)
        logger.info(f"TTS | voice={voice} | chars={len(text)} | -> {output_path}")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success = loop.run_until_complete(
                _synthesize_async(text, voice, output_path)
            )
            loop.close()
            return success
        except Exception as exc:
            logger.error(f"TTS event-loop error: {exc}", exc_info=True)
            return False

    def synthesize_all(
        self,
        steps: List[str],
        language: str,
        output_dir: str,
        progress_cb=None,
    ) -> List[str]:
        """
        Generate one MP3 file per step.

        Args:
            steps:       List of narration sentences (one per cooking step).
            language:    Language key (e.g. 'english', 'malayalam').
            output_dir:  Directory to write MP3 files into.
            progress_cb: Optional callable(current, total) for progress updates.

        Returns:
            List of absolute file paths (None entries mark failures).
        """
        os.makedirs(output_dir, exist_ok=True)
        paths: List[str] = []

        for i, step_text in enumerate(steps, 1):
            out_path = os.path.join(output_dir, f"step_{i:03d}.mp3")
            ok = self.synthesize_step(step_text, language, out_path)
            paths.append(out_path if ok else None)

            if progress_cb:
                progress_cb(i, len(steps))

        succeeded = sum(1 for p in paths if p is not None)
        logger.info(f"TTS complete: {succeeded}/{len(steps)} steps synthesised in '{language}'")
        return paths


# ---------------------------------------------------------------------------
# Quick standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile

    svc = TTSService()
    with tempfile.TemporaryDirectory() as tmpdir:
        test_steps = [
            "Welcome to this cooking tutorial. Today we will make a delicious burger.",
            "First, gather all your ingredients: ground beef, buns, lettuce, and tomato.",
            "Season the beef with salt and pepper, then shape into patties.",
            "Cook the patties on a hot grill for 4 minutes per side.",
            "Assemble your burger and enjoy your meal!",
        ]
        service = TTSService()
        result_paths = service.synthesize_all(test_steps, "english", tmpdir,
                                              progress_cb=lambda c, t: print(f"  TTS {c}/{t}"))
        for p in result_paths:
            size = os.path.getsize(p) if p and os.path.exists(p) else 0
            print(f"  {'✓' if size > 0 else '✗'}  {p}  ({size} bytes)")
