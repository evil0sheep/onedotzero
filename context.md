# Overview
This repository exists to explore clustered LLM inference and do performance analysis to find bottlenecks.

We will be working through the TODO list as a team with you writing most of the code while I direct and review. Please read each task in the todo list one-by-one and for each item think about what is most ambiguous about the task description and then ask disambiguating questions which clarify the goals and requirements. Once your questions have been answered you can proceed with the task and when you are done please stop and ask for review, and once approved you can check off that task with a ✅ at the beginning of the task description and proceed to the next task. For tasks with nested sub tasks please work through each nested sub task one by one and when you complete the last subtask you can check off the top level task as well and proceed to the next top level task.

# Cluster Topology

The cluster is a small beowulf cluster composed of 2 compute nodes (hostnames `node0` and `node1`) based on MSI MPG Z690 Carbon WiFi Gaming Motherboard with a single Intel Arc A770 GPU connected via their onboard ethernet to a switch which is also connected to the USB interface `enx8cae4cf44e21` of a NUC (hostname `nuc`) acting as the control node and the gateway. The compute nodes also have a high speed USB4 host-to-host link we will use for clustering.

The plan is for the compute nodes to PXE boot a shared linux image off of the NUC and then be configured with Ansible over SSH. The control node `nuc` will host the inference web interface and the codebase we are working in (i.e. if you run hostname in your shell you will get `nuc`)

We will be developing ansible playbooks to configure the cluster described here but we will then use the same scripts to configure a 4 node fully connected cluster of Ryzen 395 AI max boards with a different control node so we want to keep the scripts hardware agnostic where possible (or for things that arent hardware agnostic like setting up GPU drivers we just want to keep them isolated)

# Workflow Description
We will be working on my macbook editing files locally and then rsyncing them to the NUC and executing them remotely over SSH using the `remote.py` script we will develop in step 0 of the todo list.

# PXE Booting and Development Workflow

To ensure a fast and efficient development cycle, we will adopt a two-phase approach for configuring the compute nodes. This allows for rapid iteration while developing Ansible playbooks, followed by the creation of a stable, fast-booting production environment.

## Phase 1: Live Development & Testing

The primary goal of this phase is to create and validate our Ansible playbooks. To achieve a fast feedback loop, we will PXE boot the compute nodes into a **live Ubuntu environment**.

- **Process:** The node boots the standard Ubuntu Server ISO directly into a usable OS that runs entirely in RAM. No changes are made to the node's internal SSD.
- **Workflow:** We can SSH into this live environment to repeatedly run and debug our Ansible playbooks. A simple reboot wipes all changes, providing a perfectly clean slate for testing the entire configuration process from scratch. This avoids the slow process of running a full OS installation for every minor change.
- **Default Mode:** This "Live Boot" will be the default PXE boot option to facilitate easy development.

## Phase 2: Golden Image Creation (Production)

Once the Ansible playbooks are mature and reliable, we will create a "golden image" for production use. This provides fast, consistent, and centrally managed boots for the entire cluster.

- **Process:**
    1.  **Build:** On the control node, we will create a base Ubuntu 22.04 filesystem in a directory using a tool like `debootstrap`.
    2.  **Configure:** We will use `chroot` to enter this directory and run our validated Ansible playbooks inside it. This configures the image with all necessary drivers, libraries, and software without needing a VM or a live boot.
    3.  **Serve:** This configured directory (the "golden image") is then served over the network using NFS.
- **Booting:** The compute nodes will PXE boot a kernel and initrd, which will then mount the NFS share as their root filesystem. The entire OS will run over the network from the golden image.
- **Storage:** The internal SSDs on the compute nodes will be mounted separately (e.g., at `/models`) for persistent storage of large files like model weights, keeping the OS itself stateless.

This two-phase strategy allows us to combine rapid, iterative development with a robust, scalable, and easily maintainable production cluster environment.

# TODO
✅ 0. Create an exaecutably python script `remote.py` in the CWD which takes command string as an argument, rsyncs the current working directory (~/workspace/onedotzero) to `control:~/remote/onedotzero`, connects to `control`, cd's to `~/remote/onedotzero`, and executes the command string in a shell. So, for example, `remote.py ls` and `ls` should always show the same directory state. `remote` is an ssh Host defined in `~/.ssh/config` currently pointing at `nuc` but lets use this abstraction always.
1. Create an ansible playbook which takes an interface name (like `enx8cae4cf44e21`) in  a vars file (call it `compute_interface`) and configures the host to PXE boot compute nodes over that interface. This will run on `nuc` (the Ansible control node) in this setup. We want this script to do everything needed to allow a compute node configured for PXE boot booted to ubuntu 22.04 with an open SSH port so that we can configure it with ansible.  You can assume the control node is also running ubuntu. This script should do the following tasks:
  * Install any necessary tools (e.g. dnsmasq nginx syslinux-common pxelinux etc)
  * Create TFTP and HTTP directories to serve an ubuntu image
  * download an Ubuntu 22.04 iso and mount it for booting
  * set up the TFTP directory with the bootloader/kernel/initrd etc
  * ensure nothing is using any of the needed ports on the supplied interface.
  * Configure, enable, and start nginx to serve the HTTP directory
  * Configure dnsmasq for serving DHCP and the TFTP assets on the supplied interface (use the 192.168.1.0/24 subnet)
  * Create the ubuntu auto install file to tell the Ubuntu installer to set up a user, install SSH, and not ask any questions. Use the ssh public key from ~/.ssh/. if there is no public key there stop and print an error telling the user to create one. use username admin password admin (the compute nodes are on a private network segment and thus not vulnerable)
  * create the PXE boot menu
Once done with that you can run the playbook and fix any errors that occur (though if services need to be disabled or ssh keys created let me do it please). Once the script runs we will attempt to PXE boot a compute node and ssh into it.
2. Use ansible to configure compute nodes (everything reachable on `compute_interface`) to allow wake one LAN (bios settings are already setup). When done we will try to WOL and PXE boot one of the compute nodes
3. Make an executable python3 script in the CWD called "cluster.py" that takes arguments with argparse. We're gonna add a lot of functionality to this script but for now we want to be able to run `cluster.py compute up` which WOL's all the compute nodes (i.e. everything reachable on `compute_interface`), `cluster.py compute down` which attempts to shut down the compute nodes with `shutdown` over ssh, `cluster.py compute restart` which attempts to restart the cluster with `shutdown -r` over ssh, `cluster.py compute status` which attempts to ssh into all the computes nodes and returns 0 iff successful on all fronts, and `cluster.py compute configure` which runs all of the compute node ansible playbooks on all the compute nodes (but exit early if the compute status is not good). If ansible has support for any of this funcitonality we can use that but please ask. `cluster.py compute up` should send the WOL packets and then loop the status function once a second until either success or the user CTRL-C's the process. Have all commands log reasonable status updates, keep success updates succinct but make sure failures log everything relevant to debugging.
4. Make an ansible playbook to configure the drivers for the intel arc GPUs. I believe this comes down to adding `ppa:kobuk-team/intel-graphics`, installing `libze-intel-gpu1 libze1 intel-metrics-discovery intel-opencl-icd clinfo intel-gsc libze-dev intel-ocloc`, and adding the user to the `render` group, but as with all linux graphics shenanigans we should expect issues. The success condition here is seeing the A770 GPU listed in the ouput of clinfo. Once its working make sure to include this ansible playbook in `cluster.py compute configure`.
5. Make an ansible playbook to install vLLM on the compute nodes (and the control node if needed) configured for clustered inference. For testing we should use Mistral-Small-Instruct-2409 quantized to 4 bits. Success is inference running on both compute nodes.