from __future__ import annotations

import math
import unittest

from engine.dubflow.qc.provenance import (
    CalibrationProfile,
    Finding,
    QCError,
    QCReport,
    QCStatus,
    ThresholdSet,
    calibrate_confidence,
)


H = "a" * 64
H2 = "b" * 64


def report() -> QCReport:
    thresholds = ThresholdSet("cal-v1", "0.50", "0.75", "0.95")
    calibration = CalibrationProfile("cal-v1", "model", "1", "dataset-1", "confidence", thresholds)
    return QCReport(
        "qc-job-1",
        "producer-1",
        (H,),
        H2,
        "model",
        "1",
        calibration,
        thresholds,
        (Finding("cue-1", "cue-1", "SYNC_OK", "INFO", "0.90", "aligned"),),
    )


class QCProvenanceTests(unittest.TestCase):
    def test_exact_provenance_keeps_report_current(self) -> None:
        value = report()
        self.assertEqual(value.status, QCStatus.PASS)
        self.assertEqual(value.current_status(upstream_hashes=(H,), config_hash=H2, model_id="model", model_version="1", calibration_id="cal-v1", threshold_set_hash=value.thresholds.hash), QCStatus.PASS)
        self.assertEqual(value.current_status(upstream_hashes=(H,), config_hash=H, model_id="model", model_version="1", calibration_id="cal-v1", threshold_set_hash=value.thresholds.hash), QCStatus.STALE)
        self.assertEqual(value.to_dict()["provenance"]["contract_version"], "qc-v1")

    def test_low_confidence_is_warning_data_and_fail_is_explicit(self) -> None:
        thresholds = ThresholdSet("cal-v1", "0.50", "0.75", "0.95")
        calibration = CalibrationProfile("cal-v1", "model", "1", "dataset-1", "confidence", thresholds)
        warning = QCReport("qc-warning", "p", (H,), H2, "model", "1", calibration, thresholds, (Finding("cue", "cue", "LOW_CONFIDENCE", "WARN", "0.20", "review"),))
        self.assertEqual(warning.status, QCStatus.WARN)
        failing = QCReport("qc-fail", "p", (H,), H2, "model", "1", calibration, thresholds, (Finding("cue", "cue", "BAD_SYNC", "FAIL", "0.99", "invalid"),))
        self.assertEqual(failing.status, QCStatus.FAIL)

    def test_decimal_calibration_has_stable_boundary_counts(self) -> None:
        bins = calibrate_confidence(["0", "0.49", "0.50", "0.99", "1"], bins=2)
        self.assertEqual([item.count for item in bins], [2, 3])
        self.assertEqual(bins[-1].mean_confidence, "0.83")
        with self.assertRaises(QCError):
            calibrate_confidence([math.nan])

    def test_threshold_order_and_model_mismatch_are_rejected(self) -> None:
        with self.assertRaises(QCError):
            ThresholdSet("cal-v1", "0.8", "0.5", "0.9")
        thresholds = ThresholdSet("cal-v1", "0.5", "0.75", "0.95")
        calibration = CalibrationProfile("cal-v1", "other-model", "1", "dataset-1", "confidence", thresholds)
        with self.assertRaises(QCError):
            QCReport("bad", "p", (H,), H2, "model", "1", calibration, thresholds, ())


if __name__ == "__main__":
    unittest.main()
