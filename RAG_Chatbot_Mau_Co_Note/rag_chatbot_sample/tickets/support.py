"""Durable local support queue, conversation state and idempotent outcomes."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

DEFAULT_DB = Path(__file__).resolve().parents[1] / "shop_support.sqlite3"
STATUSES = ("new", "in_progress", "resolved")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class SupportStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
                    takeover INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    conversation_id TEXT NOT NULL, question TEXT NOT NULL, channel TEXT NOT NULL,
                    reason TEXT NOT NULL, criteria TEXT NOT NULL, product_ids TEXT NOT NULL,
                    source_ids TEXT NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new', assignee TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '', resolution TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, request_id TEXT,
                    event TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def state(self, conversation_id, db=None):
        if db is None:
            with self.connect() as conn:
                return self.state(conversation_id, conn)
        row = db.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row is None:
            return {"criteria": {}, "history": [], "last_products": [], "version": 0, "takeover": False}
        state = json.loads(row["state"])
        state.update(version=row["version"], takeover=bool(row["takeover"]))
        return state

    def previous(self, request_id, conversation_id, fingerprint, db):
        row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return None
        if row["conversation_id"] != conversation_id or row["fingerprint"] != fingerprint:
            raise ValueError("Mã yêu cầu đã được dùng cho nội dung hoặc phiên khác.")
        return json.loads(row["result"])

    def ticket(self, db, *, request_id, conversation_id, question, channel, reason,
               criteria, product_ids, source_ids):
        if reason == "staff_takeover":
            active = db.execute(
                """SELECT id FROM tickets WHERE conversation_id=? AND status='in_progress'
                   ORDER BY created_at DESC, id DESC LIMIT 1""",
                (conversation_id,),
            ).fetchone()
            if active is not None:
                db.execute(
                    "UPDATE tickets SET question=question || ? WHERE id=?",
                    ("\n\nKhách bổ sung:\n" + question, active["id"]),
                )
                return active["id"]
        ticket_id = "HT-" + uuid4().hex[:10].upper()
        db.execute("""INSERT INTO tickets
            (id,request_id,conversation_id,question,channel,reason,criteria,product_ids,source_ids,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (ticket_id, request_id, conversation_id, question, channel, reason,
                    dumps(criteria), dumps(product_ids), dumps(source_ids), utc_now()))
        return ticket_id

    def save(self, db, conversation_id, request_id, fingerprint, state, result, duration_ms):
        version = state["version"]
        state = dict(state)
        takeover = int(state.pop("takeover", False))
        state.pop("version", None)
        db.execute("""INSERT INTO conversations (id,state,version,takeover) VALUES (?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET state=excluded.state,
            version=excluded.version,takeover=excluded.takeover""",
                   (conversation_id, dumps(state), version + 1, takeover))
        result["state_version"] = version + 1
        db.execute("INSERT INTO requests VALUES (?,?,?,?,?)",
                   (request_id, conversation_id, fingerprint, dumps(result), utc_now()))
        self.audit(db, conversation_id, request_id, "message", {
            "status": result["status"], "reason": result["reason"],
            "product_ids": result["product_ids"], "source_ids": result["source_ids"],
            "data_version": result.get("data_version"), "duration_ms": duration_ms,
            "model_calls": result.get("model_calls", 0), "publish": result["publish"],
        })

    def audit(self, db, conversation_id, request_id, event, details):
        db.execute("INSERT INTO audit (conversation_id,request_id,event,details,created_at) VALUES (?,?,?,?,?)",
                   (conversation_id, request_id, event, dumps(details), utc_now()))

    def list_tickets(self, status=None):
        if status is not None and status not in STATUSES:
            raise ValueError("Trạng thái ticket không hợp lệ.")
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tickets" + (" WHERE status=?" if status else "") +
                              " ORDER BY created_at DESC", (status,) if status else ()).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("criteria", "product_ids", "source_ids"):
                item[key] = json.loads(item[key])
            result.append(item)
        return result

    def update_ticket(self, ticket_id, *, status, assignee="", notes="", resolution="", return_to_bot=False):
        if status not in STATUSES or any(not isinstance(x, str) or len(x) > 4000
                                         for x in (assignee, notes, resolution)):
            raise ValueError("Nội dung hoặc trạng thái cập nhật không hợp lệ.")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if row is None:
                raise ValueError("Không tìm thấy ticket.")
            db.execute("UPDATE tickets SET status=?,assignee=?,notes=?,resolution=? WHERE id=?",
                       (status, assignee, notes, resolution, ticket_id))
            if status == "in_progress":
                db.execute("UPDATE conversations SET takeover=1 WHERE id=?", (row["conversation_id"],))
            if return_to_bot:
                other = db.execute("SELECT count(*) FROM tickets WHERE conversation_id=? AND status='in_progress'",
                                   (row["conversation_id"],)).fetchone()[0]
                if other:
                    raise ValueError("Còn ticket đang xử lý. Hãy giải quyết trước khi bàn giao lại bot.")
                db.execute("UPDATE conversations SET takeover=0 WHERE id=?", (row["conversation_id"],))
            self.audit(db, row["conversation_id"], row["request_id"], "staff_update", {
                "ticket_id": ticket_id, "status": status, "assignee": assignee,
                "return_to_bot": return_to_bot,
            })

    def purge(self, before):
        cutoff = datetime.fromisoformat(before.replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise ValueError("Mốc lưu trữ phải có múi giờ.")
        with self.transaction() as db:
            ids = [r[0] for r in db.execute("""SELECT conversation_id FROM requests
                GROUP BY conversation_id HAVING MAX(created_at) < ?
                AND conversation_id NOT IN (SELECT conversation_id FROM tickets WHERE status != 'resolved')""",
                                           (cutoff.astimezone(timezone.utc).isoformat(),))]
            for conversation_id in ids:
                for table in ("requests", "tickets", "audit"):
                    db.execute(f"DELETE FROM {table} WHERE conversation_id=?", (conversation_id,))
                db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        return len(ids)


def main():
    parser = argparse.ArgumentParser(description="Hàng đợi hỗ trợ local — không gửi tin ra ngoài")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--status", choices=STATUSES)
    update = commands.add_parser("update")
    update.add_argument("ticket_id")
    update.add_argument("--status", choices=STATUSES, required=True)
    update.add_argument("--assignee", default="")
    update.add_argument("--notes", default="")
    update.add_argument("--resolution", default="")
    update.add_argument("--return-to-bot", action="store_true")
    purge = commands.add_parser("purge")
    purge.add_argument("--before", required=True)
    purge.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()
    try:
        store = SupportStore(args.db)
        if args.command == "list":
            print(json.dumps(store.list_tickets(args.status), ensure_ascii=False, indent=2))
        elif args.command == "purge":
            print(f"Đã xóa {store.purge(args.before)} phiên cũ đã hoàn tất.")
        else:
            store.update_ticket(args.ticket_id, status=args.status, assignee=args.assignee,
                                notes=args.notes, resolution=args.resolution, return_to_bot=args.return_to_bot)
            print("Đã lưu. Chưa gửi tin cho khách.")
    except (ValueError, sqlite3.Error, OSError):
        parser.exit(1, "Không thể hoàn tất thao tác. Kiểm tra dữ liệu và quyền ghi SQLite.\n")


if __name__ == "__main__":
    main()
