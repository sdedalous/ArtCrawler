import sqlite3

DB_PATH = "/storage/emulated/0/Download/ArtCrawler/art.db"

def get_db():
    return sqlite3.connect(DB_PATH)


# ---------------------------------------------------------
# MAIN INITIALIZATION
# ---------------------------------------------------------
def init_db():
    conn = get_db()
    c = conn.cursor()

    # Main items table
    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            qid TEXT PRIMARY KEY,
            year INTEGER,
            century INTEGER,
            bucket TEXT,
            priority INTEGER,
            done INTEGER DEFAULT 0
        )
    """)

    # Old indexer state (kept for compatibility)
    c.execute("""
        CREATE TABLE IF NOT EXISTS indexer_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            offset INTEGER DEFAULT 0
        )
    """)
    c.execute("INSERT OR IGNORE INTO indexer_state (id, offset) VALUES (1, 0)")

    # NEW: Per-class offsets for rotating indexer
    c.execute("""
        CREATE TABLE IF NOT EXISTS class_offsets (
            class_name TEXT PRIMARY KEY,
            offset INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------
# OLD OFFSET (kept for compatibility)
# ---------------------------------------------------------
def get_indexer_offset():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT offset FROM indexer_state WHERE id = 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def set_indexer_offset(offset):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE indexer_state SET offset = ? WHERE id = 1", (offset,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------
# NEW: PER-CLASS OFFSET SYSTEM
# ---------------------------------------------------------
def get_class_offset(class_name):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT offset FROM class_offsets WHERE class_name = ?", (class_name,))
    row = c.fetchone()
    conn.close()
    if row is None:
        return 0
    return row[0]


def set_class_offset(class_name, offset):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO class_offsets (class_name, offset)
        VALUES (?, ?)
        ON CONFLICT(class_name) DO UPDATE SET offset=excluded.offset
    """, (class_name, offset))
    conn.commit()
    conn.close()


# ---------------------------------------------------------
# YEAR CLASSIFICATION (kept exactly as you wrote it)
# ---------------------------------------------------------
def classify_year(year):
    if year is None:
        return ("unknown", 99)

    if year >= 1950:
        return ("contemporary", 1)
    if 1900 <= year <= 1949:
        return ("modern", 2)
    if 1800 <= year <= 1899:
        return ("romantic", 3)
    if 1600 <= year <= 1799:
        return ("classical", 4)
    if 1400 <= year <= 1599:
        return ("renaissance", 5)
    if year < 1400:
        return ("medieval", 6)

    return ("unknown", 99)
