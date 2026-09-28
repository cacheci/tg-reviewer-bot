from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from src.config.settings import TG_REVIEWER_GROUP
from src.database.operations import Banned_origin, Banned_user, Muted_user
from src.common.utils import get_name_from_uid, is_integer, generate_userinfo_str, get_binded_from_string
from src.strings import others as strings_others
from src.strings import submitter as strings_submitter

from datetime import datetime, timedelta, timezone
import re

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, is_spam: bool):
    usage = strings_others["spam_usage" if is_spam else "ban_usage"]
    arg_1, arg_2 = None, None
    if context.args:
        arg_1, arg_2 = context.args[0], " ".join(context.args[1:])
        if arg_1.startswith(("#USER_","#SUBMITTER_")):
            if arg_1.startswith("#USER_"):
                arg_1 = arg_1[6:]
            elif arg_1.startswith("#SUBMITTER_"):
                arg_1 = arg_1[11:]

    if not arg_2:
        # Try find unprovided id or reason from replied message
        if update.message.reply_to_message:
            replyto_user_id = str(update.message.reply_to_message.from_user.id)
            self_id = str((await context.bot.get_me()).id)
            if replyto_user_id == self_id:
                # Id not provided, meaning arg is reason if exists.
                tag_unban_id = re.findall(r"#UNBAN_(\d+)", update.message.reply_to_message.text)
                tag_submitter_id = re.findall(r"#SUBMITTER_(\d+)", update.message.reply_to_message.text)
                if tag_unban_id:
                    arg_2 = arg_1
                    user = tag_unban_id[0]
                elif tag_submitter_id:
                    arg_2 = arg_1
                    user = tag_submitter_id[0]
                else:
                    await update.message.reply_text(
                        usage,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    return

                # `arg_2` still null, meaning both arg empty, so the reason should be replied message.
                if not arg_2:
                    reason = f"${{bindmsg:{update.message.reply_to_message.id}}}"
                else:
                    reason = arg_2
            else:
                await update.message.reply_text(
                    usage,
                        parse_mode=ParseMode.MARKDOWN_V2,
                )
                return
        else:
            await update.message.reply_text(
                usage,
                    parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
    else:
        # `arg_2` exist, meaning both arg provided.
        user = arg_1
        reason = arg_2

    if (not user.isdigit()) or (len(user) < 6) or (len(user) > 11):
        await update.message.reply_text(
            strings_others["invalid_id_bold"].format(user_id=escape_markdown(user,version=2,)),
                parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if not reason:
        await update.message.reply_text(
            strings_others["provide_ban_reason"],
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # prevent dumplicated ban
    if Banned_user.is_banned(user):
        await update.message.reply_text(
            strings_others["already_banned"].format(target=user)
            + await get_banned_user_info(
                context, Banned_user.get_banned_user(user)
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    username, fullname = await get_name_from_uid(context, user)
    Banned_user.ban_user(
        user,
        username,
        fullname,
        update.effective_user.id,
        reason,
        is_spam=is_spam,
    )
    if Banned_user.is_banned(user):
        await update.message.reply_text(
            await get_banned_user_info(
                context, Banned_user.get_banned_user(user)
            )
            + escape_markdown(
                f"\n\n#BAN_{user} #USER_{user} #OPERATOR_{update.effective_user.id}",
                version=2,
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await update.message.reply_text(
            strings_others["ban_failed"].format(target=user),
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def get_banned_user_info(context: ContextTypes.DEFAULT_TYPE, user, mention = True):
    banned_userinfo = generate_userinfo_str(id=int(user.user_id),username=user.user_name,fullname=user.user_fullname,boldfullname=True,mention=mention)
    banned_by_username, banned_by_fullname = await get_name_from_uid(
        context, user.banned_by
    )
    banned_by_userinfo = generate_userinfo_str(id=int(user.banned_by),username=banned_by_username,fullname=banned_by_fullname,boldfullname=True,mention=mention)

    ban_reason, ban_bind_message = get_binded_from_string(user['banned_reason'], "bindmsg:")
    if ban_bind_message:
        ban_reason = strings_others["banned_reason_is_message"].format(
            url = f"https://t.me/c/{TG_REVIEWER_GROUP[4:]}/{ban_reason}"
        )
    else:
        ban_reason = f"`{escape_markdown(ban_reason, version=2)}`"

    users_string = strings_others["banned_info"].format(
        target=banned_userinfo,
        date=escape_markdown(str(user['banned_date']), version=2),
        operator=banned_by_userinfo,
        reason=ban_reason,
    )
    return users_string


async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        if update.message.reply_to_message:
            tag_ban_id = re.findall(r"#BAN_(\d+)", update.message.reply_to_message.text)
            tag_submitter_id = re.findall(r"#SUBMITTER_(\d+)", update.message.reply_to_message.text)
            if tag_ban_id:
                user = tag_ban_id[0]
            elif tag_submitter_id:
                user = tag_submitter_id[0]
            else:
                await update.message.reply_text(
                    strings_others["unban_usage"],
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                return
        else:
            await update.message.reply_text(
                strings_others["unban_usage"],
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
    else:
        user = context.args[0]

    if user.startswith(("#USER_","#SUBMITTER_","#BAN_")):
        if user.startswith("#USER_"):
            user = user[6:]
        elif user.startswith("#SUBMITTER_"):
            user = user[11:]
        elif user.startswith("#BAN_"):
            user = user[5:]

    if not user.isdigit():
        await update.message.reply_text(
            strings_others["invalid_id_bold"].format(user_id=escape_markdown(user,version=2,)),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    Banned_user.unban_user(user)
    if Banned_user.is_banned(user):
        await update.message.reply_text(
            strings_others["unban_failed"].format(target=user),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await update.message.reply_text(
            f"`{user}` "
            + escape_markdown(
                strings_others["unban_success"].format(target=user, operator=update.effective_user.id),
                version=2,
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def list_banned_users(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    users = Banned_user.get_banned_users()
    list_banned_users_page_count = 1
    users_string = (
        strings_others["banned_users_page"].format(page=list_banned_users_page_count)
        if users else strings_others["no_banned_users"]
    )
    for user in users:
        new_banned_usr_str = f"\\- {await get_banned_user_info(context, user, mention=False)}\n"
        if len(users_string + new_banned_usr_str) >= 1300:
            users_string += strings_others["continued"]
            await update.message.reply_text(
                users_string,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            list_banned_users_page_count += 1
            users_string = strings_others["banned_users_page"].format(
                page=list_banned_users_page_count
            )
        users_string += new_banned_usr_str
    await update.message.reply_text(
        users_string,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def ban_origin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            strings_others["provide_origin_reason"],
        )
        return
    origin, result = context.args[0], context.args[1:]
    if not is_integer(origin):
        await update.message.reply_text(
            strings_others["invalid_id_bold"].format(user_id=escape_markdown(origin,version=2,)),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    if Banned_origin.is_banned(origin):
        await update.message.reply_text(
            strings_others["already_banned"].format(target=origin)
            + await get_banned_origin_info(
                context, Banned_origin.get_banned_origin(origin)
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    if not result:
        await update.message.reply_text(
            strings_others["provide_ban_reason"],
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    Banned_origin.ban_origin(
        origin, update.effective_user.id, " ".join(result)
    )
    if Banned_origin.is_banned(origin):
        await update.message.reply_text(
            await get_banned_origin_info(
                context, Banned_origin.get_banned_origin(origin)
            )
            + escape_markdown(
                f'\n\n#BAN_ORIGIN_{origin.replace("-", "")} #OPERATOR_{update.effective_user.id}',
                version=2,
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await update.message.reply_text(
            strings_others["ban_failed"].format(target=origin),
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def get_banned_origin_info(context: ContextTypes.DEFAULT_TYPE, origin):
    banned_origininfo = f"`{origin.origin_id}`"
    banned_by_username, banned_by_fullname = await get_name_from_uid(
        context, origin.banned_by
    )
    banned_by_origininfo = generate_userinfo_str(id=int(origin.banned_by),fullname=banned_by_fullname,username=banned_by_username)
    origins_string = strings_others["banned_info"].format(
        target=banned_origininfo,
        date=escape_markdown(str(origin['banned_date']), version=2),
        operator=banned_by_origininfo,
        reason=escape_markdown(origin['banned_reason'], version=2),
    )
    return origins_string


async def unban_origin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            strings_others["provide_origin"],
        )
        return
    origin = context.args[0]

    Banned_origin.unban_origin(origin)
    if Banned_origin.is_banned(origin):
        await update.message.reply_text(
            strings_others["origin_unban_failed"].format(target=origin),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await update.message.reply_text(
            f"*{escape_markdown(origin, version=2,)}* "
            + escape_markdown(
                strings_others["origin_unban_success"].format(target=origin.replace("-", ""), operator=update.effective_user.id),
                version=2,
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def list_banned_origins(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    origins = Banned_origin.get_banned_origins()
    origins_string = strings_others["banned_origins"] if origins else strings_others["no_banned_origins"]
    for origin in origins:
        origins_string += (
            f"\\- {await get_banned_origin_info(context, origin)}\n"
        )
    await update.message.reply_text(
        origins_string,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def mute_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    args = context.args or []
    replied_message = update.message.reply_to_message
    explicit_user = len(args) >= 3

    if explicit_user:
        user = args[0]
        duration_text = args[1]
        reason = " ".join(args[2:])
        if user.startswith("#USER_"):
            user = user[6:]
        elif user.startswith("#SUBMITTER_"):
            user = user[11:]
    else:
        if not replied_message or not replied_message.from_user:
            await update.message.reply_text(strings_others["mute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
            return
        if replied_message.from_user.id != (await context.bot.get_me()).id:
            await update.message.reply_text(strings_others["mute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
            return
        replied_text = replied_message.text or replied_message.caption or ""
        tagged_user = re.search(r"#UNBAN_(\d+)|#SUBMITTER_(\d+)", replied_text)
        if not tagged_user:
            await update.message.reply_text(strings_others["mute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
            return
        user = tagged_user.group(1) or tagged_user.group(2)
        duration_text = args[0] if args else "7d"
        reason = " ".join(args[1:]) or f"${{bindmsg:{replied_message.id}}}"

    if not user.isdigit() or not 6 <= len(user) <= 11 or not reason:
        await update.message.reply_text(strings_others["mute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
        return

    try:
        if duration_text.isdigit():
            days, hours = 0, int(duration_text)
            duration_text += "h"
        else:
            duration_match = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?", duration_text)
            if not duration_match or not any(duration_match.groups()):
                await update.message.reply_text(strings_others["mute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
                return
            days = int(duration_match.group(1) or 0)
            hours = int(duration_match.group(2) or 0)
        duration = timedelta(days=days, hours=hours)
        muted_until = datetime.now(timezone.utc) + duration
    except (OverflowError, ValueError):
        await update.message.reply_text(strings_others["mute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
        return
    if duration.total_seconds() <= 0:
        await update.message.reply_text(strings_others["mute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
        return

    Muted_user.mute_user(
        user,
        update.effective_user.id,
        muted_until,
        reason,
    )
    local_until = datetime.fromtimestamp(muted_until.timestamp()).strftime("%Y-%m-%d %H:%M:%S")
    notify_failed = False
    try:
        await context.bot.send_message(
            chat_id=int(user),
            text=strings_submitter["muted"].format(until=local_until),
        )
    except TelegramError:
        notify_failed = True

    user_name, user_fullname = await get_name_from_uid(context, user)
    operator_name, operator_fullname = await get_name_from_uid(
        context, update.effective_user.id
    )
    target_info = generate_userinfo_str(
        id=int(user), username=user_name, fullname=user_fullname, boldfullname=True
    )
    operator_info = generate_userinfo_str(
        id=update.effective_user.id,
        username=operator_name,
        fullname=operator_fullname,
        boldfullname=True,
    )
    bound_message_id, is_bound_message = get_binded_from_string(reason, "bindmsg:")
    display_reason = (
        strings_others["banned_reason_is_message"].format(
            url=f"https://t.me/c/{TG_REVIEWER_GROUP[4:]}/{bound_message_id}"
        )
        if is_bound_message and bound_message_id.isdigit()
        else f"`{escape_markdown(reason, version=2)}`"
    )
    group_message = strings_others["mute_success"].format(
        target=target_info,
        date=escape_markdown(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), version=2),
        operator=operator_info,
        reason=display_reason,
        duration=escape_markdown(duration_text, version=2),
        until=escape_markdown(local_until, version=2),
        target_id=user,
        operator_id=update.effective_user.id,
    )
    if notify_failed:
        group_message += "\n" + escape_markdown(strings_others["mute_notify_failed"], version=2)
    await update.message.reply_text(
        group_message,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def unmute_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    args = context.args or []
    if len(args) == 1:
        user = args[0]
        for prefix in ("#MUTE_", "#SUBMITTER_", "#USER_"):
            if user.startswith(prefix):
                user = user[len(prefix):]
                break
    elif not args:
        replied_message = update.message.reply_to_message
        if not replied_message or not replied_message.from_user:
            await update.message.reply_text(strings_others["unmute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
            return
        if replied_message.from_user.id != (await context.bot.get_me()).id:
            await update.message.reply_text(strings_others["unmute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
            return
        replied_text = replied_message.text or replied_message.caption or ""
        tagged_user = re.search(r"#MUTE_(\d+)|#SUBMITTER_(\d+)", replied_text)
        if not tagged_user:
            await update.message.reply_text(strings_others["unmute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
            return
        user = tagged_user.group(1) or tagged_user.group(2)
    else:
        await update.message.reply_text(strings_others["unmute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
        return

    if not user.isdigit():
        await update.message.reply_text(strings_others["unmute_usage"], parse_mode=ParseMode.MARKDOWN_V2)
        return
    if not Muted_user.get_muted_user(user):
        await update.message.reply_text(
            strings_others["not_muted"].format(target=user)
        )
        return

    Muted_user.unmute_user(user)
    if Muted_user.get_muted_user(user):
        await update.message.reply_text(
            strings_others["unmute_failed"].format(target=user)
        )
        return
    await update.message.reply_text(
        strings_others["unmute_success"].format(
            target=user,
            operator=update.effective_user.id,
        )
    )


async def ban_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    await ban_user(update, context, False)

async def spam_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    await ban_user(update, context, True)
