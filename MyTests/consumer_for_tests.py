import pathlib
import ssl
import time
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.xml_types.actions import periodic_actions
from sdc11073 import certloader
ADAPTER_IP = '192.168.248.83'  # поменяй на свой IP
# SSL: тестовые сертификаты sdc11073 для event-sink сервера
# client_context -> CERT_NONE: принимаем любой серверный сертификат
# server_context -> CERT_NONE: sdcX с ENABLEMTLS=false не проверяет наш сертификат
certs_dir = pathlib.Path(__file__).parent.parent / 'tests' / 'certificates'
ssl_container = certloader.mk_ssl_contexts(
    key_file=certs_dir / 'test_private_key.pem',
    cert_file=certs_dir / 'test_certificate.pem',
    ca_file=None,
    ssl_passwd='password',  # test_private_key.pem is encrypted
)
ssl_container.client_context.check_hostname = False
ssl_container.client_context.verify_mode = ssl.CERT_NONE
ssl_container.server_context.verify_mode = ssl.CERT_NONE
# Discovery
wsd = WSDiscovery(ADAPTER_IP)
wsd.start()
print('Searching...', flush=True)
services = wsd.search_services(timeout=5)
print(f'Found {len(services)} service(s)', flush=True)
for s in services:
    print(f'  {s.epr}  {s.x_addrs}', flush=True)
if not services:
    wsd.stop()
    exit()
# Consumer: ssl_container != None -> is_ssl_connection=None -> tries TLS first
consumer = SdcConsumer.from_wsd_service(services[0], ssl_context_container=ssl_container)
print('Starting...', flush=True)
try:
    consumer.start_all(not_subscribed_actions=periodic_actions)
except Exception as e:
    print(f'start_all FAILED: {type(e).__name__}: {e}', flush=True)
print(f'connected: {consumer.is_connected}', flush=True)
if consumer.is_connected:
    mdib = ConsumerMdib(consumer)
    mdib.init_mdib()
    print(f'descriptors: {len(list(mdib.descriptions.objects))}', flush=True)
    print(f'states:      {len(list(mdib.states.objects))}', flush=True)
    time.sleep(10)
consumer.stop_all()
wsd.stop()
print('Done', flush=True)
