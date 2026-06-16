"""
htmlrecorder.page
~~~~~~~~~~~~~~~~~
Custom QWebEnginePage that:
  • Prints every JS console message to stdout.
  • Emits stop_requested when console.log("stop") is detected.
"""

from PyQt5.QtWebEngineWidgets import QWebEnginePage
from PyQt5.QtCore import pyqtSignal


class RecorderPage(QWebEnginePage):
    """QWebEnginePage subclass that watches JS console output."""

    #: Emitted for every JS console message (message, level, source_id).
    console_message = pyqtSignal(str, int, str)

    #: Emitted when JavaScript sends console.log("stop").
    stop_requested = pyqtSignal()

    # Map Qt level int → label
    _LEVEL_LABELS = {0: "DEBUG", 1: "INFO", 2: "WARN", 3: "ERROR"}

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        label = self._LEVEL_LABELS.get(int(level), "LOG")
        short_src = source_id.split("/")[-1] if source_id else ""
        print(f"[JS {label}] {message}  (line {line_number}  {short_src})")

        self.console_message.emit(message, int(level), source_id)

        # The magic stop signal
        if message.strip().lower() == "stop":
            self.stop_requested.emit()
