#!/usr/bin/env python3
"""
Smart Public Area Safety Node - Raspberry Pi 4
Sensors: Mic, IR sensor, Smoke sensor (digital), DHT11
Actions: LEDs, Buzzer, Camera capture
Connection: MQTT to ThingsBoard
"""

import time
import threading
import json
import os
import queue
import base64
from datetime import datetime

import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import Adafruit_DHT
import sounddevice as sd
import numpy as np
import cv2

# load config
import importlib
cfg = importlib.import_module("config")

# ----------------- GPIO setup -----------------
GPIO.setmode(GPIO.BCM)
GPIO.setup(cfg.PIN_IR, GPIO.IN)
GPIO.setup(cfg.PIN_SMOKE, GPIO.IN)
GPIO.setup(cfg.PIN_BUZZER, GPIO.OUT)
GPIO.setup(cfg.PIN_LED_GREEN, GPIO.OUT)
GPIO.setup(cfg.PIN_LED_YELLOW, GPIO.OUT)
GPIO.setup(cfg.PIN_LED_RED, GPIO.OUT)

buzzer_pwm = GPIO.PWM(cfg.PIN_BUZZER, 1000)
buzzer_pwm.start(0)

def led_set(green=False, yellow=False, red=False):
    GPIO.output(cfg.PIN_LED_GREEN, green)
    GPIO.output(cfg.PIN_LED_YELLOW, yellow)
    GPIO.output(cfg.PIN_LED_RED, red)

# ----------------- MQTT -----------------
client = mqtt.Client()
client.username_pw_set(cfg.MQTT_USERNAME, cfg.MQTT_PASSWORD)
client.connect(cfg.MQTT_BROKER, cfg.MQTT_PORT, 60)
client.loop_start()

def publish_telemetry(data: dict):
    try:
        payload = json.dumps(data)
        client.publish(cfg.TELEMETRY_TOPIC, payload)
    except Exception as e:
        print("MQTT error:", e)

# ----------------- Camera -----------------
if cfg.CAMERA_ENABLED:
    from picamera import PiCamera
    camera = PiCamera()
    camera.resolution = cfg.CAMERA_RESOLUTION
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
    frames = int(cfg.AUDIO_SAMPLE_RATE * (cfg.AUDIO_BLOCK_MS / 1000.0))
    try:
        data = sd.rec(frames=frames, samplerate=cfg.AUDIO_SAMPLE_RATE,
                      channels=1, dtype="float32")
        sd.wait()
        samples = np.squeeze(data)
        return rms_db(samples) + 90  # calibrated offset
    except Exception as e:
        print("Audio error:", e)
        return 0

# ----------------- DHT11 -----------------
def read_dht11():
    h, t = Adafruit_DHT.read_retry(Adafruit_DHT.DHT11, cfg.PIN_DHT11)
    return t, h

# ----------------- Event handling -----------------
event_queue = queue.Queue()

def beep(pattern="short"):
    if pattern == "short":
        buzzer_pwm.ChangeDutyCycle(50); time.sleep(0.2)
        buzzer_pwm.ChangeDutyCycle(0)
    elif pattern == "long":
        buzzer_pwm.ChangeDutyCycle(50); time.sleep(1)
        buzzer_pwm.ChangeDutyCycle(0)

def handle_event(ev):
    print("EVENT:", ev)
    led_set(yellow=True)
    beep("short")

    telemetry = {"event": ev, "ts": datetime.utcnow().isoformat() + "Z"}

    if cfg.CAMERA_ENABLED and ev.get("capture", False):
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
        if db >= cfg.SOUND_DB_THRESHOLD:
            if GPIO.input(cfg.PIN_IR) == GPIO.HIGH:  # object present
                event_queue.put({"type": "noise", "db": db, "capture": True})
            else:
                event_queue.put({"type": "noise", "db": db, "capture": False})
            time.sleep(2)

def loop_ir():
    last = 0
    while True:
        state = GPIO.input(cfg.PIN_IR)
        if state and not last:
            event_queue.put({"type": "motion", "capture": True})
        last = state
        time.sleep(0.2)

def loop_smoke():
    while True:
        state = GPIO.input(cfg.PIN_SMOKE)
        publish_telemetry({"smoke": int(state)})
        if state == 1:
            event_queue.put({"type": "smoke", "capture": False})
        time.sleep(3)

def loop_dht():
    while True:
        t, h = read_dht11()
        if t is not None:
            publish_telemetry({"temperature_c": t, "humidity": h})
            if t > cfg.TEMP_THRESHOLD:
                event_queue.put({"type": "high_temp", "temp": t})
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
    GPIO.cleanup()
    client.loop_stop()
    print("Stopped.")
