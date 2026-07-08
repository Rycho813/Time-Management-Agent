import os
import sys
import requests
from dotenv import load_dotenv
from notion_read import read_notion_texts
from ollama_extract import extract_time_info
from notion_write import write_snapshot_and_summary_to_notion

load_dotenv()

FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")


def format_minutes(minutes: int) -> str:
    minutes = max(int(minutes), 0)
    hours = minutes // 60
    remaining_minutes = minutes % 60

    if hours and remaining_minutes:
        return f"{hours}h {remaining_minutes}min"
    if hours:
        return f"{hours}h"
    return f"{remaining_minutes}min"


def normalize_items(items: list[dict]) -> list[dict]:
    valid_items = []
    for item in items:
        try:
            event_name = str(item["event_name"]).strip()
            duration_min = int(item["duration_min"])
            time_type = str(item["time_type"]).strip()
        except (KeyError, TypeError, ValueError):
            continue

        if not event_name or duration_min <= 0:
            continue
        if time_type not in {"effective", "ineffective", "neutral"}:
            continue

        valid_items.append(
            {
                "event_name": event_name,
                "duration_min": duration_min,
                "time_type": time_type,
                "evidence": str(item.get("evidence", "")).strip(),
            }
        )
    return valid_items


def build_activity_snapshot(target_date: str, items: list[dict]) -> str:
    lines = [f"【Activity Snapshot｜{target_date}】"]

    if not items:
        lines.append("没有抽取到带有明确时长的活动。")
        return "\n".join(lines)

    label_map = {
        "effective": "有效",
        "ineffective": "无效",
        "neutral": "中性",
    }

    for item in items:
        label = label_map[item["time_type"]]
        duration = format_minutes(item["duration_min"])
        lines.append(f"- [{label}] {item['event_name']}：{duration}")

    return "\n".join(lines)


def build_summary(items: list[dict]) -> str:
    effective_min = sum(
        item["duration_min"]
        for item in items
        if item["time_type"] == "effective"
    )
    return f"有效利用时间：{format_minutes(effective_min)}"


def send_feishu_text(text: str) -> None:
    if not FEISHU_WEBHOOK_URL:
        print("未配置 FEISHU_WEBHOOK_URL，跳过飞书发送")
        return

    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }

    response = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=20)
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

    print(f"\n准备读取 {target_date} 的 Notion 页面正文...\n")
    texts = read_notion_texts(target_date)
    if not texts:
        raise RuntimeError(f"{target_date} 的页面正文为空，无法生成 Snapshot 和 Summary")

    print(f"准备用 AI 抽取 {target_date} 的活动和时长...\n")
    extraction_result = extract_time_info(texts)
    items = normalize_items(extraction_result.get("items", []))

    snapshot = build_activity_snapshot(target_date, items)
    summary = build_summary(items)

    print(f"\n{target_date} 的 Activity Snapshot：\n")
    print(snapshot)
    print(f"\n{target_date} 的 Summary：\n")
    print(summary)

    print(f"\n准备写入 {target_date} 的 Notion Activity Snapshot 和 Summary...\n")
    write_snapshot_and_summary_to_notion(target_date, snapshot, summary)

    print(f"\n准备发送 {target_date} 的报告到飞书...\n")
    send_feishu_text(f"【时间管理报告｜{target_date}】\n{summary}\n\n{snapshot}")


if __name__ == "__main__":
    main()
