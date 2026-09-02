import asyncio
import logging
import os
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.config.settings import TG_SUPERADMIN
from src.strings import others as strings_others


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _parse_update_args(args: list[str]) -> tuple[str | None, str | None, bool]:
    branch = None
    remote = None
    force = False
    index = 0

    while index < len(args):
        argument = args[index]
        if argument in ("-f", "--force"):
            force = True
        elif argument in ("-b", "--branch", "-r", "--remote"):
            index += 1
            if index >= len(args) or args[index].startswith("-"):
                raise ValueError
            if argument in ("-b", "--branch"):
                branch = args[index]
            else:
                remote = args[index]
        elif argument.startswith("--branch="):
            branch = argument.removeprefix("--branch=")
            if not branch:
                raise ValueError
        elif argument.startswith("--remote="):
            remote = argument.removeprefix("--remote=")
            if not remote or remote.startswith("-"):
                raise ValueError
        else:
            raise ValueError
        index += 1

    return branch, remote, force


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


async def update_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or user.id not in TG_SUPERADMIN:
        return

    message = update.effective_message

    try:
        branch, remote, force = _parse_update_args(context.args)
    except ValueError:
        await message.reply_text(strings_others["update_usage"])
        return

    try:
        if branch is None:
            returncode, branch = await _run_git("branch", "--show-current")
            if returncode != 0 or not branch:
                logger.error("Failed to determine the current Git branch")
                await message.reply_text(strings_others["update_branch_failed"])
                return

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

        if not force:
            returncode, worktree_status = await _run_git("status", "--porcelain")
            if returncode != 0:
                logger.error("Failed to inspect the Git worktree")
                await message.reply_text(strings_others["update_failed"])
                return
            if worktree_status:
                await message.reply_text(strings_others["update_dirty"])
                return

        await message.reply_text(strings_others["update_started"])

        if force:
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

    logger.info("Git update completed: %s", git_output)
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
