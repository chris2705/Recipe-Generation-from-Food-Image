/**
 * Chef Chat — Main chat controller for Ask Chef AI.
 *
 * Manages:
 *  - Message sending via /chef-ai/chat
 *  - DOM manipulation for chat bubbles
 *  - Session ID tracking
 *  - Quick action buttons
 *  - Language selector
 *  - Typing indicator
 *  - Auto-scroll
 *  - Integration with SpeechInput and SpeechOutput modules
 */

(function () {
    'use strict';

    // ── DOM Elements ──────────────────────────────────────────────────
    const messagesContainer = document.getElementById('chef-chat-messages');
    const inputField = document.getElementById('chef-chat-input');
    const sendBtn = document.getElementById('btn-send-chat');
    const typingIndicator = document.getElementById('chef-typing');
    const welcomeMsg = document.getElementById('chef-welcome');
    const langSelector = document.getElementById('chef-lang-selector');
    const quickActions = document.querySelectorAll('.btn-quick-action');

    if (!messagesContainer || !inputField || !sendBtn) return;

    // ── State ─────────────────────────────────────────────────────────
    const context = window.__chefContext || {};
    let sessionId = _generateUUID();
    let isProcessing = false;
    let _lastFailedMessage = null;

    // ── Init ──────────────────────────────────────────────────────────
    _enableSendButton();
    _bindEvents();

    // ── Event Bindings ────────────────────────────────────────────────
    function _bindEvents() {
        // Send button click
        sendBtn.addEventListener('click', _handleSend);

        // Enter key (Shift+Enter for newline is not needed for single-line input)
        inputField.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                _handleSend();
            }
        });

        // Toggle send button state based on input content
        inputField.addEventListener('input', _enableSendButton);

        // Quick action buttons
        quickActions.forEach(function (btn) {
            btn.addEventListener('click', function () {
                var prompt = this.getAttribute('data-prompt');
                if (prompt && !isProcessing) {
                    inputField.value = prompt;
                    _enableSendButton();
                    _handleSend();
                }
            });
        });
    }

    // ── Send Message ──────────────────────────────────────────────────
    function _handleSend(retryMessage) {
        var message = retryMessage || inputField.value.trim();
        if (!message || isProcessing) return;

        isProcessing = true;
        if (!retryMessage) inputField.value = '';
        _enableSendButton();

        // Hide welcome if shown
        if (welcomeMsg) {
            welcomeMsg.style.display = 'none';
        }

        // Add user bubble (skip if this is a retry — message already shown)
        if (!retryMessage) {
            _addMessage('user', message);
        }

        // Show typing indicator
        _showTyping(true);

        // Send to server
        var payload = {
            message: message,
            result_id: context.result_id || null,
            recipe_id: context.recipe_id || null,
            language: langSelector ? langSelector.value : 'en',
            session_id: sessionId,
            include_image: false,
        };

        fetch('/chef-ai/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                _showTyping(false);
                isProcessing = false;
                _enableSendButton();

                if (data.session_id) {
                    sessionId = data.session_id;
                }

                var aiText = data.response || 'I could not process your request.';

                // Check if this was a retryable error
                if (data.retryable && data.error) {
                    _lastFailedMessage = message;
                    _addMessage('assistant', aiText, true);
                } else {
                    _lastFailedMessage = null;
                    _addMessage('assistant', aiText, false);
                }
            })
            .catch(function (err) {
                _showTyping(false);
                isProcessing = false;
                _enableSendButton();
                console.error('[ChefChat] Network error:', err);
                _lastFailedMessage = message;
                _addMessage('assistant',
                    'Network error — please check your connection and try again.',
                    true);
            });
    }

    // ── Add Message Bubble ────────────────────────────────────────────
    function _addMessage(role, text) {
        var msgDiv = document.createElement('div');
        msgDiv.className = 'chef-msg ' + role;

        var avatarDiv = document.createElement('div');
        avatarDiv.className = 'chef-msg-avatar';
        avatarDiv.textContent = role === 'assistant' ? '🧑‍🍳' : '👤';

        var bubbleDiv = document.createElement('div');
        bubbleDiv.className = 'chef-msg-bubble';

        if (role === 'assistant') {
            bubbleDiv.innerHTML = _formatResponse(text);

            // Add listen button
            var actionsDiv = document.createElement('div');
            actionsDiv.className = 'chef-msg-actions';

            var listenBtn = document.createElement('button');
            listenBtn.className = 'btn-listen';
            listenBtn.innerHTML = '<i class="fas fa-volume-up"></i> Listen';
            listenBtn.setAttribute('data-text', text);
            listenBtn.addEventListener('click', function () {
                var lang = langSelector ? langSelector.value : 'en';
                if (window.SpeechOutput) {
                    window.SpeechOutput.speak(this.getAttribute('data-text'), lang, this);
                }
            });

            actionsDiv.appendChild(listenBtn);
            bubbleDiv.appendChild(actionsDiv);
        } else {
            bubbleDiv.textContent = text;
        }

        msgDiv.appendChild(avatarDiv);
        msgDiv.appendChild(bubbleDiv);

        // Insert before typing indicator
        if (typingIndicator) {
            messagesContainer.insertBefore(msgDiv, typingIndicator);
        } else {
            messagesContainer.appendChild(msgDiv);
        }

        _scrollToBottom();
    }

    // ── Format AI Response (light markdown) ───────────────────────────
    function _formatResponse(text) {
        // Escape HTML first
        var escaped = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        // Bold: **text**
        escaped = escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // Bullet lists: lines starting with - or *
        escaped = escaped.replace(/^[\-\*]\s+(.+)$/gm, '<li>$1</li>');
        escaped = escaped.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

        // Numbered lists: lines starting with 1. 2. etc.
        escaped = escaped.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');

        // Paragraphs: split by double newline
        escaped = escaped.replace(/\n\n+/g, '</p><p>');
        escaped = escaped.replace(/\n/g, '<br>');

        // Wrap in paragraph
        if (!escaped.startsWith('<')) {
            escaped = '<p>' + escaped + '</p>';
        }

        return escaped;
    }

    // ── Typing Indicator ──────────────────────────────────────────────
    function _showTyping(show) {
        if (typingIndicator) {
            typingIndicator.classList.toggle('visible', show);
            if (show) _scrollToBottom();
        }
    }

    // ── Auto-scroll ───────────────────────────────────────────────────
    function _scrollToBottom() {
        setTimeout(function () {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }, 50);
    }

    // ── Enable/Disable Send Button ────────────────────────────────────
    function _enableSendButton() {
        var hasText = inputField.value.trim().length > 0;
        sendBtn.disabled = !hasText || isProcessing;
    }

    // ── UUID Generator ────────────────────────────────────────────────
    function _generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            var r = (Math.random() * 16) | 0;
            var v = c === 'x' ? r : (r & 0x3) | 0x8;
            return v.toString(16);
        });
    }

    // ── Expose for SpeechInput integration ────────────────────────────
    window.ChefChat = {
        setInput: function (text) {
            inputField.value = text;
            _enableSendButton();
            inputField.focus();
        },
        send: _handleSend,
        getLanguage: function () {
            return langSelector ? langSelector.value : 'en';
        },
    };
})();
