import os
from typing import Optional


class FaceDetector:
    """Face detection and embedding using insightface. Lazy-loads model."""

    def __init__(self):
        self.enabled = os.getenv("FACE_DETECTION_ENABLED", "true").lower() == "true"
        self.model = None

    def _ensure_model(self):
        """Lazy-load insightface model."""
        if not self.enabled:
            return
        if self.model is not None:
            return

        try:
            import insightface

            self.model = insightface.app.FaceAnalysis(
                name="buffalo_l", providers=["CPUExecutionProvider"]
            )
            self.model.prepare(ctx_id=-1, det_size=(640, 480))
        except ImportError:
            raise RuntimeError(
                "insightface not installed. Run: pip install insightface"
            )

    def detect_faces(self, frame) -> list[dict]:
        """Detect faces in frame. Returns list of {bbox, embedding}."""
        if not self.enabled:
            return []

        self._ensure_model()
        try:
            faces = self.model.get(frame)
            result = []
            for face in faces:
                result.append(
                    {
                        "bbox": face.bbox.tolist(),  # [x1, y1, x2, y2]
                        "embedding": face.embedding.tolist(),
                        "confidence": float(face.det_score),
                    }
                )
            return result
        except Exception as e:
            raise RuntimeError(f"Face detection failed: {e}")

    def detect_faces_from_b64(self, image_b64: str) -> list[dict]:
        """Detect faces from base64-encoded image."""
        import base64
        import cv2
        import numpy as np

        image_bytes = base64.b64decode(image_b64)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Failed to decode image")
        return self.detect_faces(frame)
