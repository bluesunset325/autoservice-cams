from types import SimpleNamespace

import numpy as np

from app.recognizer import FastAlprRecognizer, PlateRead


def result(text, conf, box=(1, 2, 3, 4)):
    return SimpleNamespace(
        detection=SimpleNamespace(bounding_box=SimpleNamespace(x1=box[0], y1=box[1], x2=box[2], y2=box[3])),
        ocr=SimpleNamespace(text=text, confidence=conf) if text is not None else None,
    )


class FakeAlpr:
    def __init__(self, results):
        self.results = results

    def predict(self, frame):
        return self.results


def test_reads_are_converted_and_filtered():
    alpr = FakeAlpr([
        result("A123BC77", 0.9),
        result("B456OP99", [0.8, 0.6]),   # посимвольная уверенность -> среднее 0.7
        result("XXX", 0.2),               # ниже порога
        result(None, 0.0),                # детектор нашёл, OCR — нет
        result("", 0.9),
    ])
    rec = FastAlprRecognizer(min_confidence=0.5, alpr=alpr)
    reads = rec.read(np.zeros((10, 10, 3), np.uint8))
    assert reads == [
        PlateRead("A123BC77", 0.9, (1, 2, 3, 4)),
        PlateRead("B456OP99", 0.7, (1, 2, 3, 4)),
    ]
