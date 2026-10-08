import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

# 导入http_util时会统一加载项目根目录的.env。
from util.config import (
    DB_POOL_RECYCLE_SECONDS,
    POSITION_ACTIVE_COLUMN,
    POSITION_ACCESS_PREFLIGHT_LIMIT,
    POSITION_CHECK_BATCH_SIZE,
    POSITION_CHECK_CONCURRENCY,
    POSITION_CHECK_REQUEST_INTERVAL_SECONDS,
    POSITION_COMPANY_DETAIL_COLUMN,
    POSITION_ID_COLUMN,
    POSITION_TABLE_NAME,
)
from util.http_util import async_request_with_proxy


BASE_URL = "https://ggfw.hrss.gd.gov.cn/recruitment/internet/main/"
POSITION_DETAIL_URL = (
    BASE_URL
    + "internet/r/c/webpage/homepage/position/detail/{bcb009}"
)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Referer": BASE_URL,
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
}

OFFLINE_KEYWORDS = (
    "岗位不存在",
    "职位不存在",
    "岗位已下架",
    "职位已下架",
    "岗位已失效",
    "职位已失效",
    "招聘已结束",
    "已停止招聘",
)
AUTH_KEYWORDS = (
    "未登录",
    "登录失效",
    "登录过期",
    "token失效",
    "token过期",
    "unauthorized",
    "无权访问",
    "认证失败",
)

CheckStatus = Literal["online", "offline", "unknown", "auth_error"]


@dataclass(frozen=True)
class PositionTarget:
    bcb009: str
    bbb911: str


@dataclass(frozen=True)
class PositionCheckResult:
    target: PositionTarget
    status: CheckStatus
    reason: str


def validate_identifier(value: str, label: str) -> str:
    """允许英文或中文表名/字段名，并阻止SQL标识符注入。"""
    pattern = r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*"
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{label}不是合法的MySQL标识符：{value!r}")
    return value


POSITION_TABLE_NAME = validate_identifier(
    POSITION_TABLE_NAME,
    "POSITION_TABLE_NAME",
)
POSITION_ID_COLUMN = validate_identifier(
    POSITION_ID_COLUMN,
    "POSITION_ID_COLUMN",
)
POSITION_COMPANY_DETAIL_COLUMN = validate_identifier(
    POSITION_COMPANY_DETAIL_COLUMN,
    "POSITION_COMPANY_DETAIL_COLUMN",
)
POSITION_ACTIVE_COLUMN = validate_identifier(
    POSITION_ACTIVE_COLUMN,
    "POSITION_ACTIVE_COLUMN",
)


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f".env中缺少配置：{name}")
    return value


def create_database_engine() -> Engine:
    url = URL.create(
        drivername="mysql+pymysql",
        username=require_env("DB_MYSQL_TEST_11_USER"),
        password=require_env("DB_MYSQL_TEST_11_PASSWORD"),
        host=require_env("DB_MYSQL_TEST_11_HOST"),
        port=int(require_env("DB_MYSQL_TEST_11_PORT")),
        database=require_env("DB_MYSQL_TEST_11_DATABASE"),
        query={"charset": "utf8mb4"},
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=DB_POOL_RECYCLE_SECONDS,
    )


def request_headers() -> dict[str, str]:
    """公开岗位详情接口不发送Authorization或登录Cookie。"""
    return dict(HEADERS)


def ensure_active_column(engine: Engine) -> None:
    """不存在时添加在招状态列，并为定时扫描添加索引。"""
    database_name = require_env("DB_MYSQL_TEST_11_DATABASE")
    check_sql = text(
        """
        SELECT COUNT(*)
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = :database_name
          AND TABLE_NAME = :table_name
          AND COLUMN_NAME = :column_name
        """
    )

    with engine.begin() as connection:
        exists = connection.execute(
            check_sql,
            {
                "database_name": database_name,
                "table_name": POSITION_TABLE_NAME,
                "column_name": POSITION_ACTIVE_COLUMN,
            },
        ).scalar_one()

        if not exists:
            connection.execute(
                text(
                    f"""
                    ALTER TABLE `{POSITION_TABLE_NAME}`
                    ADD COLUMN `{POSITION_ACTIVE_COLUMN}` TINYINT(1)
                    NOT NULL DEFAULT 1 COMMENT '岗位状态：1在招，0下架'
                    """
                )
            )

        index_exists = connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = :database_name
                  AND TABLE_NAME = :table_name
                  AND INDEX_NAME = 'idx_is_active'
                """
            ),
            {
                "database_name": database_name,
                "table_name": POSITION_TABLE_NAME,
            },
        ).scalar_one()
        if not index_exists:
            connection.execute(
                text(
                    f"""
                    ALTER TABLE `{POSITION_TABLE_NAME}`
                    ADD KEY `idx_is_active` (`{POSITION_ACTIVE_COLUMN}`)
                    """
                )
            )

def load_active_positions(engine: Engine) -> list[PositionTarget]:
    sql = text(
        f"""
        SELECT
            CAST(`{POSITION_ID_COLUMN}` AS CHAR) AS bcb009,
            CAST(`{POSITION_COMPANY_DETAIL_COLUMN}` AS CHAR) AS bbb911
        FROM `{POSITION_TABLE_NAME}`
        WHERE `{POSITION_ACTIVE_COLUMN}` = 1
          AND `{POSITION_ID_COLUMN}` IS NOT NULL
          AND TRIM(CAST(`{POSITION_ID_COLUMN}` AS CHAR)) <> ''
        ORDER BY `{POSITION_ID_COLUMN}` DESC
        """
    )

    with engine.connect() as connection:
        rows = connection.execute(sql).mappings().all()

    unique: dict[str, PositionTarget] = {}
    for row in rows:
        bcb009 = str(row["bcb009"]).strip()
        bbb911 = str(row.get("bbb911") or "").strip()
        unique.setdefault(
            bcb009,
            PositionTarget(bcb009=bcb009, bbb911=bbb911),
        )
    return list(unique.values())


def payload_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str).lower()


def classify_payload(
    target: PositionTarget,
    payload: dict[str, Any],
) -> PositionCheckResult:
    content = payload_text(payload)
    if any(keyword.lower() in content for keyword in AUTH_KEYWORDS):
        return PositionCheckResult(
            target,
            "auth_error",
            "公开接口返回访问限制或登录提示",
        )

    if any(keyword.lower() in content for keyword in OFFLINE_KEYWORDS):
        return PositionCheckResult(target, "offline", "接口明确返回岗位已失效")

    appcode = str(payload.get("appcode"))
    success = payload.get("success")
    data = payload.get("data")

    if appcode == "200" and success is not False:
        if isinstance(data, dict):
            returned_id = str(data.get("bcb009") or "").strip()
            if returned_id == target.bcb009:
                return PositionCheckResult(target, "online", "岗位详情访问正常")
            if returned_id:
                return PositionCheckResult(
                    target,
                    "unknown",
                    f"接口返回了其他岗位ID：{returned_id}",
                )
            if success is True:
                return PositionCheckResult(
                    target,
                    "offline",
                    "接口成功，但返回的岗位ID为空",
                )

        if success is True and not data:
            return PositionCheckResult(target, "offline", "接口成功但岗位详情为空")

    return PositionCheckResult(
        target,
        "unknown",
        f"未识别业务状态：appcode={appcode}, success={success}",
    )


def unwrap_retry_exception(exc: Exception) -> Exception:
    """取出 tenacity RetryError 中最后一次真实请求异常。"""
    last_attempt = getattr(exc, "last_attempt", None)
    exception_getter = getattr(last_attempt, "exception", None)
    if callable(exception_getter):
        nested = exception_getter()
        if isinstance(nested, Exception):
            return nested
    return exc


async def check_position(
    target: PositionTarget,
    headers: dict[str, str],
    semaphore: asyncio.Semaphore,
) -> PositionCheckResult:
    async with semaphore:
        try:
            response = await async_request_with_proxy(
                method="GET",
                url=POSITION_DETAIL_URL.format(bcb009=target.bcb009),
                headers=headers,
            )
        except Exception as exc:
            root_exc = unwrap_retry_exception(exc)
            if isinstance(root_exc, httpx.HTTPStatusError):
                status_code = root_exc.response.status_code
                if status_code in {401, 403}:
                    return PositionCheckResult(
                        target,
                        "auth_error",
                        f"HTTP {status_code}，公开接口访问被拒绝",
                    )
                if status_code in {404, 410}:
                    return PositionCheckResult(
                        target,
                        "offline",
                        f"HTTP {status_code}，岗位详情不存在",
                    )
                return PositionCheckResult(
                    target,
                    "unknown",
                    f"HTTP {status_code}，不作为下架依据",
                )
            return PositionCheckResult(
                target,
                "unknown",
                f"网络或接口异常，不作为下架依据：{root_exc}",
            )

        else:
            try:
                payload = response.json()
            except ValueError:
                return PositionCheckResult(
                    target,
                    "unknown",
                    "响应不是JSON，不作为下架依据",
                )

            if not isinstance(payload, dict):
                return PositionCheckResult(
                    target,
                    "unknown",
                    "JSON根节点不是对象，不作为下架依据",
                )
            return classify_payload(target, payload)
        finally:
            if POSITION_CHECK_REQUEST_INTERVAL_SECONDS > 0:
                await asyncio.sleep(POSITION_CHECK_REQUEST_INTERVAL_SECONDS)


async def verify_public_endpoint(
    targets: list[PositionTarget],
    headers: dict[str, str],
) -> None:
    """抽样确认公开接口可访问且至少存在一个在线岗位。"""
    semaphore = asyncio.Semaphore(1)
    results: list[PositionCheckResult] = []

    for target in targets[:POSITION_ACCESS_PREFLIGHT_LIMIT]:
        result = await check_position(target, headers, semaphore)
        results.append(result)
        if result.status == "auth_error":
            raise RuntimeError(f"公开岗位详情接口访问失败：{result.reason}")
        if result.status == "online":
            print(f"公开岗位详情接口验证通过，样本岗位：{target.bcb009}")
            return

    reasons = "；".join(result.reason for result in results)
    raise RuntimeError(
        "抽样岗位均无法确认在线，为避免误判，本次不更新数据库：" + reasons
    )


def mark_positions_offline(
    engine: Engine,
    position_ids: list[str],
) -> None:
    if not position_ids:
        return

    sql = text(
        f"""
        UPDATE `{POSITION_TABLE_NAME}`
        SET `{POSITION_ACTIVE_COLUMN}` = 0
        WHERE `{POSITION_ID_COLUMN}` = :bcb009
          AND `{POSITION_ACTIVE_COLUMN}` = 1
        """
    )
    with engine.begin() as connection:
        connection.execute(
            sql,
            [{"bcb009": position_id} for position_id in position_ids],
        )


async def crawl_position_status(engine: Engine) -> None:
    targets = load_active_positions(engine)
    print(f"数据库中待检查的在招岗位数：{len(targets)}")
    if not targets:
        return

    headers = request_headers()
    await verify_public_endpoint(targets, headers)
    semaphore = asyncio.Semaphore(POSITION_CHECK_CONCURRENCY)

    online_count = 0
    offline_count = 0
    unknown_count = 0

    for start in range(0, len(targets), POSITION_CHECK_BATCH_SIZE):
        batch = targets[start : start + POSITION_CHECK_BATCH_SIZE]
        results = await asyncio.gather(
            *(check_position(target, headers, semaphore) for target in batch)
        )

        access_errors = [
            result for result in results if result.status == "auth_error"
        ]
        if access_errors:
            raise RuntimeError(
                "检查过程中公开接口访问被拒绝，本批次及后续批次均未更新："
                + access_errors[0].reason
            )

        offline_ids = [
            result.target.bcb009
            for result in results
            if result.status == "offline"
        ]
        mark_positions_offline(engine, offline_ids)

        online_count += sum(result.status == "online" for result in results)
        offline_count += len(offline_ids)
        unknown_count += sum(result.status == "unknown" for result in results)

        for result in results:
            if result.status in {"offline", "unknown"}:
                company_text = (
                    f"，企业详情ID={result.target.bbb911}"
                    if result.target.bbb911
                    else ""
                )
                print(
                    f"岗位{result.target.bcb009}{company_text}："
                    f"{result.status}，{result.reason}"
                )

        print(
            f"已检查 {min(start + len(batch), len(targets))}/{len(targets)}，"
            f"在线 {online_count}，下架 {offline_count}，未知 {unknown_count}"
        )

    print(
        f"岗位状态检查完成：在线 {online_count}，"
        f"下架 {offline_count}，未知 {unknown_count}"
    )


async def main() -> None:
    engine = create_database_engine()
    try:
        ensure_active_column(engine)
        await crawl_position_status(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
