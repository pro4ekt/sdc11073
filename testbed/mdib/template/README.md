# testbed/mdib/template — structural template

`correct_mdib.xml` is the MDIB that served as the structural template for the four testbed
MDIBs in `testbed/mdib/`: one MDS with alert system, SCO operations, system context and
clock, wrapped in `msg:GetMdibResponse`. It is kept for reference only; no provider loads it.
The analysis that derived the testbed rules from it is `docs/reports/MDIB_ANALYSIS.md`.
Validate like any other file: `python tools/validate_mdib.py testbed/mdib/template/correct_mdib.xml`.
