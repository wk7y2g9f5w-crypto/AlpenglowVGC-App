"""Apple Push Notification (APNs) delivery for the companion app.

How it fits together:
- The iOS app registers its APNs device token via
  POST /api/devices/register and stores notification toggles via
  PUT /api/notifications/prefs (see the devices / notification_prefs
  tables in db.py).
- Score/start hooks queue rows into push_outbox. A background task in
  the API lifespan drains the outbox once a minute and sends via APNs;
  `/notify_test` sends directly.
- APNs auth is token-based. Cayden provides three env vars on Render:
  APNS_KEY_P8 (the full .p8 file contents), APNS_KEY_ID, APNS_TEAM_ID.
  APNS_BUNDLE_ID defaults to the app's bundle id; APNS_SANDBOX=1 talks
  to the sandbox endpoint (for dev builds).

If the env vars are absent, the sender reports unconfigured and the
drain loop simply leaves rows queued — nothing crashes, nothing sends.
"""

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db

log = logging.getLogger("vgc.push")

BUNDLE_ID = os.environ.get("APNS_BUNDLE_ID", "com.alpenglowvgc.alpenglowvgc")
DENVER = ZoneInfo("America/Denver")

_sender = None


def get_sender() -> "PushSender":
    global _sender
    if _sender is None:
        _sender = PushSender()
    return _sender


class PushSender:
    def __init__(self) -> None:
        self.key = (os.environ.get("APNS_KEY_P8") or "").strip()
        self.key_id = (os.environ.get("APNS_KEY_ID") or "").strip()
        self.team_id = (os.environ.get("APNS_TEAM_ID") or "").strip()
        self.sandbox = os.environ.get("APNS_SANDBOX") == "1"
        self._apns = None

    @property
    def configured(self) -> bool:
        return bool(self.key and self.key_id and self.team_id)

    def _client(self):
        if self._apns is None:
            from aioapns import APNs

            # aioapns accepts the PEM key content directly.
            key_pem = self.key
            if "BEGIN PRIVATE KEY" not in key_pem:
                key_pem = (
                    "-----BEGIN PRIVATE KEY-----\n"
                    + key_pem
                    + "\n-----END PRIVATE KEY-----"
                )
            self._apns = APNs(
                key=key_pem,
                key_id=self.key_id,
                team_id=self.team_id,
                topic=BUNDLE_ID,
                use_sandbox=self.sandbox,
            )
        return self._apns

    async def send(self, token: str, title: str, body: str,
                   data: dict | None = None) -> str:
        """Send one push. Returns 'ok', 'unregistered', or 'error'."""
        if not self.configured:
            return "error"
        try:
            from aioapns import NotificationRequest, PushType

            req = NotificationRequest(
                device_token=token,
                message={"aps": {"alert": {"title": title, "body": body},
                                 "sound": "default"},
                         **(data or {})},
                push_type=PushType.ALERT,
            )
            res = await self._client().send_notification(req)
            if res.is_successful:
                return "ok"
            desc = (res.description or "").lower()
            if res.status == "410" or "unregistered" in desc:
                return "unregistered"
            log.warning("APNs send failed: %s %s", res.status, res.description)
            return "error"
        except Exception:
            log.exception("APNs send error")
            return "error"


async def drain_push_outbox(db_path: str, limit: int = 50) -> int:
    """Send queued pushes. Returns the number of rows marked sent."""
    sender = get_sender()
    if not sender.configured:
        return 0
    batch = await db.poll_push_outbox(db_path, limit)
    if not batch:
        return 0
    sent_ids: list[int] = []
    for row in batch:
        tokens = await db._fetchall(
            db_path,
            "SELECT push_token FROM devices WHERE discord_id = ?",
            (row["discord_id"],),
        )
        for t in tokens:
            outcome = await sender.send(
                t["push_token"], row["title"], row["body"], row.get("data"))
            if outcome == "unregistered":
                await db.unregister_device(db_path, row["discord_id"],
                                           t["push_token"])
        # Ack even when no device is registered anymore: the user-level
        # event was handled; a token arriving later must not replay it.
        sent_ids.append(row["id"])
    await db.ack_push_outbox(db_path, sent_ids)
    return len(sent_ids)


async def check_starts(db_path: str) -> int:
    """Enqueue tournament-start and round-start pushes due today (Denver).

    Each start notifies once, guarded by push_sent_log.
    Returns the number of pushes enqueued.
    """
    today = datetime.now(DENVER).date().isoformat()
    n = 0
    tournaments = await db._fetchall(
        db_path,
        "SELECT id, name, start_date FROM tournaments"
        " WHERE start_date = ? AND status IN"
        " ('registration_open','in_progress')",
        (today,),
    )
    for t in tournaments:
        ref = f"tournament_start:{t['id']}"
        if await db.push_was_sent(db_path, "tournament_start", ref):
            continue
        await db.mark_push_sent(db_path, "tournament_start", ref)
        n += await db.notify_tournament_players(
            db_path, t["id"], "tournament_starts",
            title=f"⛳ {t['name']} is underway",
            body="The tournament starts today — good luck!",
            data={"type": "tournament_start", "tournament_id": t["id"]},
        )
    rounds = await db._fetchall(
        db_path,
        "SELECT r.tournament_id, r.round_number, t.name FROM rounds r"
        " JOIN tournaments t ON t.id = r.tournament_id"
        " WHERE r.start_date = ? AND t.status IN"
        " ('registration_open','in_progress')",
        (today,),
    )
    for r in rounds:
        ref = f"round_start:{r['tournament_id']}:{r['round_number']}"
        if await db.push_was_sent(db_path, "round_start", ref):
            continue
        await db.mark_push_sent(db_path, "round_start", ref)
        n += await db.notify_tournament_players(
            db_path, r["tournament_id"], "round_starts",
            title=f"⛳ {r['name']} — Round {r['round_number']} is open",
            body="Scores are now being accepted for this round.",
            data={"type": "round_start", "tournament_id": r["tournament_id"],
                  "round_number": r["round_number"]},
        )
    return n
