"""从正式库查询企业类型，填充测试库企业表的企业类型字段。"""

import re
from typing import Any

from employment_position.util.config import (
    COMPANY_TYPE_QUERY_BATCH_SIZE,
    COMPANY_TYPE_UPDATE_BATCH_SIZE,
    CREDIT_CODE_PATTERN,
)
from employment_position.util.database import (
    create_database_engine,
    create_source_database_engine,
    load_companies_without_type,
    lookup_company_types_by_credit_code,
    lookup_company_types_by_name,
    update_company_types,
)
from employment_position.util.logger import setup_logger

NAME = "fill_company_type"
logger = setup_logger(system="guangdong_public_job_platform", stage=NAME)

CREDIT_CODE_RE = re.compile(CREDIT_CODE_PATTERN)


def split_by_credit_code(
    companies: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把企业拆成按名称关联和按信用代码关联两组。"""
    by_name: list[dict[str, Any]] = []
    by_credit_code: list[dict[str, Any]] = []
    for company in companies:
        if CREDIT_CODE_RE.match(company["company_name"]):
            by_credit_code.append(company)
        else:
            by_name.append(company)
    return by_name, by_credit_code


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    """把列表按固定大小切分。"""
    return [items[start : start + size] for start in range(0, len(items), size)]


def run() -> None:
    engine = create_database_engine()
    source_engine = create_source_database_engine()
    try:
        companies = load_companies_without_type(engine)
        logger.info(f"[{NAME}] 企业表中待填充企业类型的记录数：{len(companies)}")
        if not companies:
            return

        by_name, by_credit_code = split_by_credit_code(companies)
        logger.info(
            f"[{NAME}] 按名称关联 {len(by_name)} 家，"
            f"按信用代码关联 {len(by_credit_code)} 家"
        )

        type_by_name: dict[str, str] = {}
        for batch in chunked(
            [company["company_name"] for company in by_name],
            COMPANY_TYPE_QUERY_BATCH_SIZE,
        ):
            type_by_name.update(
                lookup_company_types_by_name(source_engine, batch)
            )

        type_by_credit_code: dict[str, str] = {}
        for batch in chunked(
            [company["company_name"] for company in by_credit_code],
            COMPANY_TYPE_QUERY_BATCH_SIZE,
        ):
            type_by_credit_code.update(
                lookup_company_types_by_credit_code(source_engine, batch)
            )

        updates: list[dict[str, Any]] = []
        for company in by_name:
            company_type = type_by_name.get(company["company_name"])
            if company_type:
                updates.append(
                    {
                        "id": company["id"],
                        "company_type": company_type,
                    }
                )
        for company in by_credit_code:
            company_type = type_by_credit_code.get(company["company_name"])
            if company_type:
                updates.append(
                    {
                        "id": company["id"],
                        "company_type": company_type,
                    }
                )

        logger.info(f"[{NAME}] 正式库命中并取得企业类型的记录数：{len(updates)}")
        if not updates:
            logger.info(f"[{NAME}] 没有可写入的企业类型，任务结束")
            return

        written = 0
        for batch in chunked(updates, COMPANY_TYPE_UPDATE_BATCH_SIZE):
            written += update_company_types(engine, batch)

        logger.info(f"[{NAME}] 企业类型写入完成：{written} 条")
        logger.info(
            f"[{NAME}] 未命中记录数：{len(companies) - len(updates)}"
            f"（正式库未收录该企业，保留为空）"
        )
    finally:
        engine.dispose()
        source_engine.dispose()


if __name__ == "__main__":
    run()
