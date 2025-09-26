#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import logging
import yaml
import time
import jinja2

# Configure logging
logging.basicConfig(
    level=logging.WARN, format="%(asctime)s - %(levelname)s - %(message)s"
)

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
        with open(HARDWARE_VERSION_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        logging.error(f"Hardware version file not found at {HARDWARE_VERSION_FILE}.")
        logging.error(
            "Please run 'cluster hardware set <version>' to configure the target hardware."
        )
        sys.exit(1)


def load_hardware_config(version):
    """Loads the hardware configuration for the given version."""
    global HARDWARE_CONFIG
    config_path = os.path.join(ANSIBLE_DIR, "hardware_vars", f"{version}.yml")
    try:
        with open(config_path, "r") as f:
            HARDWARE_CONFIG = yaml.safe_load(f)
    except FileNotFoundError:
        logging.error(
            f"Hardware config file not found for version '{version}' at {config_path}"
        )
        sys.exit(1)


def run_command(
    command, remote=True, capture_output=False, check=True, suppress_errors=False
):
    """Runs a command locally or remotely."""
    remote_host = HARDWARE_CONFIG.get("control_host", "control")

    # Construct the command
    if remote:
        command = command.replace("'", "'\\''")
        # The `exec` command ensures that ssh exits with the code of the remote command.
        cmd_to_run = f"ssh {remote_host} 'cd {REMOTE_DIR} && exec {command}'"
    else:
        cmd_to_run = command

    # Execute the command
    logging.debug(f"Executing command: {cmd_to_run}")
    process = subprocess.run(
        cmd_to_run,
        shell=True,
        capture_output=capture_output,
        text=True,
        cwd=PROJECT_ROOT,
    )

    # Optionally check for errors
    if check and process.returncode != 0:
        if not suppress_errors:
            # Log details for debugging
            logging.error(f"Command failed with exit code {process.returncode}")
            logging.error(f"Command: {cmd_to_run}")
            if capture_output:
                logging.error(f"Stdout: {process.stdout}")
                logging.error(f"Stderr: {process.stderr}")
        raise subprocess.CalledProcessError(
            process.returncode, cmd_to_run, output=process.stdout, stderr=process.stderr
        )

    return process


def generate_inventory():
    """Generates an Ansible inventory from the hardware config file."""
    logging.info("Generating inventory from hardware config...")

    # Load ansible vars
    vars_path = os.path.join(ANSIBLE_DIR, "vars", "main.yml")
    with open(vars_path, "r") as f:
        ansible_vars = yaml.safe_load(f)

    # Initialize Jinja2 environment
    env = jinja2.Environment()

    compute_nodes = HARDWARE_CONFIG.get("compute_nodes", [])
    rendered_ips = []
    for node in compute_nodes:
        template = env.from_string(node["ip"])
        rendered_ip = template.render(ansible_vars)
        rendered_ips.append(rendered_ip)

    inventory_content = "[compute]\n"
    inventory_content += "\n".join(rendered_ips)
    inventory_content += "\n\n[compute:vars]\nansible_user=compute\n"

    with open(DYN_INVENTORY_PATH, "w") as f:
        f.write(inventory_content)

    logging.info(
        f"Inventory written to {DYN_INVENTORY_PATH} with {len(compute_nodes)} hosts."
    )


# --- Command Functions ---


def get_broadcast_address(args):
    """Gets the broadcast address for the compute interface using Ansible."""
    inventory_path = os.path.join(
        "ansible/inventory", get_hardware_version(), "hosts.ini"
    )
    command = (
        f"ansible-playbook ansible/get_broadcast.yml "
        f"-i {inventory_path} "
        f"--extra-vars 'hardware_version={get_hardware_version()}'"
    )
    try:
        proc = run_command(command, remote=args.remote, capture_output=True)
        for line in proc.stdout.split("\n"):
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
    """Brings compute nodes up using Wake-on-LAN."""
    logging.info("Bringing compute nodes up...")
    macs = [node["mac"] for node in HARDWARE_CONFIG.get("compute_nodes", [])]
    if not macs:
        logging.warning(
            "No compute nodes defined in hardware config. Cannot wake any nodes."
        )
        return

    broadcast_address = get_broadcast_address(args)
    inventory_path = os.path.join(
        "ansible/inventory", get_hardware_version(), "hosts.ini"
    )
    command = (
        f"ansible-playbook ansible/wol_up.yml "
        f"-i {inventory_path} "
        f'--extra-vars \'{{"hardware_version": "{get_hardware_version()}", "broadcast_address": "{broadcast_address}"}}\''
    )
    print(command)
    run_command(command, remote=args.remote)
    logging.info("Wake-on-LAN packets sent.")

    logging.info("Waiting for compute nodes to come up...")
    compute_wait(args)


def compute_down(args):
    """Shuts down compute nodes."""
    logging.info("Shutting down compute nodes...")
    command = f'ansible compute -i {DYN_INVENTORY_RELATIVE_PATH} -m shell -a "shutdown now" --become'
    try:
        run_command(
            command, remote=args.remote, suppress_errors=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        logging.info(f"SSH connection failed during shutdown, which is expected: {e}")


def compute_restart(args):
    """Reboots compute nodes."""
    print("Rebooting compute nodes...")
    command = f'ansible compute -i {DYN_INVENTORY_RELATIVE_PATH} -m shell -a "shutdown -r now" --become'
    try:
        run_command(
            command, remote=args.remote, suppress_errors=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        logging.info(f"SSH connection failed during reboot, which is expected: {e}")


def compute_wait(args):
    """Waits for all compute nodes to become reachable."""
    print("Waiting for all compute nodes to become reachable...")

    compute_nodes = HARDWARE_CONFIG.get("compute_nodes", [])
    if not compute_nodes:
        logging.warning("No compute nodes defined in hardware config.")
        return 0

    for i in range(100):  # Increased attempts
        try:
            command = f"ansible compute -i {DYN_INVENTORY_RELATIVE_PATH} -m ping"
            run_command(
                command, remote=args.remote, capture_output=True, suppress_errors=True
            )
            print("All compute nodes are reachable.")
            return 0
        except subprocess.CalledProcessError:
            print(f"Attempt {i + 1}/100 failed. Retrying in 1 second...")
            time.sleep(1)

    logging.error(f"Not all compute nodes were reachable after 100 attempts.")
    sys.exit(1)


def compute_configure(args):
    """Configures compute nodes using Ansible."""
    logging.info("Configuring compute nodes...")
    if compute_wait(args) != 0:
        logging.error("Compute nodes are not up. Aborting configuration.")
        sys.exit(1)

    command = (
        f"ansible-playbook -i {DYN_INVENTORY_RELATIVE_PATH} ansible/compute_configure.yml "
        f"--extra-vars 'hardware_version={get_hardware_version()}' --become"
    )
    run_command(command, remote=args.remote)
    logging.info("Compute node configuration complete.")


def compute_test(args):
    """Tests compute nodes using Ansible."""
    logging.info("Testing compute nodes...")
    # if compute_wait(args) != 0:
    #     logging.error("Compute nodes are not up. Aborting test.")
    #     sys.exit(1)

    command = (
        f"ansible-playbook -i {DYN_INVENTORY_RELATIVE_PATH} ansible/compute_test.yml "
        f"--extra-vars 'hardware_version={get_hardware_version()}' --become"
    )
    run_command(command, remote=args.remote)
    logging.info("Compute node testing complete.")


def compute_ssh(args):
    """SSH into a compute node."""
    node_index = args.node_index
    try:
        node_name = HARDWARE_CONFIG["compute_nodes"][node_index]["name"]
    except IndexError:
        logging.error(f"Invalid node index: {node_index}")
        sys.exit(1)

    if args.remote:
        remote_host = HARDWARE_CONFIG.get("control_host", "control")
        command = f"ssh -t {remote_host} 'ssh {node_name}'"
        # For interactive SSH, we use os.system to hand over control
        print(f"Connecting to {node_name} via {remote_host}...")
        os.system(command)
    else:
        command = f"ssh {node_name}"
        print(f"Connecting to {node_name}...")
        os.system(command)


def control_configure(args):
    """Configures the control node using Ansible."""
    inventory_path = os.path.join(
        "ansible/inventory", get_hardware_version(), "hosts.ini"
    )
    command = (
        f"ansible-playbook -i {inventory_path} ansible/control_configure.yml "
        f"--extra-vars 'hardware_version={get_hardware_version()}' --become"
    )
    run_command(command, remote=args.remote)
    logging.info("Control node configuration complete.")


def control_build_image(args):
    """Builds the golden image using Ansible."""
    inventory_path = os.path.join(
        "ansible/inventory", get_hardware_version(), "hosts.ini"
    )
    # we have to run this as sudo because the chroot connection requires it
    command = (
        f"sudo -E ansible-playbook -i {inventory_path} ansible/build_image.yml "
        f"--extra-vars 'hardware_version={get_hardware_version()}' --become"
    )
    run_command(command, remote=args.remote)
    logging.info("Golden image build complete.")
    logging.info("Fixing .ansible directory ownership...")
    run_command("sudo chown -R $USER:$USER $HOME/.ansible", remote=args.remote)


def control_cmd(args):
    """Executes a command on the control node."""
    run_command(args.command, remote=True)


def cluster_configure(args):
    """Configures the entire cluster from scratch."""
    logging.info("--- Starting Full Cluster Configuration ---")

    # Check compute node status before shutting down
    logging.info("Checking status of compute nodes...")  # Needed for get_host_status
    compute_nodes_up = False
    compute_nodes = HARDWARE_CONFIG.get("compute_nodes", [])
    if not compute_nodes:
        logging.info("No compute nodes defined, skipping shutdown check.")
    else:
        for node in compute_nodes:
            status = get_host_status(
                node["ip"], args.remote, DYN_INVENTORY_RELATIVE_PATH
            )
            logging.info(f"  - Host: {node['name']} ({node['ip']}) is {status}")
            if status == "UP":
                compute_nodes_up = True

    if compute_nodes_up:
        logging.info("One or more compute nodes are up. Shutting them down...")
        compute_down(args)
    else:
        logging.info(
            "All compute nodes are already down. Proceeding with configuration."
        )

    control_build_image(args)
    control_configure(args)
    compute_up(args)
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
        for node in compute_nodes:
            status = get_host_status(
                node["ip"], args.remote, DYN_INVENTORY_RELATIVE_PATH
            )
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
        logging.error(
            f"Hardware config file not found for version '{version}' at {config_path}"
        )
        sys.exit(1)

    with open(HARDWARE_VERSION_FILE, "w") as f:
        f.write(version)
    print(f"Hardware version set to '{version}'.")


def hardware_get(args):
    """Gets the active hardware version."""
    print(get_hardware_version())


def cluster_doc(args):
    """Prints out a longform list of every command and a description of what it does."""
    print("# Cluster Command Documentation")

    # This is a bit manual, but it's the simplest way to get the structure
    # without a more complex framework like click.

    print("\n## Top-Level Commands")
    print("* `cluster status`: Get a quick status of the cluster.")
    print("* `cluster configure`: Configure the entire cluster.")

    print("\n## Compute Commands (`cluster compute ...`)")
    print("* `up`: Wake up all compute nodes.")
    print("* `down`: Shut down all compute nodes.")
    print("* `restart`: Restart all compute nodes.")
    print("* `wait`: Wait for compute nodes to be reachable.")
    print("* `configure`: Run Ansible configuration on compute nodes.")

    print("\n## Control Commands (`cluster control ...`)")
    print("* `configure`: Run Ansible configuration on the control node.")
    print("* `cmd <command>`: Executes a command on the control node.")

    print("\n## Hardware Commands (`cluster hardware ...`)")
    print("* `set <version>`: Set the active hardware version (e.g., 0.1).")
    print("* `get`: Get the active hardware version.")

    print("\n## Documentation Commands (`cluster doc`)")
    print("* `doc`: Prints out this documentation.")


# --- Main Execution ---


def main():
    parser = argparse.ArgumentParser(
        description="Cluster management script.\n\nFor help on a specific command, use: cluster <command> --help",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--remote",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Execute commands on the remote host (default: True).",
    )

    subparsers = parser.add_subparsers(dest="command")

    # Top-level commands
    subparsers.add_parser(
        "status", help="Get a quick status of the cluster."
    ).set_defaults(func=cluster_status)
    subparsers.add_parser(
        "configure", help="Configure the entire cluster."
    ).set_defaults(func=cluster_configure)
    subparsers.add_parser(
        "doc",
        help="Prints out a longform list of every command and a description of what it does.",
    ).set_defaults(func=cluster_doc)

    # Compute commands
    compute_parser = subparsers.add_parser("compute", help="Manage compute nodes.")
    compute_subparsers = compute_parser.add_subparsers(dest="action", required=True)
    compute_subparsers.add_parser("up", help="Wake up all compute nodes.").set_defaults(
        func=compute_up
    )
    compute_subparsers.add_parser(
        "down", help="Shut down all compute nodes."
    ).set_defaults(func=compute_down)
    compute_subparsers.add_parser(
        "restart", help="Restart all compute nodes."
    ).set_defaults(func=compute_restart)
    compute_subparsers.add_parser(
        "wait", help="Wait for compute nodes to be reachable."
    ).set_defaults(func=compute_wait)
    compute_subparsers.add_parser(
        "configure", help="Run Ansible configuration on compute nodes."
    ).set_defaults(func=compute_configure)
    compute_subparsers.add_parser(
        "test", help="Run Ansible tests on compute nodes."
    ).set_defaults(func=compute_test)

    ssh_parser = compute_subparsers.add_parser("ssh", help="SSH into a compute node.")
    ssh_parser.add_argument(
        "node_index", type=int, help="The 0-based index of the compute node."
    )
    ssh_parser.set_defaults(func=compute_ssh)

    # Control commands
    control_parser = subparsers.add_parser("control", help="Manage the control node.")
    control_subparsers = control_parser.add_subparsers(dest="action", required=True)
    control_subparsers.add_parser(
        "configure", help="Run Ansible configuration on the control node."
    ).set_defaults(func=control_configure)
    control_subparsers.add_parser(
        "build_image", help="Build the golden image."
    ).set_defaults(func=control_build_image)
    cmd_parser = control_subparsers.add_parser(
        "cmd", help="Execute a command on the control node."
    )
    cmd_parser.add_argument("command", type=str, help="The command to execute.")
    cmd_parser.set_defaults(func=control_cmd)

    # Hardware commands
    hardware_parser = subparsers.add_parser(
        "hardware", help="Manage hardware configuration."
    )
    hardware_subparsers = hardware_parser.add_subparsers(dest="action", required=True)
    parser_set = hardware_subparsers.add_parser(
        "set", help="Set the active hardware version (e.g., 0.1)."
    )
    parser_set.add_argument("version", type=str)
    parser_set.set_defaults(func=hardware_set)
    parser_get = hardware_subparsers.add_parser(
        "get", help="Get the active hardware version."
    )
    parser_get.set_defaults(func=hardware_get)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Load hardware config for all commands except 'hardware'
    if args.command != "hardware":
        version = get_hardware_version()
        load_hardware_config(version)

    if args.command not in ["doc"]:
        generate_inventory()
        if args.remote:
            remote_host = HARDWARE_CONFIG.get("control_host", "control")
            # Rsync is always checked because if it fails, nothing else will work.
            rsync_cmd = f"rsync -avz --delete --exclude='.git' {PROJECT_ROOT}/ {remote_host}:{REMOTE_DIR}"
            logging.debug(
                f"Running remote command, first syncing CWD with '{rsync_cmd}'"
            )
            subprocess.run(
                rsync_cmd, shell=True, check=True, capture_output=True, text=True
            )

    args.func(args)


if __name__ == "__main__":
    main()
