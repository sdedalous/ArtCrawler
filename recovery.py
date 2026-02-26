import sqlite3
import re

DB_PATH = "art.db"

LOGS = {
    "403": "failed_403.log",
    "metadata": "failed_metadata.log",
    "download": "failed_download.log",
    "query": "failed_query.log",
}

def extract_qids(path):
    qids = set()
    try:
        with open(path) as f:
            for line in f:
                m = re.match(r"(Q\d+)", line)
                if m:
                    qids.add(m.group(1))
    except FileNotFoundError:
        pass
    return qids

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

total_updated = 0

for reason, logfile in LOGS.items():
    qids = extract_qids(logfile)
    print(f"{logfile}: {len(qids)} failures")

    for qid in qids:
        if reason == "403":
            c.execute("""
                UPDATE items
                SET last_fail_reason = '403',
                    done = 1,
                    wifi_retry = 0
                WHERE qid = ?
            """, (qid,))
        else:
            c.execute("""
                UPDATE items
                SET last_fail_reason = ?,
                    wifi_fail_count = wifi_fail_count + 1,
                    wifi_retry = 1,
                    done = 0
                WHERE qid = ?
            """, (reason, qid))

        total_updated += 1

conn.commit()
conn.close()

print(f"Updated {total_updated} items.")
