"""Voice embedding computation via speechbrain speaker verification model."""

import io
import base64
import numpy as np


class VoiceEmbedder:
    """Compute voice embeddings from audio using speechbrain (speaker verification)."""

    def __init__(self):
        self.enabled = True
        self.model = None

    def _ensure_model(self):
        """Lazy-load speechbrain speaker embedding model."""
        if self.model is not None:
            return

        try:
            # Modern import path (speechbrain >= 1.0). The legacy `speechbrain.pretrained`
            # redirect must NOT be used: its lazy-import machinery pulls in optional
            # integrations like k2 (not installable on Windows), and the failure cascades
            # into a recursive inspect loop that HANGS the MCP stdio server instead of
            # erroring (observed live 2026-07-12).
            from speechbrain.inference.speaker import SpeakerRecognition
            from speechbrain.utils.fetching import LocalStrategy
        except ImportError:
            raise RuntimeError(
                "speechbrain not installed. Run: pip install speechbrain torchaudio"
            )

        import os
        from pathlib import Path

        # Portable cache location (the previous hardcoded /tmp is a Unix-ism); override with
        # SPKREC_MODEL_DIR for containers/k3s.
        savedir = os.environ.get(
            "SPKREC_MODEL_DIR",
            str(Path.home() / ".cache" / "tau" / "spkrec-ecapa-voxceleb"),
        )
        # Device is env-configurable so the same image runs CPU-only or on a passed-through
        # GPU (SPKREC_DEVICE=cuda) - see docker-compose.gpu.yml. Defaults to cpu so a host
        # without CUDA/torch-cuda wheels never fails at model load.
        device = os.environ.get("SPKREC_DEVICE", "cpu")
        self.model = SpeakerRecognition.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=savedir,
            run_opts={"device": device},
            # COPY, not the default SYMLINK: creating symlinks on Windows requires elevation
            # or Developer Mode (WinError 1314 otherwise). A few duplicated model files on
            # disk beat an admin requirement for a home deployment.
            local_strategy=LocalStrategy.COPY,
        )

    @staticmethod
    def _decode_to_16k_mono(audio_bytes: bytes):
        """Decode any audio container (WAV, webm/opus from browser MediaRecorder, mp4/aac)
        to a 16kHz mono float32 torch tensor via PyAV - the same decode stack the local
        faster-whisper service uses, chosen over torchaudio whose 2.13+ load() requires the
        separately-installed torchcodec plugin and can't decode webm without it anyway.
        """
        import av
        import torch

        container = av.open(io.BytesIO(audio_bytes))
        resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)
        chunks = []
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray())
        for resampled in resampler.resample(None):  # flush
            chunks.append(resampled.to_ndarray())
        if not chunks:
            raise ValueError("no audio frames decoded")
        pcm = np.concatenate(chunks, axis=1).astype(np.float32) / 32768.0
        return torch.from_numpy(pcm)  # shape (1, samples)

    def compute_embedding(self, audio_bytes: bytes) -> list[float]:
        """Compute voice embedding from audio bytes (any common container/codec).

        Returns a 192-dimensional embedding vector.
        """
        self._ensure_model()

        try:
            audio_tensor = self._decode_to_16k_mono(audio_bytes)
            embedding = self.model.encode_batch(audio_tensor)
            return embedding.squeeze().cpu().detach().numpy().tolist()
        except ImportError:
            raise RuntimeError("av not installed. Run: pip install av")
        except Exception as e:
            raise RuntimeError(f"Voice embedding failed: {e}")

    def compute_embedding_from_b64(self, audio_b64: str) -> list[float]:
        """Compute embedding from base64-encoded audio."""
        audio_bytes = base64.b64decode(audio_b64)
        return self.compute_embedding(audio_bytes)

    def compute_similarity(self, embedding1: list[float], embedding2: list[float]) -> float:
        """Compute cosine similarity between two embeddings (0-1, higher = more similar)."""
        e1 = np.array(embedding1)
        e2 = np.array(embedding2)

        # Normalize and compute cosine similarity
        e1_norm = e1 / np.linalg.norm(e1)
        e2_norm = e2 / np.linalg.norm(e2)
        similarity = float(np.dot(e1_norm, e2_norm))

        # Clamp to [0, 1] range (cosine similarity can be [-1, 1])
        return max(0.0, (similarity + 1) / 2)
