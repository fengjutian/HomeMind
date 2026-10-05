import sqlite3
import time

import httpx
import jwt

database = r"C:\Users\Charles.feng\.octop\octop.db"
connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
user = connection.execute(
    "SELECT id, username, role FROM users WHERE username = ?",
    ("guanshanyue",),
).fetchone()
secret = connection.execute(
    "SELECT v FROM secrets WHERE k = ?", ("jwt",)
).fetchone()[0]
asset = connection.execute(
    "SELECT family_id, asset_id FROM homemind_family_assets "
    "WHERE mime_type = ? LIMIT 1",
    ("image/png",),
).fetchone()
now = int(time.time())
token = jwt.encode(
    {
        "sub": str(user[0]),
        "uname": user[1],
        "role": user[2],
        "iat": now,
        "exp": now + 60,
    },
    secret,
    algorithm="HS256",
)
response = httpx.get(
    f"http://127.0.0.1:8088/api/homemind/families/{asset[0]}/assets/{asset[1]}/content",
    headers={"Authorization": f"Bearer {token}"},
)
print(
    response.status_code,
    response.headers.get("content-type"),
    len(response.content),
    response.text[:200]
    if "text" in response.headers.get("content-type", "")
    else response.content[:16],
)
