"""闸代快照：升代并写清单。毛重不符禁封闸（甲路径，2026-11-24 锁死）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import SealIn
from validators import format_manifest, validate_bl_match, validate_ticket_sealable

router = APIRouter(tags=["seal"])


def _record_contention(lock_key: str, actor: str) -> None:
    """争用痕独立落库：后到者的痕迹不随业务事务回滚。"""
    with connect() as conn:
        conn.execute(
            """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (lock_key, actor, "争用", "同票封闸后到，只记痕不写业务", now_iso()),
        )
        conn.commit()


def _record_seal_ban(actor: str, ticket, declared: float | None) -> None:
    """毛重禁封审计：业务整笔回滚后，拒收事实独立落库。"""
    with connect() as conn:
        write_audit(
            conn,
            actor,
            "毛重禁封",
            ticket["ticket_code"],
            {
                "bl_ref": ticket["bl_ref"],
                "snapshot_gross_t": float(ticket["gross_t"]),
                "declared_gross_t": declared,
                "ticket_generation": int(ticket["generation"]),
            },
        )
        conn.commit()


@router.get("/api/seals")
def list_seals(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.*, t.ticket_code, t.vehicle_plate FROM gate_seals s
               JOIN weigh_tickets t ON t.id=s.ticket_id ORDER BY s.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/seals")
def create_seal(body: SealIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"ticket:{body.ticket_id}"
    with connect() as conn:
        try:
            require_lock(conn, lock_key, user)
        except HTTPException:
            conn.rollback()
            _record_contention(lock_key, user)
            raise
        try:
            ticket = conn.execute(
                "SELECT * FROM weigh_tickets WHERE id=?", (body.ticket_id,)
            ).fetchone()
            if not ticket:
                raise HTTPException(404, "过磅票不存在")
            validate_ticket_sealable(dict(ticket))
            if ticket["status"] == "旁路挂起":
                raise HTTPException(409, "旁路挂起不得封闸")
            open_hold = conn.execute(
                """SELECT id FROM overload_holds WHERE ticket_id=? AND status='开放'""",
                (body.ticket_id,),
            ).fetchone()
            if open_hold:
                raise HTTPException(409, "超重开放不得封闸")
            bl = conn.execute(
                "SELECT * FROM bl_manifests WHERE bl_code=?", (ticket["bl_ref"],)
            ).fetchone()
            if not bl:
                conn.rollback()
                _record_seal_ban(user, ticket, None)
                raise HTTPException(409, "提单册未登记禁封闸")
            declared = float(bl["declared_gross_t"])
            gross = float(ticket["gross_t"])
            if not validate_bl_match(declared, gross):
                conn.rollback()
                _record_seal_ban(user, ticket, declared)
                raise HTTPException(
                    409,
                    f"毛重不符禁封闸：快照读数{gross}t"
                    f"与提单{ticket['bl_ref']}申报{declared}t偏差超限",
                )
            next_gen = int(ticket["generation"]) + 1
            manifest = body.manifest or format_manifest(
                dict(ticket), float(ticket["gross_t"]), next_gen
            )
            now = now_iso()
            seal_ref = f"GS-{ticket['ticket_code']}-G{next_gen}"
            cur = conn.execute(
                """INSERT INTO gate_seals(ticket_id,ticket_generation,manifest,seal_ref,status,sealed_at)
                   VALUES (?,?,?,?,?,?)""",
                (body.ticket_id, next_gen, manifest, seal_ref, "已封闸", now),
            )
            conn.execute(
                """UPDATE weigh_tickets SET generation=?, status='已封闸' WHERE id=?""",
                (next_gen, body.ticket_id,),
            )
            write_audit(
                conn,
                user,
                "gate_seal",
                seal_ref,
                {
                    "seal_id": cur.lastrowid,
                    "generation": next_gen,
                    "manifest": manifest,
                },
            )
            conn.commit()
            return {
                "id": cur.lastrowid,
                "seal_ref": seal_ref,
                "generation": next_gen,
                "manifest": manifest,
                "status": "已封闸",
            }
        finally:
            release(conn, lock_key, user)


@router.get("/api/seals/{seal_ref}")
def get_seal(seal_ref: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        row = conn.execute(
            """SELECT s.*, t.ticket_code, t.axle_weights, t.gross_t
               FROM gate_seals s JOIN weigh_tickets t ON t.id=s.ticket_id
               WHERE s.seal_ref=?""",
            (seal_ref,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "快照不存在")
        data = dict(row)
        data["axle_weights"] = json.loads(data["axle_weights"] or "[]")
        return data