from textwrap import dedent

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from db_op import Reviewer, Submitter, current_month_key
from env import TG_REVIEWER_GROUP

import re


def format_submitter_stats(submitter_info):
    submission_count = submitter_info.submission_count if submitter_info else 0
    approved_count = submitter_info.approved_count if submitter_info else 0
    rejected_count = submitter_info.rejected_count if submitter_info else 0
    decided_count = approved_count + rejected_count
    approval_rate = approved_count / decided_count * 100 if decided_count else 0.0
    return (
        f"投稿数量: {submission_count}\n"
        f"通过数量: {approved_count}\n"
        f"拒绝数量: {rejected_count}\n"
        f"投稿通过率: {approval_rate:.2f}%"
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
    last_time = reviewer_info.last_time if reviewer_info else "无"
    return dedent(
        f"""
        审核数量: {approve_count + reject_count}
        通过数量: {approve_count}
        拒稿数量: {reject_count}
        通过但稿件被拒数量: {approve_but_rejected_count}
        拒稿但稿件通过数量: {reject_but_approved_count}

        通过但稿件被拒数量 / 通过数量: {approve_but_rejected_count / approve_count * 100 if approve_count else 0.0:.2f}%
        拒稿但稿件通过数量 / 拒稿数量: {reject_but_approved_count / reject_count * 100 if reject_count else 0.0:.2f}%

        最后一次审核时间: {last_time}
        """
    ).strip()


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
                        update.message.reply_text("请提供用户ID")
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
            f"ID `{escape_markdown(submitter_id,version=2,)}` 无效",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    month = current_month_key()
    monthly_info = Submitter.get_monthly_stats(submitter_id, month)
    total_info = Submitter.get_submitter(submitter_id)
    if not monthly_info and not total_info:
        await update.message.reply_text("还没有投稿过任何内容")
        return
    escaped_month = escape_markdown(month, version=2)
    reply_string = (
        f"*\\=\\= {escaped_month} 月度统计 \\=\\=*\n"
        + escape_markdown(format_submitter_stats(monthly_info), version=2)
        + "\n\n*\\=\\= 总统计 \\=\\=*\n"
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
            f"ID `{escape_markdown(reviewer_id,version=2,)}` 无效",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    month = current_month_key()
    monthly_info = Reviewer.get_monthly_stats(reviewer_id, month)
    total_info = Reviewer.get_reviewer(reviewer_id)
    if not monthly_info and not total_info:
        await update.message.reply_text("还没有审核过任何内容")
        return
    escaped_month = escape_markdown(month, version=2)
    reply_string = (
        f"*\\=\\= {escaped_month} 月度统计 \\=\\=*\n"
        + escape_markdown(format_reviewer_stats(monthly_info), version=2)
        + "\n\n*\\=\\= 总统计 \\=\\=*\n"
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
        usage = "使用方法：\n\\- `\\\\limit [用户 ID]` : 获取用户当前限制\n\\- `\\\\limit [用户 ID] [最大每小时投稿数]` : 设置用户每小时投稿数限制"
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
                f"用户 {user_id} 的每小时投稿数限制已设置为默认值: {max_submission_per_hour}，未来将随默认值的变化而变化"
            )
        else:
            await update.message.reply_text(
                f"设置成功，用户 {user_id} 的每小时投稿数限制已设置为: {max_submission_per_hour}"
            )
    else:
        max_submission_per_hour = (
            Submitter.get_submitter_max_submission_per_hour(user_id)
        )
        await update.message.reply_text(
            f"用户 {user_id} 的每小时投稿数限制为: {max_submission_per_hour}"
        )


async def reset_submitter_max_submission_per_hour(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.message.reply_text("请提供用户 ID")
        return
    user_id = context.args[0]
    default_max = Submitter.get_default_max_submission_per_hour()
    Submitter.set_submitter_max_submission_per_hour(user_id, default_max)
    await update.message.reply_text(
        f"重置成功，用户的每小时投稿数限制已设置为默认值: {default_max}"
    )


async def get_set_default_max_submission_per_hour(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        max_submission_per_hour = (
            Submitter.get_default_max_submission_per_hour()
        )
        await update.message.reply_text(
            f"当前默认每小时投稿数限制为: {max_submission_per_hour}\n使用方法： `\\\\limit_default [最大每小时投稿数]` : 设置默认每小时投稿数限制",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    new_max_submission_per_hour = context.args[0]
    Submitter.set_default_max_submission_per_hour(new_max_submission_per_hour)
    await update.message.reply_text(
        f"默认每小时投稿数限制已设置为: {new_max_submission_per_hour}"
    )
