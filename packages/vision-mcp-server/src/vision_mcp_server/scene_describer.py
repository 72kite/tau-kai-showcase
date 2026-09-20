import os


class SceneDescriber:
    """Scene understanding using a lightweight model. Currently a stub."""

    def __init__(self):
        self.enabled = os.getenv("SCENE_DESCRIPTION_ENABLED", "true").lower() == "true"
        self.model = None

    def _ensure_model(self):
        """Lazy-load scene understanding model (stub for now)."""
        if not self.enabled:
            return
        if self.model is not None:
            return

        # TODO: Integrate a lightweight model like:
        # - timm (image classification)
        # - CLIP (vision-language)
        # - YOLOv5/v8 (object detection)
        # For now, this is a placeholder that detects basic properties.

    def describe_scene(self, frame) -> str:
        """Describe the scene in a frame. Returns a text description."""
        import cv2
        import numpy as np

        if not self.enabled:
            return "Scene description disabled"

        self._ensure_model()

        # Stub implementation: analyze basic image properties
        h, w = frame.shape[:2]
        brightness = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()
        is_bright = "well-lit" if brightness > 127 else "dimly-lit"

        # Simple object detection stub (detect edges/motion)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        edge_density = edges.sum() / (h * w)
        detail_level = (
            "high detail" if edge_density > 50 else "simple scene"
        )

        # Detect dominant colors (R, G, B channels)
        b, g, r = cv2.split(frame)
        dominant = np.argmax([r.mean(), g.mean(), b.mean()])
        color_map = {0: "reddish", 1: "greenish", 2: "bluish"}
        dominant_color = color_map.get(dominant, "neutral")

        description = f"{is_bright}, {detail_level}, {dominant_color} tones. {h}x{w} resolution."
        return description

    def describe_scene_from_b64(self, image_b64: str) -> str:
        """Describe scene from base64-encoded image."""
        import base64
        import cv2
        import numpy as np

        image_bytes = base64.b64decode(image_b64)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Failed to decode image")
        return self.describe_scene(frame)
