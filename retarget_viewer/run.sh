#!/usr/bin/env bash
# 리타겟팅 웹 검증기 실행 (ro1 호스트, conda robosynapse).
#   bash run.sh                 -> http://127.0.0.1:8030/  (원격: ssh -L 8030:127.0.0.1:8030 ro1)
#   bash run.sh --port 8031     -> 다른 포트 (server.py 인자 그대로 전달)
# Quest 실시간 입력은 웹에서 [Quest 실시간]을 누르면 https://<ro1>:8013/?k=<토큰> 이 뜬다.
set -e
cd "$(dirname "$0")"
REPO="${ROBOSYNAPSE_REPO:-$HOME/workspace/robo-synapse}"
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate robosynapse
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
exec python server.py --repo "$REPO" "$@"
