import sqlite3

conn = sqlite3.connect("test.db")
cur = conn.cursor()

cur.execute("CREATE TABLE IF NOT EXISTS cpu_fan_data "
                "(cpu_temp REAL, fan_speed TEXT, cond TEXT, sig TEXT)")
if(True):
        cur.execute("INSERT INTO cpu_fan_data (cpu_temp, fan_speed, sig, cond) VALUES (?, ?, ?, ?)",(1, "str(fan)", "str(cond)", "str(sig)"))
else:
        cur.execute("DELETE FROM cpu_fan_data")

conn.commit()
cur.close()
conn.close()