# -*- coding: utf-8 -*-
"""01_create.py — 버킷·테이블·PG 스키마를 만든다. **멱등이어야 한다.**

두 번 돌려도 같은 결과여야 한다 (구현설계_MVP.md §5).

    python infra/bootstrap/01_create.py

필요 의존성: boto3, psycopg  (스택이 떠 있어야 한다)
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "schema"))


def env(k, default=None):
    v = os.environ.get(k, default)
    if v is None:
        sys.exit(f"환경변수 {k} 가 없다. .env.example 을 .env 로 복사해 채워라.")
    return v


def create_bucket():
    import boto3
    from botocore.exceptions import ClientError
    s3 = boto3.client(
        "s3", endpoint_url=env("AWS_ENDPOINT_S3"),
        aws_access_key_id=env("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=env("AWS_SECRET_ACCESS_KEY"),
        region_name=env("AWS_REGION", "ap-northeast-2"))
    name = env("S3_BUCKET", "ns-raw-payload")
    try:
        s3.head_bucket(Bucket=name)
        print(f"  버킷 {name} 이미 있음")
    except ClientError:
        s3.create_bucket(Bucket=name)
        print(f"  버킷 {name} 생성")


def create_table():
    import boto3
    from botocore.exceptions import ClientError
    ddb = boto3.client(
        "dynamodb", endpoint_url=env("AWS_ENDPOINT_DYNAMODB"),
        aws_access_key_id=env("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=env("AWS_SECRET_ACCESS_KEY"),
        region_name=env("AWS_REGION", "ap-northeast-2"))
    spec = json.load(open(os.path.join(ROOT, "schema", "ddb_table.json"),
                          encoding="utf-8"))
    spec = {k: v for k, v in spec.items() if not k.startswith("_")}
    try:
        ddb.describe_table(TableName=spec["TableName"])
        print(f"  테이블 {spec['TableName']} 이미 있음")
    except ClientError:
        ddb.create_table(**spec)
        ddb.get_waiter("table_exists").wait(TableName=spec["TableName"])
        print(f"  테이블 {spec['TableName']} 생성")


def apply_pg():
    import psycopg
    sql = open(os.path.join(ROOT, "schema", "pg", "001_init.sql"),
               encoding="utf-8").read()
    with psycopg.connect(env("PG_DSN")) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.campaign')")
            if cur.fetchone()[0]:
                print("  PG 스키마 이미 있음")
                return
            cur.execute(sql)
        conn.commit()
    print("  PG 스키마 적용 (001_init.sql)")


if __name__ == "__main__":
    print("부트스트랩 시작 (멱등)")
    create_bucket()
    create_table()
    apply_pg()
    print("완료")
