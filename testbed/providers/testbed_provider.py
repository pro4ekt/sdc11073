#!/usr/bin/env python
"""testbed_provider.py -- configurable SDC provider for the thesis testbed (Python, sdc11073 3.0.0).

Command-line options, stdin commands and console format follow the C++ reference provider
that was used during development (not part of this repository), so both stacks can be driven
identically. One script for every device; the device is defined by the MDIB file (--mdib).

Every library call is taken from the installed sdc11073 3.0.0 sources in .venv:
  sdc11073/provider/providerimpl.py     SdcProvider, RoleProviderComponents, start_all(shared_http_server)
  sdc11073/provider/baseproduct.py      BaseProduct (product = list of role providers per SCO)
  sdc11073/provider/protocols/roleproviderprotocol.py   RoleProviderProtocol
  sdc11073/provider/operations.py       ExecuteParameters, ExecuteResult, operation classes
  sdc11073/provider/sco.py              _OperationsWorker: WAIT -> START -> execute_operation() -> final state
  sdc11073/mdib/providermdib.py         ProviderMdib.from_mdib_file, *_transaction()
  sdc11073/mdib/transactions.py         get_state / mk_context_state / get_context_state
  sdc11073/mdib/providermdibxtra.py     mk_state_containers_for_all_descriptors (the ONLY place where the
                                        library touches LastSelfCheck: once, at load, for states that are
                                        missing in the file; never periodically)
  sdc11073/httpserver/httpserverimpl.py HttpServerThreadBase (binds port 0 -> subclass binds a fixed port)
  sdc11073/certloader.py                mk_ssl_contexts_from_folder
  sdc11073/wsdiscovery/wsdimpl.py       WSDiscovery(ip), publish_service, clear_service (sends Bye)

Note on sdc11073 3.0.0 in this venv: the package has NO 'roles' sub-package (no ExtendedProduct,
no GenericAlarmProvider); older reference providers that import it do not
run with this installation. Therefore this script brings its own product/role provider
(TestbedProduct / TestbedRoleProvider) that handles SetAlertState and SetContextState.

Usage (see --help):
  python testbed/providers/testbed_provider.py --mdib testbed/mdib/ecg_mdib_v2.xml --epr urn:uuid:...
      --manufacturer "SDC Testbed" --model "ECG Monitor T1" --friendly-name "Testbed ECG monitor, bed 1"
      --patient-id P001 --room ICU-1 --bed 1 --certs-dir pki/ecg --ip 192.168.0.105 --port 10010
      [--ack accept|refuse] [--seed N]
  then type commands on stdin, e.g.:  alarm tachycardia on
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import random
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sdc11073 import loghelper
from sdc11073.certloader import mk_ssl_contexts_from_folder
from sdc11073.httpserver.httpserverimpl import HttpServerThreadBase, _ThreadingHTTPServer
from sdc11073.mdib import ProviderMdib
from sdc11073.provider import SdcProvider
from sdc11073.provider.baseproduct import BaseProduct
from sdc11073.provider.operations import ExecuteParameters, ExecuteResult
from sdc11073.provider.providerimpl import RoleProviderComponents
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.location import SdcLocation
from sdc11073.provider.scopesfactory import mk_scopes
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types import pm_types
from sdc11073.xml_types.dpws_types import ThisDeviceType, ThisModelType
from sdc11073.xml_types.msg_types import InvocationState

# =============================================================================
# Timestamps for the console: epoch milliseconds plus wall clock (same format as the reference provider)
# =============================================================================


def now_ms() -> int:
    return int(time.time() * 1000)


def stamp() -> str:
    ms = now_ms()
    hms = time.strftime('%H:%M:%S', time.localtime(ms // 1000))
    return f'[{ms} {hms}.{ms % 1000:03d}]'


def say(text: str) -> None:
    print(f'{stamp()} {text}', flush=True)


def out(text: str) -> None:
    """Plain line without timestamp."""
    print(text, flush=True)


# =============================================================================
# Data read from the MDIB
# =============================================================================


@dataclass
class ChannelSpec:
    name: str
    metric_handle: str
    condition_handle: str
    signal_handle: str
    initial_value: Decimal
    decimals: int


@dataclass
class ChannelRuntime:
    fixed: bool = False
    fixed_value: Decimal = Decimal(0)
    alarm: bool = False
    last_published: str = ''


@dataclass
class AlertSystemSpec:
    handle: str
    period_sec: float  # SelfCheckPeriod in seconds (0 = none -> no self-check for it)


@dataclass
class Options:
    mdib: pathlib.Path
    epr: str
    manufacturer: str
    model: str
    friendly_name: str
    serial: str
    patient_root: str
    patient_id: str
    room: str
    bed: str
    certs_dir: pathlib.Path | None
    no_tls: bool
    ip: str
    port: int
    ack_accept: bool
    seed: int | None
    scope_room: str = ''   # test only: room announced in the discovery scope (!= --room)
    ctx_patient: str = ''
    ctx_location: str = ''
    ctx_ensemble: str = ''
    ctx_workflow: str = ''
    channels: list[ChannelSpec] = field(default_factory=list)
    alert_systems: list[AlertSystemSpec] = field(default_factory=list)


DEFAULT_PATIENT_ROOT = 'urn:uuid:7e0d2b48-3f0f-4c8e-9a6f-2c1d5e8f9a01'  # fixed root shared by all testbed devices


def parse_args(argv: list[str]) -> Options:
    parser = argparse.ArgumentParser(
        prog='testbed_provider',
        description='TestbedProvider (Python) -- configurable SDC provider for the thesis testbed',
        epilog=(
            'Read from the MDIB: channels (every AlertCondition with its Source metric and its AlertSignal), '
            'context descriptor handles, alert systems with SelfCheckPeriod, initial metric values.\n'
            'Commands on standard input (one per line):\n'
            '  value <channel> <number>   publish exactly this value (rest noise off for the channel)\n'
            '  rest <channel>             back to rest mode (initial value +/- 2 %%)\n'
            '  alarm <channel> on|off     presence of the channel\'s condition and signal, with DeterminationTime\n'
            '  ack accept|refuse          acknowledgement policy for SetAlertState\n'
            '  selfcheck off|on           stop / resume LastSelfCheck updates of all alert systems\n'
            '  status                     values, mode and alarms of all channels\n'
            '  shutdown                   graceful shutdown (Bye), also on Ctrl-C'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    req = parser.add_argument_group('required')
    req.add_argument('--mdib', required=True, help='MDIB file (GetMdibResponse wrapper), defines the device')
    req.add_argument('--epr', required=True, help='endpoint reference, e.g. urn:uuid:...')
    req.add_argument('--manufacturer', required=True, help='DPWS ThisModel/Manufacturer')
    req.add_argument('--model', required=True, help='DPWS ThisModel/ModelName')
    req.add_argument('--friendly-name', required=True, help='DPWS ThisDevice/FriendlyName')
    req.add_argument('--patient-id', required=True, help='patient identifier (InstanceIdentifier/@Extension)')
    opt = parser.add_argument_group('optional')
    opt.add_argument('--patient-root', default=DEFAULT_PATIENT_ROOT,
                     help='InstanceIdentifier/@Root (default fixed urn:uuid, same for every testbed device)')
    opt.add_argument('--room', default='', help='LocationContextState Room; without --room no location state is created')
    opt.add_argument('--bed', default='', help='LocationContextState Bed (requires --room)')
    opt.add_argument('--scope-room', default='',
                     help='TEST ONLY: announce this room in the WS-Discovery location scope while the '
                          'LocationContextState keeps --room (scope/context discrepancy, consumer 2f)')
    opt.add_argument('--serial', default='0001', help='DPWS serial number (default 0001)')
    opt.add_argument('--certs-dir', default=None,
                     help='folder from pki/, e.g. pki/ecg (userkey.pem, sdccert.pem, cacert.pem) -> mTLS')
    opt.add_argument('--no-tls', action='store_true', help='plain HTTP, debugging only (prints a warning)')
    opt.add_argument('--ip', default=None, help='local IPv4 address to bind (default: first adapter IPv4)')
    opt.add_argument('--port', type=int, default=0,
                     help='fixed HTTPS port of the provider (default 0 = chosen by the OS); '
                          'fixed port lets you create the Windows firewall rule once')
    opt.add_argument('--ack', choices=('accept', 'refuse'), default='accept',
                     help='remote acknowledgement policy (default accept); changeable at runtime')
    opt.add_argument('--seed', type=int, default=None, help='fixed seed for the rest-mode noise (reproducible runs)')
    opt.add_argument('--ctx-patient', default='', help='override PatientContext descriptor handle read from the MDIB')
    opt.add_argument('--ctx-location', default='', help='override LocationContext descriptor handle')
    opt.add_argument('--ctx-ensemble', default='', help='override EnsembleContext descriptor handle')
    opt.add_argument('--ctx-workflow', default='', help='override WorkflowContext descriptor handle')
    a = parser.parse_args(argv)

    if not a.patient_root.startswith('urn:'):
        parser.error(f"--patient-root must be a URI such as urn:uuid:... or urn:oid:..., got '{a.patient_root}'")
    if a.bed and not a.room:
        parser.error('--bed requires --room')
    if a.seed is not None and a.seed < 0:
        parser.error(f"--seed expects a non-negative integer, got '{a.seed}'")
    if not a.no_tls and a.certs_dir is None:
        parser.error('either --certs-dir DIR (mTLS) or --no-tls (debugging) is required')
    if a.no_tls and a.certs_dir is not None:
        parser.error('--no-tls and --certs-dir exclude each other')
    return Options(
        mdib=pathlib.Path(a.mdib), epr=a.epr, manufacturer=a.manufacturer, model=a.model,
        friendly_name=a.friendly_name, serial=a.serial, patient_root=a.patient_root,
        patient_id=a.patient_id, room=a.room, bed=a.bed, scope_room=a.scope_room,
        certs_dir=pathlib.Path(a.certs_dir) if a.certs_dir else None, no_tls=a.no_tls,
        ip=a.ip, port=a.port, ack_accept=(a.ack == 'accept'), seed=a.seed,
        ctx_patient=a.ctx_patient, ctx_location=a.ctx_location,
        ctx_ensemble=a.ctx_ensemble, ctx_workflow=a.ctx_workflow,
    )


# =============================================================================
# Fixed-point text for a metric value (rounding half away from zero)
# =============================================================================


def format_value(value: Decimal | float, decimals: int) -> str:
    quantum = Decimal(1).scaleb(-decimals)  # 10**-decimals
    return str(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def decimals_of(resolution: Decimal | None) -> int:
    if resolution is None:
        return 0
    exponent = resolution.normalize().as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


# =============================================================================
# Reading channels, alert systems and context handles from the MDIB MODEL
# (read from the ProviderMdib containers)
# =============================================================================


def derive_from_mdib(mdib: ProviderMdib, opt: Options) -> None:
    conditions = mdib.descriptions.NODETYPE.get(pm.AlertConditionDescriptor, [])
    signals = mdib.descriptions.NODETYPE.get(pm.AlertSignalDescriptor, [])
    for cond in conditions:
        source = cond.Source[0] if cond.Source else ''
        signal_handle = ''
        for sig in signals:
            if sig.ConditionSignaled == cond.Handle:
                signal_handle = sig.Handle
                break
        if not source or not signal_handle:
            raise SystemExit(f"ERROR: MDIB: alert condition '{cond.Handle}' has no Source metric "
                             f'or no AlertSignal referencing it')
        tag = cond.Handle.find('alert_cond_')
        name = cond.Handle if tag < 0 else cond.Handle[tag + len('alert_cond_'):]

        metric_descr = mdib.descriptions.handle.get_one(source, allow_none=True)
        if metric_descr is None or metric_descr.NODETYPE != pm.NumericMetricDescriptor:
            raise SystemExit(f"ERROR: MDIB: Source '{source}' of '{cond.Handle}' is not a NumericMetricDescriptor")
        decimals = decimals_of(metric_descr.Resolution)

        metric_state = mdib.states.descriptor_handle.get_one(source, allow_none=True)
        if metric_state is None or metric_state.MetricValue is None or metric_state.MetricValue.Value is None:
            raise SystemExit(f"ERROR: MDIB: metric '{source}' has no numeric MetricValue/@Value in its state "
                             f'(needed for rest mode)')
        opt.channels.append(ChannelSpec(name, source, cond.Handle, signal_handle,
                                        Decimal(metric_state.MetricValue.Value), decimals))
    if not opt.channels:
        raise SystemExit('ERROR: MDIB: no AlertConditionDescriptor found -- nothing to drive')

    for alert_system in mdib.descriptions.NODETYPE.get(pm.AlertSystemDescriptor, []):
        period = alert_system.SelfCheckPeriod  # DurationAttributeProperty -> float seconds or None
        opt.alert_systems.append(AlertSystemSpec(alert_system.Handle, float(period) if period else 0.0))

    def first_handle(node_type) -> str:
        descriptors = mdib.descriptions.NODETYPE.get(node_type, [])
        return descriptors[0].Handle if descriptors else ''

    opt.ctx_patient = opt.ctx_patient or first_handle(pm.PatientContextDescriptor)
    opt.ctx_location = opt.ctx_location or first_handle(pm.LocationContextDescriptor)
    opt.ctx_ensemble = opt.ctx_ensemble or first_handle(pm.EnsembleContextDescriptor)
    opt.ctx_workflow = opt.ctx_workflow or first_handle(pm.WorkflowContextDescriptor)

    missing = ''
    if not opt.ctx_patient:
        missing += ' PatientContext'
    if not opt.ctx_workflow:
        missing += ' WorkflowContext'
    if not opt.ctx_ensemble:
        missing += ' EnsembleContext'
    if opt.room and not opt.ctx_location:
        missing += ' LocationContext (needed for --room)'
    if missing:
        raise SystemExit(f'ERROR: MDIB: SystemContext lacks descriptor(s):{missing}')


# =============================================================================
# HTTP server on a fixed port. sdc11073's HttpServerThreadBase.run() always binds
# (ip, 0) (httpserverimpl.py:146); this subclass repeats run() with the wanted port
# and is handed to SdcProvider.start_all(shared_http_server=...) (providerimpl.py:576).
# =============================================================================


class FixedPortHttpServerThread(HttpServerThreadBase):
    def __init__(self, port: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._port = port

    def run(self):
        self._stop_requested = False
        try:
            self.httpd = _ThreadingHTTPServer(
                self.logger,
                (self._my_ipaddress, self._port),
                self.chunk_size,
                self.supported_encodings,
            )
            self.logger.info('starting http server on %s:%s', self._my_ipaddress, self.server_port)
            if self._ssl_context:
                self.httpd.socket = self._ssl_context.wrap_socket(self.httpd.socket, server_side=True)
                self.base_url = f'https://{self._my_ipaddress}:{self.server_port}/'
            else:
                self.base_url = f'http://{self._my_ipaddress}:{self.server_port}/'
            self.started_evt.set()
            self.httpd.serve_forever()
        except Exception:
            if not self._stop_requested:
                self.logger.exception('Unhandled Exception at thread runtime. Thread will abort!')
            raise
        finally:
            self.logger.info('http server stopped.')


# =============================================================================
# Product / role provider: handlers for the operations declared in the MDIB.
#   SetAlertState  -> accept (Fin) or refuse (Fail), decided by the ack policy
#   SetContextState-> accept every write (Dashboard writes EnsembleContext and WorkflowContext)
# BaseProduct.init_operations() calls make_operation_instance() for every operation
# descriptor found under the SCO (baseproduct.py:117-131); the returned operation
# object is executed by the SCO worker thread: it sends OperationInvokedReport
# WAIT, START, then the ExecuteResult.invocation_state (sco.py:103-134).
# =============================================================================


class TestbedRoleProvider:
    """Implements sdc11073.provider.protocols.roleproviderprotocol.RoleProviderProtocol."""

    def __init__(self, mdib: ProviderMdib, shared: SharedState):
        self._mdib = mdib
        self._shared = shared

    def stop(self):
        pass

    def init_operations(self, sco):
        pass

    def make_missing_operations(self, sco) -> list:
        return []

    def on_pre_commit(self, mdib, transaction):
        pass

    def on_post_commit(self, mdib, transaction):
        pass

    def make_operation_instance(self, operation_descriptor_container, operation_cls_getter):
        node_type = operation_descriptor_container.NODETYPE
        if node_type == pm.SetAlertStateOperationDescriptor:
            handler = self._set_alert_state
        elif node_type == pm.SetContextStateOperationDescriptor:
            handler = self._set_context_state
        else:
            return None
        cls = operation_cls_getter(node_type)
        return cls(
            operation_descriptor_container.Handle,
            operation_descriptor_container.OperationTarget,
            operation_handler=handler,
            coded_value=operation_descriptor_container.Type,
        )

    # --- SetAlertState with the accept/refuse decision ----
    def _set_alert_state(self, params: ExecuteParameters) -> ExecuteResult:
        op_handle = params.operation_instance.handle
        proposed = params.operation_request.ProposedAlertState
        presence = 'Unknown'
        try:
            value = getattr(proposed, 'Presence', None)
            if value is not None:
                presence = str(value)
        except Exception:  # noqa: BLE001
            pass
        accept = self._shared.ack_accept
        say(f"<<< SetAlertState op='{op_handle}' Presence={presence} -> "
            f"{'ACCEPT (Fin)' if accept else 'REFUSE (Fail)'}")
        if not accept:
            # Refuse: final invocation state Fail, MDIB unchanged. The SCO worker turns this
            # into OperationInvokedReport/InvocationState=Fail (sco.py:127-134).
            return ExecuteResult(InvocationState.FAILED, self._mdib.mdib_version_group)

        target = params.operation_instance.operation_target_handle
        with self._shared.lock, self._mdib.alert_state_transaction() as mgr:
            state = mgr.get_state(target)
            if proposed is not None and proposed.DescriptorHandle == target:
                state.update_from_other_container(
                    proposed, skipped_properties=['StateVersion', 'DescriptorVersion', 'DescriptorHandle'])
                state.node = None
            if state.is_alert_signal:
                for i, ch in enumerate(self._shared.channels):
                    if ch.signal_handle == target:
                        self._shared.runtime[i].alarm = state.Presence == pm_types.AlertSignalPresence.ON
        return ExecuteResult(InvocationState.FINISHED, self._mdib.mdib_version_group, target)

    # --- SetContextState: accept every write ----
    def _set_context_state(self, params: ExecuteParameters) -> ExecuteResult:
        proposed_states = list(params.operation_request.ProposedContextState)
        target = params.operation_instance.operation_target_handle
        skipped = ['StateVersion', 'DescriptorVersion', 'DescriptorHandle', 'Handle',
                   'BindingMdibVersion', 'UnbindingMdibVersion', 'BindingStartTime', 'BindingEndTime']
        with self._shared.lock, self._mdib.context_state_transaction() as mgr:
            for proposed in proposed_states:
                existing = self._mdib.context_states.handle.get_one(proposed.Handle, allow_none=True)
                if existing is None or proposed.Handle == proposed.DescriptorHandle:
                    # sdc11073 convention (consumer/serviceclients/contextservice.py:48-57):
                    # Handle == DescriptorHandle means "insert a new context state"
                    state = mgr.mk_context_state(proposed.DescriptorHandle)
                    state.update_from_other_container(proposed, skipped_properties=skipped)
                    state.node = None
                    if state.ContextAssociation == pm_types.ContextAssociation.ASSOCIATED:
                        state.BindingMdibVersion = mgr.new_mdib_version
                        state.BindingStartTime = time.time()
                else:
                    state = mgr.get_context_state(proposed.Handle)
                    was_associated = state.ContextAssociation == pm_types.ContextAssociation.ASSOCIATED
                    state.update_from_other_container(proposed, skipped_properties=skipped)
                    state.node = None
                    if state.ContextAssociation == pm_types.ContextAssociation.ASSOCIATED and not was_associated:
                        state.BindingMdibVersion = mgr.new_mdib_version
                        state.BindingStartTime = time.time()
        return ExecuteResult(InvocationState.FINISHED, self._mdib.mdib_version_group, target)


class TestbedProduct(BaseProduct):
    """One product per SCO (providerimpl.py:_setup_components); carries the single role provider."""

    shared: SharedState | None = None  # set before SdcProvider is constructed

    def __init__(self, mdib, sco, log_prefix=None):
        super().__init__(mdib, sco, log_prefix)
        self._ordered_role_providers.append(TestbedRoleProvider(mdib, TestbedProduct.shared))


# =============================================================================
# Shared runtime state (one lock for every MDIB change and the random generator)
# =============================================================================


class SharedState:
    def __init__(self, channels: list[ChannelSpec], ack_accept: bool, seed: int | None):
        self.lock = threading.RLock()
        self.channels = channels
        self.runtime = [ChannelRuntime() for _ in channels]
        self.ack_accept = ack_accept
        self.selfcheck_enabled = True
        self.stop = False
        self.rng = random.Random(seed if seed is not None else time.time_ns())


# =============================================================================
# Context states created from the command line
# =============================================================================


def create_context_states(mdib: ProviderMdib, opt: Options) -> None:
    ident = pm_types.InstanceIdentifier(root=opt.patient_root, extension_string=opt.patient_id)

    with mdib.context_state_transaction() as mgr:
        patient = mgr.mk_context_state(opt.ctx_patient, context_state_handle=f'{opt.ctx_patient}_state',
                                       set_associated=True)
        patient.CoreData = None  # no demographics, by design of the testbed
        patient.Identification.append(ident)
    out(f'[Provider] PatientContextState created: descriptor={opt.ctx_patient} '
        f'root={opt.patient_root} extension={opt.patient_id}')

    with mdib.context_state_transaction() as mgr:
        workflow = mgr.mk_context_state(opt.ctx_workflow, context_state_handle=f'{opt.ctx_workflow}_state',
                                        set_associated=True)
        workflow.WorkflowDetail = pm_types.WorkflowDetail(
            patient=pm_types.PersonReference(identifications=[
                pm_types.InstanceIdentifier(root=opt.patient_root, extension_string=opt.patient_id)]))
        # Identification is required by the discovery scopes factory (mk_scopes) for
        # every associated context state; identify the workflow by the patient id.
        workflow.Identification.append(
            pm_types.InstanceIdentifier(root=opt.patient_root, extension_string=opt.patient_id))
    out(f'[Provider] WorkflowContextState created: descriptor={opt.ctx_workflow} '
        f'patient root={opt.patient_root} extension={opt.patient_id}')

    if opt.room:
        # Location through the sdc11073 location facility (GLUE 9.4.1.1 fallback
        # instance identifier), so that publish() announces the location scope
        # sdc.ctxt.loc:/sdc.ctxt.loc.detail/...?rm=<room>&bed=<bed> (IEEE 11073-20701 §9.4).
        mdib.xtra.set_location(SdcLocation(rm=opt.room, bed=(opt.bed or None)),
                               validators=[], location_context_descriptor_handle=opt.ctx_location)
        out(f'[Provider] LocationContextState created: descriptor={opt.ctx_location} '
            f"room={opt.room} bed={opt.bed if opt.bed else '(none)'} (via SdcLocation)")
    else:
        out('[Provider] no --room given: no LocationContextState created (topology test case)')


def read_ensemble_id(mdib: ProviderMdib, ctx_ensemble: str) -> str:
    """Current ensemble identifier written by the Dashboard, '' if none."""
    for state in mdib.context_states.descriptor_handle.get(ctx_ensemble, []):
        if state.Identification and state.Identification[0].Extension:
            return state.Identification[0].Extension
    return ''


# =============================================================================
# MDIB changes. Every function below must be called with SharedState.lock held.
# =============================================================================


def set_alarm_locked(mdib: ProviderMdib, ch: ChannelSpec, on: bool) -> None:
    with mdib.alert_state_transaction() as mgr:
        cond = mgr.get_state(ch.condition_handle)
        cond.Presence = on
        cond.DeterminationTime = time.time()
        sig = mgr.get_state(ch.signal_handle)
        sig.Presence = pm_types.AlertSignalPresence.ON if on else pm_types.AlertSignalPresence.OFF


def touch_selfcheck_locked(mdib: ProviderMdib, alert_system_handle: str) -> None:
    with mdib.alert_state_transaction() as mgr:
        state = mgr.get_state(alert_system_handle)
        state.LastSelfCheck = time.time()
        state.SelfCheckCount = (state.SelfCheckCount or 0) + 1


def publish_metric_locked(mdib: ProviderMdib, ch: ChannelSpec, text: str) -> None:
    with mdib.metric_state_transaction() as mgr:
        state = mgr.get_state(ch.metric_handle)
        state.MetricValue.Value = Decimal(text)


# =============================================================================
# Command interpreter (runs on the stdin thread)
# =============================================================================


def channel_names(opt: Options) -> str:
    return ', '.join(ch.name for ch in opt.channels)


def find_channel(opt: Options, name: str) -> int:
    for i, ch in enumerate(opt.channels):
        if ch.name == name:
            return i
    return -1


_PROVIDER = None   # SdcProvider, set at startup (for the subscription count in 'status')


def count_active_subscriptions() -> int:
    """Number of WS-Eventing subscriptions currently held by consumers (provider side)."""
    if _PROVIDER is None:
        return 0
    n = 0
    for mgr in getattr(_PROVIDER, '_subscriptions_managers', {}).values():
        for sub in getattr(mgr, '_subscriptions').objects:
            if getattr(sub, 'unsubscribed_at', None) is not None:
                continue
            valid = getattr(sub, 'is_valid', True)
            if callable(valid):
                valid = valid()
            if valid:
                n += 1
    return n


def print_status_locked(opt: Options, state: SharedState, ensemble_id: str) -> None:
    say(f"status: ack={'accept' if state.ack_accept else 'refuse'}"
        f" selfcheck={'on' if state.selfcheck_enabled else 'off'}"
        f" ensemble={ensemble_id if ensemble_id else '(none)'}"
        f" subscriptions={count_active_subscriptions()}")
    for ch, rt in zip(opt.channels, state.runtime):
        mode = f'fixed({format_value(rt.fixed_value, ch.decimals)})' if rt.fixed else 'rest'
        say(f"  {ch.name}: value={rt.last_published if rt.last_published else '-'}"
            f" mode={mode} alarm={'on' if rt.alarm else 'off'}"
            f'  [metric {ch.metric_handle}, condition {ch.condition_handle}, signal {ch.signal_handle}]')
    for a in opt.alert_systems:
        say(f'  alert system {a.handle}: SelfCheckPeriod={format_value(a.period_sec, 1)} s')


def handle_command(line: str, opt: Options, state: SharedState, mdib: ProviderMdib) -> None:
    words = line.split()
    if not words:
        return
    cmd = words[0]

    if cmd in ('value', 'rest', 'alarm'):
        if len(words) < 2:
            say(f'error: {cmd} needs a channel name ({channel_names(opt)})')
            return
        idx = find_channel(opt, words[1])
        if idx < 0:
            say(f"error: unknown channel '{words[1]}' (known: {channel_names(opt)}); no change")
            return
        ch = opt.channels[idx]
        rt = state.runtime[idx]

        if cmd == 'value':
            if len(words) < 3:
                say('error: value <channel> <number>')
                return
            try:
                v = Decimal(words[2])
                if not v.is_finite():
                    raise ValueError
            except Exception:  # noqa: BLE001
                say(f"error: '{words[2]}' is not a number; no change")
                return
            with state.lock:
                rt.fixed = True
                rt.fixed_value = v
                text = format_value(v, ch.decimals)
                publish_metric_locked(mdib, ch, text)
                rt.last_published = text
                say(f'value {ch.name} = {text} (fixed; rest noise off)')
        elif cmd == 'rest':
            with state.lock:
                rt.fixed = False
                say(f'rest {ch.name} (initial {format_value(ch.initial_value, ch.decimals)} +/- 2 %)')
        else:  # alarm
            if len(words) < 3 or words[2] not in ('on', 'off'):
                say('error: alarm <channel> on|off')
                return
            on = words[2] == 'on'
            with state.lock:
                try:
                    set_alarm_locked(mdib, ch, on)
                except Exception as ex:  # noqa: BLE001
                    say(f'error: alarm {ch.name} {words[2]} failed: {ex!r}')
                    return
                rt.alarm = on
                say(f"alarm {ch.name} {'ON' if on else 'OFF'} (condition {ch.condition_handle}, "
                    f'signal {ch.signal_handle}, DeterminationTime=now)')
    elif cmd == 'ack':
        if len(words) < 2 or words[1] not in ('accept', 'refuse'):
            say('error: ack accept|refuse')
            return
        state.ack_accept = words[1] == 'accept'
        say(f"ack policy = {'accept (Fin)' if state.ack_accept else 'refuse (Fail)'}")
    elif cmd == 'selfcheck':
        if len(words) < 2 or words[1] not in ('on', 'off'):
            say('error: selfcheck off|on')
            return
        state.selfcheck_enabled = words[1] == 'on'
        say('selfcheck ' + ('on (LastSelfCheck refreshed again)' if state.selfcheck_enabled
                            else 'off (LastSelfCheck frozen: alert systems look hung while the provider stays alive)'))
    elif cmd == 'status':
        with state.lock:
            print_status_locked(opt, state, read_ensemble_id(mdib, opt.ctx_ensemble))
    elif cmd in ('shutdown', 'quit', 'exit'):
        say('shutdown requested')
        state.stop = True
    elif cmd in ('help', '?'):
        say('commands: value <ch> <n> | rest <ch> | alarm <ch> on|off | ack accept|refuse | '
            f'selfcheck off|on | status | shutdown  (channels: {channel_names(opt)})')
    else:
        say(f"error: unknown command '{cmd}' (type help)")


# =============================================================================
# main
# =============================================================================


def pick_ip(requested: str | None) -> str:
    from sdc11073 import network  # noqa: PLC0415
    adapters = network.get_adapters()
    if requested:
        for adapter in adapters:
            if str(adapter.ip) == requested:
                return requested
        raise SystemExit(f'ERROR: unable to determine network interface for IP address {requested}')
    if not adapters:
        raise SystemExit('ERROR: no network adapter with an IPv4 address found')
    return str(adapters[0].ip)


def main(argv: list[str]) -> int:  # noqa: C901, PLR0912, PLR0915
    opt = parse_args(argv)
    say(f"TestbedProvider start: epr={opt.epr} mdib={opt.mdib} ack={'accept' if opt.ack_accept else 'refuse'}"
        f" seed={opt.seed if opt.seed is not None else 'time'}")

    # sdc11073 internal log to the console: warnings and errors only (TLS errors, notify failures)
    loghelper.basic_logging_setup(level=logging.WARNING)

    # --- network: local address / fixed port -----------------------------------------
    local_ip = pick_ip(opt.ip)
    out(f'[Provider] binding to {local_ip}:{opt.port if opt.port else "(OS-chosen port)"}')

    # --- TLS -----------------------------------------------------------------------
    if opt.no_tls:
        ssl_container = None
        out('WARNING: --no-tls given: plain HTTP without certificates (debugging only, the Dashboard expects mTLS)')
    else:
        if not opt.certs_dir.is_dir():
            raise SystemExit(f"ERROR: --certs-dir '{opt.certs_dir}' is not a folder")
        ssl_container = mk_ssl_contexts_from_folder(
            opt.certs_dir, private_key='userkey.pem', certificate='sdccert.pem', ca_public_key='cacert.pem')
        out(f'[Provider] mTLS: certificates from {opt.certs_dir} (userkey.pem, sdccert.pem, CA cacert.pem)')

    # --- MDIB --------------------------------------------------------------------------
    if not opt.mdib.is_file():
        raise SystemExit(f"ERROR: MDIB file '{opt.mdib}' could not be read")
    try:
        mdib = ProviderMdib.from_mdib_file(str(opt.mdib))
    except Exception as ex:  # noqa: BLE001
        raise SystemExit(f"ERROR: could not load Mdib from '{opt.mdib}': {ex!r}") from ex
    out('Successfully loaded Mdib!')

    # --- channels and context handles come from the MDIB itself --------------------------
    derive_from_mdib(mdib, opt)
    out(f'[Provider] MDIB {opt.mdib}: {len(opt.channels)} channel(s), contexts: patient={opt.ctx_patient}'
        f" location={opt.ctx_location if opt.ctx_location else '(none)'}"
        f' ensemble={opt.ctx_ensemble} workflow={opt.ctx_workflow}')
    for ch in opt.channels:
        out(f"  channel '{ch.name}': metric={ch.metric_handle} condition={ch.condition_handle}"
            f' signal={ch.signal_handle} initial={format_value(ch.initial_value, ch.decimals)}')
    for a in opt.alert_systems:
        out(f"  alert system '{a.handle}': SelfCheckPeriod="
            f"{format_value(a.period_sec, 1) + ' s' if a.period_sec > 0 else 'none (no self-check)'}")

    # --- check every configured handle against the MDIB model --------------------------
    ok = True
    for what, handle in ([(f"channel '{ch.name}' {kind}", h) for ch in opt.channels
                          for kind, h in (('metric', ch.metric_handle), ('condition', ch.condition_handle),
                                          ('signal', ch.signal_handle))]
                         + [('patient context descriptor', opt.ctx_patient),
                            ('workflow context descriptor', opt.ctx_workflow),
                            ('ensemble context descriptor', opt.ctx_ensemble)]
                         + ([('location context descriptor', opt.ctx_location)] if opt.room else [])):
        if mdib.descriptions.handle.get_one(handle, allow_none=True) is None:
            out(f"ERROR: {what} handle '{handle}' not found in {opt.mdib}")
            ok = False
    if not ok:
        return 3
    out('[Provider] all configured handles found in the MDIB')

    # --- shared runtime state, product with operation handlers ---------------------------
    state = SharedState(opt.channels, opt.ack_accept, opt.seed)
    TestbedProduct.shared = state

    this_model = ThisModelType(manufacturer=opt.manufacturer, model_name=opt.model, model_number='1')
    this_device = ThisDeviceType(friendly_name=opt.friendly_name, firmware_version='0.1.1',
                                 serial_number=opt.serial)

    wsd = WSDiscovery(local_ip)
    provider = SdcProvider(
        ws_discovery=wsd,
        this_model=this_model,
        this_device=this_device,
        device_mdib_container=mdib,
        epr=opt.epr,
        ssl_context_container=ssl_container,
        role_provider_components=RoleProviderComponents(role_provider_class=TestbedProduct),
    )
    provider.set_used_compression()  # no compression, like the reference providers

    http_server = FixedPortHttpServerThread(
        opt.port, local_ip,
        ssl_container.server_context if ssl_container else None,
        provider._compression_methods,  # noqa: SLF001
        logging.getLogger('sdc.device.httpsrv'),
    )

    # --- startup ----------------------------------------------------------------------
    wsd.start()
    http_server.start()
    if not http_server.started_evt.wait(timeout=10):
        out(f'ERROR: HTTP server could not bind {local_ip}:{opt.port} (port in use?)')
        wsd.stop()
        return 2
    provider.start_all(start_rtsample_loop=False, shared_http_server=http_server)
    say('Provider Started')
    out(f'[Provider] serving {provider.get_xaddrs()[0]}')

    # --- context states from options BEFORE the first Hello -----------------------------
    # Contexts are created before publish()
    # so that the very first Hello / ProbeMatch already carries the location scope.  With
    # "publish, then set the context, then re-publish" a consumer that probes in between
    # sees a device WITHOUT a location scope and takes the context-precheck path instead of
    # the discovery path (seen in the Ziel 1 pilot of 2026-10-15, trial 1).
    create_context_states(mdib, opt)
    try:
        if opt.scope_room:
            # TEST ONLY (consumer 2f): the discovery scope lies about the room, the
            # LocationContextState keeps --room.  Scopes are built as publish() would,
            # with the sdc.ctxt.loc entry replaced.  This is the ONLY Hello sent.
            scopes = mk_scopes(mdib)
            scopes.text = [s for s in scopes.text if not s.startswith(SdcLocation.scheme + ':')]
            scopes.text.append(SdcLocation(rm=opt.scope_room, bed=(opt.bed or None)).scope_string)
            provider._wsdiscovery.publish_service(
                provider.epr_urn, list(mdib.sdc_definitions.MedicalDeviceTypesFilter),
                scopes, provider.get_xaddrs())
            say(f'scopes (TEST, scope room {opt.scope_room!r} != context room {opt.room!r}): '
                + ', '.join(scopes.text))
        else:
            provider.publish()
            say('scopes: ' + ', '.join(mk_scopes(mdib).text))
    except Exception as ex:  # noqa: BLE001
        say(f'WARNING: publish with context scopes failed: {ex!r}')
        provider.publish()
    global _PROVIDER
    _PROVIDER = provider

    # --- self-check: LastSelfCheck at start, then refreshed at half of each SelfCheckPeriod ---
    with state.lock:
        for a in opt.alert_systems:
            if a.period_sec <= 0:
                continue
            try:
                touch_selfcheck_locked(mdib, a.handle)
                say(f'selfcheck {a.handle}: LastSelfCheck set (period {format_value(a.period_sec, 1)} s, '
                    f'refresh every {format_value(a.period_sec / 2.0, 1)} s)')
            except Exception as ex:  # noqa: BLE001
                say(f'WARNING: selfcheck {a.handle} failed: {ex!r}')

    def selfcheck_loop():
        last_touch = [now_ms()] * len(opt.alert_systems)
        last_failed = [False] * len(opt.alert_systems)
        while not state.stop:
            if state.selfcheck_enabled:
                now = now_ms()
                for i, a in enumerate(opt.alert_systems):
                    if a.period_sec <= 0 or now - last_touch[i] < a.period_sec * 500.0:
                        continue
                    with state.lock:
                        try:
                            touch_selfcheck_locked(mdib, a.handle)
                            failed = False
                        except Exception as ex:  # noqa: BLE001
                            failed = True
                            if not last_failed[i]:
                                say(f'WARNING: selfcheck {a.handle} failed: {ex!r}')
                    last_touch[i] = now
                    if not failed and last_failed[i]:
                        say(f'selfcheck {a.handle} working again')
                    last_failed[i] = failed
            time.sleep(0.2)

    selfcheck_thread = threading.Thread(target=selfcheck_loop, name='selfcheck', daemon=True)
    selfcheck_thread.start()

    # --- command interpreter on standard input (daemon: a blocking readline cannot be interrupted) ---
    def stdin_loop():
        while not state.stop:
            line = sys.stdin.readline()
            if not line:  # EOF: the thread just ends
                return
            try:
                handle_command(line, opt, state, mdib)
            except Exception as ex:  # noqa: BLE001
                say(f'error: command failed: {ex!r}')

    threading.Thread(target=stdin_loop, name='stdin', daemon=True).start()
    say(f"ready: type 'help' for commands (channels: {channel_names(opt)})")

    # Ctrl-C raises KeyboardInterrupt in the main thread (Python default); on Windows Ctrl-Break
    # (SIGBREAK) would kill the process silently, so it is mapped to the same graceful path.
    def _on_break(*_):
        raise KeyboardInterrupt

    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, _on_break)

    # --- main loop: rest / fixed values once per second, ensemble read-back ---------------
    last_ensemble_id = ''
    interrupted = False
    try:
        while not state.stop:
            with state.lock:
                ensemble_id = read_ensemble_id(mdib, opt.ctx_ensemble)
                if ensemble_id != last_ensemble_id:
                    if not ensemble_id:
                        say('<<< EnsembleContext cleared (no identifier)')
                    else:
                        say(f'<<< EnsembleContext set by consumer: uuid={ensemble_id} (descriptor {opt.ctx_ensemble})')
                    last_ensemble_id = ensemble_id

                line = 'publish:'
                for ch, rt in zip(opt.channels, state.runtime):
                    if rt.fixed:
                        v = rt.fixed_value
                    else:
                        deviation = (state.rng.random() * 2.0 - 1.0) * 0.02
                        v = ch.initial_value * (Decimal(1) + Decimal(repr(deviation)))
                    text = format_value(v, ch.decimals)
                    publish_metric_locked(mdib, ch, text)
                    rt.last_published = text
                    line += f" {ch.name}={text}{'(fixed)' if rt.fixed else ''}{'(ALARM)' if rt.alarm else ''}"
                say(line)
            time.sleep(1.0)
    except KeyboardInterrupt:
        interrupted = True

    # --- graceful shutdown ("shutdown" command, EOF on stdin or Ctrl-C) --------------------
    say('SIGINT received: shutting down' if interrupted else 'shutting down')
    state.stop = True
    selfcheck_thread.join(timeout=2)
    provider.stop_all()          # ends subscriptions, clear_service() -> WS-Discovery Bye
    http_server.stop()           # shared server is not stopped by stop_all()
    time.sleep(0.5)              # let the Bye datagrams leave
    wsd.stop()
    out('TestbedProvider stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
