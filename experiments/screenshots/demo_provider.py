#!/usr/bin/env python
"""demo_provider.py -- testbed_provider.py plus a fictional patient name (screenshot demo only).

Accepts every flag of testbed/providers/testbed_provider.py and two more:
    --patient-given NAME     PatientContextState/CoreData/Givenname
    --patient-family NAME    PatientContextState/CoreData/Familyname

The name is written into the PatientContextState that testbed_provider.create_context_states()
builds from --patient-id, before the first Hello, so the Dashboard receives it with the MDIB
(qtDeviceHandler: CoreData.Givenname + Familyname -> patientName).  The patient id, the
WorkflowContextState and therefore the ensemble key (patient id, room) stay exactly as the
testbed provider sets them.  testbed_provider.py itself is not modified and the experiments of
chapters 4/5 do not use this file.
"""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'testbed' / 'providers'))
import testbed_provider as tp  # noqa: E402


def _take(argv: list[str], flag: str) -> tuple[list[str], str | None]:
    """Remove ``flag VALUE`` from argv; return the remaining argv and VALUE (None if absent)."""
    if flag not in argv:
        return argv, None
    i = argv.index(flag)
    if i + 1 >= len(argv):
        raise SystemExit(f'ERROR: {flag} needs a value')
    return argv[:i] + argv[i + 2:], argv[i + 1]


def main(argv: list[str]) -> int:
    argv, given = _take(argv, '--patient-given')
    argv, family = _take(argv, '--patient-family')
    original = tp.create_context_states

    def create_context_states_with_name(mdib, opt):
        original(mdib, opt)
        if not (given or family):
            return
        from sdc11073.xml_types import pm_types  # noqa: PLC0415
        with mdib.context_state_transaction() as mgr:
            patient = mgr.get_context_state(f'{opt.ctx_patient}_state')
            patient.CoreData = pm_types.PatientDemographicsCoreData(given_name=given, family_name=family)
        tp.out(f'[Provider] PatientContextState CoreData set: Givenname={given!r} Familyname={family!r}')

    tp.create_context_states = create_context_states_with_name
    return tp.main(argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
