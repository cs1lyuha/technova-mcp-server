from technova_mcp.db import connect, seed


def test_seed_is_deterministic_and_idempotent():
    a, b = connect(), connect()
    seed(a)
    seed(b)
    seed(b)
    rows_a = [tuple(r) for r in a.execute("SELECT * FROM orders ORDER BY id")]
    rows_b = [tuple(r) for r in b.execute("SELECT * FROM orders ORDER BY id")]
    assert rows_a == rows_b and len(rows_a) == 24


def test_every_order_has_items_and_valid_status():
    c = connect()
    seed(c)
    assert c.execute("SELECT COUNT(*) FROM orders o WHERE NOT EXISTS "
                     "(SELECT 1 FROM order_items i WHERE i.order_id = o.id)").fetchone()[0] == 0
    statuses = {r[0] for r in c.execute("SELECT DISTINCT status FROM orders")}
    assert statuses <= {"new", "paid", "shipped", "delivered", "cancelled"}
