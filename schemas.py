from pydantic import BaseModel

class SendMessageRequest(BaseModel):
    session_phone: str  # The WhatsApp account phone number (session)
    recipient_chat_id: str  # The recipient's chat ID (e.g., "1234567890@c.us")
    text: str

class CreateSessionRequest(BaseModel):
    phone: str
