# Neolink

![CI](https://github.com/QuantumEntangledAndy/neolink/workflows/CI/badge.svg)
[![dependency status](https://deps.rs/repo/github/QuantumEntangledAndy/neolink/status.svg)](https://deps.rs/repo/github/QuantumEntangledAndy/neolink)

Neolink is a small program that acts as a proxy between Reolink IP cameras and
normal RTSP clients.
Certain cameras, such as the Reolink B800, do not implement ONVIF or RTSP, but
instead use a proprietary "Baichuan" protocol only compatible with their apps
and NVRs (any camera that uses "port 9000" will likely be using this protocol).
Neolink allows you to use NVR software such as Shinobi or Blue Iris to receive
video from these cameras instead.
The Reolink NVR is not required, and the cameras are unmodified.
Your NVR software connects to Neolink, which forwards the video stream from the
camera.

The Neolink project is not affiliated with Reolink in any way; everything it
does has been reverse engineered.

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

3. the MQTT Broker container will expect this file to exist, we can use the touch command to create an empty file.

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
