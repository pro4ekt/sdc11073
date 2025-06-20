from __future__ import annotations

import socket
import logging
import time
import uuid
from decimal import Decimal

import sdc11073.entity_mdib.entity_providermdib
from sdc11073.location import SdcLocation
from sdc11073.loghelper import basic_logging_setup
from sdc11073.mdib import ProviderMdib
from sdc11073.consumer import SdcConsumer
from sdc11073.roles.product import ExtendedProduct
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073.xml_types.dpws_types import ThisDeviceType
from sdc11073.xml_types.dpws_types import ThisModelType
from sdc11073.xml_types.pm_types import NumericMetricValue

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.146.164.72", 80))
        return s.getsockname()[0]
    except Exception:
        return "Exception"
    finally:
        s.close()

basic_logging_setup(level=logging.INFO)


discovery = WSDiscovery(get_local_ip())
discovery.start()

print("WS-Discovery запустился")

service = discovery.search_services(timeout=3)

if not service:
    print("❌ Провайдеры не найдены")
else:
    print(f"✅ Найдено {len(service)} провайдер(ов):")
    for svc in service:
        print(f"- EPR: {svc.epr}")
        print(f"  Adresses: {svc.x_addrs}")
        print(f"  Types: {svc.types}")

discovery.stop()

# Инициализируем консюмера
consumer = SdcConsumer()

# Запускаем дискавери + подключение
services = consumer.start_all()

# MDIB загружен — можно получить значения
mdib = consumer.mdib

