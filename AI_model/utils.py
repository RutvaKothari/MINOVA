import json
import os
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "free_factors.json")

# CLIMATIQ_API_KEY = "YOUR_FREE_KEY_HERE" # Get one at climatiq.io

try:
    with open(DB_PATH, "r") as f:
        FREE_EMISSION_DB = json.load(f)
except FileNotFoundError:
    FREE_EMISSION_DB = {}

def get_smart_defaults(material_name: str) -> float:
    """Enhanced lookup with Fallback logic."""
    if not material_name:
        return 0.5
    
    # 1. Check local JSON first
    local_val = FREE_EMISSION_DB.get(material_name.lower())
    if local_val:
        return local_val

    # 2. (Future AI Step) Here you would call Climatiq API or an ML model
    # For now, we return 0.5 but this is where the 'AI' hook lives
    return 0.5