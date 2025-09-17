#!/usr/bin/env python3

import subprocess
import sys
import os

def main():
    if len(sys.argv) < 2:
        print("Usage: remote.py <command>")
        sys.exit(1)

    command = " ".join(sys.argv[1:])

    # Get the absolute path of the script and the directory it's in
    script_path = os.path.abspath(__file__)
    local_dir = os.path.dirname(script_path)

    remote_host = "control"
    remote_base_dir = "~/remote"
    remote_project_dir = os.path.join(remote_base_dir, os.path.basename(local_dir))

    # Step 1: Create the remote base directory
    # Use ssh to create the remote directory. We don't want to fail if it exists.
    ssh_mkdir_command = [
        "ssh",
        remote_host,
        f"mkdir -p {remote_base_dir}"
    ]
    # We don't check the result of this, as it will fail if the directory exists, which is fine.
    subprocess.run(ssh_mkdir_command, check=False)


    # Step 2: rsync the local directory to the remote one
    rsync_command = [
        "rsync",
        "-avz",
        "--exclude",
        ".git",
        f"{local_dir}/",
        f"{remote_host}:{remote_project_dir}"
    ]

    print(f"Running rsync: {' '.join(rsync_command)}")
    rsync_result = subprocess.run(rsync_command, capture_output=True, text=True)

    if rsync_result.returncode != 0:
        print("Rsync failed:")
        print(rsync_result.stdout)
        print(rsync_result.stderr)
        sys.exit(1)


    # Step 3: Execute the command on the remote host
    ssh_command = [
        "ssh",
        remote_host,
        f"cd {remote_project_dir} && {command}"
    ]

    print(f"Running command on {remote_host}: {' '.join(ssh_command)}")
    # We want to stream the output of the remote command, so we don't capture it.
    # The remote command's stdin, stdout, and stderr are inherited from the current process.
    ssh_result = subprocess.run(ssh_command)

    sys.exit(ssh_result.returncode)

if __name__ == "__main__":
    main()
