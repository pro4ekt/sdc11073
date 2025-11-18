import mysql.connector
from decimal import Decimal
import time

from sdc11073.mdib import ProviderMdib


class DBWorker:
    def __init__(self, host: str, user: str, password: str, database: str, mdib : ProviderMdib):
        self.host = host
        self.user = user
        self.password = password
        self.database = database

        self.db = mysql.connector.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database)

        self.device_id = 0
        self.mdib = mdib
        self.metrics = []
        self.alarms = []

    def register(self,device_name: str, device_type: str, device_location: str):
        try:
            cur = self.db.cursor()

            cur.execute(
                "INSERT INTO devices (name, device_type, location) VALUES (%s, %s, %s)",
                (device_name, device_type, device_location)
            )
            device_id = cur.lastrowid
            self.device_id = device_id

            obj = self.mdib.descriptions.objects
            metrics_descriptors = []

            for containers in obj:
                type_name = type(containers).__name__
                if "NumericMetricDescriptor" in type_name:
                    metrics_descriptors.append(containers)

            for metric in metrics_descriptors:
                cur.execute(
                    "INSERT INTO metrics (device_id, name, unit, threshold) VALUES (%s, %s, %s, %s)",
                    (self.device_id, metric.Handle, metric.Unit.Code, 0))
                self.metrics.append([metric.Handle, cur.lastrowid])

            self.db.commit()
        finally:
            try:
                cur.close()
            except:
                pass

    def observation_register(self, metric_handle: str, value: Decimal):
        try:
            cur = self.db.cursor()
            for m in self.metrics:
                if (m[0] == metric_handle):
                    metric = m[1]
                    break
            cur.execute("INSERT INTO observations (metric_id, time, value) VALUES (%s, %s, %s)",
                        (metric, time.strftime("%Y-%m-%d %H:%M:%S"), value))
            self.db.commit()
        finally:
            try:
                cur.close()
            except:
                pass

    def operation_register(self, provider_id: int, operation_type: str, performed_by: str):
        try:
            cur = self.db.cursor()
            cur.execute(
                "INSERT INTO operations (consumer_id, provider_id, time, type, performed_by) VALUES (%s, %s, %s, %s, %s)",
                (self.device_id, provider_id, time.strftime("%Y-%m-%d %H:%M:%S"), operation_type, performed_by))
            self.db.commit()
        finally:
            try:
                cur.close()
            except:
                pass

    def alarm_register(self, metric_handle: str):

        try:
            cur = self.db.cursor()
            for m in self.metrics:
                if (m[0] == metric_handle):
                    metric_id = m[1]
                    break
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "INSERT INTO alarms (metric_id, device_id, state, triggered_at, threshold) VALUES (%s, %s, %s, %s, %s)",
                (metric_id, self.device_id, "firing", now, 0))
            alarm_id = cur.lastrowid
            self.alarms.append([alarm_id, metric_id , metric_handle, "firing"])
            self.db.commit()
        finally:
            try:
                cur.close()
            except:
                pass

    def alarm_resolve(self, metric_handle: str):

        try:
            for a in self.alarms:
                if (a[2] == metric_handle):
                    alarm = a
                    break
            cur = self.db.cursor()
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "UPDATE alarms SET state=%s, resolved_at=%s WHERE id=%s",
                ("resolved", now, alarm[0])
            )
            self.alarms.remove(alarm)
            self.db.commit()
        finally:
            try:
                cur.close()
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
                #self.db.close()
            except:
                pass