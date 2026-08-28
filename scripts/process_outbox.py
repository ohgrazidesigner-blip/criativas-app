import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timezone
from sqlalchemy import select
from app.db import SessionLocal
from app.models import OutboxEvent

# MVP local worker: the durable outbox is persisted in the same transaction as domain mutations.
# Replace this dispatcher with a queue/broker integration when external consumers exist.
with SessionLocal() as db:
    events = db.scalars(select(OutboxEvent).where(OutboxEvent.processed_at.is_(None)).order_by(OutboxEvent.created_at).limit(500)).all()
    for event in events:
        print(f"dispatch {event.event_type} {event.aggregate_type}:{event.aggregate_id}")
        event.processed_at = datetime.now(timezone.utc)
    db.commit()
    print(f"processed={len(events)}")
