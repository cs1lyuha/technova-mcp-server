import pytest

from technova_mcp.auth import TokenStore
from technova_mcp.db import connect, seed
from technova_mcp.service import TechNovaService

AGRONORD = "1003600012345"


@pytest.fixture
def conn():
    c = connect()
    seed(c)
    return c


@pytest.fixture
def svc(conn):
    return TechNovaService(conn)


@pytest.fixture
def tokens():
    s = TokenStore()
    s.add("tok-admin", "ana.admin", "admin")
    s.add("tok-support", "ion.support", "support")
    s.add("tok-agronord", "agronord", "customer", AGRONORD)
    return s


@pytest.fixture
def who(tokens):
    return tokens.resolve
