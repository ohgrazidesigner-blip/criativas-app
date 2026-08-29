from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .db import get_db
from .domain import D
from .models import Material, Product, TechnicalSheetItem, User
from .roadmap_2026 import (
    _category_options,
    _clean,
    _product_category_map,
    _remove_route,
    _set_product_category,
    _technical_rows_from_form,
)


def install_versioned_product_editor(main_module) -> None:
    app = main_module.app
    if getattr(app.state, "roadmap_2026_versioned_products_installed", False):
        return

    _remove_route(app, "/products/{product_id}/edit", "GET")
    _remove_route(app, "/products/{product_id}/edit", "POST")

    @app.get("/products/{product_id}/edit", response_class=HTMLResponse)
    def product_edit_2026(
        request: Request,
        product_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        product = db.scalar(
            select(Product)
            .options(selectinload(Product.technical_items).selectinload(TechnicalSheetItem.material))
            .where(Product.id == product_id, Product.company_id == user.company_id)
        )
        if not product:
            raise HTTPException(404)
        materials = db.scalars(
            select(Material)
            .where(Material.company_id == user.company_id, Material.active.is_(True))
            .order_by(Material.name)
        ).all()
        versions = [row.version for row in product.technical_items]
        current_version = max(versions or [1])
        current_items = [row for row in product.technical_items if row.version == current_version]
        return main_module.render(
            request,
            "product_form_2026.html",
            user=user,
            product=product,
            technical_items=sorted(current_items, key=lambda row: row.material.name.casefold()),
            materials=materials,
            current_category=_product_category_map(db, user.company_id).get(product.id, ""),
            categories=_category_options(db, user.company_id),
        )

    @app.post("/products/{product_id}/edit")
    async def product_update_2026(
        request: Request,
        product_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        product = db.scalar(
            select(Product)
            .options(selectinload(Product.technical_items))
            .where(Product.id == product_id, Product.company_id == user.company_id)
        )
        if not product:
            raise HTTPException(404)
        form = await request.form()
        name = _clean(form.get("name"))
        if not name:
            main_module.flash(request, "Nome do produto é obrigatório.", "danger")
            return RedirectResponse(f"/products/{product_id}/edit", 303)
        technical_rows, error = _technical_rows_from_form(form)
        if error:
            main_module.flash(request, error, "danger")
            return RedirectResponse(f"/products/{product_id}/edit", 303)

        product.name = name
        product.base_price = D(form.get("base_price") or 0)
        product.labor_minutes = int(form.get("labor_minutes") or 0)
        product.expected_loss_rate = D(form.get("expected_loss_rate") or 0) / 100
        product.standard_lead_time_days = int(form.get("standard_lead_time_days") or 3)
        product.active = str(form.get("active") or "1") == "1"
        _set_product_category(db, product, str(form.get("category") or ""))

        next_version = max([row.version for row in product.technical_items] or [0]) + 1
        for material_id, qty in technical_rows:
            material = db.get(Material, material_id)
            if material and material.company_id == user.company_id:
                db.add(
                    TechnicalSheetItem(
                        product_id=product.id,
                        material_id=material.id,
                        qty=qty,
                        version=next_version,
                    )
                )
        db.commit()
        main_module.flash(
            request,
            "Produto atualizado. A versão anterior da ficha técnica foi preservada para auditoria e histórico.",
            "success",
        )
        return RedirectResponse("/catalog", 303)

    app.state.roadmap_2026_versioned_products_installed = True
