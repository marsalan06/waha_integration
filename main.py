from fastapi import FastAPI, Request, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
import httpx
import logging
import json
import os

from models import WahaNode, WaSession, ContactContainer
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
    """Create a new WhatsApp session and assign it to a WAHA node (round-robin)"""
    phone = request.phone
    logger.info(f"-------SESSION_CREATE_START---{phone}----")
    
    # Check if session already exists
    existing = db.query(WaSession).filter_by(phone=phone).first()
    if existing:
        logger.warning(f"-------SESSION_EXISTS---{phone}----")
        return {"status": "error", "message": f"Session for {phone} already exists"}
    
    # Simple round-robin: alternate between containers
    total_sessions = db.query(WaSession).count()
    container_number = (total_sessions % 2) + 1  # Alternate between 1 and 2
    logger.info(f"-------CONTAINER_SELECTED---{container_number}---{phone}----")
    
    # Get the node for this container
    node = db.query(WahaNode).filter(
        WahaNode.url.like(f"%waha_core_{container_number}%")
    ).first()
    
    if not node:
        logger.error(f"-------CONTAINER_NOT_FOUND---{container_number}----")
        return {"status": "error", "message": f"WAHA container {container_number} not found"}
    
    # Create session in WAHA
    try:
        logger.info(f"-------WAHA_SESSION_CREATE---{phone}---{node.url}----")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{node.url}/api/sessions",
                headers={"X-Api-Key": node.api_key},
                json={"name": phone},
                timeout=30
            )
            response.raise_for_status()
        logger.info(f"-------WAHA_SESSION_CREATED---{phone}---{node.url}----")
    except Exception as e:
        logger.error(f"-------WAHA_SESSION_CREATE_FAILED---{phone}---{str(e)}----")
        return {"status": "error", "message": str(e)}
    
    # Store session in database
    db_session = WaSession(phone=phone, session_name=phone, node_id=node.id)
    db.add(db_session)
    db.commit()
    
    logger.info(f"-------SESSION_CREATE_SUCCESS---{phone}---{container_number}----")
    return {"status": "success", "assigned_to": node.url, "container": container_number, "phone": phone}

def load_container_mapping() -> dict:
    """Load container to contacts mapping from JSON file (container -> [contacts])"""
    mapping_file = os.path.join(os.path.dirname(__file__), "contact_container_mapping.json")
    try:
        with open(mapping_file, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Mapping file not found: {mapping_file}, using empty mapping")
        return {}
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in mapping file: {mapping_file}")
        return {}

async def resolve_lid_to_phone_via_waha(contact_id: str, session: str, node: WahaNode) -> str:
    """Resolve LID to phone number using WAHA API: GET /api/{session}/lids/{lid}"""
    if "@lid" not in contact_id:
        return contact_id.split("@")[0] if "@" in contact_id else contact_id
    
    lid = contact_id.split("@")[0]
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{node.url}/api/{session}/lids/{lid}",
                headers={"X-Api-Key": node.api_key},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                phone = data.get("pn", "").split("@")[0] if "@" in data.get("pn", "") else None
                if phone:
                    logger.info(f"-------LID_RESOLVED_VIA_WAHA---{contact_id}---{phone}----")
                    return phone
    except Exception as e:
        logger.warning(f"-------LID_RESOLUTION_FAILED---{contact_id}---{str(e)}----")
    
    # Fallback: return LID number
    return contact_id.split("@")[0]

def get_phone_from_contact(contact_id: str, db: Session) -> str:
    """Extract phone number from contact_id and check database for LID mapping"""
    # Extract phone/LID from contact_id
    phone_or_lid = contact_id.split("@")[0] if "@" in contact_id else contact_id
    
    # Check if this contact_id exists in database with phone_number
    existing = db.query(ContactContainer).filter_by(contact_id=contact_id).first()
    if existing and existing.phone_number:
        logger.info(f"-------PHONE_FROM_DB---{contact_id}---{existing.phone_number}----")
        return existing.phone_number
    
    # For @c.us format, phone is the extracted part
    if "@c.us" in contact_id:
        return phone_or_lid
    
    # For @lid format, return LID number (will be resolved via WAHA API when storing)
    if "@lid" in contact_id:
        logger.info(f"-------LID_DETECTED---{contact_id}---{phone_or_lid}----")
    
    # Return extracted phone/LID (will be used for matching)
    return phone_or_lid

async def get_container_for_contact(contact_id: str, db: Session) -> tuple:
    """Get container number and node for a contact (container -> phone numbers mapping from JSON)"""
    logger.info(f"-------CONTAINER_LOOKUP_START---{contact_id}----")
    
    # Check if contact already assigned to containers in database
    contact_mappings = db.query(ContactContainer).filter_by(contact_id=contact_id).all()
    
    if contact_mappings:
        # Contact already mapped, return all containers
        containers = []
        for mapping in contact_mappings:
            node = db.query(WahaNode).get(mapping.node_id)
            if node:
                containers.append((mapping.container_number, node))
                logger.info(f"-------CONTAINER_FOUND_IN_DB---{contact_id}---{mapping.container_number}----")
        
        if containers:
            # Return first container for now (you can modify to return all or handle multiple)
            return containers[0]
    
    # Contact not in DB, check JSON mapping file (container -> [phone_numbers])
    logger.info(f"-------CHECKING_JSON_MAPPING---{contact_id}----")
    json_mapping = load_container_mapping()
    container_numbers = []
    
    # Get phone number (handles LID -> phone mapping via database)
    phone_number = get_phone_from_contact(contact_id, db)
    logger.info(f"-------PHONE_RESOLVED---{contact_id}---{phone_number}----")
    
    # If LID format and not resolved yet, resolve via WAHA API BEFORE matching JSON
    if "@lid" in contact_id:
        # Check if we have stored phone@c.us format for this LID in DB
        stored_contact = db.query(ContactContainer).filter_by(contact_id=contact_id).first()
        if stored_contact and stored_contact.phone_number:
            # Already resolved and stored
            phone_number = stored_contact.phone_number
            logger.info(f"-------LID_ALREADY_RESOLVED_IN_DB---{contact_id}---{phone_number}----")
        else:
            # Not resolved yet - resolve via WAHA API
            session_obj = db.query(WaSession).first()
            session_name = session_obj.phone if session_obj else "default"
            # Get first node to resolve (we'll get correct node later)
            node = db.query(WahaNode).first()
            if node:
                resolved_phone = await resolve_lid_to_phone_via_waha(contact_id, session_name, node)
                if resolved_phone and resolved_phone != phone_number:
                    phone_number = resolved_phone
                    logger.info(f"-------LID_RESOLVED_BEFORE_MATCHING---{contact_id}---{phone_number}----")
    
    # Search through containers to find all containers that have this phone number
    for container_str, phone_numbers in json_mapping.items():
        logger.info(f"-------CHECKING_CONTAINER---{container_str}---{phone_numbers}----")
        # JSON now has phone numbers only (no @c.us or @lid)
        if phone_number in phone_numbers:
            container_num = int(container_str)
            if container_num not in container_numbers:
                container_numbers.append(container_num)
                logger.info(f"-------CONTAINER_FOUND_IN_JSON---{contact_id}---{phone_number}---{container_num}----")
    
    
    if not container_numbers:
        # Not in JSON either, use default logic (last digit)
        phone = contact_id.split("@")[0] if "@" in contact_id else contact_id
        last_digit = int(phone[-1]) if phone and phone[-1].isdigit() else 0
        container_num = 1 if last_digit < 5 else 2
        container_numbers = [container_num]
        logger.warning(f"-------CONTAINER_DEFAULT_LOGIC---{contact_id}---{container_num}----")
    
    # Get nodes for all containers and store mappings
    containers = []
    for container_number in container_numbers:
        # Validate container number
        if container_number not in [1, 2]:
            logger.error(f"-------INVALID_CONTAINER---{contact_id}---{container_number}----")
            continue
        
        # Get the node for this container
        node = db.query(WahaNode).filter(
            WahaNode.url.like(f"%waha_core_{container_number}%")
        ).first()
        
        if not node:
            logger.error(f"-------CONTAINER_NODE_NOT_FOUND---{container_number}----")
            continue
        
        # Store mapping in database (if not already exists)
        existing = db.query(ContactContainer).filter_by(
            contact_id=contact_id,
            container_number=container_number
        ).first()
        
        if not existing:
            # Get phone number for this contact
            phone_number = get_phone_from_contact(contact_id, db)
            
            # If LID format, resolve to phone via WAHA API
            if "@lid" in contact_id:
                # Get session from any existing session or use "default"
                session_obj = db.query(WaSession).first()
                session_name = session_obj.phone if session_obj else "default"
                
                # Resolve LID to phone using WAHA API
                resolved_phone = await resolve_lid_to_phone_via_waha(contact_id, session_name, node)
                if resolved_phone and resolved_phone != phone_number:
                    phone_number = resolved_phone
                    logger.info(f"-------LID_RESOLVED_TO_PHONE---{contact_id}---{phone_number}----")
            
            contact_mapping = ContactContainer(
                contact_id=contact_id,
                phone_number=phone_number,
                container_number=container_number,
                node_id=node.id
            )
            db.add(contact_mapping)
            logger.info(f"-------CONTACT_ASSIGNED---{contact_id}---{phone_number}---{container_number}----")
            
            # If this is LID format, try to find matching phone format in JSON
            if "@lid" in contact_id and phone_number not in json_mapping.get(str(container_number), []):
                # LID doesn't match JSON phone numbers, log warning
                logger.warning(f"-------LID_NOT_IN_JSON---{contact_id}---{phone_number}---{container_number}----")
        
        containers.append((container_number, node))
    
    db.commit()
    
    if not containers:
        raise Exception(f"No valid containers found for contact {contact_id}")
    
    # Return first container for backward compatibility (you can modify to return all)
    logger.info(f"-------FOUND_ALL_CONTAINERS---{contact_id}---{[c[0] for c in containers]}----")
    return containers[0]

async def send_msg(session_phone: str, recipient_chat_id: str, text: str, db: Session):
    """Send a message via WAHA container determined by contact from database"""
    logger.info(f"-------SEND_MSG_START---{recipient_chat_id}---{text[:30]}----")
    
    # Get container and node from database
    container_number, node = await get_container_for_contact(recipient_chat_id, db)
    
    logger.info(f"-------SENDING_TO_CONTAINER---{recipient_chat_id}---{container_number}---{node.url}----")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{node.url}/api/sendText",
            headers={"X-Api-Key": node.api_key},
            json={"session": session_phone, "chatId": recipient_chat_id, "text": text},
            timeout=30
        )
        response.raise_for_status()
        logger.info(f"-------MSG_SENT_SUCCESS---{recipient_chat_id}---{container_number}----")
        return response.json()

@app.post("/send")
async def send_message_route(request: SendMessageRequest, db: Session = Depends(get_db)):
    """Send a text message via WAHA API (container selected from database)"""
    logger.info(f"-------API_SEND_REQUEST---{request.recipient_chat_id}---{request.text[:30]}----")
    try:
        container_number, _ = await get_container_for_contact(request.recipient_chat_id, db)
        result = await send_msg(
            request.session_phone,
            request.recipient_chat_id,
            request.text,
            db
        )
        logger.info(f"-------API_SEND_SUCCESS---{request.recipient_chat_id}---{container_number}----")
        return {"status": "success", "data": result, "container": container_number}
    except Exception as e:
        logger.error(f"-------API_SEND_ERROR---{request.recipient_chat_id}---{str(e)}----")
        return {"status": "error", "message": str(e)}, 500

@app.post("/webhook/waha")
async def webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook endpoint to receive messages from WAHA"""
    body = await request.body()
    payload = json.loads(body)
    event = payload.get("event")
    session = payload.get("session", "default")
    
    logger.info(f"-------WEBHOOK_RECEIVED---{event}---{session}----")
    
    if event == "message":
        # Process message
        message_payload = payload.get("payload", {})
        chat_id = message_payload.get("from")
        text = message_payload.get("body", "")
        message_id = message_payload.get("id")
        
        logger.info(f"-------MESSAGE_RECEIVED---{chat_id}---{session}---{text[:30]}---{message_id}----")
        
        # Store message in database (you can extend this)
        # store_msg(session, text, db)
        
        # Send seen (read receipt)
        try:
            logger.info(f"-------SENDING_SEEN---{chat_id}---{session}----")
            sess = db.query(WaSession).filter_by(phone=session).first()
            if sess:
                node = db.query(WahaNode).get(sess.node_id)
            else:
                # For default session not in DB, use first available node
                node = db.query(WahaNode).order_by(WahaNode.active_sessions).first()
            
            if node:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{node.url}/api/sendSeen",
                        headers={"X-Api-Key": node.api_key},
                        json={"session": session, "chatId": chat_id},
                        timeout=30
                    )
                logger.info(f"-------SEEN_SENT---{chat_id}---{session}----")
        except Exception as e:
            logger.warning(f"-------SEEN_FAILED---{chat_id}---{str(e)}----")
        
        # Echo back the message from all containers that have this contact mapped
        if text:
            try:
                logger.info(f"-------ECHO_START---{chat_id}---{session}----")
                
                # Get all containers for this contact
                contact_mappings = db.query(ContactContainer).filter_by(contact_id=chat_id).all()
                logger.info(f"-------ECHO_CHECKING_DB---{chat_id}---Found {len(contact_mappings)} mappings----")
                
                if not contact_mappings:
                    # Not in DB yet, get container (will be added to DB)
                    logger.info(f"-------ECHO_NOT_IN_DB---{chat_id}---Calling get_container_for_contact----")
                    container_num, node = await get_container_for_contact(chat_id, db)
                    contact_mappings = db.query(ContactContainer).filter_by(contact_id=chat_id).all()
                    logger.info(f"-------ECHO_AFTER_LOOKUP---{chat_id}---Found {len(contact_mappings)} mappings----")
                
                # Log all containers found
                container_nums = [m.container_number for m in contact_mappings]
                logger.info(f"-------ECHO_CONTAINERS_FOUND---{chat_id}---{container_nums}----")
                
                # Echo from each container with correct container number
                for mapping in contact_mappings:
                    node = db.query(WahaNode).get(mapping.node_id)
                    if node:
                        container_num = mapping.container_number
                        if container_num is None:
                            logger.error(f"-------CONTAINER_NUM_IS_NONE---{chat_id}---{mapping.id}----")
                            continue
                        echo_message = f"Echo (Container {container_num}): {text}"
                        logger.info(f"-------ECHO_SENDING---{chat_id}---Container {container_num}---{node.url}---Message: {echo_message[:50]}----")
                        
                        # Send echo using the specific container
                        async with httpx.AsyncClient() as client:
                            response = await client.post(
                                f"{node.url}/api/sendText",
                                headers={"X-Api-Key": node.api_key},
                                json={"session": session, "chatId": chat_id, "text": echo_message},
                                timeout=30
                            )
                            response.raise_for_status()
                        
                        logger.info(f"-------ECHO_SUCCESS---{chat_id}---{container_num}---{node.url}----")
            except Exception as e:
                logger.error(f"-------ECHO_FAILED---{chat_id}---{str(e)}----")
    
    elif event == "session.status":
        status = payload.get("payload", {}).get("status", "unknown")
        logger.info(f"-------SESSION_STATUS---{session}---{status}----")
    
    elif event == "message.ack":
        ack_data = payload.get("payload", {})
        logger.info(f"-------MESSAGE_ACK---{session}---{ack_data.get('id', 'unknown')}----")
    
    else:
        logger.info(f"-------UNHANDLED_EVENT---{event}---{session}----")
    
    return {"ok": True}

# Legacy endpoint for backward compatibility
@app.post("/bot")
async def whatsapp_webhook_legacy(request: Request, db: Session = Depends(get_db)):
    """Legacy webhook endpoint (redirects to new webhook)"""
    body = await request.body()
    data = json.loads(body)
    return await webhook(data, db)
