import sqlite3
import zipfile
import urllib.request
import csv
import os

ULS_DB = "uls_cache.db"
LOG_DB = "net_logs.db"

def init_databases():
    # Local Net Logs Table
    conn = sqlite3.connect(LOG_DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            callsign TEXT,
            name TEXT,
            location TEXT,
            comments TEXT
        )
    ''')
    conn.commit()
    conn.close()

    # ULS Lookup Table
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

    if status_callback: status_callback("Indexing records into SQLite...")
    conn = sqlite3.connect(ULS_DB)
    c = conn.cursor()
    
    to_db = []
    with open('EN.dat', 'r', encoding='latin-1') as f:
        reader = csv.reader(f, delimiter='|')
        for row in reader:
            if len(row) > 18 and row[0] == 'EN':
                call = row[4].strip()
                if call:
                    to_db.append((call, row[8].strip(), row[10].strip(), row[17].strip(), row[18].strip()))

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
    conn = sqlite3.connect(ULS_DB)
    c = conn.cursor()
    c.execute('SELECT first_name, last_name, city, state FROM uls_callsigns WHERE callsign = ?', (callsign.upper(),))
    res = c.fetchone()
    conn.close()
    if res:
        name = f"{res[0]} {res[1]}".strip()
        loc = f"{res[2]}, {res[3]}".strip(", ")
        return name, loc
    return "", ""
