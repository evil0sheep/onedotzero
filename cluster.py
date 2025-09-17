#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import logging

# Configure logging
logging.basicConfig(level=logging.WARN, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
REMOTE_HOST = "control"
REMOTE_DIR = "~/remote/onedotzero"
CWD = os.path.dirname(os.path.realpath(__file__))
ANSIBLE_DIR = os.path.join(CWD, "ansible")
DYN_INVENTORY_PATH = "ansible/inventory.dyn"
LEASE_FILE_PATH = "/var/lib/misc/dnsmasq.leases"

# --- Helper Functions ---

def run_command(command, remote=True, capture_output=False):
    """Runs a command locally or remotely."""
    if remote:
        # 1. Rsync the current directory to the remote host
        rsync_cmd = f"rsync -avz --delete --exclude='.git' {CWD}/ {REMOTE_HOST}:{REMOTE_DIR}"
        logging.info(f"Running remote command, first syncing CWD with '{rsync_cmd}'")
        subprocess.run(rsync_cmd, shell=True, check=True, capture_output=True, text=True)

        # 2. Execute the command on the remote host via SSH
        remote_command = f"ssh {REMOTE_HOST} 'cd {REMOTE_DIR} && {command}'"
        logging.info(f"Executing remote command: {remote_command}")
        return subprocess.run(remote_command, shell=True, check=True, capture_output=capture_output, text=True)
    else:
        logging.info(f"Executing local command: {command}")
        return subprocess.run(command, shell=True, check=True, cwd=CWD, capture_output=capture_output, text=True)

def generate_inventory(remote=True):
    """
    Generates a dynamic Ansible inventory from the dnsmasq lease file.
    Returns a list of MAC addresses.
    """
    logging.info("Generating dynamic inventory...")
    read_lease_cmd = f"cat {LEASE_FILE_PATH}"

    try:
        if remote:
            # Need to capture output to parse the lease file
            proc = run_command(read_lease_cmd, remote=True, capture_output=True)
            lease_content = proc.stdout
            logging.info(f"Lease file content:\n{lease_content}")
        else:
            with open(LEASE_FILE_PATH, 'r') as f:
                lease_content = f.read()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logging.error(f"Failed to read dnsmasq lease file: {e}")
        sys.exit(1)

    macs = []
    hosts = []
    for line in lease_content.strip().split('\n'):
        parts = line.split()
        if len(parts) >= 4:
            mac = parts[1]
            ip = parts[2]
            macs.append(mac)
            hosts.append(ip)

    inventory_content = "[compute]\n"
    inventory_content += "\n".join(hosts)
    inventory_content += "\n\n[compute:vars]\nansible_user=root\n"

    # This file is always written locally, then rsynced in run_command
    with open(DYN_INVENTORY_PATH, 'w') as f:
        f.write(inventory_content)

    logging.info(f"Dynamic inventory written to {DYN_INVENTORY_PATH} with {len(hosts)} hosts.")
    return macs

# --- Command Functions ---

def get_broadcast_address(remote=True):
    """Gets the broadcast address for the compute interface using Ansible."""
    logging.info("Fetching broadcast address from control node...")
    command = f"ansible-playbook ansible/get_broadcast.yml"
    try:
        # We need to capture the output to parse it
        proc = run_command(command, remote=remote, capture_output=True)
        for line in proc.stdout.split('\n'):
            if '"msg":' in line:
                # A bit fragile, but avoids adding json parsing for this one value
                broadcast = line.split('"')[3]
                logging.info(f"Found broadcast address: {broadcast}")
                return broadcast
        logging.error("Could not parse broadcast address from ansible output.")
        sys.exit(1)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logging.error(f"Failed to get broadcast address: {e}")
        sys.exit(1)

def compute_up(args):
    logging.info("Bringing compute nodes up...")
    macs = generate_inventory(args.remote)
    if not macs:
        logging.warning("No active leases found. Cannot wake any nodes.")
        return

    broadcast_address = get_broadcast_address(args.remote)

    wol_commands = [f"wakeonlan -i {broadcast_address} {mac}" for mac in macs]
    command_str = " && ".join(wol_commands)

    run_command(command_str, remote=args.remote)
    logging.info("Wake-on-LAN packets sent.")

    logging.info("Waiting for compute nodes to come up...")
    compute_wait(args)


def compute_down(args):
    logging.info("Shutting down compute nodes...")
    generate_inventory(args.remote)
    command = f'ansible compute -i {DYN_INVENTORY_PATH} -m shell -a "shutdown now"'
    run_command(command, remote=args.remote)

def compute_restart(args):
    logging.info("Rebooting compute nodes...")
    generate_inventory(args.remote)
    command = f'ansible compute -i {DYN_INVENTORY_PATH} -m shell -a "shutdown -r now"'
    run_command(command, remote=args.remote)


def compute_wait(args):
    logging.info("Waiting for compute nodes to become reachable...")
    generate_inventory(args.remote)

    for i in range(10):
        try:
            command = f"ansible compute -i {DYN_INVENTORY_PATH} -m ping"
            run_command(command, remote=args.remote, capture_output=True) # Capture output to suppress ansible spam
            logging.info("All compute nodes are reachable.")
            return 0
        except subprocess.CalledProcessError:
            logging.info(f"Attempt {i+1}/10 failed. Retrying in 5 seconds...")
            time.sleep(5)

    logging.error("Compute nodes are not reachable after 10 attempts.")
    sys.exit(1)

def compute_configure(args):
    logging.info("Configuring compute nodes...")
    if compute_wait(args) != 0:
        logging.error("Compute nodes are not up. Aborting configuration.")
        sys.exit(1)

    command = f"ansible-playbook -i {DYN_INVENTORY_PATH} ansible/compute_configure.yml --become"
    run_command(command, remote=args.remote)
    logging.info("Compute node configuration complete.")

def control_configure(args):
    logging.info("Configuring control node...")
    command = f"ansible-playbook -i ansible/inventory ansible/control_configure.yml --become"
    run_command(command, remote=args.remote)
    logging.info("Control node configuration complete.")

def get_host_status(host, remote, inventory_file=None):
    """Checks the status of a single host."""
    inventory_arg = f"-i {inventory_file}" if inventory_file else ""
    try:
        # Use a short timeout to fail faster
        command = f"ansible {host} {inventory_arg} -m ping -o --timeout 5"
        run_command(command, remote=remote, capture_output=True)
        return "UP"
    except subprocess.CalledProcessError:
        return "DOWN"

def get_last_configured_time(host, remote, timestamp_file, inventory_file=None):
    """Gets the last modified time of a file on a host."""
    inventory_arg = f"-i {inventory_file}" if inventory_file else ""
    try:
        command = f"ansible {host} {inventory_arg} -m shell -a 'stat -c %y {timestamp_file}' -o"
        proc = run_command(command, remote=remote, capture_output=True)
        logging.info(f"Stat command output for {host}:\n{proc.stdout}")
        # Successful output is in the format: 192.168.1.62 | CHANGED | rc=0 >>\n2025-09-16 21:45:52.000000000 -0400
        # We just want the date and time part.
        if ">>" in proc.stdout:
            return proc.stdout.split(">>")[1].strip().split(".")[0]
        return "Never"
    except subprocess.CalledProcessError as e:
        if "No such file or directory" in e.stderr:
            return "Never"
        logging.error(f"Failed to get last configured time for {host}: {e}")
        return "Unknown"
    except Exception as e:
        logging.error(f"An unexpected error occurred while getting last configured time for {host}: {e}")
        return "Unknown"






def cluster_configure(args):
    """Configures the entire cluster from scratch."""
    logging.info("--- Starting Full Cluster Configuration ---")

    logging.info("Step 1: Configuring control node...")
    control_configure(args)

    logging.info("Step 2: Rebooting compute nodes to apply network boot settings...")
    try:
        compute_restart(args)
    except subprocess.CalledProcessError:
        logging.info("Compute nodes rebooted as expected. SSH connection dropped.")

    logging.info("Step 3: Waiting for compute nodes to come back online...")
    compute_wait(args)

    logging.info("Step 4: Configuring compute nodes...")
    compute_configure(args)

    logging.info("--- Full Cluster Configuration Complete ---")


def cluster_status(args):
    """Provides a quick status of the entire cluster."""
    print("--- Cluster Status ---")

    # Control Node
    control_status = get_host_status("localhost", remote=args.remote, inventory_file="ansible/inventory")
    control_last_configured = "Unknown"
    if control_status == "UP":
        # The timestamp path needs to be the one on the remote machine
        remote_timestamp_path = os.path.join(REMOTE_DIR, ".last_configured_control")
        control_last_configured = get_last_configured_time(
            "localhost", args.remote, remote_timestamp_path, "ansible/inventory"
        )
    print(f"Control Node ({REMOTE_HOST}):")
    print(f"  - Status: {control_status}")
    print(f"  - Last Configured: {control_last_configured}")
    print("")

    # Compute Nodes
    print("Compute Nodes:")
    # generate_inventory returns MACs, we need IPs for the status check.
    try:
        proc = run_command(f"cat {LEASE_FILE_PATH}", remote=args.remote, capture_output=True)
        # Filter out empty lines
        lines = [line for line in proc.stdout.strip().split('\n') if line]
        if not lines:
             print("  - No active leases found.")
        else:
            for line in lines:
                parts = line.split()
                if len(parts) < 3:
                    continue
                ip = parts[2]
                status = get_host_status(ip, remote=args.remote, inventory_file=DYN_INVENTORY_PATH)
                last_configured = "Unknown"
                if status == "UP":
                    last_configured = get_last_configured_time(
                        ip, args.remote, "/etc/last_configured_compute", DYN_INVENTORY_PATH
                    )
                print(f"  - Host: {ip}")
                print(f"    - Status: {status}")
                print(f"    - Last Configured: {last_configured}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("  - Could not read lease file to find compute nodes.")


    print("----------------------")


# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(description="Cluster management script.")
    parser.add_argument('--remote', action=argparse.BooleanOptionalAction, default=True,
                        help="Execute commands on the remote 'control' host (default: True).")

    subparsers = parser.add_subparsers(dest='command', required=True)

    # Top-level commands
    subparsers.add_parser('status', help='Get a quick status of the cluster.').set_defaults(func=cluster_status)
    subparsers.add_parser('configure', help='Configure the entire cluster.').set_defaults(func=cluster_configure)

    # Compute commands
    compute_parser = subparsers.add_parser('compute', help='Manage compute nodes.')
    compute_subparsers = compute_parser.add_subparsers(dest='action', required=True)

    compute_subparsers.add_parser('up', help='Wake up all compute nodes.').set_defaults(func=compute_up)
    compute_subparsers.add_parser('down', help='Shut down all compute nodes.').set_defaults(func=compute_down)
    compute_subparsers.add_parser('restart', help='Restart all compute nodes.').set_defaults(func=compute_restart)
    compute_subparsers.add_parser('wait', help='Wait for compute nodes to be reachable.').set_defaults(func=compute_wait)
    compute_subparsers.add_parser('configure', help='Run Ansible configuration on compute nodes.').set_defaults(func=compute_configure)

    # Control commands
    control_parser = subparsers.add_parser('control', help='Manage the control node.')
    control_subparsers = control_parser.add_subparsers(dest='action', required=True)
    control_subparsers.add_parser('configure', help='Run Ansible configuration on the control node.').set_defaults(func=control_configure)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    # Need to import time for compute_wait
    import time
    main()
