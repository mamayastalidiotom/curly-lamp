import logging
import json
import os
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATA_FILE = "tracker_data.json"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# Состояния диалогов
(
    CHOOSE_TYPE,
    WAITING_PUFF_COUNT,
    WAITING_PACK_DATE,
    WAITING_CIGARETTE_COUNT,
    WAITING_PUFFS_ADD,
) = range(5)


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


def new_vape_data() -> dict:
    return {
        "type": "vape",
        "total_puffs": 0,
        "today_puffs": 0,
        "today_date": str(date.today()),
        "devices_used": 0,
        "current_device": {
            "total_puffs": None,
            "used_puffs": 0,
            "bought_at": None,
        },
        "last_puff_at": None,
        "history": [],
    }


def new_cig_data() -> dict:
    return {
        "type": "cigarettes",
        "total_cigarettes": 0,
        "today_cigarettes": 0,
        "today_date": str(date.today()),
        "packs_used": 0,
        "current_pack": {
            "bought_at": None,
            "cigarettes_used": 0,
        },
        "last_smoke_at": None,
        "history": [],
    }


def reset_today(data: dict) -> dict:
    if data.get("today_date") != str(date.today()):
        if data["type"] == "vape":
            data["today_puffs"] = 0
        else:
            data["today_cigarettes"] = 0
        data["today_date"] = str(date.today())
    return data


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


# ─────────────────────────────────────────────
# Клавиатуры
# ─────────────────────────────────────────────
def vape_keyboard():
    return ReplyKeyboardMarkup([
        ["💨 Затяжка", "🔋 Новая электронка"],
        ["📊 Статистика", "⏱ Последняя затяжка"],
        ["🔄 Сменить режим"],
    ], resize_keyboard=True)


def cig_keyboard():
    return ReplyKeyboardMarkup([
        ["🚬 Покурила", "📦 Новая пачка"],
        ["📊 Статистика", "⏱ Последний раз"],
        ["🔄 Сменить режим"],
    ], resize_keyboard=True)


def type_inline():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💨 Электронка", callback_data="type_vape"),
            InlineKeyboardButton("🚬 Сигареты", callback_data="type_cig"),
        ]
    ])


# ─────────────────────────────────────────────
# Статистика
# ─────────────────────────────────────────────
def vape_stats(data: dict) -> str:
    dev = data["current_device"]
    if dev["total_puffs"] is not None:
        used = dev["used_puffs"]
        total = dev["total_puffs"]
        remaining = max(0, total - used)
        percent = min(100, int(used / total * 100)) if total > 0 else 0
        bar = "🟩" * (percent // 10) + "⬜" * (10 - percent // 10)
        warn = "\n⚠️ Скоро закончится!" if 0 < remaining <= 20 else ""
        if remaining == 0:
            warn = "\n⚠️ Электронка на нуле! Пора менять 🔁"
        device_info = (
            f"\n🔋 *Текущая электронка*\n"
            f"{bar} {percent}%\n"
            f"Затяжек: {used} / {total}\n"
            f"Осталось: ~{remaining}{warn}\n"
        )
        if dev["bought_at"]:
            device_info += f"Куплена: {time_ago(dev['bought_at'])}\n"
    else:
        device_info = "\n🔋 Электронка не указана — нажми «Новая электронка»\n"

    return (
        f"📊 *Статистика — Электронка*\n\n"
        f"💨 Сегодня: *{data['today_puffs']} затяжек*\n"
        f"📅 За всё время: *{data['total_puffs']} затяжек*\n"
        f"🔁 Электронок куплено: *{data['devices_used']}*\n"
        f"⏱ Последняя затяжка: {time_ago(data['last_puff_at'])}\n"
        f"{device_info}"
    )


def cig_stats(data: dict) -> str:
    pack = data["current_pack"]
    if pack["bought_at"]:
        pack_info = (
            f"\n📦 *Текущая пачка*\n"
            f"Сигарет выкурено: {pack['cigarettes_used']}\n"
            f"Куплена: {time_ago(pack['bought_at'])}\n"
        )
    else:
        pack_info = "\n📦 Пачка не указана — нажми «Новая пачка»\n"

    return (
        f"📊 *Статистика — Сигареты*\n\n"
        f"🚬 Сегодня: *{data['today_cigarettes']} сигарет*\n"
        f"📅 За всё время: *{data['total_cigarettes']} сигарет*\n"
        f"📦 Пачек куплено: *{data['packs_used']}*\n"
        f"⏱ Последний раз: {time_ago(data['last_smoke_at'])}\n"
        f"{pack_info}"
    )


def stats_inline():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_stats")],
        [InlineKeyboardButton("🗑 Сбросить всё", callback_data="confirm_reset")],
    ])


# ─────────────────────────────────────────────
# /start — выбор типа
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        "Я трекер курения. Что ты куришь?",
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
            text="Нажми *«Новая электронка»* чтобы начать — укажи сколько затяжек на упаковке 👇",
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
            text="Нажми *«Новая пачка»* чтобы начать — укажи когда купила 👇",
            parse_mode="Markdown",
            reply_markup=cig_keyboard(),
        )


# ─────────────────────────────────────────────
# Смена режима
# ─────────────────────────────────────────────
async def switch_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Что куришь?",
        reply_markup=type_inline(),
    )


# ─────────────────────────────────────────────
# ЭЛЕКТРОНКА — затяжка
# ─────────────────────────────────────────────
async def vape_puff_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💨 Сколько затяжек сделала?\nВведи число (или 1 если одну):"
    )
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
    return ConversationHandler.END


# ─────────────────────────────────────────────
# ЭЛЕКТРОНКА — новая
# ─────────────────────────────────────────────
async def new_device_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔋 *Новая электронка!*\n\n"
        "Сколько затяжек написано на упаковке?\n"
        "Например: *4000*",
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
    data["current_device"] = {
        "total_puffs": puff_count,
        "used_puffs": 0,
        "bought_at": datetime.now().isoformat(),
    }
    save_data(user_id, data)

    await update.message.reply_text(
        f"✅ Новая электронка на *{puff_count} затяжек* — поехали! 😌",
        parse_mode="Markdown",
        reply_markup=vape_keyboard(),
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────
# СИГАРЕТЫ — покурила
# ─────────────────────────────────────────────
async def cig_smoke_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚬 Сколько сигарет выкурила?\nВведи число:"
    )
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
    save_data(user_id, data)

    msg = (
        f"🚬 +{count} сигарет (сегодня: {data['today_cigarettes']})\n"
        f"📦 Из этой пачки: {data['current_pack']['cigarettes_used']} шт"
    )
    await update.message.reply_text(msg, reply_markup=cig_keyboard())
    return ConversationHandler.END


# ─────────────────────────────────────────────
# СИГАРЕТЫ — новая пачка
# ─────────────────────────────────────────────
async def new_pack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📦 *Новая пачка!*\n\n"
        "Когда купила? Напиши дату или просто *сегодня*\n"
        "Например: *сегодня* или *16.05*",
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
    data["current_pack"] = {
        "bought_at": datetime.now().isoformat(),
        "cigarettes_used": 0,
        "bought_date_text": text,
    }
    save_data(user_id, data)

    await update.message.reply_text(
        f"✅ Новая пачка куплена ({text})!\nСчитаем сигареты 🚬",
        reply_markup=cig_keyboard(),
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
    kb = vape_keyboard() if data["type"] == "vape" else cig_keyboard()

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=stats_inline())


async def last_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_data(user_id)

    if not data:
        await update.message.reply_text("Сначала выбери режим через /start")
        return

    if data["type"] == "vape":
        last = data.get("last_puff_at")
        label = "затяжка"
    else:
        last = data.get("last_smoke_at")
        label = "сигарета"

    if not last:
        await update.message.reply_text(f"Ещё не было ни одной {label} 🌿")
        return

    dt = datetime.fromisoformat(last)
    await update.message.reply_text(
        f"⏱ Последняя {label} в *{dt.strftime('%H:%M')}*\n— {time_ago(last)}",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# Inline-кнопки (обновить / сбросить)
# ─────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = load_data(user_id)

    if query.data in ("type_vape", "type_cig"):
        await choose_type_callback(update, context)
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
# Запуск
# ─────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Диалог: затяжки (электронка)
    puff_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💨 Затяжка$"), vape_puff_start)],
        states={WAITING_PUFFS_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, vape_puff_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Диалог: новая электронка
    device_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔋 Новая электронка$"), new_device_start)],
        states={WAITING_PUFF_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_device_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Диалог: сигареты
    cig_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🚬 Покурила$"), cig_smoke_start)],
        states={WAITING_CIGARETTE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cig_smoke_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Диалог: новая пачка
    pack_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📦 Новая пачка$"), new_pack_start)],
        states={WAITING_PACK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_pack_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(puff_conv)
    app.add_handler(device_conv)
    app.add_handler(cig_conv)
    app.add_handler(pack_conv)
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Regex("^📊 Статистика$"), show_stats))
    app.add_handler(MessageHandler(filters.Regex("^⏱ (Последняя затяжка|Последний раз)$"), last_time))
    app.add_handler(MessageHandler(filters.Regex("^🔄 Сменить режим$"), switch_mode))

    print("✅ Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
