from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal
import threading

# --- Заглушки для отсутствующих библиотек ---
# Если вы запускаете код на ПК, а не на Raspberry Pi,
# эти заглушки предотвратят ошибки импорта.
try:
    from sense_hat import SenseHat
except ImportError:
    print("Warning: 'sense_hat' library not found. Using a mock object.")


    class SenseHat:
        def __init__(self): self.humidity = 60; self.temperature = 25

        def clear(self, *args, **kwargs): pass

        def set_pixel(self, *args, **kwargs): pass

        def get_pixels(self): return [[0, 0, 0]] * 64

        def set_pixels(self, *args, **kwargs): pass

        class SenseStick:
            def get_events(self): return []

        stick = SenseStick()

try:
    import numpy as np
    import sounddevice as sd
except ImportError:
    print("Warning: 'numpy' or 'sounddevice' not found. Sound will be disabled.")
    np = None
    sd = None
# -----------------------------------------

from myproviderimpl import MySdcProvider
from sdc11073.mdib import ProviderMdib
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.provider.operations import SetValueOperation
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscoverySingleAdapter
from sdc11073.xml_types.dpws_types import ThisDeviceType, ThisModelType
from sdc11073.xml_types.pm_types import AlertSignalPresence

# Константы для LED-матрицы (без изменений)
OFFSET_LEFT = 1
OFFSET_TOP = 3
NUMS = [1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 1, 1, 0, 0, 1, 0,
        1, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 0, 0, 1, 0, 1, 1, 1, 1, 0, 0, 1, 0, 0,
        1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1,
        1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1,
        1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 0, 1, 0, 0, 1]


# --- Функции для отображения (без изменений) ---
def show_digit(sense, val, xd, yd, r, g, b):
    offset = val * 15
    for p in range(offset, offset + 15):
        xt = p % 3
        yt = (p - offset) // 3
        sense.set_pixel(xt + xd, yt + yd, r * NUMS[p], g * NUMS[p], b * NUMS[p])


def show_number(sense, val, r, g, b):
    abs_val = abs(val)
    tens = abs_val // 10
    units = abs_val % 10
    if abs_val > 9:
        show_digit(sense, tens, OFFSET_LEFT, OFFSET_TOP, r, g, b)
    show_digit(sense, units, OFFSET_LEFT + 4, OFFSET_TOP, r, g, b)


def t_show(sense, r, g, b):
    sense.set_pixel(0, 0, r, g, b);
    sense.set_pixel(1, 0, r, g, b);
    sense.set_pixel(2, 0, r, g, b)
    sense.set_pixel(1, 1, r, g, b);
    sense.set_pixel(1, 2, r, g, b)


def h_show(sense, r, g, b):
    sense.set_pixel(0, 0, r, g, b);
    sense.set_pixel(0, 1, r, g, b);
    sense.set_pixel(0, 2, r, g, b)
    sense.set_pixel(1, 1, r, g, b)
    sense.set_pixel(2, 2, r, g, b);
    sense.set_pixel(2, 1, r, g, b);
    sense.set_pixel(2, 0, r, g, b)


def background(sense, r, g, b):
    pixels = [[r, g, b] if pixel == [0, 0, 0] else pixel for pixel in sense.get_pixels()]
    sense.set_pixels(pixels)


def play_sound(duration, frequency):
    if sd is None or np is None:
        return
    sample_rate = 44100
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    wave = 0.1 * np.sin(2 * np.pi * frequency * t)
    sd.play(wave, sample_rate)
    sd.wait()


class SenseHatProvider:
    def __init__(self, device_id, wlan_interface):
        self.device_id = device_id
        self.wlan_interface = wlan_interface
        self.sense = SenseHat()
        self.provider = None
        self.show_temp = True
        self.temp_alarm_silenced_until = 0
        self.is_running = True

    def _setup_sdc(self):
        """Инициализирует и запускает SDC-провайдер."""
        base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050231}')
        my_uuid = uuid.uuid5(base_uuid, "test_provider_2")
        mdib = ProviderMdib.from_mdib_file("Pi5 CPU Temp + Fans Control/mdib2.xml")

        model = ThisModelType(model_name='TestModel', manufacturer='TestManufacturer')
        device = ThisDeviceType(friendly_name='TestDevice2', serial_number='123456')
        components = SdcProviderComponents(role_provider_class=ExtendedProduct)
        discovery = WSDiscoverySingleAdapter(self.wlan_interface)

        self.provider = MySdcProvider(ws_discovery=discovery, epr=my_uuid, this_model=model,
                                      this_device=device, device_mdib_container=mdib,
                                      specific_components=components)

        # Регистрация обработчика для операции управления тревогой
        op_handle = "temperature_alert_control"
        alert_op = self.provider.mdib.descriptions.by_handle.get(op_handle)
        if isinstance(alert_op, SetValueOperation):
            self.provider.add_operation_callback(self.handle_set_value_op, op_handle)

        self.provider.start_all()
        self.provider.publish()

        with self.provider.mdib.metric_state_transaction() as tr:
            state = tr.get_state("device_id")
            state.MetricValue.Value = Decimal(self.device_id)

    def handle_set_value_op(self, operation, value):
        """Обрабатывает входящие SetValue операции."""
        logging.info(f"Received SetValue for {operation.handle} with value {value}")
        # Здесь мы просто глушим тревогу на 3 секунды
        self.temp_alarm_silenced_until = time.time() + 3
        operation.set_value(value)  # Подтверждаем операцию

    def _joystick_worker(self):
        """Поток для обработки событий джойстика."""
        while self.is_running:
            for e in self.sense.stick.get_events():
                if e.action == "pressed":
                    self.show_temp = not self.show_temp
            time.sleep(0.05)

    def _update_sensors(self):
        """Читает данные с сенсоров и обновляет MDIB."""
        humidity = Decimal(self.sense.humidity)
        temperature = Decimal(self.sense.temperature)

        with self.provider.mdib.metric_state_transaction() as tr:
            tr.get_state("humidity").MetricValue.Value = humidity
            tr.get_state("temperature").MetricValue.Value = temperature
        return humidity, temperature

    def _process_alarms(self, humidity, temperature):
        """Проверяет и обновляет состояния тревог."""
        # Логика для температуры
        temp_range = self.provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0]
        is_out_of_range = not (temp_range.Lower <= temperature <= temp_range.Upper)
        is_silenced = time.time() < self.temp_alarm_silenced_until

        with self.provider.mdib.alert_state_transaction() as tr:
            tr.get_state("al_condition_temperature").Presence = is_out_of_range
            sig_state = tr.get_state("al_signal_temperature")
            if is_out_of_range and not is_silenced:
                sig_state.Presence = AlertSignalPresence.ON
            else:
                sig_state.Presence = AlertSignalPresence.OFF

        # Логика для влажности (без изменений)
        hum_range = self.provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0]
        if not (hum_range.Lower <= humidity <= hum_range.Upper):
            with self.provider.mdib.alert_state_transaction() as tr:
                tr.get_state("al_condition_humidity").Presence = True
                tr.get_state("al_signal_humidity").Presence = AlertSignalPresence.ON

    def _update_display(self, humidity, temperature):
        """Обновляет LED-матрицу."""
        self.sense.clear()
        if self.show_temp:
            t_show(self.sense, 255, 255, 0)
            show_number(self.sense, int(temperature + 0.5), 255, 165, 40)

            temp_range = self.provider.mdib.entities.by_handle("temperature").state.PhysiologicalRange[0]
            if temperature < temp_range.Lower:
                background(self.sense, 51, 153, 255)
            elif temperature > temp_range.Upper:
                background(self.sense, 130, 0, 0)
            else:
                background(self.sense, 51, 204, 51)

            if self.provider.mdib.entities.by_handle("al_signal_temperature").state.Presence == AlertSignalPresence.ON:
                play_sound(1, 420)
        else:
            h_show(self.sense, 255, 255, 0)
            show_number(self.sense, int(humidity + 0.5), 255, 100, 40)

            hum_range = self.provider.mdib.entities.by_handle("humidity").state.PhysiologicalRange[0]
            if humidity < hum_range.Lower:
                background(self.sense, 204, 153, 102)
            elif humidity > hum_range.Upper:
                background(self.sense, 51, 102, 204)
            else:
                background(self.sense, 51, 204, 51)

            if self.provider.mdib.entities.by_handle("al_signal_humidity").state.Presence == AlertSignalPresence.ON:
                play_sound(1, 440)

    def run(self):
        """Основной цикл приложения."""
        logging.basicConfig(level=logging.INFO)
        self._setup_sdc()

        joystick_thread = threading.Thread(target=self._joystick_worker, daemon=True)
        joystick_thread.start()

        try:
            while self.is_running:
                humidity, temperature = self._update_sensors()
                self._process_alarms(humidity, temperature)
                self._update_display(humidity, temperature)
                time.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down...")
        finally:
            self.is_running = False
            self.provider.stop_all()


if __name__ == '__main__':
    app = SenseHatProvider(device_id=0, wlan_interface="wlan0")
    app.run()
