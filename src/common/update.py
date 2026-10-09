import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.config.settings import TG_SUPERADMIN
from src.strings import others as strings_others


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _parse_update_args(
    args: list[str],
) -> tuple[str | None, str | None, str | None, bool]:
    branch = None
    remote = None
    commit = None
    force = False
    index = 0

    while index < len(args):
        argument = args[index]
        if argument in ("-f", "--force"):
            force = True
        elif argument in (
            "-b",
            "--branch",
            "-r",
            "--remote",
            "-c",
            "--commit",
        ):
            index += 1
            if index >= len(args) or args[index].startswith("-"):
                raise ValueError
            if argument in ("-b", "--branch"):
                branch = args[index]
            elif argument in ("-r", "--remote"):
                remote = args[index]
            else:
                commit = args[index]
        elif argument.startswith("--branch="):
            branch = argument.removeprefix("--branch=")
            if not branch:
                raise ValueError
        elif argument.startswith("--remote="):
            remote = argument.removeprefix("--remote=")
            if not remote or remote.startswith("-"):
                raise ValueError
        elif argument.startswith("--commit="):
            commit = argument.removeprefix("--commit=")
            if not commit:
                raise ValueError
        else:
            raise ValueError
        index += 1

    return branch, remote, commit, force


async def _run_git(*args: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=PROJECT_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode, output.decode(errors="replace").strip()


async def _reply_in_chunks(message, text: str, limit: int = 4000) -> None:
    lines = text.splitlines()
    chunk = ""
    for line in lines:
        candidate = f"{chunk}\n{line}" if chunk else line
        if len(candidate) <= limit:
            chunk = candidate
            continue
        if chunk:
            await message.reply_text(chunk)
        chunk = line
    if chunk:
        await message.reply_text(chunk)


async def update_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or user.id not in TG_SUPERADMIN:
        return

    message = update.effective_message

    try:
        branch, remote, commit, force = _parse_update_args(context.args)
    except ValueError:
        await message.reply_text(strings_others["update_usage"])
        return

    try:
        returncode, current_branch = await _run_git("branch", "--show-current")
        if returncode != 0 or not current_branch:
            logger.error("Failed to determine the current Git branch")
            await message.reply_text(strings_others["update_branch_failed"])
            return

        if branch is None:
            branch = current_branch

        if branch.startswith("-"):
            await message.reply_text(strings_others["update_invalid_branch"])
            return

        returncode, _ = await _run_git("check-ref-format", "--branch", branch)
        if returncode != 0:
            await message.reply_text(strings_others["update_invalid_branch"])
            return

        if remote is None:
            returncode, configured_remote = await _run_git(
                "config",
                "--get",
                f"branch.{branch}.remote",
            )
            remote = configured_remote if returncode == 0 and configured_remote else "origin"

        if commit is not None and not re.fullmatch(r"[0-9a-fA-F]{4,40}", commit):
            await message.reply_text(strings_others["update_invalid_commit"])
            return

        if not force:
            returncode, worktree_status = await _run_git("status", "--porcelain")
            if returncode != 0:
                logger.error("Failed to inspect the Git worktree")
                await message.reply_text(strings_others["update_failed"])
                return
            if worktree_status:
                await message.reply_text(strings_others["update_dirty"])
                return

        returncode, old_commit = await _run_git("rev-parse", "HEAD")
        if returncode != 0:
            logger.error("Failed to determine the current Git commit")
            await message.reply_text(strings_others["update_failed"])
            return

        await message.reply_text(
            strings_others["update_started"].format(branch=current_branch)
        )

        if commit is not None:
            returncode, git_output = await _run_git(
                "fetch",
                "--force",
                "--",
                remote,
                branch,
            )
            if returncode == 0:
                returncode, target_commit = await _run_git(
                    "rev-parse",
                    "--verify",
                    f"{commit}^{{commit}}",
                )
                if returncode != 0:
                    await message.reply_text(
                        strings_others["update_invalid_commit"]
                    )
                    return
            if returncode == 0:
                returncode, _ = await _run_git(
                    "merge-base",
                    "--is-ancestor",
                    target_commit,
                    "FETCH_HEAD",
                )
                if returncode != 0:
                    await message.reply_text(
                        strings_others["update_invalid_commit"]
                    )
                    return
            if returncode == 0:
                if force:
                    returncode, git_output = await _run_git(
                        "reset",
                        "--hard",
                        target_commit,
                    )
                else:
                    returncode, git_output = await _run_git(
                        "merge",
                        "--ff-only",
                        target_commit,
                    )
        elif force:
            returncode, git_output = await _run_git(
                "fetch",
                "--force",
                "--",
                remote,
                branch,
            )
            if returncode == 0:
                returncode, git_output = await _run_git(
                    "reset",
                    "--hard",
                    "FETCH_HEAD",
                )
        else:
            returncode, git_output = await _run_git(
                "pull",
                "--ff-only",
                "--",
                remote,
                branch,
            )
    except OSError:
        logger.exception("Failed to execute Git update")
        await message.reply_text(strings_others["update_failed"])
        return

    if returncode != 0:
        logger.error(
            "Git update failed with exit code %s: %s",
            returncode,
            git_output.replace(remote, "<remote>"),
        )
        await message.reply_text(strings_others["update_failed"])
        return

    returncode, new_commit = await _run_git("rev-parse", "HEAD")
    if returncode != 0:
        logger.error("Failed to determine the updated Git commit")
        await message.reply_text(strings_others["update_failed"])
        return

    logger.info("Git update completed: %s", git_output)

    if old_commit == new_commit:
        await message.reply_text(
            strings_others["update_unchanged"].format(
                branch=current_branch,
                commit=new_commit[:7],
            )
        )
        return

    returncode, commit_log = await _run_git(
        "log",
        "--reverse",
        "--format=%h %s",
        f"{old_commit}..{new_commit}",
    )
    if returncode != 0:
        logger.error("Failed to list updated Git commits")
        await message.reply_text(strings_others["update_failed"])
        return

    if not commit_log:
        returncode, commit_log = await _run_git(
            "log",
            "--format=%h %s",
            f"{new_commit}..{old_commit}",
        )
        if returncode != 0:
            logger.error("Failed to list removed Git commits")
            await message.reply_text(strings_others["update_failed"])
            return
        commit_log = strings_others["update_removed_commits"].format(
            commits=commit_log
        )

    update_details = strings_others["update_details"].format(
        branch=current_branch,
        old_commit=old_commit[:7],
        new_commit=new_commit[:7],
        commits=commit_log,
    )
    await _reply_in_chunks(message, update_details)
    await message.reply_text(strings_others["update_restarting"])

    try:
        os.execl(
            sys.executable,
            sys.executable,
            "-m",
            "src.main",
            *sys.argv[1:],
        )
    except OSError:
        logger.exception("Failed to restart after update")
        await message.reply_text(strings_others["update_restart_failed"])
