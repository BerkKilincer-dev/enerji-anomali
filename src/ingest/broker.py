"""MQTT client set-up shared by the ingest service and the replay tool."""

from __future__ import annotations

from collections.abc import Callable

import paho.mqtt.client as mqtt

from src.config import get_settings


def start_client(
    on_connect: Callable | None = None, on_message: Callable | None = None
) -> mqtt.Client:
    """Connected client with its network loop running in a background thread."""
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if s.mqtt_user:
        client.username_pw_set(s.mqtt_user, s.mqtt_password)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(s.mqtt_host, s.mqtt_port)
    client.loop_start()
    return client
