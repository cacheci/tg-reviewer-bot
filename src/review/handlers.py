import base64
import pickle
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.database.operations import IdempotencyRecord, Reviewer, Submitter, current_month_key
from src.config.settings import (
    APPROVE_NUMBER_REQUIRED,
    REJECT_NUMBER_REQUIRED,
    REJECTION_REASON,
    TG_METADATA_ENCRYPTION_SECRET,
    TG_PUBLISH_CHANNEL,
    TG_SELF_APPROVE,
    TG_TIMEOUT_SINGLEREVIEW,
)
from src.review.utils import (
    ReviewChoice,
    SubmissionStatus,
    generate_submission_meta_string,
    generate_submission_meta_url,
    get_decision,
    remove_decision,
    save_submission_metadata,
    send_to_rejected_channel,
)
from src.common.utils import send_result_to_submitter, send_submission
from src.strings import channel as strings_channel
from src.strings import reviewer as strings_reviewer
from src.strings import submitter as strings_submitter


def review_operation_key(review_message, reviewer_id):
    return (
        f"review:{review_message.chat_id}:{review_message.message_id}:"
        f"{reviewer_id}"
    )


def finalize_operation_key(review_message):
    return f"review-finalize:{review_message.chat_id}:{review_message.message_id}"


@IdempotencyRecord.cleanup_on_error
async def approve_submission(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    action = query.data.split(".")[0]
    review_message = update.effective_message

    reviewer_id, reviewer_username, reviewer_fullname = (
        query.from_user.id,
        query.from_user.username,
        query.from_user.full_name,
    )
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )
    stats_month = submission_meta.setdefault("stats_month", {})
    reviewer_months = stats_month.setdefault("reviewers", {})

    submission_longago = (datetime.now(timezone.utc) - update.effective_message.date > timedelta(minutes=TG_TIMEOUT_SINGLEREVIEW))
    # if the reviwer is the submitter
    if not TG_SELF_APPROVE and reviewer_id == submission_meta["submitter"][0]:
        await query.answer(strings_reviewer["cannot_self_approve"])
        return
    if IdempotencyRecord.get(finalize_operation_key(review_message)):
        await query.answer(strings_reviewer["submission_finalizing"], show_alert=True)
        return

    operation_key = review_operation_key(review_message, reviewer_id)
    if reviewer_id in submission_meta["reviewer"]:
        if not IdempotencyRecord.get(operation_key):
            if IdempotencyRecord.claim(
                operation_key,
                "review",
                str(submission_meta["reviewer"][reviewer_id][2]),
            ):
                IdempotencyRecord.complete(operation_key)
        await query_decision(update, context)
        return
    approve_count = sum(
        reviewer[2] in (ReviewChoice.SFW, ReviewChoice.NSFW)
        for reviewer in submission_meta["reviewer"].values()
    )
    if not IdempotencyRecord.claim_review(operation_key, str(action)):
        await query.answer(strings_reviewer["review_processed"], show_alert=True)
        return

    # if the reviewer has not rejected the submission
    submission_meta["reviewer"][reviewer_id] = [
        reviewer_username,
        reviewer_fullname,
        action,
    ]

    # increse reviewer approve count
    reviewer_month = current_month_key()
    reviewer_months[reviewer_id] = reviewer_month
    save_submission_metadata(review_message, submission_meta, "pending")
    Reviewer.count_increase(
        reviewer_id, "approve_count", month=reviewer_month
    )

    # get options from all reviewers
    review_options = [
        reviewer[2] for reviewer in submission_meta["reviewer"].values()
    ]
    # if the submission has not been approved by enough reviewers
    if (
        (review_options.count(ReviewChoice.NSFW) + review_options.count(ReviewChoice.SFW) < APPROVE_NUMBER_REQUIRED)
        and not submission_longago
    ):
        await review_message.edit_text(
            text=generate_submission_meta_string(submission_meta),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=review_message.reply_markup,
        )
        await query.answer(
            strings_reviewer["vote_success"].format(decision=get_decision(submission_meta, reviewer_id))
        )
        IdempotencyRecord.complete(operation_key)
        return
    # else if the submission has been approved by enough reviewers
    finalization_key = finalize_operation_key(review_message)
    if not IdempotencyRecord.claim(
        finalization_key, "review_finalize", str(action)
    ):
        IdempotencyRecord.complete(operation_key)
        await query.answer(strings_reviewer["submission_finalizing"], show_alert=True)
        return
    await query.answer(strings_reviewer["approved_success"])
    # increse submitter approved count
    result_month = current_month_key()
    stats_month["result"] = result_month
    save_submission_metadata(review_message, submission_meta, "approved")
    Submitter.count_increase(
        submission_meta["submitter"][0],
        "approved_count",
        month=result_month,
    )
    # increse reviewer count
    for reviewer_id in submission_meta["reviewer"]:
        if submission_meta["reviewer"][reviewer_id][2] not in [
            ReviewChoice.SFW,
            ReviewChoice.NSFW,
        ]:
            Reviewer.count_increase(
                reviewer_id,
                "reject_but_approved_count",
                month=reviewer_months.get(reviewer_id, current_month_key()),
            )
    # then send this submission to the publish channel
    main_channel_messages = None
    submission_meta["sent_msg"] = {}
    save_submission_metadata(review_message, submission_meta, "approved")
    submission_meta["pending_send_channels"] = TG_PUBLISH_CHANNEL

    pending_send_channels = submission_meta["pending_send_channels"].copy()
    for index, publish_channel in enumerate(pending_send_channels):
        # if the submission is nsfw
        skip_all = None
        has_spoiler = False
        if ReviewChoice.NSFW in review_options:
            has_spoiler = True
            inline_keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(strings_channel["next"], url=f"https://t.me/")]]
            )
            skip_all = await context.bot.send_message(
                chat_id=publish_channel,
                text=strings_channel["nsfw_warning"],
                reply_markup=inline_keyboard,
            )

        # get all append messages from submission_meta['append']
        append_messages = []
        for append_list in submission_meta["append"].values():
            append_messages.extend(append_list)
        append_messages_string = "\n".join(append_messages)

        # Generate tracking link with encrypted metadata
        if "video" in submission_meta["media_type_list"]:
            tracking_meta = {
                **submission_meta,
                "review_message_id": review_message.message_id,
            }
        else:
            tracking_meta = {
                "review_message_id": review_message.message_id,
            }
        tracking_link = generate_submission_meta_url(
            tracking_meta, encrypt_salt=TG_METADATA_ENCRYPTION_SECRET
        )

        # Generate text
        publish_text = submission_meta["text"]
        if append_messages_string:
            publish_text += "\n" + append_messages_string
        if publish_text.endswith("||"):
            publish_text += "\n" + tracking_link
        else:
            publish_text += tracking_link

        # Send to target
        sent_messages = await send_submission(
            context=context,
            chat_id=publish_channel,
            media_id_list=submission_meta["media_id_list"],
            media_type_list=submission_meta["media_type_list"],
            documents_id_list=submission_meta["documents_id_list"],
            document_type_list=submission_meta["document_type_list"],
            text=publish_text,
            has_spoiler=has_spoiler,
        )
        if main_channel_messages is None:
            main_channel_messages = [message.message_id for message in sent_messages]

        trigger_coding = (sent_messages[-1].id == 0)
        if (trigger_coding == False):
            submission_meta["pending_send_channels"] = pending_send_channels[(index + 1):]

        # Persist published message IDs before further Telegram requests.
        if (trigger_coding == False):
            sent_message_ids = [message.message_id for message in sent_messages]
            if skip_all is not None:
                sent_message_ids.append(skip_all.message_id)
            submission_meta["sent_msg"][publish_channel] = sent_message_ids

        save_submission_metadata(review_message, submission_meta, "approved")

        # edit the skip_all message
        if skip_all:
            if (trigger_coding == False):
                url_parts = sent_messages[-1].link.rsplit("/", 1)
                next_url = url_parts[0] + "/" + str(int(url_parts[-1]) + 1)
                inline_keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(strings_channel["next"], url=next_url)]]
                )
                await skip_all.edit_text(
                    text=strings_channel["nsfw_warning"], reply_markup=inline_keyboard
                )
            else:
                await skip_all.delete()

        # Stop if trigger video coding
        if trigger_coding:
            break

    primary_channel = str(TG_PUBLISH_CHANNEL[0])[4:] if str(TG_PUBLISH_CHANNEL[0]).startswith("-100") else TG_PUBLISH_CHANNEL[0]
    # add inline keyboard to jump to this submission and its comments in the publish channel
    if not submission_meta["pending_send_channels"]:
        inline_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        strings_channel["view"], url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}"
                    ),
                    InlineKeyboardButton(
                        strings_channel["view_comments"],
                        url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}?comment=1",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        strings_reviewer["reply_submitter"],
                        switch_inline_query_current_chat="/comment ",
                    ),
                    InlineKeyboardButton(
                        strings_reviewer["withdraw_submission"],
                        callback_data=f"{ReviewChoice.APPROVED_RETRACT}",
                    ),
                ],
            ]
        )
    elif(main_channel_messages[0] != 0):
        inline_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        strings_channel["view"], url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}"
                    ),
                    InlineKeyboardButton(
                        strings_channel["view_comments"],
                        url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}?comment=1",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        strings_reviewer["reply_submitter"],
                        switch_inline_query_current_chat="/comment ",
                    ),
                    InlineKeyboardButton(
                        strings_reviewer["force_continue"], callback_data=f"f_conti" # TODO
                    )
                ]
            ]
        )
    else:
        inline_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        strings_reviewer["force_continue"], callback_data=f"f_conti" # TODO
                    )
                ]
            ]
        )

    longago_status = 0 if not submission_longago else SubmissionStatus.APPROVED

    await review_message.edit_text(
        text=generate_submission_meta_string(submission_meta,longago_status=longago_status),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=inline_keyboard,
    )
    # send result to submitter
    if (main_channel_messages[0] != 0):
        await send_result_to_submitter(
            context,
            submission_meta["submitter"][0],
            submission_meta["submitter"][3],
            strings_submitter["approved"],
            inline_keyboard_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            strings_channel["view"], url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}"
                        ),
                        InlineKeyboardButton(
                            strings_channel["view_comments"],
                            url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}?comment=1",
                        ),
                    ]
                ]
            ),
        )
    IdempotencyRecord.complete(operation_key)
    IdempotencyRecord.complete(finalization_key)


@IdempotencyRecord.cleanup_on_error
async def reject_submission(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    action = query.data.split(".")[0]
    review_message = update.effective_message
    reviewer_id, reviewer_username, reviewer_fullname = (
        query.from_user.id,
        query.from_user.username,
        query.from_user.full_name,
    )
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )
    stats_month = submission_meta.setdefault("stats_month", {})
    reviewer_months = stats_month.setdefault("reviewers", {})
    if IdempotencyRecord.get(finalize_operation_key(review_message)):
        await query.answer(strings_reviewer["submission_finalizing"], show_alert=True)
        return

    operation_key = review_operation_key(review_message, reviewer_id)
    if reviewer_id in submission_meta["reviewer"]:
        if not IdempotencyRecord.get(operation_key):
            if IdempotencyRecord.claim(
                operation_key,
                "review",
                str(submission_meta["reviewer"][reviewer_id][2]),
            ):
                IdempotencyRecord.complete(operation_key)
        await query_decision(update, context)
        return
    if not IdempotencyRecord.claim_review(operation_key, str(action)):
        await query.answer(strings_reviewer["review_processed"], show_alert=True)
        return

    submission_longago = (datetime.now(timezone.utc) - update.effective_message.date > timedelta(minutes=TG_TIMEOUT_SINGLEREVIEW))
    # if REJECT_DUPLICATE, only one reviewer is enough
    if action == ReviewChoice.REJECT_DUPLICATE:
        submission_meta["reviewer"][reviewer_id] = [
            reviewer_username,
            reviewer_fullname,
            action,
        ]
        reviewer_month = current_month_key()
        reviewer_months[reviewer_id] = reviewer_month
        finalization_key = finalize_operation_key(review_message)
        if not IdempotencyRecord.claim(
            finalization_key, "review_finalize", str(action)
        ):
            IdempotencyRecord.complete(operation_key)
            await query.answer(
                strings_reviewer["submission_finalizing"], show_alert=True
            )
            return
        await query.answer(strings_reviewer["rejected_success"])
        inline_keyboard_content = []
        inline_keyboard_content.append(
            [
                InlineKeyboardButton(
                    strings_reviewer["reply_submitter"],
                    switch_inline_query_current_chat="/comment ",
                )
            ]
        )
        # send the submittion to rejected channel
        await send_to_rejected_channel(
            update=update, context=context, submission_meta=submission_meta
        )

        # increse submitter rejected count
        result_month = current_month_key()
        stats_month["result"] = result_month
        save_submission_metadata(review_message, submission_meta, "rejected")
        Submitter.count_increase(
            submission_meta["submitter"][0],
            "rejected_count",
            month=result_month,
        )
        # increse reviewer count
        Reviewer.count_increase(
            reviewer_id, "reject_count", month=reviewer_month
        )
        for reviewer_id in submission_meta["reviewer"]:
            if submission_meta["reviewer"][reviewer_id][2] in [
                ReviewChoice.SFW,
                ReviewChoice.NSFW,
            ]:
                Reviewer.count_increase(
                    reviewer_id,
                    "approve_but_rejected_count",
                    month=reviewer_months.get(
                        reviewer_id, current_month_key()
                    ),
                )
        IdempotencyRecord.complete(operation_key)
        IdempotencyRecord.complete(finalization_key)
        return
    # else if the reviewer has not approved or rejected the submission
    submission_meta["reviewer"][reviewer_id] = [
        reviewer_username,
        reviewer_fullname,
        action,
    ]
    reviewer_month = current_month_key()
    reviewer_months[reviewer_id] = reviewer_month
    save_submission_metadata(review_message, submission_meta, "pending")
    # increse reviewer reject count
    Reviewer.count_increase(
        reviewer_id, "reject_count", month=reviewer_month
    )
    # get options from all reviewers
    review_options = [
        reviewer[2] for reviewer in submission_meta["reviewer"].values()
    ]
    # if the submission has not been rejected by enough reviewers
    if (
        (review_options.count(ReviewChoice.REJECT) < REJECT_NUMBER_REQUIRED)
        and not submission_longago
    ):
        await review_message.edit_text(
            text=generate_submission_meta_string(submission_meta),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=review_message.reply_markup,
        )
        await query.answer(
            strings_reviewer["vote_success"].format(decision=get_decision(submission_meta, reviewer_id))
        )
        IdempotencyRecord.complete(operation_key)
        return
    # else if the submission has been rejected by enough reviewers
    finalization_key = finalize_operation_key(review_message)
    if not IdempotencyRecord.claim(
        finalization_key, "review_finalize", str(action)
    ):
        IdempotencyRecord.complete(operation_key)
        await query.answer(strings_reviewer["submission_finalizing"], show_alert=True)
        return
    await query.answer(strings_reviewer["rejected_success"])
    # increse submitter rejected count
    result_month = current_month_key()
    stats_month["result"] = result_month
    save_submission_metadata(review_message, submission_meta, "rejected")
    Submitter.count_increase(
        submission_meta["submitter"][0],
        "rejected_count",
        month=result_month,
    )
    # increse reviewer count
    for reviewer_id in submission_meta["reviewer"]:
        if submission_meta["reviewer"][reviewer_id][2] in [
            ReviewChoice.SFW,
            ReviewChoice.NSFW,
        ]:
            Reviewer.count_increase(
                reviewer_id,
                "approve_but_rejected_count",
                month=reviewer_months.get(reviewer_id, current_month_key()),
            )
    # send the rejection reason options inline keyboard
    # show inline keyboard in 2 cols
    inline_keyboard_content = []
    for i in range(0, len(REJECTION_REASON), 2):
        inline_keyboard_content.append(
            [
                InlineKeyboardButton(
                    REJECTION_REASON[i], callback_data=f"REASON.{i}"
                )
            ]
        )
        if i + 1 < len(REJECTION_REASON):
            inline_keyboard_content[-1].append(
                InlineKeyboardButton(
                    REJECTION_REASON[i + 1], callback_data=f"REASON.{i+1}"
                )
            )
    inline_keyboard_content.append(
        [
            InlineKeyboardButton(
                strings_reviewer["custom_reason"],
                switch_inline_query_current_chat="/reject ",
            ),
            InlineKeyboardButton(strings_reviewer["ignore_submission"], callback_data="REASON.IGNORE"),
        ]
    )
    inline_keyboard_content.append(
        [
            InlineKeyboardButton(
                strings_reviewer["reply_submitter"],
                switch_inline_query_current_chat="/comment ",
            )
        ]
    )
    longago_status = 0 if not submission_longago else SubmissionStatus.REJECTED
    await review_message.edit_text(
        text=generate_submission_meta_string(submission_meta, longago_status=longago_status),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup(inline_keyboard_content),
    )
    IdempotencyRecord.complete(operation_key)
    IdempotencyRecord.complete(finalization_key)

@IdempotencyRecord.cleanup_on_error
async def force_continue_callback(update, context):
    review_message = update.effective_message
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )
    reviewer_id = query.from_user.id

    query = update.callback_query
    query.answer(strings_reviewer["excuting"])

    operation_key = review_operation_key(review_message, reviewer_id)
    finalization_key = finalize_operation_key(review_message)


    if not IdempotencyRecord.get(operation_key):
        if IdempotencyRecord.claim(
            operation_key,
            "review",
            str(submission_meta["reviewer"][reviewer_id][2]),
        ):
            IdempotencyRecord.complete(operation_key)
    else:
        await query_decision(update, context)
        return

    # get options from all reviewers
    review_options = [
        reviewer[2] for reviewer in submission_meta["reviewer"].values()
    ]
    main_channel_messages = submission_meta["sent_msg"].get(TG_PUBLISH_CHANNEL[0])
    should_send_to_submitter = False

    pending_send_channels = submission_meta["pending_send_channels"].copy()
    for index, publish_channel in enumerate(pending_send_channels):
        # if the submission is nsfw
        skip_all = None
        has_spoiler = False
        if ReviewChoice.NSFW in review_options:
            has_spoiler = True
            inline_keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(strings_channel["next"], url=f"https://t.me/")]]
            )
            skip_all = await context.bot.send_message(
                chat_id=publish_channel,
                text=strings_channel["nsfw_warning"],
                reply_markup=inline_keyboard,
            )

        # get all append messages from submission_meta['append']
        append_messages = []
        for append_list in submission_meta["append"].values():
            append_messages.extend(append_list)
        append_messages_string = "\n".join(append_messages)

        # Generate tracking link with encrypted metadata
        if "video" in submission_meta["media_type_list"]:
            tracking_meta = {
                **submission_meta,
                "review_message_id": review_message.message_id,
            }
        else:
            tracking_meta = {
                "review_message_id": review_message.message_id,
            }
        tracking_link = generate_submission_meta_url(
            tracking_meta, encrypt_salt=TG_METADATA_ENCRYPTION_SECRET
        )

        # Generate text
        publish_text = submission_meta["text"]
        if append_messages_string:
            publish_text += "\n" + append_messages_string
        if publish_text.endswith("||"):
            publish_text += "\n" + tracking_link
        else:
            publish_text += tracking_link

        # Send to target
        sent_messages = await send_submission(
            context=context,
            chat_id=publish_channel,
            media_id_list=submission_meta["media_id_list"],
            media_type_list=submission_meta["media_type_list"],
            documents_id_list=submission_meta["documents_id_list"],
            document_type_list=submission_meta["document_type_list"],
            text=publish_text,
            has_spoiler=has_spoiler,
        )

        if submission_meta["sent_msg"] == {}:
            main_channel_messages = [message.message_id for message in sent_messages]
            should_send_to_submitter = True

        trigger_coding = (sent_messages[-1].id == 0)
        if (trigger_coding == False):
            submission_meta["pending_send_channels"] = pending_send_channels[(index + 1):]

        # Persist published message IDs before further Telegram requests.
        if (trigger_coding == False):
            sent_message_ids = [message.message_id for message in sent_messages]
            if skip_all is not None:
                sent_message_ids.append(skip_all.message_id)
            submission_meta["sent_msg"][publish_channel] = sent_message_ids

        save_submission_metadata(review_message, submission_meta, "approved")

        # edit the skip_all message
        if skip_all:
            if (trigger_coding == False):
                url_parts = sent_messages[-1].link.rsplit("/", 1)
                next_url = url_parts[0] + "/" + str(int(url_parts[-1]) + 1)
                inline_keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(strings_channel["next"], url=next_url)]]
                )
                await skip_all.edit_text(
                    text=strings_channel["nsfw_warning"], reply_markup=inline_keyboard
                )
            else:
                await skip_all.delete()

        # Stop if trigger video coding
        if trigger_coding:
            break

    primary_channel = str(TG_PUBLISH_CHANNEL[0])[4:] if str(TG_PUBLISH_CHANNEL[0]).startswith("-100") else TG_PUBLISH_CHANNEL[0]

    # add inline keyboard to jump to this submission and its comments in the publish channel
    if not submission_meta["pending_send_channels"]:
        inline_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        strings_channel["view"], url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}"
                    ),
                    InlineKeyboardButton(
                        strings_channel["view_comments"],
                        url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}?comment=1",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        strings_reviewer["reply_submitter"],
                        switch_inline_query_current_chat="/comment ",
                    ),
                    InlineKeyboardButton(
                        strings_reviewer["withdraw_submission"],
                        callback_data=f"{ReviewChoice.APPROVED_RETRACT}",
                    ),
                ],
            ]
        )
    elif(main_channel_messages[0] != 0):
        inline_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        strings_channel["view"], url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}"
                    ),
                    InlineKeyboardButton(
                        strings_channel["view_comments"],
                        url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}?comment=1",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        strings_reviewer["reply_submitter"],
                        switch_inline_query_current_chat="/comment ",
                    ),
                    InlineKeyboardButton(
                        strings_reviewer["force_continue"], callback_data=f"f_conti" # TODO
                    )
                ]
            ]
        )
    else:
        inline_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        strings_reviewer["force_continue"], callback_data=f"f_conti" # TODO
                    )
                ]
            ]
        )

    submission_longago = (datetime.now(timezone.utc) - update.effective_message.date > timedelta(minutes=TG_TIMEOUT_SINGLEREVIEW))
    longago_status = 0 if not submission_longago else SubmissionStatus.APPROVED

    await review_message.edit_text(
        text=generate_submission_meta_string(submission_meta,longago_status=longago_status),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=inline_keyboard,
    )

    # send result to submitter
    if ((main_channel_messages[0] != 0) and should_send_to_submitter):
        await send_result_to_submitter(
            context,
            submission_meta["submitter"][0],
            submission_meta["submitter"][3],
            strings_submitter["approved"],
            inline_keyboard_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            strings_channel["view"], url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}"
                        ),
                        InlineKeyboardButton(
                            strings_channel["view_comments"],
                            url=f"https://t.me/c/{primary_channel}/{main_channel_messages[0]}?comment=1",
                        ),
                    ]
                ]
            ),
        )
    IdempotencyRecord.complete(operation_key)
    IdempotencyRecord.complete(finalization_key)

async def query_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    review_message = update.effective_message
    reviewer_id = query.from_user.id
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )

    await query.answer(get_decision(submission_meta, reviewer_id))


@IdempotencyRecord.cleanup_on_error
async def withdraw_decision(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    review_message = update.effective_message
    reviewer_id = query.from_user.id
    submission_meta = pickle.loads(
        base64.urlsafe_b64decode(
            review_message.text_markdown_v2_urled.split("/")[-1][:-1]
        )
    )
    if IdempotencyRecord.get(finalize_operation_key(review_message)):
        await query.answer(strings_reviewer["submission_finalizing"], show_alert=True)
        return

    operation_key = review_operation_key(review_message, reviewer_id)
    if reviewer_id in submission_meta["reviewer"] and not IdempotencyRecord.get(
        operation_key
    ):
        if IdempotencyRecord.claim(
            operation_key,
            "review",
            str(submission_meta["reviewer"][reviewer_id][2]),
        ):
            IdempotencyRecord.complete(operation_key)
    if not IdempotencyRecord.claim_withdraw(operation_key):
        await query.answer(strings_reviewer["withdraw_unavailable"], show_alert=True)
        return

    submission_meta, removed = remove_decision(submission_meta, reviewer_id)
    if removed:
        save_submission_metadata(review_message, submission_meta, "pending")
        await review_message.edit_text(
            text=generate_submission_meta_string(submission_meta),
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=review_message.reply_markup,
        )
        IdempotencyRecord.complete(operation_key)
        await query.answer(strings_reviewer["withdrawn"])
    else:
        IdempotencyRecord.complete(operation_key)
        await query.answer(strings_reviewer["no_vote"])
