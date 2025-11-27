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

1. Create new directory and cd to it

2. Clone this repo

```bash
git clone https://github.com/EOLab-HSRW/USERNAME-neolink-mqtt.git
```

3. the Mosquitto MQTT Broker service will expect this file to exist, we can use the touch command to create an empty file

```bash
touch ./mqtt/config/pwfile
```

Then change the file permission to “0700” as expected by the service

```bash
sudo chmod 0700 ./mqtt/config/pwfile
```

4. Build the docker compose images

```bash
docker compose build
```

5. Run the compose container detached

```bash
docker compose up -d
```

Then use exec to run commands on the MQTT Broker container

```bash
docker compose exec mosquitto sh
```

6. Create a new mosquitto user

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

7. Stop the compose container

```bash
docker compose stop
```

and continue to next subsection of the setup.

### Configuring Environment Variables and Neolink Config File

#### Credentials

After setting up the new user for the MQTT broker, you must modify the environtment variables of the client and the config file of Neolink so both of them can connect to the broker and the Neolink itself can connect to the Reolink Cameras. 

Additionally, since the captured images will be uploaded directly to the nextcloud drive using WebDAV, you must also set the credentials in the client's environment variables.

#### Preset Range and Scheduled Capture Times

Currently we only have 4 presets for the image capture automation (preset 0 to 3). After you have more/new presets that you had assigned with the manual controls, then you can edit the environment variables of the automation service in the **compose.yaml** file (START_PRESET and END_PRESET). 

You can also configure the daily capture time by editing the SCHEDULED_TIMES (A list of tuple with 24H format), be aware that the camera is 5 hour ahead than the local time (Camera time is UTC, Local time of Lima - Peru is UTC-05:00).

#### Set these up

- In config.toml:

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

- In compose.yaml:

```bash
mqtt-client-controller:
    # ............. 

    environment:
    # ..............
      - START_PRESET=0
      - END_PRESET=3
      - SCHEDULED_TIMES=16:30,17:00,17:30
      - NEXTCLOUD_WEBDAV_URL=https://cloud.eolab.de/remote.php/dav/files/YOUR_USERNAME/
      - NEXTCLOUD_TARGET_DIR=/Photos
      - NEXTCLOUD_USERNAME=***
      - NEXTCLOUD_PASSWORD=***
      - MQTT_USERNAME=***
      - MQTT_PASSWORD=***
    # .............
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

Thank you so much for the contributors and team from Neolink, Paho, and Mosquitto for making this project possible!

### Neolink

https://github.com/QuantumEntangledAndy/neolink

### Paho Python MQTT Client

https://eclipse.dev/paho/files/paho.mqtt.python/html/index.html

### Mosquitto Local MQTT Broker

https://mosquitto.org/