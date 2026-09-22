# -*- coding: utf-8 -*-
"""run.py — 이 프로젝트의 단일 진입점. make 대신 쓴다 (Windows 에 make 가 없다).

    python run.py <명령>

    check      환경 점검 (WSL · Docker Desktop · 엔진)       infra/bootstrap/00_check.ps1
    up         로컬 스택 기동                                docker compose up -d
    down       로컬 스택 정지 (데이터 유지)                  docker compose down
    bootstrap  버킷·테이블·PG 스키마 생성 (멱등)             infra/bootstrap/01_create.py
    test       단위 테스트                                   unittest discover -s tests
    contract   계약·팔별 신호 스냅샷이 rev.9 와 맞는지 확인  gen_edge_contract.py --check
                                                             gen_arm_signals.py --check
    replay ARM [--dry-run] [--limit N] [--speed X]
               차량 시뮬레이터 — 한 팔의 pool 후보 전량 재생  edge-agent/replay.py
    collect    수집기 — 판정 평면 MQTT → DynamoDB (Ctrl+C)   ingest/collector.py
    reconcile  조정기 — S3 이벤트 → 상태 전이·체크섬 (Ctrl+C) ingest/reconciler.py
    snapshot common|ARM
               M2 데이터셋 스냅샷 (tar · manifest · 계보)     pipelines/snapshot.py
    notebook   M3 Colab 노트북을 굽는다                      pipelines/build_colab_nb.py
    compare [경로]
               M3 결과 검증 · 계보 등록 · 오프라인 대조       pipelines/compare.py
    status [출력경로]
               진행 현황 페이지를 산출물에서 굽는다          docs/build_status.py
    guide [출력경로]
               도메인 입문 페이지를 굽는다                   docs/build_guide.py

어느 파이썬으로 불러도 `.venv` 인터프리터로 다시 실행한다. 여기서 막는 함정
(00_README.md §5 "Windows 함정"):
  - 콘솔 cp949 → 자식 프로세스에 PYTHONUTF8=1, 자신의 stdout 은 UTF-8 로 재설정
  - .env 를 아무도 읽지 않음 → 여기서 읽어 자식 프로세스 환경에 넣는다
  - .ps1 실행 정책 → -ExecutionPolicy Bypass (이 프로세스에만 적용)

stdlib 만 쓴다 — .venv 가 비어 있어도 돌아야 한다.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(ROOT, ".venv", "Scripts" if os.name == "nt" else "bin",
                       "python.exe" if os.name == "nt" else "python")
COMPOSE = ["docker", "compose", "-f", os.path.join(ROOT, "infra", "docker-compose.yml")]


def _in_venv() -> bool:
    return os.path.normcase(os.path.abspath(sys.prefix)) == \
        os.path.normcase(os.path.join(ROOT, ".venv"))


def _load_env(path: str) -> dict[str, str]:
    """KEY=VALUE 만 읽는다. 행 끝 `  # 주석` 과 따옴표를 벗긴다. 이미 있는 환경변수가 이긴다."""
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.split(" #", 1)[0].strip().strip('"').strip("'")
            out[k.strip()] = v
    return out


def _env() -> dict[str, str]:
    env = dict(os.environ)
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for k, v in _load_env(path).items():
            env.setdefault(k, v)
    env["PYTHONUTF8"] = "1"
    return env


def _need_env_file() -> None:
    if not os.path.exists(os.path.join(ROOT, ".env")):
        sys.exit(".env 가 없다. .env.example 을 .env 로 복사해 채워라 (커밋하지 않는다).")


def _run(cmd: list[str]) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT, env=_env())


def cmd_check() -> int:
    return _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 os.path.join(ROOT, "infra", "bootstrap", "00_check.ps1")])


def cmd_up() -> int:
    return _run(COMPOSE + ["up", "-d", "--wait"])


def cmd_down() -> int:
    return _run(COMPOSE + ["down"])


def cmd_bootstrap() -> int:
    _need_env_file()
    return _run([sys.executable, os.path.join(ROOT, "infra", "bootstrap", "01_create.py")])


def cmd_test() -> int:
    return _run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])


def cmd_contract() -> int:
    rc = _run([sys.executable, os.path.join(ROOT, "schema", "gen_edge_contract.py"), "--check"])
    return rc or _run([sys.executable, os.path.join(ROOT, "schema", "gen_arm_signals.py"), "--check"])


def cmd_replay(*args: str) -> int:
    _need_env_file()
    return _run([sys.executable, os.path.join(ROOT, "edge-agent", "replay.py"), *args])


def cmd_collect(*args: str) -> int:
    _need_env_file()
    return _run([sys.executable, os.path.join(ROOT, "ingest", "collector.py"), *args])


def cmd_reconcile(*args: str) -> int:
    _need_env_file()
    return _run([sys.executable, os.path.join(ROOT, "ingest", "reconciler.py"), *args])


def cmd_snapshot(*args: str) -> int:
    _need_env_file()
    return _run([sys.executable, os.path.join(ROOT, "pipelines", "snapshot.py"), *args])


def cmd_notebook(*args: str) -> int:
    _need_env_file()
    return _run([sys.executable, os.path.join(ROOT, "pipelines", "build_colab_nb.py"), *args])


def cmd_compare(*args: str) -> int:
    _need_env_file()
    return _run([sys.executable, os.path.join(ROOT, "pipelines", "compare.py"), *args])


def cmd_status(*args: str) -> int:
    return _run([sys.executable, os.path.join(ROOT, "docs", "build_status.py"), *args])


def cmd_guide(*args: str) -> int:
    return _run([sys.executable, os.path.join(ROOT, "docs", "build_guide.py"), *args])


COMMANDS = {
    "check": cmd_check, "up": cmd_up, "down": cmd_down,
    "bootstrap": cmd_bootstrap, "test": cmd_test, "contract": cmd_contract,
    "replay": cmd_replay, "collect": cmd_collect, "reconcile": cmd_reconcile, "snapshot": cmd_snapshot, "notebook": cmd_notebook, "compare": cmd_compare, "status": cmd_status, "guide": cmd_guide,
}
#: 인자를 받는 명령
TAKES_ARGS = {"replay", "collect", "reconcile", "snapshot", "compare", "status", "guide"}


def main(argv: list[str]) -> int:
    if not _in_venv():
        if not os.path.exists(VENV_PY):
            sys.exit(".venv 가 없다:  python -m venv .venv  →  "
                     ".venv/Scripts/python -m pip install -r requirements.txt")
        return subprocess.call([VENV_PY, os.path.abspath(__file__)] + argv, env=_env())

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    if not argv or argv[0] not in COMMANDS or (len(argv) > 1 and argv[0] not in TAKES_ARGS):
        print(__doc__)
        return 2
    return COMMANDS[argv[0]](*argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
