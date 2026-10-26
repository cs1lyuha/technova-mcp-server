"""Logica de business, separată de MCP: se poate testa și refolosi (API REST, CLI).

Toate funcțiile primesc un `Principal`, iar verificarea drepturilor se face
aici, aproape de date. Stratul MCP nu e singura barieră.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from technova_mcp.auth import Principal
from technova_mcp.db import TODAY

RETURN_WINDOW_DAYS = 30
NON_RETURNABLE = {"EAR-APP": "căștile in-ear desigilate nu se returnează (pentru defecte: garanție)"}
TRANSITIONS = {
    "new": {"paid", "cancelled"},
    "paid": {"shipped", "cancelled"},
    "shipped": {"delivered"},
    "delivered": set(),
    "cancelled": set(),
}
UNTRUSTED_OPEN, UNTRUSTED_CLOSE = "<untrusted_customer_text>", "</untrusted_customer_text>"


class NotFound(Exception):
    pass


class PolicyError(Exception):
    pass


def untrusted(text: str) -> str:
    """Textul scris de clienți e marcat ca date, nu ca instrucțiuni (apărare împotriva prompt injection)."""
    if not text:
        return ""
    cleaned = text.replace(UNTRUSTED_OPEN, "").replace(UNTRUSTED_CLOSE, "")
    return f"{UNTRUSTED_OPEN}{cleaned}{UNTRUSTED_CLOSE}"


class TechNovaService:
    def __init__(self, conn: sqlite3.Connection, today: date = TODAY) -> None:
        self.conn = conn
        self.today = today

    # ---------- citire ----------

    def search_products(self, query: str, category: str | None = None, max_results: int = 10) -> list[dict]:
        sql = "SELECT sku, name, category, price, stock FROM products WHERE (name LIKE ? OR sku LIKE ?)"
        params: list[Any] = [f"%{query}%", f"%{query}%"]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY name LIMIT ?"
        params.append(max_results)
        return [dict(r) | {"in_stock": r["stock"] > 0} for r in self.conn.execute(sql, params)]

    def _order_row(self, principal: Principal, order_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        # aceeași eroare pentru „nu există” și „nu e a ta”: nu dezvăluim ce comenzi există
        if row is None or not principal.can_see(row["customer_idno"]):
            raise NotFound(f"comanda {order_id} nu a fost găsită")
        return row

    def get_order(self, principal: Principal, order_id: str) -> dict:
        row = self._order_row(principal, order_id)
        items = [dict(r) for r in self.conn.execute(
            "SELECT i.sku, p.name, i.qty, i.unit_price FROM order_items i JOIN products p USING (sku) "
            "WHERE i.order_id = ? ORDER BY i.sku", (order_id,))]
        returns = [dict(r) for r in self.conn.execute(
            "SELECT id, sku, qty, status FROM returns WHERE order_id = ? ORDER BY id", (order_id,))]
        return {
            "id": row["id"], "customer_idno": row["customer_idno"], "status": row["status"],
            "created_on": row["created_on"], "delivered_on": row["delivered_on"],
            "total": round(sum(i["qty"] * i["unit_price"] for i in items), 2),
            "items": items, "returns": returns,
            "customer_note": untrusted(row["note"]),
            "return_deadline": self._deadline(row),
        }

    def list_orders(self, principal: Principal, customer_idno: str | None = None, status: str | None = None,
                    limit: int = 20) -> list[dict]:
        if not principal.can("read_all"):
            if customer_idno and customer_idno != principal.customer_idno:
                raise NotFound("nu există comenzi vizibile pentru acest client")
            customer_idno = principal.customer_idno
        sql, params = "SELECT id, customer_idno, status, created_on FROM orders WHERE 1=1", []
        if customer_idno:
            sql += " AND customer_idno = ?"
            params.append(customer_idno)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_on DESC, id LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def _deadline(self, row: sqlite3.Row) -> str | None:
        if not row["delivered_on"]:
            return None
        return (date.fromisoformat(row["delivered_on"]) + timedelta(days=RETURN_WINDOW_DAYS)).isoformat()

    # ---------- scriere ----------

    def create_return(self, principal: Principal, order_id: str, sku: str, qty: int, reason: str,
                      idempotency_key: str | None = None) -> dict:
        principal.require("create_return")
        row = self._order_row(principal, order_id)
        if idempotency_key:  # același apel repetat (retry al agentului) → aceeași cerere, nu una nouă
            seen = self.conn.execute("SELECT * FROM returns WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if seen:
                if (seen["order_id"], seen["sku"], seen["qty"]) != (order_id, sku, qty):
                    raise PolicyError("cheia de idempotență a fost deja folosită pentru alt retur")
                return {"return_id": seen["id"], "status": seen["status"], "duplicate": True}
        if row["status"] != "delivered":
            raise PolicyError(f"returul se poate cere doar pentru comenzi livrate (status: {row['status']})")
        deadline = date.fromisoformat(self._deadline(row))
        if self.today > deadline:
            raise PolicyError(f"termenul de retur de {RETURN_WINDOW_DAYS} de zile a expirat pe {deadline}")
        if sku in NON_RETURNABLE:
            raise PolicyError(NON_RETURNABLE[sku])
        ordered = self.conn.execute("SELECT COALESCE(SUM(qty), 0) FROM order_items WHERE order_id = ? AND sku = ?",
                                    (order_id, sku)).fetchone()[0]
        if ordered == 0:
            raise PolicyError(f"produsul {sku} nu face parte din comanda {order_id}")
        already = self.conn.execute(
            "SELECT COALESCE(SUM(qty), 0) FROM returns WHERE order_id = ? AND sku = ? AND status != 'rejected'",
            (order_id, sku)).fetchone()[0]
        if qty > ordered - already:
            raise PolicyError(f"se pot returna cel mult {ordered - already} buc. din {sku}")
        cur = self.conn.execute(
            "INSERT INTO returns (order_id, sku, qty, reason, created_by, created_on, idempotency_key) "
            "VALUES (?,?,?,?,?,?,?)",
            (order_id, sku, qty, reason, principal.name, self.today.isoformat(), idempotency_key))
        self.conn.commit()
        return {"return_id": cur.lastrowid, "status": "pending", "duplicate": False,
                "message": "Cererea de retur a fost creată și așteaptă verificarea produsului la depozit."}

    def update_order_status(self, principal: Principal, order_id: str, status: str, note: str = "") -> dict:
        principal.require("update_status")
        row = self._order_row(principal, order_id)
        current = row["status"]
        if status == current:
            return {"id": order_id, "status": current, "changed": False}
        if status not in TRANSITIONS[current]:
            allowed = ", ".join(sorted(TRANSITIONS[current])) or "niciuna"
            raise PolicyError(f"tranziție nepermisă {current} → {status} (permise: {allowed})")
        delivered = self.today.isoformat() if status == "delivered" else row["delivered_on"]
        self.conn.execute("UPDATE orders SET status = ?, delivered_on = ? WHERE id = ?", (status, delivered, order_id))
        self.conn.commit()
        return {"id": order_id, "status": status, "changed": True, "previous": current}
