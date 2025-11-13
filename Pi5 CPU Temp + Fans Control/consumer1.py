from __future__ import annotations

import socket
import logging
import time
from decimal import Decimal
from copy import deepcopy
import asyncio
import threading
import keyboard

from sdc11073 import observableproperties
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types.pm_types import AlertSignalPresence


# A simple thread-safe class to share the consumer object
class SharedState:
    def __init__(self):
        self.consumer: SdcConsumer | None = None


def get_local_ip():
    # ... (your function is unchanged)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.146.164.72", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"  # Fallback for offline testing
    finally:
        s.close()


def on_metric_update(metrics_by_handle: dict):
    print("Metric update received (make later)")

def alarm_control(consumer, alert_handle: str, operation_handle: str):
    if not consumer or not consumer.mdib:
        print("Cannot control alarm: consumer not ready.")
        return
    try:
        alert_state_container = consumer.mdib.entities.by_handle(alert_handle).state
        if alert_state_container.Presence == AlertSignalPresence.ON:
            print(f"Alarm '{alert_handle}' is ON, turning OFF...")
            proposed_state = deepcopy(alert_state_container)
            proposed_state.Presence = AlertSignalPresence.OFF
            consumer.set_service_client.set_alert_state(
                operation_handle=operation_handle,
                proposed_alert_state=proposed_state
            )
        else:
            print(f"No active alarm for '{alert_handle}'.")
    except KeyError:
        print(f"Alert handle '{alert_handle}' not found in MDIB.")
    except Exception as e:
        print(f"alarm_control error for '{alert_handle}': {e}")

def button_pressed_worker(state: SharedState):
    def with_consumer(action_name, fn):
        c = state.consumer
        if not c:
            print(f"{action_name}: no active consumer connection.")
            return
        fn(c)

    # Map keys to actions
    def temp_action(c):
        alarm_control(c, "al_signal_temperature", "temperature_alert_control")

    def humidity_action(c):
        alarm_control(c, "al_signal_humidity", "humidity_alert_control")

    # Register hotkeys
    keyboard.add_hotkey("t", lambda: with_consumer("Temp", temp_action))
    keyboard.add_hotkey("h", lambda: with_consumer("Humidity", humidity_action))

    print("Keyboard: [t]=Temperature, [h]=Humidity, [esc]=exit")
    keyboard.wait("esc")
    print("Keyboard listener stopped.")


async def main():
    # logging.basicConfig(level=logging.INFO)
    local_ip = get_local_ip()
    print(f"Starting consumer on IP: {local_ip}")

    # Create one shared state object
    shared_state = SharedState()

    # Start the keyboard listener thread once
    keyboard_thread = threading.Thread(target=button_pressed_worker, args=(shared_state,), daemon=True)
    keyboard_thread.start()

    # Main loop for discovery and connection management
    while True:
        shared_state.consumer = None  # Ensure state is clean before discovery
        discovery = WSDiscovery(local_ip)
        discovery.start()

        service = []
        while not service:
            print("Searching for services...")
            try:
                # Use to_thread to avoid blocking the event loop
                service = await asyncio.to_thread(discovery.search_services, timeout=2)
                if service:
                    print(f"Found {len(service)} services, connecting to the first one...")
                    break
                await asyncio.sleep(2)  # Wait before next search
            except Exception as e:
                print(f"Error during discovery: {e}")
                await asyncio.sleep(5)  # Wait longer after an error

        discovery.stop()

        # Initialize consumer
        consumer = SdcConsumer.from_wsd_service(wsd_service=service[0], ssl_context_container=None)
        consumer.start_all()

        mdib = ConsumerMdib(consumer)
        mdib.init_mdib()

        # Safely publish the new consumer to the other thread
        shared_state.consumer = consumer

        observableproperties.bind(mdib, metrics_by_handle=on_metric_update)
        print("Connection established. Monitoring connection status...")

        # Loop to check connection status
        while True:
            await asyncio.sleep(2)
            is_connected = False
            if consumer.is_connected:
                # A more robust check is to see if subscriptions are active
                for sub in consumer.subscription_mgr.subscriptions.values():
                    if sub.is_subscribed:
                        is_connected = True
                        break

            if not is_connected:
                print("Connection lost, restarting discovery...")
                consumer.stop_all()
                shared_state.consumer = None  # Clear the shared consumer
                break  # Exit inner loop to restart discovery


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down.")
