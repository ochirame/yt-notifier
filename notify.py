import json, os, time, requests
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

WEBHOOK = os.environ["WEBHOOK_URL"]
API_KEY = os.environ["YT_API_KEY"]
TOKEN_JSON = os.environ.get("TOKEN_JSON", "").strip()
CHANNELS = json.loads(os.environ["CHANNELS"])
STATE = "state/seen.json"
MAX_STATE = 3000

STATUS_LABEL = {"live": "🔴 LIVE SEKARANG", "upcoming": "📅 Stream terjadwal", "none": "🎬 Video baru"}
TIPE = {
    "public": {"nama": "🌐 Public", "warna": 0x3498DB},
    "member": {"nama": "⭐ Member", "warna": 0xF1C40F},
}

# ---------- state ----------
first_run = not os.path.exists(STATE)
seen_list = [] if first_run else json.load(open(STATE))
seen = set(seen_list)

def mark(key):
    if key not in seen:
        seen.add(key)
        seen_list.append(key)

# ---------- client YouTube ----------
def api(endpoint, **params):
    params["key"] = API_KEY
    r = requests.get(f"https://www.googleapis.com/youtube/v3/{endpoint}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()

yt = None
if TOKEN_JSON:
    creds = Credentials.from_authorized_user_info(json.loads(TOKEN_JSON))
    if not creds.valid:
        creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)

def get_videos(cid, tipe):
    pid = ("UUMO" if tipe == "member" else "UU") + cid[2:]
    try:
        if tipe == "member":
            items = yt.playlistItems().list(part="contentDetails", playlistId=pid, maxResults=5).execute()["items"]
            if not items:
                return []
            ids = ",".join(i["contentDetails"]["videoId"] for i in items)
            return yt.videos().list(part="snippet,liveStreamingDetails", id=ids).execute()["items"]
        items = api("playlistItems", part="contentDetails", playlistId=pid, maxResults=5)["items"]
        if not items:
            return []
        ids = ",".join(i["contentDetails"]["videoId"] for i in items)
        return api("videos", part="snippet,liveStreamingDetails", id=ids)["items"]
    except (HttpError, requests.HTTPError) as e:
        code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", "?")
        print(f"[{tipe}] channel {cid[:6]}... gagal (kode {code})")
        return []

# ---------- kirim ke Discord ----------
def kirim(v, nama, tipe, status):
    info = TIPE[tipe]
    thumbs = v["snippet"].get("thumbnails", {})
    thumb = (thumbs.get("maxres") or thumbs.get("high") or thumbs.get("default") or {}).get("url")
    fields = [
        {"name": "Tipe", "value": info["nama"], "inline": True},
        {"name": "Status", "value": STATUS_LABEL[status], "inline": True},
    ]
    jadwal = v.get("liveStreamingDetails", {}).get("scheduledStartTime")
    if status == "upcoming" and jadwal:
        epoch = int(datetime.fromisoformat(jadwal.replace("Z", "+00:00")).timestamp())
        fields.append({"name": "Jadwal", "value": f"<t:{epoch}:F> (<t:{epoch}:R>)", "inline": False})
    embed = {
        "author": {"name": nama},
        "title": v["snippet"]["title"][:250],
        "url": f"https://youtu.be/{v['id']}",
        "color": info["warna"],
        "fields": fields,
        "footer": {"text": info["nama"]},
        "timestamp": v["snippet"]["publishedAt"],
    }
    if thumb:
        embed["image"] = {"url": thumb}

    for _ in range(3):
        r = requests.post(WEBHOOK, json={"embeds": [embed]}, timeout=30)
        if r.status_code in (200, 204):
            time.sleep(1)
            return True
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        print(f"Webhook gagal (kode {r.status_code})")
        return False
    return False

# ---------- proses utama ----------
done = set()
urutan = (["member"] if yt else []) + ["public"]

for cid, nama in CHANNELS.items():
    for tipe in urutan:
        for v in get_videos(cid, tipe):
            vid = v["id"]
            if tipe == "public" and vid in done:
                continue
            done.add(vid)

            status = v["snippet"]["liveBroadcastContent"]
            key = f"{vid}:{status}"
            if key in seen:
                continue

            pernah = any(k.startswith(vid + ":") for k in seen)
            # run pertama cuma mencatat; stream yang sudah diumumkan lalu selesai tidak diulang
            if not first_run and not (status == "none" and pernah):
                if not kirim(v, nama, tipe, status):
                    continue  # gagal kirim: dicoba lagi di run berikutnya
            mark(key)

os.makedirs("state", exist_ok=True)
json.dump(seen_list[-MAX_STATE:], open(STATE, "w"))
