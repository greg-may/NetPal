# Net Control Logger

A lightweight, local-first desktop application built with Python and PyQt6 for amateur radio Net Control Operators. It simplifies logging check-ins during live radio nets, supports local timestamping, exports structured CSV net reports, and automatically queries the official FCC ULS database for offline callsign lookups.

---

## Features

* **Instant Callsign Lookup:** Automatically queries name and city/state (e.g., `Norristown, PA`) from a local FCC ULS cache as you type.
* **Custom Call & Location Overrides:** Edits made to a station's preferred name or location are saved locally for future net sessions.
* **FCC ULS Auto-Updater:** Downloads and parses the official FCC Amateur Radio ZIP dataset (`EN.dat`) directly into a fast SQLite cache without overwriting your manual overrides.
* **Multi-Net Management:** Start new logging sessions or reload historical nets to review or re-export logs.
* **CSV Exporting:** Generates formatted CSV summary reports containing net metadata (Net Name, Net Control Call, Operator) alongside complete check-in records.
* **Local Timestamping:** Displays log entry timestamps in your system's local time zone.

---

## Installation & Setup

### Prerequisites
* **Python 3.9+** installed on your system.

### Running from Source

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YOUR_USERNAME/net-control-logger.git](https://github.com/YOUR_USERNAME/net-control-logger.git)
   cd net-control-logger
