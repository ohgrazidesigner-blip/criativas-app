from __future__ import annotations

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .db import get_db
from .models import Material, Product, TechnicalSheetItem, User
from .real_costs import REAL_PRODUCT_COST_REFERENCES


def _remove_route(app, path: str, method: str) -> None:
    for route in list(app.router.routes):
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            app.router.routes.remove(route)


def install_catalog_enhancements(main_module) -> None:
    app = main_module.app
    if getattr(app.state, "catalog_enhancements_installed", False):
        return
    # Criativas 2026 owns the catalog route when Phase 1 is installed. The
    # legacy refinement remains available for the preserved 2024 baseline but
    # must not replace the 2026 route during seed()/startup.
    if getattr(app.state, "roadmap_2026_phase1_installed", False):
        return

    _remove_route(app, "/catalog", "GET")

    async def catalog(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_user),
    ):
        products = db.scalars(
            select(Product)
            .where(Product.company_id == user.company_id)
            .options(selectinload(Product.technical_items).selectinload(TechnicalSheetItem.material))
            .order_by(Product.name)
        ).all()
        materials = db.scalars(
            select(Material).where(Material.company_id == user.company_id).order_by(Material.name)
        ).all()
        return main_module.render(
            request,
            "catalog.html",
            user=user,
            products=products,
            materials=materials,
            reference_costs=REAL_PRODUCT_COST_REFERENCES,
        )

    app.add_api_route("/catalog", catalog, methods=["GET"], response_class=HTMLResponse, name="enhanced_catalog")
    app.state.catalog_enhancements_installed = True
