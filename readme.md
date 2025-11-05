# RTSP-MQTT Connection using Neolink for Daily Image Capture Automation of Tambopata Tower's Surveillance Camera

This project is compiling a compose container that includes a Neolink, Mosquitto MQTT Broker, and Python MQTT Client services. Neolink is a small program that acts as a proxy between Reolink IP cameras and normal RTSP clients. The neolink will be able to communicate to the users (as the MQTT client) through the MQTT Broker. 

List of commands available:

```bash
Commands:                       " up/down/left/right - PTZ control "
                                " z - Cycle zoom levels (1x/2x/3.5x) "
                                " a - Assign current position to a preset (will prompt for ID and name) "
                                " 0-8 - Go to PTZ preset position "
                                " r - Toggle IR mode (auto/on/off) "
                                " s - Trigger snapshot on both lenses "
                                " b - Query battery level "
                                " p - Request presets report "
                                " c - Configure preset range for the daily capture sequence "
                                " d - Perform custom daily capture sequence "
                                " help - Show this help message "
```


## Setup 

### Docker

https://docs.docker.com/engine/install/

### Neolink

https://github.com/QuantumEntangledAndy/neolink

- **Ubuntu/Debian**: 

```bash
sudo apt install \
  libgstrtspserver-1.0-0 \
  libgstreamer1.0-0 \
  libgstreamer-plugins-bad1.0-0 \
  gstreamer1.0-x \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  libssl-dev
```

### Compose Container

1. Create new directory and cd to it

2. Clone this repo

3. the Mosquitto MQTT Broker container will expect this file to exist, we can use the touch command to create an empty file.

```bash
sudo touch ./mqtt/config/pwfile
```

4. Once the file is created our next step is to change the files permissions to “0700” as expected the MQTT software.

```bash
sudo chmod 0700 ./mqtt/config/pwfile
```

5. Run the compose container detached

```bash
docker compose up -d
```

Then attach to the MQTT Broker container

```bash
docker compose exec mosquitto sh
```

6. Create new mosquitto user

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

8. Attach to the MQTT Client container 

```bash
docker compose exec mqtt-client sh
```

Finally, run the client with:

```bash
python client.py
```

**Make sure you are connected to the VPN if using university's WiFi**

https://wiki.eolab.de/doku.php?id=snapcon2022:openvpn-connection&s[]=vpn
