"""Sanal sayaç: LEAD okumalarını MQTT üzerinden 'canlı' gibi gönderir.

Kullanım:
    python -m src.ingest.replay --csv data/test_buildings.csv --speed 0.5
Sahibi: Veri mühendisi. Yapılacaklar: aynı saatin tüm binalarını birlikte gönderme,
başlat/durdur, demo için anomali enjeksiyonu.
"""

import argparse
import json
import os
import time

import paho.mqtt.client as mqtt
import pandas as pd


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--speed", type=float, default=0.5, help="Bir saatlik veri kaç saniyede")
    args = p.parse_args()

    df = pd.read_csv(args.csv, parse_dates=["timestamp"]).sort_values("timestamp")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(os.getenv("MQTT_HOST", "localhost"), int(os.getenv("MQTT_PORT", 1883)))

    for ts, group in df.groupby("timestamp"):
        for _, row in group.iterrows():
            msg = {"building_id": int(row.building_id), "ts": ts.isoformat(),
                   "kwh": None if pd.isna(row.meter_reading) else float(row.meter_reading)}
            client.publish(f"meters/{int(row.building_id)}", json.dumps(msg))
        time.sleep(args.speed)


if __name__ == "__main__":
    main()
