#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import logging
import yaml
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
REMOTE_DIR = "~/remote/onedotzero"
ANSIBLE_DIR = os.path.join(PROJECT_ROOT, "ansible")
DYN_INVENTORY_PATH = os.path.join(ANSIBLE_DIR, "inventory.dyn")
DYN_INVENTORY_RELATIVE_PATH = "ansible/inventory.dyn"
HARDWARE_VERSION_FILE = os.path.join(PROJECT_ROOT, ".hardware_version")

# Global var to hold hardware config
HARDWARE_CONFIG = None

# --- Helper Functions ---

def get_hardware_version():
    """Gets the currently configured hardware version."""
    try:
        with open(HARDWARE_VERSION_FILE, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        logging.error(f"Hardware version file not found at {HARDWARE_VERSION_FILE}.")
        logging.error("Please run 'cluster hardware set <version>' to configure the target hardware.")
        sys.exit(1)

def load_hardware_config(version):
    """Loads the hardware configuration for the given version."""
    global HARDWARE_CONFIG
    config_path = os.path.join(ANSIBLE_DIR, "hardware_vars", f"{version}.yml")
    try:
        with open(config_path, 'r') as f:
            HARDWARE_CONFIG = yaml.safe_load(f)
    except FileNotFoundError:
        logging.error(f"Hardware config file not found for version '{version}' at {config_path}")
        sys.exit(1)

def run_command(command, remote=True, capture_output=False, check=True):
    """Runs a command locally or remotely."""
    remote_host = HARDWARE_CONFIG.get("control_host", "control")

    # Construct the command
    if remote:
        # Rsync is always checked because if it fails, nothing else will work.
        rsync_cmd = f"rsync -avz --delete --exclude='.git' {PROJECT_ROOT}/ {remote_host}:{REMOTE_DIR}"
        logging.debug(f"Running remote command, first syncing CWD with '{rsync_cmd}'")
        subprocess.run(rsync_cmd, shell=True, check=True, capture_output=True, text=True)

        # The `exec` command ensures that ssh exits with the code of the remote command.
        cmd_to_run = f"ssh {remote_host} 'cd {REMOTE_DIR} && exec {command}'"
    else:
        cmd_to_run = command

    # Execute the command
    logging.debug(f"Executing command: {cmd_to_run}")
    process = subprocess.run(cmd_to_run, shell=True, capture_output=capture_output, text=True, cwd=PROJECT_ROOT)

    # Optionally check for errors
    if check and process.returncode != 0:
        # Log details for debugging
        logging.error(f"Command failed with exit code {process.returncode}")
        logging.error(f"Command: {cmd_to_run}")
        if capture_output:
            logging.error(f"Stdout: {process.stdout}")
            logging.error(f"Stderr: {process.stderr}")
        raise subprocess.CalledProcessError(process.returncode, cmd_to_run, output=process.stdout, stderr=process.stderr)

    return process

def generate_inventory():
    """Generates an Ansible inventory from the hardware config file."""
    logging.info("Generating inventory from hardware config...")
    compute_nodes = HARDWARE_CONFIG.get("compute_nodes", [])

    inventory_content = "[compute]\n"
    inventory_content += "\n".join([node['ip'] for node in compute_nodes])
    inventory_content += "\n\n[compute:vars]\nansible_user=root\n"

    with open(DYN_INVENTORY_PATH, 'w') as f:
        f.write(inventory_content)

    logging.info(f"Inventory written to {DYN_INVENTORY_PATH} with {len(compute_nodes)} hosts.")

# --- Command Functions ---

def get_broadcast_address(args):
    """Gets the broadcast address for the compute interface using Ansible."""
    logging.info("Fetching broadcast address from control node...")
    inventory_path = os.path.join("ansible/inventory", get_hardware_version())
    command = (
               f"ansible-playbook ansible/get_broadcast.yml "
               f"-i {inventory_path} "
               f"--extra-vars 'hardware_version={get_hardware_version()}'")
    try:
        proc = run_command(command, remote=args.remote, capture_output=True)
        for line in proc.stdout.split('\n'):
            if '"msg":' in line:
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
    macs = [node['mac'] for node in HARDWARE_CONFIG.get("compute_nodes", [])]
    if not macs:
        logging.warning("No compute nodes defined in hardware config. Cannot wake any nodes.")
        return

    broadcast_address = get_broadcast_address(args)
    wol_commands = [f"wakeonlan -i {broadcast_address} {mac}" for mac in macs]
    command_str = " && ".join(wol_commands)

    run_command(command_str, remote=args.remote)
    logging.info("Wake-on-LAN packets sent.")

    logging.info("Waiting for compute nodes to come up...")
    compute_wait(args)

def compute_down(args):
    logging.info("Shutting down compute nodes...")
    generate_inventory()
    command = f'ansible compute -i {DYN_INVENTORY_RELATIVE_PATH} -m shell -a "shutdown now"'
    try:
        run_command(command, remote=args.remote)
    except subprocess.CalledProcessError as e:
        logging.info(f"SSH connection failed during shutdown, which is expected: {e}")


def compute_restart(args):
    logging.info("Rebooting compute nodes...")
    generate_inventory()
    command = f'ansible compute -i {DYN_INVENTORY_RELATIVE_PATH} -m shell -a "shutdown -r now"'
    try:
        run_command(command, remote=args.remote)
    except subprocess.CalledProcessError as e:
        logging.info(f"SSH connection failed during reboot, which is expected: {e}")

def compute_wait(args):
    logging.info("Waiting for all compute nodes to become reachable...")
    generate_inventory()

    compute_nodes = HARDWARE_CONFIG.get("compute_nodes", [])
    if not compute_nodes:
        logging.warning("No compute nodes defined in hardware config.")
        return 0

    for i in range(15): # Increased attempts
        try:
            command = f"ansible compute -i {DYN_INVENTORY_RELATIVE_PATH} -m ping"
            run_command(command, remote=args.remote, capture_output=True)
            logging.info("All compute nodes are reachable.")
            return 0
        except subprocess.CalledProcessError:
            logging.info(f"Attempt {i+1}/15 failed. Retrying in 10 seconds...")
            time.sleep(10)

    logging.error(f"Not all compute nodes were reachable after 15 attempts.")
    sys.exit(1)

def compute_configure(args):
    logging.info("Configuring compute nodes...")
    if compute_wait(args) != 0:
        logging.error("Compute nodes are not up. Aborting configuration.")
        sys.exit(1)

    command = (f"ansible-playbook -i {DYN_INVENTORY_RELATIVE_PATH} ansible/compute_configure.yml "
               f"--extra-vars 'hardware_version={get_hardware_version()}' --become")
    run_command(command, remote=args.remote)
    logging.info("Compute node configuration complete.")




def control_configure(args):
    logging.info("Configuring control node...")
    inventory_path = os.path.join("ansible/inventory", get_hardware_version())
    command = (
               f"ansible-playbook -i {inventory_path} ansible/control_configure.yml "
               f"--extra-vars 'hardware_version={get_hardware_version()}' --become")
    run_command(command, remote=args.remote)
    logging.info("Control node configuration complete.")

def cluster_configure(args):
    """Configures the entire cluster from scratch."""
    logging.info("--- Starting Full Cluster Configuration ---")
    control_configure(args)
    try:
        compute_restart(args)
    except subprocess.CalledProcessError:
        logging.info("Compute nodes rebooted as expected.")
    compute_wait(args)
    compute_configure(args)
    logging.info("--- Full Cluster Configuration Complete ---")

def cluster_status(args):
    """Provides a quick status of the entire cluster."""
    print("--- Cluster Status ---")
    control_host = HARDWARE_CONFIG.get("control_host", "Unknown")
    inventory_path = os.path.join("ansible/inventory", get_hardware_version())

    print(f"Control Node ({control_host}):")
    # Status check for control node is tricky with local connection, assume up if script runs
    print(f"  - Status: UP (assumed)")
    print("")

    print("Compute Nodes:")
    compute_nodes = HARDWARE_CONFIG.get("compute_nodes", [])
    if not compute_nodes:
        print("  - No compute nodes defined in hardware config.")
    else:
        print(f"  - Expected {len(compute_nodes)} node(s) based on config.")
        generate_inventory()
        for node in compute_nodes:
            status = get_host_status(node['ip'], args.remote, DYN_INVENTORY_RELATIVE_PATH)
            print(f"  - Host: {node['name']} ({node['ip']})")
            print(f"    - Status: {status}")

    print("----------------------")

def get_host_status(host, remote, inventory_file):
    """Checks the status of a single host."""
    command = f"ansible {host} -i {inventory_file} -m ping -o --timeout 3"
    # We expect this to fail, so we don't check the result here.
    proc = run_command(command, remote=remote, capture_output=True, check=False)
    return "UP" if proc.returncode == 0 else "DOWN"

def hardware_set(args):
    """Sets the active hardware version."""
    version = args.version
    config_path = os.path.join(ANSIBLE_DIR, "hardware_vars", f"{version}.yml")
    if not os.path.exists(config_path):
        logging.error(f"Hardware config file not found for version '{version}' at {config_path}")
        sys.exit(1)

    with open(HARDWARE_VERSION_FILE, 'w') as f:
        f.write(version)
    print(f"Hardware version set to '{version}'.")

def hardware_get(args):
    """Gets the active hardware version."""
    print(get_hardware_version())

# --- Main Execution ---

def main():
    parser = argparse.ArgumentParser(
        description="Cluster management script.\n\nFor help on a specific command, use: cluster <command> --help",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--remote', action=argparse.BooleanOptionalAction, default=True,
                        help="Execute commands on the remote host (default: True).")

    subparsers = parser.add_subparsers(dest='command')

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

    # Hardware commands
    hardware_parser = subparsers.add_parser('hardware', help='Manage hardware configuration.')
    hardware_subparsers = hardware_parser.add_subparsers(dest='action', required=True)
    parser_set = hardware_subparsers.add_parser('set', help='Set the active hardware version (e.g., 0.1).')
    parser_set.add_argument('version', type=str)
    parser_set.set_defaults(func=hardware_set)
    parser_get = hardware_subparsers.add_parser('get', help='Get the active hardware version.')
    parser_get.set_defaults(func=hardware_get)


    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Load hardware config for all commands except 'hardware'
    if args.command != 'hardware':
        version = get_hardware_version()
        load_hardware_config(version)

    args.func(args)

if __name__ == "__main__":
    main()
