# 生成Snapshot，summary和飞书报告的文本。
from datetime import date

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

def normalize_items(items: list[dict]) -> list[dict]:  
    valid_items = []

    for item in items:
        try:
            event_name = str(item["event_name"]).strip()
            duration_min = int(item["duration_min"])
            time_type = str(item["time_type"]).strip()
            project_name = str(item.get("project_name", "")).strip()  
            project_status = str(
                item.get("project_status", "none")
            ).strip().lower()  
        except (KeyError, TypeError, ValueError):
            continue

        if not event_name or duration_min <= 0:
            continue

        if time_type not in {"effective", "buffer", "ignored"}:
            continue

        if project_status not in {"open", "closed", "none"}:  
            project_status = "none"

        if not project_name:  
            project_status = "none"

        valid_items.append(
            {
                "event_name": event_name,
                "duration_min": duration_min,
                "time_type": time_type,
                "project_name": project_name,  
                "project_status": project_status,  
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

        project_text = (
            f"[{item['project_name']}]"
            if item["project_name"]
            else ""
        )

        lines.append(
            f"- [{label}]{project_text} {item['event_name']}：{duration}"
        )

    return "\n".join(lines)

def build_summary(target_date: str, items: list[dict], sleep_minutes: int) -> str:
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

def get_weekday_cn(target_date: str) -> str:
    weekday_map = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return weekday_map[date.fromisoformat(target_date).weekday()]

def build_feishu_report(target_date: str, snapshot: str, summary: str) -> str:
    weekday = get_weekday_cn(target_date)
    return f"【时间管理日报｜{target_date} {weekday}】\n\n{summary}\n\n{snapshot}"