import os
import time
import socket
import shutil
import requests
from urllib.parse import quote
import requests.packages.urllib3.util.connection as urllib3_cn
from db import get_db

# ---------------------------------------------------------
# GLOBAL STOP FLAG
# ---------------------------------------------------------
STOP_REQUESTED = False

# ---------------------------------------------------------
# Force IPv4 (fixes DNS failures on Android/Pydroid)
# ---------------------------------------------------------
def allowed_gai_family():
    return socket.AF_INET

urllib3_cn.allowed_gai_family = allowed_gai_family

# ---------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------
BASE_DIR = "/storage/emulated/0/Download/ArtCrawler"
IMAGES_DIR = "/storage/emulated/0/Pictures/ArtCrawler"

LOG_403 = f"{BASE_DIR}/failed_403.log"
LOG_METADATA = f"{BASE_DIR}/failed_metadata.log"
LOG_DOWNLOAD = f"{BASE_DIR}/failed_download.log"
LOG_QUERY = f"{BASE_DIR}/failed_query.log"

SLEEP_BETWEEN_ITEMS = 2

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

MIN_FREE_CRITICAL = 500 * 1024 * 1024   # 500 MB
MIN_FREE_WARN = 1_000_000_000           # 1 GB

stats = {
    "downloaded": 0,
    "failures": 0,
    "forbidden_403": 0,
    "metadata_fail": 0,
    "download_fail": 0,
    "query_fail": 0,
}

EDGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://commons.wikimedia.org/",
    "Connection": "keep-alive",
}

API_HEADERS = {
    "User-Agent": "ArtCrawler/1.0 (mobile; portrait-harvest; contact@example.com)"
}

# ---------------------------------------------------------
# Helper: safe UI callback
# ---------------------------------------------------------
def ui_log(msg, callback):
    if callback:
        callback(msg)
    else:
        print(msg)

# ---------------------------------------------------------
# Interruptible sleep
# ---------------------------------------------------------
def sleep_interruptible(seconds):
    for _ in range(seconds):
        if STOP_REQUESTED:
            return False
        time.sleep(1)
    return True

# ---------------------------------------------------------
# Thumbnail builder
# ---------------------------------------------------------
def build_thumbnail_url(filename, width=1500):
    try:
        safe_name = quote(filename, safe="")
        return (
            "https://commons.wikimedia.org/w/thumb.php"
            f"?width={width}&f={safe_name}"
        )
    except Exception:
        return None

# ---------------------------------------------------------
# Android media scanner
# ---------------------------------------------------------
def scan_media(path):
    try:
        from jnius import autoclass
        MediaScannerConnection = autoclass(
            "android.media.MediaScannerConnection"
        )
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        MediaScannerConnection.scanFile(
            PythonActivity.mActivity,
            [path],
            None,
            None,
        )
    except Exception:
        pass

# ---------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------
def ensure_dirs():
    os.makedirs(BASE_DIR, exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)

def log(path, text):
    try:
        with open(path, "a") as f:
            f.write(text + "\n")
    except Exception:
        pass

# ---------------------------------------------------------
# System checks
# ---------------------------------------------------------
def get_free_space():
    total, used, free = shutil.disk_usage("/storage/emulated/0")
    return free

def get_battery_level():
    try:
        with open("/sys/class/power_supply/battery/capacity") as f:
            return int(f.read().strip())
    except Exception:
        return 100

def get_temperature():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return 30.0

def safety_gate(callback):
    free = get_free_space()
    if free < MIN_FREE_CRITICAL:
        ui_log("Storage <500MB. Pausing 10 minutes…", callback)
        return sleep_interruptible(600)

    if free < MIN_FREE_WARN:
        ui_log("Storage <1GB. Slowing down…", callback)
        if not sleep_interruptible(5):
            return False

    battery = get_battery_level()
    if battery < 20:
        ui_log("Battery <20%. Pausing 10 minutes…", callback)
        return sleep_interruptible(600)

    temp = get_temperature()
    if temp > 45:
        ui_log(f"Device hot ({temp:.1f}°C). Cooling 5 minutes…", callback)
        return sleep_interruptible(300)

    return True

# ---------------------------------------------------------
# Network helper
# ---------------------------------------------------------
def safe_request(url, params, headers, callback, retries=5):
    delay = 2
    for _ in range(retries):
        if STOP_REQUESTED:
            return None
        try:
            return requests.get(
                url, params=params, headers=headers, timeout=10
            )
        except Exception as e:
            ui_log(f"Network error: {e}. Retrying in {delay}s…", callback)
            if not sleep_interruptible(delay):
                return None
            delay *= 2
    return None

def on_wifi():
    try:
        from jnius import autoclass

        Context = autoclass('android.content.Context')
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        ConnectivityManager = autoclass('android.net.ConnectivityManager')

        activity = PythonActivity.mActivity
        cm = activity.getSystemService(Context.CONNECTIVITY_SERVICE)
        network = cm.getActiveNetworkInfo()

        if network is None:
            return False

        return network.getType() == ConnectivityManager.TYPE_WIFI

    except Exception:
        return False

# ---------------------------------------------------------
# DB helpers
# ---------------------------------------------------------
def get_next_item():
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT qid, year "
        "FROM items "
        "WHERE done = 0 "
        "  AND wifi_retry = 0 "
        "ORDER BY rowid ASC "
        "LIMIT 1"
    )
    row = c.fetchone()
    conn.close()
    return row

def get_next_wifi_retry_item():
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT qid, year "
        "FROM items "
        "WHERE wifi_retry = 1 "
        "  AND done = 0 "
        "ORDER BY last_try ASC "
        "LIMIT 1"
    )
    row = c.fetchone()
    conn.close()
    return row


def mark_done(qid):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE items SET done = 1 WHERE qid = ?", (qid,))
    conn.commit()
    conn.close()


# Called by the main crawler when a network-related failure happens.
def record_soft_fail(qid, reason):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE items SET "
        "wifi_retry = 1, "
        "last_try = strftime('%s','now'), "
        "last_fail_reason = ?, "
        "wifi_fail_count = 0 "
        "WHERE qid = ?",
        (reason, qid)
    )
    conn.commit()
    conn.close()


# Called when the item is fundamentally bad (404, missing data, etc.)
def record_hard_fail(qid):
    mark_done(qid)


# Called by the Wi‑Fi retry crawler when a retry attempt fails.
def record_wifi_retry_fail(qid, reason):
    conn = get_db()
    c = conn.cursor()

    c.execute(
        "SELECT last_fail_reason, wifi_fail_count "
        "FROM items WHERE qid = ?",
        (qid,)
    )
    row = c.fetchone()

    if row is None:
        conn.close()
        return

    prev_reason, count = row

    # Same failure reason again → give up immediately
    if prev_reason == reason:
        mark_done(qid)
        conn.close()
        return

    # Different reason → increment retry count
    count += 1

    if count >= 2:
        mark_done(qid)
    else:
        c.execute(
            "UPDATE items SET "
            "wifi_fail_count = ?, "
            "last_fail_reason = ?, "
            "last_try = strftime('%s','now') "
            "WHERE qid = ?",
            (count, reason, qid)
        )
        conn.commit()

    conn.close()


# ---------------------------------------------------------
# Metadata fetch
# ---------------------------------------------------------
def get_image_title_for_qid(qid, callback):
    params = {
        "action": "wbgetentities",
        "ids": qid,
        "props": "claims",
        "format": "json",
    }

    r = safe_request(WIKIDATA_API, params, API_HEADERS, callback)
    if r is None:
        log(LOG_QUERY, f"{qid} | QUERY ERROR | Network unreachable")
        stats["query_fail"] += 1
        stats["failures"] += 1
        return None

    try:
        data = r.json()
        entity = data.get("entities", {}).get(qid)
        if not entity:
            return None

        claims = entity.get("claims", {})
        p18 = claims.get("P18")
        if not p18:
            return None

        return p18[0]["mainsnak"]["datavalue"]["value"]

    except Exception as e:
        log(LOG_QUERY, f"{qid} | QUERY ERROR | {e}")
        stats["query_fail"] += 1
        stats["failures"] += 1
        return None

# ---------------------------------------------------------
# Commons metadata
# ---------------------------------------------------------
def get_image_info(title, qid, callback):
    params = {
        "action": "query",
        "titles": "File:" + title,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata|dimensions",
        "format": "json",
    }

    r = safe_request(COMMONS_API, params, API_HEADERS, callback)
    if r is None:
        log(LOG_QUERY, f"{qid} | METADATA ERROR | Network unreachable")
        stats["query_fail"] += 1
        stats["failures"] += 1
        return None

    try:
        data = r.json()
        pages = data.get("query", {}).get("pages", {})

        for page in pages.values():
            info = page.get("imageinfo")
            if not info:
                log(LOG_METADATA, f"{qid} | NO METADATA | {title}")
                stats["metadata_fail"] += 1
                stats["failures"] += 1
                return None

            ii = info[0]

            mime = ii.get("mime", "") or ""
            if mime in (
                "image/svg+xml",
                "image/tiff",
                "image/x.djvu",
            ):
                return None

            width = ii.get("width", 0) or 0
            height = ii.get("height", 0) or 0

            if width > 0 and height > 0:
                ratio = width / height
                if ratio < 0.1 or ratio > 10:
                    return None

            if width < 450 or height < 450:
                return None

            full_url = ii.get("url")
            thumb_url = build_thumbnail_url(title, width=1500)

            chosen_url = full_url
            if width > 1500 or height > 1500:
                chosen_url = thumb_url or full_url

            if not chosen_url:
                log(LOG_METADATA, f"{qid} | NO URL | {title}")
                stats["metadata_fail"] += 1
                stats["failures"] += 1
                return None

            return {
                "url": chosen_url,
                "orig_url": full_url,
                "thumb_url": thumb_url,
            }

        log(LOG_METADATA, f"{qid} | NO METADATA | {title}")
        stats["metadata_fail"] += 1
        stats["failures"] += 1
        return None

    except Exception as e:
        log(LOG_QUERY, f"{qid} | METADATA ERROR | {e}")
        stats["query_fail"] += 1
        stats["failures"] += 1
        return None

# ---------------------------------------------------------
# Download
# ---------------------------------------------------------
def download_image(url, qid, callback):
    try:
        safe_url = quote(url, safe=":/?&=%")
        ext = os.path.splitext(url)[1].split("?")[0] or ".jpg"
        path = os.path.join(IMAGES_DIR, f"{qid}{ext}")

        if os.path.exists(path):
            return path

        r = requests.get(
            safe_url,
            stream=True,
            headers=EDGE_HEADERS,
            timeout=20,
        )

        if r.status_code == 403:
            log(LOG_403, f"{qid} | 403 | {safe_url}")
            stats["forbidden_403"] += 1
            stats["failures"] += 1
            return None

        r.raise_for_status()

    except Exception as e:
        log(LOG_DOWNLOAD, f"{qid} | DOWNLOAD ERROR | {e}")
        stats["download_fail"] += 1
        stats["failures"] += 1
        return None

    try:
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                if STOP_REQUESTED:
                    return None
                if chunk:
                    f.write(chunk)
    except Exception as e:
        log(LOG_DOWNLOAD, f"{qid} | FILE WRITE ERROR | {e}")
        stats["download_fail"] += 1
        stats["failures"] += 1
        return None

    if ext.lower() in (".jpg", ".jpeg", ".png"):
        scan_media(path)

    return path

# ---------------------------------------------------------
# Stats
# ---------------------------------------------------------
def print_stats(callback):
    total = stats["downloaded"] + stats["failures"]
    failure_rate = (
        stats["failures"] / total * 100 if total > 0 else 0
    )

    ui_log(
        "Downloaded: {d} | Failures: {f} | 403s: {f403} | "
        "Metadata: {m} | Download: {dl} | Query: {q} | "
        "Failure rate: {r:.2f}%".format(
            d=stats["downloaded"],
            f=stats["failures"],
            f403=stats["forbidden_403"],
            m=stats["metadata_fail"],
            dl=stats["download_fail"],
            q=stats["query_fail"],
            r=failure_rate,
        ),
        callback,
    )

# ---------------------------------------------------------
# MAIN CRAWLER LOOP
# ---------------------------------------------------------
def run_crawler(progress_callback=None):
    global STOP_REQUESTED
    STOP_REQUESTED = False

    ensure_dirs()
    ui_log("Crawler started…", progress_callback)

    while not STOP_REQUESTED:
        if not safety_gate(progress_callback):
            if STOP_REQUESTED:
                break
            continue

        item = get_next_item()
        if not item:
            ui_log("No more items. Sleeping 60s…", progress_callback)
            if not sleep_interruptible(60):
                break
            continue

        qid, year = item
        ui_log(f"Processing {qid} ({year})", progress_callback)

        title = get_image_title_for_qid(qid, progress_callback)
        if STOP_REQUESTED:
            break
        if not title:
            mark_done(qid)
            print_stats(progress_callback)
            sleep_interruptible(SLEEP_BETWEEN_ITEMS)
            continue

        info = get_image_info(title, qid, progress_callback)
        if STOP_REQUESTED:
            break
        if not info:
            mark_done(qid)
            print_stats(progress_callback)
            sleep_interruptible(SLEEP_BETWEEN_ITEMS)
            continue

        path = download_image(info["url"], qid, progress_callback)
        if STOP_REQUESTED:
            break
        if path is None:
            mark_done(qid)
            print_stats(progress_callback)
            sleep_interruptible(SLEEP_BETWEEN_ITEMS)
            continue

        ui_log(f"Saved {path}", progress_callback)
        stats["downloaded"] += 1
        mark_done(qid)

        print_stats(progress_callback)
        sleep_interruptible(SLEEP_BETWEEN_ITEMS)

    ui_log("Crawler stopped.", progress_callback)

def run_wifi_retry_pass():
    print("Starting Wi‑Fi retry pass…")
    run_wifi_retry(progress_callback=lambda msg: print(msg))
    print("Wi‑Fi retry pass complete.")

def run_wifi_retry(progress_callback=None):
    # Only run this pass when on Wi‑Fi
    if not on_wifi():
        if progress_callback:
            progress_callback("Not on Wi‑Fi — skipping retry pass")
        return

    if progress_callback:
        progress_callback("Starting Wi‑Fi retry pass…")

    while True:
        item = get_next_wifi_retry_item()
        if not item:
            if progress_callback:
                progress_callback("No Wi‑Fi retry items left")
            break

        qid, year = item

        if progress_callback:
            progress_callback(f"Retrying {qid} ({year})")

        # Step 1: Try to get the image title
        title = get_image_title_for_qid(qid, progress_callback)
        if not title:
            record_wifi_retry_fail(qid, "NO_TITLE")
            continue

        # Step 2: Try to get metadata
        info = get_image_info(title, qid, progress_callback)
        if not info:
            record_wifi_retry_fail(qid, "NO_METADATA")
            continue

        # Step 3: Try to download the image
        path = download_image(info["url"], qid, progress_callback)
        if not path:
            record_wifi_retry_fail(qid, "DOWNLOAD_FAIL")
            continue

        # Success!
        if progress_callback:
            progress_callback(f"Saved {path}")
        mark_done(qid)

if __name__ == "__main__":
    if on_wifi():
        print("Wi‑Fi detected.")
        print("Run Wi‑Fi retry pass? (y/N): ", end="")
        choice = input().strip().lower()
        if choice == "y":
            run_wifi_retry_pass()
        else:
            run_crawler()
    else:
        print("No Wi‑Fi detected. Running main crawler.")
        run_crawler()

