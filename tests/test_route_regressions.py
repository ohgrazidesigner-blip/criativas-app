def test_new_order_get_is_not_shadowed_by_order_id_route(manager):
    response = manager.get('/orders/new', follow_redirects=False)
    assert response.status_code == 200
    assert 'Novo pedido' in response.text
