"""
aminbiography Browser 2.0
A 64-bit-ready Windows desktop browser application built with Python,
PySide6, and Qt WebEngine (Chromium).

Run:
    python -m pip install -r requirements.txt
    python main.py

This is a browser application using the Chromium engine, not a browser
rendering engine implemented from scratch.
"""

# ---------------------------------------------------------------------------
# 1. Imports   
# ---------------------------------------------------------------------------
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from PySide6.QtCore import QUrl, QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QToolBar, QLineEdit,
    QStatusBar, QMessageBox, QMenu, QFileDialog, QInputDialog,
    QDialog, QVBoxLayout, QListWidget, QHBoxLayout, QPushButton,
    QLabel, QProgressBar, QWidget
)
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineDownloadRequest, QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView


# ---------------------------------------------------------------------------
# 2. Application configuration and local data paths
# ---------------------------------------------------------------------------
APP_NAME = "aminbiography"
APP_VERSION = "2.0"
HOME_URL = "https://www.google.com"
SEARCH_URL = "https://www.google.com/search?q={}"

DATA_DIR = Path.home() / ".aminbiography_browser"
DATA_DIR.mkdir(parents=True, exist_ok=True)
BOOKMARKS_FILE = DATA_DIR / "bookmarks.json"
HISTORY_FILE = DATA_DIR / "history.json"


# ---------------------------------------------------------------------------
# 3. Reusable JSON storage helpers
# ---------------------------------------------------------------------------
def read_json(path, default):
    """Read a JSON file safely, returning a default if it is missing/broken."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def write_json(path, value):
    """Write JSON data to the browser's local profile directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Bookmark manager dialog
# ---------------------------------------------------------------------------
class BookmarkDialog(QDialog):
    """Show saved bookmarks and let the user open or remove an entry."""

    def __init__(self, bookmarks, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bookmarks")
        self.resize(620, 400)
        self.bookmarks = list(bookmarks)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select a bookmark, then open or remove it."))

        self.list_widget = QListWidget()
        for item in self.bookmarks:
            self.list_widget.addItem(f"{item.get('title', 'Untitled')} — {item.get('url', '')}")
        layout.addWidget(self.list_widget)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.remove_button = QPushButton("Remove")
        self.close_button = QPushButton("Close")
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.open_button.clicked.connect(self.accept)
        self.remove_button.clicked.connect(self.remove_selected)
        self.close_button.clicked.connect(self.reject)

    def remove_selected(self):
        index = self.list_widget.currentRow()
        if index >= 0:
            self.bookmarks.pop(index)
            self.list_widget.takeItem(index)

    def selected_url(self):
        index = self.list_widget.currentRow()
        if 0 <= index < len(self.bookmarks):
            return self.bookmarks[index].get("url", "")
        return ""


# ---------------------------------------------------------------------------
# 5. Download manager dialog
# ---------------------------------------------------------------------------
class DownloadManagerDialog(QDialog):
    """Display active and completed downloads with progress information."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Downloads")
        self.resize(620, 350)
        self.rows = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Current and recent downloads"))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    def add_download(self, download, filename):
        item = self.list_widget.addItem if False else None
        from PySide6.QtWidgets import QListWidgetItem
        row = QListWidgetItem(f"{filename} — starting…")
        self.list_widget.addItem(row)
        self.rows[id(download)] = row

        def update_progress():
            total = download.totalBytes()
            received = download.receivedBytes()
            if total > 0:
                percent = int(received * 100 / total)
                row.setText(f"{filename} — {percent}%")
            else:
                row.setText(f"{filename} — {received} bytes received")

        def update_state(_state=None):
            state = download.state()
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                row.setText(f"{filename} — completed")
            elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
                row.setText(f"{filename} — cancelled")
            elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
                row.setText(f"{filename} — interrupted")
            else:
                update_progress()

        download.receivedBytesChanged.connect(update_progress)
        download.totalBytesChanged.connect(update_progress)
        download.stateChanged.connect(update_state)
        update_state()


# ---------------------------------------------------------------------------
# 6. Main browser window and Chromium profile
# ---------------------------------------------------------------------------
class BrowserWindow(QMainWindow):
    """Main application window with navigation, tabs, storage, and tools."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1360, 860)
        self.history = read_json(HISTORY_FILE, [])
        self.bookmarks = read_json(BOOKMARKS_FILE, [])
        self.download_dialog = None
        self.devtools_windows = []

        # -------------------------------------------------------------------
        # 6.1 Persistent Chromium profile
        # -------------------------------------------------------------------
        # Qt WebEngine stores cookies and cache in this named local profile.
        self.profile = QWebEngineProfile("aminbiographyBrowserProfile", self)
        self.profile.setPersistentStoragePath(str(DATA_DIR / "storage"))
        self.profile.setCachePath(str(DATA_DIR / "cache"))
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.profile.downloadRequested.connect(self.handle_download)

        # -------------------------------------------------------------------
        # 6.2 Tab widget
        # -------------------------------------------------------------------
        # Each tab uses the shared profile to share cookies and browser storage.
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.currentChanged.connect(self.update_url_bar)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self.show_tab_context_menu)
        self.setCentralWidget(self.tabs)

        # -------------------------------------------------------------------
        # 6.3 Navigation toolbar and address/search bar
        # -------------------------------------------------------------------
        self.toolbar = QToolBar("Navigation")
        self.toolbar.setMovable(False)
        self.toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(self.toolbar)

        self.toolbar.addAction(self.make_action("←", "Back", self.go_back))
        self.toolbar.addAction(self.make_action("→", "Forward", self.go_forward))
        self.toolbar.addAction(self.make_action("⟳", "Reload", self.reload_page))
        self.toolbar.addAction(self.make_action("⌂", "Home", self.go_home))

        self.url_bar = QLineEdit()
        self.url_bar.setPlaceholderText("Search or enter a website address")
        self.url_bar.setClearButtonEnabled(True)
        self.url_bar.returnPressed.connect(self.navigate_to_address)
        self.toolbar.addWidget(self.url_bar)

        self.toolbar.addAction(self.make_action("☆", "Bookmark current page", self.add_bookmark))
        self.toolbar.addAction(self.make_action("+", "New tab", self.new_tab))

        # -------------------------------------------------------------------
        # 6.4 Status bar and menus
        # -------------------------------------------------------------------
        self.setStatusBar(QStatusBar())
        self.create_menus()

        # -------------------------------------------------------------------
        # 6.5 Keyboard shortcuts
        # -------------------------------------------------------------------
        self.add_shortcut("Ctrl+L", self.focus_address_bar)
        self.add_shortcut("Ctrl+T", self.new_tab)
        self.add_shortcut("Ctrl+W", self.close_current_tab)
        self.add_shortcut("Ctrl+R", self.reload_page)
        self.add_shortcut("Ctrl+H", self.show_history)
        self.add_shortcut("Ctrl+J", self.show_downloads)
        self.add_shortcut("Ctrl+D", self.add_bookmark)
        self.add_shortcut("Ctrl+Shift+Delete", self.clear_browsing_data)
        self.add_shortcut("Ctrl+Shift+I", self.open_devtools)
        self.add_shortcut("Ctrl+F", self.find_on_page)
        self.add_shortcut("Ctrl+Plus", lambda: self.change_zoom(1.1))
        self.add_shortcut("Ctrl+Minus", lambda: self.change_zoom(1 / 1.1))
        self.add_shortcut("Ctrl+0", self.reset_zoom)
        self.add_shortcut("Alt+Left", self.go_back)
        self.add_shortcut("Alt+Right", self.go_forward)

        # -------------------------------------------------------------------
        # 6.6 Initial browser tab
        # -------------------------------------------------------------------
        self.new_tab(HOME_URL)

    # -----------------------------------------------------------------------
    # 7. Actions, shortcuts, and current-page helpers
    # -----------------------------------------------------------------------
    def make_action(self, text, tooltip, callback):
        action = QAction(text, self)
        action.setToolTip(tooltip)
        action.triggered.connect(callback)
        return action

    def add_shortcut(self, sequence, callback):
        action = QAction(self)
        action.setShortcut(QKeySequence(sequence))
        action.triggered.connect(callback)
        self.addAction(action)

    def current_view(self):
        return self.tabs.currentWidget()

    # -----------------------------------------------------------------------
    # 8. Tab creation, closing, and title updates
    # -----------------------------------------------------------------------
    def new_tab(self, url=None):
        view = QWebEngineView()
        view.setPage(QWebEnginePage(self.profile, view))

        index = self.tabs.addTab(view, "New Tab")
        self.tabs.setCurrentIndex(index)

        view.titleChanged.connect(lambda title, v=view: self.update_tab_title(v, title))
        view.urlChanged.connect(lambda qurl, v=view: self.update_url_for_view(v, qurl))
        view.loadProgress.connect(
            lambda progress, v=view: self.show_load_progress(v, progress)
        )
        view.loadFinished.connect(lambda ok, v=view: self.load_finished(v, ok))
        view.page().linkHovered.connect(self.show_link_status)
        view.urlChanged.connect(lambda qurl, v=view: self.record_history(v, qurl))

        view.setUrl(QUrl(url or HOME_URL))
        return view

    def close_tab(self, index):
        if self.tabs.count() <= 1:
            self.current_view().setUrl(QUrl(HOME_URL))
            return
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        widget.deleteLater()

    def close_current_tab(self):
        self.close_tab(self.tabs.currentIndex())

    def update_tab_title(self, view, title):
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.tabs.setTabText(index, (title or "New Tab")[:28])

    # -----------------------------------------------------------------------
    # 9. Address navigation and search fallback
    # -----------------------------------------------------------------------
    def navigate_to_address(self):
        text = self.url_bar.text().strip()
        if not text:
            return
        if " " in text or ("." not in text and not text.startswith(("http://", "https://"))):
            target = SEARCH_URL.format(quote_plus(text))
        else:
            target = text if "://" in text else "https://" + text
        self.current_view().setUrl(QUrl.fromUserInput(target))

    def update_url_bar(self, _index=None):
        view = self.current_view()
        if view:
            self.url_bar.setText(view.url().toString())
            self.url_bar.setCursorPosition(0)

    def update_url_for_view(self, view, qurl):
        if view is self.current_view():
            self.url_bar.setText(qurl.toString())
            self.url_bar.setCursorPosition(0)

    def focus_address_bar(self):
        self.url_bar.setFocus()
        self.url_bar.selectAll()

    # -----------------------------------------------------------------------
    # 10. Navigation commands and loading feedback
    # -----------------------------------------------------------------------
    def go_back(self):
        if self.current_view():
            self.current_view().back()

    def go_forward(self):
        if self.current_view():
            self.current_view().forward()

    def reload_page(self):
        if self.current_view():
            self.current_view().reload()

    def go_home(self):
        if self.current_view():
            self.current_view().setUrl(QUrl(HOME_URL))

    def show_load_progress(self, view, progress):
        if view is self.current_view():
            self.statusBar().showMessage(f"Loading… {progress}%")

    def load_finished(self, view, ok):
        if view is self.current_view():
            self.statusBar().showMessage("Page loaded" if ok else "Page could not load", 3500)
            self.update_url_bar()

        # Apply the Google homepage customization after the page has loaded.
        self.customize_google_homepage(view)

    def customize_google_homepage(self, view):
        """Rename I'm Feeling Lucky and make the Google Search button submit reliably."""
        url = view.url()
        host = (url.host() or "").lower()
        path = url.path() or "/"

        if not (host == "google.com" or host.endswith(".google.com")):
            return
        if path not in ("", "/"):
            return

        script = r"""
        (() => {
            const QUICK_LINKS = [
                ["Google Maps", "https://maps.google.com/"],
                ["Gmail", "https://mail.google.com/"],
                ["YouTube", "https://www.youtube.com/"],
                ["Google Translate", "https://translate.google.com/"],
                ["Google News", "https://news.google.com/"]
            ];

            const renameCalibrations = () => {
                document.querySelectorAll(
                    'input[name="btnI"], button[name="btnI"]'
                ).forEach((button) => {
                    if (button.tagName === "INPUT") {
                        button.value = "Calibrations";
                    } else {
                        button.textContent = "Calibrations";
                    }
                    button.setAttribute("aria-label", "Calibrations");
                });
            };

            const renameGoogleWeather = () => {
                document.querySelectorAll(
                    'input[name="btnK"], button[name="btnK"]'
                ).forEach((button) => {
                    if (button.tagName === "INPUT") {
                        button.value = "Google Weather";
                    } else {
                        button.textContent = "Google Weather";
                    }
                    button.setAttribute("aria-label", "Google Weather");
                });
            };

            const addQuickLinks = () => {
                if (document.getElementById("aminbiography-quick-links")) return;

                const searchForm = document.querySelector('form:has([name="q"])');
                if (!searchForm) return;

                const container = document.createElement("div");
                container.id = "aminbiography-quick-links";
                container.setAttribute("aria-label", "Quick links");
                container.style.cssText = [
                    "display:flex",
                    "flex-wrap:wrap",
                    "justify-content:center",
                    "align-items:center",
                    "gap:8px",
                    "margin:10px auto 0",
                    "max-width:760px",
                    "padding:0 12px"
                ].join(";");

                QUICK_LINKS.forEach(([label, url]) => {
                    const link = document.createElement("a");
                    link.href = url;
                    link.textContent = label;
                    link.style.cssText = [
                        "display:inline-flex",
                        "align-items:center",
                        "justify-content:center",
                        "min-height:36px",
                        "padding:0 16px",
                        "box-sizing:border-box",
                        "border:1px solid #3c4043",
                        "border-radius:6px",
                        "background:#303134",
                        "color:#e8eaed",
                        "font:14px Arial,sans-serif",
                        "text-decoration:none",
                        "white-space:nowrap",
                        "cursor:pointer"
                    ].join(";");

                    link.addEventListener("mouseenter", () => {
                        link.style.background = "#3c4043";
                        link.style.borderColor = "#5f6368";
                    });
                    link.addEventListener("mouseleave", () => {
                        link.style.background = "#303134";
                        link.style.borderColor = "#3c4043";
                    });

                    container.appendChild(link);
                });

                searchForm.insertAdjacentElement("afterend", container);
            };

            const handleWeatherClick = (event) => {
                const path = typeof event.composedPath === "function"
                    ? event.composedPath()
                    : [event.target];

                const clickedWeather = path.some((item) => {
                    if (!item || !item.matches) return false;
                    const name = (item.getAttribute("name") || "").toLowerCase();
                    const label = (
                        item.innerText ||
                        item.value ||
                        item.getAttribute("aria-label") ||
                        ""
                    ).trim().toLowerCase();
                    return name === "btnk" || label === "google weather";
                });

                if (!clickedWeather) return;
                event.preventDefault();
                event.stopPropagation();
                window.location.href = "https://www.google.com/search?q=weather";
            };

            if (!window.__aminbiographyWeatherHandlerInstalled) {
                document.addEventListener("click", handleWeatherClick, true);
                window.__aminbiographyWeatherHandlerInstalled = true;
            }

            const applyHomepageChanges = () => {
                renameCalibrations();
                renameGoogleWeather();
                addQuickLinks();
            };

            applyHomepageChanges();

            if (!window.__aminbiographyHomepageButtonsObserver) {
                const observer = new MutationObserver(applyHomepageChanges);
                observer.observe(document.documentElement, {
                    childList: true,
                    subtree: true
                });
                window.__aminbiographyHomepageButtonsObserver = observer;
            }
        })();
        """
        view.page().runJavaScript(script)

    def show_link_status(self, link):
        self.statusBar().showMessage(link, 5000)

    # -----------------------------------------------------------------------
    # 11. Browsing history
    # -----------------------------------------------------------------------
    def record_history(self, view, qurl):
        url = qurl.toString()
        if not url or url in ("about:blank",):
            return
        title = view.title() or url
        entry = {"title": title, "url": url, "visited": datetime.now().isoformat(timespec="seconds")}
        if self.history and self.history[-1].get("url") == url:
            self.history[-1] = entry
        else:
            self.history.append(entry)
        self.history = self.history[-1000:]
        write_json(HISTORY_FILE, self.history)

    def show_history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Browsing history")
        dialog.resize(760, 440)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        for item in reversed(self.history):
            listing.addItem(f"{item.get('visited', '')} | {item.get('title', '')} | {item.get('url', '')}")
        layout.addWidget(listing)
        buttons = QHBoxLayout()
        open_button = QPushButton("Open selected")
        clear_button = QPushButton("Clear history")
        close_button = QPushButton("Close")
        buttons.addWidget(open_button)
        buttons.addWidget(clear_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        def open_selected():
            row = listing.currentRow()
            if row >= 0:
                entry = list(reversed(self.history))[row]
                self.current_view().setUrl(QUrl(entry.get("url", "")))
                dialog.accept()

        def clear_history():
            answer = QMessageBox.question(dialog, "Clear history", "Delete all saved browsing history?")
            if answer == QMessageBox.StandardButton.Yes:
                self.history = []
                write_json(HISTORY_FILE, self.history)
                listing.clear()

        open_button.clicked.connect(open_selected)
        clear_button.clicked.connect(clear_history)
        close_button.clicked.connect(dialog.accept)
        dialog.exec()

    # -----------------------------------------------------------------------
    # 12. Bookmark management
    # -----------------------------------------------------------------------
    def save_bookmarks(self):
        write_json(BOOKMARKS_FILE, self.bookmarks)

    def add_bookmark(self):
        view = self.current_view()
        if not view:
            return
        entry = {"title": view.title() or view.url().toString(), "url": view.url().toString()}
        if entry not in self.bookmarks:
            self.bookmarks.append(entry)
            self.save_bookmarks()
            self.statusBar().showMessage("Bookmark saved", 2500)
        else:
            self.statusBar().showMessage("Already bookmarked", 2500)

    def manage_bookmarks(self):
        dialog = BookmarkDialog(self.bookmarks, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = dialog.selected_url()
            self.bookmarks = dialog.bookmarks
            self.save_bookmarks()
            if selected:
                self.current_view().setUrl(QUrl(selected))
        else:
            self.bookmarks = dialog.bookmarks
            self.save_bookmarks()

    def refresh_bookmarks_menu(self):
        self.bookmarks_menu.clear()
        for item in self.bookmarks:
            action = self.bookmarks_menu.addAction(item.get("title", "Bookmark"))
            action.triggered.connect(
                lambda checked=False, url=item.get("url", ""): self.current_view().setUrl(QUrl(url))
            )
        if not self.bookmarks:
            empty = self.bookmarks_menu.addAction("No bookmarks saved")
            empty.setEnabled(False)
        self.bookmarks_menu.addSeparator()
        self.bookmarks_menu.addAction("Manage bookmarks…", self.manage_bookmarks)
        self.bookmarks_menu.addAction("Bookmark current page", self.add_bookmark)

    # -----------------------------------------------------------------------
    # 13. Download handling and download manager
    # -----------------------------------------------------------------------
    def show_downloads(self):
        if self.download_dialog is None:
            self.download_dialog = DownloadManagerDialog(self)
        self.download_dialog.show()
        self.download_dialog.raise_()
        self.download_dialog.activateWindow()

    def handle_download(self, download: QWebEngineDownloadRequest):
        suggested = download.downloadFileName() or "download"
        path, _ = QFileDialog.getSaveFileName(self, "Save download", suggested)
        if not path:
            download.cancel()
            return
        download.setDownloadDirectory(str(Path(path).parent))
        download.setDownloadFileName(Path(path).name)
        self.show_downloads()
        self.download_dialog.add_download(download, Path(path).name)
        download.accept()
        self.statusBar().showMessage(f"Downloading {Path(path).name}", 4000)

    # -----------------------------------------------------------------------
    # 14. Developer tools
    # -----------------------------------------------------------------------
    def open_devtools(self):
        view = self.current_view()
        if not view:
            return
        devtools = QWebEngineView()
        devtools.setWindowTitle("Developer Tools — aminbiography")
        devtools.resize(1000, 650)
        view.page().setDevToolsPage(devtools.page())
        devtools.show()
        self.devtools_windows.append(devtools)

    # -----------------------------------------------------------------------
    # 15. Find in page and zoom controls
    # -----------------------------------------------------------------------
    def find_on_page(self):
        text, accepted = QInputDialog.getText(self, "Find on page", "Text to find:")
        if accepted and text:
            self.current_view().findText(text)

    def change_zoom(self, factor):
        view = self.current_view()
        if view:
            view.setZoomFactor(max(0.5, min(3.0, view.zoomFactor() * factor)))

    def reset_zoom(self):
        if self.current_view():
            self.current_view().setZoomFactor(1.0)

    # -----------------------------------------------------------------------
    # 16. Privacy controls: clear cache, cookies, and local history
    # -----------------------------------------------------------------------
    def clear_browsing_data(self):
        answer = QMessageBox.question(
            self,
            "Clear browsing data",
            "Clear cached files, cookies, and saved browsing history? "
            "This may sign you out of websites.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.profile.clearHttpCache()
        self.profile.cookieStore().deleteAllCookies()
        self.history = []
        write_json(HISTORY_FILE, self.history)
        QMessageBox.information(
            self, "Browsing data cleared",
            "The cache-clear request was sent, cookies were scheduled for deletion, "
            "and saved history was cleared. Some site data may require a browser restart."
        )

    # -----------------------------------------------------------------------
    # 17. Menus and tab context menu
    # -----------------------------------------------------------------------
    def create_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("New tab", self.new_tab, QKeySequence("Ctrl+T"))
        file_menu.addAction("Close tab", self.close_current_tab, QKeySequence("Ctrl+W"))
        file_menu.addSeparator()
        file_menu.addAction("Quit", self.close, QKeySequence("Ctrl+Q"))

        history_menu = self.menuBar().addMenu("&History")
        history_menu.addAction("Show history", self.show_history, QKeySequence("Ctrl+H"))

        self.bookmarks_menu = self.menuBar().addMenu("&Bookmarks")
        self.bookmarks_menu.aboutToShow.connect(self.refresh_bookmarks_menu)

        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction("Downloads", self.show_downloads, QKeySequence("Ctrl+J"))
        tools_menu.addAction("Developer tools", self.open_devtools, QKeySequence("Ctrl+Shift+I"))
        tools_menu.addAction("Find on page", self.find_on_page, QKeySequence("Ctrl+F"))
        tools_menu.addSeparator()
        tools_menu.addAction("Clear browsing data…", self.clear_browsing_data, QKeySequence("Ctrl+Shift+Delete"))

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction("Zoom in", lambda: self.change_zoom(1.1))
        view_menu.addAction("Zoom out", lambda: self.change_zoom(1 / 1.1))
        view_menu.addAction("Reset zoom", self.reset_zoom)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("About", self.show_about)

        # Direct New Tab command beside Help for easier discovery.
        new_tab_action = QAction("＋ New Tab", self)
        new_tab_action.setToolTip("Open a new tab (Ctrl+T)")
        new_tab_action.setStatusTip("Open a new tab")
        new_tab_action.triggered.connect(self.new_tab)
        self.menuBar().addAction(new_tab_action)

    def show_tab_context_menu(self, position):
        index = self.tabs.tabBar().tabAt(position)
        if index < 0:
            return
        menu = QMenu(self)
        menu.addAction("New tab", self.new_tab)
        menu.addAction("Close tab", lambda: self.close_tab(index))
        menu.addAction("Close other tabs", lambda: self.close_other_tabs(index))
        menu.exec(self.tabs.tabBar().mapToGlobal(position))

    def close_other_tabs(self, keep_index):
        for index in reversed(range(self.tabs.count())):
            if index != keep_index:
                self.close_tab(index)

    def show_about(self):
        QMessageBox.about(
            self,
            f"About {APP_NAME} Browser",
            f"<h2>{APP_NAME} Browser {APP_VERSION}</h2>"
            "<p>A custom desktop browser built with Python, PySide6, and Qt WebEngine.</p>"
            "<p>Chromium handles HTML, CSS, JavaScript, networking, and page rendering.</p>"
            "<p>This is a learning and development project, not a security-hardened browser.</p>"
        )


# ---------------------------------------------------------------------------
# 18. Application entry point
# ---------------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("aminbiography")
    window = BrowserWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

