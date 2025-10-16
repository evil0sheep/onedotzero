#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import logging

# --- Virtual Environment Check ---
# Ensure the script is running in a venv.
if sys.prefix == sys.base_prefix:
    print(
        "ERROR: This script must be run in a Python virtual environment.",
        file=sys.stderr,
    )
    print(
        "Please run './scripts/init.sh' to create one, then activate it with:",
        file=sys.stderr,
    )
    print("source .venv/bin/activate", file=sys.stderr)
    sys.exit(1)
# --- End Check ---

import yaml
import time
import jinja2
import shlex

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
ANSIBLE_TESTING_HOST = "odz_test"

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
    command, remote=True, capture_output=False, check=True, suppress_errors=False, remote_host_override=None
):
    """Runs a command locally or remotely."""
    remote_host = HARDWARE_CONFIG.get("control_host", "control")

    if remote_host_override:
        remote_host = remote_host_override

    # Construct the command
    if remote:
        command = command.replace("'", "'\\''")
        # The `exec` command ensures that ssh exits with the code of the remote command.
        cmd_to_run = f"ssh {remote_host} 'cd {REMOTE_DIR} && {command}'"
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
    inventory_lines = []
    for node in compute_nodes:
        template = env.from_string(node["ip"])
        rendered_ip = template.render(ansible_vars)
        inventory_lines.append(f"{node['name']} ansible_host={rendered_ip}")

    inventory_content = "[compute]\n"
    inventory_content += "\n".join(inventory_lines)
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
        all_statuses = get_all_compute_node_statuses(
            args.remote, DYN_INVENTORY_RELATIVE_PATH
        )
        if all(status == "UP" for status in all_statuses.values()):
            print("All compute nodes are reachable.")
            return 0

        down_nodes = [
            name for name, status in all_statuses.items() if status == "DOWN"
        ]
        print(
            f"Attempt {i + 1}/100 failed. Waiting for: {', '.join(down_nodes)}. Retrying in 1 second..."
        )
        time.sleep(1)

    logging.error(f"Not all compute nodes were reachable after 100 attempts.")
    sys.exit(1)


def compute_configure(args):
    """Configures compute nodes using Ansible."""
    logging.info("Configuring compute nodes...")

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


def compute_cmd(args):
    """Executes a command on a compute node."""
    node_index = args.node_index
    try:
        node_name = HARDWARE_CONFIG["compute_nodes"][node_index]["name"]
    except IndexError:
        logging.error(f"Invalid node index: {node_index}")
        sys.exit(1)

    quoted_command = shlex.quote(args.command)
    command = (
        f"ansible {node_name} -i {DYN_INVENTORY_RELATIVE_PATH} "
        f"-m shell -a {quoted_command}"
    )
    run_command(command, remote=args.remote)


def control_ssh(args):
    """SSH into the control node."""
    if not args.remote:
        print("Already on the control node, no need to SSH.")
        return

    remote_host = HARDWARE_CONFIG.get("control_host", "control")
    command = f"ssh -t {remote_host}"
    print(f"Connecting to {remote_host}...")
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


def control_test(args):
    """Tests the control node using Ansible."""
    inventory_path = os.path.join(
        "ansible/inventory", get_hardware_version(), "hosts.ini"
    )
    command = (
        f"ansible-playbook -i {inventory_path} ansible/control_test.yml "
        f"--extra-vars 'hardware_version={get_hardware_version()}' --become"
    )
    run_command(command, remote=args.remote)
    logging.info("Control node testing complete.")


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


def control_clean_image(args):
    """Removes the golden image on the control node."""
    logging.info("Removing golden image using ansible playbook...")
    inventory_path = os.path.join(
        "ansible/inventory", get_hardware_version(), "hosts.ini"
    )
    command = (
        f"ansible-playbook -i {inventory_path} ansible/clean_image.yml "
        f"--extra-vars 'hardware_version={get_hardware_version()}'"
    )
    run_command(command, remote=True)
    logging.info("Golden image removed.")


def control_cmd(args):
    """Executes a command on the control node."""
    run_command(args.command, remote=True)


def ansible_testing_configure(args):
    """configures testing host"""
    inventory_path = os.path.join(
        "ansible/inventory", get_hardware_version(), "hosts.ini"
    )
    command = (
        f"ansible-playbook -i {inventory_path} ansible/testing_configure.yml "
        f"--extra-vars 'hardware_version={get_hardware_version()}'"
    )
    run_command(command, remote=True, remote_host_override=ANSIBLE_TESTING_HOST)


def ansible_lint(args):
    """Lints all ansible files."""
    logging.info("Linting all ansible files...")
    command = "source ~/venvs/onedotzero/bin/activate && ansible-lint"
    run_command(command, remote=True, remote_host_override=ANSIBLE_TESTING_HOST)
    logging.info("Ansible linting complete.")


def ansible_test(args):
    """Tests an Ansible role or scenario using Molecule."""
    test_name = args.test_name
    logging.info(f"Searching for Molecule test: {test_name}")

    # Define remote search paths relative to the remote project rootPROJECT_ROOT
    if args.test_remote:
        project_dir = os.path.join(REMOTE_DIR, "ansible")
    else:
        project_dir = os.path.join(PROJECT_ROOT, "ansible")
    search_paths = [
        os.path.join(project_dir, "tests"),
        os.path.join(project_dir, "roles"),
    ]

    target_path = None
    for base_path in search_paths:
        potential_path = os.path.join(base_path, test_name)
        try:
            # Check if the directory exists on the remote
            run_command(f"test -d {potential_path}", remote=args.test_remote, capture_output=True, remote_host_override=ANSIBLE_TESTING_HOST)
            target_path = potential_path
            logging.info(f"Found test at  path: {target_path}")
            break
        except subprocess.CalledProcessError:
            continue

    if not target_path:
        logging.error(f"Molecule test '{test_name}' not found")
        logging.error("Searched in:")
        for path in search_paths:
            logging.error(f"  - {os.path.join(path, test_name)}")
        sys.exit(1)


    ## some kind of weird bug in molecule makes it so that the vagrant playbooks cant
    # find the vagrant plugin and so the path has to be manually specified. This seems
    # super broken and bad to me but i couldnt figure it out so instead we do this weird workaround
    #
    # Define the path to the custom modules within the remote venv
    if args.test_remote:
        venv_dir = "~/venvs/onedotzero"
    else:
        venv_dir = f'{PROJECT_ROOT}/.venv'
    module_path = f"{venv_dir}/lib/python*/site-packages/molecule_vagrant/modules"

    # 1. Find the exact module path on the remote machine by searching for the 'modules' directory.
    find_cmd = f"find {venv_dir}/lib/python*/site-packages/molecule_vagrant -name 'modules' -type d"
    proc = run_command(find_cmd, remote=args.test_remote, capture_output=True, remote_host_override=ANSIBLE_TESTING_HOST)

    # Take only the first line of output to be safe.
    module_path = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else None

    if not module_path:
        logging.error("Could not find the molecule-vagrant module path on the remote.")
        sys.exit(1)

    # 2. Build and run the final command, exporting the discovered path.
    command = (
        f"source {venv_dir}/bin/activate && "
        f"cd {target_path} && "
        f"export ANSIBLE_LIBRARY='{module_path}' && "
        f"molecule test"
    )

    run_command(command, remote=args.test_remote, remote_host_override=ANSIBLE_TESTING_HOST)
    logging.info(f"Molecule test for '{test_name}' complete.")


def cluster_configure(args):
    """Configures the entire cluster from scratch."""
    logging.info("--- Starting Full Cluster Configuration ---")

    # Check compute node status before shutting down
    logging.info("Checking status of compute nodes...")
    compute_nodes = HARDWARE_CONFIG.get("compute_nodes", [])
    if not compute_nodes:
        logging.info("No compute nodes defined, skipping shutdown check.")
        compute_nodes_up = False
    else:
        all_statuses = get_all_compute_node_statuses(
            args.remote, DYN_INVENTORY_RELATIVE_PATH
        )
        for node in compute_nodes:
            status = all_statuses.get(node["name"], "DOWN")
            logging.info(f"  - Host: {node['name']} ({node['ip']}) is {status}")
        compute_nodes_up = "UP" in all_statuses.values()

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
    print("Compute Nodes:")
    compute_nodes = HARDWARE_CONFIG.get("compute_nodes", [])
    if not compute_nodes:
        print("  - No compute nodes defined in hardware config.")
    else:
        print(f"  - Expected {len(compute_nodes)} node(s) based on config.")
        all_statuses = get_all_compute_node_statuses(
            args.remote, DYN_INVENTORY_RELATIVE_PATH
        )
        for node in compute_nodes:
            status = all_statuses.get(node["name"], "DOWN")
            print(f"  - Host: {node['name']} ({node['ip']})")
            print(f"    - Status: {status}")

    print("----------------------")


def get_all_compute_node_statuses(remote, inventory_file):
    """Checks the status of all compute hosts at once."""
    command = f"ansible compute -i {inventory_file} -m ping -o --timeout 3"
    proc = run_command(command, remote=remote, capture_output=True, check=False)

    statuses = {}
    compute_nodes = HARDWARE_CONFIG.get("compute_nodes", [])
    for node in compute_nodes:
        statuses[node["name"]] = "DOWN"

    if proc.stdout:
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) > 2 and parts[2] == "SUCCESS":
                hostname = parts[0]
                statuses[hostname] = "UP"
    return statuses


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
    print("* `cluster configure`: Configure the entire cluster.")

    print("\n## Compute Commands (`cluster compute ...`)")
    print("* `status`: Get a quick status of the cluster.")
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
    parser.add_argument(
        "--test-remote",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Execute commands on the remote host (default: True).",
    )

    subparsers = parser.add_subparsers(dest="command")

    # Top-level commands
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
    compute_subparsers.add_parser(
        "status", help="Get a quick status of the cluster."
    ).set_defaults(func=cluster_status)
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

    compute_cmd_parser = compute_subparsers.add_parser(
        "cmd", help="Execute a command on a compute node."
    )
    compute_cmd_parser.add_argument(
        "node_index", type=int, help="The 0-based index of the compute node."
    )
    compute_cmd_parser.add_argument("command", type=str, help="The command to execute.")
    compute_cmd_parser.set_defaults(func=compute_cmd)

    # Control commands
    control_parser = subparsers.add_parser("control", help="Manage the control node.")
    control_subparsers = control_parser.add_subparsers(dest="action", required=True)
    control_subparsers.add_parser(
        "configure", help="Run Ansible configuration on the control node."
    ).set_defaults(func=control_configure)
    control_subparsers.add_parser(
        "test", help="Run Ansible tests on the control node."
    ).set_defaults(func=control_test)
    control_subparsers.add_parser(
        "build_image", help="Build the golden image."
    ).set_defaults(func=control_build_image)
    cmd_parser = control_subparsers.add_parser(
        "cmd", help="Execute a command on the control node."
    )
    cmd_parser.add_argument("command", type=str, help="The command to execute.")
    cmd_parser.set_defaults(func=control_cmd)

    control_subparsers.add_parser(
        "ssh", help="SSH into the control node."
    ).set_defaults(func=control_ssh)
    control_subparsers.add_parser(
        "clean_image", help="Removes the golden image on the control node."
    ).set_defaults(func=control_clean_image)

    # Ansible commands
    ansible_parser = subparsers.add_parser("ansible", help="Manage ansible content.")
    ansible_subparsers = ansible_parser.add_subparsers(dest="action", required=True)
    ansible_subparsers.add_parser(
        "testing_configure", help="Configure testing environment for ansible tests"
    ).set_defaults(func=ansible_testing_configure)
    ansible_subparsers.add_parser(
        "lint", help="Lint all ansible files."
    ).set_defaults(func=ansible_lint)
    test_parser = ansible_subparsers.add_parser(
        "test", help="Test an ansible role or scenario using molecule."
    )
    test_parser.add_argument("test_name", type=str, help="The test to run (e.g., 'e2e' or 'control/base').")
    test_parser.set_defaults(func=ansible_test)

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

    if args.command not in ["doc", "hardware"]:
        version = get_hardware_version()
        load_hardware_config(version)
        generate_inventory()
        if args.remote:
            if args.command == "ansible":
                remote_host = ANSIBLE_TESTING_HOST
            else:
                remote_host = HARDWARE_CONFIG.get("control_host", "control")

            mkdir_cmd = f"ssh {remote_host} 'mkdir -p ~/remote'"
            subprocess.run(
                mkdir_cmd, shell=True, check=True, capture_output=True, text=True
            )

            # Rsync is always checked because if it fails, nothing else will work.
            rsync_cmd = f"rsync -avz --delete --exclude='.git' --exclude='.venv' {PROJECT_ROOT}/ {remote_host}:{REMOTE_DIR}"
            logging.debug(
                f"Running remote command, first syncing CWD with '{rsync_cmd}'"
            )
            subprocess.run(
                rsync_cmd, shell=True, check=True, capture_output=True, text=True
            )

    args.func(args)


if __name__ == "__main__":
    main()
