from sdc11073.consumer import SdcConsumer
from sdc11073.xml_types import pm_qnames as pm

from asyncua.sync import Server
from asyncua.ua import Double

class SdcOpcGateway:
    def __init__(self, sdc_consumer):
        self.opcua_server = None
        self.sdc_consumer = sdc_consumer
        self.mdib = self.sdc_consumer.mdib

    def start(self):
        # Start an OPC UA Server for this device (for testing or integration purposes)
        vmd_descriptors = [d for d in self.mdib.descriptions.objects if d.NODETYPE == pm.VmdDescriptor]
        channel_descriptors = [d for d in self.mdib.descriptions.objects if d.NODETYPE == pm.ChannelDescriptor]
        metric_descriptors = [m for m in self.mdib.descriptions.objects if m.NODETYPE == pm.NumericMetricDescriptor]

        # Создаем словарь стейтов для быстрого доступа по Handle
        metrics_states = {s.DescriptorHandle: s for s in self.mdib.states.objects if
                          s.NODETYPE == pm.NumericMetricState}
        
        alert_signal_descriptors = [
            d for d in self.mdib.descriptions.objects
            if d.NODETYPE == pm.AlertSignalDescriptor
        ]
        alert_signal_states = {
            s.DescriptorHandle: s for s in self.mdib.states.objects
            if s.NODETYPE == pm.AlertSignalState
        }

        alert_cond_desc_types = [pm.AlertConditionDescriptor, pm.LimitAlertConditionDescriptor]
        condition_descriptors = [
            d for d in self.mdib.descriptions.objects
            if d.NODETYPE in alert_cond_desc_types
        ]
        condition_states = {
            s.DescriptorHandle: s for s in self.mdib.states.objects
            if s.NODETYPE in [pm.AlertConditionState, pm.LimitAlertConditionState]
        }

        self.opcua_server = Server()
        self.opcua_server.set_endpoint("opc.tcp://192.168.0.101:4840")

        mds_idx = self.opcua_server.register_namespace("Provider NodeSpace")
        opc_objects = self.opcua_server.nodes.objects

        opc_nodes = {}

        # 1. Создаем VMD объекты
        for vmd in vmd_descriptors:
            opc_nodes[vmd.Handle] = opc_objects.add_object(mds_idx, vmd.Handle)

        # 2. Создаем Channel объекты (привязываем к их родительским VMD)
        for channel in channel_descriptors:
            parent_node = opc_nodes.get(channel.parent_handle, opc_objects)
            opc_nodes[channel.Handle] = parent_node.add_object(mds_idx, channel.Handle)

        # 3. Создаем Metric переменные (привязываем к их родительским Channel)
        for metric in metric_descriptors:
            parent_node = opc_nodes.get(metric.parent_handle, opc_objects)

            state = metrics_states.get(metric.Handle)
            initial_value = 0.0
            if state and getattr(state, 'MetricValue', None) and getattr(state.MetricValue, 'Value', None) is not None:
                try:
                    initial_value = float(state.MetricValue.Value)
                except ValueError:
                    pass

            opc_metric = parent_node.add_variable(mds_idx, metric.Handle, initial_value, varianttype=Double)
            opc_metric.set_writable()
            opc_nodes[metric.Handle] = opc_metric

        # Вспомогательная функция для поиска родительского VMD
        def get_vmd_handle(desc_handle):
            desc = next((d for d in self.mdib.descriptions.objects if d.Handle == desc_handle), None)
            while desc and desc.parent_handle:
                desc = next((d for d in self.mdib.descriptions.objects if d.Handle == desc.parent_handle), None)
                if desc and desc.NODETYPE == pm.VmdDescriptor:
                    return desc.Handle
            return None

        # Функция для получения/создания папки Alarms внутри VMD
        vmd_alarms_folders = {}
        def get_alarms_folder(vmd_handle):
            if vmd_handle not in vmd_alarms_folders:
                parent_node = opc_nodes.get(vmd_handle, opc_objects)
                vmd_alarms_folders[vmd_handle] = parent_node.add_object(mds_idx, "Alarms")
            return vmd_alarms_folders[vmd_handle]

        # 4. Создаем узлы для Alarms в соответствующих VMD на основе дескрипторов
        for alert_desc in condition_descriptors:
            vmd_handle = get_vmd_handle(alert_desc.Handle)
            alarms_folder = get_alarms_folder(vmd_handle)

            source_handles = ""
            if hasattr(alert_desc, 'Source') and alert_desc.Source:
                source_handles = ",".join([str(sh) for sh in alert_desc.Source])

            # Создаем отдельный объект под Condition, чтобы хранить несколько параметров
            cond_folder = alarms_folder.add_object(mds_idx, f"Condition_{alert_desc.Handle}")
            
            state = condition_states.get(alert_desc.Handle)
            presence = getattr(state, 'Presence', False) if state else False

            opc_presence = cond_folder.add_variable(mds_idx, "Presence", presence)
            opc_presence.set_writable()

            opc_source = cond_folder.add_variable(mds_idx, "Source", source_handles)
            opc_source.set_writable()

        for signal_desc in alert_signal_descriptors:
            vmd_handle = get_vmd_handle(signal_desc.Handle)
            alarms_folder = get_alarms_folder(vmd_handle)
            
            state = alert_signal_states.get(signal_desc.Handle)
            activation_state = str(getattr(state, 'ActivationState', 'Unknown')) if state else 'Unknown'

            opc_signal = alarms_folder.add_variable(mds_idx, f"Signal_{signal_desc.Handle}", activation_state)
            opc_signal.set_writable()

        self.opcua_server.start()