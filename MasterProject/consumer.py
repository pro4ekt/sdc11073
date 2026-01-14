from __future__ import annotations

import asyncio
import socket
import threading
import time
# import winsound
from copy import deepcopy
from decimal import Decimal

from sdc11073 import observableproperties
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.wsdiscovery import WSDiscovery
# from sdc11073.xml_types.pm_types import AlertSignalPresence, MeasurementValidity
# from myDbClass.dbworker import DBWorker


# A simple thread-safe class to share the consumer object
class SharedState:
    def __init__(self):
        self.consumer: SdcConsumer | None = None


def get_local_ip():
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


class SdcMyConsumer:
    def __init__(self):
        self.running = True
        self.shared_state = SharedState()

        """
        # Events to control alarm sound threads
        self.temp_alarm_active = threading.Event()
        self.hum_alarm_active = threading.Event()
        self.local_silence_active = threading.Event()

        # Lock for handling alert updates sequentially
        self.alert_lock = threading.Lock()

        self.db_worker = None
        """

        # Start the SDC logic in a separate thread
        self.sdc_thread = threading.Thread(target=self._run_sdc_logic, daemon=True)

        """
        # Start a single alarm sound thread
        self.sound_thread = threading.Thread(target=self._alarm_sound_loop, daemon=True)
        """

    def start(self):
        self.sdc_thread.start()
        # self.sound_thread.start()
        print("SDC Consumer logic started in background.")

    def stop(self):
        self.running = False
        # self.temp_alarm_active.set()
        # self.hum_alarm_active.set()
        print("Stopping application...")

    """
    def _alarm_sound_loop(self):
        # Plays a beep sound in a loop if any alarm event is set.
        while self.running:
            # This loop will only run when at least one alarm event is set
            if self.temp_alarm_active.is_set() or self.hum_alarm_active.is_set():
                # If local silence is active, don't play sound.
                if self.local_silence_active.is_set():
                    time.sleep(0.5)  # Check again in a bit
                    continue

                # Prioritize temperature alarm sound
                frequency = 420 if self.temp_alarm_active.is_set() else 640

                try:
                    winsound.Beep(frequency, 500)  # Play a short beep
                    time.sleep(0.8)  # Wait for 0.8 seconds before the next beep
                except Exception as e:
                    print(f"Could not play sound: {e}")
                    # Avoid busy-looping on error
                    time.sleep(1)
            else:
                # If no events are set, sleep a bit to avoid a busy-wait loop
                time.sleep(0.1)
    """

    def _run_sdc_logic(self):
        asyncio.run(self.sdc_main_loop())

    def log(self, message: str):
        print(f"[LOG] {message}")

    """
    def on_metric_update(self, metrics_by_handle: dict):
        for handle, state in metrics_by_handle.items():
            if (state.MetricValue.MetricQuality.Validity == MeasurementValidity.CALIBRATION_ONGOING):
                self.log(f"{handle}: Threshold calibration ongoing...validate or reject.")

            value = state.MetricValue
            if value and value.Value is not None:
                unit = state.descriptor_container.Unit.Code
                self.log(f"{handle}: {value.Value:.2f} {unit}")

            # Also print current thresholds if they update
            if handle in ("temperature", "humidity"):
                self._print_current_thresholds(handle, state)

    def _print_current_thresholds(self, metric_handle: str, state) -> None:
        try:
            ranges = getattr(state, "PhysiologicalRange", None)
            if ranges and len(ranges) > 0:
                pr = ranges[0]
                lower = pr.Lower if getattr(pr, "Lower", None) is not None else "N/A"
                upper = pr.Upper if getattr(pr, "Upper", None) is not None else "N/A"
                print(f"[{metric_handle.upper()} THRESHOLDS] Lower: {lower}, Upper: {upper}")
        except Exception:
            pass

    def on_alert_update(self, alerts_by_handle: dict):
        # Handle incoming alert state changes and play sounds.
        with self.alert_lock:
            for handle, state in alerts_by_handle.items():
                if handle not in ("al_signal_temperature", "al_signal_humidity"):
                    continue

                is_on = state.Presence == AlertSignalPresence.ON
                target_event = self.temp_alarm_active if 'temperature' in handle else self.hum_alarm_active

                if is_on and not target_event.is_set():
                    self.log(f"ALARM ON: {handle}")
                    target_event.set()  # Start the sound loop
                elif not is_on and target_event.is_set():
                    self.log(f"ALARM OFF: {handle}")
                    target_event.clear()  # Stop the sound loop

            # If all alarms are off, reset local silence
            if not self.temp_alarm_active.is_set() and not self.hum_alarm_active.is_set():
                if self.local_silence_active.is_set():
                    self.log("All alarms off. Resetting local silence.")
                    self.local_silence_active.clear()

    def alarm_control(self, alert_handle: str, operation_handle: str):
        consumer = self.shared_state.consumer
        if not consumer or not consumer.mdib:
            self.log("Cannot control alarm: consumer not ready.")
            return
        try:
            alert_state = consumer.mdib.entities.by_handle(alert_handle).state
            if alert_state.Presence == AlertSignalPresence.ON:
                self.log(f"Alarm '{alert_handle}' is ON, silencing...")
                proposed_state = deepcopy(alert_state)
                proposed_state.Presence = AlertSignalPresence.OFF
                consumer.set_service_client.set_alert_state(
                    operation_handle=operation_handle,
                    proposed_alert_state=proposed_state
                )
                if self.db_worker:
                    self.db_worker.operation_register(
                        provider_id=int(consumer.mdib.entities.by_handle("device_id").state.MetricValue.Value),
                        operation_handle=operation_handle,
                        performed_by="SDC Consumer")
            else:
                self.log(f"No active alarm for '{alert_handle}'.")
        except Exception as e:
            self.log(f"ERROR: alarm_control for '{alert_handle}': {e}")

    def silence_local_alarm(self):
        # Toggles local silence for alarms.
        if self.local_silence_active.is_set():
            self.local_silence_active.clear()
            self.log("Local alarm sound re-enabled.")
        else:
            self.local_silence_active.set()
            self.log("Local alarm sound silenced.")

    def threshold_control(self, metric_handle: str, operation_handle: str, value: Decimal | None = None, is_lower: bool = True, validity: MeasurementValidity | None = None):
        consumer = self.shared_state.consumer
        if not consumer or not consumer.mdib:
            self.log("Cannot control threshold: consumer not ready.")
            return
        try:
            metric_state = consumer.mdib.entities.by_handle(metric_handle).state
            proposed_metric_state = deepcopy(metric_state)

            if validity is not None:
                proposed_metric_state.MetricValue.MetricQuality.Validity = validity
                self.log(f"Setting validity for '{metric_handle}' to {validity.value}...")
            elif value is not None:
                # Ensure PhysiologicalRange exists
                if not proposed_metric_state.PhysiologicalRange:
                    proposed_metric_state.mk_proposed_value().PhysiologicalRange.append()

                if is_lower:
                    proposed_metric_state.PhysiologicalRange[0].Lower = value
                    self.log(f"Setting {metric_handle} lower threshold to {value}...")
                else:
                    proposed_metric_state.PhysiologicalRange[0].Upper = value
                    self.log(f"Setting {metric_handle} upper threshold to {value}...")
            else:
                return

            consumer.set_service_client.set_metric_state(
                operation_handle=operation_handle,
                proposed_metric_states=[proposed_metric_state]
            )
            if self.db_worker:
                 self.db_worker.operation_register(provider_id=int(consumer.mdib.entities.by_handle("device_id").state.MetricValue.Value),
                                  operation_handle=operation_handle,
                                  performed_by="SDC Consumer")
        except Exception as e:
            self.log(f"ERROR: threshold_control for '{metric_handle}': {e}")
    """

    async def sdc_main_loop(self):
        local_ip = get_local_ip()
        self.log(f"Starting consumer on IP: {local_ip}")

        while self.running:
            self.shared_state.consumer = None
            discovery = WSDiscovery(local_ip)
            discovery.start()

            services = []
            while not services and self.running:
                self.log("Searching for services...")
                try:
                    services = await asyncio.to_thread(discovery.search_services, timeout=2)
                    if services:
                        self.log(f"Found {len(services)} services, connecting...")
                        break
                    await asyncio.sleep(2)
                except Exception as e:
                    self.log(f"Error during discovery: {e}")
                    await asyncio.sleep(5)

            discovery.stop()
            if not self.running: break

            if not services: continue

            # For now, just connect to the first service found
            consumer = SdcConsumer.from_wsd_service(wsd_service=services[0], ssl_context_container=None)
            try:
                consumer.start_all()
                mdib = ConsumerMdib(consumer)
                mdib.init_mdib()

                """
                self.db_worker = DBWorker(host='localhost', user='testuser2', password='1234', database='demo_db', mdib=mdib)
                self.db_worker.register(device_name='Laptop Consumer', device_type='consumer', device_location='Laboratory')
                """

                self.shared_state.consumer = consumer
                # observableproperties.bind(mdib, metrics_by_handle=self.on_metric_update)
                # observableproperties.bind(mdib, alert_by_handle=self.on_alert_update)
                self.log("Connection established. Monitoring...")

                while self.running:
                    await asyncio.sleep(2)
                    if not consumer.is_connected:
                        self.log("Connection lost, restarting discovery...")
                        consumer.stop_all()
                        self.shared_state.consumer = None
                        break
            except Exception as e:
                 self.log(f"Connection error: {e}")
                 # Cleanup if needed
                 try: consumer.stop_all()
                 except: pass


if __name__ == '__main__':
    app = SdcMyConsumer()
    app.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        app.stop()
