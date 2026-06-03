"""
Database models for Chef AI chat persistence.

Models:
  - ChatMessage: Stores individual chat messages between user and Chef AI.

Each message belongs to a chat session (session_id). Sessions are tied to
either a result_id (unsaved, in-memory recipe) or a recipe_id (saved recipe).
Logged-in users get persistent history; guests use session-only memory.
"""

from datetime import datetime
from Foodimg2Ing import db


class ChatMessage(db.Model):
    """A single message in a Chef AI conversation."""

    __tablename__ = 'chat_message'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    recipe_id = db.Column(db.Integer, db.ForeignKey('saved_recipe.id'), nullable=True, index=True)
    result_id = db.Column(db.String(36), nullable=True)       # UUID for unsaved recipe results
    session_id = db.Column(db.String(36), nullable=False, index=True)  # groups messages in one chat
    role = db.Column(db.String(10), nullable=False)             # 'user' or 'assistant'
    message = db.Column(db.Text, nullable=False)
    language = db.Column(db.String(20), default='en')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    user = db.relationship('User', backref=db.backref('chat_messages', lazy='dynamic'))

    def to_dict(self):
        """Serialize to dict for JSON responses."""
        return {
            'id': self.id,
            'role': self.role,
            'message': self.message,
            'language': self.language,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f'<ChatMessage {self.role} session={self.session_id[:8]}>'
