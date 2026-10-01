"""OCR observation and temporal text-track boundary."""

from .production import AppOwnedOcrBackend, ProductionOcrError, ProductionTextAnalysis, TextIntelligenceManifest
from .tracker import BenchmarkReport, BBox, OcrObservation, TextIntelligence, TextTrackDocument

__all__ = [
    "AppOwnedOcrBackend",
    "BenchmarkReport",
    "BBox",
    "OcrObservation",
    "ProductionOcrError",
    "ProductionTextAnalysis",
    "TextIntelligence",
    "TextIntelligenceManifest",
    "TextTrackDocument",
]
