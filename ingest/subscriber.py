# -*- coding: utf-8 -*-
"""subscriber.py — 수집기·조정기가 함께 쓰는 MQTT 구독 루프.

규약 (구현설계_MVP.md §8)
-------------------------------------------------------------------------------
- **영속 세션 + 수동 PUBACK.** 처리를 마친 메시지만 확인한다. 저장소 오류면 확인하지 않고
  멈춘다 — 다시 켜면 브로커가 미확인 메시지를 재전송한다.
- **독 메시지**(`rejected` 예외)는 재전송해도 같으므로 확인하고 센다.

처리는 네트워크 스레드가 아니라 작업 스레드에서 한다
-------------------------------------------------------------------------------
paho 는 keepalive 가 지나면 PINGREQ 를 보내고, 다음 keepalive 까지 PINGRESP 를 **읽지 못하면**
스스로 연결을 끊는다. 콜백 안에서 번들 하나(DynamoDB 호출 약 106 회 × 76 ms ≈ 8 s)를 처리하면
브로커가 미리 보낸 메시지들 뒤에 선 PINGRESP 를 60 s 안에 읽지 못한다 — M1 완주에서 조정기가
정확히 120 s(= keepalive 60 s × 2)마다 끊겼다 다시 붙었다(결과는 멱등이라 무해했다).
그래서 네트워크 스레드는 받은 메시지를 큐에 넣기만 하고, 작업 스레드 하나가 **받은 순서대로**
처리하고 확인한다. `client.ack` 는 paho 내부 뮤텍스로 보호돼 다른 스레드에서 불러도 된다.
"""
from __future__ import annotations

import queue
import threading
from typing import Callable, Optional


def run(client_id: str, topics: list[str], handle: Callable[[str, bytes], None],
        rejected: type[Exception], counter, summary: Callable[[], str], banner: str,
        startup: Optional[Callable[[], str]] = None,
        tick: Optional[Callable[[], None]] = None, tick_every_s: float = 30.0,
        stats_every_s: float = 10.0) -> int:
    """구독자 하나를 돌린다. Ctrl+C 로 멈춘다. 저장소 오류로 멈추면 1."""
    from common import aws

    inbox: "queue.Queue" = queue.Queue()
    stop = threading.Event()
    fatal: list[BaseException] = []

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"MQTT 연결 실패: {reason_code}", flush=True)
            return
        # 영속 세션이라 구독이 남아 있을 수 있지만, 첫 연결·세션 만료에 대비해 늘 다시 건다
        c.subscribe([(t, 1) for t in topics])
        counter["connects"] += 1
        print(f"구독  {', '.join(topics)}  (세션 {'이어받음' if flags.session_present else '새로'})",
              flush=True)

    def on_disconnect(c, userdata, flags, reason_code, properties):
        if not stop.is_set():
            counter["disconnects"] += 1
            print(f"★ MQTT 연결 끊김: {reason_code} — paho 가 다시 붙는다", flush=True)

    def on_message(c, userdata, m):
        inbox.put(m)                            # 여기서 처리하지 않는다 — 머리말 참조

    def worker():
        while not stop.is_set():
            try:
                m = inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                handle(m.topic, m.payload)
            except rejected as e:
                counter["rejected"] += 1
                print(f"거부  {m.topic}: {e}", flush=True)
            except BaseException as e:          # 저장소 오류 — 확인하지 않고 멈춘다
                fatal.append(e)
                stop.set()
                return
            mq.ack(m.mid, m.qos)

    print(banner, flush=True)
    mq = aws.mqtt_client(client_id, persistent=True, on_connect=on_connect,
                         on_message=on_message, on_disconnect=on_disconnect)
    th = threading.Thread(target=worker, name=f"{client_id}-worker", daemon=True)
    th.start()
    try:
        if startup is not None:
            print(startup(), flush=True)
        next_tick, waited = 0.0, 0.0
        while not stop.is_set():
            if tick is not None and waited >= next_tick:
                tick()
                next_tick = waited + tick_every_s
            if stop.wait(stats_every_s):
                break
            waited += stats_every_s
            print("  " + summary(), flush=True)
    except KeyboardInterrupt:
        pass
    except BaseException as e:                  # startup·tick 중 저장소 오류
        fatal.append(e)
    finally:
        stop.set()
        th.join(timeout=30)
        mq.loop_stop()
        mq.disconnect()
    print("정지  " + summary(), flush=True)
    if fatal:
        print(f"★ 저장소 오류로 멈췄다 — 처리하지 못한 메시지는 확인하지 않았다(재시작하면 "
              f"재전송된다): {fatal[0]!r}", flush=True)
        return 1
    return 0
