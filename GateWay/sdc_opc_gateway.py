from sdc11073.xml_types import pm_qnames as pm

# Переключаемся с asyncua.sync на asyncua
from asyncua import Server, ua, pubsub
from asyncua.ua import Double, String

class SdcOpcGateway:
    def __init__(self, bind_ip="0.0.0.0", port=4840, pubsub_url="opc.udp://239.0.0.1:4840"):
        self.server = Server()
        self.endpoint = f"opc.tcp://{bind_ip}:{port}/freeopcua/server/"
        self.pubsub_url = pubsub_url
        self.server.set_endpoint(self.endpoint)
        self.mds_idx = None
        self.opc_nodes = {}  # epr -> {handle: node}
        self.vmd_alarms_folders = {} # epr -> {handle: node}
        self.pubsub_connection = None
        self.pubsub_service = None
        self.writer_group_id = 1
        
    async def init(self):
        await self.server.init()
        self.mds_idx = await self.server.register_namespace("SDC NodeSpace")
        self.pubsub_service = await self.server.get_pubsub()
        
        # Базовое подключение PubSub
        self.pubsub_connection = pubsub.PubSubConnection.udp_uadp(
            "SDC Publisher Connection",
            ua.UInt16(1),
            pubsub.UdpSettings(Url=self.pubsub_url),
        )
        await self.pubsub_service.add_connection(self.pubsub_connection)
        
    async def start(self):
        await self.server.start()
        await self.pubsub_service.start()
        print(f"[OPC UA] Async Central Server & PubSub started on {self.endpoint}")

    async def add_device(self, mdib, epr):
        opc_objects = self.server.nodes.objects
        
        self.opc_nodes[epr] = {}
        self.vmd_alarms_folders[epr] = {}

        provider_node = await opc_objects.add_object(self.mds_idx, f"Provider_{epr}")
        self.opc_nodes[epr]['Provider'] = provider_node
        
        await self._create_vmds(mdib, epr, provider_node)
        await self._create_channels(mdib, epr, provider_node)
        await self._create_metrics(mdib, epr, provider_node)
        await self._create_alarms(mdib, epr, provider_node)
        
        # Собираем ноды метрик для записи в PubSub Dataset (Dataset "имя устройства")
        await self._create_published_dataset(epr)

    async def update_values(self, epr, updates):
        """Асинхронно обновляет значения нод на сервере и для PubSub."""
        if epr not in self.opc_nodes:
            return
            
        for handle, value in updates.items():
            node = self.opc_nodes[epr].get(handle)
            if node:
                try:
                    await node.write_value(value)
                except Exception as e:
                    print(f"[OPC UA] Error updating node {handle}: {e}")

    async def _create_published_dataset(self, epr):
        # Очищаем epr от спецсимволов, ломающих парсер NodeId (особенно двоеточий)
        safe_epr = epr.replace(":", "_").replace("-", "_")

        variables = []
        # Добавляем все метрики в датасет
        for handle, node in self.opc_nodes[epr].items():
            if str(handle).startswith("Provider_") or str(handle).startswith("Condition_") or str(handle).startswith("Signal_"):
                continue # пропускаем папки и алармы
            var_class = await node.read_node_class()
            if var_class == ua.NodeClass.Variable:
                name = await node.read_display_name()
                variables.append(pubsub.TargetVariable(name.Text, node.nodeid))
                
        if variables:
            pds_name = f"Dataset_{safe_epr}"
            pds = await pubsub.PublishedDataItems.Create(pds_name, self.server, variables)
            await self.pubsub_service.add_published_dataset(pds)
            
            # Добавляем WriterGroup для этого устройства
            wg = pubsub.WriterGroup.new_uadp(
                name=f"WriterGroup_{safe_epr}",
                writer_group_id=ua.UInt16(self.writer_group_id),
                publishing_interval=1000, # Публикуем раз в секунду
                writer=[
                    pubsub.DataSetWriter.new_uadp(
                        name=f"Writer_{safe_epr}",
                        dataset_writer_id=ua.UInt16(self.writer_group_id),
                        dataset_name=pds_name,
                        datavalue=True,
                    )
                ],
            )
            self.writer_group_id += 1
            await self.pubsub_connection.add_writer_group(wg)
            print(f"[OPC UA PubSub] Added Dataset for {epr} with {len(variables)} variables.")

    async def _create_vmds(self, mdib, epr, root_node):
        vmd_descriptors = [d for d in mdib.descriptions.objects if d.NODETYPE == pm.VmdDescriptor]
        for vmd in vmd_descriptors:
            self.opc_nodes[epr][vmd.Handle] = await root_node.add_object(self.mds_idx, vmd.Handle)

    async def _create_channels(self, mdib, epr, root_node):
        channel_descriptors = [d for d in mdib.descriptions.objects if d.NODETYPE == pm.ChannelDescriptor]
        for channel in channel_descriptors:
            parent_node = self.opc_nodes[epr].get(channel.parent_handle, root_node)
            self.opc_nodes[epr][channel.Handle] = await parent_node.add_object(self.mds_idx, channel.Handle)

    async def _create_metrics(self, mdib, epr, root_node):
        metric_descriptors = [m for m in mdib.descriptions.objects if m.NODETYPE == pm.NumericMetricDescriptor]
        metrics_states = {s.DescriptorHandle: s for s in mdib.states.objects if s.NODETYPE == pm.NumericMetricState}

        for metric in metric_descriptors:
            parent_node = self.opc_nodes[epr].get(metric.parent_handle, root_node)
            state = metrics_states.get(metric.Handle)
            initial_value = 0.0
            if state and getattr(state, 'MetricValue', None) and getattr(state.MetricValue, 'Value', None) is not None:
                try:
                    initial_value = float(state.MetricValue.Value)
                except ValueError:
                    pass

            opc_metric = await parent_node.add_variable(self.mds_idx, metric.Handle, initial_value, varianttype=Double)
            await opc_metric.set_writable()
            self.opc_nodes[epr][metric.Handle] = opc_metric

        string_desc_types = [pm.StringMetricDescriptor, pm.EnumStringMetricDescriptor]
        string_descriptors = [m for m in mdib.descriptions.objects if m.NODETYPE in string_desc_types]
        string_states = {s.DescriptorHandle: s for s in mdib.states.objects if s.NODETYPE in [pm.StringMetricState, pm.EnumStringMetricState]}

        for metric in string_descriptors:
            parent_node = self.opc_nodes[epr].get(metric.parent_handle, root_node)
            state = string_states.get(metric.Handle)
            initial_value = "---"
            if state and getattr(state, 'MetricValue', None) and getattr(state.MetricValue, 'Value', None) is not None:
                initial_value = str(state.MetricValue.Value)

            opc_metric = await parent_node.add_variable(self.mds_idx, metric.Handle, initial_value, varianttype=String)
            await opc_metric.set_writable()
            self.opc_nodes[epr][metric.Handle] = opc_metric

    def _get_vmd_handle(self, mdib, epr, desc_handle):
        desc = next((d for d in mdib.descriptions.objects if d.Handle == desc_handle), None)
        while desc and desc.parent_handle:
            desc = next((d for d in mdib.descriptions.objects if d.Handle == desc.parent_handle), None)
            if desc and desc.NODETYPE == pm.VmdDescriptor:
                return desc.Handle
        return None

    async def _get_alarms_folder(self, vmd_handle, epr, root_node):
        if vmd_handle not in self.vmd_alarms_folders[epr]:
            parent_node = self.opc_nodes[epr].get(vmd_handle, root_node)
            self.vmd_alarms_folders[epr][vmd_handle] = await parent_node.add_object(self.mds_idx, "Alarms")
        return self.vmd_alarms_folders[epr][vmd_handle]

    async def _create_alarms(self, mdib, epr, root_node):
        alert_cond_desc_types = [pm.AlertConditionDescriptor, pm.LimitAlertConditionDescriptor]
        condition_descriptors = [d for d in mdib.descriptions.objects if d.NODETYPE in alert_cond_desc_types]
        condition_states = {s.DescriptorHandle: s for s in mdib.states.objects if s.NODETYPE in [pm.AlertConditionState, pm.LimitAlertConditionState]}
        
        alert_signal_descriptors = [d for d in mdib.descriptions.objects if d.NODETYPE == pm.AlertSignalDescriptor]
        alert_signal_states = {s.DescriptorHandle: s for s in mdib.states.objects if s.NODETYPE == pm.AlertSignalState}

        for alert_desc in condition_descriptors:
            vmd_handle = self._get_vmd_handle(mdib, epr, alert_desc.Handle)
            alarms_folder = await self._get_alarms_folder(vmd_handle, epr, root_node)

            source_handles = ""
            if hasattr(alert_desc, 'Source') and alert_desc.Source:
                source_handles = ",".join([str(sh) for sh in alert_desc.Source])

            cond_folder = await alarms_folder.add_object(self.mds_idx, f"Condition_{alert_desc.Handle}")
            state = condition_states.get(alert_desc.Handle)
            presence = getattr(state, 'Presence', False) if state else False

            opc_presence = await cond_folder.add_variable(self.mds_idx, "Presence", presence)
            await opc_presence.set_writable()
            self.opc_nodes[epr][f"Condition_{alert_desc.Handle}_Presence"] = opc_presence

            opc_source = await cond_folder.add_variable(self.mds_idx, "Source", source_handles)
            await opc_source.set_writable()

        for signal_desc in alert_signal_descriptors:
            vmd_handle = self._get_vmd_handle(mdib, epr, signal_desc.Handle)
            alarms_folder = await self._get_alarms_folder(vmd_handle, epr, root_node)

            state = alert_signal_states.get(signal_desc.Handle)
            signal_presence = str(getattr(state, 'Presence', 'Unknown')) if state else 'Unknown'

            opc_signal = await alarms_folder.add_variable(self.mds_idx, f"Signal_{signal_desc.Handle}", signal_presence)
            await opc_signal.set_writable()
            self.opc_nodes[epr][f"Signal_{signal_desc.Handle}"] = opc_signal
