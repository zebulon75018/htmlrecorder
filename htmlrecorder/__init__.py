"""
htmlrecorder
~~~~~~~~~~~~
Qt WebEngine + OpenCV : enregistrement vidéo de pages HTML animées.

Classes principales
-------------------
HtmlRecorder    — capture une page web vers une vidéo MP4/AVI/MKV
RecorderPage    — QWebEnginePage avec interception de la console JS
VideoWriter     — wrapper OpenCV pour l'écriture vidéo

Utilisation rapide
------------------
>>> from htmlrecorder import HtmlRecorder

>>> rec = HtmlRecorder(output="out.mp4", duration=8, fps=30)
>>> rec.run_html(html)
"""

from .recorder import HtmlRecorder
from .page import RecorderPage
from .writer import VideoWriter

__version__ = "2.0.0"
__all__ = ["HtmlRecorder", "RecorderPage", "VideoWriter"]
