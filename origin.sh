# Add origin user
sudo useradd -m origin
sudo usermod --password origin origin

sudo rm -rf /etc/apt/sources.list
sudo touch /etc/apt/sources.list
sudo echo "deb https://deb.debian.org/debian unstable main" > /etc/apt/sources.list

sudo apt update && sudo apt upgrade
sudo apt install labwc chromium foot wget python3-flask python3-flask-cors python3-requests python3-websockets zenity st nm-connetion-editor -y

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

mv -f startup.sh ../startup.sh
mv -f linker.py ../linker.py

echo "sh startup.sh" >> /home/origin/.bashrc
echo "python3 linker.py" >> /home/origin/.bashrc
echo "echo Welcome to the Origin Linux developer terminal! Please keep in mind a couple of things:" >> /home/origin/.bashrc
echo "echo 1. We do not provide support if you use this" >> /home/origin/.bashrc
echo "echo 2. When you're finished, type 'exit'" >> /home/origin/.bashrc
echo "echo 3. Have fun! Linux is a sandbox, make the most of it" >> /home/origin/.bashrc

reboot
