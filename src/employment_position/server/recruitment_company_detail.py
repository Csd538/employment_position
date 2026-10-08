import asyncio
import os
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

# 导入http_util时会统一加载项目根目录的.env。
from util.config import (
    COMPANY_ACCESS_PREFLIGHT_LIMIT,
    COMPANY_BATCH_SIZE,
    COMPANY_CONCURRENCY,
    COMPANY_REFRESH_DAYS,
    COMPANY_REQUEST_INTERVAL_SECONDS,
    COMPANY_TABLE_NAME,
    DB_POOL_RECYCLE_SECONDS,
    POSITION_COMPANY_DETAIL_COLUMN,
    POSITION_COMPANY_ID_COLUMN,
    POSITION_COMPANY_NAME_COLUMN,
    POSITION_INFORMATION_SOURCE_COLUMN,
    POSITION_SOURCE_NAME_COLUMN,
    POSITION_TABLE_NAME,
)
from util.http_util import async_request_with_proxy


BASE_URL = "https://ggfw.hrss.gd.gov.cn/recruitment/internet/main/"
COMPANY_DETAIL_URL = (
    BASE_URL
    + "internet/r/c/webpage/homepage/unit/detail/{bbb911}"
)
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

def validate_identifier(value: str, label: str) -> str:
    """校验表名，避免环境变量中的非法字符进入SQL。"""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"{label}不是合法的MySQL表名：{value!r}")
    return value


POSITION_TABLE_NAME = validate_identifier(
    POSITION_TABLE_NAME,
    "POSITION_TABLE_NAME",
)
COMPANY_TABLE_NAME = validate_identifier(
    COMPANY_TABLE_NAME,
    "COMPANY_TABLE_NAME",
)
POSITION_COMPANY_DETAIL_COLUMN = validate_identifier(
    POSITION_COMPANY_DETAIL_COLUMN,
    "POSITION_COMPANY_DETAIL_COLUMN",
)
POSITION_COMPANY_ID_COLUMN = validate_identifier(
    POSITION_COMPANY_ID_COLUMN,
    "POSITION_COMPANY_ID_COLUMN",
)
POSITION_COMPANY_NAME_COLUMN = validate_identifier(
    POSITION_COMPANY_NAME_COLUMN,
    "POSITION_COMPANY_NAME_COLUMN",
)
POSITION_INFORMATION_SOURCE_COLUMN = validate_identifier(
    POSITION_INFORMATION_SOURCE_COLUMN,
    "POSITION_INFORMATION_SOURCE_COLUMN",
)
POSITION_SOURCE_NAME_COLUMN = validate_identifier(
    POSITION_SOURCE_NAME_COLUMN,
    "POSITION_SOURCE_NAME_COLUMN",
)


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
        re.sub(r"\s+", " ", line).strip()
        for line in "".join(parser.parts).splitlines()
    ]
    return "\n".join(line for line in lines if line)


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f".env中缺少数据库配置：{name}")
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


def ensure_company_table(engine: Engine) -> None:
    sql = f"""
    CREATE TABLE IF NOT EXISTS `{COMPANY_TABLE_NAME}` (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '数据库自增主键',
        bbb911 VARCHAR(64) NOT NULL COMMENT '企业详情ID',
        aab001 VARCHAR(64) NULL COMMENT '企业业务编号',
        bze433 VARCHAR(20) NULL COMMENT '信息来源代码',
        source_name VARCHAR(255) NULL COMMENT '信息来源名称',
        company_name VARCHAR(255) NULL COMMENT '企业名称',
        company_intro_html LONGTEXT NULL COMMENT '企业介绍HTML，来源字段bab271',
        company_intro_text LONGTEXT NULL COMMENT '去除HTML后的企业介绍',
        contact_phone VARCHAR(255) NULL COMMENT '联系电话，来源字段aae005',
        detail_url VARCHAR(1000) NULL COMMENT '企业详情页地址',
        crawl_time DATETIME NULL COMMENT '最近成功抓取时间',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_bbb911 (bbb911),
        KEY idx_aab001 (aab001),
        KEY idx_company_name (company_name),
        KEY idx_bze433 (bze433)
    ) ENGINE=InnoDB
      DEFAULT CHARSET=utf8mb4
      COLLATE=utf8mb4_general_ci
      COMMENT='招聘企业详情信息表'
    """
    with engine.begin() as connection:
        connection.execute(text(sql))


def load_company_targets(engine: Engine) -> list[dict[str, str]]:
    """读取岗位表中的企业ID，只抓缺失、字段不全或已到刷新时间的企业。"""
    cutoff = datetime.now() - timedelta(days=COMPANY_REFRESH_DAYS)
    refresh_condition = ""
    parameters: dict[str, Any] = {}

    if COMPANY_REFRESH_DAYS > 0:
        refresh_condition = " OR c.crawl_time IS NULL OR c.crawl_time < :cutoff"
        parameters["cutoff"] = cutoff
    else:
        refresh_condition = " OR c.bbb911 IS NOT NULL"

    sql = text(
        f"""
        SELECT DISTINCT
            CAST(p.`{POSITION_COMPANY_DETAIL_COLUMN}` AS CHAR) AS bbb911,
            CAST(p.`{POSITION_INFORMATION_SOURCE_COLUMN}` AS CHAR) AS bze433,
            CAST(p.`{POSITION_COMPANY_ID_COLUMN}` AS CHAR) AS aab001,
            p.`{POSITION_COMPANY_NAME_COLUMN}` AS company_name,
            p.`{POSITION_SOURCE_NAME_COLUMN}` AS source_name
        FROM `{POSITION_TABLE_NAME}` AS p
        LEFT JOIN `{COMPANY_TABLE_NAME}` AS c
            ON c.bbb911 = p.`{POSITION_COMPANY_DETAIL_COLUMN}`
                COLLATE utf8mb4_general_ci
        WHERE p.`{POSITION_COMPANY_DETAIL_COLUMN}` IS NOT NULL
          AND TRIM(CAST(p.`{POSITION_COMPANY_DETAIL_COLUMN}` AS CHAR)) <> ''
          AND (
              c.bbb911 IS NULL
              OR c.company_intro_html IS NULL
              OR c.company_intro_html = ''
              {refresh_condition}
          )
        ORDER BY bbb911
        """
    )

    with engine.connect() as connection:
        rows = connection.execute(sql, parameters).mappings().all()

    # API只使用bbb911；即使岗位表中来源代码不同，也只请求一次。
    unique: dict[str, dict[str, str]] = {}
    for row in rows:
        bbb911 = str(row["bbb911"]).strip()
        candidate = {
            "bbb911": bbb911,
            "bze433": str(row.get("bze433") or "").strip(),
            "aab001": str(row.get("aab001") or "").strip(),
            "company_name": str(row.get("company_name") or "").strip(),
            "source_name": str(row.get("source_name") or "").strip(),
        }
        target = unique.setdefault(
            bbb911,
            candidate.copy(),
        )
        for field in ("bze433", "aab001", "company_name", "source_name"):
            if not target[field] and candidate[field]:
                target[field] = candidate[field]

    return list(unique.values())


def get_data(payload: dict[str, Any], label: str) -> dict[str, Any]:
    if str(payload.get("appcode")) != "200" or payload.get("success") is False:
        raise RuntimeError(
            f"{label}业务状态异常："
            f"appcode={payload.get('appcode')}, success={payload.get('success')}"
        )

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"{label}返回的data不是对象")
    return data


async def get_company_data(bbb911: str) -> dict[str, Any]:
    response = await async_request_with_proxy(
        method="GET",
        url=COMPANY_DETAIL_URL.format(bbb911=bbb911),
        headers=HEADERS,
    )
    return get_data(response.json(), f"企业{bbb911}详情接口")


async def check_company_access(
    sample_bbb911_values: list[str],
) -> bool:
    """运行时验证公开企业详情接口是否可以匿名访问。"""
    errors: list[str] = []

    for bbb911 in sample_bbb911_values[:COMPANY_ACCESS_PREFLIGHT_LIMIT]:
        try:
            await get_company_data(bbb911)
        except Exception as exc:
            errors.append(f"{bbb911}：{exc}")
            if COMPANY_REQUEST_INTERVAL_SECONDS > 0:
                await asyncio.sleep(COMPANY_REQUEST_INTERVAL_SECONDS)
            continue

        print("公开企业详情接口访问成功，可获取企业介绍")
        return True

    print(
        "公开企业详情接口抽样访问均失败，本次停止抓取："
        + "；".join(errors)
    )
    return False


async def get_company_detail(
    target: dict[str, str],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    bbb911 = target["bbb911"]
    bze433 = target["bze433"]

    async with semaphore:
        try:
            detail_data = await get_company_data(bbb911)
            intro_html = str(detail_data.get("bab271") or "")

            detail_url = COMPANY_PAGE_URL + "?" + urlencode(
                {"bbb911": bbb911, "bze433": bze433}
            )

            return {
                "bbb911": bbb911,
                "aab001": detail_data.get("aab001") or target["aab001"],
                "bze433": bze433 or detail_data.get("bze433"),
                "source_name": (
                    detail_data.get("bze433Name") or target["source_name"]
                ),
                "company_name": (
                    detail_data.get("aab004") or target["company_name"]
                ),
                "company_intro_html": intro_html,
                "company_intro_text": html_to_text(intro_html),
                "detail_url": detail_url,
                "crawl_time": datetime.now(),
            }
        finally:
            if COMPANY_REQUEST_INTERVAL_SECONDS > 0:
                await asyncio.sleep(COMPANY_REQUEST_INTERVAL_SECONDS)


def upsert_companies(
    engine: Engine,
    companies: list[dict[str, Any]],
) -> None:
    if not companies:
        return

    sql = text(
        f"""
        INSERT INTO `{COMPANY_TABLE_NAME}` (
            bbb911,
            aab001,
            bze433,
            source_name,
            company_name,
            company_intro_html,
            company_intro_text,
            detail_url,
            crawl_time
        ) VALUES (
            :bbb911,
            :aab001,
            :bze433,
            :source_name,
            :company_name,
            :company_intro_html,
            :company_intro_text,
            :detail_url,
            :crawl_time
        )
        ON DUPLICATE KEY UPDATE
            aab001 = COALESCE(NULLIF(VALUES(aab001), ''), aab001),
            bze433 = COALESCE(NULLIF(VALUES(bze433), ''), bze433),
            source_name = COALESCE(NULLIF(VALUES(source_name), ''), source_name),
            company_name = COALESCE(NULLIF(VALUES(company_name), ''), company_name),
            company_intro_html = COALESCE(
                NULLIF(VALUES(company_intro_html), ''),
                company_intro_html
            ),
            company_intro_text = COALESCE(
                NULLIF(VALUES(company_intro_text), ''),
                company_intro_text
            ),
            detail_url = VALUES(detail_url),
            crawl_time = VALUES(crawl_time)
        """
    )

    # 每批使用独立事务，失败会自动回滚，不会污染后续批次。
    with engine.begin() as connection:
        connection.execute(sql, companies)


async def crawl_company_details(engine: Engine) -> None:
    targets = load_company_targets(engine)
    print(f"数据库中待抓取企业数：{len(targets)}")
    if not targets:
        return

    access_available = await check_company_access(
        [target["bbb911"] for target in targets],
    )
    if not access_available:
        raise RuntimeError(
            "公开企业详情接口预检失败，请检查企业详情ID、网络或接口状态"
        )

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
                print(f"企业{target['bbb911']}抓取失败：{outcome}")
                continue
            companies.append(outcome)

        upsert_companies(engine, companies)
        success_count += len(companies)
        print(
            f"已处理 {min(start + len(batch), len(targets))}/{len(targets)}，"
            f"成功 {success_count}，失败 {failure_count}"
        )

    print(
        f"企业详情抓取完成：成功 {success_count}，失败 {failure_count}"
    )


async def main() -> None:
    engine = create_database_engine()
    try:
        ensure_company_table(engine)
        await crawl_company_details(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
