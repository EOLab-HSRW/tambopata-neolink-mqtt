import paho.mqtt.client as mqtt_client
import requests  
import logging
import uuid
import base64
import os
import asyncio
import sys
import time
import signal
import select  # For non-blocking stdin on Unix/WSL
import shutil
from datetime import datetime, timedelta
from threading import Thread, Timer
from queue import Queue
from typing import Tuple
from pathlib import Path
from dotenv import load_dotenv

# ====================== CONFIG & MODE ======================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info("Neolink MQTT Client starting...")

# Load .env file
try:
    load_dotenv()
    logging.info(".env file loaded successfully")
except Exception as e:
    logging.error(f"Failed to load environment variables: {e}, using defaults where applicable")

MODE = os.environ.get("NEOLINK_MODE", "controller").lower()
if MODE not in ["controller", "manual"]:
    logging.error(f"Invalid NEOLINK_MODE='{MODE}'. Falling back to 'controller'.")
    MODE = "controller"
logging.info(f"=== Running in {MODE.upper()} mode ===")
if MODE == "manual":
    logging.info("Type 'help' for commands, Ctrl+C to exit")

# MQTT Broker details
broker = 'mosquitto'
port = 1883
username = str(os.environ.get('MQTT_USERNAME'))
password = str(os.environ.get('MQTT_PASSWORD'))
client_id = f'python-mqtt-{MODE}-{uuid.getnode()}'

# ====================== NEXTCLOUD WEBDAV SETUP ======================

keep_hours = float(os.environ.get('LOCAL_KEEP_HOURS', '24'))

def cleanup_local_captures(keep_period: int = 24):
    """
    Delete local captures older than X hours.
    Called after each successful upload.
    """
    global save_dir
    cutoff = datetime.now() - timedelta(hours=keep_period)
    deleted_count = 0

    for root, dirs, files in os.walk(save_dir):
        for file in files:
            if not file.lower().endswith('.jpg'):
                continue
            filepath = os.path.join(root, file)
            try:
                file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                if file_time < cutoff:
                    os.remove(filepath)
                    deleted_count += 1
                    # log every 50 deletions
                    if deleted_count % 50 == 0:
                        logging.info(f"Cleaned up {deleted_count} old local images...")
            except Exception as e:
                logging.debug(f"Failed to delete {filepath}: {e}")

    # silent failure
    if deleted_count > 0:
        logging.info(f"Local cleanup complete — deleted {deleted_count} files older than {keep_period}h")

    # remove empty folders
    for root, dirs, files in os.walk(save_dir, topdown=False):
        for d in dirs:
            dirpath = os.path.join(root, d)
            try:
                if not os.listdir(dirpath):  # empty
                    os.rmdir(dirpath)
            except:
                pass

# def upload_to_nextcloud(local_filename):
#     """Upload file to Nextcloud /Photos/YYYY-MM-DD/ using raw WebDAV (PUT + MKCOL)."""
#     if MODE != "controller" or not is_capture_sequence:
#         return  # Only sequence images from controller

#     try:
#         webdav_url = os.environ.get('NEXTCLOUD_WEBDAV_URL').rstrip('/') 
#         username = os.environ.get('NEXTCLOUD_USERNAME')
#         password = os.environ.get('NEXTCLOUD_PASSWORD')
#         if not all([webdav_url, username, password]):
#             logging.warning("Nextcloud env vars missing – skipping upload")
#             return

#         now = datetime.now()
#         local_now = now - timedelta(hours=5)
#         date_str = local_now.strftime("%Y-%m-%d")
#         base_dir = os.environ.get('NEXTCLOUD_TARGET_DIR', '/Photos').lstrip('/')
#         remote_dir = f"{base_dir}/{date_str}"  # Photos/YYYY-MM-DD/
#         remote_filename = f"{webdav_url}/{remote_dir}/{os.path.basename(local_filename)}"

#         # Auth
#         auth = (username, password)
#         headers = {'Content-Type': 'image/jpg'}  

#         # MKCOL for base dir (with PROPFIND check)
#         base_url = f"{webdav_url}/{base_dir}"
#         propfind_resp = requests.request('PROPFIND', base_url, auth=auth, headers={'Depth': '0'})
#         logging.debug(f"PROPFIND {base_dir}: status {propfind_resp.status_code}")
#         if propfind_resp.status_code not in (200, 207):  # Not found/exists
#             mkcol_resp = requests.request('MKCOL', base_url, auth=auth)
#             logging.info(f"Created base dir {base_dir}: status {mkcol_resp.status_code}")
#             if mkcol_resp.status_code != 201:
#                 logging.warning(f"Base MKCOL failed (status {mkcol_resp.status_code}) – trying upload anyway")

#         # MKCOL for date subdir
#         subdir_url = f"{webdav_url}/{remote_dir}"
#         propfind_resp = requests.request('PROPFIND', subdir_url, auth=auth, headers={'Depth': '0'})
#         logging.debug(f"PROPFIND {remote_dir}: status {propfind_resp.status_code}")
#         if propfind_resp.status_code not in (200, 207):
#             mkcol_resp = requests.request('MKCOL', subdir_url, auth=auth)
#             logging.info(f"Created subdir {remote_dir}: status {mkcol_resp.status_code}")
#             if mkcol_resp.status_code != 201:
#                 logging.warning(f"Subdir MKCOL failed (status {mkcol_resp.status_code}) – trying upload anyway")

#         # Upload file with retries
#         with open(local_filename, 'rb') as f:
#             for attempt in range(3):
#                 upload_resp = requests.put(remote_filename, data=f, auth=auth, headers=headers)
#                 logging.debug(f"Upload attempt {attempt+1} to {remote_filename}: status {upload_resp.status_code}")
#                 if upload_resp.status_code in (200, 201, 204):  # Success codes per WebDAV docs
#                     logging.info(f"Uploaded to Nextcloud: {remote_dir}/{os.path.basename(local_filename)}")
#                     return
#                 else:
#                     logging.warning(f"Upload attempt {attempt+1} failed: {upload_resp.status_code} - {upload_resp.text[:200]}")
#                     f.seek(0)  # Reset file pointer for retry
#                     time.sleep(2 ** attempt)
#         logging.error(f"Failed to upload {local_filename} after 3 retries")
#     except Exception as e:
#         logging.error(f"Nextcloud upload setup failed: {e}")
#     finally:
#         # Always clean up old files
#         try:
#             global keep_hours
#             cleanup_local_captures(keep_period=keep_hours)
#         except:
#             pass  # never crash the main loop

def upload_to_nextcloud(local_filename):
    """Upload to custom Nextcloud path using only NEXTCLOUD_TARGET_DIR."""
    if MODE != "controller" or not is_capture_sequence:
        return

    try:
        webdav_url = os.environ.get('NEXTCLOUD_WEBDAV_URL').rstrip('/')
        username = os.environ.get('NEXTCLOUD_USERNAME')
        password = os.environ.get('NEXTCLOUD_PASSWORD')
        target_dir = os.environ.get('NEXTCLOUD_TARGET_DIR', '/Photos').strip('/')
        if not all([webdav_url, username, password]):
            logging.warning("Nextcloud credentials missing – skipping upload")
            return

        # Parse filename: PE-TNR_wide_off_2_09-30.jpg
        basename = os.path.basename(local_filename)
        try:
            parts = basename.split('_')
            lens_id = parts[1]        # wide or zoom
            ir_mode = parts[2]        # on/off/auto
            preset = parts[3]         # 0,1,2,3
        except IndexError:
            logging.warning(f"Cannot parse filename: {basename}")
            return

        # Use local time (-5h already applied when saving)
        local_now = datetime.now() - timedelta(hours=5)
        date_str = local_now.strftime("%d.%m.%Y")  # 03.12.2025

        # Build path exactly as you want
        remote_parts = [
            target_dir.lstrip('/'),           # e.g. "Shared/Reolink-Camera-Tambopata-1"
            date_str,                         # 03.12.2025
            lens_id,                          # wide or zoom
            ir_mode,                          # on/off/auto
            f"preset_{preset}"
        ]
        remote_dir = "/".join(remote_parts)
        remote_filename = f"{webdav_url}/{remote_dir}/{basename}"

        auth = (username, password)
        headers = {'Content-Type': 'image/jpg'}

        # Recursively create directories
        current_url = webdav_url
        for part in remote_parts:
            current_url += f"/{part}"
            resp = requests.request("PROPFIND", current_url, auth=auth, headers={"Depth": "0"}, timeout=10)
            if resp.status_code not in (200, 207):
                mkcol = requests.request("MKCOL", current_url, auth=auth, timeout=10)
                if mkcol.status_code == 201:
                    logging.info(f"Created folder: {current_url}")
                elif mkcol.status_code != 405:
                    logging.warning(f"MKCOL failed ({mkcol.status_code})")

        # Upload with retry
        with open(local_filename, "rb") as f:
            for attempt in range(1, 4):
                resp = requests.put(remote_filename, data=f, auth=auth, headers=headers, timeout=30)
                if resp.status_code in (200, 201, 204):
                    logging.info(f"Uploaded → {remote_dir}/{basename}")
                    return
                f.seek(0)
                time.sleep(2 ** attempt)
        logging.error(f"Upload failed after 3 attempts: {basename}")

    except Exception as e:
        logging.error(f"Nextcloud error: {e}", exc_info=True)
    finally:
        try:
            cleanup_local_captures(keep_period=keep_hours)
        except:
            pass

# ====================== TOPICS & PATHS ======================
# Lens details
lens_name = os.environ.get('LENS_0_NAME', 'tambopata-0')
lens_0_name = os.environ.get('LENS_0_NAME', 'tambopata-0')
lens_1_name = os.environ.get('LENS_1_NAME', 'tambopata-1')

# Topics
status_topic = "neolink/status"

preview_topic_0 = f'neolink/{lens_0_name}/status/preview'
preview_topic_1 = f'neolink/{lens_1_name}/status/preview'
battery_level_topic = f'neolink/{lens_name}/status/battery_level'
ptz_preset_status_topic = f'neolink/{lens_name}/status/ptz/preset'

base_control = f'neolink/{lens_name}/control'
ir_topic = f'{base_control}/ir'
ptz_topic = f'{base_control}/ptz'
ptz_preset_topic = f'{base_control}/ptz/preset'
ptz_assign_topic = f'{base_control}/ptz/assign'
zoom_topic = f'neolink/{lens_1_name}/control/zoom'

base_query = f'neolink/{lens_name}/query'
battery_query_topic = f'{base_query}/battery'
ptz_preset_query_topic = f'{base_query}/ptz/preset'

preview_query_topic_0 = f'neolink/{lens_0_name}/query/preview'
preview_query_topic_1 = f'neolink/{lens_1_name}/query/preview'

wakeup_topic_0 = f'neolink/{lens_0_name}/control/wakeup'
wakeup_topic_1 = f'neolink/{lens_1_name}/control/wakeup'

# Image save directory
save_dir = './captures'

if MODE == "controller":
    save_dir = os.path.join(save_dir, "from_controller")
elif MODE == "manual":
    save_dir = os.path.join(save_dir, "from_manual")

os.makedirs(save_dir, exist_ok=True)

# ===== Global states =====================

# Parse SCHEDULED_TIMES env var into list of (hour, minute) tuples
def parse_schedule_times() -> list[tuple[int, int]]:
    raw = os.environ.get('SCHEDULED_TIMES', '12:00')
    times = []
    seen = set()

    for t in raw.split(","):
        t = t.strip()
        if not t:
            continue
        try:
            h, m = map(int, t.split(":"))
            if not (0 <= h < 24 and 0 <= m < 60):
                logging.warning(f"Hour/minute out of range in time: {t}")
                raise ValueError
            if (h, m) in seen:
                logging.warning(f"Duplicate time {t} ignored")
                continue
            seen.add((h, m))
            times.append((h, m))
        except Exception:
            logging.warning(f"Invalid time format: {t} (use HH:MM), skipping")

    if not times:
        times = [(12, 0)]
        logging.warning("No valid times → fallback to 12:00")

    times.sort()

    # ——— check for too-close times ———
    for i in range(len(times)):
        h1, m1 = times[i]
        t1 = h1 * 60 + m1

        # Check against next time (and wrap around to first tomorrow)
        if i + 1 < len(times):
            h2, m2 = times[i + 1]
            t2 = h2 * 60 + m2
        else:
            h2, m2 = times[0]
            t2 = (h2 * 60 + m2) + 24 * 60  # tomorrow

        delta_min = t2 - t1
        if delta_min < 8:  # 5 minutes for a full capture sequnce + buffer minutes
            logging.warning(
                f"Scheduled times too close! {h1:02d}:{m1:02d} → {h2 % 24:02d}:{m2:02d} "
                f"is only {delta_min} minute(s) apart. Sequence takes ~6-7 min → risk of overlap!"
            )

    logging.info(f"Scheduled capture times: {', '.join(f'{h:02d}:{m:02d}' for h,m in times)}")
    return times

start_preset = int(os.environ.get("START_PRESET", "0"))
end_preset   = int(os.environ.get("END_PRESET", "3"))

# auto-swap if user sets start > end
if start_preset > end_preset:
    logging.warning(f"START_PRESET ({start_preset}) > END_PRESET ({end_preset}) – swapping automatically")
    start_preset, end_preset = end_preset, start_preset

if MODE == "controller":
    # Global list of scheduled times
    SCHEDULED_TIMES = parse_schedule_times()
    logging.info(f"Daily capture preset range configured: {start_preset} → {end_preset}")

ir_mode = 'auto'
zoom_levels = [1.0, 2.0, 3.5]
zoom_index = 0
wakeup_sent = False
is_capture_sequence = False
current_preset = None

stop_event = asyncio.Event()

def reset_wakeup_flag():
    global wakeup_sent
    wakeup_sent = False
    logging.info("Wakeup window expired")

# ====================== MQTT CLIENT SETUP ======================
def connect_mqtt(broker, port, client_id, username=None, password=None):
    global _mqtt_client, _mqtt_thread
    def on_connect(client, userdata, flags, reason_code, properties=None):
        global wakeup_sent
        if reason_code == 0:
            logging.info("Connected to MQTT Broker!")
            subscribe(client)
            query_battery(client)
            set_ir_control(client, 'auto')

            if MODE == "controller":
                wakeup_both_lenses(client, minutes=10)
                wakeup_sent = True
                Timer(600, reset_wakeup_flag).start()
        else:
            logging.error(f"Failed to connect, reason code {reason_code}")

    def on_disconnect(client, userdata, flags, reason_code, properties=None):
        logging.warning(f"Disconnected (rc={reason_code}) (flags={flags}) – will reconnect automatically")

    def on_message(client, userdata, msg):
        global is_capture_sequence, current_preset, ir_mode
        if "preview" in msg.topic:
            if msg.retain:
                logging.debug(f"Ignoring retained preview from {msg.topic}")
                return
                # PREVENT CONTROLLER FROM SAVING MANUAL SNAPSHOTS
            if MODE == "controller" and not is_capture_sequence:
                logging.info("Controller ignoring manual snapshot (taken from manual client)")
                return

            try:
                payload_bytes = msg.payload
                payload_len = len(payload_bytes)
                if payload_len < 10:
                    logging.warning(f"Preview too short ({payload_len} bytes) – skipping")
                    return

                # Try raw JPEG first
                if payload_bytes.startswith(b'\xff\xd8\xff'):
                    logging.info(f"Raw JPEG detected for {msg.topic} (len={payload_len})")
                    img_bytes = payload_bytes
                else:
                    b64_str = None
                    # Base64 / data-uri fallback with aggressive cleanup
                    try:
                        raw_str = payload_bytes.decode('ascii', errors='replace').strip()

                        if raw_str.startswith("data:image"):
                            b64_str = raw_str.split(",", 1)[1]
                        else:
                            b64_str = raw_str

                        if b64_str.startswith('"') and b64_str.endswith('"'):
                            b64_str = b64_str[1:-1]

                        b64_str = b64_str.rstrip('=')
                        truncate_by = len(b64_str) % 4
                        if truncate_by != 0:
                            b64_str = b64_str[:-truncate_by]
                            logging.warning(f"Truncated {truncate_by} chars in base64 for {msg.topic} (new len={len(b64_str)})")
                        missing = (4 - len(b64_str) % 4) % 4
                        b64_str += '=' * missing

                        img_bytes = base64.b64decode(b64_str)
                    except (UnicodeDecodeError, base64.binascii.Error, ValueError) as e:
                        # Final fallback: Treat entire payload as base64 bytes (ignore errors)
                        logging.warning(f"Base64 fallback failed for {msg.topic}; trying raw payload as base64 (len={payload_len}): {e}")
                        # Pad raw bytes to 4-multiple if needed
                        raw_b64_len = payload_len
                        truncate_by = raw_b64_len % 4
                        if truncate_by != 0:
                            payload_bytes = payload_bytes[:-truncate_by]
                        # Add pads as bytes
                        pads_needed = (4 - len(payload_bytes) % 4) % 4
                        padded_bytes = payload_bytes + b'=' * pads_needed
                        img_bytes = base64.b64decode(padded_bytes, validate=False)  # Ignore padding errors

                # Build file path
                now = datetime.now()
                local_now = now - timedelta(hours=5)
                date_str   = local_now.strftime("%d.%m.%Y")          
                timestamp  = local_now.strftime("%H-%M")             
                lens_id    = "wide" if lens_0_name in msg.topic else "zoom"

                # Base paths
                base_dir   = os.path.join(save_dir, date_str)                    
                type_dir   = "from_capture_sequence" if is_capture_sequence else "manual_snapshots"
                type_path  = os.path.join(base_dir, type_dir)                    # …/from_capture_sequence or …/manual_snapshots

                # Deep hierarchy
                lens_path  = os.path.join(type_path, lens_id)                     # …/wide or …/zoom
                ir_path    = os.path.join(lens_path, ir_mode)                     # …/on, …/off, …/auto
                preset_val = str(current_preset) if current_preset is not None else "none"
                preset_path = os.path.join(ir_path, f"preset_{preset_val}")       # …/preset_n

                # Create all directories
                os.makedirs(preset_path, exist_ok=True)

                # Final filename
                if is_capture_sequence:
                    filename = os.path.join(
                        preset_path,
                        f"PE-TNR_{lens_id}_{ir_mode}_{current_preset}_{timestamp}.jpg"
                    )
                else:
                    filename = os.path.join(
                        preset_path,
                        f"PE-TNR_{lens_id}_infrared-{ir_mode}_preset-{preset_val}_{timestamp}.jpg"
                    )

                # Write file
                with open(filename, "wb") as f:
                    f.write(img_bytes)

                logging.info(f"Saved → {filename} ({payload_len} bytes)")
                upload_to_nextcloud(filename)
            except Exception as e:
                logging.error(f"Failed to decode/save image from {msg.topic} (len={len(msg.payload)}): {e}. Hex preview: {msg.payload[:50].hex()}...")
        elif msg.topic == battery_level_topic:
            logging.info(f"Battery level: {msg.payload.decode()}%")
        elif "ptz/preset" in msg.topic:
            logging.info(f"PTZ Presets: {msg.payload.decode()}")
        else:
            try:
                logging.info(f"[{msg.topic}] {msg.payload.decode()}")
            except:
                logging.info(f"[{msg.topic}] <binary>")

    client = mqtt_client.Client(client_id=client_id, callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    if username and password:
        client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.will_set("neolink/status", "offline", qos=1, retain=True)

    client.connect(broker, port)             
    return client

def subscribe(client: mqtt_client.Client):
    topics = [
        (status_topic, 0),
        (preview_topic_0, 0),
        (preview_topic_1, 0),
        (battery_level_topic, 0),
        # (ptz_preset_status_topic, 0),
    ]

    for t, qos in topics:
        result = client.subscribe(t, qos)
        if result[0] == 0:
            logging.info(f"Subscribed to: {t}")
        else:
            logging.warning(f"Failed to subscribe to {t}, rc={result[0]}")

# ====================== COMMANDS ======================
def query_battery(client):
    result = client.publish(battery_query_topic, "", qos=1, retain=False)
    if result.rc == 0:
        logging.info(f"Sent battery query to {battery_query_topic}")
    else:
        logging.warning(f"Failed to send battery query to {battery_query_topic}, rc={result.rc}")

def set_ir_control(client, mode):
    result = client.publish(ir_topic, mode, qos=1, retain=False)
    if result.rc == 0:
        logging.info(f"Sent IR control {mode} command to {ir_topic}")
    else:
        logging.warning(f"Failed to send IR control {mode} command to {ir_topic}, rc={result.rc}")

def go_to_preset(client, preset_id):
    payload = str(preset_id)
    result = client.publish(ptz_preset_topic, payload, qos=1, retain=False)
    if result.rc == 0:
        logging.info(f"Sent go to preset {preset_id} command to {ptz_preset_topic}")
    else:
        logging.warning(f"Failed to send go to preset {preset_id} command to {ptz_preset_topic}, rc={result.rc}")

def manual_ptz_control(client, direction, amount):
    payload = f"{direction} {amount}"
    result = client.publish(ptz_topic, payload, qos=1, retain=False)
    if result.rc == 0:
        logging.info(f"Sent manual PTZ {direction} command with amount {amount} to {ptz_topic}")
    else:
        logging.warning(f"Failed to send manual PTZ {direction} command with amount {amount} to {ptz_topic}, rc={result.rc}")

def manual_zoom_control(client, amount):
    payload = str(amount)
    result = client.publish(zoom_topic, payload, qos=1, retain=False)
    if result.rc == 0:
        logging.info(f"Sent manual zoom command with amount {amount} to {zoom_topic}")
    else:
        logging.warning(f"Failed to send manual zoom command with amount {amount} to {zoom_topic}, rc={result.rc}")

def trigger_snapshot(client):
    result_0 = client.publish(preview_query_topic_0, "", qos=1, retain=False)
    if result_0.rc == 0:
        logging.info(f"Sent trigger snapshot command to {preview_query_topic_0}")
    else:
        logging.warning(f"Failed to send trigger snapshot command to {preview_query_topic_0}, rc={result_0.rc}")

    result_1 = client.publish(preview_query_topic_1, "", qos=1, retain=False)
    if result_1.rc == 0:
        logging.info(f"Sent trigger snapshot command to {preview_query_topic_1}")
    else:
        logging.warning(f"Failed to send trigger snapshot command to {preview_query_topic_1}, rc={result_1.rc}")

def assign_preset(client, preset_id, name):
    safe_name = name.replace(" ", "_")
    payload = f"{preset_id} {safe_name}"
    result = client.publish(ptz_assign_topic, payload, qos=1, retain=False)
    if result.rc == 0:
        logging.info(f"Sent assign preset {preset_id} '{name}' command to {ptz_assign_topic}")
    else:
        logging.warning(f"Failed to send assign preset {preset_id} '{name}' command to {ptz_assign_topic}, rc={result.rc}")

def wakeup_both_lenses(client, minutes=10):
    payload = str(minutes)
    result_0 = client.publish(wakeup_topic_0, payload, qos=1, retain=False)
    if result_0.rc == 0:
        logging.info(f"Sent wakeup command to {wakeup_topic_0} for {payload} minutes")
    else:
        logging.warning(f"Failed to send wakeup command to {wakeup_topic_0}, rc={result_0.rc}")

    result_1 = client.publish(wakeup_topic_1, payload, qos=1, retain=False)
    if result_1.rc == 0:
        logging.info(f"Sent wakeup command to {wakeup_topic_1} for {payload} minutes")
    else:
        logging.warning(f"Failed to send wakeup command to {wakeup_topic_1}, rc={result_1.rc}")

# ====================== DAILY SEQUENCE ======================
async def perform_daily_capture(client, event_type: str = "alt", start: int = start_preset, end: int = end_preset):
    global is_capture_sequence, current_preset, ir_mode, start_preset, end_preset
    logging.info(f"Starting daily capture sequence (mode={event_type}, presets {start}→{end})")
    query_battery(client)
    await asyncio.sleep(2)
    is_capture_sequence = True

    if event_type == "on":
        set_ir_control(client, 'on')
        ir_mode = 'on'
        await asyncio.sleep(2)
        for i in range(start, end + 1):
            current_preset = i
            go_to_preset(client, i)
            await asyncio.sleep(5)
            trigger_snapshot(client)
            await asyncio.sleep(30)
    elif event_type == "off":
        set_ir_control(client, 'off')
        ir_mode = 'off'
        await asyncio.sleep(2)
        for i in range(start, end + 1):
            current_preset = i
            go_to_preset(client, i)
            await asyncio.sleep(5)
            trigger_snapshot(client)
            await asyncio.sleep(30)
    else:  # "alt" or anything else = alternate
        for i in range(start, end + 1):
            current_preset = i
            # IR off first
            set_ir_control(client, 'off')
            ir_mode = 'off'
            await asyncio.sleep(2)
            go_to_preset(client, i)
            await asyncio.sleep(5)
            trigger_snapshot(client)
            await asyncio.sleep(30)
            # IR on second
            set_ir_control(client, 'on')
            ir_mode = 'on'
            await asyncio.sleep(2)
            trigger_snapshot(client)
            await asyncio.sleep(30)

    # Cleanup
    set_ir_control(client, 'auto')
    ir_mode = 'auto'
    is_capture_sequence = False
    logging.info("Daily capture sequence finished")

# ====================== MANUAL MODE ONLY ======================
if MODE == "manual":
    command_queue = Queue()
    stdin_lock = asyncio.Lock()
else:
    command_queue = None
    stdin_lock = None

def stdin_loop(queue: Queue):
    logging.info("Keyboard listener active – type 'help' + Enter")
    while not stop_event.is_set():
        if stdin_lock.locked():
            time.sleep(0.1)  # Pause polling while a prompt is active
            continue
        if select.select([sys.stdin], [], [], 0.1)[0]:
            line = sys.stdin.readline().strip().lower()
            if not line:
                continue
            if line in ['up', 'down', 'left', 'right']:
                command_queue.put(('ptz', line))
                logging.info(f"Enqueued PTZ: {line}")
            elif line in ['0', '1', '2', '3', '4', '5', '6', '7', '8']:
                preset_id = int(line)
                command_queue.put(('preset', preset_id))
                logging.info(f"Enqueued preset: {preset_id}")
            elif line == 'r':
                command_queue.put(('ir_toggle',))
                logging.info("Enqueued IR toggle")
            elif line == 'z':
                command_queue.put(('zoom_toggle',))
                logging.info("Enqueued zoom toggle")
            elif line == 's':
                command_queue.put(('snapshot_both',))
                logging.info("Enqueued snapshot both")
            elif line == 'b':
                command_queue.put(('battery',))
                logging.info("Enqueued battery query")  
            elif line == 'a':
                command_queue.put(('assign',))
                logging.info("Enqueued assign preset")
            elif line == 'd':
                command_queue.put(('custom_capture',))
                logging.info("Enqueued custom capture sequence")
            elif line == 'help':
                logging.info("Commands:\n"
                                " up/down/left/right - PTZ control\n"
                                " z - Cycle zoom levels (1x/2x/3.5x)\n"
                                " a - Assign current position to a preset (will prompt for ID and name)\n"
                                " 0-8 - Go to PTZ preset position\n"
                                " r - Toggle IR mode (auto/on/off)\n"
                                " s - Trigger snapshot on both lenses\n"
                                " b - Query battery level\n"
                                # " p - Request presets report\n"
                                " d - Perform custom daily capture sequence\n"
                                " help - Show this help message")
            else:
                logging.info(f"Unknown command: {line} (try: 'help' for list of commands)")

async def process_commands(client):
    global ir_mode, zoom_index, start_preset, end_preset, current_preset
    loop = asyncio.get_event_loop()
    while not stop_event.is_set():
        cmd = await loop.run_in_executor(None, command_queue.get)
        cmd_type = cmd[0]

        if cmd_type == 'ptz':
            direction = cmd[1]
            amount = await loop.run_in_executor(None, lambda: float(input(f"Amount for {direction} [32.0]: ") or "32.0"))
            manual_ptz_control(client, direction, amount)
            current_preset = None
        elif cmd_type == 'preset':
            go_to_preset(client, cmd[1])
            current_preset = cmd[1]
        elif cmd_type == 'ir_toggle':
            modes = ['auto', 'on', 'off']
            ir_mode = modes[(modes.index(ir_mode) + 1) % 3]
            set_ir_control(client, ir_mode)
        elif cmd_type == 'zoom_toggle':
            zoom_index = (zoom_index + 1) % len(zoom_levels)
            manual_zoom_control(client, zoom_levels[zoom_index])
        elif cmd_type == 'snapshot_both':
            trigger_snapshot(client)
        elif cmd_type == 'battery':
            query_battery(client)
        elif cmd_type == 'assign':
            async with stdin_lock:
                pid = await loop.run_in_executor(None, lambda: input("Preset ID: "))
                name = await loop.run_in_executor(None, lambda: input("Preset name: "))
                try:
                    assign_preset(client, int(pid), name)
                except ValueError:
                    logging.error("Invalid ID. Must be an integer.")
        elif cmd_type == 'custom_capture':
            async with stdin_lock:
                et = await loop.run_in_executor(None, lambda: input("Event (on/off/alt): ").lower())
                s = await loop.run_in_executor(None, lambda: input(f"Start preset [{start_preset}]: ") or str(start_preset))
                e = await loop.run_in_executor(None, lambda: input(f"End preset [{end_preset}]: ") or str(end_preset))
                try:
                    start_preset, end_preset = int(s), int(e)
                    if start_preset > end_preset:
                        start_preset, end_preset = end_preset, start_preset
                except ValueError:
                    logging.error("Invalid numbers")
                await perform_daily_capture(client, et if et in ["on", "off", "alt"] else "alt", start=start_preset, end=end_preset)
        command_queue.task_done()

# ====================== CONTROLLER MODE ONLY ======================

def seconds_to_next_scheduled_time() -> float:
    global SCHEDULED_TIMES
    """Return seconds until the next scheduled time (from SCHEDULED_TIMES list)"""
    now = datetime.now()
    today = now.date()
    tomorrow = today + timedelta(days=1)

    # Build list of upcoming datetimes for today and tomorrow
    candidates = []
    for h, m in SCHEDULED_TIMES:
        dt_today = datetime(today.year, today.month, today.day, h, m)
        dt_tomorrow = datetime(tomorrow.year, tomorrow.month, tomorrow.day, h, m)
        
        if dt_today > now:
            candidates.append(dt_today)
        candidates.append(dt_tomorrow)  # always include tomorrow's

    if not candidates:
        return 3600  # retry in 1 hour

    next_time = min(candidates)
    seconds = (next_time - now).total_seconds()
    logging.info(f"Next scheduled capture at {next_time.strftime('%H:%M')} → in {seconds/3600:.2f} hours")
    return seconds


async def scheduler(client):
    while not stop_event.is_set():
        try:
            seconds = seconds_to_next_scheduled_time()
            logging.info(f"Sleeping until next scheduled capture...")
            
            # Wait either for timeout or stop_event
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=seconds)
                logging.info("Scheduler stopped by stop_event")
                break
            except asyncio.TimeoutError:
                pass  # timeout → time to run

            # ——— RUN CAPTURE SEQUENCE ———
            current_time = datetime.now().strftime("%H:%M")
            logging.info(f"⏰ Scheduled time reached ({current_time}) → starting daily capture")
            
            wakeup_both_lenses(client, minutes=10)
            await asyncio.sleep(20)
            await perform_daily_capture(client, event_type="alt")
            
            # Optional: small delay after sequence to avoid double-triggering near midnight
            await asyncio.sleep(30)

        except Exception as e:
            logging.error(f"Error in scheduler loop: {e}", exc_info=True)
            await asyncio.sleep(60)  # no spam on error


# ====================== MAIN ======================
async def main():
    client = connect_mqtt(broker, port, client_id, username, password)
    
    client.loop_start()    
    
    tasks = []
    
    if MODE == "manual":
        Thread(target=stdin_loop, args=(command_queue,), daemon=True).start()
        tasks.append(process_commands(client))
        # auto-delete old manual images on startup
        manual_root = os.path.join("./captures", "from_manual")
        if os.path.exists(manual_root):
            shutil.rmtree(manual_root)
            logging.info(f"Cleared all previous manual images: {manual_root}")
        os.makedirs(manual_root, exist_ok=True)
    else:
        tasks.append(scheduler(client))

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    except asyncio.CancelledError:
        logging.info("Tasks cancelled during shutdown.")
    finally:
        # Stop the loop first to avoid issues during disconnect
        client.loop_stop()
        # Disconnect with a short timeout to prevent hanging
        client.disconnect()
        # Give a bit of time for cleanup
        client.loop_forever(timeout=1.0)
        logging.info("MQTT Client disconnected gracefully.")
        
if __name__ == '__main__':
    asyncio.run(main())