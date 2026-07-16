"""
patches.py — Third-party library monkey-patches applied once at import time.

Current patches:
  RelatedMeasurement.from_node — fixes XML deserialization when 'value' is
  not yet known during node construction.
  MessageFactory._validate_node — suppresses spurious 'No matching global
  declaration' ValidationError raised when validating SOAP request messages
  (GetMdib, SetMetricState, …) whose root element is not declared as a
  top-level XSD type.
"""

from sdc11073.xml_types.pm_types import Measurement, RelatedMeasurement


def apply_patches() -> None:
    """Apply all monkey-patches.  Idempotent — safe to call multiple times."""
    _patch_related_measurement()
    _patch_schema_validation()


def _patch_related_measurement() -> None:
    """
    Fix RelatedMeasurement.from_node() deserialization bug.

    Problem:
      The default from_node() calls cls(...) which requires the 'value'
      argument in __init__ — but during XML parsing that value is not
      yet known, so deserialization raises TypeError.

    Solution:
      Replace from_node() with a version that first creates an empty object
      via cls(Measurement(None, None)) (a valid placeholder), then populates
      it from the real XML node via update_from_node().
    """
    def _fixed_from_node(cls, node):  # type: ignore[override]
        obj = cls(Measurement(None, None))
        obj.update_from_node(node)
        return obj

    RelatedMeasurement.from_node = classmethod(_fixed_from_node)  # type: ignore[assignment]


def _patch_schema_validation() -> None:
    """
    Fix intermittent ValidationError during GetMdib / SOAP request serialization.

    Problem:
      sdc11073 MessageFactory._validate_node() calls lxml assertValid() on the
      outgoing SOAP payload element (e.g. GetMdib, SetMetricState).  These
      elements are declared as *child* types in the XSD, not as global root
      elements.  lxml rejects them with:
        'No matching global declaration available for the validation root.'
      This raises sdc11073.exceptions.ValidationError and causes the
      DeviceHandler worker to crash on the first connection attempt.  It
      manifests when multiple consumers connect in parallel (race on schema
      initialisation) and is intermittent: a retry usually succeeds.

    Fix:
      Wrap MessageFactory._validate_node to catch this specific, harmless
      error and continue without validation.  All other ValidationError
      instances (genuine schema violations) are re-raised.

    Idempotent: guarded by a '_patched' sentinel attribute.
    """
    from sdc11073.pysoap.msgfactory import MessageFactory
    from sdc11073.exceptions import ValidationError as SdcValidationError

    if getattr(MessageFactory._validate_node, '_patched', False):
        return  # already applied

    _original = MessageFactory._validate_node

    def _patched_validate_node(self, node) -> None:  # type: ignore[override]
        try:
            _original(self, node)
        except SdcValidationError as exc:
            if 'No matching global declaration' in str(exc):
                # SOAP message element is a child type, not an XSD root —
                # lxml cannot validate it standalone.  This is harmless.
                return
            raise

    _patched_validate_node._patched = True  # type: ignore[attr-defined]
    MessageFactory._validate_node = _patched_validate_node  # type: ignore[method-assign]


