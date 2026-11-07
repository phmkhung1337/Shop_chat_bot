from pathlib import Path
import tempfile
import unittest

from chatbot.shop import ShopEngine
from tickets.support import SupportStore


class SupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SupportStore(Path(self.tmp.name) / "queue.sqlite3")
        self.engine = ShopEngine(store=self.store, data_dir=Path(__file__).resolve().parents[1] / "data" / "demo")

    def test_staff_changes_persist_across_restart(self):
        ticket = self.engine.handle_message("Gặp nhân viên", "one", "one")["ticket_id"]
        self.store.update_ticket(ticket, status="in_progress", assignee="Linh", notes="Đang xác minh")
        restarted = SupportStore(self.store.path)
        item = restarted.list_tickets("in_progress")[0]
        self.assertEqual((item["assignee"], item["notes"]), ("Linh", "Đang xác minh"))
        restarted.update_ticket(ticket, status="resolved", assignee="Linh", resolution="Đã kiểm tra nhãn", return_to_bot=True)
        self.assertEqual(SupportStore(self.store.path).list_tickets("resolved")[0]["resolution"], "Đã kiểm tra nhãn")

    def test_invalid_staff_change_is_atomic(self):
        ticket = self.engine.handle_message("Gặp nhân viên", "one", "one")["ticket_id"]
        with self.assertRaises(ValueError):
            self.store.update_ticket(ticket, status="in_progress", return_to_bot=True)
        self.assertEqual(self.store.list_tickets()[0]["status"], "new")
        self.assertFalse(self.store.state("one")["takeover"])
        with self.assertRaises(ValueError):
            self.store.update_ticket(ticket, status="sent_to_customer")

    def test_retention_does_not_remove_unresolved_requests(self):
        ticket = self.engine.handle_message("Gặp nhân viên", "one", "one")["ticket_id"]
        self.assertEqual(self.store.purge("2099-01-01T00:00:00+00:00"), 0)
        self.store.update_ticket(ticket, status="resolved", return_to_bot=True)
        self.assertEqual(self.store.purge("2099-01-01T00:00:00+00:00"), 1)
        self.assertEqual(self.store.list_tickets(), [])
        self.assertEqual(self.store.state("one")["version"], 0)


if __name__ == "__main__":
    unittest.main()
