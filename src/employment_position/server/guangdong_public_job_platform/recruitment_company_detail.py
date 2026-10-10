import asyncio
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.engine import Engine

from employment_position.util.config import (
    COMPANY_BATCH_SIZE,
    COMPANY_CONCURRENCY,
    COMPANY_REQUEST_INTERVAL_SECONDS,
)
from employment_position.util.database import (
    create_database_engine,
    ensure_company_table,
    load_company_targets,
    upsert_companies,
)
from employment_position.util.http_util import async_request_with_proxy
from employment_position.util.logger import setup_logger

NAME = "recruitment_company_detail"
logger = setup_logger(system="guangdong_public_job_platform", stage=NAME)


BASE_URL = "https://ggfw.hrss.gd.gov.cn/recruitment/internet/main/"
COMPANY_DETAIL_URL = BASE_URL + "internet/r/c/webpage/homepage/unit/detail/{bbb911}"
COMPANY_PAGE_URL = BASE_URL + "#/companyDetail"

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


class _TextExtractor(HTMLParser):
    """使用标准库把企业介绍HTML转换为纯文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() in {"script", "style"}:
            self._skip_depth += 1
        elif tag.lower() in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag.lower() in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""

    parser = _TextExtractor()
    parser.feed(html)
    lines = [
        re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines()
    ]
    return "\n".join(line for line in lines if line)



async def get_company_data(company_detail_id: str) -> dict[str, Any]:
    response = await async_request_with_proxy(
        method="GET",
        url=COMPANY_DETAIL_URL.format(bbb911=company_detail_id),
        headers=HEADERS,
    )
    payload = response.json()
    if not payload.get("success"):
        error = payload.get("msg") or payload.get("appcode")
        raise RuntimeError(f"企业{company_detail_id}详情获取失败：{error}")
    return payload["data"]


async def get_company_detail(
    target: dict[str, str],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    company_detail_id = target["company_detail_id"]
    information_source_id = target["information_source_id"]

    async with semaphore:
        try:
            detail_data = await get_company_data(company_detail_id)
            intro_html = str(detail_data.get("bab271") or "")
            detail_url = (
                COMPANY_PAGE_URL
                + "?"
                + urlencode(
                    {
                        "bbb911": company_detail_id,
                        "bze433": information_source_id,
                    }
                )
            )

            return {
                "company_detail_id": company_detail_id,
                "company_id": detail_data.get("aab001") or target["company_id"],
                "information_source_id": (
                    information_source_id or detail_data.get("bze433")
                ),
                "source_name": (detail_data.get("bze433Name") or target["source_name"]),
                "company_name": (detail_data.get("aab004") or target["company_name"]),
                "company_intro_html": intro_html,
                "company_intro_text": html_to_text(intro_html),
                "contact_phone": detail_data.get("aae005"),
                "detail_url": detail_url,
                "crawl_time": datetime.now(),
            }
        finally:
            if COMPANY_REQUEST_INTERVAL_SECONDS > 0:
                await asyncio.sleep(COMPANY_REQUEST_INTERVAL_SECONDS)


async def crawl_company_details(engine: Engine) -> None:
    targets = load_company_targets(engine)
    logger.info(f"[{NAME}] 数据库中待抓取企业数：{len(targets)}")
    if not targets:
        return

    semaphore = asyncio.Semaphore(COMPANY_CONCURRENCY)
    success_count = 0
    failure_count = 0

    for start in range(0, len(targets), COMPANY_BATCH_SIZE):
        batch = targets[start : start + COMPANY_BATCH_SIZE]
        outcomes = await asyncio.gather(
            *(
                get_company_detail(
                    target,
                    semaphore,
                )
                for target in batch
            ),
            return_exceptions=True,
        )

        companies: list[dict[str, Any]] = []
        for target, outcome in zip(batch, outcomes):
            if isinstance(outcome, BaseException):
                failure_count += 1
                logger.warning(
                    f"[{NAME}] 企业{target['company_detail_id']}抓取失败：{outcome}"
                )
                continue
            companies.append(outcome)

        upsert_companies(engine, companies)
        success_count += len(companies)
        logger.info(
            f"[{NAME}] 已处理 {min(start + len(batch), len(targets))}/{len(targets)}，"
            f"成功 {success_count}，失败 {failure_count}",
        )

    logger.info(
        f"[{NAME}] 企业详情抓取完成：成功 {success_count}，失败 {failure_count}",
    )


async def main() -> None:
    engine = create_database_engine()
    try:
        ensure_company_table(engine)
        await crawl_company_details(engine)
    except Exception as exc:
        logger.error(f"[{NAME}] 企业详情抓取失败：{exc}", exc_info=True)
        raise
    finally:
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
