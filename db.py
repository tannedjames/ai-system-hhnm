from sqlalchemy import create_engine, Column, Integer, String, TIMESTAMP
from sqlalchemy.orm import declarative_base, sessionmaker
import os
from datetime import datetime
import time
DB_URL = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASS')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"

engine = create_engine(DB_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    filename = Column(String)
    object_key = Column(String)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)


def init_db():
    for i in range(10):
        try:
            Base.metadata.create_all(bind=engine)
            print("✅ DB connected")
            return
        except Exception as e:
            print("⏳ Waiting for DB...", e)
            time.sleep(2)

    raise Exception("❌ DB not ready")