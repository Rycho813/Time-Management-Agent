import os
import sys
import requests
from dotenv import load_dotenv
from notion_read import read_notion_texts
from ollama_extract import extract_time_info
from notion_write import write_summary_to_notion
from notion_sync import sync_day_from_source_page, write_summary_to_notion

load_dotenv()

FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")


def format_minutes(minutes: int) -> str:
    hours = minutes // 60
    remaining_minutes = minutes % 60

    if hours and remaining_minutes:
        return f"{hours}h {remaining_minutes}min"
    if hours:
        return f"{hours}h"
    return f"{remaining_minutes}min"


def build_report(target_date: str, items: list[dict]) -> str:
    effective_min = sum(
        item["duration_min"]
        for item in items
        if item["time_type"] == "effective"
    )

    ineffective_min = sum(
        item["duration_min"]
        for item in items
        if item["time_type"] == "ineffective"
    )

    neutral_min = sum(
        item["duration_min"]
        for item in items
        if item["time_type"] == "neutral"
    )

    total_min = effective_min + ineffective_min + neutral_min

    lines = [
        f"【时间利用报告｜{target_date}】",
        "",
        f"有效学习时间：{format_minutes(effective_min)}",
        f"无效时间：{format_minutes(ineffective_min)}",
        f"中性时间：{format_minutes(neutral_min)}",
        f"总记录时间：{format_minutes(total_min)}",
        "",
        "事件明细：",
    ]

    if not items:
        lines.append("- 没有抽取到带有明确时长的事件")
        return "\n".join(lines)

    for item in items:
        label = {
            "effective": "有效",
            "ineffective": "无效",
            "neutral": "中性",
        }.get(item["time_type"], item["time_type"])

        event_name = item["event_name"]
        duration = format_minutes(item["duration_min"])

        lines.append(f"- [{label}] {event_name}：{duration}")

    return "\n".join(lines)


def send_feishu_text(text: str) -> None:
    if not FEISHU_WEBHOOK_URL:
        raise RuntimeError("缺少 FEISHU_WEBHOOK_URL，请检查 .env")

    payload = {
        "msg_type": "text",
        "content": {
            "text": text
        }
    }

    response = requests.post(
        FEISHU_WEBHOOK_URL,
        json=payload,
        timeout=20,
    )

    if response.status_code != 200:
        print("飞书发送失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    print("飞书发送成功")


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python feishu_report.py 2026-07-02")
        raise SystemExit(1)

    target_date = sys.argv[1]

    print(f"\n准备从日程页面同步 {target_date} 的原始记录到 Notion 数据库...\n")
    sync_day_from_source_page(target_date)

    texts = read_notion_texts(target_date)
    extraction_result = extract_time_info(texts)
    items = extraction_result.get("items", [])

    report = build_report(target_date, items)
    print(f"\n{target_date} 的时间利用报告如下：\n")
    print(report)

    print(f"\n准备发送 {target_date} 的时间利用报告到飞书...\n")
    try:
        send_feishu_text(report)
    except Exception as e:
        print("飞书发送失败：", e)

    print(f"\n准备写入 {target_date} 的 Notion summary...\n")
    try:
        write_summary_to_notion(target_date, report)
    except Exception as e:
        print("Notion summary 写入失败：", e)


if __name__ == "__main__":
    main()