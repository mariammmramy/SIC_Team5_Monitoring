#!/usr/bin/env python3
"""
Smart Public Area Monitoring - Raspberry Pi 4
Sensors: Sound sensor (digital), IR sensor, Smoke sensor, DHT11
Actions: Buzzer, LEDs, Camera (rpicam-still + face detection)
Platform: Blynk (IoT integration)
"""

import time
import json
import os
import subprocess
from datetime import datetime
import threading

import cv2
import board
import adafruit_dht
from gpiozero import LED, Buzzer, DigitalInputDevice
from blynklib import Blynk

# ------------------ Config ------------------
BLYNK_AUTH_TOKEN = "YOUR_BLYNK_AUTH_TOKEN"

# Pins
PIN_SOUND = 18
PIN_IR = 17
PIN_SMOKE = 22
PIN_BUZZER = 27
PIN_LED_GREEN = 5
PIN_LED_YELLOW = 6
PIN_LED_RED = 13
PIN_DHT11 = board.D4

# Camera
CAPTURE_DIR = "captures"
os.makedirs(CAPTURE_DIR, exist_ok=True)

# ------------------ GPIO Setup ------------------
sound_sensor = DigitalInputDevice(PIN_SOUND, pull_up=False)
ir_sensor = DigitalInputDevice(PIN_IR, pull_up=False)
smoke_sensor = DigitalInputDevice(PIN_SMOKE, pull_up=False)

buzzer = Buzzer(PIN_BUZZER)
led_green = LED(PIN_LED_GREEN)
led_yellow = LED(PIN_LED_YELLOW)
led_red = LED(PIN_LED_RED)

dht_device = adafruit_dht.DHT11(PIN_DHT11)

# Face detection model
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# ------------------ Blynk Setup ------------------
blynk = Blynk(BLYNK_AUTH_TOKEN)

def publish_to_blynk(data: dict):
    """Send key-value telemetry to Blynk virtual pins"""
    for key, value in data.items():
        try:
            if key == "temperature":
                blynk.virtual_write(1, value)  # V1 = temperature
            elif key == "humidity":
                blynk.virtual_write(2, value)  # V2 = humidity
            elif key == "smoke":
                blynk.virtual_write(3, value)  # V3 = smoke
            elif key == "event":
                blynk.virtual_write(4, value)  # V4 = events log
        except Exception as e:
            print("[BLYNK ERROR]", e)

# ------------------ Helpers ------------------
def timestamp_utc():
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

def capture_image_with_face(tag="event"):
    ts = timestamp_utc()
    filename = f"{CAPTURE_DIR}/{tag}_{ts}.jpg"

    # Capture with rpicam-still
    cmd = [
        "rpicam-still",
        "-o", filename,
        "--width", "1280",
        "--height", "720",
        "--timeout", "1000",
        "--nopreview"
    ]
    try:
        subprocess.run(cmd, check=True)
        print(f"[INFO] Photo captured: {filename}")
    except subprocess.CalledProcessError as e:
        print("[ERROR] Failed to capture photo:", e)
        return filename, False

    # Face detection
    img = cv2.imread(filename)
    if img is None:
        return filename, False

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.1, 4)
    has_face = len(faces) > 0

    return filename, has_face

def beep(pattern="short"):
    if pattern == "short":
        buzzer.on(); time.sleep(0.2); buzzer.off()
    elif pattern == "long":
        buzzer.on(); time.sleep(1); buzzer.off()

def led_set(green=False, yellow=False, red=False):
    led_green.value = 1 if green else 0
    led_yellow.value = 1 if yellow else 0
    led_red.value = 1 if red else 0

# ------------------ Event Handlers ------------------
def handle_sound_event():
    led_set(red=True)
    beep("short")
    photo, has_face = capture_image_with_face("noise")
    event = {
        "event": "sound_detected",
        "face_detected": has_face,
        "photo": photo
    }
    publish_to_blynk(event)
    print("[EVENT] Sound detected:", event)
    time.sleep(2)
    led_set(green=True)

def handle_motion_event():
    led_set(yellow=True)
    beep("short")
    photo, has_face = capture_image_with_face("motion")
    event = {
        "event": "motion_detected",
        "face_detected": has_face,
        "photo": photo
    }
    publish_to_blynk(event)
    print("[EVENT] Motion detected:", event)
    time.sleep(2)
    led_set(green=True)

def handle_smoke_event(state):
    if state == 1:
        led_set(red=True)
        beep("long")
        publish_to_blynk({"event": "smoke_alert", "smoke": 1})
        print("[EVENT] Smoke detected")
    else:
        publish_to_blynk({"smoke": 0})

def handle_dht_event():
    try:
        t = dht_device.temperature
        h = dht_device.humidity
        if t is not None and h is not None:
            publish_to_blynk({"temperature": t, "humidity": h})
            print(f"[DHT] Temp={t}°C Hum={h}%")
    except RuntimeError:
        pass

# ------------------ Loops ------------------
def loop_sound():
    while True:
        if sound_sensor.value == 1:
            handle_sound_event()
        time.sleep(0.2)

def loop_ir():
    last = 0
    while True:
        state = ir_sensor.value
        if state and not last:
            handle_motion_event()
        last = state
        time.sleep(0.2)

def loop_smoke():
    while True:
        state = smoke_sensor.value
        handle_smoke_event(state)
        time.sleep(3)

def loop_dht():
    while True:
        handle_dht_event()
        time.sleep(10)

# ------------------ Main ------------------
if __name__ == "__main__":
    led_set(green=True)
    print("System running. Press Ctrl+C to exit.")

    threads = [
        threading.Thread(target=loop_sound, daemon=True),
        threading.Thread(target=loop_ir, daemon=True),
        threading.Thread(target=loop_smoke, daemon=True),
        threading.Thread(target=loop_dht, daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        while True:
            blynk.run()
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("Stopped by user")
        led_set()
        buzzer.off()
