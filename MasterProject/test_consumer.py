import asyncio
import socket
import time

from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib

def get_local_ip():
    """Helper to get the local IP address for discovery."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't have to be reachable
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

if __name__ == '__main__':
    local_ip = get_local_ip()
    print(f"Starting discovery on IP: {local_ip}...")

    # Initialize WS-Discovery with the local IP address
    discovery = WSDiscovery(local_ip)
    discovery.start()
    flag = False
    while not flag:
        services = discovery.search_services()
        if services:
            flag = True
        print("aboba")
        time.sleep(1)
    print("Found Provider, connecting...")
    consumer = SdcConsumer.from_wsd_service(wsd_service=services[0], ssl_context_container=None)
    consumer.start_all()

    while True:

        if not services:
            print("No providers found.")
            continue

        consumer = SdcConsumer.from_wsd_service(wsd_service=services[0], ssl_context_container=None)
        consumer.start_all()

        mdib = ConsumerMdib(consumer)
        mdib.init_mdib()

        print(consumer.mdib.entities.by_handle("numeric.ch0.vmd1").state.MetricValue.Value)

        time.sleep(1)
