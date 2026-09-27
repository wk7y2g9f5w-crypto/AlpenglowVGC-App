"""One-time setup for the Alpenglow VC Discord server.

Creates roles (Admin, Mod, Tournament Director), categories, channels, and
permission overwrites, then posts the house rules in #welcome.

Pure REST (no gateway needed). Idempotent: skips anything that already exists
by name. The bot token is read from .env and never printed.
"""
import json
import time
import urllib.request

API = "https://discord.com/api/v10"  # api.discord.com is blocked from here; discord.com/api works
# Match the guild by ID (from .env) so server renames don't break setup.
GUILD_ID = next(
    (l.split("=", 1)[1].strip().strip('"') for l in open(".env")
     if l.startswith("GUILD_ID=")),
    None,
)

VIEW = 1024
SEND = 2048

# ---- design ----
ROLES = [
    {"name": "Admin", "permissions": "8", "color": 0xF0B429, "hoist": True,
     "reason": "Server admins (full control)"},
    {"name": "Mod", "permissions": str(2 + 8192 + 131072 + 17179869184 + 1099511627776),
     "color": 0x3498DB, "hoist": True, "reason": "Moderators"},
    {"name": "Tournament Director", "permissions": "0", "color": 0x2ECC71,
     "hoist": False, "reason": "Can run bot tournament commands"},
]

# (category, [(channel, topic, overwrites_kind)])
# overwrites_kind: None=default, "crew_only", "readonly", "private"
LAYOUT = [
    ("The Front Gate", [
        ("welcome", "Start here — course rules and how things work.", "readonly"),
        ("announcements", "Official announcements from the crew.", "readonly"),
    ]),
    ("The Clubhouse", [
        ("tournament-talk", "Tournament discussion and banter.", None),
        ("event-signups", "Register for tournaments here. Use /register, then grab a tee time.", None),
        ("leaderboards", "Live tournament standings, updated automatically.", "readonly"),
        ("match-scheduling", "Sort out when your matches get played.", None),
        ("command-guide", "Player reference: how to use the tournament bot.", "readonly"),
    ]),
    ("The Tee Sheet", [
        ("tee-sheet", "Live board: open registrations and upcoming tee times. Tap to join.", "readonly"),
    ]),
    ("The Range", [
        ("side-quests", "Casual challenges — check the pinned quests.", None),
        ("quest-results", "Post your side-quest scores and clips.", None),
    ]),
    ("1st Tee", [
        ("looking-for-group", "Find playing partners for a round.", None),
        ("pairings-check-in", "Pairings check in with each other here.", None),
        ("game-codes", "Exchange Golf+ game codes.", None),
    ]),
    ("19th Hole", [
        ("general", "The clubhouse bar — hang out.", None),
        ("clips-and-highlights", "Best shots, worst shanks.", None),
    ]),
    ("The Office", [
        ("crew-chat", "Crew business only.", "private"),
        ("crew-guide", "Crew-only bot command reference (admin commands).", "private"),
    ]),
]

RULES_MESSAGE = """**Welcome to Alpenglow VC** :golf:
Golden-hour golf: laid back, a little competitive, always fun.

**New here? Start with `/setup`** — 30 seconds: pick your timezone and link your Golf+ username. You can't register for tournaments until your timezone is set, so do this first.

**Course rules**
1. **It's a game.** Keep it fun. Real-life beef stays off the course.
2. **Credit where it's due.** Ideas belong to everyone, but tournaments get named after whoever dreamed them up. Nothing's set in stone — final calls go to a crew poll.
3. **Discuss, don't dictate.** Everybody has the right to their own opinion. Disagreements get talked out, and polls are the ending factor — majority wins.
4. **No loyalty tests.** Play where you want, with who you want. Nobody's keeping score off the course.
5. **Golf is hard.** Keep judgment to yourself, and don't give unwarranted advice.

**Getting started:** grab a tournament in #event-signups, find a round in #looking-for-group, or just pull up a chair in #general. See you at golden hour. :sunset:"""


def load_token():
    with open(".env") as f:
        for line in f:
            if line.startswith("DISCORD_TOKEN"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DISCORD_TOKEN not found in .env")


TOKEN = load_token()


def api(method, path, data=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers={
            "Authorization": f"Bot {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "AlpenglowVC-setup/1.0 (discord bot server setup)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        raise SystemExit(f"Discord API {e.code} on {method} {path}: {body}")


def main():
    guilds = api("GET", "/users/@me/guilds")
    guild = next((g for g in guilds if g["id"] == GUILD_ID), None)
    if not guild:
        raise SystemExit(f"Guild '{GUILD_ID}' not found. Is the bot invited?")
    gid = guild["id"]
    print(f"Guild: {guild['name']} ({gid})")

    roles = api("GET", f"/guilds/{gid}/roles")
    by_name = {r["name"]: r for r in roles}
    everyone = by_name.get("@everyone")
    created_roles = {}
    for spec in ROLES:
        if spec["name"] in by_name:
            print(f"Role '{spec['name']}' exists, skipping")
            created_roles[spec["name"]] = by_name[spec["name"]]
            continue
        r = api("POST", f"/guilds/{gid}/roles", {
            "name": spec["name"],
            "permissions": spec["permissions"],
            "color": spec["color"],
            "hoist": spec["hoist"],
            "mentionable": False,
        })
        print(f"Created role '{spec['name']}'")
        created_roles[spec["name"]] = r
        time.sleep(0.5)

    # Order crew roles below the bot's own role: Admin > Mod > Tournament Director.
    roles = api("GET", f"/guilds/{gid}/roles")
    by_name = {r["name"]: r for r in roles}
    bot_user = api("GET", "/users/@me")
    me = api("GET", f"/guilds/{gid}/members/{bot_user['id']}")
    # find bot's highest role position from member role ids
    bot_role_ids = set(me["roles"])
    bot_top = max(r["position"] for r in roles if r["id"] in bot_role_ids)
    order = ["Admin", "Mod", "Tournament Director"]
    payload = [{"id": by_name[n]["id"], "position": bot_top - (i + 1)}
               for i, n in enumerate(order)]
    api("PATCH", f"/guilds/{gid}/roles", payload)
    print("Ordered crew roles below the bot role")
    time.sleep(0.5)

    channels = api("GET", f"/guilds/{gid}/channels")
    chan_by_name = {c["name"]: c for c in channels if c["type"] == 0}
    cat_by_name = {c["name"]: c for c in channels if c["type"] == 4}

    def overwrites(kind):
        if kind == "private":
            return [
                {"id": everyone["id"], "type": 0, "allow": "0", "deny": str(VIEW)},
                *[{"id": created_roles[n]["id"], "type": 0,
                   "allow": str(VIEW + SEND), "deny": "0"} for n in order],
            ]
        if kind == "readonly":
            return [
                {"id": everyone["id"], "type": 0, "allow": "0", "deny": str(SEND)},
                *[{"id": created_roles[n]["id"], "type": 0,
                   "allow": str(VIEW + SEND), "deny": "0"} for n in order],
            ]
        return []

    for cat_name, ch_list in LAYOUT:
        if cat_name in cat_by_name:
            cat = cat_by_name[cat_name]
            print(f"Category '{cat_name}' exists, skipping")
        else:
            cat = api("POST", f"/guilds/{gid}/channels",
                      {"name": cat_name, "type": 4})
            print(f"Created category '{cat_name}'")
            time.sleep(0.5)
        for ch_name, topic, kind in ch_list:
            if ch_name in chan_by_name:
                ch = chan_by_name[ch_name]
                # move it under the right category if needed
                if ch.get("parent_id") != cat["id"]:
                    api("PATCH", f"/channels/{ch['id']}",
                        {"parent_id": cat["id"]})
                    print(f"Moved #{ch_name} under '{cat_name}'")
                else:
                    print(f"Channel #{ch_name} exists, skipping")
                continue
            body = {"name": ch_name, "type": 0, "parent_id": cat["id"],
                    "topic": topic}
            ow = overwrites(kind)
            if ow:
                body["permission_overwrites"] = ow
            api("POST", f"/guilds/{gid}/channels", body)
            print(f"Created #{ch_name} under '{cat_name}'")
            time.sleep(0.5)

    # Post house rules in #welcome
    channels = api("GET", f"/guilds/{gid}/channels")
    welcome = next(c for c in channels if c["name"] == "welcome" and c["type"] == 0)
    msgs = api("GET", f"/channels/{welcome['id']}/messages?limit=5")
    if not any("Course rules" in m.get("content", "") for m in msgs):
        api("POST", f"/channels/{welcome['id']}/messages",
            {"content": RULES_MESSAGE})
        print("Posted house rules in #welcome")
    else:
        print("House rules already posted, skipping")

    # Point the bot at this guild for instant slash-command sync
    print(f"\nDone. Set GUILD_ID={gid} in .env for instant command sync.")
    return gid


if __name__ == "__main__":
    main()
