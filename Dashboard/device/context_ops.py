"""
context_ops.py — SDC context operations (EnsembleContext, WorkflowContext/FHIR).

Module-level functions that take a DeviceHandler as their first argument.
Both follow the two-phase pattern:
  Phase 1 (data_lock):  read MDIB, build the proposed context state.
  Phase 2 (no lock):    send SetContextState over the network.

Called by SmartAlertAggregator.evaluate_and_bind_device() after a device
connects successfully.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .handler import DeviceHandler


# =============================================================================
# Ensemble context
# =============================================================================

def apply_ensemble_context(handler: 'DeviceHandler', ensemble_uuid: str) -> bool:
    """
    Build an EnsembleContextState with *ensemble_uuid* and send it to the
    SDC Provider via a SetContextState SOAP call.

    On success: sets handler.ensemble_uuid = ensemble_uuid.

    Returns:
      True  — EnsembleContext successfully applied on the provider.
      False — operation skipped (no descriptor, no connection, or error).
    """
    from sdc11073.xml_types import pm_qnames as _pm
    from sdc11073.xml_types import pm_types as _pm_types

    operation_handle: str | None = None
    proposed_ens = None

    # ------------------------------------------------------------------
    # Phase 1: build EnsembleContextState under data_lock
    # ------------------------------------------------------------------
    try:
        with handler.data_lock:
            if not handler.mdib or not handler.consumer:
                handler.logger.warning('apply_ensemble_context: MDIB or consumer not ready.')
                return False

            ens_descriptors = handler.mdib.descriptions.NODETYPE.get(
                _pm.EnsembleContextDescriptor, []
            )
            if not ens_descriptors:
                handler.logger.warning(
                    'apply_ensemble_context: no EnsembleContextDescriptor in MDIB -- '
                    'device does not support ensemble binding.'
                )
                return False
            descriptor = ens_descriptors[0]

            # Find the SetContextState operation targeting EnsembleContextDescriptor.
            # Using the wrong handle causes OperationNotAllowed on the provider.
            set_ctx_ops = handler.mdib.descriptions.NODETYPE.get(
                _pm.SetContextStateOperationDescriptor, []
            )
            for op in set_ctx_ops:
                if op.OperationTarget == descriptor.Handle:
                    operation_handle = op.Handle
                    break

            if not operation_handle:
                handler.logger.warning(
                    'apply_ensemble_context: no SetContextState operation '
                    'targeting EnsembleContextDescriptor found.'
                )
                return False

            # Update existing state or create a new proposed object
            existing_ens = handler.mdib.context_states.NODETYPE.get(
                _pm.EnsembleContextState, []
            )
            if existing_ens:
                proposed_ens = existing_ens[0].mk_copy()
                # Clear stale identifiers before adding the new UUID
                if getattr(proposed_ens, 'Identification', None) is not None:
                    proposed_ens.Identification.clear()
            else:
                proposed_ens = handler.consumer.context_service_client.mk_proposed_context_object(
                    descriptor.Handle
                )

            proposed_ens.ContextAssociation = _pm_types.ContextAssociation.ASSOCIATED

            # InstanceIdentifier carries:
            #   root      — fixed UUID identifying this Orchestrator system
            #   extension — session-specific ensemble UUID from the aggregator
            identifier = _pm_types.InstanceIdentifier(
                root='bce837e3-0c46-4e52-af32-15bb36cfd746',
                extension_string=ensemble_uuid,
            )
            identifier.IdentifierName = [_pm_types.LocalizedText(ensemble_uuid)]

            if getattr(proposed_ens, 'Identification', None) is None:
                proposed_ens.Identification = []
            proposed_ens.Identification.append(identifier)

    except Exception as exc:
        handler.logger.error(f'apply_ensemble_context: failed to build state -- {exc}')
        return False

    # ------------------------------------------------------------------
    # Phase 2: send SetContextState (no lock — network call)
    # ------------------------------------------------------------------
    try:
        if not handler.consumer.context_service_client:
            handler.logger.warning('apply_ensemble_context: context_service_client not available.')
            return False

        handler.logger.info(
            f'Sending EnsembleContext {ensemble_uuid[:8]}... '
            f'to provider (op={operation_handle}).'
        )
        handler.consumer.context_service_client.set_context_state(
            operation_handle=operation_handle,
            proposed_context_states=[proposed_ens],
        )
        handler.ensemble_uuid = ensemble_uuid
        handler.logger.info(
            f'EnsembleContext applied successfully. '
            f'Device {handler.epr[-12:]} bound to ensemble {ensemble_uuid[:8]}...'
        )
        return True

    except Exception as exc:
        handler.logger.error(f'apply_ensemble_context: SOAP call failed -- {exc}')
        return False


# =============================================================================
# FHIR / WorkflowContext
# =============================================================================

def apply_fhir_contexts(handler: 'DeviceHandler', fhir_data: Any) -> None:
    """
    Write FHIR DangerCodes into the device's WorkflowContextState via
    a SetContextState SOAP call.

    Also triggers a topology audit (audit_topology) after writing the
    FHIR context, logging any missing required sensors.
    """
    if fhir_data is None:
        handler.logger.warning(
            'apply_fhir_contexts: fhir_data is None '
            '(fetch failed or FHIR server unreachable) -- skipping.'
        )
        return

    from sdc11073.xml_types import pm_qnames as _pm
    from sdc11073.xml_types import pm_types as _pm_types

    raw_danger_codes = fhir_data.get_danger_codes()
    if not raw_danger_codes:
        handler.logger.info(
            f'apply_fhir_contexts: FHIR returned no DangerCodes for patient '
            f'{fhir_data.get_patient_id()!r} -- nothing to write.'
        )
        return

    operation_handle: str | None = None
    proposed_wf = None
    coded_danger_codes: list = []

    # ------------------------------------------------------------------
    # Phase 1: build WorkflowContextState under data_lock
    # ------------------------------------------------------------------
    try:
        with handler.data_lock:
            if not handler.mdib or not handler.consumer:
                handler.logger.warning('apply_fhir_contexts: MDIB or consumer not ready.')
                return

            wf_descriptors = handler.mdib.descriptions.NODETYPE.get(
                _pm.WorkflowContextDescriptor, []
            )
            if not wf_descriptors:
                handler.logger.warning(
                    'apply_fhir_contexts: no WorkflowContextDescriptor in MDIB -- '
                    'device does not support WorkflowContext.'
                )
                return
            wf_descriptor = wf_descriptors[0]

            # Prefer the SetContextState op targeting PatientContextDescriptor
            # (it has a registered handler on the provider side).
            set_ctx_ops = handler.mdib.descriptions.NODETYPE.get(
                _pm.SetContextStateOperationDescriptor, []
            )
            pat_descriptors = handler.mdib.descriptions.NODETYPE.get(
                _pm.PatientContextDescriptor, []
            )
            pat_handle = pat_descriptors[0].Handle if pat_descriptors else None
            for op in set_ctx_ops:
                if pat_handle and op.OperationTarget == pat_handle:
                    operation_handle = op.Handle
                    break
            if not operation_handle and set_ctx_ops:
                operation_handle = set_ctx_ops[0].Handle

            if not operation_handle:
                handler.logger.warning('apply_fhir_contexts: no SetContextState operation found in MDIB.')
                return

            wf_states = handler.mdib.context_states.NODETYPE.get(_pm.WorkflowContextState, [])
            if wf_states:
                proposed_wf = wf_states[0].mk_copy()
            else:
                proposed_wf = handler.consumer.context_service_client.mk_proposed_context_object(
                    wf_descriptor.Handle
                )

            proposed_wf.ContextAssociation = _pm_types.ContextAssociation.ASSOCIATED

            # Convert raw FHIR danger codes to BICEPS CodedValue objects
            for dc in raw_danger_codes:
                try:
                    coded_value = _pm_types.CodedValue(dc['code'])
                    if dc.get('system'):
                        coded_value.CodingSystem = dc['system']
                    if dc.get('display'):
                        coded_value.ConceptDescription = [_pm_types.LocalizedText(dc['display'])]
                    coded_danger_codes.append(coded_value)
                except Exception as cv_err:
                    handler.logger.warning(
                        f'apply_fhir_contexts: could not build CodedValue for {dc!r}: {cv_err}'
                    )

            if not coded_danger_codes:
                handler.logger.warning('apply_fhir_contexts: all DangerCode conversions failed.')
                return

            if proposed_wf.WorkflowDetail is None:
                handler.logger.warning(
                    'apply_fhir_contexts: WorkflowDetail is None -- cannot set DangerCode.'
                )
                return
            proposed_wf.WorkflowDetail.DangerCode = coded_danger_codes

    except Exception as exc:
        handler.logger.error(f'apply_fhir_contexts: failed to build state -- {exc}', exc_info=True)
        return

    # ------------------------------------------------------------------
    # Phase 2: send SetContextState (no lock — network call)
    # ------------------------------------------------------------------
    try:
        if not handler.consumer.context_service_client:
            handler.logger.warning('apply_fhir_contexts: context_service_client not available.')
            return

        handler.logger.info(
            f'apply_fhir_contexts: sending {len(coded_danger_codes)} DangerCode(s) '
            f'to WorkflowContext (op={operation_handle}).'
        )
        handler.consumer.context_service_client.set_context_state(
            operation_handle=operation_handle,
            proposed_context_states=[proposed_wf],
        )
        handler.logger.info(
            f'apply_fhir_contexts: WorkflowContext DangerCodes applied successfully '
            f'on device {handler.epr[-12:]}.'
        )
    except Exception as exc:
        handler.logger.error(f'apply_fhir_contexts: SOAP call failed -- {exc}')

    # ------------------------------------------------------------------
    # Topology audit: verify ensemble has all sensors for patient's focus
    # ------------------------------------------------------------------
    if handler.ensemble_uuid:
        _aggregator = getattr(handler.manager, 'aggregator', None)
        if _aggregator is not None:
            try:
                missing = _aggregator.audit_topology(handler.ensemble_uuid)
                if missing:
                    handler.logger.warning(
                        f'[TOPOLOGY AUDIT] Missing required sensors for patient\'s '
                        f'clinical focus: {missing} '
                        f'(ensemble={handler.ensemble_uuid[:8]}..., device={handler.epr[-12:]})'
                    )
                else:
                    handler.logger.info(
                        f'[TOPOLOGY AUDIT] All required sensors present for patient\'s '
                        f'clinical focus (ensemble={handler.ensemble_uuid[:8]}...).'
                    )
                _aggregator.log_clinical_focus_summary(handler.ensemble_uuid)
            except Exception as audit_err:
                handler.logger.warning(f'[TOPOLOGY AUDIT] audit_topology raised: {audit_err}')

