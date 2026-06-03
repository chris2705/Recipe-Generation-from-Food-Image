/**
 * Speech Output — Browser SpeechSynthesis API for Chef Chat TTS.
 *
 * Converts Chef AI text responses to speech using the browser's
 * built-in text-to-speech engine. Supports multiple languages.
 *
 * Language support:
 *   en -> en-US, ml -> ml-IN, hi -> hi-IN,
 *   ta -> ta-IN, te -> te-IN, kn -> kn-IN
 */

(function () {
    'use strict';

    var synth = window.speechSynthesis;

    if (!synth) {
        console.warn('[SpeechOutput] Browser does not support SpeechSynthesis.');
        window.SpeechOutput = { speak: function () { } };
        return;
    }

    // ── Language Map ──────────────────────────────────────────────────
    var LANG_MAP = {
        'en': 'en-US',
        'ml': 'ml-IN',
        'hi': 'hi-IN',
        'ta': 'ta-IN',
        'te': 'te-IN',
        'kn': 'kn-IN',
    };

    var currentUtterance = null;
    var currentButton = null;

    /**
     * Speak text aloud using browser TTS.
     *
     * @param {string} text - The text to speak.
     * @param {string} langCode - Language code (en, ml, hi, ta, te, kn).
     * @param {HTMLElement} [btn] - The listen button element (for toggling state).
     */
    function speak(text, langCode, btn) {
        // Stop any currently playing audio
        if (synth.speaking) {
            synth.cancel();
            _resetButton(currentButton);

            // If clicking the same button, just stop
            if (currentButton === btn) {
                currentButton = null;
                currentUtterance = null;
                return;
            }
        }

        // Clean the text for speech (remove markdown-like formatting)
        var cleanText = _cleanForSpeech(text);
        if (!cleanText) return;

        var utterance = new SpeechSynthesisUtterance(cleanText);
        utterance.lang = LANG_MAP[langCode] || 'en-US';
        utterance.rate = 0.95;
        utterance.pitch = 1.0;
        utterance.volume = 1.0;

        // Try to find a matching voice
        var voices = synth.getVoices();
        var targetLang = LANG_MAP[langCode] || 'en-US';
        var matchedVoice = null;

        for (var i = 0; i < voices.length; i++) {
            if (voices[i].lang === targetLang) {
                matchedVoice = voices[i];
                break;
            }
        }

        // Fallback: match by language prefix
        if (!matchedVoice) {
            var langPrefix = targetLang.split('-')[0];
            for (var j = 0; j < voices.length; j++) {
                if (voices[j].lang.startsWith(langPrefix)) {
                    matchedVoice = voices[j];
                    break;
                }
            }
        }

        if (matchedVoice) {
            utterance.voice = matchedVoice;
        }

        // Update button state
        if (btn) {
            currentButton = btn;
            btn.classList.add('playing');
            btn.innerHTML = '<i class="fas fa-volume-mute"></i> Stop';
        }

        utterance.onend = function () {
            _resetButton(btn);
            currentButton = null;
            currentUtterance = null;
        };

        utterance.onerror = function (e) {
            console.warn('[SpeechOutput] Error:', e.error);
            _resetButton(btn);
            currentButton = null;
            currentUtterance = null;
        };

        currentUtterance = utterance;
        synth.speak(utterance);
    }

    /**
     * Clean text for speech output (remove markdown formatting).
     */
    function _cleanForSpeech(text) {
        return text
            .replace(/\*\*(.+?)\*\*/g, '$1')        // Remove bold markers
            .replace(/\*(.+?)\*/g, '$1')              // Remove italic markers
            .replace(/^[\-\*]\s+/gm, '')              // Remove bullet markers
            .replace(/^\d+\.\s+/gm, '')               // Remove numbered list markers
            .replace(/#{1,6}\s+/g, '')                 // Remove heading markers
            .replace(/`(.+?)`/g, '$1')                 // Remove inline code markers
            .replace(/\n{2,}/g, '. ')                  // Double newlines → pause
            .replace(/\n/g, ' ')                       // Single newlines → space
            .trim();
    }

    /**
     * Reset a listen button to its default state.
     */
    function _resetButton(btn) {
        if (btn) {
            btn.classList.remove('playing');
            btn.innerHTML = '<i class="fas fa-volume-up"></i> Listen';
        }
    }

    // Ensure voices are loaded (some browsers load asynchronously)
    if (synth.onvoiceschanged !== undefined) {
        synth.onvoiceschanged = function () {
            /* voices loaded */
        };
    }

    // ── Expose globally ───────────────────────────────────────────────
    window.SpeechOutput = {
        speak: speak,
    };
})();
