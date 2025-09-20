#!/usr/bin/env python3
"""
Smart Public Area Safety Node - Raspberry Pi 4
Sensors: Mic (digital), IR sensor (digital), Smoke sensor (digital), DHT11
Actions: LEDs, Buzzer, Camera capture
Connection: Blynk + Secure MQTT (HiveMQ Cloud)
"""

import time
import threading
import json
import os
import subprocess
from datetime import datetime
import ssl

import blynklib
import paho.mqtt.client as mqtt
from gpiozero import LED, Buzzer, DigitalInputDevice
import board
import adafruit_dht

# ----------------- Config -----------------
BLYNK_AUTH_TOKEN = "yI2np_RtdvbitTInHN0DQa120quz1JT4"

# --- HiveMQ Cloud secure MQTT settings ---
MQTT_BROKER = "1b14277679694b60aa7438f6116823b6.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_TOPIC = "team5/public_area"
MQTT_USERNAME = "Nada131"
MQTT_PASSWORD = "Sic1122004"

CAMERA_ENABLED = True
TEMP_THRESHOLD = 60.0

# GPIO pins
PIN_DHT11 = board.D4
PIN_IR = 17
PIN_SMOKE = 16
PIN_SOUND = 20
PIN_BUZZER = 27
PIN_LED_GREEN = 5
PIN_LED_YELLOW = 6
PIN_LED_RED = 13

# ----------------- GPIO Setup -----------------
buzzer = Buzzer(PIN_BUZZER)
led_green = LED(PIN_LED_GREEN)
led_yellow = LED(PIN_LED_YELLOW)
led_red = LED(PIN_LED_RED)
ir_sensor = DigitalInputDevice(PIN_IR, pull_up=False)
smoke_sensor = DigitalInputDevice(PIN_SMOKE, pull_up=False)
sound_sensor = DigitalInputDevice(PIN_SOUND, pull_up=False)

dht_device = adafruit_dht.DHT11(PIN_DHT11)

def led_set(green=False, yellow=False, red=False):
    led_green.value = 1 if green else 0
    led_yellow.value = 1 if yellow else 0
    led_red.value = 1 if red else 0

# ----------------- Blynk Setup -----------------
blynk = blynklib.Blynk(BLYNK_AUTH_TOKEN)

def publish_to_blynk(event: dict):
    print("[BLYNK]", event)
    if event["type"] == "smoke":
        blynk.notify("Smoke detected in monitored area!")
    elif event["type"] == "noise":
        blynk.notify("Loud noise detected!")
    elif event["type"] == "high_temp":
        blynk.notify("High temperature detected!")

# ----------------- MQTT Setup -----------------
client = mqtt.Client()
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
client.tls_set(tls_version=ssl.PROTOCOL_TLS)  # enable TLS/SSL
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

def publish_mqtt(data: dict):
    try:
        payload = json.dumps(data)
        client.publish(MQTT_TOPIC, payload)
        print("[MQTT] Published:", payload)
    except Exception as e:
        print("[MQTT ERROR]", e)

# ----------------- Camera -----------------
def capture_image(tag="event"):
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    os.makedirs("captures", exist_ok=True)
    filename = f"captures/{tag}_{ts}.jpg"
    cmd = ["rpicam-still", "-o", filename, "--width", "640", "--height", "480", "--timeout", "1000", "--nopreview"]
    try:
        subprocess.run(cmd, check=True)
        print("[CAMERA] Photo saved:", filename)
    except subprocess.CalledProcessError as e:
        print("[CAMERA ERROR]", e)
    return filename

# ----------------- Event Handling -----------------
def beep(pattern="short"):
    if pattern == "short":
        buzzer.on(); time.sleep(0.2); buzzer.off()
    elif pattern == "long":
        buzzer.on(); time.sleep(1); buzzer.off()

def handle_event(ev):
    print("[EVENT]", ev)
    led_set(yellow=True)
    beep("short")

    telemetry = {"event": ev, "ts": datetime.utcnow().isoformat() + "Z"}

    if CAMERA_ENABLED and ev.get("capture", False):
        img = capture_image(ev["type"])
        telemetry["image_saved"] = img

    publish_to_blynk(ev)
    publish_mqtt(telemetry)

    time.sleep(0.5)
    led_set(green=True)

# ----------------- Sensor Loops -----------------
def loop_sound():
    while True:
        if sound_sensor.value == 1:
            event = {"type": "noise", "capture": True}
            handle_event(event)
            time.sleep(3)
        time.sleep(0.1)

def loop_ir():
    last = 0
    while True:
        state = ir_sensor.value
        if state and not last:
            handle_event({"type": "motion", "capture": True})
        last = state
        time.sleep(0.2)

def loop_smoke():
    while True:
        state = smoke_sensor.value
        if state == 1:
            handle_event({"type": "smoke", "capture": False})
        time.sleep(3)

def loop_dht():
    while True:
        try:
            t = dht_device.temperature
            h = dht_device.humidity
            if t is not None and h is not None:
                publish_mqtt({"temperature_c": t, "humidity": h})
                if t > TEMP_THRESHOLD:
                    handle_event({"type": "high_temp", "temp": t})
        except RuntimeError:
            pass
        time.sleep(10)

# ----------------- Start -----------------
threads = [
    threading.Thread(target=loop_sound, daemon=True),
    threading.Thread(target=loop_ir, daemon=True),
    threading.Thread(target=loop_smoke, daemon=True),
    threading.Thread(target=loop_dht, daemon=True)
]

for t in threads: 
    t.start()

led_set(green=True)
print("System running. Ctrl+C to exit.")
try:
    while True:
        blynk.run()
        time.sleep(0.1)
except KeyboardInterrupt:
    client.loop_stop()
    print("Stopped.")

