import paho.mqtt.client as mqtt_client
import logging
import uuid
import base64
import os
import asyncio
import sys
import time
import signal
import select  # For non-blocking stdin on Unix/WSL
from datetime import datetime, timedelta
from threading import Thread, Timer
from queue import Queue
from typing import Tuple


# ====================== CONFIG & MODE ======================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info("Neolink MQTT Client starting...")

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
username = 'USERNAME'
password = 'PASSWORD'
client_id = f'python-mqtt-{MODE}-{uuid.getnode()}'

# ====================== TOPICS & PATHS ======================
# Lens details
lens_name = 'USERNAME-0'
lens_0_name = 'USERNAME-0'
lens_1_name = 'USERNAME-1'

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

# Default values (can be overridden by environment variables)
start_preset = int(os.environ.get("START_PRESET", "0"))
end_preset   = int(os.environ.get("END_PRESET", "3"))

# auto-swap if user sets start > end
if start_preset > end_preset:
    logging.warning(f"START_PRESET ({start_preset}) > END_PRESET ({end_preset}) – swapping automatically")
    start_preset, end_preset = end_preset, start_preset

if MODE == "controller":
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

                # Save with date subfolder
                now = datetime.now()
                date_str = now.strftime("%d.%m.%Y")
                timestamp = now.strftime("%H-%M-%S")
                lens_id = "0" if lens_0_name in msg.topic else "1"
                subdir = os.path.join(save_dir, date_str)
                os.makedirs(subdir, exist_ok=True)

                if is_capture_sequence:
                    subsubdir = os.path.join(subdir, "from_capture_sequence")
                    os.makedirs(subsubdir, exist_ok=True)
                    filename = os.path.join(subsubdir, f"t{lens_id}_{ir_mode}_p{current_preset}_{timestamp}.jpg")
                else:
                    subsubdir = os.path.join(subdir, "manual_snapshots")
                    os.makedirs(subsubdir, exist_ok=True)
                    filename = os.path.join(subsubdir, f"USERNAME{lens_id}_infrared-{ir_mode}_preset-{current_preset or 'none'}_{timestamp}.jpg")

                with open(filename, "wb") as f:
                    f.write(img_bytes)
                logging.info(f"Saved → {filename} ({payload_len} bytes)")
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

# def request_presets_report(client):
#     result = client.publish(ptz_preset_query_topic, "", qos=1, retain=False)
#     if result.rc == 0:
#         logging.info(f"Sent PTZ presets report request to {ptz_preset_query_topic}")
#     else:
#         logging.warning(f"Failed to send PTZ presets report request to {ptz_preset_query_topic}, rc={result.rc}")

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

def time_to_next_noon():
    now = datetime.now()
    next_noon = now.replace(hour=17, minute=0, second=0, microsecond=0)  # 17:00 local = noon camera time?
    if now >= next_noon:
        next_noon += timedelta(days=1)
    return (next_noon - now).total_seconds()

# def time_to_next_midnight():
#     """Seconds until local 00:00 midnight (since camera is in UTC time)."""
#     now = datetime.now()
#     next_midnight = now.replace(hour=5, minute=0, second=0, microsecond=0)
#     if now >= next_midnight:
#         next_midnight += timedelta(days=1)
#     return (next_midnight - now).total_seconds()

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
            # elif line == 'p':
            #     command_queue.put(('presets_report',))
            #     logging.info("Enqueued presets report request")
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
        # elif cmd_type == 'presets_report':
        #     request_presets_report(client)
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
async def scheduler(client):
    while not stop_event.is_set():
        seconds = time_to_next_noon()
        logging.info(f"Next scheduled capture in {seconds/3600:.2f} hours")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
            break
        except asyncio.TimeoutError:
            pass
        logging.info("⏰ Scheduled time reached → starting daily capture")
        wakeup_both_lenses(client, minutes=10)
        await asyncio.sleep(20)
        await perform_daily_capture(client, event_type="alt")

        # delta_noon = time_to_next_noon()
        # delta_midnight = time_to_next_midnight()
        # sleep_delta = min(delta_noon, delta_midnight)
        # is_midnight_event = delta_midnight <= delta_noon
        # logging.info(f"Next capture in {sleep_delta / 3600:.2f} hours (event: {'midnight' if is_midnight_event else 'noon'})")
        # await asyncio.sleep(sleep_delta)
            
        # if is_midnight_event:
        #     logging.info("Midnight reached — IR ON + capture sequence")
        #     wakeup_both_lenses(client)
        #     await asyncio.sleep(20)
        #     await perform_daily_capture(client, is_midnight_event, start_preset, end_preset)
        # else:
        #     logging.info("Noon reached — capture sequence")
        #     wakeup_both_lenses(client)
        #     await asyncio.sleep(20)
        #     await perform_daily_capture(client, is_midnight_event, start_preset, end_preset)

# ====================== MAIN ======================
async def main():
    client = connect_mqtt(broker, port, client_id, username, password)
    
    client.loop_start()    
    
    tasks = []
    
    if MODE == "manual":
        Thread(target=stdin_loop, args=(command_queue,), daemon=True).start()
        tasks.append(process_commands(client))
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