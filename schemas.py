from pydantic import BaseModel
from typing import Optional

class SendMessageRequest(BaseModel):
    recipient_chat_id: str  # The recipient's chat ID (e.g., "1234567890@c.us")
    text: str
    container_number: int  # WAHA container number (1 or 2)
    session_phone: Optional[str] = "default"  # Session name, defaults to "default"

class CreateSessionRequest(BaseModel):
    phone: str = "default"  # Default session name
    container_number: Optional[int] = None  # Optional: specify container number (1 or 2)