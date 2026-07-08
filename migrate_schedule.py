import os
import re
import json
import argparse
import requests
from datetime import date
from dotenv import load_dotenv

from notion_read import (
    notion_headers,
    get_block_children,
    get_block_plain_text,
    collect_text_recursive,
    query_database_pages_by_date,
    NOTION_DATABASE_ID,
    NOTION_DATE_PROP,
)
from notion_write import update_page_rich_text_properties

load_dotenv()

NOTION_SOURCE_PAGE_ID_RAW = os.getenv("NOTION_SOURCE_PAGE_ID", "")
NOTION_NAME_PROP = os.getenv("NOTION_NAME_PROP", "Name")
NOTION_SNAPSHOT_PROP = os.getenv("NOTION_SNAPSHOT_PROP", "Activity Snapshot")
NOTION_SUMMARY_PROP = os.getenv("NOTION_SUMMARY_PROP", "summary")
MIGRATION_YEAR = int(os.getenv("MIGRATION_YEAR", "2026"))

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")

FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")

DATE_LABEL_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\s*$")


TIME_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_name": {"type": "string"},
                    "duration_min": {"type": "integer"},
                    "time_type": {
                        "type": "string",
                        "enum": ["effective", "buffer", "ignored"],
                    },
                    "evidence": {"type": "string"},
                },
                "required": ["event_name", "duration_min", "time_type", "evidence"],
            },
        }
    },
    "required": ["items"],
}


def normalize_notion_id(raw: str, env_name: str) -> str:
    raw = raw.strip()
    if not raw:
        raise RuntimeError(f"缺少 {env_name}，请检查 .env")
    candidates = re.findall(r"[0-9a-fA-F]{32}", raw.replace("-", ""))
    if not candidates:
        raise RuntimeError(f"{env_name} 格式不对：请填写 Notion 页面链接或 32 位 ID")
    return candidates[-1]


NOTION_SOURCE_PAGE_ID = normalize_notion_id(
    NOTION_SOURCE_PAGE_ID_RAW,
    "NOTION_SOURCE_PAGE_ID",
)


def format_minutes(minutes: int) -> str:
    minutes = max(int(minutes), 0)
    hours = minutes // 60
    remaining = minutes % 60
    if hours and remaining:
        return f"{hours}h {remaining}min"
    if hours:
        return f"{hours}h"
    return f"{remaining}min"


def format_percent(numerator_min: int, denominator_min: int) -> str:
    if denominator_min <= 0:
        return "0.0%"
    return f"{numerator_min / denominator_min * 100:.1f}%"


def date_label_to_iso(label: str, year: int) -> str | None:
    match = DATE_LABEL_RE.match(label)
    if not match:
        return None
    month = int(match.group(1))
    day = int(match.group(2))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def is_date_label(text: str) -> bool:
    return DATE_LABEL_RE.match(text.strip()) is not None


def find_source_date_blocks(year: int) -> list[dict]:
    results = []
    for block in get_block_children(NOTION_SOURCE_PAGE_ID):
        label = get_block_plain_text(block)
        target_date = date_label_to_iso(label, year)
        if not target_date:
            continue
        results.append(
            {
                "date": target_date,
                "label": label,
                "block_id": block["id"],
                "block": block,
            }
        )
    results.sort(key=lambda x: x["date"])
    return results


def find_database_page_id_by_date(target_date: str) -> str | None:
    pages = query_database_pages_by_date(target_date)
    if not pages:
        return None
    if len(pages) > 1:
        print(f"警告：{target_date} 找到多条数据库记录，只使用第一条")
    return pages[0]["id"]


def create_database_page(target_date: str) -> str:
    if not NOTION_DATABASE_ID:
        raise RuntimeError("缺少 NOTION_DATABASE_ID，请检查 .env")

    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            NOTION_NAME_PROP: {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": f"{target_date}时间记录"},
                    }
                ]
            },
            NOTION_DATE_PROP: {
                "date": {"start": target_date}
            },
        },
    }

    response = requests.post(
        url,
        headers=notion_headers(),
        json=payload,
        timeout=20,
    )

    if response.status_code != 200:
        print("Notion 数据库行创建失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    return response.json()["id"]


def split_text_rich_text(
    content: str,
    annotations: dict | None = None,
    link: dict | None = None,
) -> list[dict]:
    content = content or ""
    chunks = [content[i:i + 1900] for i in range(0, len(content), 1900)] or [""]
    result = []

    for chunk in chunks:
        item = {
            "type": "text",
            "text": {"content": chunk},
        }
        if link:
            item["text"]["link"] = link
        if annotations:
            item["annotations"] = annotations
        result.append(item)

    return result


def copy_rich_text(rich_text_items: list[dict]) -> list[dict]:
    copied = []

    for item in rich_text_items or []:
        item_type = item.get("type")
        annotations = item.get("annotations")
        plain_text = item.get("plain_text", "")

        if item_type == "text":
            text_obj = item.get("text", {})
            content = text_obj.get("content", plain_text)
            link = text_obj.get("link")
            copied.extend(split_text_rich_text(content, annotations, link))
        elif item_type == "equation":
            expression = item.get("equation", {}).get("expression", "")
            if expression:
                copied.append(
                    {
                        "type": "equation",
                        "equation": {"expression": expression},
                    }
                )
        else:
            if plain_text:
                copied.extend(split_text_rich_text(plain_text, annotations))

    return copied


def copy_color(source_body: dict) -> str:
    return source_body.get("color", "default")


def source_block_to_create_payload(source_block: dict) -> dict:
    block_type = source_block.get("type")
    source_body = source_block.get(block_type, {})

    if block_type in {"paragraph", "quote"}:
        if source_block.get("has_children"):
            text = get_block_plain_text(source_block) or "[empty]"
            return {
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": split_text_rich_text(text),
                    "color": "default",
                },
            }

        return {
            "object": "block",
            "type": block_type,
            block_type: {
                "rich_text": copy_rich_text(source_body.get("rich_text", [])),
                "color": copy_color(source_body),
            },
        }

    if block_type in {"heading_1", "heading_2", "heading_3", "heading_4"}:
        payload_type = block_type if block_type != "heading_4" else "heading_3"
        payload_body = {
            "rich_text": copy_rich_text(source_body.get("rich_text", [])),
            "color": copy_color(source_body),
        }

        if "is_toggleable" in source_body:
            payload_body["is_toggleable"] = bool(source_body.get("is_toggleable"))

        return {
            "object": "block",
            "type": payload_type,
            payload_type: payload_body,
        }

    if block_type in {"bulleted_list_item", "numbered_list_item", "toggle"}:
        return {
            "object": "block",
            "type": block_type,
            block_type: {
                "rich_text": copy_rich_text(source_body.get("rich_text", [])),
                "color": copy_color(source_body),
            },
        }

    if block_type == "to_do":
        return {
            "object": "block",
            "type": "to_do",
            "to_do": {
                "rich_text": copy_rich_text(source_body.get("rich_text", [])),
                "checked": bool(source_body.get("checked", False)),
                "color": copy_color(source_body),
            },
        }

    if block_type == "code":
        return {
            "object": "block",
            "type": "code",
            "code": {
                "rich_text": copy_rich_text(source_body.get("rich_text", [])),
                "language": source_body.get("language", "plain text"),
                "caption": copy_rich_text(source_body.get("caption", [])),
            },
        }

    if block_type == "callout":
        body = {
            "rich_text": copy_rich_text(source_body.get("rich_text", [])),
            "color": copy_color(source_body),
        }
        if source_body.get("icon"):
            body["icon"] = source_body["icon"]

        return {
            "object": "block",
            "type": "callout",
            "callout": body,
        }

    if block_type == "divider":
        return {
            "object": "block",
            "type": "divider",
            "divider": {},
        }

    if block_type == "bookmark":
        return {
            "object": "block",
            "type": "bookmark",
            "bookmark": {
                "url": source_body.get("url", ""),
                "caption": copy_rich_text(source_body.get("caption", [])),
            },
        }

    if block_type == "equation":
        return {
            "object": "block",
            "type": "equation",
            "equation": {
                "expression": source_body.get("expression", "")
            },
        }

    if block_type == "child_page":
        title = source_body.get("title", "Untitled")
        return {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": split_text_rich_text(f"📄 {title}"),
                "color": "default",
            },
        }

    text = get_block_plain_text(source_block) or f"[{block_type} block]"
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": split_text_rich_text(text),
            "color": "default",
        },
    }


def append_single_block(parent_block_id: str, block_payload: dict) -> str:
    url = f"https://api.notion.com/v1/blocks/{parent_block_id}/children"

    response = requests.patch(
        url,
        headers=notion_headers(),
        json={"children": [block_payload]},
        timeout=20,
    )

    if response.status_code != 200:
        print("Notion block 写入失败")
        print("Status code:", response.status_code)
        print(response.text)
        print("失败 block payload:")
        print(block_payload)
        raise SystemExit(1)

    results = response.json().get("results", [])
    if not results:
        raise RuntimeError("Notion append block 成功但没有返回新 block id")

    return results[0]["id"]


def clone_block_tree(source_block: dict, target_parent_block_id: str) -> None:
    new_block_payload = source_block_to_create_payload(source_block)
    new_block_id = append_single_block(target_parent_block_id, new_block_payload)

    if source_block.get("has_children"):
        for child in get_block_children(source_block["id"]):
            clone_block_tree(child, new_block_id)


def delete_block(block_id: str) -> None:
    url = f"https://api.notion.com/v1/blocks/{block_id}"

    response = requests.delete(
        url,
        headers=notion_headers(),
        timeout=20,
    )

    if response.status_code != 200:
        print("Notion 页面旧正文删除失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)


def clear_page_body(page_id: str) -> None:
    for child in get_block_children(page_id):
        delete_block(child["id"])


def clear_property(page_id: str, prop_name: str) -> None:
    url = f"https://api.notion.com/v1/pages/{page_id}"

    payload = {
        "properties": {
            prop_name: {"rich_text": []}
        }
    }

    response = requests.patch(
        url,
        headers=notion_headers(),
        json=payload,
        timeout=20,
    )

    if response.status_code != 200:
        print(f"清空属性失败：{prop_name}")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)


def collect_hierarchical_texts(block_id: str, ancestors: list[str] | None = None) -> list[str]:
    """
    递归读取 block 层级，并把文本转成带路径的形式。
    例如：时间监控工具制作 > 扩展更新部分的实现 3h8min
    这样 AI 不会丢掉父级项目名。
    """
    if ancestors is None:
        ancestors = []

    lines = []

    for child in get_block_children(block_id):
        text = get_block_plain_text(child).strip()
        next_ancestors = ancestors

        if text:
            if is_date_label(text):
                next_ancestors = ancestors
            else:
                path_parts = ancestors + [text]
                lines.append(" > ".join(path_parts))
                next_ancestors = path_parts

        if child.get("has_children"):
            lines.extend(collect_hierarchical_texts(child["id"], next_ancestors))

    return lines


def extract_time_info(hierarchical_texts: list[str]) -> dict:
    if not hierarchical_texts:
        return {"items": []}

    raw_text = "\n".join(hierarchical_texts)

    prompt = f"""
你是一个时间记录抽取器。请从下面带层级路径的时间记录中，抽取所有带有明确时长的活动。/no_think

重要要求：
1. event_name 必须保留完整活动路径，不要只写最后一级任务。
   例如原文是「时间监控工具制作 > 扩展更新部分的实现 3h8min」，
   event_name 必须写成「时间监控工具制作 > 扩展更新部分的实现」，
   不能只写「扩展更新部分的实现」。
2. 只抽取有明确时长的活动；没有明确时长就不要抽取。
3. duration_min 必须换算成分钟，例如 1h=60，1h23min=83，3h8min=188，半小时=30。
4. 不要编造原文没有的活动或时长。
5. evidence 必须填写原文依据。
6. time_type 必须严格从 effective、buffer、ignored 三者中选择一个。

分类规则：
1. effective：学习、读书、写代码、项目开发、功能实现、技术阅读、课程学习、看专业书、面试准备、复盘总结、项目管理学习等真正推进目标的活动。
2. buffer：缓冲时间、运动、散步、逛超市、主动刷视频、主动玩游戏等。注意：睡觉不要算 buffer，因为睡觉时间由程序单独用默认值或自定义值扣除。
3. ignored：所有无法归类为 effective 或 buffer 的活动。

输出要求：
只输出 JSON，不要输出解释文字。

原始层级文本：
{raw_text}
""".strip()

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": TIME_EXTRACTION_SCHEMA,
        "options": {"temperature": 0},
    }

    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=120,
    )

    if response.status_code != 200:
        print("Ollama 调用失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    content = response.json()["message"]["content"]

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        print("模型返回内容不是合法 JSON：")
        print(content)
        raise SystemExit(1)


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

        if time_type not in {"effective", "buffer", "ignored"}:
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
        "buffer": "缓冲",
        "ignored": "未计入",
    }

    for item in items:
        label = label_map[item["time_type"]]
        duration = format_minutes(item["duration_min"])
        lines.append(f"- [{label}] {item['event_name']}：{duration}")

    return "\n".join(lines)


def build_summary(
    target_date: str,
    items: list[dict],
    sleep_minutes: int,
) -> str:
    denominator_min = 24 * 60 - sleep_minutes

    if denominator_min <= 0:
        raise ValueError("睡觉时间不能大于或等于 24 小时")

    effective_min = sum(
        item["duration_min"]
        for item in items
        if item["time_type"] == "effective"
    )

    buffer_min = sum(
        item["duration_min"]
        for item in items
        if item["time_type"] == "buffer"
    )

    untracked_min = denominator_min - effective_min - buffer_min
    over_min = 0

    if untracked_min < 0:
        over_min = abs(untracked_min)
        untracked_min = 0

    lines = [
        f"【时间利用报告｜{target_date}】",
        f"睡觉时间：{format_minutes(sleep_minutes)}",
        f"统计分母：{format_minutes(denominator_min)}",
        "",
        f"有效利用时间：{format_minutes(effective_min)}（{format_percent(effective_min, denominator_min)}）",
        f"缓冲时间：{format_minutes(buffer_min)}（{format_percent(buffer_min, denominator_min)}）",
        f"未感知时间：{format_minutes(untracked_min)}（{format_percent(untracked_min, denominator_min)}）",
    ]

    if over_min > 0:
        lines.append(f"注意：已记录时间超出统计分母 {format_minutes(over_min)}，请检查当天记录是否重复。")

    return "\n".join(lines)


def generate_ai_summary_for_texts(
    target_date: str,
    hierarchical_texts: list[str],
    page_id: str,
    sleep_minutes: int,
) -> None:
    print(f"准备用 AI 生成 {target_date} 的 Activity Snapshot 和 summary...")

    if not hierarchical_texts:
        print(f"跳过 AI 总结：{target_date} 没有可读取的层级文本")
        return

    extraction_result = extract_time_info(hierarchical_texts)
    items = normalize_items(extraction_result.get("items", []))

    snapshot = build_activity_snapshot(target_date, items)
    summary = build_summary(target_date, items, sleep_minutes)

    update_page_rich_text_properties(
        page_id,
        {
            NOTION_SNAPSHOT_PROP: snapshot,
            NOTION_SUMMARY_PROP: summary,
        },
    )

    print(f"已写入 AI 总结：{target_date}")
    print(summary)


def should_keep_date(target_date: str, start: str | None, end: str | None) -> bool:
    if start and target_date < start:
        return False
    if end and target_date > end:
        return False
    return True


def migrate(
    start: str | None,
    end: str | None,
    overwrite_body: bool,
    with_ai: bool,
    only_ai: bool,
    clear_snapshot: bool,
    clear_summary: bool,
    sleep_hours: float,
    dry_run: bool,
) -> None:
    sleep_minutes = int(round(sleep_hours * 60))

    if sleep_minutes < 0 or sleep_minutes >= 24 * 60:
        raise ValueError("--sleep-hours 必须大于等于 0 且小于 24")

    date_blocks = find_source_date_blocks(MIGRATION_YEAR)

    if not date_blocks:
        print("没有在源页面第一层找到 6.28 / 7.1 / 7.2 这种日期折叠块")
        return

    total = 0
    created = 0
    body_written = 0
    body_skipped = 0
    ai_written = 0
    ai_skipped = 0

    for item in date_blocks:
        target_date = item["date"]

        if not should_keep_date(target_date, start, end):
            continue

        total += 1
        source_date_block = item["block"]
        page_id = find_database_page_id_by_date(target_date)

        if dry_run:
            print(f"\n[DRY RUN] {target_date}")
            print(f"源日期块标题：{item['label']}")
            print(f"源日期块类型：{source_date_block.get('type')}")
            print(f"只生成 AI：{only_ai}")
            print(f"同步生成 AI：{with_ai}")
            print(f"覆盖页面正文：{overwrite_body}")
            print(f"睡觉时间：{sleep_hours}h")
            continue

        if only_ai:
            if not page_id:
                ai_skipped += 1
                print(f"\n跳过：{target_date} 数据库中还没有这一行，不能只生成 AI")
                continue

            hierarchical_texts = collect_hierarchical_texts(page_id)
            generate_ai_summary_for_texts(
                target_date=target_date,
                hierarchical_texts=hierarchical_texts,
                page_id=page_id,
                sleep_minutes=sleep_minutes,
            )
            ai_written += 1
            continue

        if not page_id:
            page_id = create_database_page(target_date)
            created += 1
            print(f"\n已新建数据库行：{target_date}")
        else:
            print(f"\n已找到已有数据库行：{target_date}")

        existing_body = get_block_children(page_id)

        if existing_body and not overwrite_body:
            body_skipped += 1
            print(f"页面正文已有内容，跳过正文重写：{target_date}")
        else:
            if existing_body and overwrite_body:
                clear_page_body(page_id)
                print(f"已清空旧页面正文：{target_date}")

            clone_block_tree(source_date_block, page_id)
            body_written += 1
            print(f"已迁移折叠日程正文：{target_date}")

        if clear_snapshot:
            clear_property(page_id, NOTION_SNAPSHOT_PROP)
            print(f"已清空 Activity Snapshot：{target_date}")

        if clear_summary:
            clear_property(page_id, NOTION_SUMMARY_PROP)
            print(f"已清空 summary：{target_date}")

        if with_ai:
            # 一键迁移时，直接用源日程 block 的层级文本给 AI，避免刚写完又从数据库页面重读一遍。
            hierarchical_texts = collect_hierarchical_texts(item["block_id"])
            generate_ai_summary_for_texts(
                target_date=target_date,
                hierarchical_texts=hierarchical_texts,
                page_id=page_id,
                sleep_minutes=sleep_minutes,
            )
            ai_written += 1

    print("\n执行完成")
    print(f"扫描日期数：{total}")
    print(f"新建数据库行数：{created}")
    print(f"迁移正文数：{body_written}")
    print(f"跳过正文数：{body_skipped}")
    print(f"生成 AI 总结数：{ai_written}")
    print(f"跳过 AI 总结数：{ai_skipped}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="一键迁移源日程折叠块到 Raw Time Logs，并可选生成更完整的 Activity Snapshot 和三类 summary。"
    )

    parser.add_argument("--start", default=None, help="开始日期，例如 2026-07-01")
    parser.add_argument("--end", default=None, help="结束日期，例如 2026-07-08")
    parser.add_argument(
        "--overwrite-body",
        action="store_true",
        help="如果目标页面正文已有内容，先清空再重建折叠层级",
    )
    parser.add_argument(
        "--with-ai",
        action="store_true",
        help="迁移完成后，同步生成 Activity Snapshot 和 summary",
    )
    parser.add_argument(
        "--only-ai",
        action="store_true",
        help="不迁移正文，只对已经存在的数据库行批量生成 Activity Snapshot 和 summary",
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
        "--dry-run",
        action="store_true",
        help="只预览，不实际写入 Notion",
    )

    args = parser.parse_args()

    migrate(
        start=args.start,
        end=args.end,
        overwrite_body=args.overwrite_body,
        with_ai=args.with_ai,
        only_ai=args.only_ai,
        clear_snapshot=args.clear_snapshot,
        clear_summary=args.clear_summary,
        sleep_hours=args.sleep_hours,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()