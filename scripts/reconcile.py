import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SessionLocal
from app.domain import reconcile
from app.models import Company
from sqlalchemy import select

with SessionLocal() as db:
    company = db.scalar(select(Company).limit(1))
    if not company:
        raise SystemExit("Company not found")
    issues = reconcile(db, company.id)
    if not issues:
        print("OK: no structural reconciliation issues")
    else:
        for issue in issues:
            print(f"{issue['severity'].upper()}: {issue['entity']} - {issue['message']}")
        raise SystemExit(1 if any(i['severity']=='critical' for i in issues) else 0)
