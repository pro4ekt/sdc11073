import os
import platform

def get_cpu_temperature():
    """
    Универсальная функция получения температуры CPU.
    Работает на Raspberry Pi, большинстве Linux-систем.
    На Windows и Mac возвращает заглушку.
    """
    system = platform.system()

    if system == 'Linux':
        # Стандартный путь для Raspberry Pi и других Linux
        path = '/sys/class/thermal/thermal_zone0/temp'
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    raw_temp = f.read().strip()
                    return round(int(raw_temp) / 1000.0, 1)
            except Exception as e:
                print(f"[Ошибка чтения температуры]: {e}")
                return 42.0

    # Если не Linux или файл не найден — вернуть заглушку
    print("[INFO] Температура недоступна на этой системе.")
    return 42.0


if __name__ == '__main__':
    temp = get_cpu_temperature()
    print(f"🌡 Температура CPU: {temp} °C")