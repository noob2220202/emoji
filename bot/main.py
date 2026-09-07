import logging

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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")

    db.init_db()

    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(config.BOT_TOKEN).defaults(defaults).build()

    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("status", handlers.status))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.on_document))
    app.add_handler(MessageHandler(filters.ANIMATION, handlers.on_animation))
    app.add_handler(MessageHandler(filters.VIDEO, handlers.on_video))
    app.add_handler(CallbackQueryHandler(handlers.on_bg_choice, pattern=r"^bg:"))
    app.add_handler(CallbackQueryHandler(handlers.on_pack_choice, pattern=r"^pack:"))
    app.add_handler(PreCheckoutQueryHandler(handlers.on_pre_checkout_query))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handlers.on_successful_payment))

    if app.job_queue is not None:
        app.job_queue.run_repeating(handlers.cleanup_pending, interval=300, first=300)

    app.run_polling()


if __name__ == "__main__":
    main()
