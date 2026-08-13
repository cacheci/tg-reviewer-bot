from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from db_op import Banned_origin, Banned_user
from utils import get_name_from_uid, is_integer, generate_userinfo_str
from strings import others as strings_others

import re

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        # need at least reason even if user is from reply
        await update.message.reply_text(
            strings_others["ban_usage"],
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    user, result = context.args[0], context.args[1:]
    if user.startswith(("#USER_","#SUBMITTER_")):
        if user.startswith("#USER_"):
            user = user[6:]
        elif user.startswith("#SUBMITTER_"):
            user = user[11:]

    # only reason, no userid. so `user` is just reason
    if not user.isdigit():
        if update.message.reply_to_message:
            replyto_user_id = str(update.message.reply_to_message.from_user.id)
            self_id = str((await context.bot.get_me()).id)
            if replyto_user_id == self_id:
                tag_unban_id = re.findall(r"#UNBAN_(\d+)", update.message.reply_to_message.text)
                tag_submitter_id = re.findall(r"#SUBMITTER_(\d+)", update.message.reply_to_message.text)
                if tag_unban_id:
                    result = user
                    user = tag_unban_id[0]
                elif tag_submitter_id:
                    result = user
                    user = tag_submitter_id[0]
                else:
                    await update.message.reply_text(
                        strings_others["invalid_id_no_submitter"].format(user_id=escape_markdown(user,version=2,)),
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    return
            else:
                await update.message.reply_text(
                    strings_others["invalid_id_not_bot"].format(user_id=escape_markdown(user,version=2,)),
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                return
        else:                
            await update.message.reply_text(
                strings_others["invalid_id_bold"].format(user_id=escape_markdown(user,version=2,)),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
    if Banned_user.is_banned(user):
        await update.message.reply_text(
            strings_others["already_banned"].format(target=user)
            + await get_banned_user_info(
                context, Banned_user.get_banned_user(user)
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

    username, fullname = await get_name_from_uid(context, user)
    Banned_user.ban_user(
        user, username, fullname, update.effective_user.id, " ".join(result)
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
    users_string = strings_others["banned_info"].format(
        target=banned_userinfo,
        date=escape_markdown(str(user['banned_date']), version=2),
        operator=banned_by_userinfo,
        reason=escape_markdown(user['banned_reason'], version=2),
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
