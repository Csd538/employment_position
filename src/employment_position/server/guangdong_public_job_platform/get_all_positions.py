import asyncio
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.engine import Engine

from employment_position.util.config import (
    POSITION_BATCH_INTERVAL_SECONDS,
    POSITION_BATCH_SIZE,
    POSITION_DETAIL_BATCH_SIZE,
    POSITION_DETAIL_CONCURRENCY,
    POSITION_DETAIL_REQUEST_INTERVAL_SECONDS,
    POSITION_MAX_PAGES,
    POSITION_PAGE_SIZE,
    POSITION_PROVINCE_CONCURRENCY,
    PROVINCES,
)
from employment_position.util.database import (
    create_database_engine,
    ensure_position_link_column,
    upsert_positions,
)
from employment_position.util.http_util import async_request_with_proxy
from employment_position.util.logger import setup_logger

NAME = "get_all_positions"
logger = setup_logger(system="guangdong_public_job_platform", stage=NAME)


URL = (
    "https://ggfw.hrss.gd.gov.cn/recruitment/internet/main/"
    "internet/retrieval/c/recruitment/homepage/positions"
)
POSITION_PAGE_URL = (
    "https://ggfw.hrss.gd.gov.cn/recruitment/internet/main/#/positionDetail"
)
POSITION_DETAIL_URL = (
    "https://ggfw.hrss.gd.gov.cn/recruitment/internet/main/"
    "internet/r/c/webpage/homepage/position/detail/{position_id}"
)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Origin": "https://ggfw.hrss.gd.gov.cn",
    "Referer": "https://ggfw.hrss.gd.gov.cn/recruitment/internet/main/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua": ('"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"'),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

DETAIL_HEADERS = {
    key: value
    for key, value in HEADERS.items()
    if key not in {"Content-Type", "Origin"}
}

# 输出字段：主要岗位字段以及后续入库、核验需要的业务标识。
OUTPUT_FIELDS = [
    ("岗位名称", "bce055"),
    ("企业", "aab004"),
    ("行业", "aab022Name"),
    ("地区", "acb204Name"),
    ("技能要求", "aac015Name"),
    ("岗位要求", "_position_requirements"),
    ("薪资范围", "bcca68"),
    ("发布时间", "bdb286"),
    ("职位ID", "bcb009"),
    ("企业ID", "aab001"),
    ("企业详情ID", "bbb911"),
    ("link", "_detail_link"),
    ("学历要求", "aac011Name"),
    ("工作经验要求", "aae162Name"),
    ("工作性质", "acb239Name"),
    ("招聘人数", "acb240"),
    ("福利待遇", "bcb182Name"),
    ("信息来源", "bze433Name"),
    ("信息来源代码", "bze433"),
    ("是否在招", "_is_active"),
]


def build_request_data(
    page: int,
    province_code: str,
    province_name: str,
) -> dict[str, Any]:
    """构造岗位列表接口的请求参数。"""
    return {
        "bce055": "",
        "acb241": "",
        "acb242": "",
        "aab056": "",
        "lately": -1,
        "bze433": "",
        "aac015": "",
        "aac011": "",
        "aae162": "",
        "acb239": "",
        "acb204": province_code,
        "acb204Name": province_name,
        "gzxz": "0",
        "pageTag": "01",
        "current": page,
        "size": POSITION_PAGE_SIZE,
        "orderType": "01",
    }


async def get_positions(
    page: int,
    province_code: str,
    province_name: str,
) -> tuple[int, list[dict[str, Any]]]:
    """获取一页岗位数据，并返回页码和记录列表。"""
    response = await async_request_with_proxy(
        method="POST",
        url=URL,
        headers=HEADERS,
        json=build_request_data(page, province_code, province_name),
    )

    payload = response.json()
    if not payload.get("success"):
        error = payload.get("msg") or payload.get("appcode")
        raise RuntimeError(f"{province_name}第 {page} 页抓取失败：{error}")
    return page, payload["data"]["records"]


def extract_position(record: dict[str, Any]) -> dict[str, Any]:
    """从原始记录中提取需要入库的字段。"""
    result = {
        output_name: record.get(source_name)
        for output_name, source_name in OUTPUT_FIELDS
    }

    # 刚从在招岗位列表抓取到的数据，初始化为1；后续入库后由定时任务更新。
    result["是否在招"] = 1
    result["岗位要求"] = ""

    company_detail_id = str(result["企业详情ID"] or "").strip()
    position_id = str(result["职位ID"] or "").strip()
    result["link"] = (
        POSITION_PAGE_URL
        + "?"
        + urlencode(
            {
                "bbb911": company_detail_id,
                "bcb009": position_id,
            }
        )
        if company_detail_id and position_id
        else ""
    )

    # 招聘人数转为整数。
    try:
        result["招聘人数"] = int(result["招聘人数"] or 0)
    except (TypeError, ValueError):
        result["招聘人数"] = 0

    return result


async def crawl_positions(
    province_code: str,
    province_name: str,
) -> tuple[list[dict[str, Any]], int]:
    """分批并发抓取岗位列表，遇到空页或不足一页时停止。"""
    unique_positions: dict[str, dict[str, Any]] = {}
    raw_count = 0

    for start_page in range(1, POSITION_MAX_PAGES + 1, POSITION_BATCH_SIZE):
        page_numbers = list(
            range(
                start_page,
                min(start_page + POSITION_BATCH_SIZE, POSITION_MAX_PAGES + 1),
            )
        )
        outcomes = await asyncio.gather(
            *(
                get_positions(page, province_code, province_name)
                for page in page_numbers
            ),
            return_exceptions=True,
        )

        page_records: dict[int, list[dict[str, Any]]] = {}
        failed_pages: list[str] = []
        for page, outcome in zip(page_numbers, outcomes):
            if isinstance(outcome, BaseException):
                failed_pages.append(f"第{page}页：{outcome}")
            else:
                page_records[outcome[0]] = outcome[1]

        if failed_pages:
            logger.warning(f"[{NAME}] {province_name}本批跳过失败页：{failed_pages}")
        if not page_records:
            raise RuntimeError(
                f"{province_name}第 {page_numbers[0]}-{page_numbers[-1]} 页全部抓取失败"
            )

        terminal_page = None
        for page, records in sorted(page_records.items()):
            raw_count += len(records)
            for record in records:
                unique_positions[str(record["bcb009"])] = extract_position(record)
            if terminal_page is None and len(records) < POSITION_PAGE_SIZE:
                terminal_page = page

        logger.info(
            f"[{NAME}] {province_name}已完成"
            f"第 {page_numbers[0]}-{page_numbers[-1]} 页，"
            f"原始记录 {raw_count} 条，去重后 {len(unique_positions)} 条"
        )

        if terminal_page is not None:
            logger.info(
                f"[{NAME}] {province_name}第 {terminal_page} 页已到末页，停止翻页"
            )
            break

        if POSITION_BATCH_INTERVAL_SECONDS > 0:
            await asyncio.sleep(POSITION_BATCH_INTERVAL_SECONDS)
    else:
        raise RuntimeError(f"已达到安全页数上限 {POSITION_MAX_PAGES}")

    return list(unique_positions.values()), raw_count


async def get_position_requirements(
    position_id: str,
    semaphore: asyncio.Semaphore,
) -> str:
    """获取一个岗位的岗位要求。"""
    async with semaphore:
        try:
            response = await async_request_with_proxy(
                method="GET",
                url=POSITION_DETAIL_URL.format(position_id=position_id),
                headers=DETAIL_HEADERS,
            )
            payload = response.json()
            if not payload.get("success"):
                error = payload.get("msg") or payload.get("appcode")
                raise RuntimeError(error)
            return str(payload["data"].get("acb22a") or "").strip()
        finally:
            if POSITION_DETAIL_REQUEST_INTERVAL_SECONDS > 0:
                await asyncio.sleep(POSITION_DETAIL_REQUEST_INTERVAL_SECONDS)


async def enrich_position_requirements(
    positions: list[dict[str, Any]],
    province_name: str,
) -> None:
    """分批并发补充岗位要求。"""
    targets = [position for position in positions if position.get("职位ID")]
    semaphore = asyncio.Semaphore(POSITION_DETAIL_CONCURRENCY)
    success_count = 0
    empty_count = 0
    failure_count = 0

    for start in range(0, len(targets), POSITION_DETAIL_BATCH_SIZE):
        batch = targets[start : start + POSITION_DETAIL_BATCH_SIZE]
        outcomes = await asyncio.gather(
            *(
                get_position_requirements(str(position["职位ID"]), semaphore)
                for position in batch
            ),
            return_exceptions=True,
        )

        for position, outcome in zip(batch, outcomes):
            if isinstance(outcome, BaseException):
                failure_count += 1
                logger.warning(
                    f"[{NAME}] 岗位{position['职位ID']}要求获取失败：{outcome}"
                )
                continue

            position["岗位要求"] = outcome
            if outcome:
                success_count += 1
            else:
                empty_count += 1

        logger.info(
            f"[{NAME}] {province_name}岗位要求已处理 "
            f"{min(start + len(batch), len(targets))}/{len(targets)}，"
            f"有内容 {success_count}，空值 {empty_count}，失败 {failure_count}"
        )


async def process_province(
    index: int,
    province_name: str,
    province_code: str,
    semaphore: asyncio.Semaphore,
    engine: Engine,
) -> tuple[
    tuple[str, int, int, int] | None,
    tuple[str, str] | None,
]:
    async with semaphore:
        logger.info(
            f"[{NAME}] [{index}/{len(PROVINCES)}] 开始抓取："
            f"{province_name}（{province_code}）"
        )
        try:
            positions, raw_count = await crawl_positions(
                province_code,
                province_name,
            )
            await enrich_position_requirements(positions, province_name)
            logger.info(
                f"[{NAME}] {province_name} 岗位详情补充完成，开始写入数据库，"
                f"共 {len(positions)} 条"
            )
            written_count = await asyncio.to_thread(
                upsert_positions,
                engine,
                positions,
            )
            vacancy_count = sum(
                int(position.get("招聘人数") or 0) for position in positions
            )

            logger.info(f"[{NAME}] {province_name}原始记录数：{raw_count}")
            logger.info(f"[{NAME}] {province_name}去重后职位数：{len(positions)}")
            logger.info(f"[{NAME}] {province_name}在招岗位数：{vacancy_count}")
            logger.info(
                f"[{NAME}] {province_name}数据库已处理（新增或更新）："
                f"{written_count} 条"
            )
            return (
                province_name,
                raw_count,
                len(positions),
                vacancy_count,
            ), None
        except Exception as exc:
            logger.exception(f"[{NAME}] {province_name}抓取或入库失败，已跳过：{exc}")
            return None, (province_name, str(exc))


async def main() -> None:
    engine = create_database_engine()
    try:
        await asyncio.to_thread(ensure_position_link_column, engine)
        semaphore = asyncio.Semaphore(POSITION_PROVINCE_CONCURRENCY)
        results = await asyncio.gather(
            *(
                process_province(
                    index,
                    province_name,
                    province_code,
                    semaphore,
                    engine,
                )
                for index, (province_name, province_code) in enumerate(PROVINCES, 1)
            )
        )
    finally:
        engine.dispose()

    successes = [success for success, _ in results if success is not None]
    failures = [failure for _, failure in results if failure is not None]

    logger.info(
        f"[{NAME}] 全部省份处理完成：成功 {len(successes)} 个，失败 {len(failures)} 个"
    )
    logger.info(f"[{NAME}] 成功抓取职位总数：{sum(item[2] for item in successes)}")
    logger.info(f"[{NAME}] 成功抓取在招岗位总数：{sum(item[3] for item in successes)}")

    if failures:
        logger.warning(f"[{NAME}] 失败省份：{failures}")


if __name__ == "__main__":
    asyncio.run(main())
