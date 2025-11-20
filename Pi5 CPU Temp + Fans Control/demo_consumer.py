from __future__ import annotations

import asyncio
import socket
import threading
import tkinter as tk
import time
import winsound
from collections import deque
from copy import deepcopy
from decimal import Decimal
from queue import Queue, Empty
from tkinter import ttk, scrolledtext

from sdc11073 import observableproperties
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types.pm_types import AlertSignalPresence, MeasurementValidity
from myDbClass.dbworker import DBWorker


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


class SdcConsumerApp:
    def __init__(self, root_tk: tk.Tk):
        self.root = root_tk
        self.root.title("SDC Consumer")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.gui_queue = Queue()
        self.shared_state = SharedState()
        self.running = True

        # Events to control alarm sound threads
        self.temp_alarm_active = threading.Event()
        self.hum_alarm_active = threading.Event()
        self.local_silence_active = threading.Event()

        # Lock for handling alert updates sequentially
        self.alert_lock = threading.Lock()

        # Keep a buffer of recent metric messages
        self.metric_log = deque(maxlen=100)

        self.silence_button = None  # To hold the silence button widget

        self._init_ui()

        # Start the SDC logic in a separate thread
        self.sdc_thread = threading.Thread(target=self._run_sdc_logic, daemon=True)
        self.sdc_thread.start()
        self.sdc_consumer_id = None
        self.db_worker = None

        # Start a single alarm sound thread
        self.sound_thread = threading.Thread(target=self._alarm_sound_loop, daemon=True)
        self.sound_thread.start()

        # Start the periodic GUI updater
        self.process_queue()

    def _init_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # --- Metrics Display Frame ---
        metrics_frame = ttk.LabelFrame(main_frame, text="Metric Updates", padding="10")
        metrics_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(0, weight=1)

        self.metrics_text = scrolledtext.ScrolledText(metrics_frame, wrap=tk.WORD, state=tk.DISABLED, height=20, width=50)
        self.metrics_text.grid(row=0, column=0, sticky="nsew")
        metrics_frame.grid_rowconfigure(0, weight=1)
        metrics_frame.grid_columnconfigure(0, weight=1)

        # --- Controls Frame ---
        controls_frame = ttk.LabelFrame(main_frame, text="Controls", padding="10")
        controls_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        main_frame.grid_columnconfigure(1, weight=1)

        self._create_control_widgets(controls_frame)

    def _create_control_widgets(self, parent):
        # Helper to create a button and bind its action
        def add_button(text, action, row, col, colspan=1):
            button = ttk.Button(parent, text=text, command=action)
            button.grid(row=row, column=col, columnspan=colspan, sticky="ew", pady=2, padx=2)
            return button

        # --- Alarm Controls ---
        ttk.Label(parent, text="Alarm Silence:").grid(row=0, column=0, columnspan=2, sticky="w")
        add_button("Temperature", lambda: self.alarm_control("al_signal_temperature", "temperature_alert_control"), 1, 0)
        add_button("Humidity", lambda: self.alarm_control("al_signal_humidity", "humidity_alert_control"), 1, 1)
        self.silence_button = add_button("Silence Local Alarm", self.silence_local_alarm, 2, 0, colspan=2)

        # --- Threshold Controls ---
        ttk.Separator(parent, orient='horizontal').grid(row=3, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(parent, text="Set Thresholds:").grid(row=4, column=0, columnspan=2, sticky="w")

        self.temp_thresh_var = tk.StringVar()
        self.hum_thresh_var = tk.StringVar()

        # New StringVars for individual current threshold display
        self.temp_lower_var = tk.StringVar(value="Current Lower: N/A")
        self.temp_upper_var = tk.StringVar(value="Current Upper: N/A")
        self.hum_lower_var = tk.StringVar(value="Current Hum Lower: N/A")
        self.hum_upper_var = tk.StringVar(value="Current Hum Upper: N/A")

        # Temperature controls
        ttk.Label(parent, text="Temperature:").grid(row=5, column=0, columnspan=2, sticky="w", pady=(5,0))
        temp_entry = ttk.Entry(parent, textvariable=self.temp_thresh_var)
        temp_entry.grid(row=6, column=0, columnspan=2, sticky="ew", pady=2)

        # Current threshold labels for temperature (in a row)
        ttk.Label(parent, textvariable=self.temp_lower_var).grid(row=7, column=0, sticky="w", padx=(0, 5))
        ttk.Label(parent, textvariable=self.temp_upper_var).grid(row=7, column=1, sticky="w", pady=(0, 4), padx=(5, 0))

        add_button("Set Temp Lower", lambda: self.threshold_control_from_ui("temperature", "temperature_threshold_control", self.temp_thresh_var, is_lower=True), 8, 0)
        add_button("Set Temp Upper", lambda: self.threshold_control_from_ui("temperature", "temperature_threshold_control", self.temp_thresh_var, is_lower=False), 8, 1)

        # Humidity controls
        ttk.Label(parent, text="Humidity:").grid(row=9, column=0, columnspan=2, sticky="w", pady=(5,0))
        hum_entry = ttk.Entry(parent, textvariable=self.hum_thresh_var)
        hum_entry.grid(row=10, column=0, columnspan=2, sticky="ew", pady=2)

        # Current threshold labels for humidity (in a row)
        ttk.Label(parent, textvariable=self.hum_lower_var).grid(row=11, column=0, sticky="w", padx=(0, 5))
        ttk.Label(parent, textvariable=self.hum_upper_var).grid(row=11, column=1, sticky="w", pady=(0, 4), padx=(5, 0))

        add_button("Set Hum Lower", lambda: self.threshold_control_from_ui("humidity", "humidity_threshold_control", self.hum_thresh_var, is_lower=True), 12, 0)
        add_button("Set Hum Upper", lambda: self.threshold_control_from_ui("humidity", "humidity_threshold_control", self.hum_thresh_var, is_lower=False), 12, 1)

        # --- Validation Controls ---
        ttk.Separator(parent, orient='horizontal').grid(row=13, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(parent, text="Validate Change:").grid(row=14, column=0, columnspan=2, sticky="w")
        add_button("Validate Temperature", lambda: self.threshold_control("temperature", "temperature_threshold_control", validity=MeasurementValidity.VALIDATED_DATA), 15, 0)
        add_button("Reject Temperature", lambda: self.threshold_control("temperature", "temperature_threshold_control", validity=MeasurementValidity.QUESTIONABLE), 15, 1)

    def _alarm_sound_loop(self):
        """Plays a beep sound in a loop if any alarm event is set."""
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

    def _update_current_threshold_labels(self, metric_handle: str, state) -> None:
        """Update the matching current-threshold StringVars from a metric state object."""
        lower, upper = "N/A", "N/A"
        try:
            # PhysiologicalRange may be absent; handle gracefully
            ranges = getattr(state, "PhysiologicalRange", None)
            if ranges and len(ranges) > 0:
                pr = ranges[0]
                lower = pr.Lower if getattr(pr, "Lower", None) is not None else "N/A"
                upper = pr.Upper if getattr(pr, "Upper", None) is not None else "N/A"
        except Exception:
            pass  # Keep default "N/A" on any error

        if metric_handle == "temperature":
            self.temp_lower_var.set(f"Current Lower: {lower}")
            self.temp_upper_var.set(f"Current Upper: {upper}")
        elif metric_handle == "humidity":
            self.hum_lower_var.set(f"Current Hum Lower: {lower}")
            self.hum_upper_var.set(f"Current Hum Upper: {upper}")

    def _run_sdc_logic(self):
        asyncio.run(self.sdc_main_loop())

    def log_to_gui(self, message: str):
        """Thread-safe method to queue a message for the GUI."""
        self.gui_queue.put(message)

    def process_queue(self):
        """Periodically called to update the GUI from the queue."""
        log_updated = False
        try:
            while True:
                message = self.gui_queue.get_nowait()

                if isinstance(message, tuple) and message[0] == "update_button":
                    # Handle button update command
                    if self.silence_button:
                        self.silence_button.config(text=message[1])
                elif isinstance(message, str):
                    # Handle log message
                    self.metric_log.append(message)
                    log_updated = True
                # else: ignore unknown message types

        except Empty:
            pass  # No new messages

        # Only update the text widget if the log has changed
        if log_updated:
            self.metrics_text.configure(state=tk.NORMAL)
            self.metrics_text.delete('1.0', tk.END)
            self.metrics_text.insert(tk.END, "\n".join(self.metric_log))
            self.metrics_text.see(tk.END)  # Auto-scroll
            self.metrics_text.configure(state=tk.DISABLED)

        if self.running:
            self.root.after(100, self.process_queue)

    def on_metric_update(self, metrics_by_handle: dict):
        for handle, state in metrics_by_handle.items():
            if (state.MetricValue.MetricQuality.Validity == MeasurementValidity.CALIBRATION_ONGOING):
                log_msg = f"{handle}: Threshold calibration ongoing...validate or reject."
                self.log_to_gui(log_msg)
            value = state.MetricValue
            if value and value.Value is not None:
                unit = state.descriptor_container.Unit.Code
                log_msg = f"{handle}: {value.Value:.2f} {unit}"
                self.log_to_gui(log_msg)

            # Update displayed current thresholds when metric state arrives
            if handle in ("temperature", "humidity"):
                self._update_current_threshold_labels(handle, state)

    def on_alert_update(self, alerts_by_handle: dict):
        """Handle incoming alert state changes and play sounds."""
        with self.alert_lock:
            for handle, state in alerts_by_handle.items():
                if handle not in ("al_signal_temperature", "al_signal_humidity"):
                    continue

                is_on = state.Presence == AlertSignalPresence.ON
                target_event = self.temp_alarm_active if 'temperature' in handle else self.hum_alarm_active

                if is_on and not target_event.is_set():
                    self.log_to_gui(f"ALARM ON: {handle}")
                    target_event.set()  # Start the sound loop
                elif not is_on and target_event.is_set():
                    self.log_to_gui(f"ALARM OFF: {handle}")
                    target_event.clear()  # Stop the sound loop

            # If all alarms are off, reset local silence
            if not self.temp_alarm_active.is_set() and not self.hum_alarm_active.is_set():
                if self.local_silence_active.is_set():
                    self.log_to_gui("All alarms off. Resetting local silence.")
                    self.local_silence_active.clear()
                    if self.silence_button:
                        # This needs to be thread-safe
                        self.gui_queue.put(("update_button", "Silence Local Alarm"))

    def alarm_control(self, alert_handle: str, operation_handle: str):
        consumer = self.shared_state.consumer
        if not consumer or not consumer.mdib:
            self.log_to_gui("Cannot control alarm: consumer not ready.")
            return
        try:
            alert_state = consumer.mdib.entities.by_handle(alert_handle).state
            if alert_state.Presence == AlertSignalPresence.ON:
                self.log_to_gui(f"Alarm '{alert_handle}' is ON, silencing...")
                proposed_state = deepcopy(alert_state)
                proposed_state.Presence = AlertSignalPresence.OFF
                consumer.set_service_client.set_alert_state(
                    operation_handle=operation_handle,
                    proposed_alert_state=proposed_state
                )
                self.db_worker.operation_register(
                    provider_id=int(consumer.mdib.entities.by_handle("device_id").state.MetricValue.Value),
                    operation_handle=operation_handle,
                    performed_by="SDC Consumer")
            else:
                self.log_to_gui(f"No active alarm for '{alert_handle}'.")
        except Exception as e:
            self.log_to_gui(f"ERROR: alarm_control for '{alert_handle}': {e}")

    def silence_local_alarm(self):
        """Toggles local silence for alarms."""
        if self.local_silence_active.is_set():
            self.local_silence_active.clear()
            self.log_to_gui("Local alarm sound re-enabled.")
            if self.silence_button:
                self.silence_button.config(text="Silence Local Alarm")
        else:
            self.local_silence_active.set()
            self.log_to_gui("Local alarm sound silenced.")
            if self.silence_button:
                self.silence_button.config(text="Un-silence Local Alarm")

    def threshold_control_from_ui(self, metric_handle: str, op_handle: str, tk_var: tk.StringVar, is_lower: bool):
        try:
            value = Decimal(tk_var.get())
            self.threshold_control(metric_handle, op_handle, value=value, is_lower=is_lower)
        except Exception:
            self.log_to_gui(f"Invalid input for {metric_handle} threshold.")

    def threshold_control(self, metric_handle: str, operation_handle: str, value: Decimal | None = None, is_lower: bool = True, validity: MeasurementValidity | None = None):
        consumer = self.shared_state.consumer
        if not consumer or not consumer.mdib:
            self.log_to_gui("Cannot control threshold: consumer not ready.")
            return
        try:
            metric_state = consumer.mdib.entities.by_handle(metric_handle).state
            proposed_metric_state = deepcopy(metric_state)

            if validity is not None:
                proposed_metric_state.MetricValue.MetricQuality.Validity = validity
                self.log_to_gui(f"Setting validity for '{metric_handle}' to {validity.value}...")
            elif value is not None:
                # Ensure PhysiologicalRange exists, which is robust practice.
                if not proposed_metric_state.PhysiologicalRange:
                    # If it's missing, create it. The sdc11073 library will handle the type.
                    proposed_metric_state.mk_proposed_value().PhysiologicalRange.append()

                if is_lower:
                    proposed_metric_state.PhysiologicalRange[0].Lower = value
                    self.log_to_gui(f"Setting {metric_handle} lower threshold to {value}...")
                else:
                    proposed_metric_state.PhysiologicalRange[0].Upper = value
                    self.log_to_gui(f"Setting {metric_handle} upper threshold to {value}...")
            else:
                return # Nothing to do

            consumer.set_service_client.set_metric_state(
                operation_handle=operation_handle,
                proposed_metric_states=[proposed_metric_state]
            )
            self.db_worker.operation_register(provider_id=int(consumer.mdib.entities.by_handle("device_id").state.MetricValue.Value),
                                  operation_handle=operation_handle,
                                  performed_by="SDC Consumer")
        except Exception as e:
            self.log_to_gui(f"ERROR: threshold_control for '{metric_handle}': {e}")

    async def sdc_main_loop(self):
        local_ip = get_local_ip()
        self.log_to_gui(f"Starting consumer on IP: {local_ip}")

        while self.running:
            self.shared_state.consumer = None
            discovery = WSDiscovery(local_ip)
            discovery.start()

            services = []
            while not services and self.running:
                self.log_to_gui("Searching for services...")
                try:
                    services = await asyncio.to_thread(discovery.search_services, timeout=2)
                    if services:
                        self.log_to_gui(f"Found {len(services)} services, connecting...")
                        break
                    await asyncio.sleep(2)
                except Exception as e:
                    self.log_to_gui(f"Error during discovery: {e}")
                    await asyncio.sleep(5)

            discovery.stop()
            if not self.running: break

            if not services: continue

            consumer = SdcConsumer.from_wsd_service(wsd_service=services[0], ssl_context_container=None)
            consumer.start_all()

            mdib = ConsumerMdib(consumer)
            mdib.init_mdib()

            self.db_worker = db = DBWorker(host='localhost', user='testuser2', password='1234', database='demo_db', mdib=mdib)
            self.db_worker.register(device_name='Laptop Consumer', device_type='consumer', device_location='Laboratory')
            self.sdc_consumer_id = db.device_id

            self.shared_state.consumer = consumer
            observableproperties.bind(mdib, metrics_by_handle=self.on_metric_update)
            observableproperties.bind(mdib, alert_by_handle=self.on_alert_update)
            self.log_to_gui("Connection established. Monitoring...")

            while self.running:
                await asyncio.sleep(2)
                if not consumer.is_connected:
                    self.log_to_gui("Connection lost, restarting discovery...")
                    consumer.stop_all()
                    self.shared_state.consumer = None
                    break

    def on_closing(self):
        self.running = False
        # Signal alarm thread to stop waiting
        self.temp_alarm_active.set() # Set to allow the loop to exit if waiting
        self.hum_alarm_active.set()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    app = SdcConsumerApp(root)
    root.mainloop()
