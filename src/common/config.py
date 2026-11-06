import json
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.config import settings
from src.strings import others as strings_others


logger = logging.getLogger(__name__)


def _config_line(name):
    if name in settings.SENSITIVE_CONFIG:
        value = strings_others["config_hidden"]
    else:
        value = json.dumps(settings.get_config(name), ensure_ascii=False)
    return f"{value}"


async def config_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None or user.id not in settings.TG_SUPERADMIN:
        return

    chat = update.effective_chat
    message = update.effective_message
    args = context.args
    if not args:
        await message.reply_text(strings_others["config_usage"], parse_mode=ParseMode.MARKDOWN_V2)
        return
    if len(args) < 2 or args[0].lower() not in ("get", "set"):
        await message.reply_text(strings_others["config_usage"], parse_mode=ParseMode.MARKDOWN_V2)
        return

    action = args[0].lower()
    name = settings.config_name(args[1])
    if name not in settings.EDITABLE_CONFIG and name not in settings.READONLY_CONFIG:
        await message.reply_text(strings_others["config_unknown"].format(name=name), parse_mode=ParseMode.MARKDOWN_V2)
        return
    if action == "get":
        if len(args) != 2:
            await message.reply_text(strings_others["config_usage"], parse_mode=ParseMode.MARKDOWN_V2)
            return
        await message.reply_text(
            strings_others["config_hidden"] if (name in settings.SENSITIVE_CONFIG) else f"`{json.dumps(settings.get_config(name), ensure_ascii=False)}`",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    if name not in settings.EDITABLE_CONFIG:
        await message.reply_text(strings_others["config_not_editable"].format(name=name), parse_mode=ParseMode.MARKDOWN_V2)
        return
    if len(args) < 3:
        await message.reply_text(strings_others["config_usage"], parse_mode=ParseMode.MARKDOWN_V2)
        return

    raw_value = message.text.split(None, 3)[3].strip()
    try:
        value = settings.parse_config_value(name, raw_value)
    except ValueError:
        _, value_type, minimum, maximum = settings.EDITABLE_CONFIG[name]
        await message.reply_text(strings_others["config_invalid_value"].format(name=name), parse_mode=ParseMode.MARKDOWN_V2)
        return

    try:
        settings.set_config(name, value)
    except (OSError, ValueError, TypeError, AttributeError):
        logger.exception("Failed to save configuration: %s", name)
        await message.reply_text(strings_others["config_save_failed"].format(name=name))
        return
    await message.reply_text(strings_others["config_saved"])
