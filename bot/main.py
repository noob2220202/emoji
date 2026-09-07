import logging

from telegram import BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    Defaults,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from . import config, db, handlers

COMMANDS = [
    BotCommand("start", "봇 소개 및 사용법"),
    BotCommand("text", "글자를 꾸며서 이모지로 만들기"),
    BotCommand("status", "내 무료 횟수 / 이모지 팩 목록 보기"),
]


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(COMMANDS)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")

    db.init_db()

    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .defaults(defaults)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("status", handlers.status))
    app.add_handler(CommandHandler("text", handlers.text_command))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.on_document))
    app.add_handler(CallbackQueryHandler(handlers.on_bg_choice, pattern=r"^bg:"))
    app.add_handler(CallbackQueryHandler(handlers.on_pack_select, pattern=r"^packsel:"))
    app.add_handler(CallbackQueryHandler(handlers.on_font_choice, pattern=r"^txtfont:"))
    app.add_handler(CallbackQueryHandler(handlers.on_style_choice, pattern=r"^txtstyle:"))
    app.add_handler(PreCheckoutQueryHandler(handlers.on_pre_checkout_query))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handlers.on_successful_payment))
    # 팩 이름 / 조각 수 / 글자 이모지 문구처럼 다음 텍스트 메시지를 기다리는 단계를 처리한다.
    # 반드시 다른 핸들러들 뒤에 등록해서 명령어나 결제 메시지를 가로채지 않게 한다.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text_message))

    if app.job_queue is not None:
        app.job_queue.run_repeating(handlers.cleanup_pending, interval=300, first=300)

    app.run_polling()


if __name__ == "__main__":
    main()
