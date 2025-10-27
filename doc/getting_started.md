# Overview
See `docs/project.md` for an overview of the project.

This scripts in this project are designed to run on a laptop/workstation connected to the cluster over ssh (so the code is on your laptop and it gets rsynced to the control node before each command)

# Workstation Setup

The core development tool is `scripts/odz.py` which is designed to abstract over different hardware versions and network conditions. In order to work, `odz.py` needs a few preconditions to be met:

## Set up SSH configs for hardware versions
To use `scripts/odz.py` with hardware version 0.2 (4x Ryzen 395) you need an ssh config for `control_0_2` in `~/.ssh/config` (or wherever your ssh config is, if not here).

```bash
Host control_0_2 odz_build odz_test
  HostName "<control node ip address>"
  User "<control node user>"
  Port "<control node port>"
```

My config for hardware 0.1 looks like this:

```bash
Host control_0_1 odz_build odz_test
  HostName 192.168.8.99
  User forrest
  Port 22
```

This can be any configuration you want for remote development or whatever, as long as the `Host` line is correct and you can ssh to the control node with `ssh control_0_1` (or `ssh control_0_2` for hardware 0.2)

Note that there are 3 hostnames associated with the host. this uses the control node for building images and remote testing, but these can be different hosts (I do `odz_build` and `odz_test` as different users on the same machine to reduce conflicts when multitasking, but doing all three hosts to the same user/host is fine if you are doing one thing at a time)

## Init venv
Run `scripts/init.sh` from the root of the project directory to create the python venv for `odz.py` and install the dependencies. You must source this venv before running `odz.py`. Alternative you can run `/bin/odz` which is an executable shell wrapper that sets up the venv

## (OPTIONAL) modify your environment for better ergonomics.
You can source `scripts/environment.sh` to set up your environment to make running commands easier. I have the following in my `.zshrc` to improve ergonomics

```bash
alias odzenv="(cd ~/workspace/onedotzero && source scripts/environment.sh && ${SHELL} -i)"
alias odzenv_alt="(cd ~/workspace/onedotzero_alt && source scripts/environment.sh && ${SHELL} -i)"
```

which lets me run type `odzenv` to source the venv and then I can run `odz <command>` to mess with the cluster, and `exit` will reset the enviroment and bring me back to the cwd from whence i called `odzenv`. `~/workspace/onedotzero` and `~/workspace/onedotzero_alt` are just two checkouts of the same git repo that I use for A and B tasks (e.g. I use `odzenv` for cluster development and `odzenv_alt` to work on testing/documentation/cleanup tasks while i wait for images to build and the cluster to provision etc.)

All of this is optional but if you see `odz foo` later in the document it is assuming that the `bin` dir is in your path and the python venv is sourced. If you dont do this step just remember to source the venv and run `./bin/odz foo`

## Set a hardware version
`odz.py` controls which hardware it targets by storing a string like "0.1" or "0.2" in `.hardware_version`, when running commands it will read this string and use it to resolve an SSH host to connect to as well as a hardware configuration yml like `ansible/hardware_vars/0.1.yml`. You can write this file manually, or use the helper

``` bash
(.venv) % odz hardware set 0.2
Hardware version set to '0.2'.
(.venv) % odz hardware get
0.2
```

You must set this before you will be able to configure the odz.

See `docs/hardware_abstraction.md` for more details

# Cluster Setup

## Control Node Setup
Control node must have two network interfaces, one facing the internet and one facing the compute nodes. The user you use in your ssh profile must be configured for key based ssh from your workstation and set up for passwordless sudo (ansible cant configure passwordless sudo without passwordless sudo)

```bash
# Enable passwordless sudo for the current user
echo "$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee "/etc/sudoers.d/$USER" > /dev/null

# install ansible
sudo apt install -y ansible
```

## Compute Node Setup
Compute nodes must have secure boot disabled and be configured for IPV4 PXE booting and Wake On LAN on the interface that is connected to the control node. I recommend disabling IPV6 PXE booting and all non-PXE booting options though this is not strictly neccessary. All of these changes must be applied in the BIOS of each compute node.

## Basic Workflows

# Selecting a hardware target:
```bash
# this modulates the targets of `odz control` and `odz compute` commands
odz hardware set 0.2
```


# Building a bootable image:
```bash
# build and configure the golden ubuntu image to serve to the 0.2 compute nodes
odz image build 0.2

# copy image to 0.2 control node for serving:
odz image copy 0.2

# perform a full clean of the 0.2 image
odz image clean 0.2
```


# Configure the Control node
To configure the control node:

```bash
# Install libraries and configure networking and boot servers
# make sure you set a hardware version first
odz control configure
```

At this point the control node should be ready to netboot compute nodes. You also have the following commands:

```bash
# Open an interactive ssh shell to the control node
odz control ssh

# run a command on the control node over SSH in the recently rsynced directory
cluster control cmd "pwd && ls"

# completely remove the golden image (including stopping everything exporting it)
# note that you must run `cluster control build_image && cluster control configure` after this to recover the bootable state
cluster control clean image
```

# Compute Node Power Management

```bash
# power on the compute nodes via WOL
cluster compute up

# shutdown the compute nodes over ssh (requires them to have fully booted)
cluster compute down

# restart compute nodes over SSH
cluster compute restart
```

# Testing
