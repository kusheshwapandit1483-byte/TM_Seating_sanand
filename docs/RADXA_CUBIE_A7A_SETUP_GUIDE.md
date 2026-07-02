# Radxa Cubie A7A Camera Monitor Setup Guide

This guide explains how to set up the TM Seating Sanand camera monitor on a Radxa Cubie A7A from a fresh board. It is written for a new technician who may not have prior Linux, Radxa, MediaMTX, or RTSP experience.

## 1. System Overview

The system has four main parts:

```text
IP Camera
  -> RTSP stream over Ethernet
  -> MediaMTX running on Radxa
  -> Browser live preview through WebRTC/HLS
  -> Python camera monitor app records RTSP locally with ffmpeg
```

The browser must not open the camera RTSP URL directly. Chrome/Chromium displays the stream through MediaMTX:

```text
Live preview through MediaMTX:
http://127.0.0.1:8889/pramacam

Main application page:
http://127.0.0.1:8080
```

From another laptop on the same Wi-Fi network, replace `127.0.0.1` with the Radxa Wi-Fi IP address.

## 2. Required Hardware

Use the following hardware:

- Radxa Cubie A7A board
- Radxa-compatible power adapter, preferably 5V/3A or better
- microSD card with Radxa Debian image installed
- HDMI monitor
- USB keyboard and mouse
- Ethernet cable
- IP camera
- Camera power supply, PoE injector, or PoE switch, depending on camera type
- Wi-Fi network for remote access or app viewing from laptop

Important: if the camera is a PoE camera, the Radxa Ethernet port cannot power it. Use a PoE switch or PoE injector.

## 3. Fixed IP Plan

Use this IP plan unless the site network requires a different one:

```text
Camera IP:             192.168.10.20
Radxa Ethernet IP:     192.168.10.50
Radxa Wi-Fi IP:        Assigned by Wi-Fi router, for example 192.168.1.51
MediaMTX local RTSP:   rtsp://127.0.0.1:8554/pramacam
MediaMTX WebRTC page:  http://127.0.0.1:8889/pramacam
Main app page:         http://127.0.0.1:8080
```

The camera and Radxa Ethernet must be in the same range:

```text
192.168.10.x
```

The Wi-Fi IP may be different, for example:

```text
192.168.1.x
```

This is normal. Use Wi-Fi IP for accessing the app from another laptop. Use Ethernet IP for camera communication.

## 4. First Boot Checks

Open a terminal on the Radxa:

```text
Ctrl + Alt + T
```

Check the IP addresses:

```bash
hostname -I
```

Check network connections:

```bash
nmcli con show
nmcli device status
```

If the terminal asks for a password after a `sudo` command, enter the Radxa password. The password will not show while typing.

## 5. Set Permanent Ethernet IP

First find the exact Ethernet connection name:

```bash
nmcli con show
```

The name is usually:

```text
Wired connection 1
```

Linux is case-sensitive. `Wired Connection 1` is not the same as `Wired connection 1`.

Set the Ethernet IP permanently:

```bash
sudo nmcli con mod "Wired connection 1" ipv4.method manual ipv4.addresses 192.168.10.50/24
sudo nmcli con mod "Wired connection 1" ipv4.gateway ""
sudo nmcli con mod "Wired connection 1" ipv4.dns ""
sudo nmcli con down "Wired connection 1"
sudo nmcli con up "Wired connection 1"
```

Verify:

```bash
hostname -I
```

You should see:

```text
192.168.10.50
```

Test the camera:

```bash
ping 192.168.10.20
```

Stop ping:

```text
Ctrl + C
```

If you see `Destination Host Unreachable`, check the Ethernet cable, camera power, PoE injector/switch, and camera IP address.

## 6. Install Required Packages

Install Git and ffmpeg:

```bash
sudo apt update
sudo apt install git ffmpeg -y
```

Verify ffmpeg:

```bash
ffmpeg -version
```

If the app shows `No such file or directory: 'ffmpeg'`, ffmpeg is missing or not installed correctly.

## 7. Download Project Code

Go to the home directory:

```bash
cd ~
```

Clone the project from GitHub:

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git TM_Seating_sanand
```

Enter the project folder:

```bash
cd ~/TM_Seating_sanand
```

If the project already exists and you only need to update it:

```bash
cd ~/TM_Seating_sanand
git pull
```

Note: if the GitHub repository is private, GitHub requires a Personal Access Token instead of the normal account password.

## 8. Install MediaMTX

Use MediaMTX to bridge the camera RTSP stream into browser-friendly WebRTC/HLS.

For Radxa Cubie A7A, check CPU type:

```bash
uname -m
```

If it shows:

```text
aarch64
```

download:

```text
mediamtx_v1.11.2_linux_arm64v8.tar.gz
```

Manual download page:

```text
https://github.com/bluenviron/mediamtx/releases/tag/v1.11.2
```

Place the extracted MediaMTX folder inside:

```text
~/TM_Seating_sanand/
```

Expected folder:

```text
~/TM_Seating_sanand/mediamtx_v1.11.2_linux_arm64v8
```

Make the executable runnable:

```bash
cd ~/TM_Seating_sanand/mediamtx_v1.11.2_linux_arm64v8
chmod +x mediamtx
```

## 9. Configure MediaMTX

Open the MediaMTX config:

```bash
cd ~/TM_Seating_sanand/mediamtx_v1.11.2_linux_arm64v8
nano mediamtx.yml
```

Find the `paths:` section and configure the path name as exactly:

```yaml
paths:
  pramacam:
    source: rtsp://USERNAME:PASSWORD@192.168.10.20:554/STREAM_PATH
```

Replace:

```text
USERNAME
PASSWORD
STREAM_PATH
```

with the real camera username, password, and stream path.

Common stream paths:

```text
Hikvision main stream: /Streaming/Channels/101
Hikvision sub stream:  /Streaming/Channels/102
Dahua main stream:     /cam/realmonitor?channel=1&subtype=0
Dahua sub stream:      /cam/realmonitor?channel=1&subtype=1
```

Important: the path must be `pramacam`, not `paracam`. The app records from:

```text
rtsp://127.0.0.1:8554/pramacam
```

Save in nano:

```text
Ctrl + O
Enter
Ctrl + X
```

## 10. Start MediaMTX

Open terminal 1:

```bash
cd ~/TM_Seating_sanand/mediamtx_v1.11.2_linux_arm64v8
./mediamtx
```

Keep this terminal open.

Expected successful signs:

```text
MediaMTX v1.11.2
configuration loaded
[path pramacam] [RTSP source] started
```

If you see:

```text
listen udp :8000: bind: address already in use
```

another MediaMTX process is already running. Stop it:

```bash
pkill mediamtx
./mediamtx
```

Test live stream in Chromium:

```text
http://127.0.0.1:8889/pramacam
```

If you see the camera footage here, MediaMTX is working.

## 11. Start the Camera Monitor App

Open terminal 2:

```bash
cd ~/TM_Seating_sanand
python3 server.py
```

Expected output:

```text
Camera Monitor running at http://0.0.0.0:8080
Recording source: rtsp://127.0.0.1:8554/pramacam
Recordings folder: /home/radxa/TM_Seating_sanand/recordings
Retention: 2 day(s), maximum 2 day(s)
```

Open the app on the Radxa:

```text
http://127.0.0.1:8080
```

Open the app from another laptop on the same Wi-Fi:

```text
http://RADXA_WIFI_IP:8080
```

Example:

```text
http://192.168.1.51:8080
```

Do not open `index.html` directly. If the browser shows HTML code, you opened the file directly. Use:

```text
http://127.0.0.1:8080
```

## 12. Recording Behavior

The app records from:

```text
rtsp://127.0.0.1:8554/pramacam
```

Default segment length:

```text
30 minutes
```

This means the app creates one MP4 file every 30 minutes:

```text
recordings/data_YYYY-MM-DD_HH-MM-SS.mp4
```

A recording may not play while it is still being written. Wait until the 30-minute segment finishes before testing playback.

The app prepares browser playback using HLS cache under:

```text
recordings/.hls
```

If playback fails for old or broken files, clear old recordings:

```bash
cd ~/TM_Seating_sanand
rm recordings/*.mp4
rm -rf recordings/.hls
```

Then restart MediaMTX and the app.

## 13. Recording Size and Storage Planning

Measured Radxa recording size:

```text
30 minutes = about 950 MB
1 hour     = about 1.9 GB
24 hours   = about 45.6 GB
2 days     = about 91.2 GB
```

Recommendation:

```text
64 GB card:  keep 1 day
128 GB card: keep 2 days, but monitor free space
```

If Raspberry Pi recordings were smaller, for example 350 MB per 30 minutes, the camera stream used there was probably lower bitrate or sub stream. The app uses `ffmpeg -c copy`, so it saves the stream exactly as received.

To reduce file size while keeping 30-minute recordings, change the camera stream or bitrate:

```text
Target bitrate: 1500 kbps to 1800 kbps for about 350 MB per 30 minutes
```

For Hikvision, use sub stream:

```text
/Streaming/Channels/102
```

For Dahua, use sub stream:

```text
/cam/realmonitor?channel=1&subtype=1
```

## 14. Normal Startup Order

Always start in this order:

1. Camera powered on and connected
2. Radxa Ethernet active at `192.168.10.50`
3. MediaMTX running
4. Camera monitor app running
5. Browser opened at `http://127.0.0.1:8080`

Commands:

Terminal 1:

```bash
cd ~/TM_Seating_sanand/mediamtx_v1.11.2_linux_arm64v8
./mediamtx
```

Terminal 2:

```bash
cd ~/TM_Seating_sanand
python3 server.py
```

Browser:

```text
http://127.0.0.1:8080
```

## 15. Troubleshooting

### Browser shows HTML code

Cause: `index.html` was opened directly.

Fix:

```text
Open http://127.0.0.1:8080
```

### Live works in MediaMTX but app has no video

Check that MediaMTX path is exactly:

```text
pramacam
```

Test:

```text
http://127.0.0.1:8889/pramacam
```

If MediaMTX config says `paracam`, change it to `pramacam`.

### ffmpeg says 404 Not Found

Error:

```text
rtsp://127.0.0.1:8554/pramacam: Server returned 404 Not Found
```

Cause: MediaMTX does not have a path named `pramacam`, or MediaMTX is not running.

Fix:

```bash
pkill mediamtx
cd ~/TM_Seating_sanand/mediamtx_v1.11.2_linux_arm64v8
./mediamtx
```

Confirm it says:

```text
[path pramacam]
```

### ffmpeg says connection refused

Error:

```text
Connection to tcp://127.0.0.1:8554 failed: Connection refused
```

Cause: MediaMTX is stopped.

Fix: start MediaMTX first, then start the Python app.

### Could not start ffmpeg

Error:

```text
No such file or directory: 'ffmpeg'
```

Fix:

```bash
sudo apt update
sudo apt install ffmpeg -y
```

### MediaMTX says UDP 8000 already in use

Error:

```text
listen udp :8000: bind: address already in use
```

Fix:

```bash
pkill mediamtx
./mediamtx
```

### Camera ping fails

Command:

```bash
ping 192.168.10.20
```

Possible causes:

- Camera is not powered
- PoE injector or PoE switch is missing
- Ethernet cable is loose
- Camera IP is not `192.168.10.20`
- Radxa Ethernet IP is not `192.168.10.50`

Check:

```bash
hostname -I
nmcli device status
```

### Recording playback unavailable

Possible causes:

- The 30-minute MP4 is still being written
- The file is old/broken from a previous failed run
- MediaMTX was stopped during recording
- ffmpeg could not prepare HLS playback

Check logs:

```bash
cd ~/TM_Seating_sanand
tail -80 recordings/recorder.log
ls -lh recordings
```

Clear broken recordings:

```bash
rm recordings/*.mp4
rm -rf recordings/.hls
```

Restart MediaMTX first, then the app.

## 16. Useful Commands

Check Radxa IP:

```bash
hostname -I
```

Check network connections:

```bash
nmcli con show
nmcli device status
```

Test camera:

```bash
ping 192.168.10.20
```

Stop ping:

```text
Ctrl + C
```

Stop MediaMTX:

```bash
pkill mediamtx
```

Stop Python server:

```text
Ctrl + C
```

Check recordings:

```bash
ls -lh ~/TM_Seating_sanand/recordings
```

Check recorder log:

```bash
tail -80 ~/TM_Seating_sanand/recordings/recorder.log
```

Check free disk space:

```bash
df -h
```

## 17. Final Verification Checklist

Before handing over the system, verify:

- Radxa boots normally
- Ethernet IP remains `192.168.10.50` after reboot
- Camera responds to `ping 192.168.10.20`
- MediaMTX starts without errors
- `http://127.0.0.1:8889/pramacam` shows live camera
- `python3 server.py` starts without ffmpeg errors
- `http://127.0.0.1:8080` opens the TM camera monitor page
- Live view works in the app
- A completed 30-minute recording appears in Recordings
- Completed recording plays in the browser
- Retention is set according to available storage

