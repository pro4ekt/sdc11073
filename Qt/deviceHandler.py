import threading
import asyncio
from decimal import Decimal
from qtDeviceHandler import QtDeviceHandler
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib import ConsumerMdib
from sdc11073.xml_types.actions import periodic_actions
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073.xml_types.pm_types import Measurement, CodedValue
from sdc11073 import observableproperties
from PySide6.QtGui import QGuiApplication
from sdc11073.xml_types.pm_types import Measurement, RelatedMeasurement

@classmethod
def _related_measurement_from_node(cls, node):
    # Передаем заглушку Measurement, чтобы обойти ошибку __init__ "missing 1 required argument 'value'"
    obj = cls(Measurement(None, None))
    obj.update_from_node(node)
    return obj

RelatedMeasurement.from_node = _related_measurement_from_node

class DeviceHandler(threading.Thread):
    """
    Worker class (The "Worker").
    Responsible for maintaining a connection to a SINGLE specific device (Provider).
    Runs in its own system thread with its own independent asyncio event loop.
    """

    # Removed QObject inheritance and Signal definition

    def __init__(self, wsd_service, manager):
        # Initialize only Thread
        threading.Thread.__init__(self, daemon=True)

        self.wsd_service = wsd_service
        self.epr = str(wsd_service.epr)  # Explicitly convert to string to ensure consistent key usage
        self.manager = manager
        self.patient_context = self.manager.get_patient_context_data()  # Данные пациента для SDC-контекста
        self.running = True
        self.consumer = None
        self.mdib = None
        self.qtDeviceHandler = None
        self.error_occurred = False  # Track if the session ended with an error
        self.data_lock = threading.Lock()  # Lock for MDIB access
        self.opcua_server = None  # Placeholder for OPC UA Server instance if needed

    def run(self):
        # 1. Isolation: Create a new asyncio event loop for this thread.
        # This ensures network delays on this device don't affect others.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._worker_logic())
        finally:
            try:
                loop.close()
            except Exception:
                pass
            # 2. Self-cleanup: When the thread dies, remove self from Manager's registry.
            # We pass 'error_occurred' so the manager knows if it should invalidate the cache.
            self.manager.remove_device(self.epr, self.error_occurred)
            print(f"[Worker {self.epr}] Thread Exiting (Dead).")

    async def _worker_logic(self):
        print(f"[Worker {self.epr}] Connecting...")
        try:
            # 3. Connection: Create SDC Consumer for this specific service
            self.consumer = SdcConsumer.from_wsd_service(wsd_service=self.wsd_service, ssl_context_container=None)
            self.consumer.start_all(not_subscribed_actions=periodic_actions)

            with self.data_lock:
                self.mdib = ConsumerMdib(self.consumer)
                self.mdib.init_mdib()

            # Записываем данные пациента из FHIR в PatientContext провайдера
            self.apply_patient_to_mdib()

            # Регистрируем устройство в OPC UA Gateway только ПОСЛЕ инициализации MDIB
            # ВАЖНО: Делаем вызов потокобезопасным, перекидывая задачу в event loop Менеджера!
            # if self.manager.opcua_gateway is not None and hasattr(self.manager, 'manager_loop'):
            #     print(f"[Worker {self.epr}] Registering in Async Central OPC UA Server...")
            #     future = asyncio.run_coroutine_threadsafe(
            #         self.manager.opcua_gateway.add_device(self.mdib, self.epr),
            #         self.manager.manager_loop
            #     )
            #     future.result()  # Ожидаем завершения добавления нод

            # 4. Subscription (Bindings for real-time updates)
            observableproperties.bind(self.mdib, metrics_by_handle=self.on_metric_update)
            observableproperties.bind(self.mdib, alert_by_handle=self.on_alert_update)

            print(f"[Worker {self.epr}] Connection established. Monitoring...")

            # 1. Создаем Qt-обертку. Сейчас она "принадлежит" этому рабочему потоку.
            self.qtDeviceHandler = QtDeviceHandler(self)

            # 2. ВАЖНО: Перемещаем объект в главный поток UI.
            # Без этого QML может ругаться при попытке доступа к свойствам/слотам.
            main_thread = QGuiApplication.instance().thread()
            if main_thread:
                self.qtDeviceHandler.moveToThread(main_thread)
                # No connect needed here anymore, the QtDeviceHandler connects its own signal in __init__
            else:
                print(f"[Worker {self.epr}] Warning: Could not find Main Thread!")

            # 3. Уведомляем UI (сигнал уйдет в главный поток через очередь событий)
            # Мы вызываем emit у менеджера, который сам потокобезопасен (Qt Signals thread-safe)
            self.manager.deviceConnected.emit(self.qtDeviceHandler)

            # 5. Lifecycle Loop: Keep running as long as connected
            while self.running:
                if not self.consumer.is_connected:
                    print(f"[Worker {self.epr}] Connection lost reported by SDC stack.")
                    self.error_occurred = True
                    break

                # Trigger update on UI thread safely via method call
                if self.qtDeviceHandler:
                    self.qtDeviceHandler.scheduleUpdate()

                # АКТИВНАЯ ПРОВЕРКА (После бага с Vector Provider)
                try:
                    # Пытаемся сделать легкий запрос с коротким тайм-аутом
                    if self.consumer and self.consumer.is_connected:
                        # ИСПРАВЛЕНИЕ: Обращаемся к context_service_client напрямую (это свойство, а не функция)
                        if self.consumer.context_service_client:
                            self.consumer.context_service_client.get_context_states()
                        else:
                            # Если ContextService нет (редко, но бывает), можно дернуть GetService
                            # self.consumer.get_service_client.get_md_state()
                            pass
                except Exception as e:
                    print(f"[Worker {self.epr}] Ping failed: {e}")
                    self.error_occurred = True
                    break

                # CHANGED: Reverted to 1.0 second standard update rate (cancels smooth scrolling idea)
                await asyncio.sleep(1.0)

        except Exception as e:
            print(f"[Worker {self.epr}] Critical Error: {e}")
            self.error_occurred = True
        finally:
            if self.consumer:
                print(f"[Worker {self.epr}] Stopping consumer resources...")
                try:
                    self.consumer.stop_all()
                except:
                    pass

    def apply_patient_to_mdib(self):
        """
        Записывает данные пациента из FHIR в PatientContextState провайдера
        через вызов SetContextState по сети.
        """
        if not self.patient_context:
            print(f"[Worker {self.epr}] No patient context data, skipping.")
            return

        try:
            # 1. Находим PatientContextDescriptor в MDIB провайдера
            pat_descriptors = self.mdib.descriptions.NODETYPE.get(pm.PatientContextDescriptor, [])
            if not pat_descriptors:
                print(f"[Worker {self.epr}] No PatientContextDescriptor found in provider MDIB.")
                return

            descriptor = pat_descriptors[0]

            # 2. Ищем SetContextStateOperationDescriptor в MDIB провайдера
            set_ctx_ops = self.mdib.descriptions.NODETYPE.get(pm.SetContextStateOperationDescriptor, [])
            if not set_ctx_ops:
                print(f"[Worker {self.epr}] No SetContextStateOperationDescriptor found in provider MDIB.")
                return
            operation_handle = set_ctx_ops[0].Handle

            # 3. Берём существующее состояние или создаём новое
            existing_states = self.mdib.context_states.NODETYPE.get(pm.PatientContextState, [])
            if existing_states:
                # Если в MDIB уже есть пациент (например, "пустой", прописанный в XML) - мы просто обновим его
                proposed_state = existing_states[0].mk_copy()
            else:
                # Иначе предлагаем новый
                proposed_state = self.consumer.context_service_client.mk_proposed_context_object(descriptor.Handle)
                
            proposed_state.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED

            # 4. Заполняем CoreData данными из FHIR
            ctx = self.patient_context
            proposed_state.CoreData.Givenname = ctx.get('given_name') or None
            proposed_state.CoreData.Familyname = ctx.get('family_name') or None

            weight_num = ctx.get('weight_value')
            if weight_num is not None:
                # Use str() correctly depending on if it's a number/float
                weight_val_str = str(weight_num).split()[0] if isinstance(weight_num, str) else str(weight_num)
                proposed_state.CoreData.Weight = Measurement(
                    Decimal(weight_val_str),
                    CodedValue(ctx.get('weight_unit') or 'kg')
                )
            else:
                proposed_state.CoreData.Weight = None

            height_num = ctx.get('height_value')
            if height_num is not None:
                height_val_str = str(height_num).split()[0] if isinstance(height_num, str) else str(height_num)
                proposed_state.CoreData.Height = Measurement(
                    Decimal(height_val_str),
                    CodedValue(ctx.get('height_unit') or 'cm')
                )
            else:
                proposed_state.CoreData.Height = None

            states_to_send = [proposed_state]

            # --- Обновляем существующий LocationContextState моковыми данными ---
            existing_loc_states = self.mdib.context_states.NODETYPE.get(pm.LocationContextState, [])
            if existing_loc_states:
                proposed_loc = existing_loc_states[0].mk_copy()
                proposed_loc.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED
                if proposed_loc.LocationDetail is None:
                    proposed_loc.LocationDetail = pm_types.LocationDetail()
                proposed_loc.LocationDetail.Room = "Room 101"
                proposed_loc.LocationDetail.Facility = "My Mock Facility"
                proposed_loc.LocationDetail.Bed = "Bed A"
                states_to_send.append(proposed_loc)

            # --- Обновляем WorkflowContextState ---
            wf_descriptors = self.mdib.descriptions.NODETYPE.get(pm.WorkflowContextDescriptor, [])
            if wf_descriptors:
                wf_descriptor = wf_descriptors[0]
                
                existing_wf_states = self.mdib.context_states.NODETYPE.get(pm.WorkflowContextState, [])
                if existing_wf_states:
                    proposed_wf_state = existing_wf_states[0].mk_copy()
                else:
                    proposed_wf_state = self.consumer.context_service_client.mk_proposed_context_object(wf_descriptor.Handle)
                
                proposed_wf_state.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED
                
                if not hasattr(proposed_wf_state, 'WorkflowDetail') or proposed_wf_state.WorkflowDetail is None:
                    proposed_wf_state.WorkflowDetail = pm_types.WorkflowDetail()

                # Привязка ID Пациента
                patient_id = ctx.get('patient_id')
                if patient_id:
                    patient_data = pm_types.PatientDemographicsCoreData()
                    identifier = pm_types.InstanceIdentifier(root="Hospital_FHIR", extension_string=str(patient_id))
                    patient_data.Identification.append(identifier)
                    proposed_wf_state.WorkflowDetail.Patient = patient_data

                # Внедрение кода заболевания
                conditions = ctx.get('conditions', [])
                if conditions:
                    if not proposed_wf_state.WorkflowDetail.DangerCode:
                        proposed_wf_state.WorkflowDetail.DangerCode = []
                    # Очищаем старые если были, чтобы не дублировать
                    proposed_wf_state.WorkflowDetail.DangerCode.clear()

                    for condition in conditions:
                        # Используем название заболевания напрямую как код
                        danger_code_obj = pm_types.CodedValue(str(condition))
                        proposed_wf_state.WorkflowDetail.DangerCode.append(danger_code_obj)

                states_to_send.append(proposed_wf_state)

            # 5. Отправляем SetContextState запрос провайдеру
            if self.consumer.context_service_client:
                # ВАЖНО: operation_handle не может быть None или пустым строкой согласно XSD.
                if not operation_handle:
                    print(f"[Worker {self.epr}] operation_handle is empty, cannot send SetContextState.")
                    return
                print(f"[Worker {self.epr}] Sending SetContextState with operation '{operation_handle}'")
                self.consumer.context_service_client.set_context_state(
                    operation_handle=operation_handle,
                    proposed_context_states=states_to_send
                )
                print(f"[Worker {self.epr}] PatientContext applied: "
                      f"{ctx.get('given_name')} {ctx.get('family_name')}")
            else:
                print(f"[Worker {self.epr}] No context_service_client available.")

        except Exception as e:
            print(f"[Worker {self.epr}] Failed to apply patient context: {e}")

    def on_metric_update(self, metrics_by_handle):
        """Callback invoked by SDC library when metrics change remotely."""
        return # OPC UA Updates disabled
        # if not self.manager.opcua_gateway or not hasattr(self.manager, 'manager_loop'):
        #     return
        # 
        # updates = {}
        # for handle, state in metrics_by_handle.items():
        #     if state.NODETYPE == pm.NumericMetricState:
        #         val = getattr(state.MetricValue, 'Value', None)
        #         if val is not None:
        #             try:
        #                 updates[handle] = float(val)
        #             except ValueError:
        #                 pass
        #     elif state.NODETYPE in [pm.StringMetricState, pm.EnumStringMetricState]:
        #         val = getattr(state.MetricValue, 'Value', None)
        #         if val is not None:
        #             updates[handle] = str(val)
        # 
        # if updates:
        #     # Передаем обновление в асинхронный цикл менеджера для безопасной записи в OPC
        #     asyncio.run_coroutine_threadsafe(
        #         self.manager.opcua_gateway.update_values(self.epr, updates),
        #         self.manager.manager_loop
        #     )

    def on_alert_update(self, alert_by_handle):
        """Callback invoked by SDC library when alerts change."""
        return # OPC UA Updates disabled
        # if not self.manager.opcua_gateway or not hasattr(self.manager, 'manager_loop'):
        #     return
        # 
        # updates = {}
        # for handle, state in alert_by_handle.items():
        #     if state.NODETYPE in [pm.AlertConditionState, pm.LimitAlertConditionState]:
        #         presence = getattr(state, 'Presence', False)
        #         updates[f"Condition_{handle}_Presence"] = presence
        #     elif state.NODETYPE == pm.AlertSignalState:
        #         signal_presence = str(getattr(state, 'Presence', 'Unknown'))
        #         updates[f"Signal_{handle}"] = signal_presence
        # 
        # if updates:
        #     # Передаем обновление в асинхронный цикл менеджера для безопасной записи в OPC
        #     asyncio.run_coroutine_threadsafe(
        #         self.manager.opcua_gateway.update_values(self.epr, updates),
        #         self.manager.manager_loop
        #     )

    def stop(self):
        self.running = False
