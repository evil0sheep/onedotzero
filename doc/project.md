# Overview
This repository exists to explore clustered LLM inference and do performance analysis to find bottlenecks.

# Cluster Topology

This project will configure and manage multiple hardware clusters. The Ansible automation is designed to be hardware-agnostic, with specific configurations abstracted into versioned profiles. See `doc/hardware_abstraction.md` for a detailed plan.

The plan is for the compute nodes in each cluster to PXE boot a shared linux image off of their respective control node and then be configured with Ansible over SSH.

## Hardware v0.1: Radeon RX7600 XT Cluster

-   **Control Node:** A NUC (hostname `control_0_1`) connected to the cluster via its USB ethernet interface (`enx8cae4cf44e21`).
-   **Compute Nodes:** 2x nodes (hostnames `compute0`, `compute1`) based on MSI MPG Z690 Carbon WiFi Gaming Motherboards.
-   **Accelerators:** Each compute node has a single AMD Radeon RX7600 XT GPU.
-   **Network:** The nodes are connected via their onboard ethernet to a switch. They also have a high-speed USB4 host-to-host link for clustering.

## Hardware v0.2: Ryzen AI Cluster (Planned)

-   **Control Node:** A dedicated machine (hostname `control_0_2`).
-   **Compute Nodes:** 4x Ryzen 395 AI max boards.
-   **Accelerators:** The NPU on the Ryzen AI boards.
-   **Network:** Fully connected.

# Workflow Description
We will be working on my macbook editing files locally and then rsyncing them to the NUC and executing them remotely over SSH using the `cluster` command. if you run commands locally you will not have access to ansible or the compute nodes. use `cluster control cmd` to execute custom commands on the control node. Please feel free to run `cluster doc` or `cluster --help` for information on how to use this tool.

Please read each task in the todo list one-by-one and for each item think about what is most ambiguous about the task description and then ask disambiguating questions which clarify the goals and requirements. Once your questions have been answered you can proceed with the task and when you are done please stop and ask for review, and once approved you can check off that task with a ✅ at the beginning of the task description and proceed to the next task. If you run into problems PLEASE STOP and explain the problem so we can discuss solutions before continuing. DO NOT iterate non-interactively trying to solve a problem.

1.
  1. Make new ansible roles for Apache Ray that installs ray on both the compute nodes and the control node and configures Ray to use the control node as the Ray head node and the compute nodes as the Ray worker nodes. Verify in a new `verify.yml` that `ray status` shows the cluster topology that is expected given the definition of `compute_nodes` in `ansible/hardware_vars/0.1.yml`. Add the new `verify.yml` to `control_test.yml` (not hardware specific)

  2. Make a new ansible role for vLLM which installs the vLLM server on the control node and configures it for tensor parallelisåm and expert parallelism based on the definition of `compute_nodes` in `ansible/hardware_vars/0.1.yml`. You should use `https://huggingface.co/AMead10/Mistral-Small-Instruct-2409-awq` for testing
2. Misc cleanup
  * add `compute-node` to `127.0.0.1` line in `/etc/hosts` on compute nodes
  * deduplicate inventory path resolution in `scripts/cluster.py`
  * remove remote flag from cluster.py
  * dont setup test environment in test control node
  * make the `compute` user and ansible variable instead of hardcoding it everywhere
  * deduplicate stopping nfs and unmounting things between build_image and clean_image
  * make linter be ok with role paths and reintroduce full paths to playbooks
  * put roles to include in chroot connection in variable block in build_image
  * break test setup logic out of control_configure into its own playbook
