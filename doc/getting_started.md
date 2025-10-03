# Overview
See `docs/project.md` for an overview of the project.

This scripts in this project are designed to run on a laptop/workstation connected to the cluster over ssh (so the code is on your laptop and it gets rsynced to the control node before each command)

# Workstation Setup

The core development tool is `scripts/cluster.py` which is designed to abstract over different hardware versions and network conditions. In order to work, `cluster.py` needs a few preconditions to be met:

## Set up SSH configs for hardware versions
To use `scripts/cluster.py` with hardware version 0.2 (4x Ryzen 395) you need an ssh config for `control_0_2` in `~/.ssh/config` (or wherever your ssh config is, if not here). My config for hardware 0.1 looks like this:

```bash
Host control_0_1
  HostName 192.168.8.99
  User forrest
  Port 22
```

This can be any configuration you want for remote development or whatever, as long as the `Host` line is correct and you can ssh to the control node with `ssh control_0_1` (or `ssh control_0_2` for hardware 0.2)

## Init venv
Run `scripts/init.sh` from the root of the project directory to create the python venv for `cluster.py` and install the dependencies. You must source this venv before running `cluster.py`

## (OPTIONAL) modify your environment for better ergonomics.
I have the following in my `.zshrc` to improve ergonomics

```bash
export PATH="$PATH:/path/to/onedotzero"
alias onedotzero='source /path/to/onedotzero/.venv/bin/activate'
```

which lets me run type `onedotzero` to source the venv and `cluster <command>` to mess with the cluster. Optional but further commands in this doc are written assuming it, so if you dont do it then just mentally adjust commands

## Set a hardware version
`cluster.py` controls which hardware it targets by storing a string like "0.1" or "0.2" in `.hardware_version`, when running commands it will read this string and use it to resolve an SSH host to connect to as well as a hardware configuration yml like `ansible/hardware_vars/0.1.yml`. You can write this file manually, or use the helper

``` bash
(.venv) % cluster hardware set 0.2
Hardware version set to '0.2'.
(.venv) % cluster hardware get
0.2
```

You must set this before you will be able to configure the cluster.

See `docs/hardware_abstraction.md` for more details

# Cluster Setup

## Control Node Setup
Control node must have two network interfaces, one facing the internet and one facing the compute nodes. The user you use in your ssh profile must be configured for key based ssh from your workstation and set up for passwordless sudo (ansible cant configure passwordless sudo without passwordless sudo)

## Compute Node Setup
Compute nodes must have secure boot disabled and be configured for IPV4 PXE booting and Wake On LAN on the interface that is connected to the control node. I recommend disabling IPV6 PXE booting and all non-PXE booting options though this is not strictly neccessary. All of these changes must be applied in the BIOS of each compute node.

## Basic Workflows

# Configure the Control node
To configure the control node:

```bash
# Install libraries and configure networking and boot servers
cluster control configure

# build and configure the golden ubuntu image to serve to compute nodes
cluster control build_image
```

At this point the control node should be ready to netboot compute nodes. You also have the following commands:

```bash
# Test that the control node is configured correctly
cluster control test

# Open an interactive ssh shell to the control node
cluster control ssh

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

#
