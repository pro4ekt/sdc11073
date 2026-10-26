#!/usr/bin/env python
"""validate_mdib.py -- validate a BICEPS MDIB XML file against the sdc11073 schemas.

Read-only tool. It never modifies the input file.

Entry point is BICEPS_MessageModel.xsd, which imports the participant and
extension schemas, so a file whose root element is <msg:GetMdibResponse>
(the format sdc11073 providers load and the Dashboard receives) is accepted.
A file whose root is <pm:Mdib>, <pm:MdDescription> or <pm:MdState> is also
accepted because those are global elements of the participant schema.

The XSD files import each other through absolute IEEE / W3C / OASIS URLs.
A local lxml resolver maps every such URL onto the file of the same name in
the schema folder, so no network access happens.

Usage:
    python tools/validate_mdib.py <mdib.xml> [<more.xml> ...]
    python tools/validate_mdib.py --xsd-dir <folder> <mdib.xml>

Exit code: 0 = all files valid, 1 = at least one violation, 2 = usage/setup error.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from lxml import etree

MSG_NS = 'http://standards.ieee.org/downloads/11073/11073-10207-2017/message'
PM_NS = 'http://standards.ieee.org/downloads/11073/11073-10207-2017/participant'
EXT_NS = 'http://standards.ieee.org/downloads/11073/11073-10207-2017/extension'


def default_xsd_dir() -> pathlib.Path | None:
    """Return the xsd folder shipped inside the installed sdc11073 package."""
    try:
        import sdc11073  # noqa: WPS433 (runtime import on purpose)
    except ImportError:
        return None
    return pathlib.Path(sdc11073.__file__).resolve().parent / 'xsd'


class LocalSchemaResolver(etree.Resolver):
    """Map absolute schemaLocation URLs onto local files by basename."""

    def __init__(self, xsd_dir: pathlib.Path) -> None:
        super().__init__()
        self._xsd_dir = xsd_dir
        self._by_name = {p.name: p for p in xsd_dir.glob('*.xsd')}

    def resolve(self, system_url: str, public_id, context):  # noqa: ANN001
        name = system_url.rsplit('/', 1)[-1]
        local = self._by_name.get(name)
        if local is None:
            candidate = pathlib.Path(system_url)
            if candidate.exists():
                return self.resolve_filename(str(candidate), context)
            sys.stderr.write(f'WARNING: cannot resolve schema location {system_url!r}\n')
            return None
        return self.resolve_filename(str(local), context)


def build_schema(xsd_dir: pathlib.Path) -> etree.XMLSchema:
    """Compile one schema that imports message, participant and extension model."""
    parser = etree.XMLParser(resolve_entities=True)
    parser.resolvers.add(LocalSchemaResolver(xsd_dir))
    # schemaLocation must be a URI: a bare Windows path (back-slashes, drive
    # letter) makes libxml2 skip the import silently and every root element
    # then fails with "No matching global declaration".
    ext_uri = (xsd_dir / 'ExtensionPoint.xsd').resolve().as_uri()
    pm_uri = (xsd_dir / 'BICEPS_ParticipantModel.xsd').resolve().as_uri()
    msg_uri = (xsd_dir / 'BICEPS_MessageModel.xsd').resolve().as_uri()
    wrapper = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified">'
        f'<xsd:import namespace="{EXT_NS}" schemaLocation="{ext_uri}"/>'
        f'<xsd:import namespace="{PM_NS}" schemaLocation="{pm_uri}"/>'
        f'<xsd:import namespace="{MSG_NS}" schemaLocation="{msg_uri}"/>'
        '</xsd:schema>'
    )
    tree = etree.fromstring(wrapper.encode('utf-8'), parser=parser)
    schema = etree.XMLSchema(etree=tree)
    # Sanity check: the message-model entry element must be known, otherwise
    # the imports did not load and every result would be a false negative.
    probe = etree.fromstring(f'<GetMdib xmlns="{MSG_NS}"/>'.encode('utf-8'))
    if not schema.validate(probe):
        msgs = '; '.join(e.message for e in schema.error_log)
        if 'No matching global declaration' in msgs:
            raise etree.XMLSchemaParseError(
                f'message-model schema did not load from {xsd_dir} ({msgs})'
            )
    return schema


def validate_file(path: pathlib.Path, schema: etree.XMLSchema) -> list[str]:
    """Return a list of human-readable violations (empty list = valid)."""
    try:
        doc = etree.parse(str(path))
    except (etree.XMLSyntaxError, OSError) as exc:
        return [f'{path}:0:0: not well-formed / unreadable: {exc}']
    root = doc.getroot()
    root_qn = etree.QName(root)
    violations: list[str] = []
    if root_qn.namespace not in (MSG_NS, PM_NS):
        violations.append(
            f'{path}:{root.sourceline}:0: root element {{{root_qn.namespace}}}{root_qn.localname} '
            f'is neither in the BICEPS message nor participant namespace'
        )
    if schema.validate(doc):
        return violations
    for err in schema.error_log:
        violations.append(f'{path}:{err.line}:{err.column}: [{err.type_name}] {err.message}')
    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+', type=pathlib.Path, help='MDIB XML file(s) to validate')
    ap.add_argument('--xsd-dir', type=pathlib.Path, default=None,
                    help='folder with BICEPS_*.xsd (default: the xsd folder of the installed sdc11073)')
    args = ap.parse_args(argv)

    xsd_dir = args.xsd_dir or default_xsd_dir()
    if xsd_dir is None or not (xsd_dir / 'BICEPS_MessageModel.xsd').exists():
        sys.stderr.write('ERROR: schema folder not found; pass --xsd-dir\n')
        return 2
    try:
        schema = build_schema(xsd_dir)
    except etree.XMLSchemaParseError as exc:
        sys.stderr.write(f'ERROR: cannot compile schemas from {xsd_dir}: {exc}\n')
        for err in exc.error_log:
            sys.stderr.write(f'  {err.filename}:{err.line}: {err.message}\n')
        return 2

    print(f'schema folder : {xsd_dir}')
    print(f'entry point   : BICEPS_MessageModel.xsd (+ participant, extension)')
    rc = 0
    for path in args.files:
        violations = validate_file(path, schema)
        root_desc = ''
        try:
            r = etree.parse(str(path)).getroot()
            root_desc = f' (root <{r.prefix}:{etree.QName(r).localname}>)'
        except Exception:  # noqa: BLE001 - reporting only
            pass
        if violations:
            rc = 1
            print(f'\n{path}{root_desc}: {len(violations)} violation(s)')
            for v in violations:
                print('  ' + v)
        else:
            print(f'\n{path}{root_desc}: VALID (0 violations)')
    return rc


if __name__ == '__main__':
    sys.exit(main())
