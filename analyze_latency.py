import sqlite3
import csv
import os

def analyze_latency():
    # Пути к базам данных
    base_dir = os.path.dirname(os.path.abspath(__file__))
    prov_db = os.path.join(base_dir, "MyTests", "correct_provider", "provider_latency.db")
    gw_db = os.path.join(base_dir, "GateWay", "gateway_latency.db")

    if not all([os.path.exists(prov_db), os.path.exists(gw_db)]):
        print("Ошибка: Не все базы данных найдены. Проверьте пути файлов.")
        print(f"Ищем: \n1. {prov_db}\n2. {gw_db}")
        return

    # Подключаемся к базе Provider в памяти и цепляем остальные
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()

    cursor.execute(f"ATTACH DATABASE '{prov_db}' AS prov")
    cursor.execute(f"ATTACH DATABASE '{gw_db}' AS gw")

    # SQL-запрос теперь сопоставляет данные с помощью LIKE для игнорирования префикса 'urn:uuid:'
    query = """
        SELECT 
            p.timestamp AS provider_ts,
            MIN(g.local_timestamp) AS gateway_ts,
            ABS(MIN(g.local_timestamp) - p.timestamp) AS total_latency,
            p.handle,
            p.value
        FROM prov.provider_logs p
        JOIN gw.latency_logs g 
            ON g.epr LIKE '%' || p.epr || '%'
            AND p.handle = g.handle 
            AND (p.value = g.value OR ABS(CAST(p.value AS REAL) - CAST(g.value AS REAL)) < 0.001)
            AND ABS(g.local_timestamp - p.timestamp) < 10.0
        GROUP BY p.timestamp, p.handle, p.value
        ORDER BY p.timestamp ASC
    """

    cursor.execute(query)
    rows = cursor.fetchall()

    if not rows:
        print("Нет совпадений данных. Убедитесь, что скрипты работали одновременно.")
        return

    # Добавим предупреждение при подозрительно большом количестве записей (чтобы выявить старые данные если скрипты не перезапускались)
    if len(rows) > 10000:
        print(f"Внимание: Обработано очень много записей ({len(rows)}). Возможно базы данных не были очищены при перезапуске скриптов.")

    csv_path = os.path.join(base_dir, "latency_report.csv")

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Provider_TS", "GateWay_TS", "Total_Latency_Seconds", "Handle", "Value"])

        total_lat = 0
        for row in rows:
            writer.writerow(row)
            total_lat += row[2]

    avg_latency = total_lat / len(rows)
    print(f"Успешно обработано {len(rows)} записей.")
    print(f"Отчет сохранен в: {csv_path}")
    print(f"Средняя задержка (Provider -> OPC UA GateWay): {avg_latency:.4f} секунд ({avg_latency * 1000:.2f} мс)")

if __name__ == "__main__":
    analyze_latency()
