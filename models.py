from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class WahaNode(Base):
    __tablename__ = "waha_nodes"
    id = Column(Integer, primary_key=True)
    url = Column(String)         # http://waha1:3000
    api_key = Column(String)
    max_sessions = Column(Integer)
    active_sessions = Column(Integer, default=0)

class WaSession(Base):
    __tablename__ = "wa_sessions"
    id = Column(Integer, primary_key=True)
    phone = Column(String)
    session_name = Column(String)
    node_id = Column(Integer, ForeignKey("waha_nodes.id"))
