# Disable TM Camera kiosk mode

Run this on the Raspberry Pi as an admin user if you need to disable kiosk auto-login:

```bash
sudo rm -f /etc/lightdm/lightdm.conf.d/99-tm-camera-kiosk.conf
sudo rm -f /home/operator/.config/autostart/tm-camera-kiosk.desktop
sudo systemctl disable tm-camera-monitor.service
sudo reboot
```

This does not delete recordings or remove the project files.
