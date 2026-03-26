#!/bin/bash
set -euo pipefail
source /home/blue/repo/atlas-cortex-cli/parts/satellite/run/environment.sh
set -x
"${PARTS_PYTHON_INTERPRETER}" -m venv ${PARTS_PYTHON_VENV_ARGS} "/home/blue/repo/atlas-cortex-cli/parts/satellite/install"
PARTS_PYTHON_VENV_INTERP_PATH="/home/blue/repo/atlas-cortex-cli/parts/satellite/install/bin/${PARTS_PYTHON_INTERPRETER}"
/home/blue/repo/atlas-cortex-cli/parts/satellite/install/bin/pip install  -U aiohttp websockets numpy
[ -f setup.py ] || [ -f pyproject.toml ] && /home/blue/repo/atlas-cortex-cli/parts/satellite/install/bin/pip install  -U .
find "/home/blue/repo/atlas-cortex-cli/parts/satellite/install" -type f -executable -print0 | xargs --no-run-if-empty -0 \
    sed -i "1 s|^#\!${PARTS_PYTHON_VENV_INTERP_PATH}.*$|#!/usr/bin/env ${PARTS_PYTHON_INTERPRETER}|"

# look for a provisioned python interpreter
opts_state="$(set +o|grep errexit)"
set +e
install_dir="/home/blue/repo/atlas-cortex-cli/parts/satellite/install/usr/bin"
stage_dir="/home/blue/repo/atlas-cortex-cli/stage/usr/bin"

# look for the right Python version - if the venv was created with python3.10,
# look for python3.10
basename=$(basename $(readlink -f ${PARTS_PYTHON_VENV_INTERP_PATH}))
echo Looking for a Python interpreter called \"${basename}\" in the payload...
payload_python=$(find "$install_dir" "$stage_dir" -type f -executable -name "${basename}" -print -quit 2>/dev/null || true)

if [ -n "$payload_python" ]; then
    # We found a provisioned interpreter, use it.
    echo Found interpreter in payload: \"${payload_python}\"
    installed_python="${payload_python##/home/blue/repo/atlas-cortex-cli/parts/satellite/install}"
    if [ "$installed_python" = "$payload_python" ]; then
        # Found a staged interpreter.
        symlink_target="..${payload_python##/home/blue/repo/atlas-cortex-cli/stage}"
    else
        # The interpreter was installed but not staged yet.
        symlink_target="..$installed_python"
    fi
else
    # Otherwise use what _get_system_python_interpreter() told us.
    echo "Python interpreter not found in payload." >&2
    symlink_target="/usr/bin/python3.12"
fi

if [ -z "$symlink_target" ]; then
    echo "No suitable Python interpreter found, giving up." >&2
    exit 1
fi

eval "${opts_state}"

ln -sf "${symlink_target}" "${PARTS_PYTHON_VENV_INTERP_PATH}"
