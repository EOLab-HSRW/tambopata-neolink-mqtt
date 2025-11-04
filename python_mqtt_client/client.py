import paho.mqtt.client as mqtt_client
import logging
import uuid
import base64
import os
import asyncio
import sys
import time
from datetime import datetime, timedelta
from threading import Thread, Event, Timer
from queue import Queue
from typing import Tuple
import select  # For non-blocking stdin on Unix/WSL

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info("Logging initialized.")

ENABLE_STDIN_INPUT = os.environ.get('ENABLE_KEYBOARD', 'false').lower() == 'true' 
if ENABLE_STDIN_INPUT:
    logging.info("Stdin input enabled (terminal key reading).")

# MQTT Broker details
broker = 'mosquitto'
port = 1883
username = 'USERNAME'
password = 'PASSWORD'
client_id = f'python-mqtt-client-{uuid.getnode()}'

# Lens details
lens_name = 'USERNAME-0'  # Control and queries (except preview and zoom) go here
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
os.makedirs(save_dir, exist_ok=True)

# Global states
ir_mode = 'auto'
zoom_levels = [1.0, 2.0, 3.5]
zoom_index = 0
interactive_event = Event()  # To pause stdin_loop during interactive inputs
wakeup_sent = False # To avoid multiple wakeup commands

def connect_mqtt(broker, port, client_id, username=None, password=None):
    def on_connect(client, userdata, flags, reason_code, properties=None):
        global wakeup_sent  
        if reason_code == 0:
            logging.info("Connected to MQTT Broker!")
            subscribe(client)
            query_battery(client)
            set_ir_control(client, 'auto')
            # Proactive wakeup on connect, set flag, and start reset timer
            wakeup_both_lenses(client)
            wakeup_sent = True
            def reset_wakeup():
                global wakeup_sent
                wakeup_sent = False
                logging.info("Wakeup window expired—ready for next idle re-wake.")
            Timer(600, reset_wakeup).start()  # Threading Timer for reset (avoids asyncio in MQTT thread)
        else:
            logging.error(f"Failed to connect, reason code {reason_code}")

    def on_disconnect(client, userdata, flags, reason_code, properties=None):
        logging.info(f"Disconnected with reason code: {reason_code} (flags: {flags})")

    def on_message(client, userdata, msg):
        if "preview" in msg.topic:
            if msg.retain:  # Skip retained messages (e.g., last preview on subscribe)
                logging.debug(f"Ignoring retained preview from {msg.topic}")
                return
            try:
                raw = msg.payload.decode(errors="ignore").strip()
                if raw.startswith("data:image"):
                    # data URI
                    b64 = raw.split(",", 1)[1]
                else:
                    b64 = raw
                # remove possible surrounding quotes
                if b64.startswith('"') and b64.endswith('"'):
                    b64 = b64[1:-1]
                img_bytes = base64.b64decode(b64)
                now = datetime.now()
                date_str = now.strftime("%d.%m.%Y")
                timestamp = now.strftime("%H-%M-%S")   
                lens_id = "0" if lens_0_name in msg.topic else "1"
                subdir = os.path.join(save_dir, date_str)
                os.makedirs(subdir, exist_ok=True)  # Create date-based subdir if needed
                filename = os.path.join(subdir, f"lens{lens_id}_{timestamp}.jpg")
                with open(filename, "wb") as f:
                    f.write(img_bytes)
                logging.info(f"Saved snapshot: {filename}")
            except Exception as e:
                logging.error(f"Failed to decode image: {e}")
        elif msg.topic == battery_level_topic:
            level = msg.payload.decode()
            logging.info(f"Battery level: {level}%")
        elif "ptz/preset" in msg.topic:
            presets = msg.payload.decode()
            logging.info(f"PTZ Presets: {presets}")
        else:
            logging.info(f"[{msg.topic}] {msg.payload.decode()}")

    client = mqtt_client.Client(client_id=client_id, callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
    # Reconnection parameters (removed invalid 'delay_factor')
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
        (ptz_preset_status_topic, 0),
    ]
    for t, qos in topics:
        result = client.subscribe(t, qos)
        if result[0] == 0:
            logging.info(f"Subscribed to: {t}")
        else:
            logging.warning(f"Failed to subscribe to {t}, rc={result[0]}")

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

def request_presets_report(client):
    result = client.publish(ptz_preset_query_topic, "", qos=1, retain=False)
    if result.rc == 0:
        logging.info(f"Sent PTZ presets report request to {ptz_preset_query_topic}")
    else:
        logging.warning(f"Failed to send PTZ presets report request to {ptz_preset_query_topic}, rc={result.rc}")

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

async def process_commands(client, command_queue):
    global ir_mode, zoom_index, interactive_event, wakeup_sent
    loop = asyncio.get_event_loop()
    while True:
        try:
            future = loop.run_in_executor(None, command_queue.get)
            cmd: Tuple = await future
            cmd_type = cmd[0]

            # Trigger wakeup only after initial connect window (on subsequent idles)
            if not wakeup_sent:
                wakeup_both_lenses(client)
                await asyncio.sleep(15)  # Give some time for wakeup
                wakeup_sent = True
                def reset_wakeup():
                    global wakeup_sent
                    wakeup_sent = False
                    logging.info("Wake up window expired — ready for next idle re-wake.")
                Timer(600, reset_wakeup).start()

            if cmd_type == 'ptz':
                direction = cmd[1]
                amount_future = loop.run_in_executor(None, input, f"Enter amount for {direction} (default 32.0): ")
                amount_str = (await amount_future).strip()
                try:
                    amount = float(amount_str) if amount_str else 32.0
                except ValueError:
                    amount = 32.0
                manual_ptz_control(client, direction, amount)
            elif cmd_type == 'preset':
                preset_id = cmd[1]
                go_to_preset(client, preset_id)
            elif cmd_type == 'ir_toggle':
                modes = ['auto', 'on', 'off']
                current_idx = modes.index(ir_mode)
                next_idx = (current_idx + 1) % 3
                ir_mode = modes[next_idx]
                set_ir_control(client, ir_mode)
            elif cmd_type == 'zoom_toggle':
                zoom_index = (zoom_index + 1) % 3
                amount = zoom_levels[zoom_index]
                manual_zoom_control(client, amount)
            elif cmd_type == 'snapshot_both':
                trigger_snapshot(client)
            elif cmd_type == 'battery':
                query_battery(client)
            elif cmd_type == 'presets_report':
                request_presets_report(client)
            elif cmd_type == 'daily_capture':
                await perform_daily_capture(client)
            elif cmd_type == 'assign':
                interactive_event.set()  #Pause stdin_loop during interactive input
                try:
                    id_future = loop.run_in_executor(None, input, "Enter preset ID (integer): ")
                    id_str = (await id_future).strip()
                    name_future = loop.run_in_executor(None, input, "Enter preset name (no spaces): ")
                    name = (await name_future).strip()
                    try:
                        preset_id = int(id_str)
                        assign_preset(client, preset_id, name)
                    except ValueError:
                        logging.error("Invalid preset ID. Must be an integer.")
                finally:
                    interactive_event.clear()  # Resume stdin_loop
            command_queue.task_done()
        except Exception as e:
            logging.error(f"Error processing command: {e}")

def stdin_loop(command_queue: Queue):
    """Reads keys/lines from stdin non-blockingly and enqueues commands."""
    global interactive_event
    logging.info("Stdin listener started. Type '--help' and press Enter for commands list.")
    while True:
        if interactive_event.is_set():
            time.sleep(0.1)
            continue
        # Use select for non-blocking read 
        ready, _, _ = select.select([sys.stdin], [], [], 0.1)  # 0.1s timeout
        if ready:
            line = sys.stdin.readline().strip().lower()
            if not line:
                continue
            # Map input strings to commands and enqueue
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
            elif line == 'p':
                command_queue.put(('presets_report',))
                logging.info("Enqueued presets report request")
            elif line == 'd':
                command_queue.put(('daily_capture',))
                logging.info("Enqueued daily capture sequence")
            elif line == '--help':
                logging.info("Commands:\n"
                             " up/down/left/right - PTZ control\n"
                             " 0-8 - Go to PTZ preset position\n"
                             " r - Toggle IR mode (auto/on/off)\n"
                             " z - Cycle zoom levels (1x/2x/3.5x)\n"
                             " s - Trigger snapshot on both lenses\n"
                             " b - Query battery level\n"
                             " a - Assign preset (will prompt for ID and name)\n"
                             " p - Request presets report\n"
                             " --help - Show this help message")
            else:
                logging.info(f"Unknown command: {line} (try: up/down/left/right, 0-8, r/z/s/b/a)")


async def perform_daily_capture(client):
    query_battery(client)
    await asyncio.sleep(2)
    for i in range(6):
        go_to_preset(client, i)
        await asyncio.sleep(5)
        trigger_snapshot(client)
        await asyncio.sleep(20)

def time_to_next_noon():
    """Seconds until local 12:00 noon (since camera +5h)."""
    now = datetime.now()
    next_noon = now.replace(hour=17, minute=0, second=0, microsecond=0)
    if now >= next_noon:
        next_noon += timedelta(days=1)
    return (next_noon - now).total_seconds()

def time_to_next_midnight():
    """Seconds until local 00:00 midnight (since camera +5h)."""
    now = datetime.now()
    next_midnight = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if now >= next_midnight:
        next_midnight += timedelta(days=1)
    return (next_midnight - now).total_seconds()

async def scheduler(client):
    while True:
        delta_noon = time_to_next_noon()
        delta_midnight = time_to_next_midnight()
        sleep_delta = min(delta_noon, delta_midnight)
        is_midnight_event = delta_midnight <= delta_noon
        logging.info(f"Next capture in {sleep_delta / 3600:.2f} hours (event: {'midnight' if is_midnight_event else 'noon'})")
        await asyncio.sleep(sleep_delta)
        if is_midnight_event:
            logging.info("Midnight reached — IR ON + capture sequence")
            wakeup_both_lenses(client)
            await asyncio.sleep(20)
            set_ir_control(client, 'on')
            await asyncio.sleep(10)
            await perform_daily_capture(client)
            set_ir_control(client, 'auto')
        else:
            logging.info("Noon reached — capture sequence")
            wakeup_both_lenses(client)
            await asyncio.sleep(20)
            await perform_daily_capture(client)

async def main():
    global ir_mode, zoom_index
    client = connect_mqtt(broker, port, client_id, username, password)
    client.loop_start()

    command_queue = Queue()
    stdin_thread = None
    if ENABLE_STDIN_INPUT:
        stdin_thread = Thread(target=stdin_loop, args=(command_queue,), daemon=True)
        stdin_thread.start()

    sched_task = asyncio.create_task(scheduler(client))
    proc_task = asyncio.create_task(process_commands(client, command_queue))

    try:
        await asyncio.gather(sched_task, proc_task)
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