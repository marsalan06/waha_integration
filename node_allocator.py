from sqlalchemy.orm import Session
from models import WahaNode

def pick_node(db: Session):
    """Pick the least-loaded WAHA node that has capacity"""
    return db.query(WahaNode)\
        .filter(WahaNode.active_sessions < WahaNode.max_sessions)\
        .order_by(WahaNode.active_sessions)\
        .first()
