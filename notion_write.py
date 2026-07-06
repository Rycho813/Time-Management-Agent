import os
import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_DATE_PROP = os.getenv("NOTION_DATE_PROP", "Date")
NOTION_SUMMARY_PROP = os.getenv("NOTION_SUMMARY_PROP", "summary")
NOTION_VERSION = "2022-06-28"


def get_notion_headers() -> dict:
    if not NOTION_TOKEN:
        raise RuntimeError("缺少 NOTION_TOKEN，请检查 .env")

    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def split_rich_text(text: str, chunk_size: int = 1900) -> list[dict]:
    return [
        {
            "type": "text",
            "text": {
                "content": text[i:i + chunk_size]
            }
        }
        for i in range(0, len(text), chunk_size)
    ]


def find_notion_page_id_by_date(target_date: str) -> str | None:
    if not NOTION_DATABASE_ID:
        raise RuntimeError("缺少 NOTION_DATABASE_ID，请检查 .env")

    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"

    payload = {
        "filter": {
            "property": NOTION_DATE_PROP,
            "date": {
                "equals": target_date
            }
        }
    }

    response = requests.post(
        url,
        headers=get_notion_headers(),
        json=payload,
        timeout=20,
    )

    if response.status_code != 200:
        print("Notion 查询 page 失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    results = response.json().get("results", [])

    if not results:
        return None

    if len(results) > 1:
        print(f"警告：日期 {target_date} 找到多条 Notion 记录，只写入第一条")

    return results[0]["id"]


def write_summary_to_notion(target_date: str, summary: str) -> None:
    page_id = find_notion_page_id_by_date(target_date)

    if not page_id:
        raise RuntimeError(f"没有找到日期为 {target_date} 的 Notion 记录，无法写入 summary")

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
        headers=get_notion_headers(),
        json=payload,
        timeout=20,
    )

    if response.status_code != 200:
        print("Notion 写入 summary 失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    print(f"Notion summary 写入成功：{target_date}")