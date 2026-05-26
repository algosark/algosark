import sqlite3

conn = sqlite3.connect("users.db")
cursor = conn.cursor()

cursor.execute("""
ALTER TABLE users ADD COLUMN marketing_opt_in INTEGER DEFAULT 0;
""")

conn.commit()
conn.close()