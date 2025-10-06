# Script de instalación de paquetes de Bruno
# Guarda esto como install_packages.sh

#!/bin/bash

# Actualizar el sistema primero
sudo apt update
sudo apt upgrade -y

# Instalaciones de paquetes
sudo apt install -y build-essential zsh neovim
sudo apt install -y bat
sudo apt install -y tldr
sudo apt install -y openjdk-8-jdk unzip openjdk x11-apps
sudo apt install -y openssh-client
# sudo apt install -y redis-tools
# 2021-03-24 09:50:08	apt install -yq libicu[0-9] libkrb5-3 zlib1g
# 2021-03-24 09:50:14	apt install -yq gnome-keyring libsecret-1-0
# 2021-04-03 13:22:52	apt-get install -y libgbm-dev

# sudo apt install -y gnome-keyring
# sudo apt install -y mono-complete
# sudo apt install -y iq maven python3.9 python2
sudo apt install -y maven net-tools software-properties-common
sudo apt install -y python make g++
# sudo apt install -y libxsst
# sudo apt install -y fonts-noto-color-emoji
# sudo apt install -y wine32
sudo apt install -y pip
sudo apt install -y silversearcher-ag
# sudo apt install -y mesa-utils # es para graficas AMD-radeon
sudo apt install -y openjdk-11-jdk
sudo apt install -y libc6
# instalar Cloud Foundry CLI
wget -q -O - https://packages.cloudfoundry.org/debian/cli.cloudfoundry.org.key | sudo gpg --dearmor -o /usr/share/keyrings/cli.cloudfoundry.org.gpg
echo "deb [signed-by=/usr/share/keyrings/cli.cloudfoundry.org.gpg] https://packages.cloudfoundry.org/debian stable main" | sudo tee /etc/apt/sources.list.d/cloudfoundry-cli.list
sudo apt-get update
sudo apt install -y cf8-cli
cf install-plugin -f multiapps
# bash <(curl -s https://raw.githubusercontent.com/CodelyTV/dotly/HEAD/installer)
# 2024-03-01 13:13:37	apt-get install -y --no-install-recommends fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 libflac8 libgdk-pixbuf2.0-0 libwoff1 libxkbcommon0 libxmu6 libxshmfence6 libxtst6 libevent-2.1-7
# 2024-04-24 21:23:03	apt install libgtk-3-dev libnotify-dev libconf-2-4 libss3 libsound2 libgstreamer-plugins-bad1.0-0 gstreamer1.0-libav -y
# Instalación de .deb locales (Asegúrate de tener el archivo en el mismo directorio)
# sudo dpkg -i ./exa_0.9.0-4_amd64.deb

mkdir ~/programs
mkdir ~/programs/homebrew && curl -L https://github.com/Homebrew/brew/tarball/main | tar xz --strip-components 1 -C ~/programs/homebrew

#uninstall python from cargo
# cargo uninstall python

brew install denisidoro/tools/docpars
brew install fzf


curl -fsSL https://raw.githubusercontent.com/zimfw/install/master/install.zsh | zsh

# instalar dotly y setear configuracion
git clone https://github.com/chiptime/dotfiles $HOME/.dotfiles
cd $HOME/.dotfiles
git submodule update --init --recursive modules/dotly
DOTFILES_PATH="$HOME/.dotfiles" DOTLY_PATH="$HOME/.dotfiles/modules/dotly" "$HOME/.dotfiles/modules/dotly/bin/dot" self install


brew install eza