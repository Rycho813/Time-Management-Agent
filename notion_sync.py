import os
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_SOURCE_PAGE_ID = os.getenv("NOTION_SOURCE_PAGE_ID")

NOTION_NAME_PROP = os.getenv("NOTION_NAME_PROP", "Name")
NOTION_DATE_PROP = os.getenv("NOTION_DATE_PROP", "Date")
NOTION_TEXT_PROP = os.getenv("NOTION_TEXT_PROP", "Text")
NOTION_SUMMARY_PROP = os.getenv("NOTION_SUMMARY_PROP", "summary")
NOTION_VERSION = "2022-06-28"


def notion_headers() -> dict:
    if not NOTION_TOKEN:
        raise RuntimeError("缺少 NOTION_TOKEN，请检查 .env")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def split_rich_text(text: str, chunk_size: int = 1900) -> list[dict]:
    # Notion 单个 rich_text text.content 上限是 2000 字符，这里保守切成 1900
    return [
        {
            "type": "text",
            "text": {"content": text[i:i + chunk_size]},
        }
        for i in range(0, len(text), chunk_size)
    ]


def get_block_children(block_id: str) -> list[dict]:
    url = f"https://api.notion.com/v1/blocks/{block_id}/children"
    results = []
    start_cursor = None

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor

        response = requests.get(
            url,
            headers=notion_headers(),
            params=params,
            timeout=20,
        )

        if response.status_code != 200:
            print("Notion block 读取失败")
            print("Status code:", response.status_code)
            print(response.text)
            raise SystemExit(1)

        data = response.json()
        results.extend(data.get("results", []))

        if not data.get("has_more"):
            break

        start_cursor = data.get("next_cursor")

    return results


def rich_text_to_plain(rich_text_items: list[dict]) -> str:
    return "".join(item.get("plain_text", "") for item in rich_text_items)


def get_block_plain_text(block: dict) -> str:
    block_type = block.get("type")
    block_body = block.get(block_type, {})

    if "rich_text" not in block_body:
        return ""

    return rich_text_to_plain(block_body.get("rich_text", [])).strip()


def collect_text_recursive(block_id: str) -> list[str]:
    lines = []

    for child in get_block_children(block_id):
        text = get_block_plain_text(child)
        if text:
            lines.append(text)

        if child.get("has_children"):
            lines.extend(collect_text_recursive(child["id"]))

    return lines


def date_to_toggle_label(target_date: str) -> str:
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    return f"{dt.month}.{dt.day}"


def read_source_page_text_by_date(target_date: str) -> str:
    if not NOTION_SOURCE_PAGE_ID:
        raise RuntimeError("缺少 NOTION_SOURCE_PAGE_ID，请检查 .env")

    target_label = date_to_toggle_label(target_date)

    for block in get_block_children(NOTION_SOURCE_PAGE_ID):
        block_text = get_block_plain_text(block)

        if block_text == target_label:
            lines = collect_text_recursive(block["id"])
            return "\n".join(lines).strip()

    raise RuntimeError(f"图1页面里没有找到日期折叠块：{target_label}")


def find_database_page_by_date(target_date: str) -> str | None:
    if not NOTION_DATABASE_ID:
        raise RuntimeError("缺少 NOTION_DATABASE_ID，请检查 .env")

    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"

    payload = {
        "filter": {
            "property": NOTION_DATE_PROP,
            "date": {"equals": target_date},
        }
    }

    response = requests.post(
        url,
        headers=notion_headers(),
        json=payload,
        timeout=20,
    )

    if response.status_code != 200:
        print("Notion 数据库查询失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    results = response.json().get("results", [])
    if not results:
        return None

    if len(results) > 1:
        print(f"警告：{target_date} 找到多条记录，只使用第一条")

    return results[0]["id"]


def create_database_page(target_date: str, raw_text: str) -> str:
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
            NOTION_TEXT_PROP: {
                "rich_text": split_rich_text(raw_text)
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
        print("Notion 新建数据库记录失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    return response.json()["id"]


def update_database_text(page_id: str, raw_text: str) -> None:
    url = f"https://api.notion.com/v1/pages/{page_id}"

    payload = {
        "properties": {
            NOTION_TEXT_PROP: {
                "rich_text": split_rich_text(raw_text)
            }
        }
    }

    response = requests.patch(
        url,
        headers=notion_headers(),
        json=payload,
        timeout=20,
    )

    if response.status_code != 200:
        print("Notion Text 更新失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)


def sync_day_from_source_page(target_date: str) -> None:
    raw_text = read_source_page_text_by_date(target_date)

    if not raw_text:
        raise RuntimeError(f"{target_date} 的折叠块下面没有可同步文本")

    page_id = find_database_page_by_date(target_date)

    if page_id:
        update_database_text(page_id, raw_text)
        print(f"已更新 Notion 数据库 Text：{target_date}")
    else:
        create_database_page(target_date, raw_text)
        print(f"已新建 Notion 数据库记录：{target_date}")


def write_summary_to_notion(target_date: str, summary: str) -> None:
    page_id = find_database_page_by_date(target_date)

    if not page_id:
        raise RuntimeError(f"数据库中没有找到 {target_date}，无法写入 summary")

    url = f"https://api.notion.com/v1/pages/{page_id}"

    payload = {
        "properties": {
            NOTION_SUMMARY_PROP: {
                "rich_text": split_rich_text(summary)
            }
        }
    }

    response = requests.patch(
        url,
        headers=notion_headers(),
        json=payload,
        timeout=20,
    )

    if response.status_code != 200:
        print("Notion summary 写入失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    print(f"已写入 Notion summary：{target_date}")