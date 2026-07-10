import os
from collections import defaultdict
from datetime import date

import requests
from dotenv import load_dotenv

from notion_read import notion_headers
from notion_write import split_rich_text
from time_summary import format_minutes

load_dotenv()

PROJECT_DATABASE_ID = os.getenv("PROJECT_DATABASE_ID")
NAME_PROP = "项目名称"
STATUS_PROP = "项目状态"
START_PROP = "开始日期"
DONE_PROP = "完成日期"
DAILY_PROP = "每日时间记录"
TOTAL_PROP = "总时间"


def _database_id() -> str:
    if not PROJECT_DATABASE_ID:
        raise RuntimeError("缺少 PROJECT_DATABASE_ID，请检查 .env 或 GitHub Actions Secrets")
    return PROJECT_DATABASE_ID


def _plain(page: dict, prop_name: str, prop_type: str) -> str:
    items = page.get("properties", {}).get(prop_name, {}).get(prop_type, [])
    return "".join(item.get("plain_text", "") for item in items).strip()


def _select(page: dict, prop_name: str) -> str:
    value = page.get("properties", {}).get(prop_name, {}).get("select") or {}
    return str(value.get("name", "")).strip().lower()


def _date(page: dict, prop_name: str) -> str | None:
    value = page.get("properties", {}).get(prop_name, {}).get("date")
    return value.get("start") if value else None


def _number(page: dict, prop_name: str) -> int | None:
    value = page.get("properties", {}).get(prop_name, {}).get("number")
    return int(value) if value is not None else None


def query_projects() -> list[dict]:
    url = f"https://api.notion.com/v1/databases/{_database_id()}/query"
    results: list[dict] = []
    cursor = None

    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        response = requests.post(url, headers=notion_headers(), json=payload, timeout=20)
        if response.status_code != 200:
            raise RuntimeError(f"项目状态表查询失败：{response.text}")
        data = response.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            return results
        cursor = data.get("next_cursor")


def get_existing_project_names() -> list[str]:
    names = [_plain(page, NAME_PROP, "title") for page in query_projects()]
    return sorted(name for name in names if name)


def _parse_records(text: str) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("｜", 2)]
        if len(parts) != 3:
            continue
        record_date, minutes_text, details = parts
        try:
            date.fromisoformat(record_date)
            minutes = int(minutes_text)
        except ValueError:
            continue
        if minutes > 0 and details:
            records[record_date] = {"duration_min": minutes, "details": details}
    return records


def _serialize_records(records: dict[str, dict]) -> str:
    return "\n".join(
        f"{record_date}｜{records[record_date]['duration_min']}｜{records[record_date]['details']}"
        for record_date in sorted(records)
    )


def _group_today(items: list[dict]) -> dict[str, dict]:
    grouped: dict[str, dict] = {}
    for item in items:
        project_name = str(item.get("project_name", "")).strip()
        if item.get("time_type") != "effective" or not project_name:
            continue

        project = grouped.setdefault(
            project_name,
            {"duration_min": 0, "events": defaultdict(int), "closed": False},
        )
        minutes = int(item["duration_min"])
        project["duration_min"] += minutes
        project["events"][item["event_name"]] += minutes
        project["closed"] = project["closed"] or item.get("project_status") == "closed"

    for project in grouped.values():
        project["details"] = "；".join(
            f"{event}（{format_minutes(minutes)}）"
            for event, minutes in project.pop("events").items()
        )
    return grouped


def _save_project(
    page: dict | None,
    project_name: str,
    status: str,
    start_date: str,
    completion_date: str | None,
    records_text: str,
    total_minutes: int | None,
) -> bool:
    properties = {
        STATUS_PROP: {"select": {"name": status}},
        START_PROP: {"date": {"start": start_date}},
        DONE_PROP: {"date": {"start": completion_date} if completion_date else None},
        DAILY_PROP: {"rich_text": split_rich_text(records_text)},
        TOTAL_PROP: {"number": total_minutes},
    }

    if page:
        unchanged = (
            (_select(page, STATUS_PROP) or "open") == status
            and _date(page, START_PROP) == start_date
            and _date(page, DONE_PROP) == completion_date
            and _plain(page, DAILY_PROP, "rich_text") == records_text
            and _number(page, TOTAL_PROP) == total_minutes
        )
        if unchanged:
            return False
        url = f"https://api.notion.com/v1/pages/{page['id']}"
        response = requests.patch(
            url,
            headers=notion_headers(),
            json={"properties": properties},
            timeout=20,
        )
    else:
        properties[NAME_PROP] = {
            "title": [{"type": "text", "text": {"content": project_name}}]
        }
        response = requests.post(
            "https://api.notion.com/v1/pages",
            headers=notion_headers(),
            json={
                "parent": {"database_id": _database_id()},
                "properties": properties,
            },
            timeout=20,
        )

    if response.status_code != 200:
        raise RuntimeError(f"项目状态表写入失败：{project_name}；{response.text}")
    return True


def _archive(page: dict) -> None:
    response = requests.patch(
        f"https://api.notion.com/v1/pages/{page['id']}",
        headers=notion_headers(),
        json={"archived": True},
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"空项目归档失败：{response.text}")


def _completion_report(
    project_name: str,
    start_date: str,
    completion_date: str,
    records: dict[str, dict],
) -> str:
    total_minutes = sum(
        record["duration_min"]
        for record_date, record in records.items()
        if record_date <= completion_date
    )
    lines = [
        f"【项目完成｜{project_name}】",
        f"开始日期：{start_date}",
        f"完成日期：{completion_date}",
        f"总时间：{format_minutes(total_minutes)}",
        "每日投入明细：",
    ]
    for record_date in sorted(records):
        if record_date <= completion_date:
            record = records[record_date]
            lines.append(
                f"- {record_date}：{record['details']}；当日合计 {format_minutes(record['duration_min'])}"
            )
    return "\n".join(lines)


def update_projects_for_day(target_date: str, items: list[dict]) -> str:
    today = _group_today(items)
    pages = query_projects()
    page_map = {
        _plain(page, NAME_PROP, "title"): page
        for page in pages
        if _plain(page, NAME_PROP, "title")
    }

    affected = set(today)
    for project_name, page in page_map.items():
        records = _parse_records(_plain(page, DAILY_PROP, "rich_text"))
        if target_date in records:
            affected.add(project_name)

    reports: list[str] = []
    for project_name in sorted(affected):
        page = page_map.get(project_name)
        day_data = today.get(project_name)
        records = (
            _parse_records(_plain(page, DAILY_PROP, "rich_text"))
            if page
            else {}
        )
        old_record = records.get(target_date)

        if day_data:
            records[target_date] = {
                "duration_min": day_data["duration_min"],
                "details": day_data["details"],
            }
        else:
            records.pop(target_date, None)

        if not records:
            _archive(page)
            print(f"项目状态表：{project_name} 已无有效记录，已归档")
            continue

        marked_closed = bool(day_data and day_data["closed"])
        old_status = _select(page, STATUS_PROP) if page else "open"
        completion_date = _date(page, DONE_PROP) if page else None

        if completion_date == target_date and not marked_closed:
            status, completion_date = "open", None
        elif marked_closed:
            status = "closed"
            completion_date = min(completion_date, target_date) if completion_date else target_date
        else:
            status = old_status or "open"

        start_date = min(records)
        total_minutes = (
            sum(
                record["duration_min"]
                for record_date, record in records.items()
                if record_date <= completion_date
            )
            if status == "closed" and completion_date
            else None
        )

        changed = _save_project(
            page=page,
            project_name=project_name,
            status=status,
            start_date=start_date,
            completion_date=completion_date,
            records_text=_serialize_records(records),
            total_minutes=total_minutes,
        )

        if not changed:
            print(f"项目状态表：{project_name}，{target_date} 内容一致，跳过写入")
        elif not page:
            print(f"项目状态表：新建项目 {project_name}")
        elif day_data and old_record:
            print(f"项目状态表：{project_name}，{target_date} 覆盖更新")
        elif day_data:
            print(f"项目状态表：{project_name}，{target_date} 新增记录")
        else:
            print(f"项目状态表：{project_name}，{target_date} 删除旧记录")

        if marked_closed and completion_date:
            reports.append(
                _completion_report(project_name, start_date, completion_date, records)
            )

    return "\n\n".join(reports)
