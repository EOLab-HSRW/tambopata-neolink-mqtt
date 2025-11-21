# RTSP-MQTT Connection using Neolink for Daily Image Capture Automation of Tambopata Tower's Surveillance Camera

This project compiles Neolink, Mosquitto MQTT Broker, and Python MQTT Client services in a Docker compose container. Neolink is a small program that acts as a proxy between Reolink IP cameras and normal RTSP clients. The neolink will communicate with the client through the MQTT Broker. 

The client has a scheduler loop that runs in the background to automatically capture images from different PTZ presets every noon (12.00) both when the infrared light is on and off (alternating between both). In addition to that, some manual controls are accessible when you attach to the service.

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
mosquitto_passwd -c /mosquitto/config/pwfile USERNAME
```

After running the above command, you will be prompted to type in a password for this user. Type the password below:

```bash
Password: PASSWORD
Reenter password: PASSWORD
```

Then exit the bash

```bash
exit
```

7. Restart the compose container

```bash
docker compose restart
```

### Configuring The Preset Range

Currently we only have 4 presets for the image capture automation (preset 0 to 3). When you have more presets that you can assign through accessing the manual controls, you can edit the environment variables of the automation service in the **compose.yaml** file (START_PRESET and END_PRESET):

```bash
mqtt-client-controller:
    # ............. 

    environment:
      - NEOLINK_MODE=controller
      - START_PRESET=0 
      - END_PRESET=3

    # .............
```

### Accessing Manual Controls

Assigning new presets will only be possible through the manual controls. After the compose container is up, you can attach to the manual control service container with:

```bash
docker attach mqtt-client-manual
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

You can detach after you are done with the sequence **CTRL+P -> CTRL+Q** to return to your shell.

**IMPORTANT:**

If you accidentally press **CTRL+C** and disconnect, you have to restart the compose container.

## References

### Neolink

https://github.com/QuantumEntangledAndy/neolink

### Paho Python MQTT Client

https://eclipse.dev/paho/files/paho.mqtt.python/html/index.html

### Mosquitto Local MQTT Broker

https://mosquitto.org/