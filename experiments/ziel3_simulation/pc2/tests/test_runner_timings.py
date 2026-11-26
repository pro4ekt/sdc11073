"""Tests of the runner timings: start gap >= 3 s, state=done -> shutdown >= 20 s (also after Ctrl-C)."""
import inspect
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pc2_run  # noqa: E402
from provider_pool import ProviderPool  # noqa: E402


class RunnerTimingsTest(unittest.TestCase):
    def test_minimums(self):
        self.assertEqual(pc2_run.MIN_START_GAP_S, 3.0)
        self.assertEqual(pc2_run.MIN_OFF_TO_SHUTDOWN_S, 20.0)

    def test_values_below_the_minimum_are_raised(self):
        self.assertEqual(pc2_run.effective_timings(0.5, 5.0), (3.0, 20.0))
        self.assertEqual(pc2_run.effective_timings(1.5, 60.0), (3.0, 60.0))

    def test_values_above_the_minimum_are_kept(self):
        self.assertEqual(pc2_run.effective_timings(4.0, 90.0), (4.0, 90.0))

    def test_abort_waits_the_minimum_before_shutdown(self):
        self.assertEqual(pc2_run.effective_timings(3.0, 60.0, aborted=True), (3.0, 20.0))
        self.assertEqual(pc2_run.effective_timings(3.0, 5.0, aborted=True), (3.0, 20.0))

    def test_cli_defaults(self):
        src = inspect.getsource(pc2_run.main)
        self.assertIn("default=MIN_START_GAP_S", src)
        self.assertIn("'--off-to-shutdown', type=float, default=60.0", src)
        self.assertIn("'--p-cross', type=float, default=CrisisParams().p_cross", src)
        self.assertIn("CrisisParams(p_cross=a.p_cross, dur_min_s=a.crisis_min, dur_extra_mean_s=a.crisis_extra_mean)", src)
        self.assertIn("'p_cross': a.p_cross", src)
        self.assertEqual(inspect.signature(ProviderPool.__init__).parameters['start_gap_s'].default, 3.0)


if __name__ == '__main__':
    unittest.main()
