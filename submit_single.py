from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import MessageOriginType
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.helpers import escape_markdown

from db_op import IdempotencyRecord, Submitter, current_month_key
from env import TG_EXPAND_LENGTH, TG_REVIEWER_GROUP, TG_REVIEWONLY
from review_utils import reply_review_message
from utils import (
    check_submission,
    send_result_to_submitter,
    send_submission,
)

media_groups = {}
async def reply_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_submission(update) == False:
        return

    if TG_REVIEWONLY:
        await update.message.reply_text(
            "本 Bot 已暂停使用。请查看频道获取更多信息",
            do_quote=True,)
        return

    message = update.message

    if message.media_group_id:
        if message.media_group_id in media_groups:
            submission = media_groups[message.media_group_id]
        else:
            submission = {
                "media_id_list": [],
                "media_type_list": [],
                "document_id_list": [],
                "document_type_list": [],
            }
        if message.photo:
            submission["media_id_list"].append(message.photo[-1].file_id)
            submission["media_type_list"].append("photo")
        if message.video:
            submission["media_id_list"].append(message.video.file_id)
            submission["media_type_list"].append("video")
        if message.animation:  # GIF
            submission["media_id_list"].append(message.animation.file_id)
            submission["media_type_list"].append("animation")
        # elif because gif is also a document but can not be sent as a group
        elif message.document:
            submission["document_id_list"].append(message.document.file_id)
            submission["document_type_list"].append("document")

        if message.media_group_id in media_groups:
            return
        media_groups[message.media_group_id] = submission

    # show options as an inline keyboard
    keyboard = [
        [
            InlineKeyboardButton(
                "署名投稿", callback_data=f"realname#{message.message_id}"
            ),
            InlineKeyboardButton(
                "匿名投稿", callback_data=f"anonymous#{message.message_id}"
            ),
        ],
        [InlineKeyboardButton("取消投稿", callback_data="cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        """❔确认投稿？（确认后无法编辑内容）

请确认稿件不包含以下内容，否则可能不会被通过：
- 过于哗众取宠、摆拍卖蠢（傻逼不算沙雕）
- 火星救援
- 纯链接（请投稿链接里的内容，如图片、视频等）
- 恶俗性挂人

稿件将由多位管理投票审核，每位管理的审核标准可能不一，投票制可以改善这类问题，但仍可能对部分圈内的梗不太熟悉，请您理解""",
        do_quote=True,
        reply_markup=reply_markup,
    )


async def confirm_submission(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    user = update.effective_user

    confirm_message = update.effective_message
    origin_message = confirm_message.reply_to_message
    action = query.data.split("#")[0]
    operation_key = (
        f"submission:{confirm_message.chat_id}:{confirm_message.message_id}"
    )

    if action == "cancel":
        if not IdempotencyRecord.claim(operation_key, "submission", action):
            await query.answer("该投稿操作已经处理", show_alert=True)
            return
        await query.answer()
        await query.edit_message_text(text="投稿已取消")
        IdempotencyRecord.complete(operation_key)
    elif action in ("anonymous", "realname"):
        record = IdempotencyRecord.get(operation_key)
        if record:
            message = (
                "投稿正在处理中"
                if record.status == "processing"
                else "该投稿操作已经处理"
            )
            await query.answer(message, show_alert=True)
            return
        if await check_submission(update) == False:
            return
        if not IdempotencyRecord.claim(operation_key, "submission", action):
            record = IdempotencyRecord.get(operation_key)
            message = (
                "投稿正在处理中"
                if record and record.status == "processing"
                else "该投稿操作已经处理"
            )
            await query.answer(message, show_alert=True)
            return
        await query.answer()

        text = (
            origin_message.text_markdown_v2_urled
            or origin_message.caption_markdown_v2_urled
            or ""
        )
        if len(text) > TG_EXPAND_LENGTH:
            text = (
                "**>"
                + text.replace("**>", "")
                .replace("||", "")
                .replace("\n>", "\n")
                .replace("\n", "\n>")
                + "||"
            )
        # add forward origin
        if origin_message.forward_origin is not None:
            forward_string = "\n\n_from_ "
            match origin_message.forward_origin.type:
                case MessageOriginType.USER:
                    forward_string += f"[{escape_markdown(origin_message.forward_origin.sender_user.full_name,version=2,)}](tg://user?id={origin_message.forward_origin.sender_user.id})"
                case MessageOriginType.CHAT:
                    forward_string += f"[{escape_markdown(origin_message.forward_origin.sender_chat.title,version=2,)}]({origin_message.forward_origin.sender_chat.link})"
                case MessageOriginType.CHANNEL:
                    forward_string += f"[{escape_markdown(origin_message.forward_origin.chat.title,version=2,)}]({origin_message.forward_origin.chat.link}/{origin_message.forward_origin.message_id})"
                case MessageOriginType.HIDDEN_USER:

                    forward_string += escape_markdown(
                        origin_message.forward_origin.sender_user_name,
                        version=2,
                    )
            text += f"{forward_string}"

        # add submitter sign string
        if query.data.startswith("realname"):
            sign_string = f"_via_ [{escape_markdown(user.full_name,version=2,)}](tg://user?id={user.id})"
            # if the last line is a forward message, put in the same line
            if text.split("\n")[-1].startswith("_from_"):
                text += " " + sign_string
            else:
                text += "\n\n" + sign_string

        if origin_message.media_group_id:
            # is a group of media
            submission = media_groups[origin_message.media_group_id]
            pass
        else:
            # single media or pure text
            submission = {
                "media_id_list": [],
                "media_type_list": [],
                "document_id_list": [],
                "document_type_list": [],
            }
            if origin_message.photo:
                submission["media_id_list"].append(
                    origin_message.photo[-1].file_id
                )
                submission["media_type_list"].append("photo")
            if origin_message.video:
                submission["media_id_list"].append(
                    origin_message.video.file_id
                )
                submission["media_type_list"].append("video")
            if origin_message.sticker:
                submission["media_id_list"].append(
                    origin_message.sticker.file_id
                )
                submission["media_type_list"].append("sticker")
                # just ignore any forward or realname infomation for sticker
                # in single submit mode because it is not allowed to have
                # text with sticker
                text = ""
            if origin_message.animation:  # GIF
                submission["media_id_list"].append(
                    origin_message.animation.file_id
                )
                submission["media_type_list"].append("animation")
            # elif because gif is also a document but can not be sent as a group
            elif origin_message.document:
                submission["document_id_list"].append(
                    origin_message.document.file_id
                )
                submission["document_type_list"].append("document")

        submission_messages = await send_submission(
            context=context,
            chat_id=TG_REVIEWER_GROUP,
            media_id_list=submission["media_id_list"],
            media_type_list=submission["media_type_list"],
            documents_id_list=submission["document_id_list"],
            document_type_list=submission["document_type_list"],
            text=text.strip(),
        )

        submission_month = current_month_key()
        submission_meta = {
            "submitter": [
                user.id,
                user.username,
                user.full_name,
                origin_message.message_id,
            ],
            "reviewer": {},
            "text": text,
            "media_id_list": submission["media_id_list"],
            "media_type_list": submission["media_type_list"],
            "documents_id_list": submission["document_id_list"],
            "document_type_list": submission["document_type_list"],
            "append": {},
            "stats_month": {
                "submission": submission_month,
                "reviewers": {},
            },
        }

        await reply_review_message(
            submission_messages[0], submission_meta, context
        )
        await query.delete_message()
        await send_result_to_submitter(
            context,
            user.id,
            origin_message.message_id,
            "❤️ 投稿成功，阿里嘎多！我们会在稍后通知您审核结果。",
        )

        Submitter.count_increase(
            user.id, "submission_count", month=submission_month
        )
        Submitter.add_count_in_hour(user.id)
        IdempotencyRecord.complete(operation_key)


submission_handler = MessageHandler(
    filters.ChatType.PRIVATE & ~filters.COMMAND, reply_option
)
confirm_submit_handler = CallbackQueryHandler(
    confirm_submission,
    pattern="^(cancel|anonymous|realname)",
)
