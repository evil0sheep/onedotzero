# Ansible Hardware Abstraction Plan

This document outlines the strategy for refactoring the Ansible project to support multiple, distinct hardware configurations. The goal is to encapsulate all hardware-specific settings, allowing for seamless switching between different cluster versions (e.g., `v0.1` for the A770 cluster, `v0.2` for the Ryzen AI cluster).

The core principle is to separate configuration data from execution logic using Ansible's inventory and variable systems.

---

## The Strategy: Isolate, Select, and Inject

The approach is broken down into three phases:

1.  **Isolate:** All hardware-specific configuration (hostnames, network interfaces, required drivers, etc.) will be moved out of the playbooks and into dedicated variable files.
2.  **Select:** A simple mechanism will be implemented in the `scripts/cluster.py` wrapper script to select which hardware profile is currently active.
3.  **Inject:** The selected profile will be passed to Ansible at runtime, which will then dynamically load the correct variables and execute the appropriate hardware-specific tasks.

---

## Step 1: Implement the Hardware "Switcher" in `cluster.py`

The `cluster.py` script will be updated to manage the active hardware version via a state file.

-   **State File:** A new file named `.hardware_version` will be created in the project root.
    -   This file **must be added to `.gitignore`** to avoid committing local environment settings.
    -   `cluster.py` will be modified to **error and exit** if this file is not present when running most commands, instructing the user to run `cluster hardware set <version>` first.

-   **New CLI Commands:**
    -   `cluster hardware set <version>`: This command will write the specified version to the `.hardware_version` file. It should also perform a check to ensure a corresponding configuration file exists at `ansible/hardware_vars/<version>.yml`.
    -   `cluster hardware get`: This command will read and display the current version from the `.hardware_version` file.

-   **Integration with Existing Commands:** All other commands (`control configure`, `compute up`, etc.) will be modified to:
    1.  Read the active version from `.hardware_version` at startup.
    2.  Select the correct inventory path (e.g., `ansible/inventory/<version>/`).
    3.  Pass the version to `ansible-playbook` using the `--extra-vars` flag.

    **Example Command Execution:**
    ```bash
    # The script will translate this:
    python3 scripts/cluster.py control configure

    # Into this command for Ansible:
    ansible-playbook -i ansible/inventory/0.1/ \
                     --extra-vars "hardware_version=0.1" \
                     ansible/control_configure.yml
    ```

---

## Step 2: Restructure the Ansible Project

The `ansible/` directory will be reorganized to support this new modular approach.

#### Proposed New Structure:
```
ansible/
├── ansible.cfg
├── inventory/              # <-- New directory for inventories
│   ├── 0.1/
│   │   └── hosts.ini       # <-- v0.1 specific hosts
│   └── 0.2/
│       └── hosts.ini       # <-- v0.2 specific hosts
├── hardware_vars/          # <-- New directory for hardware variables
│   ├── 0.1.yml
│   └── 0.2.yml
├── control_configure.yml
├── compute_configure.yml
├── roles/
│   ├── common/
│   ├── nfs_server/
│   ├── ...
│   └── hardware_specific/  # <-- New directory for specific roles
│       ├── intel_arc_drivers/
│       │   └── tasks/
│       │       └── main.yml
│       └── ryzen_ai_drivers/
│           └── tasks/
│               └── main.yml
└── vars/
    └── main.yml            # <-- For truly global, non-hardware vars
```

---

## Step 3: Create Hardware-Specific Variable Files

All variables that differ between hardware versions will be defined in YAML files within the `ansible/hardware_vars/` directory. This includes a persistent list of all compute nodes, with their MAC addresses and desired static IPs.

#### `ansible/hardware_vars/0.1.yml` (Example for A770 cluster):
```yaml
---
# Hardware v0.1 Configuration (A770)
control_host: "control_0_1"
compute_interface: "enx8cae4cf44e21"
arch: "amd64"
debootstrap_arch: "amd64"

compute_nodes:
  - { mac: "11:22:33:44:55:66", ip: "192.168.1.100", name: "node0" }
  - { mac: "AA:BB:CC:DD:EE:FF", ip: "192.168.1.101", name: "node1" }

# Role to run for GPU driver setup on compute nodes
gpu_driver_role: "hardware_specific/intel_arc_drivers"
```

#### `ansible/hardware_vars/0.2.yml` (Example for Ryzen AI cluster):
```yaml
---
# Hardware v0.2 Configuration (Ryzen AI)
control_host: "control_0_2"
compute_interface: "eno1"
arch: "amd64"
debootstrap_arch: "amd64"

compute_nodes:
  - { mac: "...", ip: "10.0.1.100", name: "node0" }
  - { mac: "...", ip: "10.0.1.101", name: "node1" }
  - { mac: "...", ip: "10.0.1.102", name: "node2" }
  - { mac: "...", ip: "10.0.1.103", name: "node3" }

# A different role for a different GPU/NPU
gpu_driver_role: "hardware_specific/ryzen_ai_drivers"
```

---

## Step 4: Create Versioned Inventories

Each hardware version will have its own static inventory file defining the control host.

#### `ansible/inventory/0.1/hosts.ini`:
```ini
[control]
control_0_1 ansible_connection=local
```

#### `ansible/inventory/0.2/hosts.ini`:
```ini
[control]
control_0_2 ansible_connection=local
```
The dynamic inventory for compute nodes (`inventory.dyn`) will continue to be generated by `cluster.py` as before. These new versioned files replace the previous top-level `ansible/inventory` file, which will be removed.

---

## Step 5: Modify Playbooks and `cluster.py` for Dynamic Logic

The playbooks and orchestration script will be updated to use the new static node definitions.

#### `ansible/roles/dnsmasq/templates/dnsmasq.conf.j2`:
The `dnsmasq` configuration will be updated to iterate over the `compute_nodes` list and generate static `dhcp-host` entries. This ensures each node always receives the same IP address, making the system robust and predictable.

```jinja
# Static DHCP assignments for compute nodes
{% for node in compute_nodes %}
dhcp-host={{ node.mac }},{{ node.ip }},{{ node.name }}
{% endfor %}
```

#### `ansible/control_configure.yml`:
The `vars_files` directive will be used to load the appropriate hardware configuration file.

```yaml
---
- hosts: localhost
  connection: local
  become: yes
  vars_files:
    - vars/main.yml  # Global defaults
    - "hardware_vars/{{ hardware_version }}.yml" # Dynamically load hardware vars

  roles:
    - common
    - gateway
    - nfs_server
    # ... etc
```

#### `ansible/compute_configure.yml`:
The `include_role` module will be used to dynamically execute the correct hardware-specific driver role.

```yaml
---
- hosts: compute
  become: yes
  vars_files:
    - "hardware_vars/{{ hardware_version }}.yml"

  roles:
    # ... other common compute configuration roles ...

    - name: Include hardware-specific driver role
      include_role:
        name: "{{ gpu_driver_role }}"
```

#### `scripts/cluster.py` Logic Updates:
The script will be updated to use the hardware vars file as the source of truth, removing its dependency on the DHCP lease file.

-   **`generate_inventory`:** This function will now read the `compute_nodes` list from the hardware vars file and use the defined static IPs to generate the `ansible/inventory.dyn` file.
-   **`compute_up`:** This function will read the `compute_nodes` list and use the defined MAC addresses to send Wake-on-LAN packets. This works even if the nodes are fully offline and have no active lease.
-   **`cluster compute wait`:** This command will be updated to poll the static IPs until the number of reachable nodes equals the total number of nodes defined in the hardware vars file.
-   **`cluster status`:** This command will be updated to show the expected nodes and their static IPs, and then ping each one to show its live status.