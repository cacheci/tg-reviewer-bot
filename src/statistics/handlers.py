from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from src.database.operations import Reviewer, Submitter, current_month_key
from src.config.settings import TG_REVIEWER_GROUP
from src.strings import others as strings_others

import re


def format_submitter_stats(submitter_info):
    submission_count = submitter_info.submission_count if submitter_info else 0
    approved_count = submitter_info.approved_count if submitter_info else 0
    rejected_count = submitter_info.rejected_count if submitter_info else 0
    decided_count = approved_count + rejected_count
    approval_rate = approved_count / decided_count * 100 if decided_count else 0.0
    return strings_others["submitter_stats"].format(
        submission_count=submission_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        approval_rate=approval_rate,
    )


def format_reviewer_stats(reviewer_info):
    approve_count = reviewer_info.approve_count if reviewer_info else 0
    reject_count = reviewer_info.reject_count if reviewer_info else 0
    approve_but_rejected_count = (
        reviewer_info.approve_but_rejected_count if reviewer_info else 0
    )
    reject_but_approved_count = (
        reviewer_info.reject_but_approved_count if reviewer_info else 0
    )
    last_time = reviewer_info.last_time if reviewer_info else strings_others["none"]
    return strings_others["reviewer_stats"].format(
        review_count=approve_count + reject_count,
        approve_count=approve_count,
        reject_count=reject_count,
        approve_but_rejected_count=approve_but_rejected_count,
        reject_but_approved_count=reject_but_approved_count,
        approve_but_rejected_rate=(
            approve_but_rejected_count / approve_count * 100
            if approve_count else 0.0
        ),
        reject_but_approved_rate=(
            reject_but_approved_count / reject_count * 100
            if reject_count else 0.0
        ),
        last_time=last_time,
    )


async def submitter_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        if not str(update.effective_chat.id).startswith("-100"):
            submitter_id = str(update.effective_chat.id)
        elif str(update.effective_chat.id) == TG_REVIEWER_GROUP:
            if update.message.reply_to_message:
                replyto_user_id = str(update.message.reply_to_message.from_user.id)
                self_id = str((await context.bot.get_me()).id)
                if replyto_user_id == self_id:
                    tag_submitter_id = re.findall(r"#SUBMITTER_(\d+)", update.message.reply_to_message.text)
                    if tag_submitter_id:
                        submitter_id = tag_submitter_id[0]
                    else:
                        update.message.reply_text(strings_others["provide_user_id"])
                        return
                else:
                    submitter_id = replyto_user_id
            else:
                submitter_id = str(update.effective_user.id)
        else:
            return
    else:
        if str(update.effective_chat.id) == TG_REVIEWER_GROUP:
            submitter_id = context.args[0]
        else:
            return
    if submitter_id.startswith(("#USER_","#SUBMITTER_")):
        if submitter_id.startswith("#USER_"):
            submitter_id = submitter_id[6:]
        elif submitter_id.startswith("#SUBMITTER_"):
            submitter_id = submitter_id[11:]
    if not submitter_id.isdigit():
        await update.message.reply_text(
            strings_others["invalid_id"].format(user_id=escape_markdown(submitter_id,version=2,)),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    month = current_month_key()
    monthly_info = Submitter.get_monthly_stats(submitter_id, month)
    total_info = Submitter.get_submitter(submitter_id)
    if not monthly_info and not total_info:
        await update.message.reply_text(strings_others["no_submission_stats"])
        return
    escaped_month = escape_markdown(month, version=2)
    reply_string = (
        strings_others["monthly_title"].format(month=escaped_month)
        + escape_markdown(format_submitter_stats(monthly_info), version=2)
        + strings_others["total_title"]
        + escape_markdown(
            f"{format_submitter_stats(total_info)}\n\n"
            f"#USER_{submitter_id} #SUBMITTER_{submitter_id}",
            version=2,
        )
    )
    await update.message.reply_text(
        reply_string, parse_mode=ParseMode.MARKDOWN_V2
    )


async def reviewer_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        if not str(update.effective_chat.id).startswith("-100"):
            reviewer_id = str(update.effective_chat.id)
        elif str(update.effective_chat.id) == TG_REVIEWER_GROUP:
            reviewer_id = str(update.effective_user.id)
        else:
            return
    elif str(update.effective_chat.id) == TG_REVIEWER_GROUP:
        reviewer_id = context.args[0]
    else:
        return
    if reviewer_id.startswith("#REVIEWER_"):
        reviewer_id = reviewer_id[10:]
    if not reviewer_id.isdigit():
        await update.message.reply_text(
            strings_others["invalid_id"].format(user_id=escape_markdown(reviewer_id,version=2,)),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    month = current_month_key()
    monthly_info = Reviewer.get_monthly_stats(reviewer_id, month)
    total_info = Reviewer.get_reviewer(reviewer_id)
    if not monthly_info and not total_info:
        await update.message.reply_text(strings_others["no_reviewer_stats"])
        return
    escaped_month = escape_markdown(month, version=2)
    reply_string = (
        strings_others["monthly_title"].format(month=escaped_month)
        + escape_markdown(format_reviewer_stats(monthly_info), version=2)
        + strings_others["total_title"]
        + escape_markdown(
            f"{format_reviewer_stats(total_info)}\n\n#REVIEWER_{reviewer_id}",
            version=2,
        )
    )
    await update.message.reply_text(
        reply_string, parse_mode=ParseMode.MARKDOWN_V2
    )


async def get_set_submitter_max_submission_per_hour(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        usage = strings_others["limit_usage"]
        await update.message.reply_text(
            usage,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    user_id = context.args[0]
    if len(context.args) > 1:
        max_submission_per_hour = int(context.args[1])
        Submitter.set_submitter_max_submission_per_hour(
            user_id, max_submission_per_hour
        )
        default_max = Submitter.get_default_max_submission_per_hour()
        if default_max == max_submission_per_hour:
            await update.message.reply_text(
                strings_others["limit_default_set"].format(user_id=user_id, max_count=max_submission_per_hour)
            )
        else:
            await update.message.reply_text(
                strings_others["limit_set"].format(user_id=user_id, max_count=max_submission_per_hour)
            )
    else:
        max_submission_per_hour = (
            Submitter.get_submitter_max_submission_per_hour(user_id)
        )
        await update.message.reply_text(
            strings_others["limit_get"].format(user_id=user_id, max_count=max_submission_per_hour)
        )


async def reset_submitter_max_submission_per_hour(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.message.reply_text(strings_others["provide_user_id_spaced"])
        return
    user_id = context.args[0]
    default_max = Submitter.get_default_max_submission_per_hour()
    Submitter.set_submitter_max_submission_per_hour(user_id, default_max)
    await update.message.reply_text(
        strings_others["limit_reset"].format(max_count=default_max)
    )


async def get_set_default_max_submission_per_hour(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        max_submission_per_hour = (
            Submitter.get_default_max_submission_per_hour()
        )
        await update.message.reply_text(
            strings_others["default_limit_get"].format(max_count=max_submission_per_hour),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    new_max_submission_per_hour = context.args[0]
    Submitter.set_default_max_submission_per_hour(new_max_submission_per_hour)
    await update.message.reply_text(
        strings_others["default_limit_set"].format(max_count=new_max_submission_per_hour)
    )
