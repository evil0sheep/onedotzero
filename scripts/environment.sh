setup_env() {
    # ANSI Color Codes
    local RED='\033[0;31m'
    local YELLOW='\033[0;33m'
    local NC='\033[0m' # No Color (Reset)

    # Robustly check if we are in the project root by looking for key files/dirs
    if [[ ! -f "scripts/environment.sh" || ! -d "ansible" ]]; then
        echo -e "${RED}Error: This script must be sourced from the project root directory.${NC}"
        return 1
    fi

    # Set project root for use within this function
    local ODZ_PROJECT_ROOT="$PWD"

    # Add project's bin directory to PATH if it's not already there
    local BIN_DIR="${ODZ_PROJECT_ROOT}/bin"
    case ":${PATH}:" in
        *":${BIN_DIR}:"*)
            # Already in PATH
            ;;
        *)
            export PATH="${BIN_DIR}:${PATH}"
            ;;
    esac

    local VENV_ACTIVATE="${ODZ_PROJECT_ROOT}/.venv/bin/activate"

    if [ ! -f "$VENV_ACTIVATE" ]; then
        echo -e "${YELLOW}Error: Virtual environment not found at ${VENV_ACTIVATE}, calling init.sh to create the venv${NC}"
        "${ODZ_PROJECT_ROOT}/scripts/init.sh"
    fi
    source "$VENV_ACTIVATE"
}

setup_env
