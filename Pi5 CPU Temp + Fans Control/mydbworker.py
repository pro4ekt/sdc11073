import mysql.connector
from decimal import Decimal
import time

class DBWorker:
    def __init__(self, host: str, user: str, password: str, database: str):
        self.host = host
        self.user = user
        self.password = password
        self.database = database

        self.db = mysql.connector.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database)

    def register(self):

        try:
            cur = self.db.cursor()

            # 🔹 Вставляем устройство без проверки
            cur.execute(
                "INSERT INTO devices (name, device_type, location) VALUES (%s, %s, %s)",
                ("Provider", "provider", "Würzburg, DE")
            )
            device_id = cur.lastrowid  # Получаем сгенерированный ID
            global DEVICE_ID
            DEVICE_ID = device_id  # сохраняем в глобальную переменную device id в базе данных

            # 🔹 Вставляем метрики, связанные с этим устройством
            metrics = [
                ("cpu_temp", "C", 54),
                ("fan_rotation", "bool", 999)
            ]

            for name, unit, threshold in metrics:
                cur.execute(
                    "INSERT INTO metrics (device_id, name, unit, threshold) VALUES (%s, %s, %s, %s)",
                    (device_id, name, unit, threshold)
                )
                metric_id = cur.lastrowid  # если нужно использовать дальше
                if name == "cpu_temp":
                    global TEMP_ID
                    TEMP_ID = metric_id

            self.db.commit()

        finally:
            try:
                cur.close()
                self.db.close()
            except:
                pass

    def observation_register(self, metric_id: int, value: Decimal):
        try:
            cur = self.db.cursor()
            cur.execute("INSERT INTO observations (metric_id, time, value) VALUES (%s, %s, %s)",
                        (metric_id, time.strftime("%Y-%m-%d %H:%M:%S"), value,))
            self.db.commit()
        finally:
            try:
                cur.close()
                self.db.close()
            except:
                pass

    def operation_register(self):
        try:
            cur = self.db.cursor()
            cur.execute(
                "INSERT INTO operations (consumer_id, provider_id, time, type, performed_by) VALUES (%s, %s, %s, %s, %s)",
                (DEVICE_ID, DEVICE_ID, time.strftime("%Y-%m-%d %H:%M:%S"), "alert_control", "provider"))
            self.db.commit()
        finally:
            try:
                cur.close()
                self.db.close()
            except:
                pass

    def alarm_register(self):

        try:
            cur = self.db.cursor()

            now = time.strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "INSERT INTO alarms (metric_id, device_id, state, triggered_at, threshold) VALUES (%s, %s, %s, %s, %s)",
                (TEMP_ID, DEVICE_ID, "firing", now, 54))
            alarm_id = cur.lastrowid
            global TEMP_ALARM_ID
            TEMP_ALARM_ID = alarm_id

            self.db.commit()
        finally:
            try:
                cur.close()
                self.db.close()
            except:
                pass

    def alarm_resolve(self, alarm_id: int):

        try:
            cur = self.db.cursor()
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "UPDATE alarms SET state=%s, resolved_at=%s WHERE id=%s",
                ("resolved", now, alarm_id)
            )
            self.db.commit()
        finally:
            try:
                cur.close()
                self.db.close()
            except:
                pass

    def delete_db(self):
        try:
            cur = self.db.cursor()
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for tbl in ['observations', 'alarms', 'operations', 'metrics', 'devices']:
                cur.execute(f"TRUNCATE TABLE {tbl}")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
            self.db.commit()
        finally:
            try:
                cur.close()
                self.db.close()
            except:
                pass