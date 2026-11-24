"""提单册登记。毛重核对与封闸同一口径。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from models import BlIn
from validators import validate_bl_match

router = APIRouter(tags=["bl"])


@router.get("/api/bl")
def list_bl(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bl_manifests ORDER BY bl_code"
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            declared = float(d["declared_gross_t"])
            tickets = conn.execute(
                """SELECT gross_t FROM weigh_tickets
                   WHERE bl_ref=? AND status<>'作废'""",
                (d["bl_code"],),
            ).fetchall()
            d["ticket_sum_t"] = round(
                sum(float(t["gross_t"]) for t in tickets), 3
            )
            d["gross_mismatch"] = any(
                not validate_bl_match(declared, float(t["gross_t"]))
                for t in tickets
            )
            items.append(d)
        return {"items": items}


@router.get("/api/bl/{bl_code}")
def get_bl(bl_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "提单不存在")
        tickets = conn.execute(
            """SELECT ticket_code, gross_t, status FROM weigh_tickets
               WHERE bl_ref=? ORDER BY id DESC""",
            (bl_code,),
        ).fetchall()
        declared = float(row["declared_gross_t"])
        items = []
        for t in tickets:
            d = dict(t)
            d["gross_matches_declared"] = validate_bl_match(
                declared, float(d["gross_t"])
            )
            items.append(d)
        return {"bl": dict(row), "tickets": items}


@router.post("/api/bl")
def create_bl(body: BlIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        if conn.execute(
            "SELECT id FROM bl_manifests WHERE bl_code=?", (body.bl_code,)
        ).fetchone():
            raise HTTPException(409, "提单号已存在")
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO bl_manifests(bl_code,cargo_desc,declared_gross_t,vessel_voyage,
               status,created_at) VALUES (?,?,?,?,?,?)""",
            (
                body.bl_code,
                body.cargo_desc,
                body.declared_gross_t,
                body.vessel_voyage,
                "在港",
                now,
            ),
        )
        write_audit(conn, user, "bl_register", body.bl_code, {"bl_id": cur.lastrowid})
        conn.commit()
        return {"id": cur.lastrowid, "bl_code": body.bl_code}