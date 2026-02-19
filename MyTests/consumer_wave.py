from __future__ import annotations

import socket
import logging
import time
import uuid
import matplotlib.pyplot as plt # Добавлен импорт для графиков
from decimal import Decimal
from copy import deepcopy

import sdc11073.entity_mdib.entity_providermdib
from aiohttp.helpers import set_result
from sdc11073 import observableproperties
from sdc11073.definitions_sdc import SdcV1Definitions
from sdc11073.location import SdcLocation
from sdc11073.loghelper import basic_logging_setup
from sdc11073.mdib import ProviderMdib, ConsumerMdib
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib.statecontainers import AlertSignalStateContainer
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073.xml_types.pm_types import AlertSignalPresence
from sdc11073.xml_types.dpws_types import ThisDeviceType
from sdc11073.xml_types.dpws_types import ThisModelType
from sdc11073.xml_types.pm_types import NumericMetricValue
from sdc11073.pysoap.msgfactory import CreatedMessage
from sdc11073.xml_types.actions import periodic_actions
from sdc11073.consumer.serviceclients.setservice import SetServiceClient

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.146.164.72", 80))
        return s.getsockname()[0]
    except Exception:
        return "Exception"
    finally:
        s.close()

# Глобальный буфер для данных графика
wave_data = []
MAX_SAMPLES = 500  # Сколько точек хранить на экране

# Функция для обработки обновлений волновых форм (RealTimeSampleArray)
def on_waveform_update(waveform_by_handle: dict):
    # waveform_by_handle - словарь, где ключ = handle, значение = SampleArrayValue
    a = waveform_by_handle
    for handle, sample_array in waveform_by_handle.items():
        if handle == "wave_form_test":
            # Добавляем новые сэмплы в общий буфер
            new_samples = [float(x) for x in sample_array.MetricValue.Samples]
            wave_data.extend(new_samples)

            # Ограничиваем размер буфера, чтобы память не текла
            if len(wave_data) > MAX_SAMPLES:
                del wave_data[:len(wave_data) - MAX_SAMPLES]

            # sample_array.Samples - это список Decimal
            #print(f"Received waveform chunk for {handle}: {len(sample_array.MetricValue.Samples)}")
#Функция которая потом будет вызываться в observableproperties.bind которая нужна для вывода обновлённых метрик
def on_metric_update(metrics_by_handle: dict):
    if(consumer.mdib.entities.by_handle("liquid").state.MetricValue.Value == 0):
        print("Liquid is empty, please inject more liquid")
    #print(f"Got update on Metric with handle: {list(metrics_by_handle.keys())}")
    #print(f"Curent CPU Temperature : {consumer.mdib.entities.by_handle("met1").state.MetricValue.Value}")
    #print(f"Current Alarm State: {consumer.mdib.entities.by_handle("als1").state.Presence}")

def get_number():
    value = Decimal(input("Input your Value: "))
    return value

if __name__ == '__main__':
    #logging.basicConfig(level=logging.INFO)

    # Создаём и запускаем discovery для поиска
    discovery = WSDiscovery(get_local_ip())
    discovery.start()

    # Достаём все сервиы которые были найдены в discovery
    services = discovery.search_services(timeout=1)

    # затычка конкретно для меня потомушо у меня ток 1 сервис
    service = services[0]

    # Инициализация consumer
    consumer = SdcConsumer.from_wsd_service(wsd_service=service, ssl_context_container=None)

    time.sleep(3)

    # Старт консьюмера
    consumer.start_all()

    # Инициализация mdib от provider
    mdib = ConsumerMdib(consumer)
    mdib.init_mdib()

    # Подписываемся на обновления метрик и волновых форм
    # waveform_by_handle - специальный аргумент для потоковых данных (WaveformStream)
    observableproperties.bind(mdib, metrics_by_handle=on_metric_update, waveform_by_handle=on_waveform_update)

    print("Subscribed to waveforms. Waiting for data...")

    # Настройка графика
    plt.ion()  # Включаем интерактивный режим
    fig, ax = plt.subplots()
    line, = ax.plot([], [])
    ax.set_ylim(-15, 15) # Установите пределы по Y в зависимости от амплитуды сигнала
    ax.set_xlim(0, MAX_SAMPLES)
    ax.grid(True)
    plt.title("RealTime Waveform")

    while True:
        # Обновляем график в главном потоке
        if wave_data:
            line.set_ydata(wave_data)
            line.set_xdata(range(len(wave_data)))
            # Если данных меньше чем MAX_SAMPLES, можно динамически менять xlim, но проще фиксировать

            plt.draw()
            plt.pause(0.01) # Даем времени matplotlib отрисовать кадр
        else:
            time.sleep(0.1)
