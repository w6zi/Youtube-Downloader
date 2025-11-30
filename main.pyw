import sys
import os
from contextlib import contextmanager
from typing import Optional, List, Dict
from urllib.parse import urlparse, parse_qs

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLineEdit,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QSplitter,
    QLabel,
    QFrame,
    QGraphicsDropShadowEffect,
    QFileDialog,
    QProgressBar,
    QComboBox,
)

from yt_dlp import YoutubeDL

# Fewer search results = faster perceived speed
SEARCH_LIMIT = 25


@contextmanager
def no_proxies():
    """
    Temporarily disable system HTTP(S) proxies for yt_dlp calls.
    """
    saved = {
        "HTTP_PROXY": os.environ.pop("HTTP_PROXY", None),
        "HTTPS_PROXY": os.environ.pop("HTTPS_PROXY", None),
        "http_proxy": os.environ.pop("http_proxy", None),
        "https_proxy": os.environ.pop("https_proxy", None),
        "NO_PROXY": os.environ.get("NO_PROXY"),
        "no_proxy": os.environ.get("no_proxy"),
    }
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        yield
    finally:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            if saved[k] is not None:
                os.environ[k] = saved[k]
        for k in ("NO_PROXY", "no_proxy"):
            if saved[k] is not None:
                os.environ[k] = saved[k]
            else:
                os.environ.pop(k, None)


def looks_like_url(text: str) -> bool:
    """
    Very basic check to decide if the input is a URL instead of a search query.
    """
    text = text.strip()
    if text.startswith("http://") or text.startswith("https://"):
        return True
    if "youtube.com" in text or "youtu.be" in text:
        return True
    return False


class MainWindow(QMainWindow):
    """
    Clean YouTube downloader:
    - Search by query OR paste a URL.
    - Shows results on the left.
    - Download controls + progress on the right.
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle("YouTube Downloader")
        self.resize(1100, 700)

        # Make title bar black
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor("#202123"))
        self.setPalette(palette)

        # Data store for current search results
        self.results: List[Dict[str, str]] = []  # each: {title, url, id}

        # The last "direct URL" info when user pastes a link instead of search
        self.direct_entry: Optional[Dict[str, str]] = None

        # Download directory
        self.download_dir = os.path.expanduser("~/Downloads")

        # --- THEME: ChatGPT-ish gray + orange accent ---
        self.theme = {
            "bg": "#202123",            # main background
            "bg_panel": "#343541",      # secondary panel
            "card_bg": "#3a3b45",       # card
            "fg": "#e5e7eb",            # main text
            "muted": "#9ca3af",         # subtle text
            "border": "#4b5563",        # border lines
            "accent": "#f97316",        # orange accent
            "accent_soft": "#fed7aa",   # light orange accent
            "scrollbar_bg": "#202123",
            "scrollbar_handle": "#4b5563",
        }

        self._build_ui()
        self._apply_theme()

    # -------------------------------------------------------------------------
    # UI CONSTRUCTION
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        """
        Build the full UI: left side (input + results) and right side (download).
        """
        # LEFT SIDE: input + results
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search YouTube or paste a link…")

        self.search_button = QPushButton("Go")

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        self.results_list = QListWidget()
        self.results_list.itemSelectionChanged.connect(self.on_selection_changed)
        self.results_list.itemDoubleClicked.connect(self.on_download_selected)

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(10)

        title_label = QLabel("YouTube Downloader")
        title_label.setObjectName("TitleLabel")

        subtitle_label = QLabel("Search or paste a link, pick quality, and download.")
        subtitle_label.setObjectName("SubtitleLabel")

        left_layout.addWidget(title_label)
        left_layout.addWidget(subtitle_label)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        input_row.addWidget(self.search_input, 1)
        input_row.addWidget(self.search_button)
        left_layout.addLayout(input_row)

        results_header = QLabel("Results")
        results_header.setObjectName("SectionHeader")
        left_layout.addWidget(results_header)
        left_layout.addWidget(self.results_list, 1)
        left_layout.addWidget(self.status_label)

        left_panel = QFrame()
        left_panel.setObjectName("LeftPanel")
        left_panel.setLayout(left_layout)

        # RIGHT SIDE: details + download controls + progress
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)

        self.selected_title_label = QLabel("No video selected.")
        self.selected_title_label.setObjectName("SelectedTitle")
        self.selected_title_label.setWordWrap(True)

        self.selected_url_label = QLabel("")
        self.selected_url_label.setObjectName("SelectedUrl")
        self.selected_url_label.setWordWrap(True)

        # Card container
        self.details_card = QFrame()
        self.details_card.setObjectName("DetailsCard")
        card_layout = QVBoxLayout(self.details_card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)

        # Add a subtle drop shadow to the card
        shadow = QGraphicsDropShadowEffect()
        shadow.setOffset(0, 10)
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(0, 0, 0, 190))
        self.details_card.setGraphicsEffect(shadow)

        # Quality selector
        quality_row = QHBoxLayout()
        quality_row.setSpacing(10)
        quality_label = QLabel("Quality")
        quality_label.setObjectName("FieldLabel")

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(
            [
                "Fast (360p)",      # small, fast
                "Balanced (720p)",  # good default
                "Max (best available)",
            ]
        )
        self.quality_combo.setCurrentIndex(1)

        quality_row.addWidget(quality_label)
        quality_row.addWidget(self.quality_combo, 1)

        # Folder selector
        folder_row = QHBoxLayout()
        folder_row.setSpacing(10)
        folder_label = QLabel("Folder")
        folder_label.setObjectName("FieldLabel")

        self.folder_value_label = QLabel(self.download_dir)
        self.folder_value_label.setObjectName("FolderValue")
        self.folder_value_label.setWordWrap(True)

        self.choose_dir_button = QPushButton("Change…")

        folder_row.addWidget(folder_label)
        folder_row.addWidget(self.folder_value_label, 1)
        folder_row.addWidget(self.choose_dir_button)

        # Download button
        self.download_button = QPushButton("Download Selected")
        self.download_button.setObjectName("DownloadButton")

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("ProgressLabel")

        # Assemble card
        card_layout.addWidget(self.selected_title_label)
        card_layout.addWidget(self.selected_url_label)
        card_layout.addSpacing(10)
        card_layout.addLayout(quality_row)
        card_layout.addLayout(folder_row)
        card_layout.addSpacing(10)
        card_layout.addWidget(self.download_button)
        card_layout.addSpacing(10)
        card_layout.addWidget(self.progress_bar)
        card_layout.addWidget(self.progress_label)

        right_layout.addWidget(self.details_card, 0, Qt.AlignTop)
        right_layout.addStretch(1)

        right_panel = QFrame()
        right_panel.setObjectName("RightPanel")
        right_panel.setLayout(right_layout)

        # Splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([550, 550])

        container = QWidget()
        container_layout = QHBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(splitter)
        container.setLayout(container_layout)

        self.setCentralWidget(container)

        # Wire up signals
        self.search_button.clicked.connect(self.on_search_clicked)
        self.search_input.returnPressed.connect(self.on_search_clicked)
        self.choose_dir_button.clicked.connect(self.on_choose_dir_clicked)
        self.download_button.clicked.connect(self.on_download_selected)

    # -------------------------------------------------------------------------
    # THEME / STYLING
    # -------------------------------------------------------------------------

    def _build_stylesheet(self) -> str:
        t = self.theme
        return f"""
    QWidget {{
        background-color: {t['bg']};
        color: {t['fg']};
        font-family: "Inter", "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont;
        font-size: 13px;
        font-weight: 300;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }}

    QFrame#LeftPanel {{
        background-color: {t['bg_panel']};
        border-right: 1px solid {t['border']};
    }}

    QFrame#RightPanel {{
        background-color: {t['bg']};
    }}

    QFrame#DetailsCard {{
        background-color: {t['card_bg']};
        border-radius: 24px;
        border: 1px solid {t['border']};
    }}

    QLabel#TitleLabel {{
        font-size: 20px;
        font-weight: 600;
    }}

    QLabel#SubtitleLabel {{
        color: {t['muted']};
        font-size: 12px;
    }}

    QLabel#SectionHeader {{
        font-size: 13px;
        font-weight: 500;
        color: {t['muted']};
    }}

    QLabel#SelectedTitle {{
        font-size: 15px;
        font-weight: 600;
    }}

    QLabel#SelectedUrl {{
        font-size: 11px;
        color: {t['muted']};
    }}

    QLabel#FieldLabel {{
        font-size: 12px;
        color: {t['muted']};
    }}

    QLabel#FolderValue {{
        font-size: 12px;
    }}

    QLabel#ProgressLabel {{
        font-size: 11px;
        color: {t['muted']};
    }}

    QLineEdit {{
        background-color: {t['bg']};
        color: {t['fg']};
        border: 1px solid {t['border']};
        border-radius: 15px;
        padding: 6px 10px;
    }}

    QLineEdit:focus {{
        border: 1px solid {t['accent']};
    }}

    QPushButton {{
        background-color: {t['bg']};
        color: {t['fg']};
        border-radius: 15px;
        padding: 6px 14px;
        border: 1px solid {t['border']};
    }}

    QPushButton:hover {{
        border-color: {t['accent']};
    }}

    QPushButton:pressed {{
        background-color: {t['accent']};
        color: #111827;
        border-color: {t['accent']};
    }}

    QPushButton#DownloadButton {{
        background-color: {t['accent']};
        color: #111827;
        border-radius: 999px;
        font-weight: 600;
        border: none;
        padding: 8px 20px;
    }}

    QPushButton#DownloadButton:hover {{
        background-color: {t['accent_soft']};
    }}

    QComboBox {{
        background-color: {t['bg']};
        color: {t['fg']};
        border-radius: 15px;
        padding: 4px 8px;
        border: 1px solid {t['border']};
    }}

    QComboBox::drop-down {{
        border: none;
    }}

    QListWidget {{
        background-color: {t['bg']};
        color: {t['fg']};
        border-radius: 15px;
        border: 1px solid {t['border']};
        padding: 4px;
    }}

    QListWidget::item {{
        padding: 6px 6px;
    }}

    QListWidget::item:selected {{
        background-color: rgba(249, 115, 22, 0.1);
        color: {t['fg']};
        border-radius: 20px;
    }}

    QProgressBar {{
        background-color: {t['bg']};
        border-radius: 6px;
        border: 1px solid {t['border']};
        height: 10px;
    }}

    QProgressBar::chunk {{
        background-color: {t['accent']};
        border-radius: 6px;
    }}

    QScrollBar:vertical {{
        background: {t['scrollbar_bg']};
        width: 10px;
        margin: 0;
        border: none;
    }}

    QScrollBar::handle:vertical {{
        background: {t['scrollbar_handle']};
        min-height: 20px;
        border-radius: 5px;
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        background: transparent;
        height: 0;
    }}

    QScrollBar:horizontal {{
        background: {t['scrollbar_bg']};
        height: 10px;
        margin: 0;
        border: none;
    }}

    QScrollBar::handle:horizontal {{
        background: {t['scrollbar_handle']};
        min-width: 20px;
        border-radius: 5px;
    }}

    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {{
        background: transparent;
        width: 0;
    }}

    QSplitter::handle {{
        background: {t['border']};
        width: 1px;
    }}
    """

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app:
            app.setStyleSheet(self._build_stylesheet())

    # -------------------------------------------------------------------------
    # EVENT HANDLERS
    # -------------------------------------------------------------------------

    def on_search_clicked(self) -> None:
        text = self.search_input.text().strip()
        if not text:
            self._set_status("Type a search or paste a YouTube link.")
            return

        # If it looks like a URL, resolve it directly into one "result"
        if looks_like_url(text):
            self._search_single_url(text)
        else:
            self._search_query(text)

    def _search_query(self, query: str) -> None:
        """
        Use yt_dlp's ytsearch to fetch a list of matching videos.
        """
        self._set_status("Searching YouTube…")
        QApplication.processEvents()

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "default_search": f"ytsearch{SEARCH_LIMIT}",
        }

        try:
            with no_proxies():
                with YoutubeDL(ydl_opts) as ydl:
                    data = ydl.extract_info(query, download=False)
        except Exception as e:
            self._set_status(f"Search error: {e}")
            return

        entries = data.get("entries", []) if isinstance(data, dict) else []

        self.results.clear()
        self.results_list.clear()
        self.direct_entry = None

        for idx, e in enumerate(entries, start=1):
            vid_id = e.get("id")
            title = e.get("title") or "(no title)"
            if vid_id:
                url = f"https://www.youtube.com/watch?v={vid_id}"
            else:
                url = e.get("url")
            if not url:
                continue
            item = {"title": title, "url": url, "id": vid_id or ""}
            self.results.append(item)
            lw_item = QListWidgetItem(f"{idx:02d}. {title}")
            self.results_list.addItem(lw_item)

        if self.results:
            self._set_status(f"Found {len(self.results)} results.")
            self.results_list.setCurrentRow(0)
        else:
            self._set_status("No results found.")

    def _search_single_url(self, url: str) -> None:
        """
        Resolve a single YouTube URL into a result entry (title + url + id).
        """
        self._set_status("Resolving video info…")
        QApplication.processEvents()

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }

        try:
            with no_proxies():
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
        except Exception as e:
            self._set_status(f"Error resolving URL: {e}")
            return

        if not isinstance(info, dict):
            self._set_status("Could not read info for that link.")
            return

        title = info.get("title") or "(no title)"
        vid_id = info.get("id") or self._extract_video_id(url)

        self.results.clear()
        self.results_list.clear()
        self.direct_entry = {"title": title, "url": url, "id": vid_id or ""}

        self.results.append(self.direct_entry)
        self.results_list.addItem(QListWidgetItem(title))
        self.results_list.setCurrentRow(0)

        self._set_status("Ready to download.")

    def on_selection_changed(self) -> None:
        """
        When the selection in the results list changes, update the right panel.
        """
        row = self.results_list.currentRow()
        if row < 0 or row >= len(self.results):
            self.selected_title_label.setText("No video selected.")
            self.selected_url_label.setText("")
            return

        entry = self.results[row]
        self.selected_title_label.setText(entry["title"])
        self.selected_url_label.setText(entry["url"])

    def on_choose_dir_clicked(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose download folder",
            self.download_dir,
        )
        if directory:
            self.download_dir = directory
            self.folder_value_label.setText(self.download_dir)

    def on_download_selected(self) -> None:
        """
        Triggered by clicking the Download button OR double-clicking a result.
        """
        row = self.results_list.currentRow()
        if row < 0 or row >= len(self.results):
            self._set_status("Select a video first.")
            return

        entry = self.results[row]
        url = entry["url"]
        title = entry["title"]
        self._download_video(url, title)

    # -------------------------------------------------------------------------
    # DOWNLOAD LOGIC
    # -------------------------------------------------------------------------

    def _format_for_quality(self) -> str:
        """
        Map combo selection to yt_dlp format selector.
        """
        idx = self.quality_combo.currentIndex()
        if idx == 0:
            # Fast: smaller progressive MP4 around 360p/480p
            return "18/best[height<=480][ext=mp4]/best[height<=480]/best"
        elif idx == 1:
            # Balanced: prefer 720p MP4
            return "22/18/best[height<=720][ext=mp4]/best[height<=720]/best"
        else:
            # Max: best available video+audio or just best
            return "bestvideo+bestaudio/best"

    def _download_video(self, url: str, title: str) -> None:
        """
        Synchronous download using yt_dlp with a progress hook that updates the UI.
        """
        self._set_status(f"Downloading: {title}")
        self.progress_bar.setValue(0)
        self.progress_label.setText("0%")
        QApplication.processEvents()

        fmt = self._format_for_quality()

        def progress_hook(d: Dict) -> None:
            if d.get("status") == "downloading":
                pstr = (d.get("_percent_str") or "").strip().replace("%", "").replace(",", ".")
                try:
                    pval = int(float(pstr))
                except Exception:
                    pval = 0
                pval = max(0, min(100, pval))
                self.progress_bar.setValue(pval)
                self.progress_label.setText(f"{pval}%")
                QApplication.processEvents()
            elif d.get("status") == "finished":
                self.progress_bar.setValue(100)
                self.progress_label.setText("Processing file…")
                QApplication.processEvents()

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": os.path.join(self.download_dir, "%(title)s.%(ext)s"),
            "format": fmt,
            "progress_hooks": [progress_hook],
        }

        try:
            with no_proxies():
                with YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
        except Exception as e:
            self._set_status(f"Download error: {e}")
            self.progress_label.setText("")
            return

        self._set_status(f"Downloaded to: {self.download_dir}")
        self.progress_bar.setValue(100)
        self.progress_label.setText("Done")

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    @staticmethod
    def _extract_video_id(url: str) -> str:
        """
        Best-effort extraction of the YouTube video ID from the given URL.
        """
        try:
            parsed = urlparse(url)
            if parsed.hostname in ("youtu.be",):
                return parsed.path.lstrip("/")
            if "youtube.com" in (parsed.hostname or ""):
                qs = parse_qs(parsed.query)
                if "v" in qs:
                    return qs["v"][0]
        except Exception:
            pass
        return ""


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
