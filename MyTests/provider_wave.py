from __future__ import annotations

import math # Добавлен импорт math
import logging
import time
import uuid
from decimal import Decimal

from sdc11073.loghelper import basic_logging_setup
from sdc11073.mdib import ProviderMdib
from sdc11073.provider import SdcProvider
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscoverySingleAdapter
from sdc11073.xml_types.dpws_types import ThisDeviceType
from sdc11073.xml_types.dpws_types import ThisModelType
from sdc11073.xml_types.pm_types import SampleArrayValue # Добавлен SampleArrayValue

if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    base_uuid = uuid.UUID('{cc013678-79f6-403c-998f-3cc0cc050230}')
    my_uuid = uuid.uuid5(base_uuid, "12345")

    # Подгрузка mdib с файла
    mdib = ProviderMdib.from_mdib_file("mdib.xml")

    # Объявление компонентов(полей) провайдера
    model = ThisModelType(model_name='TestModel',
                          manufacturer='TestManufacturer',
                          manufacturer_url='http://testurl.com')
    components = SdcProviderComponents(role_provider_class=ExtendedProduct)
    device = ThisDeviceType(friendly_name='TestDevice', serial_number='12345')
    discovery = WSDiscoverySingleAdapter("WLAN")  # Wi-Fi если на windows или wlan0 если линукс

    # Создание экземпляра Provider
    provider = SdcProvider(ws_discovery=discovery,
                           epr=my_uuid,
                           this_model=model,
                           this_device=device,
                           device_mdib_container=mdib,
                           specific_components=components)

    # Запуск Дискавери
    discovery.start()

    # Запуск всех сервисов провайера
    provider.start_all()

    # Публикация провайлера в сеть чтобы его можно было обнаружить
    provider.publish()

    # Конфигурация волновой формы
    waveform_handle = "wave_form_test"
    sin_rad = 0.0

    # Период дискретизации (время между двумя точками).
    # В идеале должен совпадать с SamplePeriod из Descriptor мдиба, но для теста возьмем 0.005 сек
    sample_period = 0.005

    while True:
        # Логика для waveforms
        # В SDC мы не отправляем каждую точку отдельно (это создаст огромный трафик).
        # Мы собираем "пакет" (Chunk) сэмплов и отправляем их разом.
        samples = []

        # Засекаем время начала этого пакета данных
        now = time.time()
        start_time = now

        # Генерируем пачку сэмплов (например, 10 штук за цикл)
        for _ in range(10):
            sin_rad += 0.1
            if sin_rad > 2 * math.pi:
                sin_rad -= 2 * math.pi
            # Значение синусоиды от -10 до 10
            samples.append(Decimal(math.sin(sin_rad) * 10))

        # Время окончания = время начала + (количество точек * период)
        stop_time = start_time + (len(samples) * sample_period)

        # Обновление метрик
        # Транзакция берет собранный массив и обновляет состояние в MDIB.
        # Это вызывает событие WaveformStream, которое улетает подписчикам.
        # Используем rt_sample_state_transaction для потоковых данных
        with provider.mdib.rt_sample_state_transaction() as tr:
            # Обновление Waveform
            wf_state = tr.get_state(waveform_handle)
            a = wf_state.MetricValue.Samples
            if wf_state:
                # Наполняем сэмплы
                wf_state.MetricValue.Samples = samples
                wf_state.MetricValue.DeterminationTime = now
                wf_state.MetricValue.StartTime = start_time
                wf_state.MetricValue.StopTime = stop_time

                # Аннотации обычно не меняются каждый раз, можно их не трогать или оставить пустыми
                wf_state.MetricValue.Annotations = []
                wf_state.MetricValue.ApplyAnnotations = []

        time.sleep(0.05) # Небольшая задержка, чтобы эмулировать частоту дискретизации
