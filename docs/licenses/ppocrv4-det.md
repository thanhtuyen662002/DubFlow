# PP-OCRv4 mobile text detector (Chinese)

DubFlow uses this text-detection model to find burned-in captions so it can
blur them. It only detects where text is; it does not read it.

- Model: `ch_PP-OCRv4_det_infer.onnx`, PaddleOCR PP-OCRv4 mobile detection
  (DBNet), as converted to ONNX and published by the RapidOCR project
  (<https://huggingface.co/SWHL/RapidOCR>, file `PP-OCRv4/ch_PP-OCRv4_det_infer.onnx`).
- Original model: PaddlePaddle/PaddleOCR (<https://github.com/PaddlePaddle/PaddleOCR>).
- License: Apache License 2.0 (<https://www.apache.org/licenses/LICENSE-2.0>).
  Copyright (c) PaddlePaddle Authors. The ONNX file is redistributed unchanged.
