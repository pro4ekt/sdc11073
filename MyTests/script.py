"""
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
"""

import mysql.connector

def main():
    try:
        db = mysql.connector.connect(
            host="localhost",
            user="root",
            password="1234",
            database="TestDB"
        )
        cur = db.cursor()  # или: cursor = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM users")
        rows = cur.fetchall()
        if not rows:
            print("Таблица users пуста.")
        else:
            for row in rows:
                print(row)
    except mysql.connector.Error as err:
        print(f"Ошибка MySQL: {err}")
    except Exception as e:
        print(f"Неожиданная ошибка: {e}")
    finally:
        try:
            cur.close()
            db.close()
        except:
            pass

if __name__ == "__main__":
    main()