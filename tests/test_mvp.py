from decimal import Decimal
from sqlalchemy import select

from app.db import SessionLocal
from app.domain import D, aggregated_dashboard, inventory_on_hand, order_payment_summary
from app.models import (
    CommercialStatus, CostSnapshot, FulfillmentStatus, InventoryTransaction, InventoryTxType,
    Material, Order, ProductionOrder, ProductionStatus, Purchase, Role, User
)
from .conftest import bootstrap_entities


def create_order(client, data, price='30', qty='1', method='PICKUP'):
    r=client.post('/orders/new', data={
        'customer_id':data['customer_id'],'product_id':data['product_id'],'quantity':qty,'unit_price':price,
        'discount':'0','freight_charged':'0','company_freight_cost':'0','expected_payment_method':'PIX',
        'fulfillment_method':method,'promised_at':'','priority':'NORMAL'
    }, follow_redirects=False)
    assert r.status_code==303
    return r.headers['location'].split('/')[-1]


def test_incomplete_cost_is_hard_block(manager):
    data=bootstrap_entities(manager,costs=False,stock=20)
    oid=create_order(manager,data,price='60')
    r=manager.post(f'/orders/{oid}/confirm',data={'override_reason':'qualquer'},follow_redirects=False)
    assert r.status_code==303
    with SessionLocal() as db:
        o=db.get(Order,oid)
        assert o.commercial_status==CommercialStatus.DRAFT
        assert db.scalar(select(CostSnapshot).where(CostSnapshot.order_id==oid)) is None


def test_confirm_creates_snapshot_and_reservation(manager):
    data=bootstrap_entities(manager,costs=True,stock=20)
    oid=create_order(manager,data,price='60',qty='2')
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    with SessionLocal() as db:
        o=db.get(Order,oid)
        assert o.commercial_status==CommercialStatus.CONFIRMED
        assert len(o.snapshots)==1
        assert o.production.status==ProductionStatus.READY
        snap_total=o.snapshots[0].total_cost
        # Changing material cost later must not mutate historical snapshot.
        m=db.scalar(select(Material).where(Material.name=='Caneca Cerâmica Branca 325ml'))
        m.current_cost=Decimal('99')
        db.commit()
    with SessionLocal() as db:
        o=db.get(Order,oid)
        assert o.snapshots[0].total_cost==snap_total


def test_low_margin_requires_manager_reason(manager):
    data=bootstrap_entities(manager,costs=True,stock=20)
    oid=create_order(manager,data,price='15')
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    with SessionLocal() as db:
        assert db.get(Order,oid).commercial_status==CommercialStatus.DRAFT
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':'Pedido estratégico aprovado pela gestão'})
    with SessionLocal() as db:
        o=db.get(Order,oid)
        assert o.commercial_status==CommercialStatus.CONFIRMED
        assert o.margin_override_reason


def test_purchase_does_not_change_stock_until_receipt(manager):
    data=bootstrap_entities(manager,costs=True,stock=0)
    oid=create_order(manager,data,price='60')
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    with SessionLocal() as db:
        o=db.get(Order,oid); assert o.production.status==ProductionStatus.BLOCKED
        caneca=db.scalar(select(Material).where(Material.name=='Caneca Cerâmica Branca 325ml'))
        before=inventory_on_hand(db,caneca.id); caneca_id=caneca.id
    r=manager.post('/purchases/new',data={'supplier_id':data['supplier_id'],'material_id':[caneca_id,caneca_id,caneca_id],'quantity':['10','0','0'],'unit_price':['8','0','0'],'freight':'0'},follow_redirects=False)
    assert r.status_code==303
    pid=r.headers['location'].split('/')[-1]
    with SessionLocal() as db:
        assert inventory_on_hand(db,caneca_id)==before
        p=db.get(Purchase,pid); item=p.items[0]; item_id=item.id
    manager.post(f'/purchases/{pid}/receipt',data={f'qty_{item_id}':'10'})
    with SessionLocal() as db:
        assert inventory_on_hand(db,caneca_id)==Decimal('10.000')
        o=db.get(Order,oid)
        # Caixa is still missing, so it remains blocked.
        assert o.production.status==ProductionStatus.BLOCKED


def test_full_receipt_unblocks_then_production_loss_extra_and_completion(manager):
    data=bootstrap_entities(manager,costs=True,stock=0)
    oid=create_order(manager,data,price='60')
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    # One purchase with two materials.
    with SessionLocal() as db:
        mats={m.name:m.id for m in db.scalars(select(Material)).all()}
    form={'supplier_id':data['supplier_id'],'material_id':[mats['Caneca Cerâmica Branca 325ml'],mats['Caixa para caneca'],mats['Papel sulfite A4']],'quantity':['10','10','10'],'unit_price':['8','2.5','0.05'],'freight':'15'}
    r=manager.post('/purchases/new',data=form,follow_redirects=False); pid=r.headers['location'].split('/')[-1]
    with SessionLocal() as db:
        p=db.get(Purchase,pid); ids={i.material.name:i.id for i in p.items}
    manager.post(f'/purchases/{pid}/receipt',data={f"qty_{ids['Caneca Cerâmica Branca 325ml']}":'10',f"qty_{ids['Caixa para caneca']}":'10',f"qty_{ids['Papel sulfite A4']}":'10'})
    with SessionLocal() as db:
        o=db.get(Order,oid); prod_id=o.production.id; assert o.production.status==ProductionStatus.READY
    manager.post(f'/production/{prod_id}/start')
    manager.post(f'/production/{prod_id}/loss',data={'material_id':mats['Caneca Cerâmica Branca 325ml'],'quantity':'1','reason':'Erro de impressão'})
    manager.post(f'/production/{prod_id}/extra',data={'material_id':mats['Caixa para caneca'],'quantity':'1','reason':'Reposição de embalagem'})
    manager.post(f'/production/{prod_id}/complete')
    with SessionLocal() as db:
        p=db.get(ProductionOrder,prod_id)
        assert p.status==ProductionStatus.COMPLETED
        assert p.order.fulfillment.status==FulfillmentStatus.READY_FOR_PICKUP
        loss=db.scalar(select(InventoryTransaction).where(InventoryTransaction.source_id==prod_id,InventoryTransaction.tx_type==InventoryTxType.LOSS_OUT))
        assert loss is not None


def test_failed_delivery_preserves_commercial_status(manager):
    data=bootstrap_entities(manager,costs=True,stock=20)
    oid=create_order(manager,data,price='60',method='LOCAL_DELIVERY')
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    with SessionLocal() as db: prod_id=db.get(Order,oid).production.id
    manager.post(f'/production/{prod_id}/start'); manager.post(f'/production/{prod_id}/complete')
    with SessionLocal() as db:
        o=db.get(Order,oid); fid=o.fulfillment.id; assert o.fulfillment.status==FulfillmentStatus.READY_FOR_DELIVERY
    manager.post(f'/deliveries/{fid}/start'); manager.post(f'/deliveries/{fid}/fail',data={'reason':'Cliente ausente'})
    with SessionLocal() as db:
        o=db.get(Order,oid)
        assert o.commercial_status==CommercialStatus.CONFIRMED
        assert o.fulfillment.status==FulfillmentStatus.READY_FOR_DELIVERY
        assert o.fulfillment.attempts[-1].status=='FAILED'


def test_operational_pages_omit_finance_values(client):
    # Setup with manager first.
    client.post('/login',data={'email':'admin@criativas.local','password':'Criativas2024!'})
    data=bootstrap_entities(client,costs=True,stock=20)
    oid=create_order(client,data,price='60')
    client.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    client.post('/logout')
    client.post('/login',data={'email':'operacao@criativas.local','password':'Criativas2024!'})
    detail=client.get(f'/orders/{oid}').text
    dashboard=client.get('/dashboard').text
    assert 'Resumo financeiro' not in detail
    assert 'R$' not in detail
    assert 'Resultado' not in dashboard
    assert 'A receber' not in dashboard


def test_partial_payments(manager):
    data=bootstrap_entities(manager,costs=True,stock=20)
    oid=create_order(manager,data,price='60')
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    manager.post(f'/orders/{oid}/payment',data={'amount':'20','method':'PIX'})
    with SessionLocal() as db:
        o=db.get(Order,oid); paid,balance,status=order_payment_summary(o)
        assert status=='PARTIAL'; assert paid==Decimal('20.00'); assert balance==Decimal('40.00')


def test_promise_change_preserves_original(manager):
    data=bootstrap_entities(manager,costs=True,stock=20)
    oid=create_order(manager,data,price='60')
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    manager.post(f'/orders/{oid}/promise',data={'new_promised_at':'2026-09-01T16:00','reason':'CUSTOMER_REQUEST','notes':'Cliente solicitou nova data'})
    with SessionLocal() as db:
        o=db.get(Order,oid)
        assert len(o.promise_changes)==1
        assert o.current_promised_at is not None
        assert o.fulfillment.current_promised_at==o.current_promised_at


def test_payment_reversal_is_non_destructive(manager):
    data=bootstrap_entities(manager,costs=True,stock=20)
    oid=create_order(manager,data,price='60')
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    manager.post(f'/orders/{oid}/payment',data={'amount':'20','method':'PIX'})
    with SessionLocal() as db:
        o=db.get(Order,oid); pid=o.payments[0].id
    manager.post(f'/payments/{pid}/reverse',data={'reason':'Pagamento lançado em duplicidade'})
    with SessionLocal() as db:
        o=db.get(Order,oid)
        assert len(o.payments)==1 and o.payments[0].reversed is True
        paid,balance,status=order_payment_summary(o)
        assert paid==Decimal('0.00') and status=='UNPAID'


def test_cancel_releases_reservations_without_erasing_ledger(manager):
    data=bootstrap_entities(manager,costs=True,stock=20)
    oid=create_order(manager,data,price='60')
    manager.post(f'/orders/{oid}/confirm',data={'override_reason':''})
    with SessionLocal() as db:
        o=db.get(Order,oid); prod_id=o.production.id
        before=len(db.scalars(select(InventoryTransaction)).all())
    manager.post(f'/orders/{oid}/cancel',data={'reason':'Cliente desistiu'})
    with SessionLocal() as db:
        o=db.get(Order,oid)
        assert o.commercial_status==CommercialStatus.CANCELLED
        assert o.production.status==ProductionStatus.CANCELLED
        assert o.fulfillment.status==FulfillmentStatus.CANCELLED
        assert all(not r.active for r in o.production.reservations)
        after=len(db.scalars(select(InventoryTransaction)).all())
        assert after==before
