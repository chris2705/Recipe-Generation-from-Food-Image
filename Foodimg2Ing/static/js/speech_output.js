/**
 * Speech Output — Server-side Edge-TTS + Browser fallback.
 *
 * Synthesizes Chef AI chatbot responses on the server using Edge-TTS
 * and falls back to window.speechSynthesis if the server is offline or fails.
 */

(function () {
    'use strict';

    var synth = window.speechSynthesis;
    var currentAudio = null;
    var currentButton = null;

    // ── Language tag mapping for browser fallback ────────────────────
    var BROWSER_LANG_MAP = {
        'en': 'en-US',
        'ml': 'ml-IN',
        'hi': 'hi-IN',
        'ta': 'ta-IN',
        'te': 'te-IN',
        'kn': 'kn-IN',
    };

    /**
     * Stop all currently playing TTS audio (either server MP3 or browser voice).
     */
    function _stopAllPlayback() {
        if (currentAudio) {
            currentAudio.pause();
            currentAudio = null;
        }
        if (synth && synth.speaking) {
            synth.cancel();
        }
        _resetButton(currentButton);
    }

    /**
     * Reset a button to its default "Listen" state.
     */
    function _resetButton(btn) {
        if (btn) {
            btn.classList.remove('playing');
            btn.innerHTML = '<i class="fas fa-volume-up"></i> Listen';
        }
    }

    /**
     * Play an audio URL (MP3) using HTML5 Audio.
     */
    function _playAudioUrl(url, btn) {
        currentAudio = new Audio(url);

        if (btn) {
            btn.classList.add('playing');
            btn.innerHTML = '<i class="fas fa-volume-mute"></i> Stop';
        }

        currentAudio.onended = function () {
            _resetButton(btn);
            currentButton = null;
            currentAudio = null;
        };

        currentAudio.onerror = function (err) {
            console.error('[SpeechOutput] HTML5 Audio playback error:', err);
            _resetButton(btn);
            currentButton = null;
            currentAudio = null;
        };

        currentAudio.play().catch(function (e) {
            console.error('[SpeechOutput] HTML5 Audio playback failed (possibly blocked by autoplay policies):', e);
            _resetButton(btn);
            currentButton = null;
            currentAudio = null;
        });
    }

    /**
     * Fallback to the browser's speechSynthesis.
     */
    function _playBrowserFallback(cleanText, langCode, btn) {
        if (!synth) {
            console.warn('[SpeechOutput] SpeechSynthesis is not supported by this browser.');
            _resetButton(btn);
            currentButton = null;
            return;
        }

        var utterance = new SpeechSynthesisUtterance(cleanText);
        utterance.lang = BROWSER_LANG_MAP[langCode] || 'en-US';
        utterance.rate = 0.95;
        utterance.pitch = 1.0;
        utterance.volume = 1.0;

        // Try to match a native speech voice for the language
        var voices = synth.getVoices();
        var targetLang = BROWSER_LANG_MAP[langCode] || 'en-US';
        var matchedVoice = null;

        for (var i = 0; i < voices.length; i++) {
            if (voices[i].lang === targetLang) {
                matchedVoice = voices[i];
                break;
            }
        }

        // Prefix match fallback
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

        if (btn) {
            btn.classList.add('playing');
            btn.innerHTML = '<i class="fas fa-volume-mute"></i> Stop';
        }

        utterance.onend = function () {
            _resetButton(btn);
            currentButton = null;
        };

        utterance.onerror = function (e) {
            console.warn('[SpeechOutput] Browser speechSynthesis error:', e.error);
            _resetButton(btn);
            currentButton = null;
        };

        synth.speak(utterance);
    }

    /**
     * Clean text for clean narration (remove markdown formatting).
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
     * Primary speak function.
     * Tries server-side synthesis first, then falls back to browser TTS.
     *
     * @param {string} text - Chatbot message text to read.
     * @param {string} langCode - Language code ('en', 'ml', 'hi', etc.)
     * @param {HTMLElement} [btn] - The listen button element.
     */
    function speak(text, langCode, btn) {
        // Toggle play state: if clicking the currently playing speaker, stop it
        if (currentButton === btn && (currentAudio || (synth && synth.speaking))) {
            _stopAllPlayback();
            currentButton = null;
            return;
        }

        // Stop any active audio first
        _stopAllPlayback();

        var cleanText = _cleanForSpeech(text);
        if (!cleanText) return;

        // Visual loading spinner state
        if (btn) {
            currentButton = btn;
            btn.classList.add('playing');
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Loading...';
        }

        // Get gender selection from DOM dropdown selector
        var gender = 'female';
        var genderSelector = document.getElementById('chef-gender-selector');
        if (genderSelector) {
            gender = genderSelector.value;
        }

        // Request server Edge-TTS audio
        fetch('/chef-ai/tts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text: cleanText,
                language: langCode,
                gender: gender
            })
        })
        .then(function (res) {
            if (!res.ok) {
                throw new Error('HTTP status ' + res.status);
            }
            return res.json();
        })
        .then(function (data) {
            if (data.url) {
                _playAudioUrl(data.url, btn);
            } else {
                throw new Error('No URL in response');
            }
        })
        .catch(function (err) {
            console.warn('[SpeechOutput] Server Edge-TTS failed: ' + err.message + '. Falling back to browser SpeechSynthesis.');
            _playBrowserFallback(cleanText, langCode, btn);
        });
    }

    // Expose TTS API
    window.SpeechOutput = {
        speak: speak
    };

})();
