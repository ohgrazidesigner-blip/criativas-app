from .main import app
from .enhancements import register_enhancements

register_enhancements(app)


def _promote_static_get_route(path: str, before_path: str) -> None:
    """Keep specific GET routes ahead of dynamic parameter routes.

    FastAPI/Starlette resolves routes in declaration order. Enhancements replace
    /orders/new after /orders/{order_id} already exists, so without this reorder
    the literal word 'new' is captured as an order_id and returns 404.
    """
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
