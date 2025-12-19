# RTSP-MQTT Connection using Neolink for Daily Image Capture Automation of Tambopata Tower's Surveillance Camera

This project compiles Neolink, Mosquitto MQTT Broker, and Python MQTT Client services in a Docker compose container. Neolink is a small program that acts as a proxy between Reolink IP cameras and normal RTSP clients. The neolink will communicate with the client through the MQTT Broker. 

The client has a scheduler loop that runs in the background to automatically capture images from different PTZ presets according to the configured time (from environment variables) alternating between both infrared filter mode (on/off). In addition to that, some manual controls are accessible and is required if you want to assign new PTZ presets.

## Setup 

### Docker Installation

Follow the instruction from the link below based on your operating system:

https://docs.docker.com/engine/install/

### OpenVPN Connect

Some devices that are connected to the university's WiFi require a VPN to access the reolink cameras with neolink. Follow the instruction from the link below and contact the responsible person from the lab:

https://wiki.eolab.de/doku.php?id=snapcon2022:openvpn-connection&s[]=vpn

After you are connected to the VPN then continue to the next step.

### Docker Compose Container

1. Clone this repo

```bash
git clone https://github.com/EOLab-HSRW/tambopata-neolink-mqtt.git
```

then

```bash
cd tambopata-neolink-mqtt
```

2. the Mosquitto MQTT Broker service will expect this file to exist, we can use the touch command to create an empty file

```bash
touch ./mqtt/config/pwfile
```

Then change the owner of the file to root as expected by the mosquitto service

```bash
sudo chown root ./mqtt/config/pwfile
```

3. Build the docker compose images

```bash
docker compose build
```

4. Run the compose container detached

```bash
docker compose up -d
```

Then use exec to run commands on the MQTT Broker container

```bash
docker compose exec mosquitto sh
```

5. Create a new mosquitto broker user

```bash
mosquitto_passwd -c /mosquitto/config/pwfile <username>
```

After running the above command, you will be prompted to type in a password for this user. 

```bash
Password: <password>
Reenter password: <password>
```

**This credential will then be used by both Neolink and the MQTT client (see next section).**

Then exit the bash

```bash
exit
```

6. Stop the compose container

```bash
docker compose stop
```

### Configuring Environment Variables (with .var file) and Neolink Config File

#### Credentials

After setting up the new user for the MQTT broker, you must modify the environment variables of the client and the config file of Neolink so both of them can connect to the broker and the Neolink itself can connect to the Reolink Cameras. 

Additionally, since the captured images will be uploaded directly to the nextcloud drive using WebDAV, credentials for this are also needed.

#### Preset Range and Scheduled Capture Times

Currently we only have 4 presets for the image capture automation (preset 0 to 3). After you have new presets that you had assigned with the manual controls, then simply define them in the .env file. 

You can also configure the daily capture time by editing the SCHEDULED_TIMES (A list of tuple with 24H format), be aware that the camera is 5 hour ahead than the local time (Camera time is UTC, local time of Lima - Peru is UTC-05:00).

#### Set these up

- In neolink/config.toml:

```bash
[mqtt]
broker_addr = "mosquitto" # Address of the mqtt server
port = 1883 # mqtt servers port
credentials = ["USERNAME", "PASSWORD"] # mqtt server login details

[[cameras]]
username = "YOUR_USERNAME" 
password = "YOUR_PASSWORD"
uid = "YOUR_UID"
```

Then copy and edit the .env.example file to the client directory:

```bash
cp .env.example python_mqtt_client/.env

nano python_mqtt_client/.env               # configure variables
```

```bash
# .env.example
# Copy this file to .env and fill in your secrets and configurations

# === preset configuration ===
START_PRESET=0
END_PRESET=3

# === Scheduled times (comma-separated HH:MM) ===
SCHEDULED_TIMES=06:30,12:00,18:00,23:45

# === MQTT Broker ===
MQTT_USERNAME=your_mqtt_user
MQTT_PASSWORD=your_mqtt_password

# === Nextcloud WebDAV Upload (controller only) ===
NEXTCLOUD_WEBDAV_URL=https://cloud.eolab.de/remote.php/dav/files/your_nextcloud_username/
NEXTCLOUD_TARGET_DIR=/Photos
NEXTCLOUD_USERNAME=your_nextcloud_username
NEXTCLOUD_PASSWORD=your_nextcloud_app_password

# === Camera configuration (must be the same as in Neolink config.toml file) ===
LENS_0_NAME=tambopata-0 
LENS_1_NAME=tambopata-1

# === Local capture retention (in hours) ===
LOCAL_KEEP_HOURS=24
``` 

Finally after all these are set, you can then start the compose container again:

```bash
docker compose up -d
```

### Accessing Manual Controls

Assigning new presets will only be possible through the manual controls. After the compose container is up, the mqtt-client-manual container is still idle and will only run the python script on your command. 

```bash
docker compose exec -it mqtt-client-manual python client.py
```
Then you will be able to execute the available commands:

```bash
Commands:                       " up/down/left/right - PTZ control "
                                " z - Cycle zoom levels (1x/2x/3.5x) "
                                " a - Assign current position to a preset (will prompt for ID and name) "
                                " 0-8 - Go to PTZ preset position "
                                " r - Toggle IR mode (auto/on/off) "
                                " s - Trigger snapshot on both lenses "
                                " b - Query battery level "
                                " d - Perform custom daily capture sequence "
                                " help - Show this help message "
```

**IMPORTANT:**

You must disconnect and close the manual control MQTT connection using **CTRL+C** after you are done, if you forgot to do so and had closed the terminal without closing the connection, you have to restart the compose container to be able to access it again. Otherwise if you run the exec command again without closing the MQTT connection of the previous one, there will be an endless loop of client reconnection of the manual service.

You can detach after you close the connection with the sequence **CTRL+P -> CTRL+Q** and return to your shell.

Contributors will be welcomed and appreciated to fix this issue with the manual control service! :)

## References

### Neolink

https://github.com/QuantumEntangledAndy/neolink

### Paho Python MQTT Client

https://eclipse.dev/paho/files/paho.mqtt.python/html/index.html

### Mosquitto Local MQTT Broker

https://mosquitto.org/

### Docker Python API

https://docker-py.readthedocs.io/en/stable/index.html

### Camera used in this project

https://reolink.com/at/product/trackmix-series-b770/?srsltid=AfmBOorseGtuIwkLq2XB1J_5GkBWVwLwHaqyIHLHVOAukUpoaAaTwm7y#overview