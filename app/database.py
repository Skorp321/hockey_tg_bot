from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from .config import Config

# Создаем глобальную сессию
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
db_session = scoped_session(sessionmaker(bind=engine)) 