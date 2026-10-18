"""Push notification when a witness is INPUT (Cayden 2026-10-18).

Only the Solo-role designation path notifies: a solo player picking
an admin/Mod/director as their witness pushes to that witness. A
witness joining on their own does not (they already know), and an
admin adding a witness does not either. Gated by the witness's own
witness_designated pref and having a registered device.
"""
from test_api import ApiTestCase, run  # noqa: F401
from test_solo_witness import SoloWitnessTestCase

from src import db


class WitnessNotifyTestCase(SoloWitnessTestCase):
    def _setup_tt(self):
        self._mk()
        return self._tt()["id"]

    def _device(self, uid="2"):
        run(db.register_device(self.db_path, uid,
                               "push-token-0123456789abcdef"))

    def _pushes(self, uid="2"):
        rows = run(db.poll_push_outbox(self.db_path))
        return [r for r in rows if r["discord_id"] == uid]

    def test_designate_notifies_witness(self):
        tt = self._setup_tt()
        self._device()
        r = self._designate(tt, "2")
        self.assertEqual(r.status_code, 200, r.text)
        pushes = self._pushes()
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0]["title"], "You've been named a witness")
        self.assertIn("Solo stroke", pushes[0]["body"])
        self.assertEqual(pushes[0]["data"]["kind"], "witness_designated")
        self.assertEqual(pushes[0]["data"]["tee_time_id"], tt)

    def test_designate_pref_off_no_push(self):
        tt = self._setup_tt()
        self._device()
        run(db.set_notification_prefs(self.db_path, "2", {
            "tournament_starts": True, "round_starts": True,
            "ace": True, "albatross": True, "top3_changes": True,
            "witness_designated": False,
        }))
        r = self._designate(tt, "2")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._pushes(), [])

    def test_designate_no_device_no_push(self):
        tt = self._setup_tt()
        r = self._designate(tt, "2")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._pushes(), [])

    def test_redesignate_no_second_push(self):
        tt = self._setup_tt()
        self._device()
        self._designate(tt, "2")
        self._designate(tt, "2")  # idempotent — already the witness
        self.assertEqual(len(self._pushes()), 1)

    def test_self_join_no_push(self):
        tt = self._setup_tt()
        self._device()
        r = self.client.post(f"/api/tee-times/{tt}/witness",
                             headers=self.h("2"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._pushes(), [])

    def test_admin_add_no_push(self):
        tt = self._setup_tt()
        self._device()
        self._admin(True)
        r = self.client.post(f"/api/tee-times/{tt}/witnesses/2",
                             headers=self.h("1"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._pushes(), [])


del SoloWitnessTestCase
del ApiTestCase
