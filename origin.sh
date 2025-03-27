sudo useradd -m origin

sudo pacman -Syuu --noconfirm
sudo pacman -S cage chromium foot wget python python-flask python-flask-cors python-requests python-websockets --noconfirm

mkdir /etc/systemd/system/getty@tty1.service.d/
echo "[Service]
ExecStart=
ExecStart=-/usr/bin/agetty --autologin origin --noclear %I $TERM" >/etc/systemd/system/getty@tty1.service.d/override.conf
sudo systemctl enable getty@tty1

rm ~/linker.py
wget https://raw.githubusercontent.com/RoturTW/roturLink/refs/heads/main/linker.py

mkdir -p /home/origin/.themes
mv -f dots/theme /home/origin/.themes/theme

mkdir -p /home/origin/.config
mv -f dots/labwc /home/origin/.config/labwc

echo "cage -d -- chromium --new-window --start-fullscreen --app=https://origin.mistium.com &" >> /home/origin/.bashrc
echo "python3 linker.py" >> /home/origin/.bashrc
echo "echo Welcome to the Origin Linux developer terminal! Please keep in mind a couple of things:" >> /home/origin/.bashrc
echo "echo 1. We do not provide support if you use this" >> /home/origin/.bashrc
echo "echo 2. When you're finished, type 'exit'" >> /home/origin/.bashrc
echo "echo 3. Have fun! Linux is a sandbox, make the most of it" >> /home/origin/.bashrc

reboot
