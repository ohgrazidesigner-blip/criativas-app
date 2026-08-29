from . import main as main_module
from .enhancements import register_enhancements
from .roadmap_2026 import install_roadmap_2026

app = main_module.app

register_enhancements(app)
install_roadmap_2026(main_module)


def _promote_static_get_route(path: str, before_path: str) -> None:
    """Keep specific GET routes ahead of dynamic parameter routes."""
    routes = app.router.routes
    target = next(
        (
            route
            for route in routes
            if getattr(route, "path", None) == path
            and "GET" in (getattr(route, "methods", None) or set())
        ),
        None,
    )
    if target is None:
        return

    routes.remove(target)
    insert_at = next(
        (
            index
            for index, route in enumerate(routes)
            if getattr(route, "path", None) == before_path
            and "GET" in (getattr(route, "methods", None) or set())
        ),
        len(routes),
    )
    routes.insert(insert_at, target)


_promote_static_get_route("/orders/new", "/orders/{order_id}")
