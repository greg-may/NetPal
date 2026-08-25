import sqlite3
import csv
import os
import zipfile
import urllib.request
import io

ULS_DB = 'uls_cache.db'
TEMP_DB = 'uls_temp.db'

def init_db():
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
    init_db()
    
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
        
        # HD.dat
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

        # AM.dat
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

        # EN.dat (Fixed to match 8 columns)
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

                        # 8 elements matching en_raw schema
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
    # Exact join query including unique_system_identifier (usi)
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
        return None
        
    conn = sqlite3.connect(ULS_DB)
    c = conn.cursor()
    c.execute('''
        SELECT first_name, last_name, city, state 
        FROM uls_callsigns 
        WHERE callsign = ?
    ''', (callsign.strip().upper(),))
    
    result = c.fetchone()
    conn.close()
    
    if result:
        return {
            'first_name': result[0],
            'last_name': result[1],
            'city': result[2],
            'state': result[3]
        }
    return None

if __name__ == '__main__':
    def print_status(msg):
        print(f"[ULS Build] {msg}")

    update_uls_db(status_callback=print_status)
    
    for test_call in ["W3UP", "N3SAM"]:
        res = query_uls(test_call)
        print(f"Test Lookup for {test_call}: {res}")
