#!/usr/bin/env bash
# Source this file to make CTranslate2 find CUDA 12 / cuDNN 9 installed as pip wheels:
#   source scripts/gpu_env.sh
# It is a no-op when the NVIDIA wheels are not installed (CPU-only setups).
set -u

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_python="${VIRTUAL_ENV:-$_here/../.venv}/bin/python"
[ -x "$_python" ] || _python="python3"

_nvidia_paths="$("$_python" - <<'PY' 2>/dev/null
import os
try:
    import nvidia.cublas
    import nvidia.cudnn
except ImportError:
    raise SystemExit(1)
print(
    os.path.dirname(nvidia.cublas.__path__[0]) + "/cublas/lib:"
    + os.path.dirname(nvidia.cudnn.__path__[0]) + "/cudnn/lib"
)
PY
)"

if [ -n "${_nvidia_paths:-}" ]; then
    export LD_LIBRARY_PATH="${_nvidia_paths}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    echo "AudioLens: CUDA/cuDNN wheels added to LD_LIBRARY_PATH" >&2
else
    echo "AudioLens: NVIDIA wheels not found, running CPU-only" >&2
fi

unset _here _python _nvidia_paths
