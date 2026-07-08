import os
import requests
from dotenv import load_dotenv
from notion_read import find_page_id_by_date, notion_headers

load_dotenv()

NOTION_SNAPSHOT_PROP = os.getenv("NOTION_SNAPSHOT_PROP", "Activity Snapshot")
NOTION_SUMMARY_PROP = os.getenv("NOTION_SUMMARY_PROP", "Summary")


def split_rich_text(text: str, chunk_size: int = 1900) -> list[dict]:
    if not text:
        return []
    return [
        {
            "type": "text",
            "text": {"content": text[i:i + chunk_size]},
        }
        for i in range(0, len(text), chunk_size)
    ]


def update_page_rich_text_properties(page_id: str, properties: dict[str, str]) -> None:
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
        "properties": {
            prop_name: {"rich_text": split_rich_text(prop_value)}
            for prop_name, prop_value in properties.items()
        }
    }

    response = requests.patch(url, headers=notion_headers(), json=payload, timeout=20)
    if response.status_code != 200:
        print("Notion 属性写入失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)


def write_snapshot_and_summary_to_notion(
    target_date: str,
    snapshot: str,
    summary: str,
) -> None:
    page_id = find_page_id_by_date(target_date)
    update_page_rich_text_properties(
        page_id,
        {
            NOTION_SNAPSHOT_PROP: snapshot,
            NOTION_SUMMARY_PROP: summary,
        },
    )
    print(f"已写入 Notion：{target_date} 的 Activity Snapshot 和 Summary")


def write_summary_to_notion(target_date: str, summary: str) -> None:
    page_id = find_page_id_by_date(target_date)
    update_page_rich_text_properties(page_id, {NOTION_SUMMARY_PROP: summary})
    print(f"已写入 Notion Summary：{target_date}")
