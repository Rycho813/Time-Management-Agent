# migrate_and_summary.py

import os
import argparse
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from notion_migration import (
    migrate_single_day,
    collect_hierarchical_texts,
    NOTION_SNAPSHOT_PROP,
    NOTION_SUMMARY_PROP,
)
from notion_write import update_page_rich_text_properties
from ollama_extract import extract_time_info
from time_summary import (
    normalize_items,
    build_activity_snapshot,
    build_summary,
    build_feishu_report,
)
from feishu_report import send_feishu_text

load_dotenv()

APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Singapore")

def iter_date_range(start: str, end: str) -> list[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)

    if start_date > end_date:
        raise ValueError("--start 不能晚于 --end")

    result = []
    current = start_date

    while current <= end_date:
        result.append(current.isoformat())
        current += timedelta(days=1)

    return result


def resolve_target_dates(args) -> list[str]:
    if args.start or args.end:
        if not args.start or not args.end:
            raise ValueError("--start 和 --end 必须同时填写")
        return iter_date_range(args.start, args.end)

    if args.date:
        return [args.date]

    return [get_today_str()]


def get_today_str() -> str:
    return datetime.now(ZoneInfo(APP_TIMEZONE)).date().isoformat()


def validate_sleep_hours(sleep_hours: float) -> int:
    sleep_minutes = int(round(sleep_hours * 60))

    if sleep_minutes < 0 or sleep_minutes >= 24 * 60:
        raise ValueError("--sleep-hours 必须大于等于 0 且小于 24")

    return sleep_minutes


def generate_snapshot_and_summary(
    target_date: str,
    page_id: str,
    source_block_id: str,
    sleep_minutes: int,
) -> tuple[str, str]:
    print(f"\n准备读取 {target_date} 的层级日程文本...\n")
    hierarchical_texts = collect_hierarchical_texts(source_block_id)

    if not hierarchical_texts:
        raise RuntimeError(f"{target_date} 没有可读取的层级文本，无法生成 AI 总结")

    print(f"准备用 AI 抽取 {target_date} 的活动和时长...\n")
    extraction_result = extract_time_info(hierarchical_texts)
    items = normalize_items(extraction_result.get("items", []))

    snapshot = build_activity_snapshot(target_date, items)
    summary = build_summary(target_date, items, sleep_minutes)

    print(f"\n准备写入 {target_date} 的 Activity Snapshot 和 summary...\n")
    update_page_rich_text_properties(
        page_id,
        {
            NOTION_SNAPSHOT_PROP: snapshot,
            NOTION_SUMMARY_PROP: summary,
        },
    )

    print(f"已写入 AI 总结：{target_date}")
    return snapshot, summary


def run_daily_workflow(
    target_date: str,
    overwrite_body: bool,
    clear_snapshot: bool,
    clear_summary: bool,
    sleep_hours: float,
    send_feishu: bool,
    dry_run: bool,
) -> None:
    sleep_minutes = validate_sleep_hours(sleep_hours)

    print(f"\n开始执行 {target_date} 的日程迁移与总结流程")
    print(f"当前时区：{APP_TIMEZONE}")
    print(f"睡觉时间：{sleep_hours}h")

    page_id, source_block_id = migrate_single_day(
        target_date=target_date,
        overwrite_body=overwrite_body,
        clear_snapshot=clear_snapshot,
        clear_summary=clear_summary,
        dry_run=dry_run,
    )

    if not page_id or not source_block_id:
        print(f"\n{target_date} 没有完成迁移，跳过 AI 总结和飞书发送")
        return

    if dry_run:
        print(f"\n[DRY RUN] 跳过 AI 总结、Notion 写入和飞书发送：{target_date}")
        return

    snapshot, summary = generate_snapshot_and_summary(
        target_date=target_date,
        page_id=page_id,
        source_block_id=source_block_id,
        sleep_minutes=sleep_minutes,
    )

    report = build_feishu_report(target_date, snapshot, summary)

    if send_feishu:
        print(f"\n准备发送 {target_date} 的报告到飞书...\n")
        send_feishu_text(report)
    else:
        print(f"\n已关闭飞书发送，仅完成 Notion 迁移与 AI 总结：{target_date}")

    print(f"\n执行完成：{target_date}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="每天自动迁移当天日程；也支持按日期范围批量迁移、总结、发送飞书。"
    )

    parser.add_argument(
        "--date",
        default=None,
        help="目标日期，例如 2026-07-08；不填则默认使用今天",
    )
    parser.add_argument("--start", default=None, help="批量开始日期，例如 2026-07-01")
    parser.add_argument("--end", default=None, help="批量结束日期，例如 2026-07-08")
    parser.add_argument(
        "--overwrite-body",
        action="store_true",
        help="如果目标页面正文已有内容，先清空再重建折叠层级",
    )
    parser.add_argument(
        "--clear-snapshot",
        action="store_true",
        help="迁移时先清空 Activity Snapshot 属性列",
    )
    parser.add_argument(
        "--clear-summary",
        action="store_true",
        help="迁移时先清空 summary 属性列",
    )
    parser.add_argument(
        "--sleep-hours",
        type=float,
        default=7.0,
        help="每天睡觉时间，默认 7 小时；例如 6.5 表示 6h30min",
    )
    parser.add_argument(
        "--no-feishu",
        action="store_true",
        help="只写入 Notion，不发送飞书",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览，不实际写入 Notion，不调用 AI，不发送飞书",
    )

    args = parser.parse_args()
    target_dates = resolve_target_dates(args)

    for target_date in target_dates:
        run_daily_workflow(
            target_date=target_date,
            overwrite_body=args.overwrite_body,
            clear_snapshot=args.clear_snapshot,
            clear_summary=args.clear_summary,
            sleep_hours=args.sleep_hours,
            send_feishu=not args.no_feishu,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()