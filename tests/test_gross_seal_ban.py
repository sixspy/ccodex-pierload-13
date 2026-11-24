"""毛重禁封第13号（毛重案寅）：PL-M13 毛重不符禁封闸。

种子夹具：票 PL-M13 快照读数 58.5t，提单 BL-2026-0113 申报 40.0t，严重不符；
另有影子草稿 GS-PL-M13-DRAFT 挂在该票名下。

三红：
- 测甲：毛重不符仍出正式快照 —— 须 409 拒封、零增、整笔回滚。
- 测乙：对账把影子草稿当齐套 —— 须只认正式快照。
- 争用：双会话抢同票封闸 —— 后到只记争用痕，谁都不出正式快照。
"""
import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user="admin", password="admin123") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _pl_m13(c: TestClient, h: dict) -> dict:
    items = c.get("/api/tickets", headers=h).json()["items"]
    return next(t for t in items if t["ticket_code"] == "PL-M13")


def _formal_seals(c: TestClient, h: dict, ticket_code: str) -> list:
    seals = c.get("/api/seals", headers=h).json()["items"]
    return [
        s for s in seals
        if s["ticket_code"] == ticket_code and s["status"] == "已封闸"
    ]


def test_a_mismatch_never_formal_snapshot():
    """测甲：快照读数与提单申报严重不符，禁封闸、零增、不得出正式快照。"""
    c = TestClient(app)
    h = _auth(c)
    ticket = _pl_m13(c, h)
    assert ticket["gross_t"] == 58.5
    r = c.post("/api/seals", headers=h, json={"ticket_id": ticket["id"]})
    assert r.status_code == 409, r.text
    assert "毛重不符禁封闸" in r.json()["detail"]
    assert _formal_seals(c, h, "PL-M13") == []
    after = c.get(f"/api/tickets/{ticket['id']}", headers=h).json()["ticket"]
    assert after["generation"] == ticket["generation"]
    assert after["status"] == "已裁定"


def test_b_shadow_draft_not_qitao():
    """测乙：影子草稿不得计入快照数，对账不得标齐套。"""
    c = TestClient(app)
    h = _auth(c)
    recon = c.get("/api/reconcile/BL-2026-0113", headers=h).json()
    assert recon["balanced"] is False
    assert recon["seal_count"] == 0
    assert recon["seals"] == []
    assert recon["proof_ready"] is False


def test_c_two_sessions_race_same_ticket_seal():
    """争用：双会话抢 PL-M13 封闸，后到只记争用痕；毛重不符谁都不出正式快照。"""
    c = TestClient(app)
    h1 = _auth(c, "admin", "admin123")
    h2 = _auth(c, "writer", "writer123")
    ticket = _pl_m13(c, h1)
    lock_key = f"ticket:{ticket['id']}"
    got = c.post("/api/locks/acquire", headers=h1, json={"lock_key": lock_key})
    assert got.status_code == 200
    late = c.post("/api/seals", headers=h2, json={"ticket_id": ticket["id"]})
    assert late.status_code == 409
    traces = c.get("/api/contention", headers=h1).json()["items"]
    assert any(
        x["lock_key"] == lock_key and x["actor"] == "writer" for x in traces
    ), "后到者的争用痕必须落库"
    own = c.post("/api/seals", headers=h1, json={"ticket_id": ticket["id"]})
    assert own.status_code == 409
    assert "毛重不符禁封闸" in own.json()["detail"]
    assert _formal_seals(c, h1, "PL-M13") == []
    after = c.get(f"/api/tickets/{ticket['id']}", headers=h1).json()["ticket"]
    assert after["generation"] == ticket["generation"]
