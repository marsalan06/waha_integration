from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from models import Base, WahaNode

logger = logging.getLogger(__name__)

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://waha:waha@postgres:5432/waha")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_nodes():
    """Initialize WAHA nodes in database if they don't exist"""
    db = SessionLocal()
    try:
        # Check if nodes already exist
        existing_nodes = db.query(WahaNode).count()
        if existing_nodes == 0:
            logger.info("Initializing WAHA nodes...")
            # Add nodes based on docker-compose configuration
            # Read API keys from environment variables
            api_key_1 = os.getenv("WAHA_API_KEY_1", "secret1")
            api_key_2 = os.getenv("WAHA_API_KEY_2", "secret2")
            
            nodes = [
                WahaNode(url="http://waha_core_1:3000", api_key=api_key_1, max_sessions=200, active_sessions=0),
                WahaNode(url="http://waha_core_2:3000", api_key=api_key_2, max_sessions=200, active_sessions=0),
            ]
            for node in nodes:
                db.add(node)
            db.commit()
            logger.info(f"Initialized {len(nodes)} WAHA nodes")
        else:
            logger.info(f"Found {existing_nodes} existing WAHA nodes")
    except Exception as e:
        logger.error(f"Error initializing nodes: {e}")
    finally:
        db.close()
