from fastapi import FastAPI, Request, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
import httpx
import logging
import json

from models import WahaNode, WaSession
from node_allocator import pick_node
from database import get_db, init_nodes
from schemas import SendMessageRequest, CreateSessionRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="WAHA WhatsApp Bot", version="2.0.0")

@app.on_event("startup")
async def startup_event():
    """Initialize WAHA nodes in database if they don't exist"""
    init_nodes()

@app.get("/")
def root():
    return {"message": "WhatsApp Bot is ready!"}

@app.post("/session/create")
async def create_session(request: CreateSessionRequest, db: Session = Depends(get_db)):
    """Create a new WhatsApp session and assign it to a WAHA node"""
    phone = request.phone
    
    # Check if session already exists
    existing = db.query(WaSession).filter_by(phone=phone).first()
    if existing:
        return {"status": "error", "message": f"Session for {phone} already exists"}
    
    # Pick the least-loaded node
    node = pick_node(db)
    if not node:
        return {"status": "error", "message": "No available WAHA nodes"}
    
    # Create session in WAHA
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{node.url}/api/sessions",
                headers={"X-Api-Key": node.api_key},
                json={"name": phone},
                timeout=30
            )
            response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to create WAHA session: {e}")
        return {"status": "error", "message": str(e)}
    
    # Store session in database
    db_session = WaSession(phone=phone, session_name=phone, node_id=node.id)
    db.add(db_session)
    node.active_sessions += 1
    db.commit()
    
    logger.info(f"Created session for {phone} on node {node.url}")
    return {"status": "success", "assigned_to": node.url, "phone": phone}

async def send_msg(session_phone: str, recipient_chat_id: str, text: str, db: Session):
    """Send a message via the assigned WAHA node"""
    sess = db.query(WaSession).filter_by(phone=session_phone).first()
    if not sess:
        raise Exception(f"No session found for {session_phone}. Create session first.")
    
    node = db.query(WahaNode).get(sess.node_id)
    if not node:
        raise Exception(f"Node not found for session {session_phone}")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{node.url}/api/sendText",
            headers={"X-Api-Key": node.api_key},
            json={"session": session_phone, "chatId": recipient_chat_id, "text": text},
            timeout=30
        )
        response.raise_for_status()
        return response.json()

@app.post("/send")
async def send_message_route(request: SendMessageRequest, db: Session = Depends(get_db)):
    """Send a text message via WAHA API"""
    try:
        result = await send_msg(request.session_phone, request.recipient_chat_id, request.text, db)
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return {"status": "error", "message": str(e)}, 500

@app.post("/webhook/waha")
async def webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook endpoint to receive messages from WAHA"""
    body = await request.body()
    payload = json.loads(body)
    logger.info(f"Webhook received: {json.dumps(payload, indent=2)}")
    
    event = payload.get("event")
    session = payload.get("session", "default")
    
    if event == "message":
        # Process message
        message_payload = payload.get("payload", {})
        chat_id = message_payload.get("from")
        text = message_payload.get("body", "")
        message_id = message_payload.get("id")
        
        logger.info(f"Message received - Session: {session}, From: {chat_id}, Text: {text[:50]}...")
        
        # Store message in database (you can extend this)
        # store_msg(session, text, db)
        
        # Send seen (read receipt)
        try:
            sess = db.query(WaSession).filter_by(phone=session).first()
            if sess:
                node = db.query(WahaNode).get(sess.node_id)
                if node:
                    async with httpx.AsyncClient() as client:
                        await client.post(
                            f"{node.url}/api/sendSeen",
                            headers={"X-Api-Key": node.api_key},
                            json={"session": session, "chatId": chat_id},
                            timeout=30
                        )
                    logger.info(f"Sent seen to {chat_id}")
        except Exception as e:
            logger.warning(f"Failed to send seen to {chat_id}: {e}")
        
        # Echo back the message (you can replace this with your business logic)
        if text:
            try:
                await send_msg(session, chat_id, f"Echo: {text}", db)
                logger.info(f"Echoed message from {session} to {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send echo message: {e}")
    
    elif event == "session.status":
        status = payload.get("payload", {}).get("status", "unknown")
        logger.info(f"Session status changed: {session} -> {status}")
    
    elif event == "message.ack":
        ack_data = payload.get("payload", {})
        logger.info(f"Message acknowledgment: {ack_data}")
    
    else:
        logger.info(f"Unhandled event type: {event}")
    
    return {"ok": True}

# Legacy endpoint for backward compatibility
@app.post("/bot")
async def whatsapp_webhook_legacy(request: Request, db: Session = Depends(get_db)):
    """Legacy webhook endpoint (redirects to new webhook)"""
    body = await request.body()
    data = json.loads(body)
    return await webhook(data, db)
