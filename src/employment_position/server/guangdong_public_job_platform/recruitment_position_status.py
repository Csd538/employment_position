import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.engine import Engine

from employment_position.util.config import (
    POSITION_CHECK_BATCH_SIZE,
    POSITION_CHECK_CONCURRENCY,
    POSITION_CHECK_REQUEST_INTERVAL_SECONDS,
)
from employment_position.util.database import (
    PositionTarget,
    create_database_engine,
    ensure_active_column,
    load_active_positions,
    mark_positions_offline,
)
from employment_position.util.http_util import async_request_with_proxy
from employment_position.util.logger import setup_logger

NAME = "recruitment_position_status"
logger = setup_logger(system="guangdong_public_job_platform", stage=NAME)


BASE_URL = "https://ggfw.hrss.gd.gov.cn/recruitment/internet/main/"
POSITION_DETAIL_URL = (
    BASE_URL + "internet/r/c/webpage/homepage/position/detail/{bcb009}"
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

CheckStatus = Literal["online", "offline", "unknown"]


@dataclass(frozen=True)
class PositionCheckResult:
    target: PositionTarget
    status: CheckStatus
    reason: str



def classify_payload(
    target: PositionTarget,
    payload: dict[str, Any],
) -> PositionCheckResult:
    data = payload.get("data")
    appcode = str(payload.get("appcode") or "")

    if appcode == "200" and isinstance(data, dict):
        response_position_id = str(data.get("bcb009") or "").strip()
        if response_position_id == target.bcb009:
            return PositionCheckResult(
                target, "online", "岗位详情访问正常"
            )
        if not response_position_id:
            return PositionCheckResult(
                target, "offline", "岗位详情未返回职位ID，判定已下架"
            )

    if appcode == "10" and not data:
        return PositionCheckResult(
            target,
            "offline",
            str(payload.get("msg") or "岗位详情不存在"),
        )

    return PositionCheckResult(
        target,
        "unknown",
        f"未识别业务状态：appcode={appcode}",
    )


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
            return classify_payload(target, response.json())
        except Exception as exc:
            return PositionCheckResult(
                target,
                "unknown",
                f"\u8bf7\u6c42\u5931\u8d25\uff0c\u4e0d\u66f4\u65b0\u5c97\u4f4d\u72b6\u6001\uff1a{exc}",
            )
        finally:
            if POSITION_CHECK_REQUEST_INTERVAL_SECONDS > 0:
                await asyncio.sleep(POSITION_CHECK_REQUEST_INTERVAL_SECONDS)


async def crawl_position_status(engine: Engine) -> None:
    targets = load_active_positions(engine)
    logger.info(f"[{NAME}] 数据库中待检查的在招岗位数：{len(targets)}")
    if not targets:
        return

    headers = HEADERS
    semaphore = asyncio.Semaphore(POSITION_CHECK_CONCURRENCY)

    online_count = 0
    offline_count = 0
    unknown_count = 0

    for start in range(0, len(targets), POSITION_CHECK_BATCH_SIZE):
        batch = targets[start : start + POSITION_CHECK_BATCH_SIZE]
        logger.info(
            f"[{NAME}] 开始检查第 {start + 1}-"
            f"{start + len(batch)} 条，"
            f"职位ID {batch[0].bcb009} -> {batch[-1].bcb009}"
        )
        results = await asyncio.gather(
            *(check_position(target, headers, semaphore) for target in batch)
        )

        offline_ids = [
            result.target.bcb009 for result in results if result.status == "offline"
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
                message = (
                    f"[{NAME}] 岗位{result.target.bcb009}{company_text}："
                    f"{result.status}，{result.reason}"
                )
                if result.status == "unknown":
                    logger.warning(message)
                else:
                    logger.info(message)

        logger.info(
            f"[{NAME}] 已检查 {min(start + len(batch), len(targets))}/{len(targets)}，"
            f"在线 {online_count}，下架 {offline_count}，未知 {unknown_count}",
        )

    logger.info(
        f"[{NAME}] 岗位状态检查完成：在线 {online_count}，"
        f"下架 {offline_count}，未知 {unknown_count}",
    )


async def main() -> None:
    engine = create_database_engine()
    try:
        ensure_active_column(engine)
        await crawl_position_status(engine)
    except Exception as exc:
        logger.error(f"[{NAME}] 岗位状态检查失败：{exc}", exc_info=True)
        raise
    finally:
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
