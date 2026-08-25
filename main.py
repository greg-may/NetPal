import sys
import csv
import sqlite3
import zipfile
import urllib.request
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTableWidget, QTableWidgetItem, QHeaderView, 
                             QMessageBox, QFileDialog, QProgressBar, QFrame,
                             QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
                             QComboBox)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QEvent

ULS_DB = "uls_cache.db"
LOG_DB = "net_logs.db"
APP_VERSION = "v1.04"

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
            callsign TEXT PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            city TEXT,
            state TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_overrides (
            callsign TEXT PRIMARY KEY,
            preferred_name TEXT,
            preferred_location TEXT
        )
    ''')
    conn.commit()
    conn.close()

def update_uls_db(status_callback=None):
    url = "https://data.fcc.gov/download/pub/uls/complete/l_amat.zip"
    zip_path = "l_amat.zip"
    
    if status_callback: status_callback("Downloading FCC ULS Database...")
    urllib.request.urlretrieve(url, zip_path)
    
    if status_callback: status_callback("Extracting EN.dat...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extract('EN.dat')

    if status_callback: status_callback("Appending new records into SQLite...")
    conn = sqlite3.connect(ULS_DB)
    c = conn.cursor()
    
    to_db = []
    with open('EN.dat', 'r', encoding='latin-1') as f:
        reader = csv.reader(f, delimiter='|')
        for row in reader:
            if len(row) > 17 and row[0] == 'EN':
                call = row[4].strip()
                city = row[16].strip()
                state = row[17].strip()
                if call:
                    to_db.append((call, row[8].strip(), row[10].strip(), city, state))

    c.execute('DELETE FROM uls_callsigns')

    c.executemany('''
        INSERT OR REPLACE INTO uls_callsigns (callsign, first_name, last_name, city, state)
        VALUES (?, ?, ?, ?, ?)
    ''', to_db)
    conn.commit()
    conn.close()

    os.remove(zip_path)
    os.remove('EN.dat')
    if status_callback: status_callback("Update complete!")

def query_uls(callsign):
    if not os.path.exists(ULS_DB):
        return "", ""
    
    callsign = callsign.upper()
    conn = sqlite3.connect(ULS_DB)
    c = conn.cursor()
    
    c.execute('SELECT preferred_name, preferred_location FROM user_overrides WHERE callsign = ?', (callsign,))
    override = c.fetchone()
    if override:
        conn.close()
        return override[0], override[1]

    c.execute('SELECT first_name, last_name, city, state FROM uls_callsigns WHERE callsign = ?', (callsign,))
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
        
        init_databases()
        self.init_ui()
        self.start_new_net_session()

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
        self.status_dropdown.addItems(["General", "Mobile", "Portable", "Short Time", "In/Out"])
        # Event filter allows pressing Enter while focused on dropdown to log contact
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

        # Table Display Area (6 Columns)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Timestamp", "Callsign", "Name", "Location", "Status", "Comments"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

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
        # Capture Enter keypresses directly on the Status dropdown
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
        self.status_dropdown.setCurrentIndex(0)  # Default to 'General'
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

        # Check callsign and active session presence before logging
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
        self.status_dropdown.setCurrentIndex(0)  # Reset dropdown to General
        self.call_input.setFocus()
        self.load_session_logs()

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
            display_data = row_data[1:]  # [Timestamp, Callsign, Name, Location, Status, Comments]
            
            self.table.insertRow(row_idx)
            for col_idx, value in enumerate(display_data):
                item = QTableWidgetItem(str(value or ""))
                if col_idx == 0:
                    item.setData(Qt.ItemDataRole.UserRole, checkin_id)
                self.table.setItem(row_idx, col_idx, item)

    def start_uls_update(self):
        self.progress.show()
        self.thread = ULSUpdateThread()
        self.thread.status_signal.connect(self.status_label.setText)
        self.thread.finished_signal.connect(self.update_finished)
        self.thread.start()

    def update_finished(self):
        self.progress.hide()
        QMessageBox.information(self, "Update", "FCC ULS Database updated without disturbing existing records!")

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
