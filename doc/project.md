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
We will be working on my macbook editing files locally and then rsyncing them to the NUC and executing them remotely over SSH using the `cluster` command. if you run commands locally you will not have access to ansible or the compute nodes. use `cluster control cmd` to execute custom commands on the control node. Please feel free to run `cluster doc` or `cluster --help` for information on how to use this tool.

Please read each task in the todo list one-by-one and for each item think about what is most ambiguous about the task description and then ask disambiguating questions which clarify the goals and requirements. Once your questions have been answered you can proceed with the task and when you are done please stop and ask for review, and once approved you can check off that task with a ✅ at the beginning of the task description and proceed to the next task. If you run into problems PLEASE STOP and explain the problem so we can discuss solutions before continuing. DO NOT iterate non-interactively trying to solve a problem.

# TODO

1. Modify Ansible workflows to install vLLM and dependencies and verify proper configuration
  1. Add intel oneAPI apt repository and key on compute nodes in `ansible/roles/hardware_specific/intel_arc_drivers/tasks/main.yml` following the pattern used for the intel GPU drivers. Verfiy in the corresponding `verify.yml` by running `sycl-ls` and ensuring that the output contains `Intel(R) Arc(TM) A770 Graphics`. You will need to ensure that `/opt/intel/oneapi/setvars.sh` is sourced on the compute nodes before verification and also before the next configuration step.
  2. Make a new `hardware_specific/ipex_llm` role and have it install pip and use pip to install the intel extensions for pytorch (IPEX). Verify in a new `verify.yml` (folowing the pattern from the `hardware_specific/intel_arc_drivers` role) by having ansible create a python pytest module which does `import intel_extension_for_pytorch as ipex` and confirms that `ipex.xpu.is_available() == True`,  `ipex.xpu.device_count() == 1` and that allocates a tensor, does `.to('xpu').to('cpu')` and verifies the content is unchanged. Add this new role to both `compute_configure.yml` and `control_configure.yml` (following the pattern of the `Include hardware-specific configuration roles` task from `compute_configure.yml`). Make a new top level ansible playbook `control_test.yml` targeting the control nodes which runs the ipex_llm `verify.yml` and a new command `cluster control test` which runs `control_test.yml`.
  3. Make new ansible roles for Apache Ray that installs ray on both the compute nodes and the control node and configures Ray to use the control node as the Ray head node and the compute nodes as the Ray worker nodes. Verify in a new `verify.yml` that `ray status` shows the cluster topology that is expected given the definition of `compute_nodes` in `ansible/hardware_vars/0.1.yml`. Add the new `verify.yml` to `control_test.yml` (not hardware specific)
  4. Make a new hardware specific ansible role attached to `compute_configure.yml` which builds and installs vLLM for intel using the following commands. Verify in a corresponding `verify.yml`by running vLLM, loading up `shuyuej/gemma-2b-it-GPTQ`, and hitting it with a test prompt. Be sure to configure it for reproducable testing (zero temperature, fixed random seed, etc)
  ```bash
  source /opt/intel/oneapi/setvars.sh
  pip install --upgrade pip
  pip install -v -r requirements-xpu.txt
  VLLM_TARGET_DEVICE=xpu python setup.py install
  ```

  5. Make a new ansible role for vLLM which installs the vLLM server on the control node and configures it for tensor parallelism and expert parallelism based on the definition of `compute_nodes` in `ansible/hardware_vars/0.1.yml`. You should use `https://huggingface.co/AMead10/Mistral-Small-Instruct-2409-awq` for testing
