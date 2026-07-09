# time_comparison.py
# 新增：负责日报环比、上周均值对比、自定义周总结。

import re
from dataclasses import dataclass
from datetime import date, timedelta

from notion_read import read_rich_text_property_by_date
from time_summary import format_minutes


WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
WEEKDAY_EN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAY_SHORT_EN = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_SHORT_CN = ["一", "二", "三", "四", "五", "六", "日"]


@dataclass(frozen=True)
class DayStats:
    target_date: str
    effective_min: int
    buffer_min: int
    sleep_min: int
    denominator_min: int
    source: str = ""


def normalize_weekly_summary_day(raw: str | None) -> int | None:
    """把 monday/sunday、1~7、周一~周日、星期一~星期日 转成 Python weekday：周一=0，周日=6。"""
    if raw is None:
        return 6

    value = str(raw).strip().lower()
    if value in {"", "none", "off", "false", "关闭", "不启用"}:
        return None

    for index, name in enumerate(WEEKDAY_EN):
        candidates = {
            name,
            WEEKDAY_SHORT_EN[index],
            str(index + 1),
            WEEKDAY_SHORT_CN[index],
            f"周{WEEKDAY_SHORT_CN[index]}",
            f"星期{WEEKDAY_SHORT_CN[index]}",
        }
        if index == 6:
            candidates.update({"天", "周天", "星期天", "sunday", "sun", "7"})
        if value in candidates:
            return index

    raise ValueError("--weekly-summary-day 只能填写 monday~sunday、1~7、周一~周日，或 none")


def format_weekday_cn(weekday_index: int | None) -> str:
    if weekday_index is None:
        return "不启用"
    return WEEKDAY_CN[weekday_index]


def build_current_stats(target_date: str, items: list[dict], sleep_minutes: int) -> DayStats:
    denominator_min = 24 * 60 - sleep_minutes
    if denominator_min <= 0:
        raise ValueError("睡觉时间不能大于或等于 24 小时")

    effective_min = sum(item["duration_min"] for item in items if item["time_type"] == "effective")
    buffer_min = sum(item["duration_min"] for item in items if item["time_type"] == "buffer")

    return DayStats(
        target_date=target_date,
        effective_min=effective_min,
        buffer_min=buffer_min,
        sleep_min=sleep_minutes,
        denominator_min=denominator_min,
        source="current_items",
    )


def parse_duration_to_minutes(text: str) -> int | None:
    hour_match = re.search(r"(\d+)\s*h", text.strip())
    minute_match = re.search(r"(\d+)\s*min", text.strip())

    if not hour_match and not minute_match:
        return None

    hours = int(hour_match.group(1)) if hour_match else 0
    minutes = int(minute_match.group(1)) if minute_match else 0
    return hours * 60 + minutes


def extract_metric_minutes(summary: str, label: str) -> int | None:
    match = re.search(rf"{re.escape(label)}\s*[:：]\s*([^\n（(]+)", summary)
    if not match:
        return None
    return parse_duration_to_minutes(match.group(1))


def parse_summary_to_stats(target_date: str, summary: str, default_sleep_min: int) -> DayStats | None:
    effective_min = extract_metric_minutes(summary, "有效利用时间")
    if effective_min is None:
        return None

    buffer_min = extract_metric_minutes(summary, "缓冲时间") or 0
    sleep_min = extract_metric_minutes(summary, "睡觉时间") or default_sleep_min
    denominator_min = extract_metric_minutes(summary, "统计分母") or (24 * 60 - sleep_min)

    if denominator_min <= 0:
        denominator_min = max(24 * 60 - default_sleep_min, 1)

    return DayStats(
        target_date=target_date,
        effective_min=effective_min,
        buffer_min=buffer_min,
        sleep_min=sleep_min,
        denominator_min=denominator_min,
        source="notion_summary",
    )


def read_day_stats_from_notion(target_date: str, summary_prop: str, default_sleep_min: int) -> DayStats | None:
    summary = read_rich_text_property_by_date(target_date, summary_prop)
    if not summary:
        return None
    return parse_summary_to_stats(target_date, summary, default_sleep_min)


def week_range(target_date: str, offset_weeks: int = 0) -> tuple[date, date]:
    target = date.fromisoformat(target_date)
    monday = target - timedelta(days=target.weekday()) + timedelta(weeks=offset_weeks)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def iter_dates(start_date: date, end_date: date) -> list[str]:
    result = []
    current = start_date
    while current <= end_date:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def collect_stats_for_dates(
    target_dates: list[str],
    summary_prop: str,
    default_sleep_min: int,
    current_stats: DayStats | None = None,
) -> list[DayStats]:
    stats = []

    for target_date in target_dates:
        if current_stats and target_date == current_stats.target_date:
            stats.append(current_stats)
            continue

        day_stats = read_day_stats_from_notion(target_date, summary_prop, default_sleep_min)
        if day_stats:
            stats.append(day_stats)

    return stats


def average_minutes(values: list[int]) -> int | None:
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def format_signed_percent(delta_min: int, denominator_min: int) -> str:
    if denominator_min <= 0:
        return "+0.0%"
    return f"{delta_min / denominator_min * 100:+.1f}%"


def format_delta_text(delta_min: int) -> str:
    if delta_min > 0:
        return f"增加 {format_minutes(delta_min)}"
    if delta_min < 0:
        return f"减少 {format_minutes(abs(delta_min))}"
    return "持平 0min"


def build_compare_line(
    label: str,
    current_min: int,
    baseline_label: str,
    baseline_min: int,
    denominator_min: int,
) -> str:
    delta_min = current_min - baseline_min
    return (
        f"- {label}：当前 {format_minutes(current_min)}；"
        f"{baseline_label} {format_minutes(baseline_min)}；"
        f"{format_delta_text(delta_min)}（{format_signed_percent(delta_min, denominator_min)}）"
    )


def build_daily_comparison_section(target_date: str, current_stats: DayStats, summary_prop: str) -> str:
    target = date.fromisoformat(target_date)
    previous_day = (target - timedelta(days=1)).isoformat()
    previous_stats = read_day_stats_from_notion(previous_day, summary_prop, current_stats.sleep_min)

    previous_week_start, previous_week_end = week_range(target_date, offset_weeks=-1)
    previous_week_stats = collect_stats_for_dates(
        target_dates=iter_dates(previous_week_start, previous_week_end),
        summary_prop=summary_prop,
        default_sleep_min=current_stats.sleep_min,
    )
    previous_week_avg_effective = average_minutes([item.effective_min for item in previous_week_stats])

    lines = [
        "【对比分析】",
        f"1. 日对比｜对比前一天 {previous_day}",
    ]

    if previous_stats:
        lines.append(build_compare_line("有效利用时间", current_stats.effective_min, "前一天", previous_stats.effective_min, current_stats.denominator_min))
        # lines.append(build_compare_line("缓冲时间", current_stats.buffer_min, "前一天", previous_stats.buffer_min, current_stats.denominator_min))
    else:
        lines.append("- 前一天没有可解析的 summary，暂不生成日环比。")

    lines.extend([
        "",
        f"2. 周均对比｜对比上一周 {previous_week_start.isoformat()} ~ {previous_week_end.isoformat()}",
    ])

    if previous_week_avg_effective is not None:
        lines.append(
            build_compare_line(
                "有效利用时间",
                current_stats.effective_min,
                f"上周日均（{len(previous_week_stats)}/7 天）",
                previous_week_avg_effective,
                current_stats.denominator_min,
            )
        )
    else:
        lines.append("- 上一周没有可解析的 summary，暂不生成周均对比。")

    return "\n".join(lines)


def build_weekly_summary_section(
    target_date: str,
    current_stats: DayStats,
    summary_prop: str,
    weekly_summary_weekday: int | None,
) -> str:
    if weekly_summary_weekday is None:
        return ""

    target = date.fromisoformat(target_date)
    if target.weekday() != weekly_summary_weekday:
        return ""

    current_week_start, current_week_end = week_range(target_date, offset_weeks=0)
    previous_week_start, previous_week_end = week_range(target_date, offset_weeks=-1)

    current_week_stats = collect_stats_for_dates(
        target_dates=iter_dates(current_week_start, current_week_end),
        summary_prop=summary_prop,
        default_sleep_min=current_stats.sleep_min,
        current_stats=current_stats,
    )
    previous_week_stats = collect_stats_for_dates(
        target_dates=iter_dates(previous_week_start, previous_week_end),
        summary_prop=summary_prop,
        default_sleep_min=current_stats.sleep_min,
    )

    current_avg_effective = average_minutes([item.effective_min for item in current_week_stats])
    previous_avg_effective = average_minutes([item.effective_min for item in previous_week_stats])

    lines = [
        "【本周周总结】",
        f"触发日：{format_weekday_cn(weekly_summary_weekday)}",
        f"本周范围：{current_week_start.isoformat()} ~ {current_week_end.isoformat()}（有效记录 {len(current_week_stats)}/7 天）",
        f"上周范围：{previous_week_start.isoformat()} ~ {previous_week_end.isoformat()}（有效记录 {len(previous_week_stats)}/7 天）",
    ]

    if current_avg_effective is None:
        lines.append("- 本周没有可解析的 summary，暂不生成周总结。")
        return "\n".join(lines)

    lines.append(f"- 本周有效利用日均：{format_minutes(current_avg_effective)}")

    if previous_avg_effective is None:
        lines.append("- 上周没有可解析的 summary，暂不生成周环比。")
        return "\n".join(lines)

    lines.append(f"- 上周有效利用日均：{format_minutes(previous_avg_effective)}")
    delta_min = current_avg_effective - previous_avg_effective
    lines.append(f"- 周环比：{format_delta_text(delta_min)}（{format_signed_percent(delta_min, current_stats.denominator_min)}）")

    return "\n".join(lines)


def build_comparison_sections(
    target_date: str,
    current_stats: DayStats,
    summary_prop: str,
    weekly_summary_weekday: int | None,
) -> str:
    sections = [build_daily_comparison_section(target_date, current_stats, summary_prop)]

    weekly_summary = build_weekly_summary_section(
        target_date=target_date,
        current_stats=current_stats,
        summary_prop=summary_prop,
        weekly_summary_weekday=weekly_summary_weekday,
    )
    if weekly_summary:
        sections.append(weekly_summary)

    return "\n\n".join(sections)