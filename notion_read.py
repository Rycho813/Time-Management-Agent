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


def read_notion_texts(target_date: str) -> list[str]:
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

    payload = {
        "filter": {
            "property": NOTION_DATE_PROP,
            "date": {
                "equals": target_date
            }
        }
    }

    response = requests.post(url, headers=headers, json=payload, timeout=20)

    if response.status_code != 200:
        print("Notion 读取失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    data = response.json()
    texts = []

    for page in data.get("results", []):
        properties = page.get("properties", {})
        text_property = properties.get(NOTION_TEXT_PROP)

        if not text_property:
            continue

        text = get_rich_text_plain_text(text_property).strip()

        if text:
            texts.append(text)

    return texts


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python notion_read.py 2026-07-02")
        raise SystemExit(1)

    target_date = sys.argv[1]
    texts = read_notion_texts(target_date)

    print(f"目标日期：{target_date}")
    print(f"读取到 {len(texts)} 条记录")

    for index, text in enumerate(texts, start=1):
        print(f"\n--- 记录 {index} ---")
        print(text)


if __name__ == "__main__":
    main()