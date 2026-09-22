# -*- coding: utf-8 -*-
"""compose 가 고정 문자열로 적은 값이 schema/verdict.py 의 단일 소스와 같은지 검사한다.

compose 는 파이썬을 import 할 수 없어 토픽을 문자열로 적는다. 한쪽만 고치면 MinIO 는
엉뚱한 토픽에 발행하고 조정기는 아무것도 받지 못한 채 **조용히** 멈춘다 — 그래서 테스트로 묶는다.

    python -m unittest discover -s tests
"""
import os
import re
import sys
import unittest

_R = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_R, "schema"))
import verdict as V  # noqa: E402

_COMPOSE = os.path.join(_R, "infra", "docker-compose.yml")


def _minio_env(name: str) -> str:
    with open(_COMPOSE, encoding="utf-8") as f:
        m = re.search(rf'^\s*{name}:\s*"([^"]*)"', f.read(), re.M)
    if not m:
        raise AssertionError(f"compose 에 {name} 가 없다")
    return m.group(1)


class TestMinioNotifyConfig(unittest.TestCase):
    def test_topic_matches_single_source(self):
        self.assertEqual(_minio_env("MINIO_NOTIFY_MQTT_TOPIC_NS"), V.TOPIC_S3_EVENTS)

    def test_target_id_matches_arn(self):
        # 환경변수 접미사(대상 ID)가 ARN 의 ID 자리와 같아야 규칙이 대상을 찾는다
        self.assertEqual(_minio_env("MINIO_NOTIFY_MQTT_ENABLE_NS"), "on")
        self.assertEqual(V.S3_EVENT_TARGET_ARN, "arn:minio:sqs::NS:mqtt")

    def test_event_topic_cannot_collide_with_vehicle(self):
        with self.assertRaises(ValueError):
            V.vin_of("_cloud")
        for arm in V.ARMS:
            self.assertNotEqual(V.topic_verdict(V.vin_of(arm)), V.TOPIC_S3_EVENTS)


if __name__ == "__main__":
    unittest.main()
