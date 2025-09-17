# Ansible Hardware Abstraction Guide

This document describes the hardware abstraction system built into this Ansible project. It allows the same set of playbooks to configure multiple, distinct hardware clusters by isolating environment-specific variables.

---

## System Overview

The core principle of the system is the separation of configuration data from execution logic. This is achieved through a combination of the `scripts/cluster.py` orchestration script and Ansible's variable and inventory systems.

### Components

1.  **Hardware Switcher (`scripts/cluster.py`)**
    -   The `cluster.py` script acts as the main user interface. It uses a state file, `.hardware_version` (which is git-ignored), to determine which hardware profile is currently active.
    -   You **must** set the active hardware before running most commands using `python3 scripts/cluster.py hardware set <version>`.
    -   When a command is run, the script reads the active version and passes it to Ansible, dynamically selecting the correct inventory and variable files.

2.  **Hardware Variables (`ansible/hardware_vars/`)**
    -   This directory contains a YAML file for each hardware version (e.g., `0.1.yml`).
    -   These files are the **single source of truth** for all hardware-specific data, including:
        -   The hostname of the control node (`control_host`).
        -   The network interface to use on the control node (`compute_interface`).
        -   A complete list of `compute_nodes`, including their persistent MAC addresses, desired static IPs, and hostnames.
        -   Variables that point to hardware-specific roles (e.g., `gpu_driver_role`).

3.  **Versioned Inventories (`ansible/inventory/`)**
    -   Each hardware version has a dedicated inventory directory (e.g., `ansible/inventory/0.1/`) containing a `hosts.ini` file.
    -   This file's only job is to define the `[control]` group, which points to the correct control node for that hardware version.
    -   The `[compute]` group is generated dynamically by `cluster.py` from the hardware variables file.

4.  **Dynamic Playbooks**
    -   Playbooks like `control_configure.yml` and `compute_configure.yml` are written to be generic.
    -   They use the `vars_files` directive to load the correct `hardware_vars/<version>.yml` file at runtime.
    -   They use the `include_role` module to dynamically execute hardware-specific logic (like installing a GPU driver) based on variables defined in the hardware vars file.

---

## How to Add a New Hardware Version (e.g., v0.2)

Adding support for a new cluster is a straightforward process.

#### Step 1: Create the Hardware Variables File

1.  Create a new file: `ansible/hardware_vars/0.2.yml`.
2.  Populate it with all the specific details for the new hardware. Follow the structure of `0.1.yml` as a template.

    ```yaml
    # ansible/hardware_vars/0.2.yml
    ---
    control_host: "control_0_2"
    compute_interface: "eno1"
    # ... etc ...

    compute_nodes:
      - { mac: "...", ip: "10.0.1.100", name: "node0" }
      - { mac: "...", ip: "10.0.1.101", name: "node1" }
      # ... etc ...

    gpu_driver_role: "hardware_specific/ryzen_ai_drivers" # Or whatever is appropriate
    ```

#### Step 2: Create the Inventory File

1.  Create a new directory: `ansible/inventory/0.2/`.
2.  Create a new file inside it: `ansible/inventory/0.2/hosts.ini`.
3.  Define the control host for the v0.2 cluster.

    ```ini
    # ansible/inventory/0.2/hosts.ini
    [control]
    control_0_2 ansible_connection=local
    ```

#### Step 3: Activate the New Version

You can now switch your local environment to target the new hardware:

```bash
python3 scripts/cluster.py hardware set 0.2
```
All subsequent `cluster` commands will now target the v0.2 cluster.

---

## How to Add Hardware-Specific Logic (e.g., GPU Drivers)

The system is designed to make it easy to run specific Ansible tasks for only one type of hardware.

#### Step 1: Create a New Role

1.  Create a new, self-contained role for your specific task in the `ansible/roles/hardware_specific/` directory.

    ```bash
    mkdir -p ansible/roles/hardware_specific/intel_arc_drivers/tasks
    touch ansible/roles/hardware_specific/intel_arc_drivers/tasks/main.yml
    ```
2.  Add all the necessary tasks to the `main.yml` file within that role.

#### Step 2: Link the Role in Hardware Variables

1.  Open the hardware variables file for the target hardware (e.g., `ansible/hardware_vars/0.1.yml`).
2.  Define a variable that points to your new role. For example:
    ```yaml
    gpu_driver_role: "hardware_specific/intel_arc_drivers"
    ```

#### Step 3: Include the Role in a Playbook

1.  Open the playbook where this logic should be executed (e.g., `ansible/compute_configure.yml`).
2.  Add an `include_role` task that uses the variable you just defined.

    ```yaml
    # ansible/compute_configure.yml
    ---
    - hosts: compute
      become: yes
      vars_files:
        - "hardware_vars/{{ hardware_version }}.yml"

      roles:
        # ... other common compute roles ...

        - name: Include hardware-specific driver role
          include_role:
            name: "{{ gpu_driver_role }}"
          when: gpu_driver_role is defined
    ```
    *(Note: Adding the `when` condition makes the playbook more robust, as it won't fail if a hardware version doesn't define that specific variable.)*

Now, when `compute_configure` is run for hardware `v0.1`, it will automatically find the `gpu_driver_role` variable and run the `intel_arc_drivers` role. If it's run for a different hardware version that doesn't define that variable, the task will be safely skipped.
