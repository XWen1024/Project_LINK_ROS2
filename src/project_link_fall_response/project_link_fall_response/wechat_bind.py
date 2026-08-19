#!/usr/bin/env python3
"""One-time foreground QR login and emergency-contact binding command."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import secrets

from .wechat import BindingStore


async def bind(args) -> None:
    from wechatbot import WeChatBot

    code = args.code or f"{secrets.randbelow(1_000_000):06d}"
    print(f"绑定码：{code}")
    print(f"扫码登录后，请由唯一紧急联系人发送：/bind {code}")
    done = asyncio.Event()
    store = BindingStore(args.binding_path)
    bot = WeChatBot(cred_path=str(Path(args.credentials_path).expanduser()))
    await bot.login()

    @bot.on_message
    async def on_message(message) -> None:
        if message.text.strip() != f"/bind {code}":
            return
        store.save(message.user_id, message._context_token)
        await bot.reply(message, "Project LINK 紧急联系人绑定成功。")
        done.set()
        bot.stop()

    poll = asyncio.create_task(bot.start())
    await done.wait()
    await poll
    print(f"已绑定联系人：{store.load()['user_id']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default="")
    parser.add_argument(
        "--credentials-path", default="~/.config/project_link/wechatbot/credentials.json"
    )
    parser.add_argument(
        "--binding-path", default="~/.local/state/project-link/clawbot/binding.json"
    )
    asyncio.run(bind(parser.parse_args()))


if __name__ == "__main__":
    main()
