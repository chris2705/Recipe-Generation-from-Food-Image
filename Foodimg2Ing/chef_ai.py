"""
Chef AI blueprint for AI Recipe Generator.

Routes:
  /chef-ai/chat              – Send a chat message (AJAX, POST)
  /chef-ai/history/<session>  – Get chat history for a session (GET)
  /chef-ai/sessions           – List user's chat sessions (GET)

The chat endpoint resolves recipe context from either:
  - result_id: in-memory _TEMP_RESULTS (unsaved, freshly generated recipes)
  - recipe_id: database SavedRecipe (saved recipe book entries)
"""

import os
import uuid
import json
import logging
from datetime import datetime

from flask import Blueprint, request, jsonify, session
from flask_login import current_user

from Foodimg2Ing import db
from Foodimg2Ing.chat_models import ChatMessage
from Foodimg2Ing.models import SavedRecipe
from Foodimg2Ing.services.chef_ai_service import chat as chef_chat, MAX_CONTEXT_MESSAGES

logger = logging.getLogger(__name__)

chef_ai_bp = Blueprint('chef_ai', __name__)


def _get_recipe_context_from_result(result_id: str) -> dict | None:
    """
    Resolve recipe context from in-memory temp results (unsaved recipes).
    Returns a normalized context dict or None.
    """
    from Foodimg2Ing.routes import get_temp_result
    data = get_temp_result(result_id)
    if not data:
        return None

    title = data.get('title', ['Unknown'])
    food_name = title[0] if isinstance(title, list) and title else str(title)

    ingredients = data.get('ingredients', [[]])
    ingredient_list = ingredients[0] if isinstance(ingredients, list) and ingredients else ingredients

    recipe = data.get('recipe', [[]])
    recipe_steps = recipe[0] if isinstance(recipe, list) and recipe else recipe

    return {
        'food_name': food_name,
        'ingredients': ingredient_list if isinstance(ingredient_list, list) else [],
        'recipe_steps': recipe_steps if isinstance(recipe_steps, list) else [],
        'nutrition': data.get('nutrition'),
        'prediction_source': data.get('source', 'AI Model'),
        'image_path': data.get('img', ''),
    }


def _get_recipe_context_from_saved(recipe_id: int) -> dict | None:
    """
    Resolve recipe context from a saved recipe in the database.
    Returns a normalized context dict or None.
    """
    recipe = SavedRecipe.query.get(recipe_id)
    if not recipe:
        return None

    # Security: only the owner can chat about their recipe
    if current_user.is_authenticated and recipe.user_id != current_user.id:
        return None

    return {
        'food_name': recipe.food_name,
        'ingredients': recipe.get_ingredients(),
        'recipe_steps': recipe.get_recipe_steps(),
        'nutrition': recipe.get_nutrition_dict(),
        'prediction_source': recipe.prediction_source or 'AI Model',
        'image_path': recipe.image_path or '',
    }


def _resolve_image_path(context: dict) -> str | None:
    """Resolve the absolute filesystem path to the food image."""
    img_relative = context.get('image_path', '')
    if not img_relative:
        return None

    from flask import current_app
    abs_path = os.path.join(current_app.root_path, 'static', img_relative)
    if os.path.exists(abs_path):
        return abs_path

    return None


def _get_history(session_id: str, user_id: int = None) -> list:
    """
    Retrieve chat history for a session, capped at MAX_CONTEXT_MESSAGES.

    For logged-in users: reads from database.
    For guests: reads from Flask session.
    """
    if user_id:
        messages = (
            ChatMessage.query
            .filter_by(session_id=session_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(MAX_CONTEXT_MESSAGES * 2)  # both user + assistant
            .all()
        )
        return [{'role': m.role, 'message': m.message} for m in messages]
    else:
        # Guest: read from Flask session
        guest_history = session.get(f'chef_chat_{session_id}', [])
        return guest_history[-(MAX_CONTEXT_MESSAGES * 2):]


def _save_messages(session_id: str, result_id: str, recipe_id: int,
                   user_msg: str, ai_msg: str, language: str):
    """
    Persist chat messages.

    Logged-in users: saved to database.
    Guests: saved to Flask session.
    """
    user_id = current_user.id if current_user.is_authenticated else None

    if user_id:
        # Save to database
        user_message = ChatMessage(
            user_id=user_id,
            recipe_id=recipe_id,
            result_id=result_id,
            session_id=session_id,
            role='user',
            message=user_msg,
            language=language,
        )
        ai_message = ChatMessage(
            user_id=user_id,
            recipe_id=recipe_id,
            result_id=result_id,
            session_id=session_id,
            role='assistant',
            message=ai_msg,
            language=language,
        )
        db.session.add(user_message)
        db.session.add(ai_message)
        db.session.commit()
        logger.info(f"[CHEF AI] Saved 2 messages to DB for session {session_id[:8]}")
    else:
        # Guest: save to Flask session
        key = f'chef_chat_{session_id}'
        history = session.get(key, [])
        history.append({'role': 'user', 'message': user_msg})
        history.append({'role': 'assistant', 'message': ai_msg})
        # Keep only last N messages in session
        session[key] = history[-(MAX_CONTEXT_MESSAGES * 2):]
        session.modified = True


# ---------------------------------------------------------------------------
# POST /chef-ai/chat — Main chat endpoint
# ---------------------------------------------------------------------------
@chef_ai_bp.route('/chef-ai/chat', methods=['POST'])
def chat_endpoint():
    """
    Handle a Chef AI chat message.

    Expected JSON body:
        {
            "message": "Can I replace eggs?",
            "result_id": "uuid" (optional, for unsaved recipes),
            "recipe_id": 42 (optional, for saved recipes),
            "language": "en" (optional, default 'en'),
            "session_id": "uuid" (optional, auto-generated if missing),
            "include_image": false (optional)
        }

    Returns JSON:
        {
            "response": "Yes! You can use...",
            "session_id": "uuid"
        }
    """
    data = request.get_json(silent=True) or {}

    user_message = data.get('message', '').strip()
    if not user_message:
        return jsonify({'error': 'Message is required.'}), 400

    result_id = data.get('result_id')
    recipe_id = data.get('recipe_id')
    language = data.get('language', 'en')
    session_id = data.get('session_id') or str(uuid.uuid4())
    include_image = data.get('include_image', False)

    # Resolve recipe context
    context = None
    if recipe_id:
        try:
            context = _get_recipe_context_from_saved(int(recipe_id))
        except (ValueError, TypeError):
            pass

    if not context and result_id:
        context = _get_recipe_context_from_result(result_id)

    if not context:
        return jsonify({
            'error': 'Recipe context not found. Please generate or open a recipe first.',
        }), 404

    # Get conversation history
    user_id = current_user.id if current_user.is_authenticated else None
    history = _get_history(session_id, user_id)

    # Resolve image path
    image_path = _resolve_image_path(context)

    # Call Chef AI service
    result = chef_chat(
        user_message=user_message,
        recipe_context=context,
        history=history,
        language=language,
        image_path=image_path,
        include_image=include_image,
    )

    # Save messages (if successful)
    if not result.get('error') or result['error'] is None:
        _save_messages(
            session_id=session_id,
            result_id=result_id,
            recipe_id=int(recipe_id) if recipe_id else None,
            user_msg=user_message,
            ai_msg=result['response'],
            language=language,
        )

    return jsonify({
        'response': result['response'],
        'session_id': session_id,
        'error': result.get('error'),
        'retryable': result.get('retryable', False),
    })


# ---------------------------------------------------------------------------
# GET /chef-ai/history/<session_id> — Retrieve chat history
# ---------------------------------------------------------------------------
@chef_ai_bp.route('/chef-ai/history/<session_id>')
def chat_history(session_id):
    """Retrieve chat history for a specific session."""
    if not current_user.is_authenticated:
        # Guest: return from Flask session
        key = f'chef_chat_{session_id}'
        history = session.get(key, [])
        return jsonify({'messages': history, 'session_id': session_id})

    messages = (
        ChatMessage.query
        .filter_by(session_id=session_id, user_id=current_user.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )

    return jsonify({
        'messages': [m.to_dict() for m in messages],
        'session_id': session_id,
    })


# ---------------------------------------------------------------------------
# GET /chef-ai/sessions — List user's chat sessions
# ---------------------------------------------------------------------------
@chef_ai_bp.route('/chef-ai/sessions')
def chat_sessions():
    """List all chat sessions for the current user."""
    if not current_user.is_authenticated:
        return jsonify({'sessions': [], 'authenticated': False})

    # Get distinct session IDs with their latest message
    sessions_query = (
        db.session.query(
            ChatMessage.session_id,
            ChatMessage.recipe_id,
            ChatMessage.result_id,
            db.func.max(ChatMessage.created_at).label('last_message_at'),
            db.func.count(ChatMessage.id).label('message_count'),
        )
        .filter_by(user_id=current_user.id)
        .group_by(ChatMessage.session_id)
        .order_by(db.func.max(ChatMessage.created_at).desc())
        .limit(20)
        .all()
    )

    sessions = []
    for s in sessions_query:
        sessions.append({
            'session_id': s.session_id,
            'recipe_id': s.recipe_id,
            'result_id': s.result_id,
            'last_message_at': s.last_message_at.isoformat() if s.last_message_at else None,
            'message_count': s.message_count,
        })

    return jsonify({'sessions': sessions, 'authenticated': True})


# ---------------------------------------------------------------------------
# POST /chef-ai/tts — Server-side Text-to-Speech synthesis
# ---------------------------------------------------------------------------
@chef_ai_bp.route('/chef-ai/tts', methods=['POST'])
def chef_tts_endpoint():
    """
    Generate neural TTS audio on the server for a chatbot response.
    Expects JSON:
        {
            "text": "Hello world",
            "language": "en",
            "gender": "female"
        }
    """
    import hashlib
    from Foodimg2Ing.services.tts_service import TTSService
    
    # Initialize TTS service
    tts_service = TTSService()
    
    data = request.get_json(silent=True) or {}
    text = data.get('text', '').strip()
    language = data.get('language', 'en').strip()
    gender = data.get('gender', 'female').strip().lower()

    if not text:
        return jsonify({'error': 'Text is required.'}), 400

    # Map language code to full language name for voice registry mapping
    lang_code_to_name = {
        'en': 'english',
        'ml': 'malayalam',
        'hi': 'hindi',
        'ta': 'tamil',
        'te': 'telugu',
        'kn': 'kannada'
    }
    lang_name = lang_code_to_name.get(language, 'english')

    # Female / Male voice mapping configuration
    VOICE_MAP = {
        'english': {
            'female': 'en-US-AriaNeural',
            'male': 'en-US-GuyNeural'
        },
        'malayalam': {
            'female': 'ml-IN-SobhanaNeural',
            'male': 'ml-IN-MidhunNeural'
        },
        'hindi': {
            'female': 'hi-IN-SwaraNeural',
            'male': 'hi-IN-MadhurNeural'
        },
        'tamil': {
            'female': 'ta-IN-PallaviNeural',
            'male': 'ta-IN-ValluvarNeural'
        },
        'telugu': {
            'female': 'te-IN-ShrutiNeural',
            'male': 'te-IN-MohanNeural'
        },
        'kannada': {
            'female': 'kn-IN-SapnaNeural',
            'male': 'kn-IN-GaganNeural'
        }
    }

    voice_options = VOICE_MAP.get(lang_name, VOICE_MAP['english'])
    selected_voice = voice_options.get(gender, voice_options['female'])

    # Hash parameters to generate unique cached filename
    hash_payload = f"{text}_{selected_voice}"
    filename_hash = hashlib.sha256(hash_payload.encode('utf-8')).hexdigest()
    filename = f"{filename_hash}.mp3"

    # Define paths
    from flask import current_app
    cache_dir = os.path.join(current_app.root_path, 'static', 'generated_audio')
    os.makedirs(cache_dir, exist_ok=True)
    output_path = os.path.join(cache_dir, filename)

    relative_url = f"/static/generated_audio/{filename}"

    # If already cached, return the URL instantly
    if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
        logger.info(f"[TTS CACHE HIT] {filename}")
        return jsonify({'url': relative_url})

    # Otherwise synthesize
    logger.info(f"[TTS CACHE MISS] Synthesizing using voice '{selected_voice}'")
    try:
        ok = tts_service.synthesize_step(text, lang_name, output_path, voice=selected_voice)
        if ok:
            return jsonify({'url': relative_url})
    except Exception as exc:
        logger.error(f"TTS endpoint synthesis failed: {exc}", exc_info=True)

    return jsonify({'error': 'Failed to generate Text-to-Speech audio.'}), 500
