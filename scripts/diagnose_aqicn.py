import json
import os
import sys
import urllib.request as u

sys.path.insert(0, "scripts")
from clock_starter import load_dotenv

load_dotenv()
token = os.environ["AQICN_TOKEN"]

paths = [
    "geo:33.6844;73.0479",      # what clock_starter sends now
    "geo:33.6844%3B73.0479",    # semicolon percent-encoded
    "islamabad",
    "lahore",
    "here",
]
for p in paths:
    url = "https://api.waqi.info/feed/" + p + "/?token=" + token
    try:
        d = json.load(u.urlopen(url, timeout=20))
    except Exception as e:
        print(f"{p:28} ERROR {e}")
        continue
    data = d.get("data")
    if isinstance(data, dict):
        city = (data.get("city") or {}).get("name")
        geo = (data.get("city") or {}).get("geo")
        aqi, idx = data.get("aqi"), data.get("idx")
        print(f"{p:28} {d.get('status'):6} aqi={aqi} idx={idx} {city} {geo}")
    else:
        print(f"{p:28} {d.get('status'):6} {data}")
