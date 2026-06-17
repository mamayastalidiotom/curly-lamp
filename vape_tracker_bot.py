import logging
import json
import os
import httpx
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

MOSCOW = ZoneInfo("Europe/Moscow")

# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DATA_FILE = "tracker_data.json"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

(
    WAITING_PUFF_COUNT,
    WAITING_PACK_DATE,
    WAITING_CIGARETTE_COUNT,
    WAITING_PUFFS_ADD,
    WAITING_LIMIT,
    WAITING_CUSTOM_TRIGGER,
    WAITING_STATUS,
) = range(7)

# Базовые триггеры — причины почему закурила
DEFAULT_TRIGGERS = [
    "😰 Стресс",
    "😴 Скука",
    "🍷 Алкоголь",
    "👥 Социализация",
    "☕ Кофе/чай",
    "🍽 После еды",
    "🤖 Привычка",
    "😟 Тревога",
    "🌙 Не могу уснуть",
    "🔥 Секс",
]


# ─────────────────────────────────────────────
# Данные
# ─────────────────────────────────────────────
def load_data(user_id: int) -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        all_data = json.load(f)
    return all_data.get(str(user_id), {})


def save_data(user_id: int, data: dict):
    all_data = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            all_data = json.load(f)
    all_data[str(user_id)] = data
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)


def load_all_users() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def new_vape_data() -> dict:
    return {
        "type": "vape",
        "status": None,          # "smoking" | "reducing" | "quitting"
        "status_set_at": None,   # когда установлен (для подсчёта дней без курения)
        "fact_sent_date": None,  # когда последний раз отправляли факт
        "total_puffs": 0,
        "today_puffs": 0,
        "today_date": str(date.today()),
        "devices_used": 0,
        "current_device": {"total_puffs": None, "used_puffs": 0, "bought_at": None},
        "last_puff_at": None,
        "history": [],
        "daily_history": {},
        "events": {},
        "custom_triggers": [],
        "daily_limit": None,
        "limit_warned": False,
    }


def new_cig_data() -> dict:
    return {
        "type": "cigarettes",
        "status": None,
        "status_set_at": None,
        "fact_sent_date": None,
        "total_cigarettes": 0,
        "today_cigarettes": 0,
        "today_date": str(date.today()),
        "packs_used": 0,
        "current_pack": {"bought_at": None, "cigarettes_used": 0},
        "last_smoke_at": None,
        "history": [],
        "daily_history": {},
        "events": {},
        "custom_triggers": [],
        "daily_limit": None,
        "limit_warned": False,
    }


def reset_today(data: dict) -> dict:
    today = str(date.today())
    if data.get("today_date") != today:
        # Сохраняем вчерашние данные в историю
        yesterday = data.get("today_date")
        if yesterday:
            if data["type"] == "vape":
                data["daily_history"][yesterday] = data.get("today_puffs", 0)
                data["today_puffs"] = 0
            else:
                data["daily_history"][yesterday] = data.get("today_cigarettes", 0)
                data["today_cigarettes"] = 0
        data["today_date"] = today
        data["limit_warned"] = False
    return data


def get_today_count(data: dict) -> int:
    return data.get("today_puffs", 0) if data["type"] == "vape" else data.get("today_cigarettes", 0)


def get_yesterday_count(data: dict) -> int:
    yesterday = str(date.today() - timedelta(days=1))
    return data.get("daily_history", {}).get(yesterday, 0)


def get_week_stats(data: dict) -> dict:
    history = data.get("daily_history", {})
    today = date.today()
    days = {}
    for i in range(1, 8):
        d = str(today - timedelta(days=i))
        days[d] = history.get(d, 0)
    total = sum(days.values())
    max_day = max(days, key=days.get) if days else None
    avg = total // 7
    return {"total": total, "max_day": max_day, "max_val": days.get(max_day, 0), "avg": avg}


def get_month_stats(data: dict) -> dict:
    history = data.get("daily_history", {})
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1))
    month_str = last_month.strftime("%Y-%m")
    days = {k: v for k, v in history.items() if k.startswith(month_str)}
    total = sum(days.values())
    max_day = max(days, key=days.get) if days else None
    min_day = min(days, key=days.get) if days else None
    return {
        "total": total,
        "max_day": max_day,
        "max_val": days.get(max_day, 0),
        "min_day": min_day,
        "min_val": days.get(min_day, 0),
        "month_name": last_month.strftime("%B"),
    }


def record_event(data: dict, count: int):
    """Записывает затяжку/сигарету с точным временем — нужно для сравнения с прошлым днём"""
    today_str = str(date.today())
    now_str = datetime.now().strftime("%H:%M:%S")
    if "events" not in data:
        data["events"] = {}
    data["events"].setdefault(today_str, []).append({"time": now_str, "count": count})
    # Чистим события старше 32 дней — нужны для месячной статистики
    cutoff = str(date.today() - timedelta(days=32))
    data["events"] = {d: v for d, v in data["events"].items() if d >= cutoff}


def get_count_until(data: dict, date_str: str, time_str: str) -> int:
    """Сколько затяжек/сигарет было записано в date_str до времени time_str включительно"""
    events = data.get("events", {}).get(date_str, [])
    return sum(e["count"] for e in events if e["time"] <= time_str)


def get_top_triggers(data: dict, date_strings: list, top_n: int = 3) -> str:
    """Считает топ триггеров за список дат и возвращает строку для вставки в промпт"""
    counts = {}
    for d in date_strings:
        for event in data.get("events", {}).get(d, []):
            for trig in event.get("triggers", []):
                counts[trig] = counts.get(trig, 0) + event.get("count", 1)
    if not counts:
        return ""
    sorted_trigs = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    parts = [f"{t} ({c} раз)" for t, c in sorted_trigs]
    return "Основные причины: " + ", ".join(parts)


def build_trigger_keyboard(data: dict, selected: set, all_triggers: list = None):
    """Клавиатура с триггерами — стандартные + кастомные пользователя, с отметками выбранных"""
    if all_triggers is None:
        all_triggers = DEFAULT_TRIGGERS + data.get("custom_triggers", [])

    buttons = []
    row = []
    for i, trig in enumerate(all_triggers):
        label = f"✅ {trig}" if trig in selected else trig
        row.append(InlineKeyboardButton(label, callback_data=f"trig_{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("✏️ Свой вариант", callback_data="trig_custom")])
    buttons.append([InlineKeyboardButton("✅ Готово", callback_data="trig_done")])
    return InlineKeyboardMarkup(buttons), all_triggers


# ─────────────────────────────────────────────
# Claude API
# ─────────────────────────────────────────────
async def ask_claude(prompt: str) -> str | None:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=15,
            )
            result = resp.json()
            return result["content"][0]["text"]
    except Exception as e:
        logging.error(f"Claude API error: {e}")
        return None


async def generate_morning_message(data: dict) -> str:
    unit = "затяжек" if data["type"] == "vape" else "сигарет"
    yesterday = get_yesterday_count(data)
    yesterday_str = str(date.today() - timedelta(days=1))
    day_before = data.get("daily_history", {}).get(str(date.today() - timedelta(days=2)), 0)
    diff = yesterday - day_before
    diff_str = f"на {abs(diff)} {'больше' if diff > 0 else 'меньше'} чем позавчера" if day_before else ""
    triggers_str = get_top_triggers(data, [yesterday_str], top_n=3)

    prompt = (
        f"Напиши короткое утреннее сообщение (2-3 предложения) для трекера курения. "
        f"Вчера было {yesterday} {unit} {diff_str}. "
        f"{triggers_str}. "
        f"Тон дружелюбный, немного с юмором. Без хэштегов. Не повторяй шаблонные фразы."
    )
    result = await ask_claude(prompt)
    if result:
        return result
    # Простой fallback без Claude
    parts = [f"Вчера: {yesterday} {unit}"]
    if diff_str:
        parts.append(diff_str)
    if triggers_str:
        parts.append(triggers_str)
    return " | ".join(parts)


async def generate_weekly_message(data: dict) -> str:
    unit = "затяжек" if data["type"] == "vape" else "сигарет"
    stats = get_week_stats(data)
    max_day_fmt = datetime.strptime(stats["max_day"], "%Y-%m-%d").strftime("%A") if stats["max_day"] else "—"
    week_dates = [str(date.today() - timedelta(days=i)) for i in range(1, 8)]
    triggers_str = get_top_triggers(data, week_dates, top_n=3)

    prompt = (
        f"Напиши короткий еженедельный отчёт (3-4 предложения) для трекера курения. "
        f"За неделю: {stats['total']} {unit}, самый активный день — {max_day_fmt} ({stats['max_val']} {unit}), "
        f"среднее в день: {stats['avg']}. {triggers_str}. Тон дружелюбный. Без хэштегов."
    )
    result = await ask_claude(prompt)
    if result:
        return result
    return (f"За неделю: {stats['total']} {unit}. "
            f"Самый активный день: {max_day_fmt} ({stats['max_val']}). "
            f"Среднее: {stats['avg']}/день. {triggers_str}")


async def generate_monthly_message(data: dict) -> str:
    unit = "затяжек" if data["type"] == "vape" else "сигарет"
    stats = get_month_stats(data)
    max_day_fmt = datetime.strptime(stats["max_day"], "%Y-%m-%d").strftime("%d %B") if stats["max_day"] else "—"
    min_day_fmt = datetime.strptime(stats["min_day"], "%Y-%m-%d").strftime("%d %B") if stats["min_day"] else "—"
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1))
    month_str = last_month.strftime("%Y-%m")
    month_dates = [k for k in data.get("events", {}) if k.startswith(month_str)]
    triggers_str = get_top_triggers(data, month_dates, top_n=3)

    prompt = (
        f"Напиши короткий ежемесячный отчёт (3-4 предложения) для трекера курения за {stats['month_name']}. "
        f"Всего: {stats['total']} {unit}, рекордный день — {max_day_fmt} ({stats['max_val']}), "
        f"лучший день — {min_day_fmt} ({stats['min_val']}). {triggers_str}. Тон поддерживающий. Без хэштегов."
    )
    result = await ask_claude(prompt)
    if result:
        return result
    return (f"За {stats['month_name']}: {stats['total']} {unit}. "
            f"Рекордный день: {max_day_fmt} ({stats['max_val']}). "
            f"Лучший день: {min_day_fmt} ({stats['min_val']}). {triggers_str}")


async def generate_limit_warning(data: dict, percent: int) -> str:
    unit = "затяжек" if data["type"] == "vape" else "сигарет"
    limit = data["daily_limit"]
    current = get_today_count(data)
    prompt = (
        f"Напиши короткое предупреждение (1-2 предложения) — человек {'достиг' if percent == 100 else 'приближается к'} "
        f"дневному лимиту курения. Лимит: {limit} {unit}, сейчас: {current}. "
        f"Тон {'мягкий, но серьёзный' if percent == 100 else 'предупредительный'}. Без хэштегов."
    )
    result = await ask_claude(prompt)
    if result:
        return result
    if percent >= 100:
        return f"Лимит {limit} {unit} исчерпан. Сегодня уже {current}."
    return f"Осторожно — уже {current} из {limit} {unit} на сегодня."


async def generate_pace_message(data: dict, diff: int, percent: int) -> str:
    unit = "затяжек" if data["type"] == "vape" else "сигарет"
    prompt = (
        f"Напиши короткое сообщение (1-2 предложения) для трекера курения. "
        f"К этому моменту дня уже на {diff} {unit} больше чем вчера в это же время "
        f"(это на {percent}% больше). Тон лёгкий, без морализаторства, можно с долей иронии. Без хэштегов."
    )
    result = await ask_claude(prompt)
    if result:
        return result
    return f"К этому времени уже на {diff} {unit} больше чем вчера ({percent}% сверх нормы)."


# ─────────────────────────────────────────────
# Клавиатуры
# ─────────────────────────────────────────────
def status_inline():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚬 Курю", callback_data="status_smoking"),
        InlineKeyboardButton("📉 Снижаю", callback_data="status_reducing"),
        InlineKeyboardButton("🏁 Бросаю", callback_data="status_quitting"),
    ]])


def vape_keyboard():
    return ReplyKeyboardMarkup([
        ["💨 Затяжка", "🔋 Новая электронка"],
        ["📊 Статистика", "⏱ Последняя затяжка"],
        ["🎯 Лимит дня", "⚙️ Настройки"],
        ["🔄 Сменить режим"],
    ], resize_keyboard=True)


def cig_keyboard():
    return ReplyKeyboardMarkup([
        ["🚬 Покурила", "📦 Новая пачка"],
        ["📊 Статистика", "⏱ Последний раз"],
        ["🎯 Лимит дня", "⚙️ Настройки"],
        ["🔄 Сменить режим"],
    ], resize_keyboard=True)


def type_inline():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💨 Электронка", callback_data="type_vape"),
        InlineKeyboardButton("🚬 Сигареты", callback_data="type_cig"),
    ]])


def stats_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_stats")],
        [InlineKeyboardButton("🗑 Сбросить всё", callback_data="confirm_reset")],
    ])


# ─────────────────────────────────────────────
# Утилиты
# ─────────────────────────────────────────────
def time_ago(iso_string: str) -> str:
    if not iso_string:
        return "нет данных"
    diff = datetime.now() - datetime.fromisoformat(iso_string)
    s = int(diff.total_seconds())
    if s < 60:
        return f"{s} сек назад"
    elif s < 3600:
        return f"{s // 60} мин назад"
    elif s < 86400:
        return f"{s // 3600} ч {(s % 3600) // 60} мин назад"
    else:
        return f"{s // 86400} дн назад"


def vape_stats(data: dict) -> str:
    dev = data["current_device"]
    limit = data.get("daily_limit")
    limit_str = f"\n🎯 Лимит дня: *{data['today_puffs']} / {limit}*" if limit else ""

    if dev["total_puffs"] is not None:
        used, total = dev["used_puffs"], dev["total_puffs"]
        remaining = max(0, total - used)
        percent = min(100, int(used / total * 100)) if total > 0 else 0
        bar = "🟩" * (percent // 10) + "⬜" * (10 - percent // 10)
        warn = "\n⚠️ Скоро закончится!" if 0 < remaining <= 20 else ""
        if remaining == 0:
            warn = "\n⚠️ Электронка на нуле! Пора менять 🔁"
        device_info = f"\n🔋 *Текущая электронка*\n{bar} {percent}%\nЗатяжек: {used} / {total}\nОсталось: ~{remaining}{warn}\n"
        if dev["bought_at"]:
            device_info += f"Куплена: {time_ago(dev['bought_at'])}\n"
    else:
        device_info = "\n🔋 Электронка не указана — нажми «Новая электронка»\n"

    return (
        f"📊 *Статистика — Электронка*\n\n"
        f"💨 Сегодня: *{data['today_puffs']} затяжек*\n"
        f"📅 За всё время: *{data['total_puffs']} затяжек*\n"
        f"🔁 Электронок куплено: *{data['devices_used']}*\n"
        f"⏱ Последняя затяжка: {time_ago(data['last_puff_at'])}"
        f"{limit_str}\n"
        f"{device_info}"
    )


def cig_stats(data: dict) -> str:
    pack = data["current_pack"]
    limit = data.get("daily_limit")
    limit_str = f"\n🎯 Лимит дня: *{data['today_cigarettes']} / {limit}*" if limit else ""

    pack_info = (
        f"\n📦 *Текущая пачка*\nСигарет выкурено: {pack['cigarettes_used']}\nКуплена: {time_ago(pack['bought_at'])}\n"
        if pack["bought_at"] else "\n📦 Пачка не указана — нажми «Новая пачка»\n"
    )
    return (
        f"📊 *Статистика — Сигареты*\n\n"
        f"🚬 Сегодня: *{data['today_cigarettes']} сигарет*\n"
        f"📅 За всё время: *{data['total_cigarettes']} сигарет*\n"
        f"📦 Пачек куплено: *{data['packs_used']}*\n"
        f"⏱ Последний раз: {time_ago(data['last_smoke_at'])}"
        f"{limit_str}\n"
        f"{pack_info}"
    )


# ─────────────────────────────────────────────
# Проверка лимита
# ─────────────────────────────────────────────
async def check_limit(user_id: int, data: dict, context: ContextTypes.DEFAULT_TYPE):
    limit = data.get("daily_limit")
    if not limit:
        return
    current = get_today_count(data)
    percent = int(current / limit * 100)

    if percent >= 100 and not data.get("limit_warned"):
        data["limit_warned"] = True
        save_data(user_id, data)
        msg = await generate_limit_warning(data, 100)
        await context.bot.send_message(chat_id=user_id, text=f"🚫 {msg}")
    elif percent >= 80 and not data.get("limit_warned"):
        data["limit_warned"] = True
        save_data(user_id, data)
        msg = await generate_limit_warning(data, 80)
        await context.bot.send_message(chat_id=user_id, text=f"⚠️ {msg}")


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\nМалыш, что курим?",
        reply_markup=type_inline(),
    )


async def choose_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "type_vape":
        data = load_data(user_id)
        if data.get("type") != "vape":
            data = new_vape_data()
            save_data(user_id, data)
        await query.edit_message_text("💨 Режим электронки включён!")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Нажми *«Новая электронка»* чтобы начать 👇",
            parse_mode="Markdown",
            reply_markup=vape_keyboard(),
        )
    elif query.data == "type_cig":
        data = load_data(user_id)
        if data.get("type") != "cigarettes":
            data = new_cig_data()
            save_data(user_id, data)
        await query.edit_message_text("🚬 Режим сигарет включён!")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Нажми *«Новая пачка»* чтобы начать 👇",
            parse_mode="Markdown",
            reply_markup=cig_keyboard(),
        )


async def manual_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_data(user_id)
    if not data:
        await update.message.reply_text("Сначала выбери режим через /start")
        return
    await update.message.reply_text("Готовлю отчёт... ⏳")
    msg = await generate_morning_message(data)
    await update.message.reply_text(f"🌅 {msg}")


# ─────────────────────────────────────────────
# ЭЛЕКТРОНКА — затяжка
# ─────────────────────────────────────────────
async def switch_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Малыш, что курим?", reply_markup=type_inline())
    await update.message.reply_text("💨 Сколько затяжек сделала?\nВведи число:")
    return WAITING_PUFFS_ADD


async def vape_puff_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Введи просто число, например: 3")
        return WAITING_PUFFS_ADD

    count = int(text)
    data = load_data(user_id)
    data = reset_today(data)
    data["total_puffs"] += count
    data["today_puffs"] += count
    data["last_puff_at"] = datetime.now().isoformat()
    data["current_device"]["used_puffs"] += count
    record_event(data, count)
    save_data(user_id, data)

    dev = data["current_device"]
    msg = f"💨 +{count} затяжек (сегодня: {data['today_puffs']})\n"
    if dev["total_puffs"] is not None:
        remaining = max(0, dev["total_puffs"] - dev["used_puffs"])
        msg += f"🔋 Осталось: ~{remaining} затяжек"
        if remaining <= 0:
            msg += "\n⚠️ Электронка на нуле! Пора менять 🔁"
        elif remaining <= 20:
            msg += "\n⚠️ Скоро закончится!"

    await update.message.reply_text(msg, reply_markup=vape_keyboard())
    await check_limit(user_id, data, context)

    # Спрашиваем причину
    today_str = str(date.today())
    context.user_data["trigger_event_date"] = today_str
    context.user_data["trigger_event_index"] = len(data["events"][today_str]) - 1
    context.user_data["trigger_selected"] = set()
    context.user_data["awaiting_custom_trigger"] = False
    kb, all_triggers = build_trigger_keyboard(data, set())
    context.user_data["trigger_list"] = all_triggers
    await update.message.reply_text("Почему закурила? Можно выбрать несколько 👇", reply_markup=kb)
    return ConversationHandler.END


# ─────────────────────────────────────────────
# ЭЛЕКТРОНКА — новая
# ─────────────────────────────────────────────
async def new_device_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔋 *Новая электронка!*\n\nСколько затяжек на упаковке?\nНапример: *4000*",
        parse_mode="Markdown",
    )
    return WAITING_PUFF_COUNT


async def new_device_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Введи просто число, например: 4000")
        return WAITING_PUFF_COUNT

    puff_count = int(text)
    data = load_data(user_id)
    old = data["current_device"]
    if old["bought_at"]:
        data["history"].append({**old, "ended_at": datetime.now().isoformat()})

    data["devices_used"] += 1
    data["current_device"] = {"total_puffs": puff_count, "used_puffs": 0, "bought_at": datetime.now().isoformat()}
    save_data(user_id, data)

    await update.message.reply_text(
        f"✅ Принято! Новая электронка на *{puff_count} затяжек*\nБуду считать остаток 😌",
        parse_mode="Markdown",
        reply_markup=vape_keyboard(),
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────
# СИГАРЕТЫ — покурила
# ─────────────────────────────────────────────
async def cig_smoke_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚬 Сколько сигарет выкурила?\nВведи число:")
    return WAITING_CIGARETTE_COUNT


async def cig_smoke_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Введи просто число, например: 2")
        return WAITING_CIGARETTE_COUNT

    count = int(text)
    data = load_data(user_id)
    data = reset_today(data)
    data["total_cigarettes"] += count
    data["today_cigarettes"] += count
    data["last_smoke_at"] = datetime.now().isoformat()
    data["current_pack"]["cigarettes_used"] += count
    record_event(data, count)
    save_data(user_id, data)

    msg = f"🚬 +{count} сигарет (сегодня: {data['today_cigarettes']})\n📦 Из этой пачки: {data['current_pack']['cigarettes_used']} шт"
    await update.message.reply_text(msg, reply_markup=cig_keyboard())
    await check_limit(user_id, data, context)

    # Спрашиваем причину
    today_str = str(date.today())
    context.user_data["trigger_event_date"] = today_str
    context.user_data["trigger_event_index"] = len(data["events"][today_str]) - 1
    context.user_data["trigger_selected"] = set()
    context.user_data["awaiting_custom_trigger"] = False
    kb, all_triggers = build_trigger_keyboard(data, set())
    context.user_data["trigger_list"] = all_triggers
    await update.message.reply_text("Почему закурила? Можно выбрать несколько 👇", reply_markup=kb)
    return ConversationHandler.END


# ─────────────────────────────────────────────
# СИГАРЕТЫ — новая пачка
# ─────────────────────────────────────────────
async def new_pack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📦 *Новая пачка!*\n\nКогда купила? Напиши дату или просто *сегодня*",
        parse_mode="Markdown",
    )
    return WAITING_PACK_DATE


async def new_pack_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    data = load_data(user_id)
    old = data["current_pack"]
    if old["bought_at"]:
        data["history"].append({**old, "ended_at": datetime.now().isoformat()})

    data["packs_used"] += 1
    data["current_pack"] = {"bought_at": datetime.now().isoformat(), "cigarettes_used": 0, "bought_date_text": text}
    save_data(user_id, data)

    await update.message.reply_text(f"✅ Новая пачка куплена ({text})!\nСчитаем сигареты 🚬", reply_markup=cig_keyboard())
    return ConversationHandler.END


# ─────────────────────────────────────────────
# ЛИМИТ ДНЯ
# ─────────────────────────────────────────────
async def limit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_data(user_id)
    unit = "затяжек" if data.get("type") == "vape" else "сигарет"
    current_limit = data.get("daily_limit")
    current_str = f"Текущий лимит: *{current_limit} {unit}*\n\n" if current_limit else ""

    await update.message.reply_text(
        f"🎯 *Лимит дня*\n\n{current_str}"
        f"Сколько максимум {unit} в день?\nВведи число (или 0 чтобы убрать лимит):",
        parse_mode="Markdown",
    )
    return WAITING_LIMIT


async def limit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Введи просто число, например: 200")
        return WAITING_LIMIT

    limit = int(text)
    data = load_data(user_id)
    unit = "затяжек" if data.get("type") == "vape" else "сигарет"
    kb = vape_keyboard() if data.get("type") == "vape" else cig_keyboard()

    if limit == 0:
        data["daily_limit"] = None
        save_data(user_id, data)
        await update.message.reply_text("✅ Лимит убран", reply_markup=kb)
    else:
        data["daily_limit"] = limit
        data["limit_warned"] = False
        save_data(user_id, data)
        await update.message.reply_text(
            f"✅ Лимит установлен: *{limit} {unit}* в день\nПредупрежу когда будет 80% и 100% 🎯",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    return ConversationHandler.END


# ─────────────────────────────────────────────
# Статистика и последний раз
# ─────────────────────────────────────────────
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_data(user_id)
    if not data:
        await update.message.reply_text("Сначала выбери режим через /start")
        return
    data = reset_today(data)
    save_data(user_id, data)
    text = vape_stats(data) if data["type"] == "vape" else cig_stats(data)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=stats_inline())


async def last_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_data(user_id)
    if not data:
        await update.message.reply_text("Сначала выбери режим через /start")
        return
    last = data.get("last_puff_at") if data["type"] == "vape" else data.get("last_smoke_at")
    label = "затяжка" if data["type"] == "vape" else "сигарета"
    if not last:
        await update.message.reply_text(f"Ещё не было ни одной {label} 🌿")
        return
    dt = datetime.fromisoformat(last)
    await update.message.reply_text(
        f"⏱ Последняя {label} в *{dt.strftime('%H:%M')}*\n— {time_ago(last)}",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# Триггеры — обработка кнопок
# ─────────────────────────────────────────────
async def trigger_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict, idx: int):
    query = update.callback_query
    all_triggers = context.user_data.get("trigger_list") or (DEFAULT_TRIGGERS + data.get("custom_triggers", []))
    if idx >= len(all_triggers):
        return
    trig = all_triggers[idx]
    selected = context.user_data.get("trigger_selected", set())
    if trig in selected:
        selected.discard(trig)
    else:
        selected.add(trig)
    context.user_data["trigger_selected"] = selected
    kb, _ = build_trigger_keyboard(data, selected, all_triggers)
    await query.edit_message_reply_markup(reply_markup=kb)


async def trigger_done(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    user_id = update.effective_user.id
    selected = context.user_data.get("trigger_selected", set())
    date_str = context.user_data.get("trigger_event_date")
    idx = context.user_data.get("trigger_event_index")

    if date_str is not None and idx is not None:
        events = data.get("events", {}).get(date_str, [])
        if 0 <= idx < len(events):
            events[idx]["triggers"] = sorted(selected)
            save_data(user_id, data)

    context.user_data.pop("trigger_selected", None)
    context.user_data.pop("trigger_event_date", None)
    context.user_data.pop("trigger_event_index", None)
    context.user_data.pop("trigger_list", None)

    if selected:
        await query.edit_message_text("Записала: " + ", ".join(sorted(selected)))
    else:
        await query.edit_message_text("Без причины — ок 🌿")


async def trigger_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✏️ Напиши свою причину (можно своими словами):")
    return WAITING_CUSTOM_TRIGGER


async def trigger_custom_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    data = load_data(user_id)

    custom = data.setdefault("custom_triggers", [])
    if text not in custom and text not in DEFAULT_TRIGGERS:
        custom.append(text)
        if len(custom) > 15:
            custom.pop(0)
    save_data(user_id, data)

    selected = context.user_data.get("trigger_selected", set())
    selected.add(text)
    context.user_data["trigger_selected"] = selected

    kb, all_triggers = build_trigger_keyboard(data, selected)
    context.user_data["trigger_list"] = all_triggers
    await update.message.reply_text(
        f"✅ Добавила «{text}»\nПочему закурила? Можно выбрать несколько 👇",
        reply_markup=kb,
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────
# Inline-кнопки
# ─────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = load_data(user_id)

    if query.data in ("type_vape", "type_cig"):
        await choose_type_callback(update, context)
        return

    if query.data.startswith("trig_"):
        suffix = query.data[len("trig_"):]
        if suffix == "done":
            await trigger_done(update, context, data)
        elif suffix.isdigit():
            await trigger_toggle(update, context, data, int(suffix))
        return

    if query.data == "refresh_stats":
        data = reset_today(data)
        save_data(user_id, data)
        text = vape_stats(data) if data["type"] == "vape" else cig_stats(data)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=stats_inline())

    elif query.data == "confirm_reset":
        await query.edit_message_text(
            "⚠️ Точно сбросить *всю* статистику?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Да", callback_data="do_reset"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_reset"),
            ]]),
        )
    elif query.data == "do_reset":
        save_data(user_id, {})
        await query.edit_message_text("🗑 Сброшено. Напиши /start чтобы начать заново.")
    elif query.data == "cancel_reset":
        data = reset_today(data)
        text = vape_stats(data) if data["type"] == "vape" else cig_stats(data)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=stats_inline())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_data(user_id)
    kb = vape_keyboard() if data.get("type") == "vape" else cig_keyboard()
    await update.message.reply_text("Отмена.", reply_markup=kb)
    return ConversationHandler.END


# ─────────────────────────────────────────────
# Расписание
# ─────────────────────────────────────────────
async def send_morning_reports(context: ContextTypes.DEFAULT_TYPE):
    all_users = load_all_users()
    for user_id_str, data in all_users.items():
        if not data:
            continue
        try:
            msg = await generate_morning_message(data)
            await context.bot.send_message(chat_id=int(user_id_str), text=f"🌅 {msg}")
        except Exception as e:
            logging.error(f"Morning report error for {user_id_str}: {e}")


async def send_weekly_reports(context: ContextTypes.DEFAULT_TYPE):
    if datetime.now().weekday() != 0:  # Только в понедельник
        return
    all_users = load_all_users()
    for user_id_str, data in all_users.items():
        if not data:
            continue
        try:
            msg = await generate_weekly_message(data)
            await context.bot.send_message(chat_id=int(user_id_str), text=f"📆 {msg}")
        except Exception as e:
            logging.error(f"Weekly report error for {user_id_str}: {e}")


async def send_monthly_reports(context: ContextTypes.DEFAULT_TYPE):
    if datetime.now().day != 1:  # Только 1-го числа
        return
    all_users = load_all_users()
    for user_id_str, data in all_users.items():
        if not data:
            continue
        try:
            msg = await generate_monthly_message(data)
            await context.bot.send_message(chat_id=int(user_id_str), text=f"🗓 {msg}")
        except Exception as e:
            logging.error(f"Monthly report error for {user_id_str}: {e}")


async def send_pace_checks(context: ContextTypes.DEFAULT_TYPE):
    """Сравнивает сколько накурено к этому моменту сегодня и сколько было вчера к этому же времени"""
    now_str = datetime.now().strftime("%H:%M:%S")
    today_str = str(date.today())
    yesterday_str = str(date.today() - timedelta(days=1))

    all_users = load_all_users()
    for user_id_str, data in all_users.items():
        if not data:
            continue
        try:
            today_count = get_count_until(data, today_str, now_str)
            yesterday_count = get_count_until(data, yesterday_str, now_str)
            if yesterday_count == 0:
                continue
            diff = today_count - yesterday_count
            percent = int(diff / yesterday_count * 100)
            if percent >= 20:
                msg = await generate_pace_message(data, diff, percent)
                await context.bot.send_message(chat_id=int(user_id_str), text=f"📈 {msg}")
        except Exception as e:
            logging.error(f"Pace check error for {user_id_str}: {e}")


# ─────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    puff_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💨 Затяжка$"), vape_puff_start)],
        states={WAITING_PUFFS_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, vape_puff_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    device_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔋 Новая электронка$"), new_device_start)],
        states={WAITING_PUFF_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_device_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    cig_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🚬 Покурила$"), cig_smoke_start)],
        states={WAITING_CIGARETTE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cig_smoke_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    pack_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📦 Новая пачка$"), new_pack_start)],
        states={WAITING_PACK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_pack_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    limit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🎯 Лимит дня$"), limit_start)],
        states={WAITING_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, limit_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    trigger_custom_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(trigger_custom_start, pattern="^trig_custom$")],
        states={WAITING_CUSTOM_TRIGGER: [MessageHandler(filters.TEXT & ~filters.COMMAND, trigger_custom_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", manual_report))
    app.add_handler(puff_conv)
    app.add_handler(device_conv)
    app.add_handler(cig_conv)
    app.add_handler(pack_conv)
    app.add_handler(limit_conv)
    app.add_handler(trigger_custom_conv)
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Regex("^📊 Статистика$"), show_stats))
    app.add_handler(MessageHandler(filters.Regex("^⏱ (Последняя затяжка|Последний раз)$"), last_time))
    app.add_handler(MessageHandler(filters.Regex("^🔄 Сменить режим$"), switch_mode))

    # Расписание — каждый день в 9:00 по Москве
    job_queue = app.job_queue
    job_queue.run_daily(send_morning_reports, time=datetime.strptime("09:00", "%H:%M").time().replace(tzinfo=MOSCOW))
    job_queue.run_daily(send_weekly_reports, time=datetime.strptime("09:00", "%H:%M").time().replace(tzinfo=MOSCOW))
    job_queue.run_daily(send_monthly_reports, time=datetime.strptime("09:00", "%H:%M").time().replace(tzinfo=MOSCOW))

    # Сравнение с прошлым днём — 3 раза в день по Москве
    for t in ["13:00", "18:00", "22:00"]:
        job_queue.run_daily(send_pace_checks, time=datetime.strptime(t, "%H:%M").time().replace(tzinfo=MOSCOW))

    print("✅ Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
