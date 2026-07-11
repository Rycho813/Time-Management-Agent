// 新增代码：web/app.js

"use strict";

const TOTAL_YEARS = 90;
const WEEKS_PER_ROW = 52;
const TOTAL_CELLS = TOTAL_YEARS * WEEKS_PER_ROW;

const calendarElement = document.getElementById("life-calendar");
const tooltipElement = document.getElementById("tooltip");
const modalElement = document.getElementById("week-modal");
const modalTitleElement = document.getElementById("week-detail-title");
const modalSummaryElement = document.getElementById("week-detail-summary");
const dayGridElement = document.getElementById("day-grid");

let calendarData = null;
let dayMap = new Map();
let weekMap = new Map();

function parseDate(dateText) {
    return new Date(`${dateText}T00:00:00Z`);
}

function formatDate(date) {
    return date.toISOString().slice(0, 10);
}

function addDays(dateText, days) {
    const result = parseDate(dateText);
    result.setUTCDate(result.getUTCDate() + days);
    return formatDate(result);
}

function formatMinutes(minutes) {
    const safeMinutes = Math.max(Number(minutes) || 0, 0);
    const hours = Math.floor(safeMinutes / 60);
    const remainingMinutes = safeMinutes % 60;

    if (hours > 0 && remainingMinutes > 0) {
        return `${hours}小时${remainingMinutes}分钟`;
    }

    if (hours > 0) {
        return `${hours}小时`;
    }

    return `${remainingMinutes}分钟`;
}

function formatPercent(value) {
    return `${((Number(value) || 0) * 100).toFixed(1)}%`;
}

function getLevelColor(level) {
    const colors = calendarData.config.colors;

    if (level === "low") {
        return colors.low;
    }

    if (level === "medium") {
        return colors.medium;
    }

    return colors.high;
}

function getFillHeight(utilization) {
    const percent = Math.min(Math.max(Number(utilization) || 0, 0), 1) * 100;

    // 已记录但利用率为0时，保留一条细线，以便与“无数据”区分。
    return percent === 0 ? 4 : percent;
}

function createFillElement(className, utilization, level) {
    const fillElement = document.createElement("div");
    fillElement.className = className;
    fillElement.style.height = `${getFillHeight(utilization)}%`;
    fillElement.style.backgroundColor = getLevelColor(level);
    return fillElement;
}

function showTooltip(event, html) {
    tooltipElement.innerHTML = html;
    tooltipElement.classList.remove("hidden");

    const offset = 14;
    tooltipElement.style.left = `${event.clientX + offset}px`;
    tooltipElement.style.top = `${event.clientY + offset}px`;
}

function hideTooltip() {
    tooltipElement.classList.add("hidden");
}

function getMonday(date) {
    const result = new Date(date);
    const weekday = result.getUTCDay();
    const distance = weekday === 0 ? -6 : 1 - weekday;
    result.setUTCDate(result.getUTCDate() + distance);
    return result;
}

function calculateCurrentLifeWeekIndex() {
    const birthDate = parseDate(calendarData.config.birth_date);
    const birthWeekStart = getMonday(birthDate);
    const currentWeekStart = getMonday(new Date());

    return Math.floor(
        (currentWeekStart.getTime() - birthWeekStart.getTime())
        / (7 * 24 * 60 * 60 * 1000)
    );
}

function buildWeekTooltip(week) {
    const lifeYearIndex = Math.floor(
        week.life_week_index / WEEKS_PER_ROW
    ); // 修改后

    const weekInLifeYear =
        week.life_week_index % WEEKS_PER_ROW + 1; // 修改后

    return `
        <strong>${week.iso_week}（公历周）</strong><br>
        人生第 ${week.life_week_index + 1} 周<br>
        ${week.week_start} ～ ${week.week_end}<br>
        平均有效时间：${formatMinutes(
            week.average_effective_minutes
        )}<br>
        平均利用率：${formatPercent(
            week.average_utilization
        )}<br>
        有效记录：${week.recorded_days}/7天
    `;
}

function buildDayTooltip(day) {
    return `
        <strong>${day.date}</strong><br>
        有效时间：${formatMinutes(day.effective_minutes)}<br>
        清醒时间上限：${formatMinutes(day.denominator_minutes)}<br>
        利用率：${formatPercent(day.utilization)}
    `;
}

function renderCalendar() {
    calendarElement.replaceChildren();

    const currentLifeWeekIndex = calculateCurrentLifeWeekIndex();

    for (let yearIndex = 0; yearIndex < TOTAL_YEARS; yearIndex += 1) {
        const yearRow = document.createElement("div");
        yearRow.className = "year-row";

        const yearLabel = document.createElement("div");
        yearLabel.className = "year-label";
        yearLabel.textContent = yearIndex;

        const weekRow = document.createElement("div");
        weekRow.className = "week-row";

        for (let weekColumn = 0; weekColumn < WEEKS_PER_ROW; weekColumn += 1) {
            const lifeWeekIndex =
                yearIndex * WEEKS_PER_ROW + weekColumn;

            const cell = document.createElement("div");
            cell.className = "week-cell";

            if (lifeWeekIndex === currentLifeWeekIndex) {
                cell.classList.add("current-week");
            }

            const week = weekMap.get(lifeWeekIndex);

            if (week) {
                cell.classList.add("has-data");
                cell.appendChild(
                    createFillElement(
                        "week-fill",
                        week.average_utilization,
                        week.level
                    )
                );

                cell.addEventListener("mousemove", (event) => {
                    showTooltip(event, buildWeekTooltip(week));
                });

                cell.addEventListener("mouseleave", hideTooltip);
                cell.addEventListener("click", () => openWeekModal(week));
            }

            weekRow.appendChild(cell);
        }

        yearRow.append(yearLabel, weekRow);
        calendarElement.appendChild(yearRow);
    }
}

function openWeekModal(week) {
    modalTitleElement.textContent = `${week.iso_week}｜一周详情`;

    modalSummaryElement.textContent =
        `${week.week_start} ～ ${week.week_end}；` +
        `平均有效时间 ${formatMinutes(week.average_effective_minutes)}；` +
        `平均利用率 ${formatPercent(week.average_utilization)}。` + `有效记录 ${week.recorded_days}/7天。`;

    dayGridElement.replaceChildren();

    const weekdayNames = [
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
        "星期日",
    ];

    for (let dayOffset = 0; dayOffset < 7; dayOffset += 1) {
        const dateText = addDays(week.week_start, dayOffset);
        const day = dayMap.get(dateText);

        const card = document.createElement("div");
        card.className = "day-card";

        const dayName = document.createElement("div");
        dayName.className = "day-name";
        dayName.textContent = weekdayNames[dayOffset];

        const dayDate = document.createElement("div");
        dayDate.className = "day-date";
        dayDate.textContent = dateText.slice(5);

        const dayCell = document.createElement("div");
        dayCell.className = "day-cell";

        if (day) {
            dayCell.classList.add("has-data");
            dayCell.appendChild(
                createFillElement(
                    "day-fill",
                    day.utilization,
                    day.level
                )
            );

            dayCell.addEventListener("mousemove", (event) => {
                showTooltip(event, buildDayTooltip(day));
            });

            dayCell.addEventListener("mouseleave", hideTooltip);
        } else {
            dayCell.classList.add("no-data");
        }

        card.append(dayName, dayDate, dayCell);
        dayGridElement.appendChild(card);
    }

    modalElement.classList.remove("hidden");
}

function closeWeekModal() {
    modalElement.classList.add("hidden");
    hideTooltip();
}

async function loadCalendarData() {
    try {
        const response = await fetch(
            `./data/life_calendar.json?time=${Date.now()}`
        );

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        calendarData = await response.json();

        dayMap = new Map(
            calendarData.days.map((day) => [day.date, day])
        );

        weekMap = new Map(
            calendarData.weeks.map((week) => [
                week.life_week_index,
                week,
            ])
        );

        renderCalendar();
    } catch (error) {
        calendarElement.textContent =
            `Life Calendar数据加载失败：${error.message}`;
        console.error(error);
    }
}

document
    .getElementById("close-modal")
    .addEventListener("click", closeWeekModal);

document
    .querySelector("[data-close-modal]")
    .addEventListener("click", closeWeekModal);

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
        closeWeekModal();
    }
});

loadCalendarData();