"""live_check.py -- end-to-end check of testbed/providers/testbed_provider.py with an sdc11073 consumer.

Usage:  .venv\\Scripts\\python tools\\live_check.py --mdib testbed/mdib/ecg_mdib_v2.xml --certs pki/ecg --ip <adapter-ipv4> --port 10002 [--no-room]

Starts the provider as a subprocess (stdin piped), connects over mTLS with pki/testconsumer,
reads MDIB + context states, drives 'alarm <ch> on', acknowledges with accept and refuse policy,
writes the EnsembleContext like the Dashboard, then sends 'shutdown' and waits for WS-Discovery Bye.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import threading
import time
import uuid
import queue

ROOT = pathlib.Path(__file__).resolve()
for parent in ROOT.parents:
    if (parent / 'pki').is_dir() and (parent / 'testbed').is_dir():
        REPO = parent
        break
else:
    REPO = pathlib.Path(r'C:\Users\zheka\Documents\VSCode\sdc11073')

from sdc11073 import observableproperties as properties  # noqa: E402
from sdc11073.certloader import mk_ssl_contexts_from_folder  # noqa: E402
from sdc11073.consumer.consumerimpl import SdcConsumer  # noqa: E402
from sdc11073.mdib import ConsumerMdib  # noqa: E402
from sdc11073.wsdiscovery import WSDiscovery  # noqa: E402
from sdc11073.xml_types import pm_qnames as pm  # noqa: E402
from sdc11073.xml_types import pm_types  # noqa: E402
from sdc11073.xml_types.msg_types import InvocationState  # noqa: E402
from sdc11073 import loghelper  # noqa: E402
import logging  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    RESULTS.append((name, ok, detail))
    print(f"CHECK {'PASS' if ok else 'FAIL'}: {name} {detail}", flush=True)


class ProviderProc:
    def __init__(self, args: list[str]):
        self.proc = subprocess.Popen(
            [sys.executable, str(REPO / 'testbed' / 'providers' / 'testbed_provider.py'), *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1, cwd=str(REPO))
        self.lines: list[str] = []
        self.q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip('\n')
            self.lines.append(line)
            self.q.put(line)
            print(f'P| {line}', flush=True)

    def send(self, cmd: str):
        print(f'>> {cmd}', flush=True)
        self.proc.stdin.write(cmd + '\n')
        self.proc.stdin.flush()

    def wait_line(self, substring: str, timeout: float = 15) -> str | None:
        end = time.monotonic() + timeout
        for line in list(self.lines):
            if substring in line:
                return line
        while time.monotonic() < end:
            try:
                line = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if substring in line:
                return line
        return None

    def wait_new_line(self, substring: str, timeout: float = 15) -> str | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                line = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if substring in line:
                return line
        return None


def run(mdib: str, certs: str, ip: str, port: int, epr: str, room: str | None) -> None:
    loghelper.basic_logging_setup(level=logging.WARNING)
    args = ['--mdib', mdib, '--epr', epr, '--manufacturer', 'SDC Testbed', '--model', 'Test Model',
            '--friendly-name', 'live check device', '--patient-id', 'P001',
            '--certs-dir', certs, '--ip', ip, '--port', str(port), '--ack', 'accept', '--seed', '7']
    if room:
        args += ['--room', room, '--bed', '1']
    prov = ProviderProc(args)
    line = prov.wait_line('ready:', timeout=30)
    check('provider started and listed channels', line is not None, line or 'no ready line')
    channels = []
    for ln in prov.lines:
        if ln.startswith("  channel '"):
            channels.append(ln.split("'")[1])
    print('channels:', channels)

    # --- discovery ---------------------------------------------------------------
    wsd = WSDiscovery(ip)
    bye_evt = threading.Event()
    bye_seen: list[str] = []

    def on_bye(addr, epr_seen):
        bye_seen.append(epr_seen)
        if epr_seen == epr:
            bye_evt.set()

    wsd.set_remote_service_bye_callback(on_bye)
    wsd.start()
    services = wsd.search_services(timeout=6)
    service = next((s for s in services if s.epr == epr), None)
    check('provider discovered via WS-Discovery', service is not None,
          f'x_addrs={service.x_addrs if service else None}')
    if service is None:
        prov.send('shutdown')
        prov.proc.wait(10)
        wsd.stop()
        return
    check('provider announces https', all(a.startswith('https://') for a in service.x_addrs), str(service.x_addrs))
    check('provider uses the fixed port', all(f':{port}/' in a for a in service.x_addrs), str(service.x_addrs))

    # --- connect over mTLS -------------------------------------------------------
    ssl_container = mk_ssl_contexts_from_folder(REPO / 'pki' / 'testconsumer', private_key='userkey.pem',
                                                certificate='sdccert.pem', ca_public_key='cacert.pem')
    consumer = SdcConsumer.from_wsd_service(service, ssl_context_container=ssl_container)
    consumer.start_all()
    check('consumer connected over TLS', consumer.is_ssl_connection is True, f'is_ssl={consumer.is_ssl_connection}')
    tm = consumer.host_description.this_model
    td = consumer.host_description.this_device
    check('DPWS identity', tm.Manufacturer[0].text == 'SDC Testbed' and tm.ModelName[0].text == 'Test Model'
          and td.FriendlyName[0].text == 'live check device',
          f'{tm.Manufacturer[0].text}/{tm.ModelName[0].text}/{td.FriendlyName[0].text}')

    mdib_c = ConsumerMdib(consumer)
    mdib_c.init_mdib()
    check('GetMdib received', len(mdib_c.descriptions.objects) > 0, f'{len(mdib_c.descriptions.objects)} descriptors')

    def ctx_states(node_type):
        return [s for s in mdib_c.context_states.objects if s.NODETYPE == node_type]

    pats = ctx_states(pm.PatientContextState)
    check('PatientContextState with Identification', bool(pats) and pats[0].Identification
          and pats[0].Identification[0].Extension == 'P001'
          and pats[0].ContextAssociation == pm_types.ContextAssociation.ASSOCIATED,
          f'{[(p.Handle, [ (i.Root, i.Extension) for i in p.Identification], str(p.ContextAssociation)) for p in pats]}')
    wfs = ctx_states(pm.WorkflowContextState)
    check('WorkflowContextState with patient', bool(wfs) and wfs[0].WorkflowDetail is not None
          and wfs[0].WorkflowDetail.Patient.Identification[0].Extension == 'P001',
          f'{[w.Handle for w in wfs]}')
    locs = ctx_states(pm.LocationContextState)
    if room:
        check('LocationContextState with room/bed', bool(locs) and locs[0].LocationDetail.Room == room
              and locs[0].LocationDetail.Bed == '1',
              f'{[(l.Handle, l.LocationDetail.Room, l.LocationDetail.Bed) for l in locs]}')
    else:
        check('no LocationContextState without --room', not locs, f'{[l.Handle for l in locs]}')
    ens = ctx_states(pm.EnsembleContextState)
    check('EnsembleContextState slot present, unassociated', bool(ens)
          and ens[0].ContextAssociation == pm_types.ContextAssociation.NO_ASSOCIATION, f'{[e.Handle for e in ens]}')

    # alert systems: LastSelfCheck present
    alert_systems = [s for s in mdib_c.states.objects if s.NODETYPE == pm.AlertSystemState]
    check('AlertSystemState.LastSelfCheck set at start',
          bool(alert_systems) and all(s.LastSelfCheck is not None and abs(time.time() - s.LastSelfCheck) < 30
                                      for s in alert_systems),
          f'{[(s.DescriptorHandle, s.LastSelfCheck) for s in alert_systems]}')

    # --- observe reports -----------------------------------------------------------
    alert_updates: list = []
    metric_updates: list = []
    ctx_updates: list = []
    lock = threading.Lock()

    def on_alert(states_by_handle):
        with lock:
            alert_updates.append(dict(states_by_handle))

    def on_metric(states_by_handle):
        with lock:
            metric_updates.append(dict(states_by_handle))

    def on_ctx(states_by_handle):
        with lock:
            ctx_updates.append(dict(states_by_handle))

    properties.bind(mdib_c, alert_by_handle=on_alert)
    properties.bind(mdib_c, metrics_by_handle=on_metric)
    properties.bind(mdib_c, context_by_handle=on_ctx)

    time.sleep(3.5)
    with lock:
        n_metric = len(metric_updates)
    check('EpisodicMetricReport once per second', n_metric >= 2, f'{n_metric} reports in 3.5 s')

    # self-check refresh -> alert reports with AlertSystemState
    time.sleep(6)
    with lock:
        sc = [u for u in alert_updates if any(st.NODETYPE == pm.AlertSystemState for st in u.values())]
    check('LastSelfCheck refreshed via EpisodicAlertReport (period 10 s, refresh 5 s)', len(sc) >= 1,
          f'{len(sc)} alert-system reports in ~9 s')

    # --- alarm on ----------------------------------------------------------------
    ch = channels[0]
    cond_descr = next(d for d in mdib_c.descriptions.objects
                      if d.NODETYPE == pm.AlertConditionDescriptor and d.Handle.endswith('alert_cond_' + ch))
    sig_descr = next(d for d in mdib_c.descriptions.objects
                     if d.NODETYPE == pm.AlertSignalDescriptor and d.ConditionSignaled == cond_descr.Handle)
    op_descr = next(d for d in mdib_c.descriptions.objects
                    if d.NODETYPE == pm.SetAlertStateOperationDescriptor and d.OperationTarget == sig_descr.Handle)
    with lock:
        alert_updates.clear()
    prov.send(f'alarm {ch} on')
    ok = prov.wait_new_line(f'alarm {ch} ON', 10) is not None
    deadline = time.monotonic() + 10
    got = False
    while time.monotonic() < deadline and not got:
        with lock:
            for u in alert_updates:
                c = u.get(cond_descr.Handle)
                s = u.get(sig_descr.Handle)
                if c is not None and c.Presence is True and s is not None \
                        and s.Presence == pm_types.AlertSignalPresence.ON:
                    got = True
        time.sleep(0.2)
    cs = mdib_c.states.descriptor_handle.get_one(cond_descr.Handle)
    check(f'alarm {ch} on reaches the consumer (EpisodicAlertReport, condition+signal)', ok and got,
          f'Presence={cs.Presence} DeterminationTime={cs.DeterminationTime}')

    # --- acknowledgement: accept -----------------------------------------------------
    proposed = mdib_c.xtra.mk_proposed_state(sig_descr.Handle)
    proposed.Presence = pm_types.AlertSignalPresence.ACK
    fut = consumer.set_service_client.set_alert_state(op_descr.Handle, proposed)
    res = fut.result(timeout=15)
    st = res.InvocationInfo.InvocationState
    line = prov.wait_line("SetAlertState op='" + op_descr.Handle + "' Presence=Ack -> ACCEPT (Fin)", 5)
    time.sleep(1)
    sig_state = mdib_c.states.descriptor_handle.get_one(sig_descr.Handle)
    check('ack with policy accept -> InvocationState Fin, provider printed ACCEPT',
          st == InvocationState.FINISHED and line is not None, f'state={st} line={line}')
    check('signal Presence=Ack after accepted ack', sig_state.Presence == pm_types.AlertSignalPresence.ACK,
          f'Presence={sig_state.Presence}')

    # --- acknowledgement: refuse -------------------------------------------------------
    prov.send('ack refuse')
    prov.wait_new_line('ack policy = refuse', 5)
    proposed = mdib_c.xtra.mk_proposed_state(sig_descr.Handle)
    proposed.Presence = pm_types.AlertSignalPresence.ACK
    fut = consumer.set_service_client.set_alert_state(op_descr.Handle, proposed)
    res = fut.result(timeout=15)
    st = res.InvocationInfo.InvocationState
    line = prov.wait_line("Presence=Ack -> REFUSE (Fail)", 5)
    check('ack with policy refuse -> InvocationState Fail, provider printed REFUSE',
          st == InvocationState.FAILED and line is not None,
          f'state={st} error={res.InvocationInfo.InvocationError} parts={[p.InvocationInfo.InvocationState for p in res.report_parts]}')

    # --- value / status / alarm off ----------------------------------------------------
    prov.send(f'value {ch} 123')
    l1 = prov.wait_new_line(f'value {ch} = 123 (fixed; rest noise off)', 5)
    time.sleep(1.5)
    ms = mdib_c.states.descriptor_handle.get_one(cond_descr.Source[0])
    check('value <ch> 123 published', l1 is not None and ms.MetricValue.Value == 123, f'consumer sees {ms.MetricValue.Value}')
    prov.send(f'rest {ch}')
    prov.wait_new_line(f'rest {ch} (initial', 5)
    prov.send(f'alarm {ch} off')
    prov.wait_new_line(f'alarm {ch} OFF', 5)
    prov.send('status')
    l2 = prov.wait_new_line('status: ack=refuse selfcheck=on', 5)
    check('status line', l2 is not None, l2 or '')
    time.sleep(1)
    cs = mdib_c.states.descriptor_handle.get_one(cond_descr.Handle)
    check('alarm off reaches the consumer', cs.Presence is False, f'Presence={cs.Presence}')

    # --- SetContextState like the Dashboard (ensemble) ----------------------------------
    ens_descr = next(d for d in mdib_c.descriptions.objects if d.NODETYPE == pm.EnsembleContextDescriptor)
    ens_op = next(d for d in mdib_c.descriptions.objects
                  if d.NODETYPE == pm.SetContextStateOperationDescriptor and d.OperationTarget == ens_descr.Handle)
    existing = [s for s in mdib_c.context_states.objects if s.NODETYPE == pm.EnsembleContextState]
    proposed_ctx = existing[0].mk_copy()
    ens_uuid = str(uuid.uuid4())
    proposed_ctx.Identification.clear()
    ident = pm_types.InstanceIdentifier(root='bce837e3-0c46-4e52-af32-15bb36cfd746', extension_string=ens_uuid)
    ident.IdentifierName.append(pm_types.LocalizedText(ens_uuid))
    proposed_ctx.Identification.append(ident)
    proposed_ctx.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED
    fut = consumer.context_service_client.set_context_state(ens_op.Handle, [proposed_ctx])
    res = fut.result(timeout=15)
    line = prov.wait_line(f'EnsembleContext set by consumer: uuid={ens_uuid}', 10)
    check('SetContextState(ensemble) accepted and printed by provider',
          res.InvocationInfo.InvocationState == InvocationState.FINISHED and line is not None,
          f'state={res.InvocationInfo.InvocationState} line={line}')
    time.sleep(1)
    ens_now = [s for s in mdib_c.context_states.objects if s.NODETYPE == pm.EnsembleContextState]
    check('consumer sees updated EnsembleContextState (same handle, Assoc)',
          len(ens_now) == 1 and ens_now[0].Handle == existing[0].Handle
          and ens_now[0].ContextAssociation == pm_types.ContextAssociation.ASSOCIATED
          and ens_now[0].Identification[0].Extension == ens_uuid,
          f'{[(e.Handle, str(e.ContextAssociation), [i.Extension for i in e.Identification]) for e in ens_now]}')

    # --- selfcheck off: no more AlertSystemState reports -----------------------------------
    prov.send('selfcheck off')
    prov.wait_new_line('selfcheck off', 5)
    time.sleep(1)
    with lock:
        alert_updates.clear()
    time.sleep(8)
    with lock:
        sc = [u for u in alert_updates if any(st.NODETYPE == pm.AlertSystemState for st in u.values())]
    check('selfcheck off stops LastSelfCheck updates', len(sc) == 0, f'{len(sc)} alert-system reports in 8 s')
    prov.send('selfcheck on')
    prov.wait_new_line('selfcheck on', 5)

    # --- shutdown: Bye -------------------------------------------------------------------
    prov.send('shutdown')
    bye = bye_evt.wait(timeout=15)
    rc = prov.proc.wait(timeout=20)
    stopped = prov.wait_line('TestbedProvider stopped', 5) is not None
    check('shutdown -> WS-Discovery Bye received by consumer', bye, f'bye eprs={bye_seen}')
    check('provider process exited cleanly', rc == 0 and stopped, f'rc={rc}')

    try:
        consumer.stop_all(unsubscribe=False)
    except Exception as ex:  # noqa: BLE001
        print('consumer stop:', repr(ex))
    wsd.stop()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--mdib', required=True)
    ap.add_argument('--certs', required=True)
    ap.add_argument('--ip', required=True)
    ap.add_argument('--port', type=int, required=True)
    ap.add_argument('--epr', default='urn:uuid:' + str(uuid.uuid4()))
    ap.add_argument('--room', default='ICU-1')
    ap.add_argument('--no-room', action='store_true')
    a = ap.parse_args()
    run(a.mdib, a.certs, a.ip, a.port, a.epr, None if a.no_room else a.room)
    failed = [r for r in RESULTS if not r[1]]
    print(f'\nSUMMARY: {len(RESULTS) - len(failed)} passed, {len(failed)} failed')
    for name, _, detail in failed:
        print('  FAILED:', name, detail)
    sys.exit(1 if failed else 0)
