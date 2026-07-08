import os
import re
import requests
from datetime import date
from dotenv import load_dotenv

from notion_read import (
    notion_headers,
    get_block_children,
    get_block_plain_text,
    query_database_pages_by_date,
    NOTION_DATABASE_ID,
    NOTION_DATE_PROP,
)

load_dotenv()

NOTION_SOURCE_PAGE_ID_RAW = os.getenv("NOTION_SOURCE_PAGE_ID", "")
NOTION_NAME_PROP = os.getenv("NOTION_NAME_PROP", "Name")
NOTION_SNAPSHOT_PROP = os.getenv("NOTION_SNAPSHOT_PROP", "Activity Snapshot")
NOTION_SUMMARY_PROP = os.getenv("NOTION_SUMMARY_PROP", "summary")
MIGRATION_YEAR = int(os.getenv("MIGRATION_YEAR", "2026"))

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

def find_source_date_block_by_date(target_date: str, year: int = MIGRATION_YEAR) -> dict | None:
    for item in find_source_date_blocks(year):
        if item["date"] == target_date:
            return item
    return None

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

def migrate_single_day(
    target_date: str,
    overwrite_body: bool,
    clear_snapshot: bool,
    clear_summary: bool,
    dry_run: bool = False,
) -> tuple[str | None, str | None]:
    source_item = find_source_date_block_by_date(target_date)

    if not source_item:
        print(f"跳过：源页面没有找到 {target_date} 的日期折叠块")
        return None, None

    source_date_block = source_item["block"]
    source_block_id = source_item["block_id"]
    page_id = find_database_page_id_by_date(target_date)

    if dry_run:
        print(f"\n[DRY RUN] {target_date}")
        print(f"源日期块标题：{source_item['label']}")
        print(f"源日期块类型：{source_date_block.get('type')}")
        print(f"目标数据库页面是否已存在：{bool(page_id)}")
        print(f"覆盖页面正文：{overwrite_body}")
        print(f"清空 Activity Snapshot：{clear_snapshot}")
        print(f"清空 summary：{clear_summary}")
        return page_id, source_block_id

    if not page_id:
        page_id = create_database_page(target_date)
        print(f"\n已新建数据库行：{target_date}")
    else:
        print(f"\n已找到已有数据库行：{target_date}")

    existing_body = get_block_children(page_id)

    if existing_body and not overwrite_body:
        print(f"页面正文已有内容，跳过正文重写：{target_date}")
    else:
        if existing_body and overwrite_body:
            clear_page_body(page_id)
            print(f"已清空旧页面正文：{target_date}")

        clone_block_tree(source_date_block, page_id)
        print(f"已迁移折叠日程正文：{target_date}")

    if clear_snapshot:
        clear_property(page_id, NOTION_SNAPSHOT_PROP)
        print(f"已清空 Activity Snapshot：{target_date}")

    if clear_summary:
        clear_property(page_id, NOTION_SUMMARY_PROP)
        print(f"已清空 summary：{target_date}")

    return page_id, source_block_id
