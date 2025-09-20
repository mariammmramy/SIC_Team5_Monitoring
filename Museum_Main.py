#!/usr/bin/env python3
"""
Smart Public Area Safety Node - Raspberry Pi 4
Sensors: Mic, IR sensor (digital), Smoke sensor (digital), DHT11
Actions: LEDs, Buzzer, Camera capture
Connection: MQTT (ThingsBoard)
"""

import time
import threading
import json
import os
import queue
import base64
from datetime import datetime

import paho.mqtt.client as mqtt
from gpiozero import LED, Buzzer, DigitalInputDevice
import sounddevice as sd
import numpy as np
import cv2
import board
import adafruit_dht

# ----------------- Config -----------------
MQTT_BROKER = "demo.thingsboard.io"
MQTT_PORT = 1883
MQTT_USERNAME = "YOUR_THINGSBOARD_DEVICE_TOKEN"
MQTT_PASSWORD = ""
TELEMETRY_TOPIC = "v1/devices/me/telemetry"

AUDIO_SAMPLE_RATE = 16000
AUDIO_BLOCK_MS = 200
SOUND_DB_THRESHOLD = 70.0

CAMERA_ENABLED = True
CAMERA_RESOLUTION = (640, 480)
TEMP_THRESHOLD = 60.0

# GPIO pins
PIN_DHT11 = board.D4
PIN_IR = 17
PIN_SMOKE = 22
PIN_BUZZER = 27
PIN_LED_GREEN = 5
PIN_LED_YELLOW = 6
PIN_LED_RED = 13

# ----------------- GPIOZero setup -----------------
buzzer = Buzzer(PIN_BUZZER)
led_green = LED(PIN_LED_GREEN)
led_yellow = LED(PIN_LED_YELLOW)
led_red = LED(PIN_LED_RED)
ir_sensor = DigitalInputDevice(PIN_IR, pull_up=False)
smoke_sensor = DigitalInputDevice(PIN_SMOKE, pull_up=False)

dht_device = adafruit_dht.DHT11(PIN_DHT11)

def led_set(green=False, yellow=False, red=False):
    led_green.value = 1 if green else 0
    led_yellow.value = 1 if yellow else 0
    led_red.value = 1 if red else 0

# ----------------- MQTT -----------------
client = mqtt.Client()
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

def publish_telemetry(data: dict):
    try:
        payload = json.dumps(data)
        client.publish(TELEMETRY_TOPIC, payload)
    except Exception as e:
        print("MQTT error:", e)

# ----------------- Camera -----------------
if CAMERA_ENABLED:
    from picamera import PiCamera
    camera = PiCamera()
    camera.resolution = CAMERA_RESOLUTION
    time.sleep(1)

face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades +
                                     "haarcascade_frontalface_default.xml")

def capture_image(tag="event"):
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    os.makedirs("captures", exist_ok=True)
    filename = f"captures/{tag}_{ts}.jpg"
    camera.capture(filename)

    img = cv2.imread(filename)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.1, 4)
    has_face = len(faces) > 0

    return filename, has_face

# ----------------- Audio processing -----------------
def rms_db(samples):
    rms = np.sqrt(np.mean(np.square(samples), dtype=np.float64))
    if rms <= 0:
        return -100.0
    return 20 * np.log10(rms)

def sample_audio_block():
    frames = int(AUDIO_SAMPLE_RATE * (AUDIO_BLOCK_MS / 1000.0))
    try:
        data = sd.rec(frames=frames, samplerate=AUDIO_SAMPLE_RATE,
                      channels=1, dtype="float32")
        sd.wait()
        samples = np.squeeze(data)
        return rms_db(samples) + 90  # calibration offset
    except Exception as e:
        print("Audio error:", e)
        return 0

# ----------------- Event handling -----------------
event_queue = queue.Queue()

def beep(pattern="short"):
    if pattern == "short":
        buzzer.on(); time.sleep(0.2); buzzer.off()
    elif pattern == "long":
        buzzer.on(); time.sleep(1); buzzer.off()

def handle_event(ev):
    print("EVENT:", ev)
    led_set(yellow=True)
    beep("short")

    telemetry = {"event": ev, "ts": datetime.utcnow().isoformat() + "Z"}

    if CAMERA_ENABLED and ev.get("capture", False):
        img, has_face = capture_image(ev["type"])
        telemetry["image_saved"] = img
        telemetry["image_has_face"] = has_face
        if has_face:
            with open(img, "rb") as f:
                telemetry["image_b64"] = base64.b64encode(f.read()).decode("utf-8")

    publish_telemetry(telemetry)
    time.sleep(0.5)
    led_set(green=True)

# ----------------- Sensor loops -----------------
def loop_sound():
    while True:
        db = sample_audio_block()
        if db >= SOUND_DB_THRESHOLD:
            if ir_sensor.value:  # object present
                event_queue.put({"type": "noise", "db": db, "capture": True})
            else:
                event_queue.put({"type": "noise", "db": db, "capture": False})
            time.sleep(2)

def loop_ir():
    last = 0
    while True:
        state = ir_sensor.value
        if state and not last:
            event_queue.put({"type": "motion", "capture": True})
        last = state
        time.sleep(0.2)

def loop_smoke():
    while True:
        state = smoke_sensor.value
        publish_telemetry({"smoke": int(state)})
        if state == 1:
            event_queue.put({"type": "smoke", "capture": False})
        time.sleep(3)

def loop_dht():
    while True:
        try:
            t = dht_device.temperature
            h = dht_device.humidity
            if t is not None and h is not None:
                publish_telemetry({"temperature_c": t, "humidity": h})
                if t > TEMP_THRESHOLD:
                    event_queue.put({"type": "high_temp", "temp": t})
        except RuntimeError:
            pass  # DHT sometimes fails, just retry
        time.sleep(10)

def dispatcher():
    while True:
        ev = event_queue.get()
        handle_event(ev)
        event_queue.task_done()

# ----------------- Start -----------------
threads = [
    threading.Thread(target=loop_sound, daemon=True),
    threading.Thread(target=loop_ir, daemon=True),
    threading.Thread(target=loop_smoke, daemon=True),
    threading.Thread(target=loop_dht, daemon=True),
    threading.Thread(target=dispatcher, daemon=True)
]

for t in threads: t.start()

led_set(green=True)
print("System running. Ctrl+C to exit.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    client.loop_stop()
    print("Stopped.")

