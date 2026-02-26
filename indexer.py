import time
import requests

from db import (
    get_db,
    init_db,
    classify_year,
    get_class_offset,
    set_class_offset,
)

STOP_INDEXER = False

SPARQL_URL = "https://query.wikidata.org/sparql"

YEAR_MIN = 1880
YEAR_MAX = 2024

# Smaller batches = lighter queries = fewer timeouts
BATCH_LIMIT = 60
SLEEP_BETWEEN_BATCHES = 5

HEADERS = {
    "Accept": "application/sparql-results+json",
    "User-Agent": "ArtCrawler/1.0 (mobile; portrait-harvest; contact: you@example.com)"
}

CLASSES = [
    ("portrait", "wd:Q134307"),
]






def ui_log(msg, callback):
    if callback:
        callback(msg)
    else:
        print(msg)


def build_query(class_qid, offset):
    return f"""
    SELECT ?item ?itemLabel ?image ?year WHERE {{

      # Portraits (genre = portrait)
      ?item wdt:P136 wd:Q134307 .

      # Any valid image property
      ?item (wdt:P18|wdt:P4765|wdt:P6802|wdt:P7482) ?image .

      # Must have a creation date (your DB requires this)
      ?item wdt:P571 ?date .
      BIND(YEAR(?date) AS ?year)

      # Year range filter
      FILTER(?year >= {YEAR_MIN} && ?year <= {YEAR_MAX})

      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}
    LIMIT {BATCH_LIMIT}
    OFFSET {offset}
    """



def fetch_items(class_qid, offset):
    query = build_query(class_qid, offset)
    response = requests.get(
        SPARQL_URL,
        params={"query": query},
        headers=HEADERS,
        timeout=180  # was 60
    )

    if response.status_code != 200:
        raise Exception(f"SPARQL query failed ({response.status_code})")

    data = response.json()
    results = []

    for row in data["results"]["bindings"]:
        qid = row["item"]["value"].split("/")[-1]
        year_val = row.get("year", {}).get("value")
        if not year_val:
            continue
        try:
            year = int(year_val)
        except ValueError:
            continue
        results.append((qid, year))

    return results


def insert_item(qid, year):
    bucket, priority = classify_year(year)
    century = (year // 100) + 1

    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT OR IGNORE INTO items (qid, year, century, bucket, priority)
            VALUES (?, ?, ?, ?, ?)
        """, (qid, year, century, bucket, priority))
        conn.commit()
    finally:
        conn.close()


def run_indexer(progress_callback=None):
    init_db()

    classes_done = {name: False for name, _ in CLASSES}
    consecutive_failures = 0

    ui_log("Indexer starting with Tier 1 classes only.", progress_callback)
    ui_log("Classes: " + ", ".join(name for name, _ in CLASSES), progress_callback)

    while True:
        if STOP_INDEXER:
            ui_log("Indexer stopping...", progress_callback)
            break

        if all(classes_done.values()):
            ui_log("Indexer complete — all classes exhausted.", progress_callback)
            break

        for class_name, class_qid in CLASSES:
            if classes_done[class_name]:
                continue

            offset = get_class_offset(class_name)
            ui_log(f"[INDEXER] Class={class_name}, offset={offset}", progress_callback)

            try:
                items = fetch_items(class_qid, offset)
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                # cap backoff at 5 minutes, but start gentler
                wait_time = min(30 * consecutive_failures, 300)
                ui_log(f"[ERROR] Fetch error for {class_name} at offset {offset}: {e}", progress_callback)
                ui_log(f"[INFO] Sleeping {wait_time}s before retry…", progress_callback)
                time.sleep(wait_time)
                continue

            if not items:
                ui_log(f"[INFO] No more items for class {class_name}. Marking as done.", progress_callback)
                classes_done[class_name] = True
                continue

            for qid, year in items:
                insert_item(qid, year)

            new_offset = offset + BATCH_LIMIT
            set_class_offset(class_name, new_offset)
            ui_log(f"[INFO] Indexed {len(items)} items for {class_name}. New offset={new_offset}", progress_callback)

            ui_log(f"[INFO] Sleeping {SLEEP_BETWEEN_BATCHES}s before next batch…", progress_callback)
            time.sleep(SLEEP_BETWEEN_BATCHES)

        ui_log("[INFO] Completed one full pass over classes. Short pause…", progress_callback)
        time.sleep(5)


if __name__ == "__main__":
    run_indexer()
