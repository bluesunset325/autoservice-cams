"""Распознавание номеров на кадре. Сменный адаптер: можно заменить на другой Recognizer."""
from dataclasses import dataclass
from typing import Protocol

import numpy as np

DEFAULT_DETECTOR = "yolo-v9-t-384-license-plate-end2end"
DEFAULT_OCR = "cct-xs-v1-global-model"


@dataclass(frozen=True)
class PlateRead:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]


class Recognizer(Protocol):
    def read(self, frame: np.ndarray) -> list[PlateRead]: ...


class FastAlprRecognizer:
    """fast-alpr: YOLO-детектор номера + OCR, ONNX на CPU. Модели скачиваются при первом запуске."""

    def __init__(self, detector_model: str = DEFAULT_DETECTOR, ocr_model: str = DEFAULT_OCR,
                 min_confidence: float = 0.5, alpr=None):
        if alpr is None:
            from fast_alpr import ALPR
            alpr = ALPR(detector_model=detector_model, ocr_model=ocr_model)
        self._alpr = alpr
        self._min_confidence = min_confidence

    def read(self, frame: np.ndarray) -> list[PlateRead]:
        reads = []
        for r in self._alpr.predict(frame):
            if r.ocr is None or not r.ocr.text:
                continue
            confidence = round(float(np.mean(r.ocr.confidence)), 3)
            if confidence < self._min_confidence:
                continue
            b = r.detection.bounding_box
            reads.append(PlateRead(r.ocr.text, confidence, (int(b.x1), int(b.y1), int(b.x2), int(b.y2))))
        return reads
