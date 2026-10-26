#!/usr/bin/env python
"""check_mdib_rules.py -- cross-file rule checker for the testbed provider MDIBs.

Read-only. Checks ALL given files together and prints every failure as
``file:line: message``. Exit code 0 = no failure, 1 = at least one failure,
2 = usage error.

Rules (from the testbed brief, MDIB_ANALYSIS.md Part B/C):
  R1  handle uniqueness across all files (descriptor handles and context-state handles)
  R2  every descriptor with a single-state type has exactly one state;
      context descriptors (multi-state) may have 0..n states (reported, not failed)
  R3  every State/@DescriptorHandle, Operation/@OperationTarget,
      AlertSignal/@ConditionSignaled and AlertCondition/pm:Source resolves to a
      descriptor of the same file
  R4  every AlertConditionDescriptor has >= 1 Source and each Source is a
      NumericMetricDescriptor
  R5  every SetAlertStateOperationDescriptor targets an AlertSignalDescriptor
  R6  every element carrying a Code attribute (CodedValue, Translation) also
      carries a non-empty CodingSystem
  R7  every code whose CodingSystem is LOINC passes the LOINC mod-10 check digit
  R8  every AlertSystemDescriptor has a SelfCheckPeriod in PT[nH][nM][nS] form
  R9  no LimitAlertConditionDescriptor / LimitAlertConditionState anywhere
Additional sanity checks (same spirit, cheap):
  S1  root is msg:GetMdibResponse with a SequenceId that has a URI scheme
  S2  every state has an xsi:type that matches its descriptor's state type
  S3  every ClockState has RemoteSync; every operation state has OperatingMode="En"
  S4  every SetContextStateOperationDescriptor targets a context descriptor
  S5  every xsd:duration attribute anywhere is in PT form (sdc11073 parser limit)

Usage:
    python tools/check_mdib_rules.py testbed/mdib/*.xml
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import defaultdict

from lxml import etree

PM = 'http://standards.ieee.org/downloads/11073/11073-10207-2017/participant'
MSG = 'http://standards.ieee.org/downloads/11073/11073-10207-2017/message'
XSI = 'http://www.w3.org/2001/XMLSchema-instance'

LOINC_SYSTEMS = {'http://loinc.org', 'urn:oid:2.16.840.1.113883.6.1'}
CONTEXT_DESCRIPTOR_TYPES = {
    'PatientContextDescriptor', 'LocationContextDescriptor', 'EnsembleContextDescriptor',
    'WorkflowContextDescriptor', 'OperatorContextDescriptor', 'MeansContextDescriptor',
}
# descriptor elements that are identified by their tag, not by xsi:type
TAG_TO_TYPE = {
    'Mds': 'MdsDescriptor', 'Vmd': 'VmdDescriptor', 'Channel': 'ChannelDescriptor',
    'Clock': 'ClockDescriptor', 'Battery': 'BatteryDescriptor', 'Sco': 'ScoDescriptor',
    'SystemContext': 'SystemContextDescriptor', 'AlertSystem': 'AlertSystemDescriptor',
    'AlertCondition': 'AlertConditionDescriptor', 'AlertSignal': 'AlertSignalDescriptor',
    'PatientContext': 'PatientContextDescriptor', 'LocationContext': 'LocationContextDescriptor',
    'EnsembleContext': 'EnsembleContextDescriptor', 'WorkflowContext': 'WorkflowContextDescriptor',
    'OperatorContext': 'OperatorContextDescriptor', 'MeansContext': 'MeansContextDescriptor',
}
DURATION_ATTRS = {
    'SelfCheckPeriod', 'DefaultConditionGenerationDelay', 'DefaultSignalGenerationDelay',
    'MinSignalGenerationDelay', 'MaxSignalGenerationDelay', 'AcknowledgeTimeout',
    'MaxMeasurementTime', 'MaxDelayTime', 'DeterminationPeriod', 'LifeTimePeriod',
    'ActivationDuration', 'AveragingPeriod', 'ActiveAveragingPeriod', 'ActiveDeterminationPeriod',
    'MaxTimeToFinish', 'InvocationEffectiveTimeout', 'ActualConditionGenerationDelay',
    'ActualSignalGenerationDelay', 'Resolution_duration',
}
# same constraint as sdc11073/xml_types/isoduration.py (PT form only)
PT_DURATION = re.compile(r'^PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)(?:\.\d+)?S)?(?<!PT)$')
URI_SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.\-]*:')


def loinc_check_digit_ok(code: str) -> bool:
    """LOINC Mod-10 check digit (Regenstrief algorithm)."""
    m = re.fullmatch(r'(\d+)-(\d)', code)
    if not m:
        return False
    num, chk = m.group(1), int(m.group(2))
    rev = num[::-1]
    odd = ''.join(rev[0::2])[::-1]    # positions 1,3,5.. from the right
    even = ''.join(rev[1::2])[::-1]   # positions 2,4,6.. from the right
    total = sum(int(c) for c in even + str(int(odd) * 2))
    return (10 - total % 10) % 10 == chk


def local(el_or_tag) -> str:
    tag = el_or_tag if isinstance(el_or_tag, str) else el_or_tag.tag
    return etree.QName(tag).localname


def xsi_type(el) -> str | None:
    t = el.get(f'{{{XSI}}}type')
    return t.split(':')[-1] if t else None


def descriptor_type(el) -> str | None:
    return xsi_type(el) or TAG_TO_TYPE.get(local(el))


class Checker:
    def __init__(self) -> None:
        self.failures: list[tuple[str, int, str]] = []
        self.notes: list[str] = []
        # global handle registry: handle -> [(file, line)]
        self.handles: dict[str, list[tuple[str, int]]] = defaultdict(list)

    def fail(self, path: pathlib.Path, line: int | None, msg: str) -> None:
        self.failures.append((str(path), line or 0, msg))

    # ------------------------------------------------------------------
    def check_file(self, path: pathlib.Path) -> None:
        try:
            doc = etree.parse(str(path))
        except (etree.XMLSyntaxError, OSError) as exc:
            self.fail(path, 0, f'cannot parse: {exc}')
            return
        root = doc.getroot()

        # S1 root / SequenceId
        if root.tag != f'{{{MSG}}}GetMdibResponse':
            self.fail(path, root.sourceline, f'S1 root is <{root.tag}>, expected msg:GetMdibResponse')
        for el in (root, root.find(f'{{{MSG}}}Mdib')):
            if el is None:
                self.fail(path, root.sourceline, 'S1 no msg:Mdib child')
                continue
            sid = el.get('SequenceId')
            if not sid or not URI_SCHEME.match(sid):
                self.fail(path, el.sourceline, f'S1 <{local(el)}> SequenceId={sid!r} is not a URI with a scheme')

        # ---- descriptors -------------------------------------------------
        descriptors: dict[str, tuple[etree._Element, str]] = {}
        md_desc = root.find(f'.//{{{PM}}}MdDescription')
        if md_desc is None:
            self.fail(path, root.sourceline, 'no pm:MdDescription')
            return
        for el in md_desc.iter():
            if el.get('Handle') is None or not isinstance(el.tag, str):
                continue
            h = el.get('Handle')
            dtype = descriptor_type(el)
            if dtype is None:
                self.fail(path, el.sourceline, f'descriptor {h!r}: cannot determine type (tag {local(el)}, no xsi:type)')
                continue
            if h in descriptors:
                self.fail(path, el.sourceline, f'R1 duplicate descriptor handle {h!r} inside the file')
            descriptors[h] = (el, dtype)
            self.handles[h].append((str(path), el.sourceline))

        # ---- states ------------------------------------------------------
        md_state = root.find(f'.//{{{PM}}}MdState')
        states_by_descr: dict[str, list[etree._Element]] = defaultdict(list)
        if md_state is None:
            self.fail(path, root.sourceline, 'no pm:MdState')
        else:
            for st in md_state.findall(f'{{{PM}}}State'):
                dh = st.get('DescriptorHandle')
                stype = xsi_type(st)
                if stype is None:
                    self.fail(path, st.sourceline, f'S2 state for {dh!r} has no xsi:type')
                if dh not in descriptors:
                    self.fail(path, st.sourceline, f'R3 State/@DescriptorHandle {dh!r} does not resolve')
                    continue
                states_by_descr[dh].append(st)
                d_el, dtype = descriptors[dh]
                expected = dtype.replace('Descriptor', 'State')
                if stype and stype != expected:
                    self.fail(path, st.sourceline, f'S2 state type {stype} for {dh!r} does not match descriptor type {dtype} (expected {expected})')
                if stype == 'LimitAlertConditionState':
                    self.fail(path, st.sourceline, 'R9 LimitAlertConditionState is forbidden')
                if dtype in CONTEXT_DESCRIPTOR_TYPES:
                    mh = st.get('Handle')
                    if not mh:
                        self.fail(path, st.sourceline, f'context state for {dh!r} has no Handle')
                    else:
                        self.handles[mh].append((str(path), st.sourceline))
                if stype == 'ClockState' and st.get('RemoteSync') is None:
                    self.fail(path, st.sourceline, f'S3 ClockState {dh!r} lacks required RemoteSync')
                if stype and stype.endswith('OperationState') and st.get('OperatingMode') != 'En':
                    self.fail(path, st.sourceline, f'S3 operation state {dh!r} OperatingMode={st.get("OperatingMode")!r}, expected "En"')

        # R2 exactly one state per single-state descriptor
        for h, (el, dtype) in descriptors.items():
            n = len(states_by_descr.get(h, []))
            if dtype in CONTEXT_DESCRIPTOR_TYPES:
                self.notes.append(f'{path.name}: context descriptor {h!r} ({dtype}) has {n} state(s) in file')
                continue
            if n != 1:
                self.fail(path, el.sourceline, f'R2 descriptor {h!r} ({dtype}) has {n} state(s), expected exactly 1')

        # R3/R4/R5/R8/R9/S4 descriptor-level checks
        for h, (el, dtype) in descriptors.items():
            if dtype == 'LimitAlertConditionDescriptor':
                self.fail(path, el.sourceline, f'R9 {h!r} is a LimitAlertConditionDescriptor (forbidden)')
            if dtype == 'AlertConditionDescriptor':
                sources = [s for s in el.findall(f'{{{PM}}}Source')]
                if not sources:
                    self.fail(path, el.sourceline, f'R4 alert condition {h!r} has no Source')
                for s in sources:
                    ref = (s.text or '').strip()
                    if ref not in descriptors:
                        self.fail(path, s.sourceline, f'R3 Source {ref!r} of {h!r} does not resolve')
                    elif descriptors[ref][1] != 'NumericMetricDescriptor':
                        self.fail(path, s.sourceline, f'R4 Source {ref!r} of {h!r} is a {descriptors[ref][1]}, not NumericMetricDescriptor')
            if dtype == 'AlertSignalDescriptor':
                cs = el.get('ConditionSignaled')
                if cs is None:
                    self.fail(path, el.sourceline, f'R3 alert signal {h!r} has no ConditionSignaled')
                elif cs not in descriptors:
                    self.fail(path, el.sourceline, f'R3 ConditionSignaled {cs!r} of {h!r} does not resolve')
                elif descriptors[cs][1] != 'AlertConditionDescriptor':
                    self.fail(path, el.sourceline, f'R3 ConditionSignaled {cs!r} of {h!r} is a {descriptors[cs][1]}')
            if dtype.endswith('OperationDescriptor'):
                tgt = el.get('OperationTarget')
                if tgt not in descriptors:
                    self.fail(path, el.sourceline, f'R3 OperationTarget {tgt!r} of {h!r} does not resolve')
                else:
                    ttype = descriptors[tgt][1]
                    if dtype == 'SetAlertStateOperationDescriptor' and ttype != 'AlertSignalDescriptor':
                        self.fail(path, el.sourceline, f'R5 SetAlertState op {h!r} targets {tgt!r} ({ttype}), expected AlertSignalDescriptor')
                    if dtype == 'SetContextStateOperationDescriptor' and ttype not in CONTEXT_DESCRIPTOR_TYPES:
                        self.fail(path, el.sourceline, f'S4 SetContextState op {h!r} targets {tgt!r} ({ttype}), expected a context descriptor')
            if dtype == 'AlertSystemDescriptor':
                scp = el.get('SelfCheckPeriod')
                if scp is None:
                    self.fail(path, el.sourceline, f'R8 alert system {h!r} has no SelfCheckPeriod')
                elif not PT_DURATION.match(scp):
                    self.fail(path, el.sourceline, f'R8 SelfCheckPeriod {scp!r} of {h!r} is not in PT[nH][nM][nS] form')

        # R6/R7 coded values, S5 durations (whole document)
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            code = el.get('Code')
            if code is not None:
                cs = el.get('CodingSystem')
                if not cs:
                    self.fail(path, el.sourceline, f'R6 <{local(el)} Code={code!r}> has no CodingSystem')
                elif cs in LOINC_SYSTEMS and not loinc_check_digit_ok(code):
                    self.fail(path, el.sourceline, f'R7 LOINC code {code!r} fails the mod-10 check digit')
            for attr, val in el.attrib.items():
                if local(attr) in DURATION_ATTRS and not PT_DURATION.match(val):
                    self.fail(path, el.sourceline, f'S5 duration {local(attr)}={val!r} is not in PT form')

    # ------------------------------------------------------------------
    def check_global(self) -> None:
        for h, locs in self.handles.items():
            if len(locs) > 1:
                where = ', '.join(f'{pathlib.Path(f).name}:{ln}' for f, ln in locs)
                self.failures.append((locs[0][0], locs[0][1], f'R1 handle {h!r} is not unique across files: {where}'))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+', type=pathlib.Path)
    ap.add_argument('--quiet-notes', action='store_true', help='suppress informational notes')
    args = ap.parse_args(argv)

    files = [p for p in args.files if p.exists()]
    missing = [p for p in args.files if not p.exists()]
    for p in missing:
        print(f'ERROR: {p} does not exist', file=sys.stderr)
    if not files:
        return 2

    chk = Checker()
    for p in files:
        chk.check_file(p)
    chk.check_global()

    if not args.quiet_notes:
        for n in chk.notes:
            print(f'note  {n}')
    for f, ln, msg in sorted(chk.failures):
        print(f'FAIL  {f}:{ln}: {msg}')
    n_handles = len(chk.handles)
    print(f'\nchecked {len(files)} file(s), {n_handles} distinct handle(s), {len(chk.failures)} failure(s)')
    return 1 if chk.failures or missing else 0


if __name__ == '__main__':
    sys.exit(main())
