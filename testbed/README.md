# testbed — simulated SDC devices

- `providers/testbed_provider.py` — one configurable SDC provider script for every device
  (sdc11073 3.0.0); the device is defined by the MDIB file passed with `--mdib`. Flags and
  stdin commands: root README, section "Running a testbed provider".
- `mdib/` — the MDIB files of the four devices (`ecg`, `spo2`, `abp`, `vent`, files
  `*_mdib_v2.xml`), two demo variants for the UI figures and the design notes
  `mdib/README.md`; `mdib/template/` holds the structural template the files were derived from.

Used by `experiments/harness.py` (which starts providers as subprocesses), by
`tools/live_check.py` and by the two-PC bundle built with
`experiments/campaign_2pc/build_pc2_c.py`. Certificates come from `pki/<device>/`
(generated with `tools/make_testbed_pki.py`, not in git).
