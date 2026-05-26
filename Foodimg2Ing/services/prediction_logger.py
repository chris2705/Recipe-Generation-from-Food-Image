"""
Prediction Logger for AI Recipe Generator.

Logs every prediction to a JSONL file (one JSON object per line)
for performance tracking and debugging.
"""

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Log directory is at the project root level
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
_LOG_FILE = os.path.join(_LOG_DIR, 'predictions.jsonl')


def log_prediction(
    image_name: str,
    local_prediction: str,
    local_confidence: float,
    used_gemini: bool,
    gemini_prediction: str,
    final_prediction: str,
    source: str,
) -> None:
    """
    Append a prediction log entry to logs/predictions.jsonl.

    Args:
        image_name: Name of the uploaded/sample image file.
        local_prediction: Title predicted by the local model.
        local_confidence: Confidence score from the local model (0.0-1.0).
        used_gemini: Whether Gemini was called.
        gemini_prediction: Title predicted by Gemini (or 'N/A'/'FAILED'/'DISABLED').
        final_prediction: The title that was ultimately shown to the user.
        source: Which AI produced the final result.
    """
    entry = {
        'timestamp': datetime.now().isoformat(),
        'image_name': image_name,
        'local_prediction': local_prediction,
        'local_confidence': round(local_confidence, 4),
        'used_gemini': used_gemini,
        'gemini_prediction': gemini_prediction,
        'final_prediction': final_prediction,
        'source': source,
    }

    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        logger.info(f"Logged prediction: {final_prediction} (source={source}, confidence={local_confidence:.2f})")
    except Exception as e:
        logger.error(f"Failed to write prediction log: {e}")
