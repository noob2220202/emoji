import logging

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from . import config, handlers


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")

    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.on_document))
    app.add_handler(MessageHandler(filters.ANIMATION, handlers.on_animation))
    app.add_handler(MessageHandler(filters.VIDEO, handlers.on_video))
    app.add_handler(CallbackQueryHandler(handlers.on_choice, pattern=r"^proceed:"))

    if app.job_queue is not None:
        app.job_queue.run_repeating(handlers.cleanup_pending, interval=300, first=300)

    app.run_polling()


if __name__ == "__main__":
    main()
