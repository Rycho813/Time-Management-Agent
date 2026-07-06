import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_DATE_PROP = os.getenv("NOTION_DATE_PROP", "Date")
NOTION_TEXT_PROP = os.getenv("NOTION_TEXT_PROP", "Text")
NOTION_VERSION = "2022-06-28"


def get_rich_text_plain_text(property_value: dict) -> str:
    rich_text_items = property_value.get("rich_text", [])
    return "".join(item.get("plain_text", "") for item in rich_text_items)


def get_title_plain_text(property_value: dict) -> str:
    title_items = property_value.get("title", [])
    return "".join(item.get("plain_text", "") for item in title_items)


def get_date_start(property_value: dict) -> str:
    date_value = property_value.get("date")
    if not date_value:
        return ""
    return date_value.get("start", "")


def query_all_pages() -> list[dict]:
    if not NOTION_TOKEN:
        raise RuntimeError("缺少 NOTION_TOKEN，请检查 .env")

    if not NOTION_DATABASE_ID:
        raise RuntimeError("缺少 NOTION_DATABASE_ID，请检查 .env")

    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    all_pages = []
    payload = {
        "page_size": 100
    }

    while True:
        response = requests.post(url, headers=headers, json=payload, timeout=20)

        if response.status_code != 200:
            print("Notion 读取失败")
            print("Status code:", response.status_code)
            print(response.text)
            raise SystemExit(1)

        data = response.json()
        all_pages.extend(data.get("results", []))

        if not data.get("has_more"):
            break

        payload["start_cursor"] = data.get("next_cursor")

    return all_pages


def read_notion_texts(target_date: str) -> list[str]:
    pages = query_all_pages()
    texts = []

    print(f"数据库中总记录数：{len(pages)}")
    print("下面是 Python 从 Notion API 读到的真实日期：")

    for index, page in enumerate(pages, start=1):
        properties = page.get("properties", {})

        title_property = properties.get("Name") or properties.get("名称") or {}
        date_property = properties.get(NOTION_DATE_PROP, {})
        text_property = properties.get(NOTION_TEXT_PROP, {})

        title = get_title_plain_text(title_property)
        date_start = get_date_start(date_property)
        text = get_rich_text_plain_text(text_property).strip()

        print(f"{index}. title={title} | date_start={date_start} | text={text}")

        # 关键：只取日期前 10 位，例如 2026-07-01
        # 这样即使 Notion 返回 2026-07-01T00:00:00.000+08:00，也可以匹配。
        date_only = date_start[:10]

        if date_only == target_date and text:
            texts.append(text)

    return texts


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python notion_read.py 2026-07-02")
        raise SystemExit(1)

    target_date = sys.argv[1]
    texts = read_notion_texts(target_date)

    print("")
    print(f"目标日期：{target_date}")
    print(f"读取到 {len(texts)} 条记录")

    for index, text in enumerate(texts, start=1):
        print(f"\n--- 记录 {index} ---")
        print(text)


if __name__ == "__main__":
    main()