# 新增代码：life_calendar_exporter.py

import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent

DAILY_STORE_PATH = ROOT_DIR / "data" / "life_calendar_daily.json"
WEB_OUTPUT_PATH = ROOT_DIR / "web" / "data" / "life_calendar.json"

TOTAL_LIFE_WEEKS = 90 * 52


def require_birth_date() -> date:
    birth_date_text = os.getenv("LIFE_BIRTH_DATE", "").strip()

    if not birth_date_text:
        raise RuntimeError(
            "缺少 LIFE_BIRTH_DATE，请在 .env 或 GitHub Variables 中配置"
        )

    try:
        return date.fromisoformat(birth_date_text)
    except ValueError as exc:
        raise RuntimeError(
            "LIFE_BIRTH_DATE 必须使用 YYYY-MM-DD 格式"
        ) from exc


def read_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数") from exc

    return value


def read_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default)).strip()

    try:
        return float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是数字") from exc


def load_config() -> dict[str, Any]:
    awake_minutes = read_int_env("LIFE_AWAKE_MINUTES", 1020)
    low_threshold = read_float_env("LIFE_LOW_THRESHOLD", 0.30)
    high_threshold = read_float_env("LIFE_HIGH_THRESHOLD", 0.60)

    if awake_minutes <= 0:
        raise RuntimeError("LIFE_AWAKE_MINUTES 必须大于0")

    if not 0 <= low_threshold <= high_threshold:
        raise RuntimeError(
            "颜色阈值必须满足 0 <= low <= high"
        )

    return {
        "birth_date": require_birth_date(),
        "awake_minutes_per_day": awake_minutes,
        "thresholds": {
            "low": low_threshold,
            "high": high_threshold,
        },
        "colors": {
            "low": os.getenv("LIFE_COLOR_LOW", "#D9534F"),
            "medium": os.getenv("LIFE_COLOR_MEDIUM", "#E2B93B"),
            "high": os.getenv("LIFE_COLOR_HIGH", "#46A267"),
        },
    }


def load_daily_store() -> dict[str, Any]:
    if not DAILY_STORE_PATH.exists():
        return {
            "version": 1,
            "days": {},
        }

    try:
        with DAILY_STORE_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"无法读取 {DAILY_STORE_PATH}"
        ) from exc

    if not isinstance(data.get("days"), dict):
        raise RuntimeError(
            "life_calendar_daily.json 的 days 必须是对象"
        )

    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_suffix(f"{path.suffix}.tmp")

    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.write("\n")

    temporary_path.replace(path)


def get_week_start(target_date: date) -> date:
    return target_date - timedelta(days=target_date.weekday())


def get_level(
    utilization: float,
    low_threshold: float,
    high_threshold: float,
) -> str:
    if utilization < low_threshold:
        return "low"

    if utilization <= high_threshold:
        return "medium"

    return "high"


def build_calendar_output(
    daily_store: dict[str, Any],
) -> dict[str, Any]:
    config = load_config()

    birth_date = config["birth_date"]
    awake_minutes = config["awake_minutes_per_day"]
    low_threshold = config["thresholds"]["low"]
    high_threshold = config["thresholds"]["high"]

    birth_week_start = get_week_start(birth_date)

    output_days: list[dict[str, Any]] = []
    grouped_days: dict[date, list[dict[str, Any]]] = defaultdict(list)

    for date_text, raw_record in sorted(
        daily_store["days"].items()
    ):
        try:
            current_date = date.fromisoformat(date_text)
            effective_minutes = int(
                raw_record["effective_minutes"]
            )
        except (ValueError, TypeError, KeyError):
            print(f"警告：忽略非法日期记录：{date_text}")
            continue

        effective_minutes = max(effective_minutes, 0)
        status = str(
            raw_record.get("status", "complete")
        ).strip()

        utilization = effective_minutes / awake_minutes

        day_record = {
            "date": date_text,
            "effective_minutes": effective_minutes,
            "denominator_minutes": awake_minutes,
            "utilization": round(utilization, 6),
            "level": get_level(
                utilization,
                low_threshold,
                high_threshold,
            ),
            "status": status,
        }

        output_days.append(day_record)

        week_start = get_week_start(current_date)
        grouped_days[week_start].append(day_record)

    output_weeks: list[dict[str, Any]] = []

    for week_start, week_days in sorted(grouped_days.items()):
        recorded_days = [
            day
            for day in week_days
            if day["status"] == "complete"
        ]

        if not recorded_days:
            continue

        life_week_index = (
            week_start - birth_week_start
        ).days // 7

        if not 0 <= life_week_index < TOTAL_LIFE_WEEKS:
            continue

        effective_minutes = sum(
            day["effective_minutes"]
            for day in recorded_days
        )

        denominator_minutes = (
            awake_minutes * len(recorded_days)
        )

        utilization = (
            effective_minutes / denominator_minutes
            if denominator_minutes > 0
            else 0.0
        )

        iso_year, iso_week, _ = week_start.isocalendar()

        output_weeks.append(
            {
                "life_week_index": life_week_index,
                "iso_week": f"{iso_year}年第{iso_week}周",
                "week_start": week_start.isoformat(),
                "week_end": (
                    week_start + timedelta(days=6)
                ).isoformat(),
                "effective_minutes": effective_minutes,
                "recorded_days": len(recorded_days),
                "denominator_minutes": denominator_minutes,
                "utilization": round(utilization, 6),
                "level": get_level(
                    utilization,
                    low_threshold,
                    high_threshold,
                ),
            }
        )

    return {
        "version": 1,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "config": {
            "birth_date": birth_date.isoformat(),
            "awake_minutes_per_day": awake_minutes,
            "thresholds": config["thresholds"],
            "colors": config["colors"],
        },
        "days": output_days,
        "weeks": output_weeks,
    }


def rebuild_life_calendar_json() -> None:
    daily_store = load_daily_store()
    output = build_calendar_output(daily_store)
    atomic_write_json(WEB_OUTPUT_PATH, output)

    print(
        f"已生成 Life Calendar 网页数据："
        f"{WEB_OUTPUT_PATH}"
    )


def update_life_calendar_day(
    target_date: str,
    effective_minutes: int,
    status: str = "complete",
) -> None:
    date.fromisoformat(target_date)

    if effective_minutes < 0:
        raise ValueError("effective_minutes 不能小于0")

    daily_store = load_daily_store()

    daily_store["days"][target_date] = {
        "effective_minutes": int(effective_minutes),
        "status": status,
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    atomic_write_json(DAILY_STORE_PATH, daily_store)
    rebuild_life_calendar_json()

    print(
        f"已更新 Life Calendar：{target_date}，"
        f"有效时间 {effective_minutes} 分钟"
    )


def update_life_calendar_from_items(
    target_date: str,
    items: list[dict],
) -> None:
    effective_minutes = sum(
        int(item["duration_min"])
        for item in items
        if item.get("time_type") == "effective"
    )

    update_life_calendar_day(
        target_date=target_date,
        effective_minutes=effective_minutes,
        status="complete",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="更新或重新生成Life Calendar数据"
    )

    parser.add_argument(
        "--date",
        default=None,
        help="测试日期，例如 2026-07-08",
    )
    parser.add_argument(
        "--effective-minutes",
        type=int,
        default=None,
        help="测试日期对应的有效时间分钟数",
    )

    args = parser.parse_args()

    has_date = args.date is not None
    has_minutes = args.effective_minutes is not None

    if has_date != has_minutes:
        parser.error(
            "--date 和 --effective-minutes 必须同时填写"
        )

    if has_date:
        update_life_calendar_day(
            target_date=args.date,
            effective_minutes=args.effective_minutes,
        )
    else:
        rebuild_life_calendar_json()


if __name__ == "__main__":
    main()