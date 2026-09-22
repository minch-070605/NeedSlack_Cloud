# -*- coding: utf-8 -*-
"""aws.py — AWS 접근의 **유일한 통로** (구현설계_MVP.md §5-1).

로컬 ↔ AWS 전환이 환경변수 하나여야 한다. `boto3` / `paho` 를 이 모듈 밖에서 직접
부르는 코드가 하나라도 생기면 M4 에서 전부 찾아 고쳐야 한다.

    AWS_ENDPOINT_S3=http://localhost:9000        # 비우면 실제 AWS
    AWS_ENDPOINT_DYNAMODB=http://localhost:8000
    MQTT_HOST=localhost                          # M4 에서 IoT Core 엔드포인트로

M4 — 컴포넌트마다 실제 AWS 로 돌린다 (`infra/aws/m4.py`)
-------------------------------------------------------------------------------
    DDB_TARGET=aws     DynamoDB 를 실제 AWS 로 (자격은 M4_AWS_* — 로컬 AWS_* 와 섞지 않는다)
    MQTT_TARGET=aws    MQTT 를 AWS IoT Core 로 (mTLS 8883 · 인증서는 data/aws/certs/{client_id}.*)

평면 2(S3 번들)는 M4 에서도 로컬 MinIO 에 남는다(설계결정-0002). 그래서 S3 에는 스위치가 없다.
IoT Core 는 client_id 가 Thing 이름과 같아야 접속을 허락한다(정책변수 `${iot:Connection.Thing.ThingName}`).

값은 `.env` 에서 온다 — `run.py` 가 읽어 환경에 넣는다. 자격은 boto3 기본 체인
(AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)을 그대로 쓴다.

의존성은 **호출 시점에** import 한다. 재생기의 dry-run 과 단위 테스트는 이 모듈을
부르지 않으므로 .venv 가 비어 있어도 돈다.
"""
from __future__ import annotations

import json
import os


def _env(k: str, default: str | None = None) -> str:
    v = os.environ.get(k, default)
    if v is None:
        raise RuntimeError(f"환경변수 {k} 가 없다. .env.example 을 .env 로 복사하고 "
                           f"`python run.py …` 로 실행하라.")
    return v


def _endpoint(k: str) -> str | None:
    return os.environ.get(k) or None          # 빈 문자열 = 실제 AWS


def _target(kind: str) -> str:
    v = os.environ.get(f"{kind}_TARGET", "local")
    if v not in ("local", "aws"):
        raise RuntimeError(f"{kind}_TARGET={v!r} — local 또는 aws")
    return v


def mqtt_is_aws() -> bool:
    return _target("MQTT") == "aws"


def m4_session():
    """실제 AWS (IAM 사용자 ns-m4-admin). 루트 계정 키를 쓰지 않는다."""
    import boto3
    return boto3.Session(aws_access_key_id=_env("M4_AWS_ACCESS_KEY_ID"),
                         aws_secret_access_key=_env("M4_AWS_SECRET_ACCESS_KEY"),
                         region_name=_env("M4_AWS_REGION", "ap-northeast-2"))


M4_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "aws")


def m4_state() -> dict:
    p = os.path.join(M4_DIR, "m4.json")
    if not os.path.exists(p):
        raise RuntimeError(f"{p} 가 없다 — `python run.py m4 up` 을 먼저 돌린다")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def m4_cert_paths(client_id: str) -> tuple[str, str]:
    d = os.path.join(M4_DIR, "certs")
    return os.path.join(d, f"{client_id}.cert.pem"), os.path.join(d, f"{client_id}.private.key")


def aws_ca_bundle() -> str:
    """IoT Core 서버 인증서의 루트(Amazon Root CA)를 담은 botocore 의 CA 묶음. 새 의존성이 필요 없다."""
    import botocore
    return os.path.join(os.path.dirname(botocore.__file__), "cacert.pem")


def region() -> str:
    return _env("AWS_REGION", "ap-northeast-2")


def raw_bucket() -> str:
    """평면 2 도착지 (보고서 §2-6 `ns-raw-payload`)."""
    return _env("S3_BUCKET", "ns-raw-payload")


def dataset_bucket() -> str:
    """동결 데이터셋의 manifest · snapshot.json (설계결정-0002). 버킷 알림을 걸지 않는다."""
    return _env("DATASET_BUCKET", "ns-datasets")


def s3_client():
    import boto3
    return boto3.client("s3", endpoint_url=_endpoint("AWS_ENDPOINT_S3"), region_name=region())


def dynamodb_client():
    if _target("DDB") == "aws":
        return m4_session().client("dynamodb")
    import boto3
    return boto3.client("dynamodb", endpoint_url=_endpoint("AWS_ENDPOINT_DYNAMODB"),
                        region_name=region())


def mqtt_client(client_id: str, persistent: bool = False, on_connect=None, on_message=None,
                on_disconnect=None):
    """연결된 MQTT 클라이언트. 네트워크 루프를 백그라운드로 돌린 채 돌려준다.

    `persistent=True` 는 **구독자용**이다 — 영속 세션(`clean_session=False`)에 수동 PUBACK.
    브로커가 이 client_id 의 구독과 미확인 QoS 1 메시지를 보관하므로, 구독자가 꺼져 있던 동안의
    메시지와 처리 도중 죽은 메시지가 재접속 후 다시 온다. 처리를 마친 뒤 `c.ack(mid, qos)` 를
    부르는 것은 호출자 몫이다. client_id 는 고정이어야 세션이 이어진다.

    콜백은 연결 **전에** 건다 — 재접속 때 on_connect 가 구독을 다시 걸 수 있어야 한다.

    M1 은 Mosquitto 익명 접속이다. M4 에서 IoT Core 로 옮길 때 X.509 설정이 여기에만 들어간다.
    """
    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                    clean_session=not persistent, manual_ack=persistent)
    if on_connect is not None:
        c.on_connect = on_connect
    if on_message is not None:
        c.on_message = on_message
    if on_disconnect is not None:
        c.on_disconnect = on_disconnect
    if mqtt_is_aws():
        cert, key = m4_cert_paths(client_id)
        if not os.path.exists(cert):
            raise RuntimeError(f"{cert} 가 없다 — IoT Core 는 client_id({client_id}) 와 같은 이름의 Thing 인증서로만 붙는다")
        c.tls_set(ca_certs=aws_ca_bundle(), certfile=cert, keyfile=key)
        c.connect(m4_state()["iot_endpoint"], 8883, keepalive=60)
    else:
        c.connect(_env("MQTT_HOST", "localhost"), int(_env("MQTT_PORT", "1883")), keepalive=60)
    c.loop_start()
    return c
