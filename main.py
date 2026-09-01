import sys
import csv
import sqlite3
import zipfile
import urllib.request
import os
import io
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTableWidget, QTableWidgetItem, QHeaderView, 
                             QMessageBox, QFileDialog, QProgressBar, QFrame,
                             QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
                             QComboBox, QKeySequenceEdit, QFormLayout)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QEvent
from PyQt6.QtGui import QKeySequence, QShortcut, QAction

ULS_DB = "uls_cache.db"
LOG_DB = "net_logs.db"
TEMP_DB = "uls_temp.db"
APP_VERSION = "v1.06"

STATUS_OPTIONS = ["General", "Mobile", "Portable", "Short Time", "In/Out"]

# -------------------------------------------------------------------
# DATABASE FUNCTIONS
# -------------------------------------------------------------------
def init_databases():
    conn = sqlite3.connect(LOG_DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS nets (
            net_id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time DATETIME DEFAULT (datetime('now', 'localtime')),
            net_name TEXT,
            control_callsign TEXT,
            control_operator TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            net_id INTEGER,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            callsign TEXT,
            name TEXT,
            location TEXT,
            status TEXT,
            comments TEXT,
            FOREIGN KEY (net_id) REFERENCES nets(net_id)
        )
    ''')
    
    # Auto-migration checks for existing databases
    c.execute("PRAGMA table_info(checkins)")
    columns = [col[1] for col in c.fetchall()]
    if 'net_id' not in columns:
        c.execute("ALTER TABLE checkins ADD COLUMN net_id INTEGER")
    if 'status' not in columns:
        c.execute("ALTER TABLE checkins ADD COLUMN status TEXT DEFAULT 'General'")
        
    conn.commit()
    conn.close()

    conn = sqlite3.connect(ULS_DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS uls_callsigns (
            usi INTEGER PRIMARY KEY,
            callsign TEXT UNIQUE,
            first_name TEXT,
            last_name TEXT,
            city TEXT,
            state TEXT
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_callsign ON uls_callsigns (callsign)')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_overrides (
            callsign TEXT PRIMARY KEY,
            preferred_name TEXT,
            preferred_location TEXT
        )
    ''')
    conn.commit()
    conn.close()

def download_file(url, target_path):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        import requests
        with requests.get(url, headers=headers, stream=True) as r:
            r.raise_for_status()
            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
    except ImportError:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp, open(target_path, 'wb') as out:
            out.write(resp.read())

def update_uls_db(status_callback=None):
    init_databases()
    
    url = "https://data.fcc.gov/download/pub/uls/complete/l_amat.zip"
    zip_path = "l_amat.zip"
    
    if status_callback: status_callback("Downloading FCC ULS Database...")
    download_file(url, zip_path)

    if os.path.exists(TEMP_DB):
        os.remove(TEMP_DB)

    temp_conn = sqlite3.connect(TEMP_DB)
    temp_c = temp_conn.cursor()

    # Staging Tables
    temp_c.execute('CREATE TABLE hd_raw (unique_system_identifier INT, license_status TEXT)')
    temp_c.execute('CREATE TABLE am_raw (unique_system_identifier INT)')
    temp_c.execute('''
        CREATE TABLE en_raw (
            unique_system_identifier INT, 
            callsign TEXT, 
            first_name TEXT, 
            last_name TEXT, 
            city TEXT, 
            state TEXT,
            entity_type TEXT,
            entity_name TEXT
        )
    ''')

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        if status_callback: status_callback("Importing HD.dat...")
        with zip_ref.open('HD.dat') as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding='latin-1'), delimiter='|')
            rows = []
            for r in reader:
                if len(r) > 5 and r[0] == 'HD':
                    try:
                        rows.append((int(r[1].strip()), r[5].strip()))
                    except ValueError:
                        continue
            temp_c.executemany('INSERT INTO hd_raw VALUES (?, ?)', rows)

        if status_callback: status_callback("Importing AM.dat...")
        with zip_ref.open('AM.dat') as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding='latin-1'), delimiter='|')
            rows = []
            for r in reader:
                if len(r) > 1 and r[0] == 'AM':
                    try:
                        rows.append((int(r[1].strip()),))
                    except ValueError:
                        continue
            temp_c.executemany('INSERT INTO am_raw VALUES (?)', rows)

        if status_callback: status_callback("Importing EN.dat...")
        with zip_ref.open('EN.dat') as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding='latin-1'), delimiter='|')
            rows = []
            for r in reader:
                if len(r) > 17 and r[0] == 'EN':
                    try:
                        usi = int(r[1].strip())
                        call = r[4].strip().upper() if len(r) > 4 else ''
                        entity_type = r[5].strip() if len(r) > 5 else ''
                        entity_name = r[7].strip() if len(r) > 7 else ''
                        first = r[8].strip() if len(r) > 8 else ''
                        last = r[10].strip() if len(r) > 10 else ''
                        city = r[16].strip() if len(r) > 16 else ''
                        state = r[17].strip() if len(r) > 17 else ''
                        
                        if not first and not last:
                            first = entity_name

                        rows.append((usi, call, first, last, city, state, entity_type, entity_name))
                    except ValueError:
                        continue
            temp_c.executemany('INSERT INTO en_raw VALUES (?, ?, ?, ?, ?, ?, ?, ?)', rows)

    if status_callback: status_callback("Indexing temp tables...")
    temp_c.execute('CREATE INDEX idx_hd_usi ON hd_raw (unique_system_identifier)')
    temp_c.execute('CREATE INDEX idx_am_usi ON am_raw (unique_system_identifier)')
    temp_c.execute('CREATE INDEX idx_en_usi ON en_raw (unique_system_identifier)')
    temp_conn.commit()

    if status_callback: status_callback("Running license query...")
    exact_user_query = '''
        SELECT DISTINCT 
            e.unique_system_identifier, 
            e.callsign, 
            e.first_name, 
            e.last_name, 
            e.city, 
            e.state 
        FROM en_raw e
        JOIN am_raw a ON e.unique_system_identifier = a.unique_system_identifier
        JOIN hd_raw hd ON e.unique_system_identifier = hd.unique_system_identifier
        WHERE hd.license_status = 'A'
    '''
    active_records = temp_c.execute(exact_user_query).fetchall()

    if status_callback: status_callback("Writing to cache...")
    conn = sqlite3.connect(ULS_DB)
    c = conn.cursor()
    c.execute('DELETE FROM uls_callsigns')
    c.executemany('''
        INSERT OR REPLACE INTO uls_callsigns (usi, callsign, first_name, last_name, city, state)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', active_records)
    conn.commit()
    conn.close()

    temp_conn.close()

    for file in [zip_path, TEMP_DB]:
        if os.path.exists(file):
            os.remove(file)

    if status_callback: status_callback("Update complete!")

def query_uls(callsign):
    if not os.path.exists(ULS_DB):
        return "", ""
        
    callsign = callsign.strip().upper()
    conn = sqlite3.connect(ULS_DB)
    c = conn.cursor()
    
    c.execute('SELECT preferred_name, preferred_location FROM user_overrides WHERE callsign = ?', (callsign,))
    override = c.fetchone()
    if override:
        conn.close()
        return override[0], override[1]

    c.execute('''
        SELECT first_name, last_name, city, state 
        FROM uls_callsigns 
        WHERE callsign = ?
    ''', (callsign,))
    res = c.fetchone()
    conn.close()
    
    if res:
        name = f"{res[0]} {res[1]}".strip()
        loc = f"{res[2]}, {res[3]}".strip(", ")
        return name, loc
        
    return "", ""

def save_user_override(callsign, name, location):
    conn = sqlite3.connect(ULS_DB)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO user_overrides (callsign, preferred_name, preferred_location)
        VALUES (?, ?, ?)
    ''', (callsign.upper(), name, location))
    conn.commit()
    conn.close()

def delete_checkin_by_id(checkin_id):
    conn = sqlite3.connect(LOG_DB)
    c = conn.cursor()
    c.execute('DELETE FROM checkins WHERE id = ?', (checkin_id,))
    conn.commit()
    conn.close()

def update_checkin_status_by_id(checkin_id, new_status):
    conn = sqlite3.connect(LOG_DB)
    c = conn.cursor()
    c.execute('UPDATE checkins SET status = ? WHERE id = ?', (new_status, checkin_id))
    conn.commit()
    conn.close()

# -------------------------------------------------------------------
# HOTKEY CONFIGURATION DIALOG
# -------------------------------------------------------------------
class HotkeyConfigDialog(QDialog):
    def __init__(self, current_hotkeys, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Status Hotkeys")
        self.resize(380, 260)
        self.hotkey_editors = {}
        self.result_hotkeys = {}

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        for status in STATUS_OPTIONS:
            seq_edit = QKeySequenceEdit()
            if status in current_hotkeys and current_hotkeys[status]:
                seq_edit.setKeySequence(QKeySequence(current_hotkeys[status]))
            self.hotkey_editors[status] = seq_edit
            form_layout.addRow(QLabel(f"<b>{status}:</b>"), seq_edit)

        layout.addLayout(form_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_keys)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept_keys(self):
        for status, editor in self.hotkey_editors.items():
            seq_str = editor.keySequence().toString()
            if seq_str:
                self.result_hotkeys[status] = seq_str
        self.accept()

# -------------------------------------------------------------------
# PAST NET SELECTION DIALOG
# -------------------------------------------------------------------
class PastNetsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load Past Net Session")
        self.resize(550, 350)
        self.selected_net_id = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select a previous net session to view or re-export:"))

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.load_nets()

    def load_nets(self):
        try:
            conn = sqlite3.connect(LOG_DB)
            c = conn.cursor()
            c.execute('''
                SELECT n.net_id, n.start_time, n.net_name, n.control_callsign, n.control_operator, COUNT(c.id)
                FROM nets n
                LEFT JOIN checkins c ON n.net_id = c.net_id
                GROUP BY n.net_id
                ORDER BY n.net_id ASC
            ''')
            rows = c.fetchall()
            conn.close()

            for row in rows:
                net_id, start_time, net_name, ctrl_call, ctrl_op, count = row
                display_text = f"[{start_time}] #{net_id}: {net_name or 'Unnamed Net'} — Control: {ctrl_call or 'N/A'} ({ctrl_op or 'N/A'}) — Check-ins: {count}"
                item = QListWidgetItem(display_text)
                item.setData(100, net_id)
                self.list_widget.addItem(item)
        except sqlite3.OperationalError as e:
            QMessageBox.critical(self, "Database Error", f"Error loading past nets:\n{e}")

    def accept_selection(self):
        selected_item = self.list_widget.currentItem()
        if selected_item:
            self.selected_net_id = selected_item.data(100)
            self.accept()
        else:
            QMessageBox.warning(self, "Selection Required", "Please select a net session from the list.")

# -------------------------------------------------------------------
# GUI APPLICATION
# -------------------------------------------------------------------
class ULSUpdateThread(QThread):
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def run(self):
        update_uls_db(self.status_signal.emit)
        self.finished_signal.emit()

class NetLoggerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"NetPal - {APP_VERSION}")
        self.resize(1050, 650)
        self.current_net_id = None
        
        # Default hotkey mapping: Status -> KeySequence string
        self.hotkeys = {
            "General": "Ctrl+1",
            "Mobile": "Ctrl+2",
            "Portable": "Ctrl+3",
            "Short Time": "Ctrl+4",
            "In/Out": "Ctrl+5"
        }
        self.active_shortcuts = []

        init_databases()
        self.init_ui()
        self.setup_menu()
        self.register_hotkeys()
        self.start_new_net_session()

    def setup_menu(self):
        menubar = self.menuBar()
        settings_menu = menubar.addMenu("Settings")

        hotkey_action = QAction("Configure Status Hotkeys", self)
        hotkey_action.triggered.connect(self.open_hotkey_dialog)
        settings_menu.addAction(hotkey_action)

    def register_hotkeys(self):
        # Clear old shortcuts
        for sc in self.active_shortcuts:
            sc.setParent(None)
        self.active_shortcuts.clear()

        # Bind active shortcuts to update current input status
        for status, key_seq in self.hotkeys.items():
            if key_seq:
                shortcut = QShortcut(QKeySequence(key_seq), self)
                shortcut.activated.connect(lambda s=status: self.set_status_from_shortcut(s))
                self.active_shortcuts.append(shortcut)

    def set_status_from_shortcut(self, status_name):
        index = self.status_dropdown.findText(status_name)
        if index >= 0:
            self.status_dropdown.setCurrentIndex(index)
            self.status_label.setText(f"Status shortcut set to: {status_name}")

    def open_hotkey_dialog(self):
        dialog = HotkeyConfigDialog(self.hotkeys, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.hotkeys = dialog.result_hotkeys
            self.register_hotkeys()
            self.status_label.setText("Hotkey configuration updated.")

    def init_ui(self):
        main_layout = QVBoxLayout()

        # Header Frame
        header_frame = QFrame()
        header_frame.setFrameShape(QFrame.Shape.StyledPanel)
        header_layout = QHBoxLayout(header_frame)

        self.net_name_input = QLineEdit()
        self.net_name_input.setPlaceholderText("e.g. 2m Emergency Net")

        self.control_call_input = QLineEdit()
        self.control_call_input.setPlaceholderText("e.g. KD3CVQ")

        self.control_op_input = QLineEdit()
        self.control_op_input.setPlaceholderText("e.g. Greg")

        header_layout.addWidget(QLabel("<b>Net Name:</b>"))
        header_layout.addWidget(self.net_name_input)
        header_layout.addWidget(QLabel("<b>Net Control Call:</b>"))
        header_layout.addWidget(self.control_call_input)
        header_layout.addWidget(QLabel("<b>Operator Name:</b>"))
        header_layout.addWidget(self.control_op_input)

        # Input Form Row 1: Call, Name, Loc, Status
        form_layout = QHBoxLayout()
        
        self.call_input = QLineEdit()
        self.call_input.setPlaceholderText("Callsign")
        self.call_input.textChanged.connect(self.on_callsign_typed)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Name")

        self.loc_input = QLineEdit()
        self.loc_input.setPlaceholderText("Location")

        self.status_dropdown = QComboBox()
        self.status_dropdown.addItems(STATUS_OPTIONS)
        self.status_dropdown.installEventFilter(self)

        form_layout.addWidget(QLabel("Call:"))
        form_layout.addWidget(self.call_input)
        form_layout.addWidget(QLabel("Name:"))
        form_layout.addWidget(self.name_input)
        form_layout.addWidget(QLabel("Loc:"))
        form_layout.addWidget(self.loc_input)
        form_layout.addWidget(QLabel("Status:"))
        form_layout.addWidget(self.status_dropdown)

        # Input Form Row 2: Comments Box & Log Button
        comment_layout = QHBoxLayout()
        self.comment_input = QLineEdit()
        self.comment_input.setPlaceholderText("Comments (Traffic, power, rig, etc.)")

        log_btn = QPushButton("Log Check-In")
        log_btn.clicked.connect(self.log_checkin)

        self.call_input.returnPressed.connect(self.log_checkin)
        self.name_input.returnPressed.connect(self.log_checkin)
        self.loc_input.returnPressed.connect(self.log_checkin)
        self.comment_input.returnPressed.connect(self.log_checkin)

        comment_layout.addWidget(self.comment_input)
        comment_layout.addWidget(log_btn)

        # Table Display Area (7 Columns: Timestamp, Call, Name, Loc, Status, Comments, Action)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Timestamp", "Callsign", "Name", "Location", "Status", "Comments", "Action"])
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(6, 60)

        # Action Buttons
        action_layout = QHBoxLayout()
        
        new_net_btn = QPushButton("Start New Net Session")
        new_net_btn.clicked.connect(self.start_new_net_session)

        load_net_btn = QPushButton("Load Past Net")
        load_net_btn.clicked.connect(self.load_past_net_dialog)

        update_btn = QPushButton("Update FCC Database")
        update_btn.clicked.connect(self.start_uls_update)
        
        export_btn = QPushButton("Export Current Net CSV")
        export_btn.clicked.connect(self.export_csv)

        action_layout.addWidget(new_net_btn)
        action_layout.addWidget(load_net_btn)
        action_layout.addWidget(update_btn)
        action_layout.addWidget(export_btn)

        # Footer Status & Version Bar
        footer_layout = QHBoxLayout()
        self.status_label = QLabel("Ready")
        version_label = QLabel(f"<b>NetPal {APP_VERSION}</b>")
        
        footer_layout.addWidget(self.status_label)
        footer_layout.addStretch()
        footer_layout.addWidget(version_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()

        main_layout.addWidget(header_frame)
        main_layout.addLayout(form_layout)
        main_layout.addLayout(comment_layout)
        main_layout.addWidget(self.table)
        main_layout.addLayout(action_layout)
        main_layout.addLayout(footer_layout)
        main_layout.addWidget(self.progress)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

    def eventFilter(self, source, event):
        if source == self.status_dropdown and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.log_checkin()
                return True
        return super().eventFilter(source, event)

    def on_callsign_typed(self, text):
        call = text.strip().upper()
        if len(call) >= 3:
            name, loc = query_uls(call)
            if name: self.name_input.setText(name)
            if loc: self.loc_input.setText(loc)

    def start_new_net_session(self):
        net_name = self.net_name_input.text().strip() or "Standard Net"
        ctrl_call = self.control_call_input.text().strip().upper()
        ctrl_op = self.control_op_input.text().strip()

        conn = sqlite3.connect(LOG_DB)
        c = conn.cursor()
        c.execute('''
            INSERT INTO nets (net_name, control_callsign, control_operator, start_time)
            VALUES (?, ?, ?, datetime('now', 'localtime'))
        ''', (net_name, ctrl_call, ctrl_op))
        self.current_net_id = c.lastrowid
        conn.commit()
        conn.close()

        self.table.setRowCount(0)
        self.call_input.clear()
        self.name_input.clear()
        self.loc_input.clear()
        self.comment_input.clear()
        self.status_dropdown.setCurrentIndex(0)
        self.status_label.setText(f"Active Net Session #{self.current_net_id}")

    def load_past_net_dialog(self):
        dialog = PastNetsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_net_id:
            self.load_net_by_id(dialog.selected_net_id)

    def load_net_by_id(self, net_id):
        conn = sqlite3.connect(LOG_DB)
        c = conn.cursor()
        c.execute('SELECT net_name, control_callsign, control_operator FROM nets WHERE net_id = ?', (net_id,))
        net_meta = c.fetchone()
        if net_meta:
            self.current_net_id = net_id
            self.net_name_input.setText(net_meta[0] or "")
            self.control_call_input.setText(net_meta[1] or "")
            self.control_op_input.setText(net_meta[2] or "")

        conn.close()
        self.load_session_logs()
        self.status_label.setText(f"Loaded Historical Net Session #{self.current_net_id}")

    def update_net_header_info(self):
        if not self.current_net_id:
            return
        conn = sqlite3.connect(LOG_DB)
        c = conn.cursor()
        c.execute('''
            UPDATE nets 
            SET net_name = ?, control_callsign = ?, control_operator = ?
            WHERE net_id = ?
        ''', (
            self.net_name_input.text().strip(),
            self.control_call_input.text().strip().upper(),
            self.control_op_input.text().strip(),
            self.current_net_id
        ))
        conn.commit()
        conn.close()

    def log_checkin(self):
        call = self.call_input.text().strip().upper()
        name = self.name_input.text().strip()
        loc = self.loc_input.text().strip()
        status = self.status_dropdown.currentText()
        comment = self.comment_input.text().strip()

        if not call or not self.current_net_id:
            return

        self.update_net_header_info()
        save_user_override(call, name, loc)

        conn = sqlite3.connect(LOG_DB)
        c = conn.cursor()
        c.execute('''
            INSERT INTO checkins (net_id, callsign, name, location, status, comments, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ''', (self.current_net_id, call, name, loc, status, comment))
        conn.commit()
        conn.close()

        self.call_input.clear()
        self.name_input.clear()
        self.loc_input.clear()
        self.comment_input.clear()
        self.status_dropdown.setCurrentIndex(0)
        self.call_input.setFocus()
        self.load_session_logs()

    def on_table_status_changed(self, checkin_id, new_status):
        update_checkin_status_by_id(checkin_id, new_status)
        self.status_label.setText(f"Updated status for check-in #{checkin_id} to '{new_status}'")

    def confirm_delete_checkin(self, checkin_id, callsign):
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete the check-in for {callsign}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            delete_checkin_by_id(checkin_id)
            self.load_session_logs()
            self.status_label.setText(f"Deleted check-in record #{checkin_id}")

    def load_session_logs(self):
        if not self.current_net_id:
            return

        conn = sqlite3.connect(LOG_DB)
        c = conn.cursor()
        c.execute('''
            SELECT id, timestamp, callsign, name, location, status, comments 
            FROM checkins 
            WHERE net_id = ? 
            ORDER BY id ASC
        ''', (self.current_net_id,))
        rows = c.fetchall()
        conn.close()

        self.table.setRowCount(0)
        for row_idx, row_data in enumerate(rows):
            checkin_id = row_data[0]
            timestamp, callsign, name, location, status_val, comments = row_data[1:]
            callsign = callsign or "Unknown"

            self.table.insertRow(row_idx)

            # Columns 0-3: Text items
            self.table.setItem(row_idx, 0, QTableWidgetItem(str(timestamp or "")))
            self.table.setItem(row_idx, 1, QTableWidgetItem(str(callsign)))
            self.table.setItem(row_idx, 2, QTableWidgetItem(str(name or "")))
            self.table.setItem(row_idx, 3, QTableWidgetItem(str(location or "")))

            # Column 4: Editable Status ComboBox
            cell_combo = QComboBox()
            cell_combo.addItems(STATUS_OPTIONS)
            curr_idx = cell_combo.findText(status_val or "General")
            if curr_idx >= 0:
                cell_combo.setCurrentIndex(curr_idx)
            cell_combo.currentTextChanged.connect(lambda text, cid=checkin_id: self.on_table_status_changed(cid, text))
            self.table.setCellWidget(row_idx, 4, cell_combo)

            # Column 5: Comments
            self.table.setItem(row_idx, 5, QTableWidgetItem(str(comments or "")))

            # Column 6: Delete Button
            delete_btn = QPushButton("✕")
            delete_btn.setToolTip("Delete this check-in")
            delete_btn.setStyleSheet("""
                QPushButton {
                    color: red; 
                    font-weight: bold; 
                    font-size: 14px; 
                    border: none;
                    background: transparent;
                }
                QPushButton:hover {
                    color: darkred;
                    background-color: #ffeeee;
                    border-radius: 3px;
                }
            """)
            delete_btn.clicked.connect(lambda _, cid=checkin_id, cs=callsign: self.confirm_delete_checkin(cid, cs))
            self.table.setCellWidget(row_idx, 6, delete_btn)

    def start_uls_update(self):
        self.progress.show()
        self.thread = ULSUpdateThread()
        self.thread.status_signal.connect(self.status_label.setText)
        self.thread.finished_signal.connect(self.update_finished)
        self.thread.start()

    def update_finished(self):
        self.progress.hide()
        QMessageBox.information(self, "Update", "FCC ULS Database updated successfully!")

    def export_csv(self):
        if not self.current_net_id:
            return

        self.update_net_header_info()
        path, _ = QFileDialog.getSaveFileName(self, "Save Net CSV", "", "CSV Files (*.csv)")
        if path:
            conn = sqlite3.connect(LOG_DB)
            c = conn.cursor()
            
            c.execute('SELECT net_name, control_callsign, control_operator, start_time FROM nets WHERE net_id = ?', (self.current_net_id,))
            net_meta = c.fetchone()

            c.execute('SELECT id, timestamp, callsign, name, location, status, comments FROM checkins WHERE net_id = ?', (self.current_net_id,))
            rows = c.fetchall()
            conn.close()

            with open(path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Net Name:", net_meta[0], "Control Call:", net_meta[1], "Control Op:", net_meta[2], "Started:", net_meta[3]])
                writer.writerow([])
                writer.writerow(["ID", "Timestamp", "Callsign", "Name", "Location", "Status", "Comments"])
                writer.writerows(rows)
            QMessageBox.information(self, "Export Complete", f"Session #{self.current_net_id} logs exported to {path}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = NetLoggerApp()
    window.show()
    sys.exit(app.exec())
