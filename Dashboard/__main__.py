"""``python -m Dashboard`` -- start the SDC Consumer from the repository root.

The Dashboard package uses the bare imports ``app.*`` and ``device.*``, so the
``Dashboard/`` folder is put first on ``sys.path`` and ``main.py`` is executed
unchanged as ``__main__``.  Running ``python main.py`` from inside ``Dashboard/``
keeps working exactly as before.
"""
import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

runpy.run_path(os.path.join(_HERE, "main.py"), run_name="__main__")
