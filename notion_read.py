# 读取notion的基础函数
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_DATE_PROP = os.getenv("NOTION_DATE_PROP", "Date")
NOTION_VERSION = os.getenv("NOTION_VERSION", "2022-06-28")


def notion_headers() -> dict:
    if not NOTION_TOKEN:
        raise RuntimeError("缺少 NOTION_TOKEN，请检查 .env")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def query_database_pages_by_date(target_date: str) -> list[dict]:
    if not NOTION_DATABASE_ID:
        raise RuntimeError("缺少 NOTION_DATABASE_ID，请检查 .env")

    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": NOTION_DATE_PROP,
            "date": {"equals": target_date},
        }
    }

    response = requests.post(url, headers=notion_headers(), json=payload, timeout=20)
    if response.status_code != 200:
        print("Notion 数据库查询失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    return response.json().get("results", [])


def find_page_id_by_date(target_date: str) -> str:
    pages = query_database_pages_by_date(target_date)
    if not pages:
        raise RuntimeError(f"数据库中没有找到 Date = {target_date} 的页面")
    if len(pages) > 1:
        print(f"警告：{target_date} 找到多条记录，只使用第一条")
    return pages[0]["id"]


def get_block_children(block_id: str) -> list[dict]:
    url = f"https://api.notion.com/v1/blocks/{block_id}/children"
    results = []
    start_cursor = None

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor

        response = requests.get(url, headers=notion_headers(), params=params, timeout=20)
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

def get_page_rich_text_property_plain_text(page: dict, prop_name: str) -> str:  # 新增
    prop = page.get("properties", {}).get(prop_name, {})
    rich_text_items = prop.get("rich_text", [])
    return rich_text_to_plain(rich_text_items).strip()

def read_rich_text_property_by_date(target_date: str, prop_name: str) -> str | None:  # 新增
    pages = query_database_pages_by_date(target_date)

    if not pages:
        return None

    if len(pages) > 1:
        print(f"警告：{target_date} 找到多条数据库记录，只使用第一条")

    return get_page_rich_text_property_plain_text(pages[0], prop_name)

def get_block_plain_text(block: dict) -> str:
    block_type = block.get("type")
    block_body = block.get(block_type, {})
    rich_text = block_body.get("rich_text")
    if not rich_text:
        return ""
    return rich_text_to_plain(rich_text).strip()


def collect_text_recursive(block_id: str) -> list[str]:
    lines = []
    for child in get_block_children(block_id):
        text = get_block_plain_text(child)
        if text:
            lines.append(text)
        if child.get("has_children"):
            lines.extend(collect_text_recursive(child["id"]))
    return lines


def read_notion_texts(target_date: str) -> list[str]:
    """读取总数据库中 Date=target_date 的页面正文。"""
    page_id = find_page_id_by_date(target_date)
    lines = collect_text_recursive(page_id)
    return [line for line in lines if line.strip()]


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python notion_read.py 2026-07-02")
        raise SystemExit(1)

    target_date = sys.argv[1]
    texts = read_notion_texts(target_date)

    print(f"目标日期：{target_date}")
    print(f"从页面正文读取到 {len(texts)} 行文本")

    for index, text in enumerate(texts, start=1):
        print(f"\n--- 行 {index} ---")
        print(text)


if __name__ == "__main__":
    main()
