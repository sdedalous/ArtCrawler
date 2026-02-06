from db import get_db

conn = get_db()
c = conn.cursor()
c.execute("DELETE FROM items WHERE done = 1")
conn.commit()
conn.close()

print("Pruned completed items.")
