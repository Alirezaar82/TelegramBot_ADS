# imports
import html
import logging
import sys
from pathlib import Path

from telebot import TeleBot, custom_filters, types
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage

import config
import db
from db_setup import setup_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bot")

state_storage = StateMemoryStorage()
pending_replies = dict()  # admin_id -> user_id waiting for support reply
bot = TeleBot(token=config.token, state_storage=state_storage)


class Support(StatesGroup):
    text = State()
    response = State()


class AdsState(StatesGroup):
    waiting = State()


class ChargeState(StatesGroup):
    amount = State()


def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton(text="💠 ثبت آگهی"))
    markup.add("👤 حساب کاربری", "💵 شارژ حساب", "🎰 زیرمجموعه گیری", "☎️ پشتیبانی")
    return markup


def is_admin(user_id):
    return user_id == config.admin_id


def parse_callback_user_id(data, prefix):
    if not data or not data.startswith(prefix):
        return None
    try:
        return int(data[len(prefix):])
    except (TypeError, ValueError):
        return None


def parse_callback_int(data, prefix):
    return parse_callback_user_id(data, prefix)


def check_join(user):
    if not config.channels:
        return True
    for channel in config.channels:
        try:
            is_member = bot.get_chat_member(chat_id=channel, user_id=user)
        except Exception:
            logger.exception("failed membership check for channel=%s user=%s", channel, user)
            return False
        if is_member.status in ("kicked", "left"):
            return False
    return True


def join_channel(message):
    text = "برای استفاده از ربات ما باید عضو چنل های ما باشید:)"
    markup = types.InlineKeyboardMarkup()
    for link in config.links:
        markup.add(types.InlineKeyboardButton(text="عضویت", url=link))
    bot.send_message(chat_id=message.chat.id, text=text, reply_markup=markup)


def require_membership(message):
    if check_join(message.from_user.id):
        return True
    join_channel(message)
    return False


@bot.message_handler(commands=["start"])
def start(message):
    markup = main_keyboard()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or str(user_id)

    try:
        is_new = db.ensure_user(user_id)
    except Exception:
        logger.exception("failed to ensure user %s", user_id)
        bot.send_message(chat_id=message.chat.id, text="خطا در ثبت اطلاعات. لطفا دوباره تلاش کنید.")
        return

    if is_new:
        bot.send_message(
            chat_id=message.chat.id,
            text=f"خوش آمدید کاربر {username}\nاطلاعات شما ثبت شد",
            reply_markup=markup,
        )
        parts = (message.text or "").split()
        if len(parts) > 1 and parts[1].isdigit():
            referrer_id = int(parts[1])
            if referrer_id != user_id and db.user_exists(referrer_id):
                new_balance = db.add_balance(referrer_id, config.referral_bonus)
                if new_balance is not None:
                    try:
                        bot.send_message(
                            chat_id=referrer_id,
                            text=(
                                f"کاربر گرامی\nفردی با نام کاربری {username} با لینک شما دعوت شد\n"
                                f"{config.referral_bonus} به موجودی شما اضافه شد."
                            ),
                        )
                    except Exception:
                        logger.exception("failed to notify referrer %s", referrer_id)
    else:
        bot.send_message(
            chat_id=message.chat.id,
            text=f"خوش آمدید کاربر {username}",
            reply_markup=markup,
        )

    if not check_join(user_id):
        join_channel(message)


@bot.message_handler(commands=["help"])
def help_command(message):
    text = (
        "سلام این ربات برای ثبت آگهی است. شما با افزایش موجودی میتوانید آگهی خود را "
        "در کانال ما ثبت و ارسال کنید.\n"
        "برای دیدن موجودی حساب خود دکمه حساب کاربری را بزنید."
    )
    bot.reply_to(message, text=text)
    if not check_join(message.from_user.id):
        join_channel(message)


@bot.message_handler(func=lambda message: message.text == "💠 ثبت آگهی")
def ads(message):
    if not require_membership(message):
        return

    db.ensure_user(message.from_user.id)
    balance = db.get_balance(message.from_user.id)
    if balance is None:
        bot.send_message(chat_id=message.chat.id, text="حساب کاربری پیدا نشد. /start را بزنید.")
        return

    if balance >= config.ad_price:
        bot.send_message(
            chat_id=message.chat.id,
            text="موجودی حساب شما کافی بود\n\nخب حالا آگهی خودتو بفرست!",
        )
        bot.set_state(
            user_id=message.from_user.id,
            state=AdsState.waiting,
            chat_id=message.chat.id,
        )
    else:
        bot.send_message(
            chat_id=message.chat.id,
            text=(
                f"موجودی حساب شما برابر {balance}\n"
                f"برای ثبت آگهی باید حداقل {config.ad_price} موجودی داشته باشید\n"
                "لطفا با دکمه شارژ حساب موجودی خود را افزایش دهید"
            ),
        )


@bot.message_handler(state=AdsState.waiting, content_types=["photo", "text", "video"])
def get_ads(message):
    user_id = message.from_user.id
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(text="رد کردن", callback_data=f"deny:{user_id}"),
        types.InlineKeyboardButton(text="تایید کردن", callback_data=f"confirm:{user_id}"),
    )

    try:
        forwarded_message = bot.forward_message(
            chat_id=config.admin_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        bot.send_message(
            chat_id=config.admin_id,
            text=(
                f"کاربر {message.from_user.username} درخواست ثبت آگهی دارد. "
                f"در صورت تایید دکمه تایید را بزنید\n\nشناسه کاربر : {user_id}"
            ),
            reply_markup=markup,
            reply_to_message_id=forwarded_message.message_id,
        )
    except Exception:
        logger.exception("failed to forward ad from user %s", user_id)
        bot.send_message(chat_id=message.chat.id, text="ارسال آگهی به ادمین ناموفق بود. دوباره تلاش کنید.")
        return

    bot.send_message(
        chat_id=message.chat.id,
        text="درخواست شما ثبت شد\nلطفا صبر کنید به زودی نتیجه آن برای شما ارسال میشود",
    )
    bot.delete_state(chat_id=message.chat.id, user_id=message.from_user.id)


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("deny:"))
def deny(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, text="دسترسی ندارید", show_alert=True)
        return

    user = parse_callback_user_id(call.data, "deny:")
    if user is None:
        bot.answer_callback_query(call.id, text="داده نامعتبر است", show_alert=True)
        return

    try:
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=(call.message.text or "") + "\n\n❌ رد شد",
        )
    except Exception:
        logger.exception("failed to update deny message")

    bot.answer_callback_query(call.id, text="آگهی رد شد")
    bot.send_message(chat_id=user, text="درخواست شما رد شد")


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("confirm:"))
def confirm(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, text="دسترسی ندارید", show_alert=True)
        return

    user = parse_callback_user_id(call.data, "confirm:")
    if user is None:
        bot.answer_callback_query(call.id, text="داده نامعتبر است", show_alert=True)
        return

    if not call.message.reply_to_message:
        bot.answer_callback_query(call.id, text="پیام آگهی پیدا نشد", show_alert=True)
        return

    new_balance = db.deduct_balance(user, config.ad_price)
    if new_balance is None:
        bot.answer_callback_query(call.id, text="موجودی کاربر کافی نیست", show_alert=True)
        bot.send_message(chat_id=user, text="موجودی شما برای ثبت آگهی کافی نیست.")
        try:
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None,
            )
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=(call.message.text or "") + "\n\n⚠️ رد شد (موجودی ناکافی)",
            )
        except Exception:
            logger.exception("failed to update confirm/insufficient message")
        return

    try:
        for channel in config.channels:
            bot.copy_message(
                chat_id=channel,
                from_chat_id=call.message.chat.id,
                message_id=call.message.reply_to_message.message_id,
            )
    except Exception:
        logger.exception("failed publishing ad for user %s — refunding", user)
        db.add_balance(user, config.ad_price)
        bot.answer_callback_query(call.id, text="ارسال به کانال ناموفق بود", show_alert=True)
        bot.send_message(chat_id=user, text="ارسال آگهی ناموفق بود و مبلغ به حساب شما برگشت.")
        return

    try:
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=(call.message.text or "") + "\n\n✅ تایید شد",
        )
    except Exception:
        logger.exception("failed to update confirm message")

    bot.answer_callback_query(call.id, text="آگهی تایید شد")
    bot.send_message(
        chat_id=user,
        text=(
            f"مبلغ {config.ad_price} از حساب شما کم شد\n\n"
            f"موجودی حال حاضر شما برابر است با {new_balance} تومان"
        ),
    )
    bot.send_message(chat_id=user, text="آگهی شما ثبت شد")


@bot.message_handler(func=lambda message: message.text == "👤 حساب کاربری")
def account(message):
    if not require_membership(message):
        return

    db.ensure_user(message.from_user.id)
    balance = db.get_balance(message.from_user.id)
    if balance is None:
        bot.send_message(chat_id=message.chat.id, text="حساب کاربری پیدا نشد. /start را بزنید.")
        return

    safe_name = html.escape(message.from_user.first_name or "-")
    text = (
        "📊 اطلاعات حساب کاربری شما:\n\n"
        f"👤 نام کاربری : <a href='tg://user?id={message.from_user.id}'>{safe_name}</a>\n"
        f"🪪 شناسه کاربری : <code>{message.from_user.id}</code>\n"
        f"💰 موجودی : {balance} تومان"
    )
    bot.send_message(chat_id=message.chat.id, text=text, parse_mode="HTML")


@bot.message_handler(func=lambda message: message.text == "💵 شارژ حساب")
def charge_start(message):
    if not require_membership(message):
        return

    db.ensure_user(message.from_user.id)
    bot.send_message(
        chat_id=message.chat.id,
        text="مبلغ شارژ را به تومان وارد کنید (فقط عدد):\nبرای انصراف /cancel را بفرستید.",
    )
    bot.set_state(
        user_id=message.from_user.id,
        state=ChargeState.amount,
        chat_id=message.chat.id,
    )


@bot.message_handler(commands=["cancel"], state="*")
def cancel_state(message):
    bot.delete_state(user_id=message.from_user.id, chat_id=message.chat.id)
    pending_replies.pop(message.from_user.id, None)
    bot.send_message(chat_id=message.chat.id, text="لغو شد.", reply_markup=main_keyboard())


@bot.message_handler(state=ChargeState.amount, content_types=["text"])
def charge_amount(message):
    amount_text = (message.text or "").strip().replace(",", "")
    if not amount_text.isdigit():
        bot.send_message(chat_id=message.chat.id, text="لطفا فقط یک عدد معتبر وارد کنید.")
        return

    amount = int(amount_text)
    if amount < 1000:
        bot.send_message(chat_id=message.chat.id, text="حداقل مبلغ شارژ 1000 تومان است.")
        return
    if amount > 50_000_000:
        bot.send_message(chat_id=message.chat.id, text="مبلغ وارد شده بیش از حد مجاز است.")
        return

    user_id = message.from_user.id
    request_id = db.create_charge_request(user_id, amount)

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(text="تایید شارژ", callback_data=f"charge_ok:{request_id}"),
        types.InlineKeyboardButton(text="رد شارژ", callback_data=f"charge_no:{request_id}"),
    )

    username = message.from_user.username or "-"
    bot.send_message(
        chat_id=config.admin_id,
        text=(
            f"درخواست شارژ\n"
            f"کاربر: @{html.escape(username)} (<code>{user_id}</code>)\n"
            f"مبلغ: <b>{amount}</b> تومان\n"
            f"شناسه درخواست: <code>{request_id}</code>"
        ),
        reply_markup=markup,
        parse_mode="HTML",
    )
    bot.send_message(
        chat_id=message.chat.id,
        text="درخواست شارژ ثبت شد. پس از تایید ادمین موجودی شما افزایش می‌یابد.",
        reply_markup=main_keyboard(),
    )
    bot.delete_state(user_id=user_id, chat_id=message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("charge_ok:"))
def charge_approve(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, text="دسترسی ندارید", show_alert=True)
        return

    request_id = parse_callback_int(call.data, "charge_ok:")
    if request_id is None:
        bot.answer_callback_query(call.id, text="داده نامعتبر است", show_alert=True)
        return

    result = db.finalize_charge_request(request_id, "approved")
    if result is None:
        bot.answer_callback_query(call.id, text="این درخواست قبلا بررسی شده", show_alert=True)
        return

    user_id, amount = result
    balance = db.get_balance(user_id)
    bot.answer_callback_query(call.id, text="شارژ تایید شد")
    try:
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=(call.message.text or "") + "\n\n✅ شارژ تایید شد",
        )
    except Exception:
        logger.exception("failed to update charge approve message")

    bot.send_message(
        chat_id=user_id,
        text=f"شارژ شما تایید شد.\nمبلغ {amount} تومان اضافه شد.\nموجودی فعلی: {balance} تومان",
    )


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("charge_no:"))
def charge_deny(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, text="دسترسی ندارید", show_alert=True)
        return

    request_id = parse_callback_int(call.data, "charge_no:")
    if request_id is None:
        bot.answer_callback_query(call.id, text="داده نامعتبر است", show_alert=True)
        return

    result = db.finalize_charge_request(request_id, "denied")
    if result is None:
        bot.answer_callback_query(call.id, text="این درخواست قبلا بررسی شده", show_alert=True)
        return

    user_id, _amount = result
    bot.answer_callback_query(call.id, text="شارژ رد شد")
    try:
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=(call.message.text or "") + "\n\n❌ شارژ رد شد",
        )
    except Exception:
        logger.exception("failed to update charge deny message")

    bot.send_message(chat_id=user_id, text="درخواست شارژ شما رد شد.")


@bot.message_handler(func=lambda message: message.text == "🎰 زیرمجموعه گیری")
def referral(message):
    if not require_membership(message):
        return

    caption = (
        "این لینک رفرال شما هست:\n\n"
        f"https://t.me/{config.bot_username}?start={message.from_user.id}"
    )
    image_path = Path(config.referral_image)
    if image_path.is_file():
        with image_path.open("rb") as photo:
            bot.send_photo(chat_id=message.chat.id, photo=photo, caption=caption)
    else:
        logger.warning("referral image missing: %s", image_path)
        bot.send_message(chat_id=message.chat.id, text=caption)


@bot.message_handler(func=lambda message: message.text == "☎️ پشتیبانی")
def support_user(message):
    if not require_membership(message):
        return

    bot.send_message(chat_id=message.chat.id, text="لطفا پیام خود را ارسال کنید:\nبرای انصراف /cancel")
    bot.set_state(
        user_id=message.from_user.id,
        state=Support.text,
        chat_id=message.chat.id,
    )


@bot.message_handler(state=Support.text)
def support_text(message):
    user_id = message.from_user.id
    username = message.from_user.username or "-"
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(text="پاسخ", callback_data=f"support_reply:{user_id}")
    )

    bot.send_message(
        chat_id=config.admin_id,
        text=(
            f"پیامی از طرف <code>{user_id}</code> با نام کاربری @{html.escape(username)} دریافت شد:\n"
            f"متن پیام:\n\n<b>{html.escape(message.text or '')}</b>"
        ),
        reply_markup=markup,
        parse_mode="HTML",
    )
    bot.send_message(chat_id=message.chat.id, text="پیام شما ارسال شد\nمنتظر جواب باشید!")
    db.save_support_message(user_id, message.text or "")
    bot.delete_state(user_id=user_id, chat_id=message.chat.id)


@bot.message_handler(state=Support.response)
def answer_text(message):
    if not is_admin(message.from_user.id):
        bot.delete_state(user_id=message.from_user.id, chat_id=message.chat.id)
        return

    chat_id = pending_replies.get(message.from_user.id)
    if chat_id is None:
        bot.send_message(chat_id=message.chat.id, text="کاربری برای پاسخ انتخاب نشده است.")
        bot.delete_state(user_id=message.from_user.id, chat_id=message.chat.id)
        return

    original = db.get_support_message(chat_id)
    if original is None:
        bot.send_message(chat_id=message.chat.id, text="مشکلی در ارسال پیام بود بعدا دوباره امتحان کنید.")
        pending_replies.pop(message.from_user.id, None)
        bot.delete_state(user_id=message.from_user.id, chat_id=message.chat.id)
        return

    bot.send_message(
        chat_id=chat_id,
        text=(
            f"پیام شما:\n<i>{html.escape(original)}</i>\n\n"
            f"پیام پشتیبانی:\n<b>{html.escape(message.text or '')}</b>"
        ),
        parse_mode="HTML",
    )
    bot.send_message(chat_id=message.chat.id, text="پیام شما ارسال شد!")
    db.delete_support_message(chat_id)
    pending_replies.pop(message.from_user.id, None)
    bot.delete_state(user_id=message.from_user.id, chat_id=message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("support_reply:"))
def admin_answer(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, text="دسترسی ندارید", show_alert=True)
        return

    user_id = parse_callback_user_id(call.data, "support_reply:")
    if user_id is None:
        bot.answer_callback_query(call.id, text="داده نامعتبر است", show_alert=True)
        return

    pending_replies[call.from_user.id] = user_id
    bot.answer_callback_query(call.id)
    bot.send_message(
        chat_id=call.message.chat.id,
        text=f"جواب شما به: <code>{user_id}</code>:",
        parse_mode="HTML",
    )
    bot.set_state(
        user_id=call.from_user.id,
        state=Support.response,
        chat_id=call.message.chat.id,
    )


def validate_config():
    errors = []
    if not config.token:
        errors.append("BOT_TOKEN is empty")
    if not config.admin_id:
        errors.append("ADMIN_ID is empty")
    if not config.channels:
        logger.warning("CHANNELS is empty — membership checks are disabled")
    if not config.links:
        logger.warning("CHANNEL_LINKS is empty")
    if errors:
        for item in errors:
            logger.error("config error: %s", item)
        raise SystemExit("Invalid configuration. Copy .env.example to .env and fill values.")


if __name__ == "__main__":
    validate_config()
    setup_database()
    bot.add_custom_filter(custom_filters.StateFilter(bot))
    logger.info("bot starting (admin_id=%s)", config.admin_id)
    bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
