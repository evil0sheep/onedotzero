# Overview
This repository exists to explore clustered LLM inference and do performance analysis to find bottlenecks.

# Cluster Topology

This project will configure and manage multiple hardware clusters. The Ansible automation is designed to be hardware-agnostic, with specific configurations abstracted into versioned profiles. See `doc/hardware_abstraction.md` for a detailed plan.

The plan is for the compute nodes in each cluster to PXE boot a shared linux image off of their respective control node and then be configured with Ansible over SSH.

## Hardware v0.1: Intel Arc A770 Cluster

-   **Control Node:** A NUC (hostname `control_0_1`) connected to the cluster via its USB ethernet interface (`enx8cae4cf44e21`).
-   **Compute Nodes:** 2x nodes (hostnames `node0`, `node1`) based on MSI MPG Z690 Carbon WiFi Gaming Motherboards.
-   **Accelerators:** Each compute node has a single Intel Arc A770 GPU.
-   **Network:** The nodes are connected via their onboard ethernet to a switch. They also have a high-speed USB4 host-to-host link for clustering.

## Hardware v0.2: Ryzen AI Cluster (Planned)

-   **Control Node:** A dedicated machine (hostname `control_0_2`).
-   **Compute Nodes:** 4x Ryzen 395 AI max boards.
-   **Accelerators:** The NPU on the Ryzen AI boards.
-   **Network:** Fully connected.

# Workflow Description
We will be working on my macbook editing files locally and then rsyncing them to the NUC and executing them remotely over SSH using the `cluster` command. if you run commands locally you will not have access to ansible or the compute nodes. use `cluster control cmd` to execute custom commands on the control node and

# TODO

Please read each task in the todo list one-by-one and for each item think about what is most ambiguous about the task description and then ask disambiguating questions which clarify the goals and requirements. Once your questions have been answered you can proceed with the task and when you are done please stop and ask for review, and once approved you can check off that task with a ✅ at the beginning of the task description and proceed to the next task.

1. ✅ Improve cluster.py
  * `cluster compute wait` should wait do 100 iterations with a 1 second wait interval instead of 15 iteration with a 10 second wait interval.
  * make a new command `cluster control cmd` which takes a command string and executes it on the appropriate directory on the remote host and once working remove `scripts/remote.py`. So for example `cluster control cmd "ls -la"` would return the directory listing of the directory that was just rsynced to the control node
  * add a new command `cluster doc` which prints out a longform list of every command and a description of what it does.
2. ✅ Remove ubuntu ISO serving functionality from ansible directory. We only care about the NFS boot with overlayfs workflow, anything that is used exclusively by the other two grub boot options should go.
3. ✅ create compute user on compute nodes with passwordless sudo and use this for all ansible operations on the `[compute]` inventory
4. ✅ Make an ansible playbook to configure the drivers for the intel arc GPUs. I believe this comes down to adding `ppa:kobuk-team/intel-graphics`, installing `libze-intel-gpu1 libze1 intel-metrics-discovery intel-opencl-icd clinfo intel-gsc libze-dev intel-ocloc`, and adding the user to the `render` group, but as with all linux graphics shenanigans we should expect issues. The success condition here is seeing the A770 GPU listed in the ouput of clinfo. Once its working make sure to include this ansible playbook in `cluster.py compute configure`.
5. Make an ansible playbook to install vLLM on the compute nodes (and the control node if needed) configured for clustered inference. For testing we should use Mistral-Small-Instruct-2409 quantized to 4 bits. Success is inference running on both compute nodes.
