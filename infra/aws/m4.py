# -*- coding: utf-8 -*-
"""m4.py — M4 AWS 수직 절단면. 판정 평면을 실제 AWS IoT Core · DynamoDB 로 올리고, 차량 격리를 확인하고, 치운다.

    python run.py m4 plan      # 만들 자원 목록만 (아무것도 바꾸지 않는다)
    python run.py m4 up        # 만든다 — 멱등. data/aws/m4.json 에 기록
    python run.py m4 check     # 격리 확인 — 토픽 · client_id · S3 접두사
    python run.py m4 run       # 짧은 재생(DYN_B 앞 3,000 후보)을 IoT Core → AWS DynamoDB 로 통과
    python run.py m4 down      # 전부 지우고 남은 것이 없음을 보인다

범위 (README §4 M4 · 설계결정-0002)
-------------------------------------------------------------------------------
    판정 평면   차량 Thing 2 · X.509 인증서 · IoT 정책(토픽 정책변수로 차량 격리) → AWS IoT Core
                수집기(로컬 PC) 가 IoT Core 를 구독해 AWS DynamoDB 에 적재한다 — 서버를 배포하지 않는다
    S3 격리     Credential Provider(role alias → IAM 역할) 가 준 임시 자격이 자기 vin 접두사에만 쓰는지 — 소량 객체로
    평면 2      사진 번들은 로컬 MinIO 에 남는다

비용 — 켜 두면 과금되는 자원(EC2 · NAT 등)이 없다. 사용량은 메시지 수백 · 쓰기 수천 건 규모다.
자격 — IAM 사용자 ns-m4-admin (정책 infra/aws/ns-m4-admin-policy.json). 루트 키를 쓰지 않는다.
되돌리기 — `down` 이 기록과 이름으로 전부 지운다. 마지막에 콘솔에서 IAM 사용자와 키를 지운다.
"""
from __future__ import annotations

import http.client
import json
import os
import ssl
import subprocess
import sys
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (os.path.join(ROOT, "schema"), os.path.join(ROOT, "ingest"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import verdict as V                 # noqa: E402
from common import aws              # noqa: E402

REGION = os.environ.get("M4_AWS_REGION", "ap-northeast-2")
VEHICLES = tuple(V.vin_of(a) for a in V.ARMS)          # sim-STA_B · sim-DYN_B
COLLECTOR = "ns-collector"
POLICY_VEHICLE, POLICY_COLLECTOR = "ns-vehicle", "ns-collector"
ROLE = ALIAS = "ns-m4-vehicle-s3"
LOCAL_BUCKET = "ns-m4-local"                           # M4 재생의 평면 2 (로컬 MinIO · 알림 없음)
RECONCILER_ID = "ns-reconciler-m4"
STATE = os.path.join(aws.M4_DIR, "m4.json")
CERTS = os.path.join(aws.M4_DIR, "certs")


def table_spec() -> dict:
    with open(os.path.join(ROOT, "schema", "ddb_table.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def load_state() -> dict:
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(st: dict) -> None:
    os.makedirs(aws.M4_DIR, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, ensure_ascii=False)


def err_code(e: Exception) -> str:
    return getattr(e, "response", {}).get("Error", {}).get("Code", "")


# ─────────────────────────────────────────────────────────── 정책 문서
def vehicle_policy(acct: str) -> dict:
    """차량은 **자기 이름의** client_id 로만 붙고, **자기 토픽에만** 발행한다. 구독은 없다."""
    arn = f"arn:aws:iot:{REGION}:{acct}"
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "iot:Connect",
         "Resource": f"{arn}:client/${{iot:Connection.Thing.ThingName}}",
         "Condition": {"Bool": {"iot:Connection.Thing.IsAttached": "true"}}},
        {"Effect": "Allow", "Action": "iot:Publish",
         "Resource": f"{arn}:topic/ns/v1/${{iot:Connection.Thing.ThingName}}/curation/*"},
        {"Effect": "Allow", "Action": "iot:AssumeRoleWithCertificate", "Resource": f"{arn}:rolealias/{ALIAS}"}]}


def collector_policy(acct: str) -> dict:
    """수집기는 전 차량의 curation 토픽을 받는다. 발행 권한은 없다."""
    arn = f"arn:aws:iot:{REGION}:{acct}"
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "iot:Connect",
         "Resource": f"{arn}:client/${{iot:Connection.Thing.ThingName}}",
         "Condition": {"Bool": {"iot:Connection.Thing.IsAttached": "true"}}},
        {"Effect": "Allow", "Action": "iot:Subscribe", "Resource": f"{arn}:topicfilter/ns/v1/*"},
        {"Effect": "Allow", "Action": "iot:Receive", "Resource": f"{arn}:topic/ns/v1/*"}]}


def role_trust() -> dict:
    return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
            "Principal": {"Service": "credentials.iot.amazonaws.com"}}]}


def role_s3_policy(bucket: str) -> dict:
    """임시 자격은 자기 Thing 이름의 접두사에만 쓰고 읽는다 — 보고서 §2-6 의 S3 접두사 스코핑."""
    return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"],
            "Resource": f"arn:aws:s3:::{bucket}/vin=${{credentials-iot:ThingName}}/*"}]}


# ─────────────────────────────────────────────────────────── plan · up
def plan(acct: str) -> list[str]:
    bucket = f"ns-m4-{acct}-apne2"
    return ([f"DynamoDB 테이블 {table_spec()['TableName']} (PAY_PER_REQUEST · GSI1)",
             f"S3 버킷 {bucket} (공개 차단) — 접두사 격리 확인용 소량 객체만",
             f"IAM 역할 {ROLE} (신뢰: credentials.iot.amazonaws.com · 권한: {bucket}/vin=${{ThingName}}/*)",
             f"IoT role alias {ALIAS} → 위 역할 (임시 자격 15 분)",
             f"IoT 정책 {POLICY_VEHICLE} (자기 client_id · 자기 토픽 발행 · role alias)",
             f"IoT 정책 {POLICY_COLLECTOR} (ns/v1/* 구독·수신)"]
            + [f"IoT Thing {t} + X.509 인증서 (개인키 data/aws/certs/, 커밋 안 됨)" for t in VEHICLES + (COLLECTOR,)])


def up(s) -> dict:
    st = load_state()
    acct = s.client("sts").get_caller_identity()["Account"]
    iot, ddb, s3, iam = s.client("iot"), s.client("dynamodb"), s.client("s3"), s.client("iam")
    st.update(account=acct, region=REGION,
              iot_endpoint=iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"],
              cred_endpoint=iot.describe_endpoint(endpointType="iot:CredentialProvider")["endpointAddress"],
              bucket=f"ns-m4-{acct}-apne2", table=table_spec()["TableName"], certs=st.get("certs", {}))
    save_state(st)

    spec = table_spec()
    try:
        ddb.describe_table(TableName=spec["TableName"])
        print(f"  테이블 {spec['TableName']} 이미 있음")
    except ddb.exceptions.ResourceNotFoundException:
        ddb.create_table(**spec)
        ddb.get_waiter("table_exists").wait(TableName=spec["TableName"])
        print(f"  테이블 {spec['TableName']} 생성")

    try:
        s3.head_bucket(Bucket=st["bucket"])
        print(f"  버킷 {st['bucket']} 이미 있음")
    except Exception:
        s3.create_bucket(Bucket=st["bucket"], CreateBucketConfiguration={"LocationConstraint": REGION})
        s3.put_public_access_block(Bucket=st["bucket"], PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        print(f"  버킷 {st['bucket']} 생성 (공개 차단)")

    try:
        role_arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
        print(f"  역할 {ROLE} 이미 있음")
    except iam.exceptions.NoSuchEntityException:
        role_arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(role_trust()),
                                   Description="NeedSlack M4 - S3 temp credentials for vehicle certs (own vin prefix only)")["Role"]["Arn"]
        print(f"  역할 {ROLE} 생성")
    iam.put_role_policy(RoleName=ROLE, PolicyName="s3-own-prefix", PolicyDocument=json.dumps(role_s3_policy(st["bucket"])))
    st["role_arn"] = role_arn
    save_state(st)

    try:
        iot.describe_role_alias(roleAlias=ALIAS)
        print(f"  role alias {ALIAS} 이미 있음")
    except iot.exceptions.ResourceNotFoundException:
        for attempt in range(10):          # 막 만든 IAM 역할은 전파에 몇 초 걸린다
            try:
                iot.create_role_alias(roleAlias=ALIAS, roleArn=role_arn, credentialDurationSeconds=900)
                break
            except Exception as e:
                if attempt == 9 or err_code(e) not in ("InvalidRequestException", "UnauthorizedException"):
                    raise
                time.sleep(3)
        print(f"  role alias {ALIAS} 생성")

    for name, doc in ((POLICY_VEHICLE, vehicle_policy(acct)), (POLICY_COLLECTOR, collector_policy(acct))):
        try:
            iot.get_policy(policyName=name)
            print(f"  IoT 정책 {name} 이미 있음")
        except iot.exceptions.ResourceNotFoundException:
            iot.create_policy(policyName=name, policyDocument=json.dumps(doc))
            print(f"  IoT 정책 {name} 생성")

    os.makedirs(CERTS, exist_ok=True)
    for thing in VEHICLES + (COLLECTOR,):
        try:
            iot.describe_thing(thingName=thing)
        except iot.exceptions.ResourceNotFoundException:
            iot.create_thing(thingName=thing)
        cert_path, key_path = aws.m4_cert_paths(thing)
        if thing in st["certs"] and os.path.exists(cert_path):
            print(f"  Thing {thing} · 인증서 이미 있음")
            continue
        c = iot.create_keys_and_certificate(setAsActive=True)
        with open(cert_path, "w", encoding="utf-8") as f:
            f.write(c["certificatePem"])
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(c["keyPair"]["PrivateKey"])
        st["certs"][thing] = {"id": c["certificateId"], "arn": c["certificateArn"]}
        save_state(st)
        iot.attach_policy(policyName=POLICY_COLLECTOR if thing == COLLECTOR else POLICY_VEHICLE, target=c["certificateArn"])
        iot.attach_thing_principal(thingName=thing, principal=c["certificateArn"])
        print(f"  Thing {thing} · 인증서 {c['certificateId'][:12]}… 생성 · 정책 연결")
    return st


# ─────────────────────────────────────────────────────────── check
def _connect(client_id: str, cert_of: str, st: dict, timeout: float = 10.0):
    """cert_of 의 인증서로 client_id 를 내세워 붙는다. (클라이언트, 접속됐나, 끊김 기록) 을 돌려준다."""
    import paho.mqtt.client as mqtt
    state = {"connected": False, "disconnects": [], "got": []}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
    cert, key = aws.m4_cert_paths(cert_of)
    c.tls_set(ca_certs=aws.aws_ca_bundle(), certfile=cert, keyfile=key)
    c.on_connect = lambda cl, u, f, rc, p: state.__setitem__("connected", not rc.is_failure)
    c.on_disconnect = lambda cl, u, f, rc, p: state["disconnects"].append(str(rc))
    c.on_message = lambda cl, u, m: state["got"].append(m.topic)
    try:
        c.connect(st["iot_endpoint"], 8883, keepalive=30)
    except Exception as e:
        state["disconnects"].append(f"connect error {type(e).__name__}")
        return c, state
    c.loop_start()
    t0 = time.time()
    while time.time() - t0 < timeout and not state["connected"] and not state["disconnects"]:
        time.sleep(0.2)
    time.sleep(1.0)                       # IoT Core 는 정책 위반 client 를 접속 직후 끊는다
    return c, state


def _close(c) -> None:
    try:
        c.loop_stop()
        c.disconnect()
    except Exception:
        pass


def temp_credentials(st: dict, thing: str) -> dict:
    """Credential Provider — 차량 인증서(mTLS)로 role alias 의 임시 자격을 받는다. stdlib 만 쓴다."""
    ctx = ssl.create_default_context(cafile=aws.aws_ca_bundle())
    ctx.load_cert_chain(*aws.m4_cert_paths(thing))
    conn = http.client.HTTPSConnection(st["cred_endpoint"], 443, context=ctx, timeout=15)
    conn.request("GET", f"/role-aliases/{ALIAS}/credentials", headers={"x-amzn-iot-thingname": thing})
    r = conn.getresponse()
    body = r.read().decode()
    if r.status != 200:
        raise RuntimeError(f"Credential Provider {r.status}: {body[:200]}")
    return json.loads(body)["credentials"]


def check(s, st: dict) -> list[tuple[str, bool, str]]:
    out = []
    a, b = VEHICLES
    # 1. 수집기가 차량 토픽을 받는다 · 차량은 자기 토픽에 발행할 수 있다
    col, cs = _connect(COLLECTOR, COLLECTOR, st)
    col.subscribe("ns/v1/+/curation/#", qos=1)
    time.sleep(1.5)
    veh, vs = _connect(a, a, st)
    info = veh.publish(f"ns/v1/{a}/curation/probe", json.dumps({"probe": str(uuid.uuid4())}), qos=1)
    info.wait_for_publish(timeout=10)
    time.sleep(2.0)
    ok = info.is_published() and f"ns/v1/{a}/curation/probe" in cs["got"]
    out.append((f"{a} 가 자기 토픽에 발행 → 수집기가 받는다", ok, f"수신 {cs['got'][:1]}"))
    # 2. 남의 토픽에 발행하면 IoT Core 가 끊는다 (정책변수 격리)
    n0 = len(vs["disconnects"])
    veh.publish(f"ns/v1/{b}/curation/probe", "spoof", qos=1)
    time.sleep(4.0)
    ok = len(vs["disconnects"]) > n0 and f"ns/v1/{b}/curation/probe" not in cs["got"]
    out.append((f"{a} 가 {b} 토픽에 발행 → 거부(연결 끊김) · 수집기에 안 옴", ok, f"끊김 {vs['disconnects'][n0:][:1]}"))
    _close(veh)
    _close(col)
    # 3. 남의 이름(client_id)을 내세우면 접속이 거부된다
    spoof, ss = _connect(b, a, st)
    ok = not ss["connected"]
    out.append((f"{a} 인증서로 client_id={b} 접속 → 거부", ok, f"끊김 {ss['disconnects'][:1]}"))
    _close(spoof)
    # 4. 임시 자격은 자기 접두사에만 쓴다
    cred = temp_credentials(st, a)
    import boto3
    s3v = boto3.client("s3", region_name=REGION, aws_access_key_id=cred["accessKeyId"],
                       aws_secret_access_key=cred["secretAccessKey"], aws_session_token=cred["sessionToken"])
    s3v.put_object(Bucket=st["bucket"], Key=f"vin={a}/probe.txt", Body=b"own prefix")
    out.append((f"{a} 임시 자격으로 vin={a}/ 에 쓰기 → 허용", True, "PutObject 200"))
    try:
        s3v.put_object(Bucket=st["bucket"], Key=f"vin={b}/probe.txt", Body=b"other prefix")
        out.append((f"{a} 임시 자격으로 vin={b}/ 에 쓰기 → 거부", False, "허용됐다 ★"))
    except Exception as e:
        out.append((f"{a} 임시 자격으로 vin={b}/ 에 쓰기 → 거부", err_code(e) == "AccessDenied", err_code(e)))
    try:
        s3v.list_objects_v2(Bucket=st["bucket"])
        out.append(("임시 자격으로 버킷 목록 → 거부", False, "허용됐다 ★"))
    except Exception as e:
        out.append(("임시 자격으로 버킷 목록 → 거부", err_code(e) == "AccessDenied", err_code(e)))
    return out


# ─────────────────────────────────────────────────────────── run — 짧은 재생
def run_short(s, st: dict, limit: int = 3000) -> dict:
    """재생(IoT Core 로 발행) → 수집기(IoT Core 구독 · AWS DynamoDB) · 조정기(로컬 MinIO · AWS DynamoDB)."""
    import boto3
    local_s3 = aws.s3_client()
    try:
        local_s3.head_bucket(Bucket=LOCAL_BUCKET)
    except Exception:
        local_s3.create_bucket(Bucket=LOCAL_BUCKET)
    py = sys.executable
    base = dict(os.environ)
    logdir = os.path.join(aws.M4_DIR, "run")
    os.makedirs(logdir, exist_ok=True)
    col_env = dict(base, MQTT_TARGET="aws", DDB_TARGET="aws")
    rec_env = dict(base, MQTT_TARGET="local", DDB_TARGET="aws")
    rep_env = dict(base, MQTT_TARGET="aws", S3_BUCKET=LOCAL_BUCKET)
    col = subprocess.Popen([py, os.path.join(ROOT, "ingest", "collector.py"), "--client-id", COLLECTOR,
                            "--bucket", LOCAL_BUCKET], env=col_env,
                           stdout=open(os.path.join(logdir, "collector.log"), "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    time.sleep(5)
    t0 = time.time()
    rp = subprocess.run([py, os.path.join(ROOT, "edge-agent", "replay.py"), "DYN_B", "--limit", str(limit),
                         "--speed", "12.5"], env=rep_env, capture_output=True, text=True, encoding="utf-8")
    replay_out = rp.stdout + rp.stderr
    # 조정기는 재생이 끝난 뒤 버킷 대조로 번들을 처리한다 — 이 버킷에는 알림을 걸지 않았다
    rec = subprocess.Popen([py, os.path.join(ROOT, "ingest", "reconciler.py"), "--client-id", RECONCILER_ID,
                            "--bucket", LOCAL_BUCKET], env=rec_env,
                           stdout=open(os.path.join(logdir, "reconciler.log"), "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    import store as S
    st_aws = S.Store(S.DynamoTable(s.client("dynamodb"), st["table"]))
    accepted = next((int(l.split("수락")[1].split("·")[0].replace(",", "")) for l in replay_out.splitlines() if "수락" in l), None)
    last = None
    while time.time() - t0 < 900:
        time.sleep(10)
        counts = {fs.value: len(st_aws.frames_in_state("sim-DYN_B", fs)) for fs in
                  (V.FrameState.PENDING, V.FrameState.UPLOADED, V.FrameState.INDEXED)}
        if accepted and counts["INDEXED"] >= accepted and counts == last:
            break
        last = counts
    time.sleep(11)
    for p in (col, rec):
        p.terminate()
        p.wait(timeout=30)
    return {"replay": [l for l in replay_out.splitlines() if "후보" in l or "판정" in l or "도착지연" in l],
            "accepted": accepted, "aws_table": counts, "wall_s": round(time.time() - t0)}


# ─────────────────────────────────────────────────────────── down
def down(s, st: dict) -> list[str]:
    iot, ddb, s3, iam = s.client("iot"), s.client("dynamodb"), s.client("s3"), s.client("iam")
    log = []

    def quiet(f, *a, **k):
        try:
            f(*a, **k)
            return True
        except Exception as e:
            if err_code(e) in ("ResourceNotFoundException", "NoSuchEntity", "NoSuchBucket", "404", "NotFound"):
                return False
            raise

    for thing in VEHICLES + (COLLECTOR,):
        principals = []
        try:
            principals = iot.list_thing_principals(thingName=thing)["principals"]
        except iot.exceptions.ResourceNotFoundException:
            pass
        for arn in principals:
            quiet(iot.detach_thing_principal, thingName=thing, principal=arn)
            for pol in iot.list_attached_policies(target=arn)["policies"]:
                quiet(iot.detach_policy, policyName=pol["policyName"], target=arn)
            cid = arn.split("/")[-1]
            quiet(iot.update_certificate, certificateId=cid, newStatus="INACTIVE")
            for attempt in range(10):
                try:
                    iot.delete_certificate(certificateId=cid, forceDelete=True)
                    break
                except Exception as e:
                    if attempt == 9 or err_code(e) not in ("CertificateStateException", "DeleteConflictException"):
                        raise
                    time.sleep(2)
            log.append(f"인증서 {cid[:12]}… 삭제")
        if quiet(iot.delete_thing, thingName=thing):
            log.append(f"Thing {thing} 삭제")
    for name in (POLICY_VEHICLE, POLICY_COLLECTOR):
        if quiet(iot.delete_policy, policyName=name):
            log.append(f"IoT 정책 {name} 삭제")
    if quiet(iot.delete_role_alias, roleAlias=ALIAS):
        log.append(f"role alias {ALIAS} 삭제")
    quiet(iam.delete_role_policy, RoleName=ROLE, PolicyName="s3-own-prefix")
    if quiet(iam.delete_role, RoleName=ROLE):
        log.append(f"역할 {ROLE} 삭제")
    bucket = st.get("bucket")
    if bucket:
        try:
            for o in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=o["Key"])
            s3.delete_bucket(Bucket=bucket)
            log.append(f"버킷 {bucket} 비우고 삭제")
        except Exception as e:
            if err_code(e) not in ("NoSuchBucket", "404"):
                raise
    if quiet(ddb.delete_table, TableName=table_spec()["TableName"]):
        ddb.get_waiter("table_not_exists").wait(TableName=table_spec()["TableName"])
        log.append(f"테이블 {table_spec()['TableName']} 삭제")
    # 로컬 쪽 — 이 점검이 만든 MinIO 버킷과 Mosquitto 세션
    ls3 = aws.s3_client()
    try:
        for o in ls3.list_objects_v2(Bucket=LOCAL_BUCKET).get("Contents", []):
            ls3.delete_object(Bucket=LOCAL_BUCKET, Key=o["Key"])
        ls3.delete_bucket(Bucket=LOCAL_BUCKET)
        log.append(f"로컬 MinIO 버킷 {LOCAL_BUCKET} 삭제")
    except Exception:
        pass
    try:
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=RECONCILER_ID, clean_session=True)
        c.connect(os.environ.get("MQTT_HOST", "localhost"), int(os.environ.get("MQTT_PORT", "1883")))
        c.disconnect()
        log.append(f"로컬 Mosquitto 세션 {RECONCILER_ID} 정리")
    except Exception:
        pass
    for thing in VEHICLES + (COLLECTOR,):
        for p in aws.m4_cert_paths(thing):
            if os.path.exists(p):
                os.remove(p)
    if os.path.exists(STATE):
        os.remove(STATE)
    log.append("로컬 인증서 · 개인키 · m4.json 삭제")
    return log


def leftovers(s) -> dict:
    """남은 것이 없음을 목록으로 보인다."""
    iot, iam, ddb = s.client("iot"), s.client("iam"), s.client("dynamodb")
    out = {"things": [t["thingName"] for t in iot.list_things()["things"]],
           "iot_policies": [p["policyName"] for p in iot.list_policies()["policies"] if p["policyName"].startswith("ns-")],
           "role_aliases": [a for a in iot.list_role_aliases()["roleAliases"] if a.startswith("ns-")],
           "certificates": [c["certificateId"][:12] for c in iot.list_certificates()["certificates"]]}
    try:
        ddb.describe_table(TableName=table_spec()["TableName"])
        out["table"] = "남음 ★"
    except ddb.exceptions.ResourceNotFoundException:
        out["table"] = "없음"
    try:
        iam.get_role(RoleName=ROLE)
        out["role"] = "남음 ★"
    except iam.exceptions.NoSuchEntityException:
        out["role"] = "없음"
    return out


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd not in ("plan", "up", "check", "run", "down"):
        raise SystemExit("사용: python run.py m4 plan|up|check|run|down")
    s = aws.m4_session()
    me = s.client("sts").get_caller_identity()
    if me["Arn"].endswith(":root"):
        raise SystemExit("루트 계정 키다 — IAM 사용자 ns-m4-admin 의 키를 쓴다")
    print(f"AWS {me['Arn'].split(':')[-1]} · 리전 {REGION}")
    if cmd == "plan":
        print("만들 자원:")
        for line in plan(me["Account"]):
            print("  · " + line)
        return 0
    if cmd == "up":
        up(s)
        print(f"기록 {STATE}")
        return 0
    st = aws.m4_state() if cmd != "down" else load_state()
    if cmd == "check":
        res = check(s, st)
        for name, ok, why in res:
            print(f"  {'✓' if ok else '✗'} {name}   ({why})")
        return 0 if all(ok for _, ok, _ in res) else 1
    if cmd == "run":
        r = run_short(s, st)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["accepted"] and r["aws_table"]["INDEXED"] == r["accepted"] else 1
    for line in down(s, st):
        print("  " + line)
    print("남은 것:", json.dumps(leftovers(s), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
