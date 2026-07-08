#!/bin/bash
cd /home/dx/Projects/transcription
source venv/bin/activate
export LD_LIBRARY_PATH=$(python3 -c "import nvidia.cublas; import nvidia.cudnn; print(nvidia.cublas.__path__[0] + '/lib:' + nvidia.cudnn.__path__[0] + '/lib')"):$LD_LIBRARY_PATH
python3 transcribe.py "$@"
