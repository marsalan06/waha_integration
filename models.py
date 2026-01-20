from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class WahaNode(Base):
    __tablename__ = "waha_nodes"
    id = Column(Integer, primary_key=True)
    url = Column(String)         # http://waha_core_1:3000
    api_key = Column(String)
    max_sessions = Column(Integer)
    active_sessions = Column(Integer, default=0)

class WaSession(Base):
    __tablename__ = "wa_sessions"
    id = Column(Integer, primary_key=True)
    phone = Column(String)
    session_name = Column(String)
    node_id = Column(Integer, ForeignKey("waha_nodes.id"))

class ContactContainer(Base):
    __tablename__ = "contact_containers"
    id = Column(Integer, primary_key=True)
    contact_id = Column(String)  # e.g., "923458832795@c.us" or "167310467801252@lid"
    phone_number = Column(String)  # Phone number extracted from contact_id
    container_number = Column(Integer)  # 1 or 2
    node_id = Column(Integer, ForeignKey("waha_nodes.id"))
    __table_args__ = (UniqueConstraint('contact_id', 'container_number', name='uq_contact_container'),)