import sqlite3

DB_PATH = "/storage/emulated/0/Download/ArtCrawler/art.db"

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

print("=== Last 20 Indexed Items ===")

# Ensure table exists
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [t[0] for t in c.fetchall()]
if "items" not in tables:
    print("ERROR: 'items' table not found.")
    conn.close()
    raise SystemExit

# Fetch last 20 items by insertion order (rowid)
c.execute("""
    SELECT qid, year, century, bucket, priority, done
    FROM items
    ORDER BY rowid DESC
    LIMIT 20
""")

rows = c.fetchall()

if not rows:
    print("No items found.")
else:
    for r in rows:
        qid, year, century, bucket, priority, done = r
        print(f"QID: {qid}, Year: {year}, Century: {century}, "
              f"Bucket: {bucket}, Priority: {priority}, Done: {done}")

conn.close()
