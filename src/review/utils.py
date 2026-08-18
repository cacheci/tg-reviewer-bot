import base64
import binascii
import pickle
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import MessageOriginType, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from src.database.operations import Banned_user, Reviewer, Submitter, current_month_key
from src.config.settings import (
    APPROVE_NUMBER_REQUIRED,
    REJECT_NUMBER_REQUIRED,
    REJECTION_REASON,
    TG_PUBLISH_CHANNEL,
    TG_REJECT_REASON_USER_LIMIT,
    TG_REJECTED_CHANNEL,
    TG_RETRACT_NOTIFY,
    TG_REVIEWER_GROUP,
)
from src.common.utils import send_result_to_submitter, send_submission, sanitize_userinfo, generate_userinfo_str
from src.strings import channel as strings_channel
from src.strings import reviewer as strings_reviewer
from src.strings import submitter as strings_submitter

"""
submission_meta = {
    "submitter": [submitter.id, submitter.username, submitter.full_name, first_submission_message.id],
    "reviewer": {
        reviewer1.id: [reviewer1.username, reviewer1.full_name, option1],
        reviewer2.id: [reviewer2.username, reviewer2.full_name, option2],
        ...
    },
    "media_id_list": [media1.id, media2.id, ...],
    "media_type_list": [media1.type, media2.type, ...],
    "documents_id_list": [document1.id, document2.id, ...],
    "document_type_list": [document1.type, document2.type, ...],
    "append": {
        reviewer1.full_name: ["审核注：...", ...],
        reviewer2.full_name: ["审核注：...", ...],
    },
    "sent_msg": {
        publish_channel1: [msg_id1, msg_id2, ...],
        publish_channel2: [msg_id1, msg_id2, ...],
    }
}
"""


class ReviewChoice:
    SFW = "0"
    NSFW = "1"
    REJECT = "2"
    REJECT_DUPLICATE = "3"
    QUERY = "4"
    WITHDRAW = "5"
    APPROVED_RETRACT = "6"


class SubmissionStatus:
    PENDING = 0
    APPROVED = 1
    REJECTED = 2
    REJECTED_NO_REASON = 3


async def reply_review_message(
    first_submission_message, submission_meta, context
):
    # reply the first submission_message and show the inline keyboard to let the reviewers to decide whether to publish it
    inline_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    strings_reviewer["approve"],
                    callback_data=f"{ReviewChoice.SFW}.{first_submission_message.message_id}",
                ),
                InlineKeyboardButton(
                    strings_reviewer["approve_nsfw"],
                    callback_data=f"{ReviewChoice.NSFW}.{first_submission_message.message_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    strings_reviewer["reject"],
                    callback_data=f"{ReviewChoice.REJECT}.{first_submission_message.message_id}",
                ),
                InlineKeyboardButton(
                    strings_reviewer["reject_duplicate"],
                    callback_data=f"{ReviewChoice.REJECT_DUPLICATE}.{first_submission_message.message_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    strings_reviewer["query_vote"],
                    callback_data=f"{ReviewChoice.QUERY}.{first_submission_message.message_id}",
                ),
                InlineKeyboardButton(
                    strings_reviewer["withdraw_vote"],
                    callback_data=f"{ReviewChoice.WITHDRAW}.{first_submission_message.message_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    strings_reviewer["add_note"],
                    switch_inline_query_current_chat="/append ",
                ),
                InlineKeyboardButton(
                    strings_reviewer["remove_note"],
                    switch_inline_query_current_chat="/remove_append ",
                ),
            ],
            [
                InlineKeyboardButton(
                    strings_reviewer["reply_submitter"],
                    switch_inline_query_current_chat="/comment ",
                ),
            ],
        ]
    )

    try:
        await first_submission_message.reply_text(
            generate_submission_meta_string(submission_meta),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=inline_keyboard,
        )
    except BadRequest as br:
        if br.message.startswith("Entities_too_long"):
            submitter_id, submitter_username, submitter_fullname, _ = (
                submission_meta["submitter"]
            )
            await first_submission_message.reply_text(
                escape_markdown(
                    strings_reviewer["submission_too_long"].format(
                        submitter=(
                            f"{submitter_fullname} "
                            f"({f'@{submitter_username}, ' if submitter_username else ''}{submitter_id})"
                        ),
                        submitter_id=submitter_id,
                    ),
                    version=2,
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            await send_result_to_submitter(
                context,
                submission_meta["submitter"][0],
                submission_meta["submitter"][3],
                strings_submitter["too_long"],
            )


async def reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    review_message = update.effective_message
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )

    reviewer_id, reviewer_username, reviewer_fullname = (
        query.from_user.id,
        query.from_user.username,
        query.from_user.full_name,
    )

    if TG_REJECT_REASON_USER_LIMIT:
        # if the reviewer has not rejected the submission
        if (
            reviewer_id not in submission_meta["reviewer"]
            or submission_meta["reviewer"][reviewer_id][2]
            != ReviewChoice.REJECT
        ):
            await query.answer(strings_reviewer["no_reject_vote"])
            return

    # if the reviewer has rejected the submission
    match query.data:
        case "REASON.IGNORE":
            # IGNORE's index is the length of REJECTION_REASON (means the last number)
            reason = len(REJECTION_REASON)
        case _:
            # every rejection reason has an index, see REJECTION_REASON
            reason = int(query.data.split(".")[1])

    submission_meta["reviewer"][reviewer_id] = [
        reviewer_username,
        reviewer_fullname,
        reason,
    ]
    await query.answer()

    # send the submittion to rejected channel
    await send_to_rejected_channel(
        update=update, context=context, submission_meta=submission_meta
    )


async def send_to_rejected_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    submission_meta=None,
    is_custom=False,
):
    user_id = update.effective_user.id
    review_message = update.effective_message
    if is_custom:
        review_message = review_message.reply_to_message

    # get all append messages from submission_meta['append']
    append_messages = []
    for append_list in submission_meta["append"].values():
        append_messages.extend(append_list)
    append_messages_string = "\n".join(append_messages)

    inline_keyboard_content = []
    button_to_rejected_channel = None
    inline_keyboard_content.append(
        [
            InlineKeyboardButton(
                strings_reviewer["reply_submitter"],
                switch_inline_query_current_chat="/comment ",
            )
        ]
    )

    # if has rejected channel and not IGNORE, forward rejected message to it
    if (
        TG_REJECTED_CHANNEL
        and not Banned_user.is_banned(submission_meta["submitter"][0])
        and submission_meta["reviewer"][user_id][2] != len(REJECTION_REASON)
    ):
        # send the submittion to rejected channel
        sent_message = await send_submission(
            context=context,
            chat_id=TG_REJECTED_CHANNEL,
            media_id_list=submission_meta["media_id_list"],
            media_type_list=submission_meta["media_type_list"],
            documents_id_list=submission_meta["documents_id_list"],
            document_type_list=submission_meta["document_type_list"],
            text=submission_meta["text"] + "\n" + append_messages_string,
        )
        button_to_rejected_channel = [
            [
                InlineKeyboardButton(
                    strings_channel["view_rejected"], url=sent_message[-1].link
                )
            ],
        ]

        inline_keyboard_content.extend(button_to_rejected_channel)

    # if not IGNORE, forward rejected message to it
    if submission_meta["reviewer"][user_id][2] != len(REJECTION_REASON):
        # send result to submitter
        reason = strings_reviewer["rejection_reason"].format(
            reason=get_rejection_reason_text(submission_meta['reviewer'][user_id][2])
        )
        await send_result_to_submitter(
            context,
            submission_meta["submitter"][0],
            submission_meta["submitter"][3],
            strings_submitter["rejected"].format(reason=reason),
            # link to rejected submission button
            inline_keyboard_markup=(
                InlineKeyboardMarkup(button_to_rejected_channel)
                if button_to_rejected_channel
                else None
            ),
        )

    # delete reason buttons and reserve the comment button and rejected channel link button
    await review_message.edit_text(
        text=generate_submission_meta_string(submission_meta),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup(inline_keyboard_content),
    )


async def append_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    append_message = update.message.text_markdown_v2_urled.split("/append ")[1]
    if not update.message.reply_to_message:
        return
    review_message = update.message.reply_to_message
    # if there is not a submission_meta in the review_message
    if "\u200b" not in review_message.text_markdown_v2_urled:
        return
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )
    if get_submission_status(submission_meta)[0] != SubmissionStatus.PENDING:
        await update.message.reply_text(strings_reviewer["only_pending_add_note"])
        return
    reviewer_fullname = update.message.from_user.full_name
    if reviewer_fullname not in submission_meta["append"]:
        submission_meta["append"][reviewer_fullname] = []
    submission_meta["append"][reviewer_fullname].append(
        strings_reviewer["note_prefix"].format(message=append_message)
    )
    await update.message.reply_text(strings_reviewer["note_added"])
    await review_message.edit_text(
        text=generate_submission_meta_string(submission_meta),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=review_message.reply_markup,
    )


async def remove_append_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    append_message_num = update.message.text.split("/remove_append ")[1]
    if not update.message.reply_to_message:
        return
    review_message = update.message.reply_to_message
    # if there is not a submission_meta in the review_message
    if "\u200b" not in review_message.text:
        return
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )
    if get_submission_status(submission_meta)[0] != SubmissionStatus.PENDING:
        await update.message.reply_text(strings_reviewer["only_pending_remove_note"])
        return
    reviewer_fullname = update.message.from_user.full_name
    if reviewer_fullname not in submission_meta["append"]:
        await update.message.reply_text(strings_reviewer["no_note"])
        return
    try:
        append_message_num = int(append_message_num)
    except:
        await update.message.reply_text(strings_reviewer["invalid_note_number"])
        return
    if append_message_num < 1 or append_message_num > len(
        submission_meta["append"][reviewer_fullname]
    ):
        await update.message.reply_text(strings_reviewer["invalid_note_number"])
        return
    submission_meta["append"][reviewer_fullname].pop(append_message_num - 1)
    if not submission_meta["append"][reviewer_fullname]:
        del submission_meta["append"][reviewer_fullname]
    await update.message.reply_text(strings_reviewer["note_removed"])
    await review_message.edit_text(
        text=generate_submission_meta_string(submission_meta),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=review_message.reply_markup,
    )


async def comment_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment_message = update.message.text_markdown_v2_urled.split("/comment ")[
        1
    ]
    if not update.message.reply_to_message:
        return
    review_message = update.message.reply_to_message
    # if there is not a submission_meta in the review_message
    if "\u200b" not in review_message.text_markdown_v2_urled:
        return
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )
    await send_result_to_submitter(
        context,
        submission_meta["submitter"][0],
        submission_meta["submitter"][3],
        strings_reviewer["review_message"].format(message=comment_message),
    )
    await update.message.reply_text(strings_reviewer["sent"])


async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_message = update.message.reply_to_message
    if not reply_message:
        await update.message.reply_text(
            strings_reviewer["track_usage"]
        )
        return

    forward_origin = reply_message.forward_origin
    publish_channel_ids = {int(channel_id) for channel_id in TG_PUBLISH_CHANNEL}
    if (
        not forward_origin
        or forward_origin.type != MessageOriginType.CHANNEL
        or forward_origin.chat.id not in publish_channel_ids
    ):
        await update.message.reply_text(
            strings_reviewer["track_not_channel_post"].format(
                usage=strings_reviewer["track_usage"]
            )
        )
        return

    reply_text = (
        reply_message.text_markdown_v2_urled
        or reply_message.caption_markdown_v2_urled
        or ""
    )
    tracking_tokens = re.findall(
        r"https?://t\.me/([A-Za-z0-9_-]+={0,2})", reply_text
    )
    if not tracking_tokens:
        await update.message.reply_text(
            strings_reviewer["track_missing"].format(usage=strings_reviewer["track_usage"])
        )
        return

    try:
        tracking_meta = pickle.loads(
            base64.urlsafe_b64decode(tracking_tokens[-1])
        )
        review_message_id = int(tracking_meta["review_message_id"])
    except (
        binascii.Error,
        EOFError,
        ValueError,
        KeyError,
        TypeError,
        pickle.UnpicklingError,
    ):
        await update.message.reply_text(strings_reviewer["track_invalid"])
        return

    reviewer_group_id = str(TG_REVIEWER_GROUP)
    if reviewer_group_id.startswith("-100"):
        reviewer_group_id = reviewer_group_id[4:]
    await update.message.reply_text(
        f"https://t.me/c/{reviewer_group_id}/{review_message_id}",
        disable_web_page_preview=True,
    )


async def send_custom_rejection_reason(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    reject_msg = update.message.text_markdown_v2_urled.split("/reject ", 1)[1]
    if not update.message.reply_to_message:
        return
    review_message = update.message.reply_to_message

    # if there is not a submission_meta in the review_message
    if "\u200b" not in review_message.text_markdown_v2_urled:
        return
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )

    # if the submission has not been rejected yet
    status = get_submission_status(submission_meta)
    if (
        strings_reviewer["rejected_title_marker"] not in review_message.text_markdown_v2_urled
        or status[1] == strings_reviewer["retracted_status"]
    ):
        return

    user = update.message.from_user
    reviewer_id, reviewer_username, reviewer_fullname = (
        user.id,
        user.username,
        user.full_name,
    )

    if TG_REJECT_REASON_USER_LIMIT:
        # if the reviewer has not rejected the submission
        if reviewer_id not in submission_meta["reviewer"] or submission_meta[
            "reviewer"
        ][reviewer_id][2] in [
            ReviewChoice.SFW,
            ReviewChoice.NSFW,
        ]:
            await update.message.reply_text(strings_reviewer["no_reject_vote"])
            return
    # if the reviewer has rejected the duplicate submission without other reviewer rejecting it
    options = [
        reviewer[2] for reviewer in submission_meta["reviewer"].values()
    ]
    approve_num = options.count(ReviewChoice.NSFW) + options.count(
        ReviewChoice.SFW
    )

    if reviewer_id in submission_meta["reviewer"]:
        if submission_meta["reviewer"][reviewer_id][
            2
        ] == ReviewChoice.REJECT_DUPLICATE and approve_num + 1 == len(options):
            await update.message.reply_text(strings_reviewer["duplicate_reason_locked"])
            return
        # if the reason has not been changed
        if submission_meta["reviewer"][reviewer_id][2] == reject_msg:
            return

    submission_meta["reviewer"][reviewer_id] = [
        reviewer_username,
        reviewer_fullname,
        reject_msg,
    ]
    await send_to_rejected_channel(update, context, submission_meta, True)
    await update.message.reply_text(strings_reviewer["sent"])
    # delete the custom rejection reason message if the bot can
    try:
        await update.message.delete()
    except:
        pass


def get_decision(submission_meta, reviewer_id):
    if reviewer_id not in submission_meta["reviewer"]:
        return strings_reviewer["no_vote"]
    choice = strings_reviewer["choice_prefix"]
    match submission_meta["reviewer"][reviewer_id][2]:
        case ReviewChoice.SFW:
            choice += strings_reviewer["approve"]
        case ReviewChoice.NSFW:
            choice += strings_reviewer["approve_nsfw"]
        case ReviewChoice.REJECT:
            choice += strings_reviewer["reject"]
    return choice


def remove_decision(submission_meta, reviewer_id):
    if reviewer_id in submission_meta["reviewer"]:
        reviewer_months = submission_meta.get("stats_month", {}).get(
            "reviewers", {}
        )
        reviewer_month = reviewer_months.get(reviewer_id, current_month_key())
        # decrease reviewer count
        if submission_meta["reviewer"][reviewer_id][2] in [
            ReviewChoice.SFW,
            ReviewChoice.NSFW,
        ]:
            Reviewer.count_increase(
                reviewer_id, "approve_count", -1, month=reviewer_month
            )
        else:
            Reviewer.count_increase(
                reviewer_id, "reject_count", -1, month=reviewer_month
            )
        reviewer_months.pop(reviewer_id, None)
        del submission_meta["reviewer"][reviewer_id]
        return submission_meta, True
    else:
        return submission_meta, False


async def retract_approved_submission(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    review_message = update.effective_message
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )
    if query.from_user.id not in submission_meta["reviewer"]:
        await query.answer(strings_reviewer["no_vote"])
        return
    if submission_meta["reviewer"][query.from_user.id][2] not in [
        ReviewChoice.SFW,
        ReviewChoice.NSFW,
    ]:
        await query.answer(strings_reviewer["no_approve_vote"])
        return
    try:
        for publish_channel, msg_ids in submission_meta["sent_msg"].items():
            await context.bot.delete_messages(
                chat_id=publish_channel, message_ids=msg_ids
            )
        await query.answer(strings_reviewer["withdrawn"])
        submission_meta["reviewer"][query.from_user.id][2] = strings_reviewer["retracted_status"]
        inline_keyboard = None
        await review_message.edit_text(
            text=generate_submission_meta_string(submission_meta),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=inline_keyboard,
        )
        # send result to submitter
        if TG_RETRACT_NOTIFY:
            await send_result_to_submitter(
                context,
                submission_meta["submitter"][0],
                submission_meta["submitter"][3],
                strings_submitter["retracted"],
            )
        # modify stats data
        result_month = submission_meta.get("stats_month", {}).get(
            "result", current_month_key()
        )
        Submitter.count_increase(
            submission_meta["submitter"][0],
            "approved_count",
            -1,
            month=result_month,
        )
        Submitter.count_increase(
            submission_meta["submitter"][0],
            "rejected_count",
            month=current_month_key(),
        )
    except:
        await query.answer(
            strings_reviewer["retract_failed"]
        )


def get_rejection_reason_text(option):
    # rejection reason is an int value, see reject_reason()
    if isinstance(option, int):
        if option < len(REJECTION_REASON):
            option_text = REJECTION_REASON[option]
        elif option == len(REJECTION_REASON):  # JUST IGNORE IT!!
            option_text = strings_reviewer["rejection_ignore"]
    elif option == ReviewChoice.REJECT_DUPLICATE:
        option_text = strings_reviewer["rejection_duplicate"]
    else:
        option_text = option
    return option_text


def get_submission_status(submission_meta, longago_status=0):
    if longago_status == SubmissionStatus.APPROVED:
        return SubmissionStatus.APPROVED, ""

    status = -1
    rejection_reason = ""
    review_options = [
        reviewer[2] for reviewer in submission_meta["reviewer"].values()
    ]
    approve_num = review_options.count(
        ReviewChoice.NSFW
    ) + review_options.count(ReviewChoice.SFW)
    reject_noreason_num = review_options.count(ReviewChoice.REJECT)
    reject_reason_num = len(review_options) - approve_num - reject_noreason_num

    if approve_num >= APPROVE_NUMBER_REQUIRED:
        status = SubmissionStatus.APPROVED
    elif reject_reason_num > 0:
        # At least one reviewer has given rejection reason
        status = SubmissionStatus.REJECTED
        for review_option in review_options:
            if review_option not in [
                ReviewChoice.NSFW,
                ReviewChoice.SFW,
                ReviewChoice.REJECT,
            ]:
                rejection_reason = get_rejection_reason_text(review_option)
                break
    elif reject_noreason_num >= REJECT_NUMBER_REQUIRED or longago_status == SubmissionStatus.REJECTED:
        status = SubmissionStatus.REJECTED_NO_REASON
    else:
        status = SubmissionStatus.PENDING
    return status, rejection_reason


def generate_submission_meta_string(submission_meta, longago_status=0):
    # generate the submission_meta string from the submission_meta
    # get status and rejection reason
    status, rejection_reason = get_submission_status(submission_meta, longago_status)

    # submitter_string
    submitter_id, submitter_username, submitter_fullname, _ = submission_meta[
        "submitter"
    ]
    submitter_string = strings_reviewer["submitter_label"].format(
        submitter=generate_userinfo_str(
            id=int(submitter_id),
            username=submitter_username,
            fullname=submitter_fullname,
        )
    )

    # reviewers_string
    is_nsfw = False
    reviewers_string = strings_reviewer["reviewers_label"]
    if status == SubmissionStatus.PENDING:
        reviewers_string += strings_reviewer["reviewers_hidden"]
    else:
        for reviewer_id, [
            reviewer_username,
            reviewer_fullname,
            option,
        ] in submission_meta["reviewer"].items():
            option_text = ""
            option_sign = ""
            match option:
                case ReviewChoice.SFW:
                    option_text = strings_reviewer["option_sfw"]
                    option_sign = "🟢"
                case ReviewChoice.NSFW:
                    option_text = strings_reviewer["option_nsfw"]
                    option_sign = "🟡"
                    is_nsfw = True
                case ReviewChoice.REJECT:
                    option_text = strings_reviewer["option_reject"]
                    option_sign = "🔴"
                case _:
                    option_text = strings_reviewer["option_reject_reason"].format(
                        reason=get_rejection_reason_text(option)
                    )
                    option_sign = "🔴"
            reviewers_string += strings_reviewer["reviewer_line"].format(
                sign=option_sign,
                reviewer=generate_userinfo_str(
                    id=int(reviewer_id),
                    fullname=reviewer_fullname,
                    username=reviewer_username,
                ),
                option=option_text,
            )

    # append_string
    append_string = strings_reviewer["notes_label"]
    for reviewer_fullname, append_list in submission_meta["append"].items():
        append_string += strings_reviewer["note_owner"].format(
            reviewer=sanitize_userinfo(
                escape_markdown(reviewer_fullname, version=2)
            )
        )
        append_string += "".join(
            f"\n    {i+1}\\. {escape_markdown(message,version=2)}" for i, message in enumerate(append_list)
        )

    if append_string == strings_reviewer["notes_label"]:
        append_string = ""
    else:
        append_string = "\n" + append_string + "\n"

    # status_string and status_tag
    status_string = ""
    status_tag = ""
    match status:
        case SubmissionStatus.PENDING:
            status_string = strings_reviewer["status_pending"]
            status_tag = "#PENDING"
        case SubmissionStatus.APPROVED:
            status_string = (
                strings_reviewer["status_approved_nsfw"]
                if is_nsfw
                else strings_reviewer["status_approved_sfw"]
            )
            status_tag = "#APPROVED #SFW" if not is_nsfw else "#APPROVED #NSFW"
        case SubmissionStatus.REJECTED:
            status_string = strings_reviewer["status_rejected"].format(
                reason=rejection_reason
            )
            status_tag = "#REJECTED"
        case SubmissionStatus.REJECTED_NO_REASON:
            status_string = strings_reviewer["status_rejected_pending_reason"]
            status_tag = "#PENDING_FOR_REASON"

    # status_title
    status_title = (
        strings_reviewer["title_pending"]
        if status == SubmissionStatus.PENDING
        else (
            strings_reviewer["title_approved"]
            if status == SubmissionStatus.APPROVED
            else strings_reviewer["title_rejected"]
        )
    )
    # tags
    tags = f"#USER_{submitter_id} #SUBMITTER_{submitter_id}"
    if status != SubmissionStatus.PENDING:
        for reviewer_id in submission_meta["reviewer"].keys():
            tags += f" #USER_{reviewer_id} #REVIEWER_{reviewer_id}"
    tags += f" {status_tag}"

    submission_meta_text = f"[\u200b](http://t.me/{base64.urlsafe_b64encode(pickle.dumps(submission_meta)).decode()})"
    visible_content = (
        status_title
        + "\n\n"
        + submitter_string
        + "\n"
        + reviewers_string
        + "\n"
        + append_string
        + "\n"
        + strings_reviewer["current_status"].format(status=status_string)
        + "\n\n"
        + escape_markdown(tags, version=2)
    )

    # use Zero-width non-joiner and fake url(or the bot api will delete invalid link) to hide the submission_meta
    return f"{visible_content}{submission_meta_text}"
