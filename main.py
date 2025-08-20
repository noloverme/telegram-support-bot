import os
import json
import logging
import datetime
from datetime import timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.utils.markdown import hbold
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Токен бота и ID чата администратора (замените на свои)
BOT_TOKEN = "Токен бота"
ADMIN_CHAT_ID = "Группа для бота" #нужно выдать ему админку

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
META_DIR = os.path.join(BOT_DIR, 'meta')

if not os.path.exists(META_DIR):
    try:
        os.makedirs(META_DIR)
        logger.info(f"Создана директория: {META_DIR}")
    except OSError as e:
        logger.error(f"Не удалось создать директорию {META_DIR}: {e}")

USERS_DATA_FILE = os.path.join(META_DIR, 'users_data.json')
MESSAGES_MAPPING_FILE = os.path.join(META_DIR, 'messages_mapping.json')
REPLY_MAPPING_FILE = os.path.join(META_DIR, 'reply_mapping.json')
LOG_FILE_NAME = os.path.join(META_DIR, 'admin_log.txt')
MESSAGES_FILE = os.path.join(BOT_DIR, 'messages.json')

PAGE_SIZE = 10


def load_data(file_path: str) -> dict:
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}
    return {}


def save_data(file_path: str, data: dict):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


try:
    MESSAGES = load_data(MESSAGES_FILE)
    if not MESSAGES:
        logger.warning(f"Файл сообщений '{MESSAGES_FILE}' пуст или не найден. Используются значения по умолчанию.")
except Exception as e:
    logger.error(f"Ошибка при загрузке файла сообщений: {e}")
    MESSAGES = {}


def log_admin_action(admin_id, action_type, details):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Admin ID: {admin_id} | Action: {action_type} | Details: {details}\n"
    with open(LOG_FILE_NAME, 'a', encoding='utf-8') as f:
        f.write(log_entry)


def get_russian_month(month_number):
    months = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    return months.get(month_number, '')


def format_datetime_for_message(dt_obj):
    moscow_tz = timezone(timedelta(hours=3))
    dt_moscow = dt_obj.astimezone(moscow_tz)
    day = dt_moscow.day
    month_name = get_russian_month(dt_moscow.month)
    year = dt_moscow.year
    hour = dt_moscow.hour
    minute = dt_moscow.minute
    return f"{day} {month_name} {year} в {hour:02}:{minute:02} (по мск)"


def cleanup_old_messages():
    messages_mapping = load_data(MESSAGES_MAPPING_FILE)
    thirty_days_ago = datetime.datetime.now() - timedelta(days=30)
    new_mapping = {}
    for user_msg_id, data in messages_mapping.items():
        timestamp = datetime.datetime.fromtimestamp(data['timestamp'])
        if timestamp > thirty_days_ago:
            new_mapping[user_msg_id] = data
    save_data(MESSAGES_MAPPING_FILE, new_mapping)


def is_user_banned(user_id):
    users_data = load_data(USERS_DATA_FILE)
    user_id_str = str(user_id)
    if user_id_str in users_data and 'banned_until' in users_data[user_id_str]:
        ban_end_time_str = users_data[user_id_str]['banned_until']
        if ban_end_time_str == datetime.datetime.max.isoformat():
            return True, "навсегда"
        ban_end_time = datetime.datetime.fromisoformat(ban_end_time_str)
        if ban_end_time > datetime.datetime.now():
            return True, format_datetime_for_message(ban_end_time)
        else:
            del users_data[user_id_str]['banned_until']
            if 'ban_reason' in users_data[user_id_str]:
                del users_data[user_id_str]['ban_reason']
            save_data(USERS_DATA_FILE, users_data)
    return False, None


async def is_admin(user_id: int, chat_id: int, bot: Bot) -> bool:
    try:
        admins = await bot.get_chat_administrators(chat_id)
        return any(admin.user.id == user_id for admin in admins)
    except Exception:
        return False


async def start_command(message: Message):
    user_id = str(message.chat.id)
    is_banned, _ = is_user_banned(user_id)
    if is_banned:
        return

    users_data = load_data(USERS_DATA_FILE)
    now = datetime.datetime.now()

    if user_id not in users_data:
        users_data[user_id] = {
            'first_launch': now.isoformat(),
            'total_messages': 0,
            'monthly_messages': 0,
            'weekly_messages': 0,
            'last_message_date': now.isoformat()
        }
        if message.from_user.username:
            users_data[user_id]['username'] = message.from_user.username
        save_data(USERS_DATA_FILE, users_data)
        await message.reply(MESSAGES.get("welcome_user", "Привет!"))
    else:
        first_launch_dt = datetime.datetime.fromisoformat(users_data[user_id]['first_launch'])
        formatted_date = format_datetime_for_message(first_launch_dt)
        await message.reply(
            MESSAGES.get("already_started", "С возвращением!").format(formatted_date=formatted_date))


async def help_command(message: Message):
    await message.reply(MESSAGES.get("help_message", "Это бот для связи с администратором."))


async def msg_admin_command(message: Message, bot: Bot):
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    args = message.text.split()[1:]
    if len(args) < 2:
        await message.reply(MESSAGES.get("msg_usage", "Использование: /msg <user_id> <текст>"))
        return

    user_id_to_send = args[0]
    text_to_send = ' '.join(args[1:])

    if not user_id_to_send.isdigit():
        await message.reply(MESSAGES.get("id_not_numeric", "ID пользователя должен быть числом."))
        return

    try:
        await bot.send_message(user_id_to_send, text_to_send)
        await message.reply(
            MESSAGES.get("msg_sent_success", "Сообщение отправлено пользователю {user_id_to_send}.").format(
                user_id_to_send=user_id_to_send))
        log_admin_action(message.from_user.id, "SEND_MSG",
                         f"To user {user_id_to_send}: '{text_to_send[:50]}...'")
    except Exception as e:
        await message.reply(MESSAGES.get("msg_send_error", "Ошибка при отправке: {error}").format(error=e))


async def who_admin_command(message: Message, bot: Bot):
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    target_user_id = None
    if message.reply_to_message:
        messages_mapping = load_data(MESSAGES_MAPPING_FILE)
        if str(message.reply_to_message.message_id) in messages_mapping:
            target_user_id = messages_mapping[str(message.reply_to_message.message_id)]['user_id']
    elif len(message.text.split()) > 1:
        target_user_id = message.text.split()[1]

    if not target_user_id:
        await message.reply(
            MESSAGES.get("who_usage", "Использование: /who <user_id> или ответьте на сообщение пользователя."))
        return

    users_data = load_data(USERS_DATA_FILE)
    if str(target_user_id) in users_data:
        user_info = users_data[str(target_user_id)]
        first_launch_dt = datetime.datetime.fromisoformat(user_info['first_launch'])
        formatted_date = format_datetime_for_message(first_launch_dt)
        username_info = f"@{user_info['username']}" if 'username' in user_info else "не указан"

        text = MESSAGES.get("user_info_template",
                            "ID: {user_id}\nUsername: {username_info}\nПервый запуск: {formatted_date}").format(
            user_id=target_user_id,
            username_info=username_info,
            formatted_date=formatted_date
        )
        await message.reply(text, parse_mode='HTML')
        log_admin_action(message.from_user.id, "GET_USER_INFO", f"For user {target_user_id}")
    else:
        await message.reply(MESSAGES.get("user_not_found", "Пользователь не найден."))


async def stats_admin_command(message: Message, bot: Bot):
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    users_data = load_data(USERS_DATA_FILE)
    total_messages = sum(user_info.get('total_messages', 0) for user_info in users_data.values())

    one_month_ago = datetime.datetime.now() - timedelta(days=30)
    monthly_messages = sum(
        user_info.get('monthly_messages', 0)
        for user_info in users_data.values()
        if datetime.datetime.fromisoformat(
            user_info.get('last_message_date', datetime.datetime.now().isoformat())) > one_month_ago
    )

    one_week_ago = datetime.datetime.now() - timedelta(days=7)
    weekly_messages = sum(
        user_info.get('weekly_messages', 0)
        for user_info in users_data.values()
        if datetime.datetime.fromisoformat(
            user_info.get('last_message_date', datetime.datetime.now().isoformat())) > one_week_ago
    )

    text = MESSAGES.get("stats_template",
                        "📊 <b>Статистика бота</b>\n\nВсего сообщений: {total_messages}\nСообщений за месяц: {monthly_messages}\nСообщений за неделю: {weekly_messages}").format(
        total_messages=total_messages,
        monthly_messages=monthly_messages,
        weekly_messages=weekly_messages
    )
    await message.reply(text)


async def ban_admin_command(message: Message, bot: Bot):
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    target_user_id = None
    args = message.text.split()[1:]

    if message.reply_to_message:
        messages_mapping = load_data(MESSAGES_MAPPING_FILE)
        if str(message.reply_to_message.message_id) in messages_mapping:
            target_user_id = messages_mapping[str(message.reply_to_message.message_id)]['user_id']
    elif args and args[0].isdigit():
        target_user_id = args[0]
        args = args[1:]
    else:
        await message.reply(MESSAGES.get("ban_usage", "Использование: /ban <user_id> [срок] [причина]"))
        return

    if not target_user_id:
        return

    users_data = load_data(USERS_DATA_FILE)
    user_id_str = str(target_user_id)
    if user_id_str not in users_data:
        await message.reply(MESSAGES.get("ban_user_not_found", "Пользователь не найден в базе данных."))
        return

    is_banned, ban_until_text = is_user_banned(target_user_id)
    if is_banned:
        ban_reason = users_data[user_id_str].get('ban_reason', 'не указана')
        await message.reply(
            MESSAGES.get("user_already_banned",
                         "Пользователь уже заблокирован.\nПричина: {reason}\nЗаблокирован до: {until}").format(
                reason=ban_reason,
                until=ban_until_text)
        )
        return

    ban_duration = None
    reason_str = None
    time_str = None
    value = None

    if args:
        potential_time_str = args[0]
        try:
            unit = potential_time_str[-1].lower()
            val = int(potential_time_str[:-1])
            if unit in ['y', 'г', 'w', 'н', 'd', 'д', 'h', 'ч', 'm', 'м']:
                if unit in ['y', 'г']:
                    ban_duration = timedelta(days=val * 365)
                elif unit in ['w', 'н']:
                    ban_duration = timedelta(weeks=val)
                elif unit in ['d', 'д']:
                    ban_duration = timedelta(days=val)
                elif unit in ['h', 'ч']:
                    ban_duration = timedelta(hours=val)
                elif unit in ['m', 'м']:
                    ban_duration = timedelta(minutes=val)
                time_str = potential_time_str
                value = val
                if len(args) > 1:
                    reason_str = ' '.join(args[1:])
            else:
                reason_str = ' '.join(args)
        except (ValueError, IndexError):
            reason_str = ' '.join(args)

    ban_end_time = datetime.datetime.now() + ban_duration if ban_duration else datetime.datetime.max

    users_data[user_id_str]['banned_until'] = ban_end_time.isoformat()
    if reason_str:
        users_data[user_id_str]['ban_reason'] = reason_str
    save_data(USERS_DATA_FILE, users_data)

    user_ban_message = ""
    if ban_duration:
        duration_text = ""
        if 'y' in time_str or 'г' in time_str:
            duration_text = f"{value} г."
        elif 'w' in time_str or 'н' in time_str:
            duration_text = f"{value} нед."
        elif 'd' in time_str or 'д' in time_str:
            duration_text = f"{value} д."
        elif 'h' in time_str or 'ч' in time_str:
            duration_text = f"{value} ч."
        elif 'm' in time_str or 'м' in time_str:
            duration_text = f"{value} мин."

        user_ban_message = MESSAGES.get("user_ban_message_with_duration",
                                        "Вы были заблокированы в данном боте.\nДлительность блокировки: {duration}\nПричина блокировки: {reason}").format(
            duration=duration_text,
            reason=reason_str or MESSAGES.get("user_ban_message_no_reason", "Причина не указана."))
    else:
        user_ban_message = MESSAGES.get("user_ban_message_permanent",
                                        "Вы были заблокированы в данном боте.\nДлительность блокировки: навсегда\nПричина блокировки: {reason}").format(
            reason=reason_str or MESSAGES.get("user_ban_message_no_reason", "Причина не указана."))

    try:
        await bot.send_message(target_user_id, user_ban_message)
    except Exception:
        pass

    await message.reply(
        MESSAGES.get("admin_ban_success", "Пользователь с ID {user_id} успешно заблокирован.").format(
            user_id=target_user_id))
    log_admin_action(message.from_user.id, "BAN_USER",
                     f"User {target_user_id} banned. Duration: {time_str or 'Permanent'}. Reason: {reason_str or 'Not specified'}")


async def unban_admin_command(message: Message, bot: Bot):
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    target_user_id = None
    if message.reply_to_message:
        messages_mapping = load_data(MESSAGES_MAPPING_FILE)
        if str(message.reply_to_message.message_id) in messages_mapping:
            target_user_id = messages_mapping[str(message.reply_to_message.message_id)]['user_id']
    elif len(message.text.split()) > 1:
        target_user_id = message.text.split()[1]

    if not target_user_id:
        await message.reply(
            MESSAGES.get("unban_usage",
                         "Не могу определить ID пользователя для разбана. Используйте /unban <ID> или ответьте на сообщение пользователя."))
        return

    users_data = load_data(USERS_DATA_FILE)
    user_id_str = str(target_user_id)

    if user_id_str in users_data and 'banned_until' in users_data[user_id_str]:
        del users_data[user_id_str]['banned_until']
        if 'ban_reason' in users_data[user_id_str]:
            del users_data[user_id_str]['ban_reason']
        save_data(USERS_DATA_FILE, users_data)

        try:
            await bot.send_message(target_user_id,
                                   MESSAGES.get("user_unbanned_message", "Вы были разблокированы в боте поддержки."))
        except Exception:
            pass

        await message.reply(
            MESSAGES.get("admin_unban_success", "Пользователь с ID {user_id} разблокирован.").format(
                user_id=target_user_id))
        log_admin_action(message.from_user.id, "UNBAN_USER", f"User {target_user_id} unbanned.")
    else:
        await message.reply(MESSAGES.get("user_not_banned", "Пользователь не был заблокирован."))


async def banlist_admin_command(message: Message, bot: Bot):
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return
    await _send_banlist_page(message, bot, 1)


async def _send_banlist_page(message: Message, bot: Bot, page: int):
    users_data = load_data(USERS_DATA_FILE)
    banned_users_list = []

    for user_id, user_info in users_data.items():
        is_banned, ban_until_text = is_user_banned(user_id)
        if is_banned:
            reason = user_info.get('ban_reason', 'не указана')
            username = user_info.get('username', 'неизвестный')
            banned_users_list.append(
                MESSAGES.get("banned_user_template",
                             "&lt;b&gt;ID:&lt;/b&gt; &lt;code&gt;{user_id}&lt;/code&gt;\n&lt;b&gt;Username:&lt;/b&gt; @{username}\n&lt;b&gt;Причина:&lt;/b&gt; {reason}\n&lt;b&gt;Заблокирован до:&lt;/b&gt; {until}\n").format(
                    user_id=user_id,
                    username=username,
                    reason=reason,
                    until=ban_until_text
                )
            )

    if not banned_users_list:
        await message.reply(
            MESSAGES.get("no_banned_users", "В данный момент заблокированных пользователей нет."))
        return

    total_users = len(banned_users_list)
    total_pages = (total_users + PAGE_SIZE - 1) // PAGE_SIZE

    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    start_index = (page - 1) * PAGE_SIZE
    end_index = min(start_index + PAGE_SIZE, total_users)
    paginated_users = banned_users_list[start_index:end_index]

    message_text = MESSAGES.get("banned_list_title",
                                "&lt;b&gt;Список заблокированных пользователей (страница {current_page}/{total_pages}):&lt;/b&gt;\n").format(
        current_page=page, total_pages=total_pages) + "\n---\n".join(paginated_users)

    keyboard = []
    row = []
    if page > 1:
        row.append(InlineKeyboardButton("<< Назад", callback_data=f"banlist_{page - 1}"))
    if page < total_pages:
        row.append(InlineKeyboardButton("Вперед >>", callback_data=f"banlist_{page + 1}"))
    if row:
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply(message_text, reply_markup=reply_markup)
    log_admin_action(message.from_user.id, "GET_BANLIST", f"Ban list requested, page {page}")


async def button_handler(callback_query: types.CallbackQuery, bot: Bot):
    await callback_query.answer()
    page = int(callback_query.data.split('_')[1])
    await _send_banlist_page(callback_query.message, bot, page)


async def handle_user_message(message: Message, bot: Bot):
    user_id = str(message.chat.id)
    is_banned, ban_until_text = is_user_banned(user_id)

    if is_banned:
        users_data = load_data(USERS_DATA_FILE)
        ban_reason = users_data.get(user_id, {}).get('ban_reason', 'не указана')
        await message.reply(
            MESSAGES.get("user_is_banned_message_with_reason",
                         "Вы были заблокированы в данном боте.\nПричина блокировки: {reason}\nЗаблокирован до: {until}").format(
                reason=ban_reason, until=ban_until_text)
        )
        return

    users_data = load_data(USERS_DATA_FILE)
    now = datetime.datetime.now()

    if user_id in users_data:
        users_data[user_id]['total_messages'] = users_data[user_id].get('total_messages', 0) + 1
        last_message_date = datetime.datetime.fromisoformat(users_data[user_id]['last_message_date'])

        if now - last_message_date > timedelta(days=30):
            users_data[user_id]['monthly_messages'] = 1
        else:
            users_data[user_id]['monthly_messages'] = users_data[user_id].get('monthly_messages', 0) + 1

        if now - last_message_date > timedelta(days=7):
            users_data[user_id]['weekly_messages'] = 1
        else:
            users_data[user_id]['weekly_messages'] = users_data[user_id].get('weekly_messages', 0) + 1
        users_data[user_id]['last_message_date'] = now.isoformat()
        save_data(USERS_DATA_FILE, users_data)

    forwarded_message = await bot.forward_message(ADMIN_CHAT_ID, user_id, message.message_id)
    messages_mapping = load_data(MESSAGES_MAPPING_FILE)
    messages_mapping[str(forwarded_message.message_id)] = {
        'user_id': user_id,
        'user_message_id': message.message_id,
        'timestamp': now.timestamp()
    }
    save_data(MESSAGES_MAPPING_FILE, messages_mapping)
    cleanup_old_messages()


async def handle_admin_reply(message: Message, bot: Bot):
    if not message.reply_to_message or not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    messages_mapping = load_data(MESSAGES_MAPPING_FILE)
    reply_to_message_id = str(message.reply_to_message.message_id)

    if reply_to_message_id in messages_mapping:
        user_id = messages_mapping[reply_to_message_id]['user_id']
        try:
            await bot.copy_message(user_id, message.chat.id, message.message_id)
            log_admin_action(message.from_user.id, "REPLY_TO_USER",
                             f"To user {user_id}: '{message.text[:50]}...'")
        except Exception as e:
            await bot.send_message(ADMIN_CHAT_ID, MESSAGES.get("error_sending_reply",
                                                               "Не удалось отправить ответ пользователю {user_id}. Возможно, он заблокировал бота. Ошибка: {error}").format(
                user_id=user_id, error=e))


async def main() -> None:
    for file_path in [USERS_DATA_FILE, MESSAGES_MAPPING_FILE, REPLY_MAPPING_FILE, LOG_FILE_NAME]:
        if not os.path.exists(file_path):
            with open(file_path, 'w' if '.json' in file_path else 'a', encoding='utf-8') as f:
                if '.json' in file_path:
                    json.dump({}, f)
                else:
                    pass

    if not os.path.exists(MESSAGES_FILE):
        with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.message.register(start_command, CommandStart())
    dp.message.register(help_command, Command("help"))
    dp.message.register(msg_admin_command, Command("msg"), F.chat.id == ADMIN_CHAT_ID)
    dp.message.register(who_admin_command, Command("who"), F.chat.id == ADMIN_CHAT_ID)
    dp.message.register(stats_admin_command, Command("stats"), F.chat.id == ADMIN_CHAT_ID)
    dp.message.register(ban_admin_command, Command("ban"), F.chat.id == ADMIN_CHAT_ID)
    dp.message.register(unban_admin_command, Command("unban"), F.chat.id == ADMIN_CHAT_ID)
    dp.message.register(banlist_admin_command, Command("banlist"), F.chat.id == ADMIN_CHAT_ID)

    dp.message.register(handle_user_message, F.chat.id != ADMIN_CHAT_ID)
    dp.message.register(handle_admin_reply, F.chat.id == ADMIN_CHAT_ID, F.reply_to_message)
    dp.callback_query.register(button_handler, F.data.startswith('banlist_'))

    try:
        logger.info("Бот запущен.")
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
