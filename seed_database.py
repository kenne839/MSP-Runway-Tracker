import urllib.request
import json
import csv
import io
import os

# The authoritative OpenSky Network Metadata Database
DB_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "msp_aircraft_db.json")

def seed_database():
    print("Downloading OpenSky authoritative aircraft database (~35MB)...")
    print("This may take a minute depending on your connection.")
    try:
        # Standard user-agent ensures the server doesn't reject the script
        req = urllib.request.Request(DB_URL, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req) as response:
            csv_data = response.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"Failed to download database: {e}")
        return

    print("Parsing commercial and private aircraft types...")
    csv_reader = csv.reader(io.StringIO(csv_data))
    
    header = next(csv_reader)
    
    # Dynamically locate columns based on OpenSky's header format
    icao_idx = header.index("icao24") if "icao24" in header else 0
    
    if "typecode" in header:
        type_idx = header.index("typecode")
    elif "model" in header:
        type_idx = header.index("model")
    else:
        type_idx = 5 # OpenSky default column for aircraft type

    aircraft_db = {}
    valid_count = 0
    commercial_count = 0
    
    # Common MSP commercial types for verification
    commercial_check = {"B738", "A321", "A320", "A319", "A220", "E75L", "CRJ9", "B752", "B712"}
    
    for row in csv_reader:
        if len(row) > max(icao_idx, type_idx):
            icao24 = row[icao_idx].strip().lower()
            ac_type = row[type_idx].strip()
            
            if ac_type and ac_type != "":
                aircraft_db[icao24] = ac_type
                valid_count += 1
                if ac_type in commercial_check:
                    commercial_count += 1
                
    print(f"Extracted {valid_count:,} total known aircraft.")
    print(f"Verified {commercial_count:,} standard commercial airliners in the database.")
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(aircraft_db, f)
        
    print(f"\nSuccess! Saved to {OUTPUT_FILE}. Your main script is now fully pre-loaded.")

if __name__ == "__main__":
    seed_database()