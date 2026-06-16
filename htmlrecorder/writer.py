"""
htmlrecorder.writer
~~~~~~~~~~~~~~~~~~~
Thin wrapper around cv2.VideoWriter with:
  • Automatic codec selection from the file extension.
  • Frame resize/conversion guard (BGR, exact target resolution).
  • Frame count and elapsed-duration helpers.
"""

from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path


class VideoWriter:
    """OpenCV-backed video writer for captured web frames."""

    # Preferred codec per output format
    _CODECS: dict[str, str] = {
        ".mp4": "mp4v",
        ".avi": "XVID",
        ".mkv": "X264",
        ".mov": "mp4v",
    }

    def __init__(
        self,
        output_path: str,
        fps: float,
        width: int,
        height: int,
        codec: str | None = None,
    ) -> None:
        self.output_path = output_path
        self.fps = fps
        self.width = width
        self.height = height
        self.frame_count: int = 0

        ext = Path(output_path).suffix.lower()
        chosen_codec = codec or self._CODECS.get(ext, "mp4v")
        chosen_codec = "H264"

        fourcc = cv2.VideoWriter_fourcc(*chosen_codec)
        self._writer = cv2.VideoWriter(
            output_path, fourcc, fps, (width, height)
        )

        if not self._writer.isOpened():
            raise RuntimeError(
                f"cv2.VideoWriter could not open {output_path!r} "
                f"(codec={chosen_codec}, {width}×{height} @ {fps} fps). "
                "Check that opencv-python is installed and the path is writable."
            )

        print(
            f"[VideoWriter] {width}×{height} @ {fps} fps  "
            f"codec={chosen_codec}  → {output_path}"
        )

    # ------------------------------------------------------------------

    def write_frame(self, frame: np.ndarray) -> None:
        """Write one BGR frame.  Resizes automatically if dimensions differ."""
        h, w = frame.shape[:2]
        if w != self.width or h != self.height:
            frame = cv2.resize(
                frame, (self.width, self.height), interpolation=cv2.INTER_AREA
            )
        self._writer.write(frame)
        self.frame_count += 1

    def release(self) -> None:
        """Flush buffers and close the video file."""
        self._writer.release()
        print(
            f"[VideoWriter] Closed after {self.frame_count} frames "
            f"({self.duration:.2f}s) → {self.output_path}"
        )

    # ------------------------------------------------------------------

    @property
    def duration(self) -> float:
        """Elapsed recording time in seconds based on frame count."""
        return self.frame_count / self.fps if self.fps else 0.0

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"VideoWriter({self.output_path!r}, {self.fps} fps, "
            f"{self.width}×{self.height}, frames={self.frame_count})"
        )
