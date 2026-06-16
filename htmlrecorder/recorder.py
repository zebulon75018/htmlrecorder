"""
htmlrecorder.recorder
~~~~~~~~~~~~~~~~~~~~~
HtmlRecorder — rend une page HTML dans un QWebEngineView,
attend le chargement complet, puis capture les frames vers une vidéo OpenCV.

Accès direct au moteur web
--------------------------
>>> rec = HtmlRecorder("page.html", output="out.mp4", duration=10)

# Propriétés exposées
>>> rec.page    → RecorderPage  (QWebEnginePage)
>>> rec.view    → QWebEngineView

# Navigation
>>> rec.set_url("https://example.com")
>>> rec.set_html("<h1>Hello</h1>", base_url="file:///tmp/")

# Exécution JavaScript
>>> rec.run_js("document.body.style.background = 'red'")
>>> rec.run_js("document.title", callback=lambda v: print(v))

# Injection CSS / JS
>>> rec.inject_css("body { font-size: 32px !important; }")
>>> rec.inject_js_file("/path/to/script.js")

# Arrêt depuis JS
>>> console.log("stop")   <- intercepté par RecorderPage
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

# ── Doit être défini AVANT tout import PyQt5 ──────────────────────────────
# Autorise l'autoplay de vidéo/audio sans geste utilisateur
_cf = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
for _flag in [
    "--autoplay-policy=no-user-gesture-required",
    "--disable-features=AutoplayIgnoreWebAudio",
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-software-rasterizer",
]:
    if _flag not in _cf:
        _cf += " " + _flag
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _cf.strip()
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QT_XCB_GL_INTEGRATION",       "none")
os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE",         "1")
if not os.environ.get("_HTMLRECORDER_WEBENGINE_INIT"):
    try:
        from PyQt5.QtWebEngineWidgets import QWebEngineView
        from PyQt5.QtWebEngine import QtWebEngine
        QtWebEngine.initialize()
    except Exception as e:
        print(e)
    os.environ["_HTMLRECORDER_WEBENGINE_INIT"] = "1"
else:
    from PyQt5.QtWebEngineWidgets import QWebEngineView


from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings  # noqa: F401
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QImage
import numpy as np

from .page import RecorderPage
from .writer import VideoWriter


# ─────────────────────────────────────────────────────────────────────────
# Utilitaires vidéo
# ─────────────────────────────────────────────────────────────────────────

def convert_video_for_web(
    input_path: str,
    output_path: str = "",
    crf: int = 33,
    audio: bool = False,
    width: int = 1280,
    height: int = 720,
) -> str:
    """
    Convertit une vidéo en VP9/WebM compatible avec Qt WebEngine.

    Qt WebEngine (Linux) ne supporte pas H.264 par défaut (codec propriétaire).
    VP9 est un codec libre lu nativement par Chromium/Qt WebEngine.

    Parameters
    ----------
    input_path  : Chemin du fichier source (MP4, AVI, MKV…).
    output_path : Chemin de sortie. Si vide, génère un fichier temporaire.
    crf         : Qualité VP9 (18 = excellent, 33 = bon, 40 = compact).
    audio       : Inclure la piste audio (codec Opus).
    width/height: Résolution cible.

    Returns
    -------
    str : Chemin du fichier WebM converti.

    Raises
    ------
    FileNotFoundError : si ffmpeg n'est pas installé.
    RuntimeError      : si la conversion échoue.
    """
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError(
            "FFmpeg introuvable. Installez-le :\n"
            "  Ubuntu/Debian : sudo apt install ffmpeg\n"
            "  macOS         : brew install ffmpeg\n"
            "  Windows       : https://ffmpeg.org/download.html"
        )

    # Normaliser le chemin (supprimer file:///)
    if input_path.startswith("file:///"):
        import urllib.parse
        input_path = urllib.parse.unquote(input_path[7:])

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Fichier source introuvable : {input_path}")

    if not output_path:
        output_path = tempfile.mktemp(suffix=".webm")

    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease," \
         f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"

    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "libvp9",
        "-crf", str(crf), "-b:v", "0",
        "-vf", vf,
        "-threads", "4",
        "-deadline", "realtime",   # encodage rapide
    ]
    if audio:
        cmd += ["-c:a", "libopus", "-b:a", "128k"]
    else:
        cmd += ["-an"]  # pas d'audio
    cmd += ["-y", output_path]

    print(f"[convert_video_for_web] Conversion VP9/WebM : {os.path.basename(input_path)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg a échoué (code {result.returncode}) :\n{result.stderr[-500:]}"
        )
    print(f"[convert_video_for_web] ✓ {output_path}")
    return output_path


class VideoCanvasPlayer:
    """
    Lit une vidéo avec OpenCV et envoie les frames sur un <canvas> HTML
    via JavaScript. Contourne totalement les limitations de codec de
    Qt WebEngine — fonctionne avec H.264, H.265, AV1, HEVC, etc.

    Usage
    -----
    >>> player = VideoCanvasPlayer("video.mp4", recorder, canvas_id="video-bg")
    >>> recorder.recording_started.connect(player.start)
    >>> recorder.recording_stopped.connect(player.stop)

    Le <canvas> doit exister dans le HTML avec l'id correspondant.
    Le JS ``window.updateVideoFrame(dataUrl)`` est appelé sur chaque frame.
    """

    def __init__(
        self,
        video_path: str,
        recorder: "HtmlRecorder",
        canvas_id: str = "video-bg",
        loop: bool = True,
        quality: int = 82,
        target_fps: Optional[float] = None,
    ) -> None:
        """
        Parameters
        ----------
        video_path  : Chemin de la vidéo (MP4, AVI…).
        recorder    : Instance HtmlRecorder cible.
        canvas_id   : id du <canvas> dans le HTML.
        loop        : Remettre à zéro en fin de vidéo.
        quality     : Qualité JPEG des frames (1-100). 82 = bon compromis.
        target_fps  : FPS cible (None = utiliser le FPS natif de la vidéo).
        """
        import cv2
        # Normaliser le chemin
        if video_path.startswith("file:///"):
            import urllib.parse
            video_path = urllib.parse.unquote(video_path[7:])

        self._cap       = cv2.VideoCapture(video_path)
        if not self._cap.isOpened():
            raise RuntimeError(f"Impossible d'ouvrir la vidéo : {video_path}")

        self._recorder  = recorder
        self._canvas_id = canvas_id
        self._loop      = loop
        self._quality   = quality
        self._cv2       = cv2

        native_fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._fps   = target_fps or native_fps

        # Timer de frames
        self._timer = QTimer()
        self._timer.setInterval(max(1, round(1000.0 / self._fps)))
        self._timer.timeout.connect(self._send_frame)

        # Préparer la fonction JS une seule fois
        self._js_init = """
if(!window._vcpInitDone){
    window._vcpInitDone=true;
    window.updateVideoFrame=function(dataUrl){
        var c=document.getElementById('""" + canvas_id + """');
        if(!c) return;
        var ctx=c.getContext('2d');
        var img=new Image();
        img.onload=function(){ ctx.drawImage(img,0,0,c.width,c.height); };
        img.src=dataUrl;
    };
}
"""

    def start(self) -> None:
        """Démarre l'envoi des frames. Appeler depuis recording_started."""
        # Injecter la fonction JS
        self._recorder.run_js(self._js_init)
        self._timer.start()

    def stop(self, *_) -> None:
        """Arrête le timer et libère la vidéo."""
        self._timer.stop()
        if self._cap.isOpened():
            self._cap.release()

    def seek(self, ms: int) -> None:
        """Aller à la position ms dans la vidéo."""
        self._cap.set(self._cv2.CAP_PROP_POS_MSEC, ms)

    def _send_frame(self) -> None:
        ret, frame = self._cap.read()
        if not ret:
            if self._loop:
                self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self._cap.read()
            if not ret:
                return

        # Encoder en JPEG puis base64
        ok, buf = self._cv2.imencode(
            ".jpg", frame,
            [self._cv2.IMWRITE_JPEG_QUALITY, self._quality]
        )
        if not ok:
            return
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        self._recorder.run_js(
            f"typeof updateVideoFrame==='function'&&"
            f"updateVideoFrame('data:image/jpeg;base64,{b64}')"
        )



class HtmlRecorder(QObject):
    """
    Enregistre une page web vers un fichier vidéo.

    Parameters
    ----------
    url : str, optional
        URL HTTP/HTTPS ou chemin local.
    output : str
        Fichier vidéo de sortie (ex. "out.mp4").
    duration : float | None
        Durée max en secondes.  None = illimité.
    fps : float
        Fréquence de capture/lecture.
    width, height : int
        Résolution du viewport et de la vidéo.
    show_window : bool
        Afficher la fenêtre pendant l'enregistrement.
    load_timeout : float
        Secondes avant de forcer le démarrage si loadFinished ne se déclenche pas.
    on_start : callable, optional
        Appelé (sans arg) quand l'enregistrement commence.
    on_stop : callable, optional
        Appelé avec le chemin de sortie quand l'enregistrement s'arrête.
    """

    recording_started = pyqtSignal()
    recording_stopped = pyqtSignal(str)

    def __init__(
        self,
        url: str = "",
        output: str = "output.mp4",
        duration: Optional[float] = None,
        fps: float = 30.0,
        width: int = 1280,
        height: int = 720,
        show_window: bool = True,
        load_timeout: float = 30.0,
        on_start: Optional[Callable] = None,
        on_stop: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._app = QApplication.instance() or QApplication(sys.argv)
        super().__init__()

        self._url = url
        self.output = output
        self.duration = duration
        self.fps = fps
        self.width = width
        self.height = height
        self._show_window = show_window
        self._load_timeout_sec = load_timeout
        self._on_start_cb = on_start
        self._on_stop_cb = on_stop

        self._recording = False
        self._video_writer: Optional[VideoWriter] = None
        self._temp_html_file: Optional[str] = None   # fichier temporaire

        # ── Fenêtre & vue ─────────────────────────────────────────────
        self._window = QMainWindow()
        self._window.setFixedSize(width, height)
        print(width)
        print(height)
        self._window.setWindowTitle("HtmlRecorder")

        self._web_view = QWebEngineView()
        self._web_view.setFixedSize(width, height)
        self._window.setCentralWidget(self._web_view)

        # ── Paramètres WebEngine ──────────────────────────────────────
        #   Nécessaires pour charger des fichiers locaux (vidéo, images)
        self._apply_web_settings()

        # ── Page personnalisée ────────────────────────────────────────
        self._page = RecorderPage(self._web_view)
        self._web_view.setPage(self._page)
        self._page.stop_requested.connect(self._stop_recording)

        # ── Timers ────────────────────────────────────────────────────
        self._capture_timer = QTimer(self)
        self._capture_timer.setInterval(max(1, round(1000.0 / fps)))
        self._capture_timer.timeout.connect(self._capture_frame)

        self._duration_timer = QTimer(self)
        self._duration_timer.setSingleShot(True)

        self._load_timeout_timer = QTimer(self)
        self._load_timeout_timer.setSingleShot(True)
        self._load_timeout_timer.setInterval(round(load_timeout * 1000))
        self._load_timeout_timer.timeout.connect(self._on_load_timeout)

        # ── Connexions ─────────────────────────────────────────────────
        self._web_view.loadFinished.connect(self._on_load_finished)
        self.recording_started.connect(self._dispatch_on_start)
        self.recording_stopped.connect(self._dispatch_on_stop)

    # ═════════════════════════════════════════════════════════════════
    # Paramètres WebEngine
    # ═════════════════════════════════════════════════════════════════

    def _apply_web_settings(self) -> None:
        """Active les permissions nécessaires pour les fichiers locaux et la vidéo."""
        s = self._web_view.settings()

        # ── Accès aux fichiers locaux ─────────────────────────────────
        # Permet à un fichier file:// de charger d'autres fichiers file://
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        # Permet à un fichier file:// de faire des requêtes vers d'autres origines
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)

        # ── Contenu mixte (HTTP + HTTPS) ──────────────────────────────
        s.setAttribute(QWebEngineSettings.AllowRunningInsecureContent, True)

        # ── Plugins & médias ──────────────────────────────────────────
        s.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        s.setAttribute(QWebEngineSettings.AutoLoadImages, True)

        # ── Appliquer aussi aux paramètres globaux ─────────────────────
        gs = QWebEngineSettings.globalSettings()
        gs.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        gs.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        gs.setAttribute(QWebEngineSettings.AllowRunningInsecureContent, True)
        gs.setAttribute(QWebEngineSettings.PluginsEnabled, True)

    # ═════════════════════════════════════════════════════════════════
    # API publique — accès direct au moteur web
    # ═════════════════════════════════════════════════════════════════

    @property
    def page(self) -> RecorderPage:
        """Accès direct à la RecorderPage (QWebEnginePage)."""
        return self._page

    @property
    def view(self) -> QWebEngineView:
        """Accès direct au QWebEngineView."""
        return self._web_view

    # ── Navigation ───────────────────────────────────────────────────

    def set_url(self, url: str) -> None:
        """Naviguer vers une nouvelle URL ou fichier local."""
        self._web_view.load(self._resolve_url(url))

    def set_html(self, html: str, base_url: str = "") -> None:
        """
        Injecter du HTML directement dans la vue.

        ⚠ Limitation : setHtml() utilise l'origine qrc:// qui BLOQUE
        l'accès aux fichiers locaux (video, images en file://).
        Utilisez run_html() ou run_html_file() à la place.

        Parameters
        ----------
        html     : Code HTML complet.
        base_url : URL de base pour résoudre les ressources relatives.
        """
        qbase = QUrl(base_url) if base_url else QUrl()
        self._web_view.setHtml(html, qbase)

    # ── JavaScript ───────────────────────────────────────────────────

    def run_js(self, code: str, callback: Optional[Callable] = None) -> None:
        """
        Exécuter du JavaScript dans la page courante.

        Parameters
        ----------
        code     : Code JS à exécuter.
        callback : Appelé avec la valeur de retour JS (facultatif).
        """
        if callback:
            self._page.runJavaScript(code, callback)
        else:
            self._page.runJavaScript(code)

    def inject_js(self, code: str) -> None:
        """Injecter un bloc de code JavaScript (alias de run_js)."""
        self._page.runJavaScript(code)

    def inject_js_file(self, path: str) -> None:
        """
        Injecter le contenu d'un fichier .js dans la page.

        Parameters
        ----------
        path : Chemin vers le fichier JavaScript.
        """
        code = Path(path).read_text(encoding="utf-8")
        self._page.runJavaScript(code)

    # ── CSS ──────────────────────────────────────────────────────────

    def inject_css(self, css: str) -> None:
        """
        Injecter des règles CSS via un élément <style> créé dynamiquement.

        Parameters
        ----------
        css : Règles CSS à injecter.
        """
        safe = css.replace("\\", "\\\\").replace("`", "\\`")
        self._page.runJavaScript(f"""
(function(){{
    var s = document.createElement('style');
    s.textContent = `{safe}`;
    document.head.appendChild(s);
}})();
""")

    def inject_css_file(self, path: str) -> None:
        """
        Injecter le contenu d'un fichier .css dans la page.

        Parameters
        ----------
        path : Chemin vers le fichier CSS.
        """
        self.inject_css(Path(path).read_text(encoding="utf-8"))

    # ── Manipulation du DOM ──────────────────────────────────────────

    def set_inner_html(self, selector: str, html: str) -> None:
        """
        Remplacer le innerHTML d'un élément DOM.

        Parameters
        ----------
        selector : Sélecteur CSS (ex. "#title", ".subtitle").
        html     : Nouveau contenu HTML.
        """
        safe = html.replace("\\", "\\\\").replace("`", "\\`")
        self._page.runJavaScript(f"""
(function(){{
    var el = document.querySelector('{selector}');
    if (el) el.innerHTML = `{safe}`;
}})();
""")

    def set_attribute(self, selector: str, attr: str, value: str) -> None:
        """
        Modifier un attribut d'un élément DOM.

        Parameters
        ----------
        selector : Sélecteur CSS.
        attr     : Nom de l'attribut (ex. "src", "class").
        value    : Nouvelle valeur.
        """
        safe_val = value.replace("\\", "\\\\").replace("`", "\\`")
        self._page.runJavaScript(f"""
(function(){{
    var el = document.querySelector('{selector}');
    if (el) el.setAttribute('{attr}', `{safe_val}`);
}})();
""")

    def set_css_var(self, name: str, value: str) -> None:
        """
        Modifier une variable CSS (custom property) sur :root.

        Parameters
        ----------
        name  : Nom de la variable CSS (avec ou sans '--', ex. '--color1').
        value : Nouvelle valeur CSS.

        Example
        -------
        >>> rec.set_css_var("--primary", "#ff6464")
        """
        if not name.startswith("--"):
            name = "--" + name
        safe_val = value.replace("'", "\\'")
        self._page.runJavaScript(
            f"document.documentElement.style.setProperty('{name}', '{safe_val}');"
        )

    # ═════════════════════════════════════════════════════════════════
    # Cycle de vie
    # ═════════════════════════════════════════════════════════════════

    def run(self) -> None:
        """
        Afficher la fenêtre, charger l'URL et entrer dans la boucle Qt.
        Bloque jusqu'à la fin de l'enregistrement.
        """
        self._window.show()
        if self._url:
            qurl = self._resolve_url(self._url)
            print(f"[HtmlRecorder] Chargement → {qurl.toString()}")
            self._web_view.load(qurl)
            self._load_timeout_timer.start()
        else:
            print("[HtmlRecorder] Pas d'URL — utilisez set_url() ou run_html().")
        self._app.exec_()

    def run_html(self, html: str, base_url: str = "") -> None:
        """
        Charger du HTML brut et démarrer l'enregistrement.

        Stratégie de chargement :
        - Si aucune base_url → écrit le HTML dans un fichier temporaire
          et le charge via file://, ce qui permet l'accès aux ressources
          locales (vidéo, images) avec LocalContentCanAccessFileUrls.
        - Si base_url fournie → utilise setHtml() avec cette base.

        Parameters
        ----------
        html     : Code source HTML complet.
        base_url : URL de base pour les ressources relatives.
                   Si vide, un fichier temporaire est utilisé.
        """
        self._window.show()

        if base_url:
            # base_url fournie explicitement → setHtml classique
            self.set_html(html, base_url)
        else:
            # Écrire dans un fichier temporaire pour autoriser file://
            self._write_temp_and_load(html)

        self._load_timeout_timer.start()
        self._app.exec_()

    def run_html_file(self, html: str, media_dir: str = "") -> None:
        """
        Charger du HTML avec accès aux ressources locales (vidéo, images).

        Écrit le HTML dans un fichier temporaire situé dans *media_dir*
        (ou dans le répertoire courant), puis le charge via file://.
        Cela garantit que les chemins relatifs pointent vers *media_dir*
        et que les chemins file:/// absolus sont accessibles.

        Parameters
        ----------
        html      : Code HTML complet.
        media_dir : Dossier contenant les fichiers médias.
                    Si vide, utilise le répertoire courant.

        Example
        -------
        >>> rec.run_html_file(html, media_dir="/home/user/Videos")
        """
        self._window.show()
        base = media_dir or os.getcwd()
        self._write_temp_and_load(html, directory=base)
        self._load_timeout_timer.start()
        self._app.exec_()

    def stop(self) -> None:
        """Arrêter l'enregistrement depuis Python."""
        self._stop_recording()

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def frame_count(self) -> int:
        return self._video_writer.frame_count if self._video_writer else 0

    # ═════════════════════════════════════════════════════════════════
    # Méthodes privées
    # ═════════════════════════════════════════════════════════════════

    def _write_temp_and_load(self, html: str, directory: Optional[str] = None) -> None:
        """
        Écrit le HTML dans un .html temporaire et le charge via file://.
        Le fichier est supprimé à l'arrêt de l'enregistrement.
        """
        kwargs: dict = {"suffix": ".html", "delete": False,
                        "mode": "w", "encoding": "utf-8"}
        if directory:
            kwargs["dir"] = directory

        with tempfile.NamedTemporaryFile(**kwargs) as f:
            f.write(html)
            self._temp_html_file = f.name

        qurl = QUrl.fromLocalFile(self._temp_html_file)
        print(f"[HtmlRecorder] Chargement HTML (fichier temp) → {qurl.toString()}")
        self._web_view.load(qurl)

    def _cleanup_temp(self) -> None:
        """Supprime le fichier HTML temporaire s'il existe."""
        if self._temp_html_file and os.path.exists(self._temp_html_file):
            try:
                os.unlink(self._temp_html_file)
            except OSError:
                pass
            self._temp_html_file = None

    @staticmethod
    def _resolve_url(url: str) -> QUrl:
        if url.startswith(("http://", "https://", "ftp://", "qrc://")):
            return QUrl(url)
        return QUrl.fromLocalFile(str(Path(url).resolve()))

    def _on_load_finished(self, ok: bool) -> None:
        self._load_timeout_timer.stop()
        status = "OK" if ok else "échec signalé (démarrage forcé)"
        print(f"[HtmlRecorder] loadFinished — {status}")
        self._start_recording()

    def _on_load_timeout(self) -> None:
        print(
            f"[HtmlRecorder] Timeout ({self._load_timeout_sec}s) — "
            "démarrage de l'enregistrement."
        )
        self._start_recording()

    def _start_recording(self) -> None:
        if self._recording:
            return
        try:
            self._video_writer = VideoWriter(
                self.output, self.fps, self.width, self.height
            )
        except RuntimeError as exc:
            print(f"[HtmlRecorder] ERREUR: {exc}")
            self._app.quit()
            return

        self._recording = True
        self._capture_timer.start()

        if self.duration is not None:
            self._duration_timer.setInterval(round(self.duration * 1000))
            self._duration_timer.timeout.connect(self._stop_recording)
            self._duration_timer.start()

        print(
            f"[HtmlRecorder] ● Enregistrement démarré  "
            f"({self.fps} fps, {self.width}×{self.height}"
            + (f", max {self.duration}s)" if self.duration else ")")
        )
        self.recording_started.emit()

    def _capture_frame(self) -> None:
        if not self._recording:
            return
        pixmap = self._web_view.grab()
        qimage = pixmap.toImage().convertToFormat(QImage.Format_RGB888)
        w, h = qimage.width(), qimage.height()
        ptr = qimage.bits()
        ptr.setsize(h * w * 3)
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 3)).copy()
        import cv2
        self._video_writer.write_frame(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))

    def _stop_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self._capture_timer.stop()
        self._duration_timer.stop()
        if self._video_writer:
            self._video_writer.release()
            print(
                f"[HtmlRecorder] ■ Arrêt — "
                f"{self._video_writer.frame_count} frames, "
                f"{self._video_writer.duration:.2f}s → {self.output}"
            )
        self.recording_stopped.emit(self.output)
        QTimer.singleShot(300, lambda: (self._cleanup_temp(), self._app.quit()))

    def _dispatch_on_start(self) -> None:
        if self._on_start_cb:
            self._on_start_cb()

    def _dispatch_on_stop(self, path: str) -> None:
        if self._on_stop_cb:
            self._on_stop_cb(path)
