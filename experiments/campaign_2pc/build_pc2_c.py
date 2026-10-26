"""build_pc2_c.py -- assemble experiments/campaign_2pc/pc2_c/ : everything PC 2 needs, repo structure kept.

    <repo>/experiments/campaign_2pc/pc2_c/
        experiments/harness.py
        experiments/campaign_2pc/{pc2_scheduler.py, schedule_c.json, SPEC_C.md, HANDOFF_PC2.md}
        testbed/providers/testbed_provider.py
        testbed/mdib/<the four *_v2.xml of harness.DEVICES>
        pki/<ecg|spo2|abp|vent>/{userkey.pem, sdccert.pem, cacert.pem}     (copied, never printed)
        .gitignore  (pki/ and logs/ are ignored so the keys never end up in a commit)
Re-run after every change of the sources; existing files are overwritten, logs/ is kept.
"""
from __future__ import annotations

import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from harness import DEVICES  # noqa: E402

DEST = HERE / 'pc2_c'
FILES = [
    ('experiments/harness.py', 'experiments/harness.py'),
    ('experiments/campaign_2pc/pc2_scheduler.py', 'experiments/campaign_2pc/pc2_scheduler.py'),
    ('experiments/campaign_2pc/schedule_c.json', 'experiments/campaign_2pc/schedule_c.json'),
    ('experiments/campaign_2pc/SPEC_C.md', 'experiments/campaign_2pc/SPEC_C.md'),
    ('experiments/campaign_2pc/HANDOFF_PC2.md', 'experiments/campaign_2pc/HANDOFF_PC2.md'),
    ('testbed/providers/testbed_provider.py', 'testbed/providers/testbed_provider.py'),
]
for _dev, (pki, mdib, _p, _m, _c) in DEVICES.items():
    FILES.append((f'testbed/mdib/{mdib}', f'testbed/mdib/{mdib}'))
    for f in ('userkey.pem', 'sdccert.pem', 'cacert.pem'):
        FILES.append((f'pki/{pki}/{f}', f'pki/{pki}/{f}'))


def main() -> int:
    missing = [s for s, _ in FILES if not (REPO / s).exists()]
    if missing:
        print('missing sources:', *missing, sep='\n  ')
        return 1
    for src, dst in FILES:
        d = DEST / dst
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / src, d)
    (DEST / '.gitignore').write_text('pki/\nlogs/\n__pycache__/\n', encoding='utf-8')
    (DEST / 'README.txt').write_text(
        'PC 2 bundle (variant C). Start here: experiments\\campaign_2pc\\HANDOFF_PC2.md\n'
        'Run from THIS folder:  python experiments\\campaign_2pc\\pc2_scheduler.py --selftest\n', encoding='utf-8')
    n_pki = sum(1 for s, _ in FILES if s.startswith('pki/'))
    print(f'pc2_c assembled at {DEST}: {len(FILES)} files ({n_pki} PKI files copied, not shown)')
    for p in sorted(DEST.rglob('*')):
        if p.is_file() and 'pki' not in p.parts:
            print(f'  {p.relative_to(DEST)}  {p.stat().st_size} B')
    print('  pki/<ecg|spo2|abp|vent>/{userkey.pem, sdccert.pem, cacert.pem}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
