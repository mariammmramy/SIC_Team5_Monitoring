#!/usr/bin/env python3
"""
Smart Public Area Safety Node - Raspberry Pi 4
Sensors: Mic (digital), IR sensor (digital), Smoke sensor (digital), DHT11
Actions: LEDs, Buzzer, Camera capture
Connection: Blynk IoT (new library) + HiveMQ MQTT
"""

import time, threading, os, subprocess, json, ssl
from datetime import datetime
import BlynkLib
import paho.mqtt.client as mqtt
from gpiozero import LED, Buzzer, DigitalInputDevice
import board, adafruit_dht

# ----------------- Config -----------------
BLYNK_AUTH = "yI2np_RtdvbitTInHN0DQa120quz1JT4"

# Connect to new Blynk IoT cloud server
blynk = BlynkLib.Blynk(BLYNK_AUTH, server="blynk.cloud", port=80)

# Virtual pin mapping (edit to match your dashboard)
V0_TEMP = 0
V1_HUM = 1
V2_RED = 2
V3_YELLOW = 3
V4_GREEN = 4
V5_BUZZER = 5
V6_SMOKE = 6
V7_SOUND = 7
V8_MOTION = 8

# MQTT broker
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

# ----------------- MQTT Setup -----------------
client = mqtt.Client()
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
client.tls_set(tls_version=ssl.PROTOCOL_TLS)
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

def publish_mqtt(data: dict):
    try:
        payload = json.dumps(data)
        client.publish(MQTT_TOPIC, payload)
        print("[MQTT] Published:", payload)
    except Exception as e:
        print("[MQTT ERROR]", e)

# ----------------- Events -----------------
def handle_event(ev_type, capture=False, temp=None):
    print("[EVENT]", ev_type)

    if ev_type == "smoke":
        blynk.virtual_write(V6_SMOKE, 1)
    elif ev_type == "noise":
        blynk.virtual_write(V7_SOUND, 1)
    elif ev_type == "motion":
        blynk.virtual_write(V8_MOTION, 1)
    elif ev_type == "high_temp":
        blynk.virtual_write(V0_TEMP, temp)

    # buzzer + LEDs
    buzzer.on()
    led_red.on()
    time.sleep(1)
    buzzer.off()
    led_red.off()

    if capture and CAMERA_ENABLED:
        capture_image(ev_type)

    publish_mqtt({"event": ev_type, "temp": temp})

# ----------------- Sensor Loops -----------------
def loop_sound():
    while True:
        if sound_sensor.value == 1:
            handle_event("noise", capture=True)
            time.sleep(3)
        time.sleep(0.1)

def loop_ir():
    last = 0
    while True:
        state = ir_sensor.value
        if state and not last:
            handle_event("motion", capture=True)
        last = state
        time.sleep(0.2)

def loop_smoke():
    while True:
        if smoke_sensor.value == 1:
            handle_event("smoke")
        time.sleep(3)

def loop_dht():
    while True:
        try:
            t = dht_device.temperature
            h = dht_device.humidity
            if t is not None and h is not None:
                blynk.virtual_write(V0_TEMP, t)
                blynk.virtual_write(V1_HUM, h)
                publish_mqtt({"temperature_c": t, "humidity": h})
                if t > TEMP_THRESHOLD:
                    handle_event("high_temp", temp=t)
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

print("System running. Ctrl+C to exit.")
try:
    while True:
        blynk.run()   # keeps Blynk connection alive
        time.sleep(0.1)
except KeyboardInterrupt:
    client.loop_stop()
    print("Stopped.")
