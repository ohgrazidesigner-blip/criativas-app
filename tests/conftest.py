import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-that-is-long-enough")
os.environ.setdefault("CRIATIVAS_INITIAL_PASSWORD", "Criativas2024!")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Customer, Material, Product, Supplier
from app.seed import seed


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def manager(client):
    r = client.post('/login', data={'email':'admin@criativas.local','password':'Criativas2024!'}, follow_redirects=False)
    assert r.status_code == 303
    return client


@pytest.fixture()
def operational(client):
    r = client.post('/login', data={'email':'operacao@criativas.local','password':'Criativas2024!'}, follow_redirects=False)
    assert r.status_code == 303
    return client


def bootstrap_entities(client, costs=True, stock=20):
    client.post('/customers', data={'name':'Cliente Teste','phone':'','email':''})
    client.post('/suppliers', data={'name':'Fornecedor Teste','contact':''})
    with SessionLocal() as db:
        mats = db.scalars(select(Material)).all()
        prod = db.scalar(select(Product))
        customer = db.scalar(select(Customer).where(Customer.name=='Cliente Teste'))
        supplier = db.scalar(select(Supplier).where(Supplier.name=='Fornecedor Teste'))
        data = {'mats': [(m.id,m.name) for m in mats], 'product_id':prod.id, 'customer_id':customer.id, 'supplier_id':supplier.id}
    if costs:
        cmap={'Caneca Cerâmica Branca 325ml':'8.00','Caixa para caneca':'2.50','Papel sulfite A4':'0.05'}
        for mid,name in data['mats']:
            client.post(f'/materials/{mid}/cost', data={'cost':cmap[name], 'min_stock':'0'})
    if stock:
        for mid,_ in data['mats']:
            client.post('/inventory/adjust', data={'material_id':mid,'quantity':str(stock),'reason':'Saldo inicial conferido'})
    return data
