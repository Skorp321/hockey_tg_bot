from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum

Base = declarative_base()

class JerseyType(enum.Enum):
    LIGHT = "light"
    DARK = "dark"

class Training(Base):
    __tablename__ = 'trainings'
    
    id = Column(Integer, primary_key=True)
    date_time = Column(DateTime, nullable=False)
    max_participants = Column(Integer, default=10)
    registrations = relationship('Registration', back_populates='training', cascade='all, delete-orphan')

class Registration(Base):
    __tablename__ = 'registrations'
    
    id = Column(Integer, primary_key=True)
    training_id = Column(Integer, ForeignKey('trainings.id'), nullable=False)
    user_id = Column(Integer, nullable=False)
    username = Column(String(100))
    registered_at = Column(DateTime, default=datetime.now, nullable=False)
    jersey_type = Column(Enum(JerseyType), nullable=True)  # Новое поле для типа майки
    
    training = relationship('Training', back_populates='registrations') 