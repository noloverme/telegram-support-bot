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
from aiogram.exceptions import TelegramAPIError

# --- Конфигурация ---
# Замените на ваш токен
BOT_TOKEN = "токен"
# Группа администрации, нужно будет выдать админку боту
ADMIN_CHAT_ID = "айди группы"
PAGE_SIZE = 10

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Определение путей
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
META_DIR = os.path.join(BOT_DIR, 'meta')

# Создание директории meta
os.makedirs(META_DIR, exist_ok=True)

# Пути к файлам данных
USERS_DATA_FILE = os.path.join(META_DIR, 'users_data.json')
MESSAGES_MAPPING_FILE = os.path.join(META_DIR, 'messages_mapping.json')
REPLY_MAPPING_FILE = os.path.join(META_DIR, 'reply_mapping.json')
LOG_FILE_NAME = os.path.join(META_DIR, 'admin_log.txt')
MESSAGES_FILE = os.path.join(BOT_DIR, 'messages.json')


# --- Утилиты для работы с данными ---

def load_data(file_path: str) -> dict:
    """Загружает данные из JSON-файла."""
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def save_data(file_path: str, data: dict):
    """Сохраняет данные в JSON-файл."""
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
    """Логирование действий администратора."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Admin ID: {admin_id} | Action: {action_type} | Details: {details}\n"
    with open(LOG_FILE_NAME, 'a', encoding='utf-8') as f:
        f.write(log_entry)


# --- Утилиты для работы с датой и временем ---

def get_russian_month(month_number: int) -> str:
    """Возвращает название месяца на русском языке."""
    months = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    return months.get(month_number, '')


def format_datetime_for_message(dt_obj: datetime.datetime) -> str:
    """Форматирует объект datetime в строку для сообщения (по МСК)."""
    moscow_tz = timezone(timedelta(hours=3))
    dt_moscow = dt_obj.astimezone(moscow_tz)
    return f"{dt_moscow.day} {get_russian_month(dt_moscow.month)} {dt_moscow.year} в {dt_moscow.hour:02}:{dt_moscow.minute:02} (по мск)"


# --- Основная логика бота ---

def cleanup_old_messages():
    """Удаляет старые сообщения (старше 30 дней) из файла-отображения."""
    messages_mapping = load_data(MESSAGES_MAPPING_FILE)
    thirty_days_ago = datetime.datetime.now() - timedelta(days=30)

    # Используем dict comprehension для большей эффективности
    new_mapping = {
        user_msg_id: data
        for user_msg_id, data in messages_mapping.items()
        if datetime.datetime.fromtimestamp(data.get('timestamp', 0)) > thirty_days_ago
    }
    save_data(MESSAGES_MAPPING_FILE, new_mapping)


def is_user_banned(user_id: int) -> (bool, str | None):
    """
    Проверяет, заблокирован ли пользователь.
    Также снимает блокировку, если ее срок истек (побочный эффект).
    """
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
            # Срок бана истек, снимаем бан
            del users_data[user_id_str]['banned_until']
            if 'ban_reason' in users_data[user_id_str]:
                del users_data[user_id_str]['ban_reason']
            save_data(USERS_DATA_FILE, users_data)

    return False, None


async def is_admin(user_id: int, chat_id: int, bot: Bot) -> bool:
    """Проверяет, является ли пользователь администратором чата."""
    try:
        admins = await bot.get_chat_administrators(chat_id)
        return any(admin.user.id == user_id for admin in admins)
    except TelegramAPIError as e:
        logger.error(f"Ошибка при проверке статуса администратора: {e}")
        return False


# --- Обработчики команд ---

async def start_command(message: Message):
    """Обрабатывает команду /start."""
    user_id = str(message.chat.id)
    if is_user_banned(int(user_id))[0]:
        return

    users_data = load_data(USERS_DATA_FILE)
    now = datetime.datetime.now()

    if user_id not in users_data:
        users_data[user_id] = {
            'first_launch': now.isoformat(),
            'total_messages': 0,
            'monthly_messages': 0,
            'weekly_messages': 0,
            'last_message_date': now.isoformat(),
            'username': message.from_user.username if message.from_user else "unknown"
        }
        save_data(USERS_DATA_FILE, users_data)
        await message.reply(MESSAGES.get("welcome_user", "Привет!"))
    else:
        first_launch_dt = datetime.datetime.fromisoformat(users_data[user_id]['first_launch'])
        formatted_date = format_datetime_for_message(first_launch_dt)
        await message.reply(
            MESSAGES.get("already_started", "С возвращением! Вы с нами с {formatted_date}.").format(
                formatted_date=formatted_date))


async def help_command(message: Message):
    """Обрабатывает команду /help."""
    await message.reply(MESSAGES.get("help_message", "Это бот для связи с администратором."))


async def msg_admin_command(message: Message, bot: Bot):
    """Обрабатывает команду /msg для отправки сообщения пользователю."""
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply(MESSAGES.get("msg_usage", "Использование: /msg <user_id> <текст>"))
        return

    _, user_id_to_send, text_to_send = args

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
    except TelegramAPIError as e:
        await message.reply(MESSAGES.get("msg_send_error", "Ошибка при отправке: {error}").format(error=e))


async def who_admin_command(message: Message, bot: Bot):
    """Обрабатывает команду /who для получения информации о пользователе."""
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    target_user_id = None
    if message.reply_to_message:
        messages_mapping = load_data(MESSAGES_MAPPING_FILE)
        target_user_id = messages_mapping.get(str(message.reply_to_message.message_id), {}).get('user_id')
    elif len(message.text.split()) > 1:
        target_user_id = message.text.split()[1]

    if not target_user_id:
        await message.reply(
            MESSAGES.get("who_usage", "Использование: /who <user_id> или ответьте на сообщение пользователя."))
        return

    users_data = load_data(USERS_DATA_FILE)
    user_info = users_data.get(str(target_user_id))

    if user_info:
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
    """Обрабатывает команду /stats для получения статистики."""
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    users_data = load_data(USERS_DATA_FILE)
    total_messages = sum(user.get('total_messages', 0) for user in users_data.values())

    now = datetime.datetime.now()
    one_month_ago = now - timedelta(days=30)
    one_week_ago = now - timedelta(days=7)

    monthly_messages = 0
    weekly_messages = 0

    for user in users_data.values():
        last_message_date_str = user.get('last_message_date')
        if last_message_date_str:
            last_message_date = datetime.datetime.fromisoformat(last_message_date_str)
            if last_message_date > one_month_ago:
                monthly_messages += user.get('monthly_messages', 0)
            if last_message_date > one_week_ago:
                weekly_messages += user.get('weekly_messages', 0)

    text = MESSAGES.get("stats_template",
                        "📊 <b>Статистика бота</b>\n\nВсего сообщений: {total_messages}\nСообщений за месяц: {monthly_messages}\nСообщений за неделю: {weekly_messages}").format(
        total_messages=total_messages,
        monthly_messages=monthly_messages,
        weekly_messages=weekly_messages
    )
    await message.reply(text)


def _parse_ban_args(args: list) -> (timedelta | None, str | None):
    """Парсит аргументы для команды бана, извлекая длительность и причину."""
    if not args:
        return None, None

    duration_str = args[0]
    reason_args = args[1:]

    duration = None
    unit = duration_str[-1].lower()
    value_str = duration_str[:-1]

    if value_str.isdigit():
        value = int(value_str)
        if unit in ['y', 'г']:
            duration = timedelta(days=value * 365)
        elif unit in ['w', 'н']:
            duration = timedelta(weeks=value)
        elif unit in ['d', 'д']:
            duration = timedelta(days=value)
        elif unit in ['h', 'ч']:
            duration = timedelta(hours=value)
        elif unit in ['m', 'м']:
            duration = timedelta(minutes=value)
        else:
            # Если последний символ не является единицей времени, считаем все аргументы причиной
            reason_args.insert(0, duration_str)

    else:
        reason_args.insert(0, duration_str)

    reason = ' '.join(reason_args) if reason_args else None
    return duration, reason


async def ban_admin_command(message: Message, bot: Bot):
    """Обрабатывает команду /ban для блокировки пользователя."""
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    args = message.text.split()[1:]
    target_user_id = None

    if message.reply_to_message:
        messages_mapping = load_data(MESSAGES_MAPPING_FILE)
        target_user_id = messages_mapping.get(str(message.reply_to_message.message_id), {}).get('user_id')
    elif args and args[0].isdigit():
        target_user_id = args.pop(0)

    if not target_user_id:
        await message.reply(
            MESSAGES.get("ban_usage", "Использование: /ban <user_id> [срок] [причина] или ответом на сообщение."))
        return

    users_data = load_data(USERS_DATA_FILE)
    user_id_str = str(target_user_id)
    if user_id_str not in users_data:
        await message.reply(MESSAGES.get("ban_user_not_found", "Пользователь не найден в базе данных."))
        return

    is_banned, ban_until_text = is_user_banned(target_user_id)
    if is_banned:
        ban_reason = users_data.get(user_id_str, {}).get('ban_reason', 'не указана')
        await message.reply(
            MESSAGES.get("user_already_banned",
                         "Пользователь уже заблокирован.\nПричина: {reason}\nЗаблокирован до: {until}").format(
                reason=ban_reason, until=ban_until_text)
        )
        return

    ban_duration, reason_str = _parse_ban_args(args)

    ban_end_time = datetime.datetime.now() + ban_duration if ban_duration else datetime.datetime.max
    users_data[user_id_str]['banned_until'] = ban_end_time.isoformat()
    if reason_str:
        users_data[user_id_str]['ban_reason'] = reason_str
    save_data(USERS_DATA_FILE, users_data)

    # Формирование сообщения пользователю
    if ban_duration:
        user_ban_message = MESSAGES.get("user_ban_message_with_duration",
                                        "Вы были заблокированы.\nДлительность: {duration}\nПричина: {reason}").format(
            duration=str(ban_duration),
            reason=reason_str or "не указана")
    else:
        user_ban_message = MESSAGES.get("user_ban_message_permanent",
                                        "Вы были заблокированы навсегда.\nПричина: {reason}").format(
            reason=reason_str or "не указана")

    try:
        await bot.send_message(target_user_id, user_ban_message)
    except TelegramAPIError:
        pass  # Пользователь мог заблокировать бота

    await message.reply(
        MESSAGES.get("admin_ban_success", "Пользователь с ID {user_id} успешно заблокирован.").format(
            user_id=target_user_id))
    log_admin_action(message.from_user.id, "BAN_USER",
                     f"User {target_user_id} banned. Duration: {str(ban_duration) or 'Permanent'}. Reason: {reason_str or 'Not specified'}")


async def unban_admin_command(message: Message, bot: Bot):
    """Обрабатывает команду /unban для разблокировки пользователя."""
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    target_user_id = None
    if message.reply_to_message:
        messages_mapping = load_data(MESSAGES_MAPPING_FILE)
        target_user_id = messages_mapping.get(str(message.reply_to_message.message_id), {}).get('user_id')
    elif len(message.text.split()) > 1:
        target_user_id = message.text.split()[1]

    if not target_user_id:
        await message.reply(
            MESSAGES.get("unban_usage",
                         "Не могу определить ID пользователя. Используйте /unban <ID> или ответьте на сообщение."))
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
                                   MESSAGES.get("user_unbanned_message", "Вы были разблокированы."))
        except TelegramAPIError:
            pass

        await message.reply(
            MESSAGES.get("admin_unban_success", "Пользователь с ID {user_id} разблокирован.").format(
                user_id=target_user_id))
        log_admin_action(message.from_user.id, "UNBAN_USER", f"User {target_user_id} unbanned.")
    else:
        await message.reply(MESSAGES.get("user_not_banned", "Пользователь не был заблокирован."))


async def banlist_admin_command(message: Message, bot: Bot):
    """Обрабатывает команду /banlist для вывода списка забаненных."""
    if not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return
    await _send_banlist_page(message, bot, 1)


async def _send_banlist_page(message: Message, bot: Bot, page: int):
    """Отправляет страницу со списком заблокированных пользователей."""
    users_data = load_data(USERS_DATA_FILE)
    banned_users_list = []

    for user_id, user_info in users_data.items():
        is_banned, ban_until_text = is_user_banned(user_id)
        if is_banned:
            banned_users_list.append({
                'user_id': user_id,
                'username': user_info.get('username', 'неизвестный'),
                'reason': user_info.get('ban_reason', 'не указана'),
                'until': ban_until_text
            })

    if not banned_users_list:
        await message.reply(MESSAGES.get("no_banned_users", "Заблокированных пользователей нет."))
        return

    total_users = len(banned_users_list)
    total_pages = (total_users + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(1, min(page, total_pages))

    start_index = (page - 1) * PAGE_SIZE
    paginated_users = banned_users_list[start_index:start_index + PAGE_SIZE]

    user_lines = [
        MESSAGES.get("banned_user_template",
                     "<b>ID:</b> <code>{user_id}</code>\n<b>Username:</b> @{username}\n<b>Причина:</b> {reason}\n<b>До:</b> {until}\n").format(
            **user)
        for user in paginated_users
    ]

    message_text = MESSAGES.get("banned_list_title",
                                "<b>Список заблокированных (страница {current_page}/{total_pages}):</b>\n").format(
        current_page=page, total_pages=total_pages) + "\n---\n".join(user_lines)

    # Кнопки пагинации
    keyboard = []
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(text="<< Назад", callback_data=f"banlist_{page - 1}"))
    if page < total_pages:
        row.append(InlineKeyboardButton(text="Вперед >>", callback_data=f"banlist_{page + 1}"))
    if row:
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    # Если сообщение было изменено, используем edit_text
    if isinstance(message, types.CallbackQuery):
        await message.message.edit_text(message_text, reply_markup=reply_markup)
    else:
        await message.reply(message_text, reply_markup=reply_markup)

    log_admin_action(message.from_user.id, "GET_BANLIST", f"Page {page}")


async def button_handler(callback_query: types.CallbackQuery, bot: Bot):
    """Обрабатывает нажатия кнопок пагинации."""
    await callback_query.answer()
    page = int(callback_query.data.split('_')[1])
    await _send_banlist_page(callback_query, bot, page)


# --- Обработчики сообщений ---

async def handle_user_message(message: Message, bot: Bot):
    """Обрабатывает сообщения от обычных пользователей."""
    user_id = str(message.chat.id)
    is_banned, ban_until_text = is_user_banned(int(user_id))

    if is_banned:
        users_data = load_data(USERS_DATA_FILE)
        ban_reason = users_data.get(user_id, {}).get('ban_reason', 'не указана')
        await message.reply(
            MESSAGES.get("user_is_banned_message_with_reason",
                         "Вы заблокированы.\nПричина: {reason}\nДо: {until}").format(
                reason=ban_reason, until=ban_until_text)
        )
        return

    # Обновление статистики пользователя
    users_data = load_data(USERS_DATA_FILE)
    now = datetime.datetime.now()
    if user_id in users_data:
        user_data = users_data[user_id]
        user_data['total_messages'] = user_data.get('total_messages', 0) + 1

        last_message_date = datetime.datetime.fromisoformat(user_data['last_message_date'])
        if now - last_message_date > timedelta(days=30):
            user_data['monthly_messages'] = 1
        else:
            user_data['monthly_messages'] = user_data.get('monthly_messages', 0) + 1

        if now - last_message_date > timedelta(days=7):
            user_data['weekly_messages'] = 1
        else:
            user_data['weekly_messages'] = user_data.get('weekly_messages', 0) + 1

        user_data['last_message_date'] = now.isoformat()
        save_data(USERS_DATA_FILE, users_data)

    # Пересылка сообщения администратору
    try:
        forwarded_message = await bot.forward_message(ADMIN_CHAT_ID, user_id, message.message_id)
        messages_mapping = load_data(MESSAGES_MAPPING_FILE)
        messages_mapping[str(forwarded_message.message_id)] = {
            'user_id': user_id,
            'user_message_id': message.message_id,
            'timestamp': now.timestamp()
        }
        save_data(MESSAGES_MAPPING_FILE, messages_mapping)
        cleanup_old_messages()
    except TelegramAPIError as e:
        logger.error(f"Не удалось переслать сообщение от {user_id}: {e}")


async def handle_admin_reply(message: Message, bot: Bot):
    """Обрабатывает ответы администраторов на сообщения пользователей."""
    if not message.reply_to_message or not await is_admin(message.from_user.id, ADMIN_CHAT_ID, bot):
        return

    messages_mapping = load_data(MESSAGES_MAPPING_FILE)
    reply_to_message_id = str(message.reply_to_message.message_id)

    if reply_to_message_id in messages_mapping:
        user_id = messages_mapping[reply_to_message_id]['user_id']
        try:
            await bot.copy_message(user_id, message.chat.id, message.message_id)
            log_admin_action(message.from_user.id, "REPLY_TO_USER", f"To user {user_id}")
        except TelegramAPIError as e:
            await bot.send_message(ADMIN_CHAT_ID, MESSAGES.get("error_sending_reply",
                                                               "Не удалось отправить ответ пользователю {user_id}. Ошибка: {error}").format(
                user_id=user_id, error=e))


# --- Запуск бота ---

async def main() -> None:
    """Главная функция для запуска бота."""
    # Упрощенное создание файлов
    for file_path in [USERS_DATA_FILE, MESSAGES_MAPPING_FILE, REPLY_MAPPING_FILE, MESSAGES_FILE]:
        if not os.path.exists(file_path):
            save_data(file_path, {})
    if not os.path.exists(LOG_FILE_NAME):
        open(LOG_FILE_NAME, 'a').close()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Регистрация обработчиков
    dp.message.register(start_command, CommandStart())
    dp.message.register(help_command, Command("help"))

    # Команды администратора
    admin_filter = F.chat.id == ADMIN_CHAT_ID
    dp.message.register(msg_admin_command, Command("msg"), admin_filter)
    dp.message.register(who_admin_command, Command("who"), admin_filter)
    dp.message.register(stats_admin_command, Command("stats"), admin_filter)
    dp.message.register(ban_admin_command, Command("ban"), admin_filter)
    dp.message.register(unban_admin_command, Command("unban"), admin_filter)
    dp.message.register(banlist_admin_command, Command("banlist"), admin_filter)

    # Обработка сообщений и колбэков
    dp.message.register(handle_admin_reply, admin_filter, F.reply_to_message)
    dp.message.register(handle_user_message, F.chat.id != ADMIN_CHAT_ID)
    dp.callback_query.register(button_handler, F.data.startswith('banlist_'))

    try:
        logger.info("Бот запущен.")
        await dp.start_polling(bot)
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())