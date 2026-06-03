/**
 * Speech Input — Web Speech API integration for Chef Chat.
 *
 * Provides voice input via the browser's SpeechRecognition API.
 * Recognized text is automatically populated into the chat input field.
 *
 * Language support:
 *   en -> en-US, ml -> ml-IN, hi -> hi-IN,
 *   ta -> ta-IN, te -> te-IN, kn -> kn-IN
 */

(function () {
    'use strict';

    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    var micBtn = document.getElementById('btn-voice-input');

    if (!SpeechRecognition || !micBtn) {
        // Browser doesn't support Speech Recognition — hide the button
        if (micBtn) micBtn.classList.add('hidden');
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

    // ── State ─────────────────────────────────────────────────────────
    var recognition = new SpeechRecognition();
    var isRecording = false;

    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    // ── Event Handlers ────────────────────────────────────────────────
    micBtn.addEventListener('click', function () {
        if (isRecording) {
            _stopRecording();
        } else {
            _startRecording();
        }
    });

    recognition.onresult = function (event) {
        var transcript = event.results[0][0].transcript;
        console.log('[SpeechInput] Recognized:', transcript);

        // Populate the chat input
        if (window.ChefChat) {
            window.ChefChat.setInput(transcript);
        }

        _stopRecording();
    };

    recognition.onerror = function (event) {
        console.warn('[SpeechInput] Error:', event.error);
        _stopRecording();
    };

    recognition.onend = function () {
        _stopRecording();
    };

    // ── Start/Stop ────────────────────────────────────────────────────
    function _startRecording() {
        // Set language
        var lang = window.ChefChat ? window.ChefChat.getLanguage() : 'en';
        recognition.lang = LANG_MAP[lang] || 'en-US';

        isRecording = true;
        micBtn.classList.add('recording');
        micBtn.innerHTML = '<i class="fas fa-stop"></i>';
        micBtn.title = 'Stop recording';

        try {
            recognition.start();
            console.log('[SpeechInput] Recording started, lang:', recognition.lang);
        } catch (e) {
            console.warn('[SpeechInput] Could not start:', e);
            _stopRecording();
        }
    }

    function _stopRecording() {
        isRecording = false;
        micBtn.classList.remove('recording');
        micBtn.innerHTML = '<i class="fas fa-microphone"></i>';
        micBtn.title = 'Speak your question';

        try {
            recognition.stop();
        } catch (e) { /* ignore */ }
    }
})();
