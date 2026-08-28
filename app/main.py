from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from .auth import verify_password, hash_password
from .db import Base, engine, get_db
from .domain import (
    D, DomainError, aggregated_dashboard, complete_fulfillment, complete_production, confirm_order,
    fail_delivery, inventory_available, inventory_on_hand, money, next_order_number, next_purchase_number,
    order_payment_summary, preview_order_economics, purchase_item_landed_cost, purchase_total,
    receive_purchase, record_extra, record_loss, record_payment, reserved_qty, start_delivery, start_production,
    change_promise, reverse_payment, cancel_order, reconcile,
)
from .models import (
    AuditEvent, CommercialStatus, Company, Customer, Expense, ExpenseKind, FulfillmentMethod,
    FulfillmentStatus, InventoryTransaction, InventoryTxType, Material, Order, OrderItem, PaymentMethod,
    Product, ProductionOrder, ProductionStatus, Purchase, PurchaseItem, Role, Supplier, User, Payment,
    TechnicalSheetItem, MaterialRequirement, ProductionException, GoodsReceipt,
)
from .seed import seed

BASE = Path(__file__).resolve().parent

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    from .db import SessionLocal
    with SessionLocal() as db:
        seed(db)
    yield

APP_ENV = os.getenv("APP_ENV", "production").lower()
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    if APP_ENV in {"development", "test"}:
        SESSION_SECRET = "dev-only-session-secret-change-me"
    else:
        raise RuntimeError("SESSION_SECRET is required in production")
if APP_ENV == "production" and len(SESSION_SECRET) < 32:
    raise RuntimeError("SESSION_SECRET must have at least 32 characters in production")

app = FastAPI(title="Criativas", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "1" if APP_ENV == "production" else "0") == "1",
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


def money_fmt(v):
    if v is None:
        return "—"
    v = D(v)
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def pct_fmt(v):
    if v is None:
        return "—"
    return f"{(D(v)*100):.1f}%".replace(".", ",")


templates.env.filters["money"] = money_fmt
templates.env.filters["pct"] = pct_fmt


def flash(request: Request, message: str, kind: str = "info"):
    request.session["flash"] = {"message": message, "kind": kind}


def pop_flash(request: Request):
    return request.session.pop("flash", None)


def current_user(request: Request, db: Session) -> User | None:
    uid = request.session.get("user_id")
    return db.get(User, uid) if uid else None


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not user or not user.active:
        raise HTTPException(status_code=401)
    return user


def require_manager(user: User = Depends(require_user)) -> User:
    if user.role != Role.MANAGER:
        raise HTTPException(status_code=403)
    return user


def render(request: Request, name: str, user: User | None = None, **ctx):
    return templates.TemplateResponse(request=request, name=name, context={"user": user, "flash": pop_flash(request), **ctx})


@app.exception_handler(401)
def unauthorized(request: Request, exc):
    return RedirectResponse("/login", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok", "service": "criativas"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not verify_password(password, user.password_hash):
        flash(request, "E-mail ou senha inválidos.", "danger")
        return RedirectResponse("/login", status_code=303)
    request.session["user_id"] = user.id
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/")
def root(request: Request):
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return render(request, "account.html", user=user)


@app.post("/account/password")
def account_password(request: Request, current_password: str = Form(...), new_password: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not verify_password(current_password, user.password_hash):
        flash(request, "Senha atual incorreta.", "danger"); return RedirectResponse("/account", 303)
    if len(new_password) < 10:
        flash(request, "A nova senha precisa ter pelo menos 10 caracteres.", "danger"); return RedirectResponse("/account", 303)
    user.password_hash=hash_password(new_password); db.commit(); flash(request, "Senha alterada.", "success"); return RedirectResponse("/account", 303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if user.role == Role.MANAGER:
        kpis = aggregated_dashboard(db, user.company_id)
    else:
        kpis = None
    productions = db.scalars(
        select(ProductionOrder).join(Order).where(Order.company_id == user.company_id, ProductionOrder.status.in_([ProductionStatus.BLOCKED, ProductionStatus.READY, ProductionStatus.IN_PROGRESS])).order_by(Order.current_promised_at.asc().nulls_last()).limit(8)
    ).all()
    deliveries = db.scalars(
        select(Order).where(Order.company_id == user.company_id, Order.commercial_status == CommercialStatus.CONFIRMED).order_by(Order.current_promised_at.asc().nulls_last()).limit(8)
    ).all()
    return render(request, "dashboard.html", user=user, kpis=kpis, productions=productions, deliveries=deliveries)


@app.get("/orders", response_class=HTMLResponse)
def orders(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.scalars(select(Order).where(Order.company_id == user.company_id).order_by(Order.created_at.desc())).all()
    return render(request, "orders.html", user=user, orders=rows, payment_summary=order_payment_summary)


@app.get("/orders/new", response_class=HTMLResponse)
def order_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    customers = db.scalars(select(Customer).where(Customer.company_id == user.company_id).order_by(Customer.name)).all()
    products = db.scalars(select(Product).where(Product.company_id == user.company_id, Product.active.is_(True)).order_by(Product.name)).all()
    return render(request, "order_form.html", user=user, customers=customers, products=products, payment_methods=list(PaymentMethod), methods=list(FulfillmentMethod))


@app.post("/orders/new")
def order_create(
    request: Request,
    customer_id: str = Form(...), product_id: list[str] = Form(...), quantity: list[Decimal] = Form(...), unit_price: list[Decimal] = Form(...),
    discount: Decimal = Form(0), freight_charged: Decimal = Form(0), company_freight_cost: Decimal = Form(0),
    expected_payment_method: PaymentMethod = Form(PaymentMethod.PIX), fulfillment_method: FulfillmentMethod = Form(FulfillmentMethod.PICKUP),
    promised_at: str = Form(""), priority: str = Form("NORMAL"), db: Session = Depends(get_db), user: User = Depends(require_user),
):
    customer = db.get(Customer, customer_id)
    if not customer or customer.company_id != user.company_id:
        raise HTTPException(400)
    dt = datetime.fromisoformat(promised_at).replace(tzinfo=timezone.utc) if promised_at else None
    order = Order(company_id=user.company_id, number=next_order_number(db), customer_id=customer_id, discount=discount, freight_charged=freight_charged, company_freight_cost=company_freight_cost, expected_payment_method=expected_payment_method, fulfillment_method=fulfillment_method, original_promised_at=dt, current_promised_at=dt, priority=priority)
    db.add(order); db.flush()
    count = 0
    for pid, q, price in zip(product_id, quantity, unit_price):
        if D(q) <= 0:
            continue
        product = db.get(Product, pid)
        if not product or product.company_id != user.company_id:
            continue
        db.add(OrderItem(order_id=order.id, product_id=pid, qty=q, unit_price=price)); count += 1
    if count == 0:
        db.rollback(); flash(request, "Adicione ao menos um item válido.", "danger"); return RedirectResponse("/orders/new", status_code=303)
    db.commit()
    flash(request, f"Pedido #{order.number} criado como rascunho.", "success")
    return RedirectResponse(f"/orders/{order.id}", status_code=303)

@app.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail(request: Request, order_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    order = db.scalar(select(Order).options(selectinload(Order.items).selectinload(OrderItem.product).selectinload(Product.technical_items), selectinload(Order.payments), selectinload(Order.snapshots), selectinload(Order.production), selectinload(Order.fulfillment)).where(Order.id == order_id, Order.company_id == user.company_id))
    if not order: raise HTTPException(404)
    econ = preview_order_economics(db, order) if user.role == Role.MANAGER else None
    paid, balance, pay_status = order_payment_summary(order)
    return render(request, "order_detail.html", user=user, order=order, econ=econ, paid=paid, balance=balance, pay_status=pay_status, payment_methods=list(PaymentMethod))


@app.post("/orders/{order_id}/confirm")
def order_confirm(request: Request, order_id: str, override_reason: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    order = db.scalar(select(Order).options(selectinload(Order.items).selectinload(OrderItem.product).selectinload(Product.technical_items).selectinload(TechnicalSheetItem.material), selectinload(Order.snapshots)).where(Order.id == order_id, Order.company_id == user.company_id))
    if not order: raise HTTPException(404)
    try:
        confirm_order(db, order, user, override_reason)
        db.commit(); flash(request, "Pedido confirmado e consequências operacionais criadas.", "success")
    except DomainError as e:
        db.rollback(); flash(request, str(e), "danger")
    return RedirectResponse(f"/orders/{order_id}", status_code=303)


@app.post("/orders/{order_id}/payment")
def order_payment(request: Request, order_id: str, amount: Decimal = Form(...), method: PaymentMethod = Form(...), db: Session = Depends(get_db), user: User = Depends(require_manager)):
    order = db.get(Order, order_id)
    if not order or order.company_id != user.company_id: raise HTTPException(404)
    try:
        record_payment(db, order, amount, method, user); db.commit(); flash(request, "Pagamento registrado.", "success")
    except DomainError as e:
        db.rollback(); flash(request, str(e), "danger")
    return RedirectResponse(f"/orders/{order_id}", status_code=303)


@app.post("/orders/{order_id}/promise")
def order_promise_change(request: Request, order_id: str, new_promised_at: str = Form(...), reason: str = Form(...), notes: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_manager)):
    order=db.get(Order,order_id)
    if not order or order.company_id!=user.company_id: raise HTTPException(404)
    dt=datetime.fromisoformat(new_promised_at).replace(tzinfo=timezone.utc)
    change_promise(db,order,dt,reason,notes,user);db.commit();flash(request,"Data combinada atualizada com histórico preservado.","success")
    return RedirectResponse(f"/orders/{order_id}",303)


@app.post("/orders/{order_id}/cancel")
def order_cancel(request: Request, order_id: str, reason: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_manager)):
    order=db.get(Order,order_id)
    if not order or order.company_id!=user.company_id: raise HTTPException(404)
    cancel_order(db,order,user,reason);db.commit();flash(request,"Pedido cancelado. Movimentos físicos e snapshots históricos foram preservados.","warning")
    return RedirectResponse(f"/orders/{order_id}",303)


@app.post("/payments/{payment_id}/reverse")
def payment_reverse(request: Request, payment_id: str, reason: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_manager)):
    payment=db.get(Payment,payment_id)
    if not payment or payment.order.company_id!=user.company_id: raise HTTPException(404)
    oid=payment.order_id;reverse_payment(db,payment,user,reason);db.commit();flash(request,"Pagamento estornado por reversão, sem exclusão destrutiva.","warning")
    return RedirectResponse(f"/orders/{oid}",303)


@app.get("/production", response_class=HTMLResponse)
def production_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.scalars(select(ProductionOrder).join(Order).where(Order.company_id == user.company_id).order_by(ProductionOrder.status, Order.current_promised_at.asc().nulls_last())).all()
    return render(request, "production.html", user=user, productions=rows)


@app.get("/production/{production_id}", response_class=HTMLResponse)
def production_detail(request: Request, production_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    p = db.scalar(select(ProductionOrder).options(selectinload(ProductionOrder.requirements).selectinload(MaterialRequirement.material), selectinload(ProductionOrder.reservations), selectinload(ProductionOrder.exceptions).selectinload(ProductionException.material), selectinload(ProductionOrder.order).selectinload(Order.customer)).where(ProductionOrder.id == production_id))
    if not p or p.order.company_id != user.company_id: raise HTTPException(404)
    reqs=[]
    for r in p.requirements:
        res=next((x for x in p.reservations if x.material_id==r.material_id and x.active),None)
        rq=D(res.qty) if res else D(0); missing=max(D(r.required_qty)-rq,D(0))
        reqs.append({"req":r,"reserved":rq,"missing":missing})
    return render(request, "production_detail.html", user=user, p=p, reqs=reqs)


@app.post("/production/{production_id}/start")
def production_start(request: Request, production_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    p=db.get(ProductionOrder,production_id)
    if not p or p.order.company_id!=user.company_id: raise HTTPException(404)
    try: start_production(db,p,user); db.commit(); flash(request,"Produção iniciada.","success")
    except DomainError as e: db.rollback(); flash(request,str(e),"danger")
    return RedirectResponse(f"/production/{production_id}",303)


@app.post("/production/{production_id}/loss")
def production_loss(request: Request, production_id: str, material_id: str = Form(...), quantity: Decimal = Form(...), reason: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_user)):
    p=db.get(ProductionOrder,production_id)
    if not p or p.order.company_id!=user.company_id: raise HTTPException(404)
    try: record_loss(db,p,material_id,quantity,reason,user); db.commit(); flash(request,"Perda registrada e reposição reavaliada.","success")
    except DomainError as e: db.rollback(); flash(request,str(e),"danger")
    return RedirectResponse(f"/production/{production_id}",303)


@app.post("/production/{production_id}/extra")
def production_extra(request: Request, production_id: str, material_id: str = Form(...), quantity: Decimal = Form(...), reason: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    p=db.get(ProductionOrder,production_id)
    if not p or p.order.company_id!=user.company_id: raise HTTPException(404)
    try: record_extra(db,p,material_id,quantity,reason,user); db.commit(); flash(request,"Consumo extra registrado.","success")
    except DomainError as e: db.rollback(); flash(request,str(e),"danger")
    return RedirectResponse(f"/production/{production_id}",303)


@app.post("/production/{production_id}/complete")
def production_complete(request: Request, production_id: str, db: Session = Depends(get_db), user: User = Depends(require_user)):
    p=db.get(ProductionOrder,production_id)
    if not p or p.order.company_id!=user.company_id: raise HTTPException(404)
    try: complete_production(db,p,user); db.commit(); flash(request,"Produção concluída e fulfillment liberado automaticamente.","success")
    except DomainError as e: db.rollback(); flash(request,str(e),"danger")
    return RedirectResponse(f"/production/{production_id}",303)


@app.get("/inventory", response_class=HTMLResponse)
def inventory(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    mats=db.scalars(select(Material).where(Material.company_id==user.company_id).order_by(Material.name)).all()
    rows=[{"material":m,"on_hand":inventory_on_hand(db,m.id),"reserved":reserved_qty(db,m.id),"available":inventory_available(db,m.id)} for m in mats]
    txs=db.scalars(select(InventoryTransaction).where(InventoryTransaction.company_id==user.company_id).order_by(InventoryTransaction.created_at.desc()).limit(50)).all()
    return render(request,"inventory.html",user=user,rows=rows,txs=txs)


@app.post("/inventory/adjust")
def inventory_adjust(request: Request, material_id: str = Form(...), quantity: Decimal = Form(...), reason: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_manager)):
    m=db.get(Material,material_id)
    if not m or m.company_id!=user.company_id: raise HTTPException(404)
    q=D(quantity)
    if q==0: flash(request,"Ajuste não pode ser zero.","danger")
    else:
        db.add(InventoryTransaction(company_id=user.company_id,material_id=m.id,tx_type=InventoryTxType.ADJUSTMENT_IN if q>0 else InventoryTxType.ADJUSTMENT_OUT,qty_signed=q,reason=reason,source_type="ManualAdjustment")); db.commit(); flash(request,"Ajuste de estoque registrado no ledger.","success")
    return RedirectResponse("/inventory",303)


@app.get("/purchases", response_class=HTMLResponse)
def purchases(request: Request, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    rows=db.scalars(select(Purchase).where(Purchase.company_id==user.company_id).order_by(Purchase.created_at.desc())).all()
    return render(request,"purchases.html",user=user,purchases=rows,purchase_total=purchase_total)


@app.get("/purchases/new", response_class=HTMLResponse)
def purchase_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    suppliers=db.scalars(select(Supplier).where(Supplier.company_id==user.company_id).order_by(Supplier.name)).all(); mats=db.scalars(select(Material).where(Material.company_id==user.company_id).order_by(Material.name)).all()
    return render(request,"purchase_form.html",user=user,suppliers=suppliers,materials=mats)


@app.post("/purchases/new")
def purchase_create(request: Request, supplier_id: str = Form(...), material_id: list[str] = Form(...), quantity: list[Decimal] = Form(...), unit_price: list[Decimal] = Form(...), freight: Decimal = Form(0), db: Session = Depends(get_db), user: User = Depends(require_manager)):
    supplier=db.get(Supplier,supplier_id)
    if not supplier or supplier.company_id!=user.company_id: raise HTTPException(400)
    p=Purchase(company_id=user.company_id,number=next_purchase_number(db),supplier_id=supplier_id,freight=freight,status="CONFIRMED");db.add(p);db.flush()
    for mid,q,price in zip(material_id,quantity,unit_price):
        if D(q)>0: db.add(PurchaseItem(purchase_id=p.id,material_id=mid,qty=q,unit_price=price))
    db.commit();flash(request,"Compra confirmada. O estoque não muda até o recebimento físico.","success");return RedirectResponse(f"/purchases/{p.id}",303)


@app.get("/purchases/{purchase_id}", response_class=HTMLResponse)
def purchase_detail(request: Request,purchase_id:str,db:Session=Depends(get_db),user:User=Depends(require_manager)):
    p=db.scalar(select(Purchase).options(selectinload(Purchase.items).selectinload(PurchaseItem.material),selectinload(Purchase.receipts).selectinload(GoodsReceipt.items)).where(Purchase.id==purchase_id,Purchase.company_id==user.company_id))
    if not p: raise HTTPException(404)
    received={i.id:D(0) for i in p.items}
    for r in p.receipts:
        for ri in r.items: received[ri.purchase_item_id]=received.get(ri.purchase_item_id,D(0))+D(ri.qty)
    rows=[{"item":i,"received":received[i.id],"pending":max(D(i.qty)-received[i.id],D(0)),"landed":purchase_item_landed_cost(p,i)} for i in p.items]
    return render(request,"purchase_detail.html",user=user,p=p,rows=rows,total=purchase_total(p))


@app.post("/purchases/{purchase_id}/receipt")
async def purchase_receipt(request: Request,purchase_id:str,db:Session=Depends(get_db),user:User=Depends(require_manager)):
    p=db.scalar(select(Purchase).options(selectinload(Purchase.items).selectinload(PurchaseItem.material)).where(Purchase.id==purchase_id,Purchase.company_id==user.company_id))
    if not p: raise HTTPException(404)
    form=await request.form(); quantities={i.id:D(form.get(f"qty_{i.id}",0)) for i in p.items}
    try: receive_purchase(db,p,quantities,user);db.commit();flash(request,"Recebimento registrado. Estoque e reservas foram recalculados.","success")
    except DomainError as e: db.rollback();flash(request,str(e),"danger")
    return RedirectResponse(f"/purchases/{purchase_id}",303)


@app.get("/deliveries", response_class=HTMLResponse)
def deliveries(request:Request,db:Session=Depends(get_db),user:User=Depends(require_user)):
    orders=db.scalars(select(Order).where(Order.company_id==user.company_id,Order.commercial_status==CommercialStatus.CONFIRMED).order_by(Order.current_promised_at.asc().nulls_last())).all()
    return render(request,"deliveries.html",user=user,orders=[o for o in orders if o.fulfillment])


@app.post("/deliveries/{fulfillment_id}/start")
def delivery_start(request:Request,fulfillment_id:str,db:Session=Depends(get_db),user:User=Depends(require_user)):
    from .models import Fulfillment
    f=db.get(Fulfillment,fulfillment_id)
    if not f or f.order.company_id!=user.company_id: raise HTTPException(404)
    try:start_delivery(db,f,user);db.commit();flash(request,"Entrega iniciada.","success")
    except DomainError as e:db.rollback();flash(request,str(e),"danger")
    return RedirectResponse("/deliveries",303)


@app.post("/deliveries/{fulfillment_id}/fail")
def delivery_fail(request:Request,fulfillment_id:str,reason:str=Form("Cliente ausente"),db:Session=Depends(get_db),user:User=Depends(require_user)):
    from .models import Fulfillment
    f=db.get(Fulfillment,fulfillment_id)
    if not f or f.order.company_id!=user.company_id: raise HTTPException(404)
    try:fail_delivery(db,f,reason,user);db.commit();flash(request,"Tentativa falhou. Pedido continua Confirmado e fulfillment voltou para Pronto para entrega.","warning")
    except DomainError as e:db.rollback();flash(request,str(e),"danger")
    return RedirectResponse("/deliveries",303)


@app.post("/deliveries/{fulfillment_id}/complete")
def delivery_complete(request:Request,fulfillment_id:str,db:Session=Depends(get_db),user:User=Depends(require_user)):
    from .models import Fulfillment
    f=db.get(Fulfillment,fulfillment_id)
    if not f or f.order.company_id!=user.company_id: raise HTTPException(404)
    try:complete_fulfillment(db,f,user);db.commit();flash(request,"Fulfillment concluído.","success")
    except DomainError as e:db.rollback();flash(request,str(e),"danger")
    return RedirectResponse("/deliveries",303)


@app.get("/catalog", response_class=HTMLResponse)
def catalog(request:Request,db:Session=Depends(get_db),user:User=Depends(require_user)):
    products=db.scalars(select(Product).where(Product.company_id==user.company_id).order_by(Product.name)).all();materials=db.scalars(select(Material).where(Material.company_id==user.company_id).order_by(Material.name)).all()
    return render(request,"catalog.html",user=user,products=products,materials=materials)


@app.post("/materials/{material_id}/cost")
def material_cost(request:Request,material_id:str,cost:Decimal=Form(...),min_stock:Decimal=Form(0),db:Session=Depends(get_db),user:User=Depends(require_manager)):
    m=db.get(Material,material_id)
    if not m or m.company_id!=user.company_id:raise HTTPException(404)
    m.current_cost=cost;m.min_stock=min_stock;db.commit();flash(request,"Custo de referência atualizado para vendas futuras.","success");return RedirectResponse("/catalog",303)


@app.post("/products")
async def product_create(request: Request, db: Session = Depends(get_db), user: User = Depends(require_manager)):
    form=await request.form()
    name=str(form.get("name","")).strip(); base_price=D(form.get("base_price",0)); labor_minutes=int(form.get("labor_minutes",0)); loss=D(form.get("expected_loss_rate",0))/100; lead=int(form.get("standard_lead_time_days",3))
    if not name: flash(request,"Nome do produto é obrigatório.","danger"); return RedirectResponse("/catalog",303)
    p=Product(company_id=user.company_id,name=name,base_price=base_price,labor_minutes=labor_minutes,expected_loss_rate=loss,standard_lead_time_days=lead);db.add(p);db.flush()
    for idx in range(1,4):
        mid=form.get(f"material_{idx}"); q=D(form.get(f"qty_{idx}",0))
        if mid and q>0:
            db.add(TechnicalSheetItem(product_id=p.id,material_id=str(mid),qty=q,version=1))
    db.commit();flash(request,"Produto e ficha técnica criados.","success");return RedirectResponse("/catalog",303)


@app.get("/expenses", response_class=HTMLResponse)
def expenses(request:Request,db:Session=Depends(get_db),user:User=Depends(require_manager)):
    rows=db.scalars(select(Expense).where(Expense.company_id==user.company_id).order_by(Expense.occurred_at.desc())).all();return render(request,"expenses.html",user=user,expenses=rows,kinds=list(ExpenseKind))


@app.post("/expenses")
def expense_create(request:Request,kind:ExpenseKind=Form(...),description:str=Form(...),amount:Decimal=Form(...),db:Session=Depends(get_db),user:User=Depends(require_manager)):
    db.add(Expense(company_id=user.company_id,kind=kind,description=description,amount=amount));db.commit();flash(request,"Movimento financeiro registrado. Retirada pessoal não altera margem de produto.","success");return RedirectResponse("/expenses",303)


@app.get("/people", response_class=HTMLResponse)
def people(request:Request,db:Session=Depends(get_db),user:User=Depends(require_user)):
    customers=db.scalars(select(Customer).where(Customer.company_id==user.company_id).order_by(Customer.name)).all();suppliers=db.scalars(select(Supplier).where(Supplier.company_id==user.company_id).order_by(Supplier.name)).all();return render(request,"people.html",user=user,customers=customers,suppliers=suppliers)


@app.post("/customers")
def customer_create(request:Request,name:str=Form(...),phone:str=Form(""),email:str=Form(""),db:Session=Depends(get_db),user:User=Depends(require_user)):
    db.add(Customer(company_id=user.company_id,name=name,phone=phone or None,email=email or None));db.commit();flash(request,"Cliente cadastrado.","success");return RedirectResponse("/people",303)


@app.post("/suppliers")
def supplier_create(request:Request,name:str=Form(...),contact:str=Form(""),db:Session=Depends(get_db),user:User=Depends(require_manager)):
    db.add(Supplier(company_id=user.company_id,name=name,contact=contact or None));db.commit();flash(request,"Fornecedor cadastrado.","success");return RedirectResponse("/people",303)


@app.get("/settings", response_class=HTMLResponse)
def settings(request:Request,db:Session=Depends(get_db),user:User=Depends(require_manager)):
    company=db.get(Company,user.company_id);events=db.scalars(select(AuditEvent).where(AuditEvent.company_id==user.company_id).order_by(AuditEvent.created_at.desc()).limit(30)).all();issues=reconcile(db,user.company_id);return render(request,"settings.html",user=user,company=company,events=events,issues=issues)


@app.post("/settings")
def settings_save(request:Request,target_margin:Decimal=Form(...),hourly_value:Decimal=Form(...),allocable_fixed_monthly:Decimal=Form(...),productive_minutes_month:int=Form(...),pix_fee_rate:Decimal=Form(...),card_fee_rate:Decimal=Form(...),risk_window_hours:int=Form(...),db:Session=Depends(get_db),user:User=Depends(require_manager)):
    c=db.get(Company,user.company_id);c.target_margin=target_margin/100 if target_margin>1 else target_margin;c.hourly_value=hourly_value;c.allocable_fixed_monthly=allocable_fixed_monthly;c.productive_minutes_month=productive_minutes_month;c.pix_fee_rate=pix_fee_rate/100 if pix_fee_rate>1 else pix_fee_rate;c.card_fee_rate=card_fee_rate/100 if card_fee_rate>1 else card_fee_rate;c.risk_window_hours=risk_window_hours;db.commit();flash(request,"Configurações atualizadas. Alterações de custo valem para vendas futuras, snapshots confirmados permanecem imutáveis.","success");return RedirectResponse("/settings",303)
