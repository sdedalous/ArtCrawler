import os
import time
import socket
import shutil
import subprocess
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
LOG_WIFI_HARD = f"{BASE_DIR}/failed_wifi_hard.log"

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
def build_thumbnail_url(filename, width=2500):
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
# Wi-Fi detection (Termux)
# ---------------------------------------------------------
def wifi_on():
    try:
        out = subprocess.check_output(
            ["termux-wifi-connectioninfo"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8")
        return '"state":"CONNECTED"' in out
    except Exception:
        return False

# ---------------------------------------------------------
# Network helper (unchanged retries, no per-request Wi-Fi logic)
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

# ---------------------------------------------------------
# DB helpers
# ---------------------------------------------------------
def get_next_item(wifi_retry_phase):
    conn = get_db()
    c = conn.cursor()

    if wifi_retry_phase:
        # Process Wi-Fi retry queue first (items that previously failed on bad network)
        c.execute(
            "SELECT qid, year FROM items "
            "WHERE wifi_retry = 1 AND wifi_fail_count < 3 "
            "LIMIT 1"
        )
        row = c.fetchone()
        if row:
            conn.close()
            return row

    # Normal queue: items never processed or newly indexed
    c.execute(
        "SELECT qid, year FROM items "
        "WHERE done = 0 "
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

def mark_success(qid):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE items
        SET done = 1,
            wifi_retry = 0,
            last_fail_reason = NULL
        WHERE qid = ?
    """, (qid,))
    conn.commit()
    conn.close()

def mark_wifi_failure(qid, reason):
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT wifi_fail_count FROM items WHERE qid = ?", (qid,))
    row = c.fetchone()
    current = row[0] if row else 0

    if current < 2:
        # Soft fail: keep for Wi-Fi retry
        c.execute("""
            UPDATE items
            SET wifi_fail_count = wifi_fail_count + 1,
                wifi_retry = 1,
                last_fail_reason = ?,
                last_try = strftime('%s','now'),
                done = 0
            WHERE qid = ?
        """, (reason, qid))
    else:
        # Hard fail after 3 attempts
        c.execute("""
            UPDATE items
            SET wifi_fail_count = wifi_fail_count + 1,
                wifi_retry = 0,
                last_fail_reason = ?,
                last_try = strftime('%s','now'),
                done = 1
            WHERE qid = ?
        """, (reason, qid))

        try:
            with open(LOG_WIFI_HARD, "a") as f:
                f.write(f"{qid} | WIFI HARD FAIL | {reason}\n")
        except Exception:
            pass

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
            thumb_url = build_thumbnail_url(title, width=2500)

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
# Improved Stats (Top Line)
# ---------------------------------------------------------
def print_stats(callback):
    conn = get_db()
    c = conn.cursor()

    # Soft fails = wifi_retry = 1
    c.execute("SELECT COUNT(*) FROM items WHERE wifi_retry = 1")
    soft_fails = c.fetchone()[0]

    # Hard fails = done=1 AND wifi_retry=0 AND last_fail_reason IS NOT NULL
    c.execute("""
        SELECT COUNT(*) FROM items
        WHERE done = 1 AND wifi_retry = 0 AND last_fail_reason IS NOT NULL
    """)
    hard_fails = c.fetchone()[0]

    # Successful downloads = done=1 AND last_fail_reason IS NULL
    c.execute("""
        SELECT COUNT(*) FROM items
        WHERE done = 1 AND last_fail_reason IS NULL
    """)
    downloaded = c.fetchone()[0]

    conn.close()

    total_attempted = downloaded + soft_fails + hard_fails
    soft_rate = (soft_fails / total_attempted * 100) if total_attempted else 0
    hard_rate = (hard_fails / total_attempted * 100) if total_attempted else 0

    ui_log(
        f"Downloaded: {downloaded} | Soft fails: {soft_fails} | Hard fails: {hard_fails} "
        f"| Soft rate: {soft_rate:.1f}% | Hard rate: {hard_rate:.1f}%",
        callback,
    )

# ---------------------------------------------------------
# DB Summary (Bottom Line)
# ---------------------------------------------------------
def print_db_summary(callback):
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM items")
    total = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM items WHERE done = 1")
    done = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM items WHERE done = 0")
    pending = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM items WHERE wifi_retry = 1")
    soft_fails = c.fetchone()[0]

    c.execute("""
        SELECT COUNT(*) FROM items
        WHERE done = 1 AND wifi_retry = 0 AND last_fail_reason IS NOT NULL
    """)
    hard_fails = c.fetchone()[0]

    c.execute("SELECT SUM(wifi_fail_count) FROM items")
    wifi_attempts = c.fetchone()[0] or 0

    conn.close()

    ui_log(
        f"DB: total={total} | done={done} | pending={pending} | "
        f"soft={soft_fails} | hard={hard_fails} | wifi_attempts={wifi_attempts}",
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

    # Phase 1: Wi-Fi retry queue (once, at startup)
    wifi_retry_phase = wifi_on()
    if wifi_retry_phase:
        ui_log("Stable Wi-Fi detected — processing Wi-Fi retry queue first.", progress_callback)
    else:
        ui_log("Wi-Fi not stable — skipping Wi-Fi retry queue.", progress_callback)

    item_counter = 0  # for printing DB summary every 20 items

    while not STOP_REQUESTED:
        if not safety_gate(progress_callback):
            if STOP_REQUESTED:
                break
            continue

        item = get_next_item(wifi_retry_phase)

        # If Wi-Fi retry phase is active but queue is empty, switch to normal mode
        if wifi_retry_phase and not item:
            wifi_retry_phase = False
            continue

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
            if wifi_retry_phase:
                mark_wifi_failure(qid, "title")
            else:
                mark_done(qid)
            print_stats(progress_callback)
            item_counter += 1
            if item_counter % 20 == 0:
                print_db_summary(progress_callback)
            sleep_interruptible(SLEEP_BETWEEN_ITEMS)
            continue

        info = get_image_info(title, qid, progress_callback)
        if STOP_REQUESTED:
            break
        if not info:
            if wifi_retry_phase:
                mark_wifi_failure(qid, "metadata")
            else:
                mark_done(qid)
            print_stats(progress_callback)
            item_counter += 1
            if item_counter % 20 == 0:
                print_db_summary(progress_callback)
            sleep_interruptible(SLEEP_BETWEEN_ITEMS)
            continue

        path = download_image(info["url"], qid, progress_callback)
        if STOP_REQUESTED:
            break
        if path is None:
            if wifi_retry_phase:
                mark_wifi_failure(qid, "download")
            else:
                mark_done(qid)
            print_stats(progress_callback)
            item_counter += 1
            if item_counter % 20 == 0:
                print_db_summary(progress_callback)
            sleep_interruptible(SLEEP_BETWEEN_ITEMS)
            continue

        ui_log(f"Saved {path}", progress_callback)
        stats["downloaded"] += 1
        mark_success(qid)

        print_stats(progress_callback)
        item_counter += 1
        if item_counter % 20 == 0:
            print_db_summary(progress_callback)

        sleep_interruptible(SLEEP_BETWEEN_ITEMS)

    ui_log("Crawler stopped.", progress_callback)


if __name__ == "__main__":
    run_crawler()
