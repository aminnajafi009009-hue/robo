"""ویرایشگر کامل رابط کاربری ربات.

این فایل عمداً فقط رابط کاربری مشتری را مدیریت می‌کند؛ بخش «مدیریت/پنل ادمین»
از ویرایشگر حذف شده تا تغییرات ادمین از این قسمت آسیب نبیند.

امکانات:
- ویرایش متن صفحه‌ها با حفظ MessageEntity و Custom Emoji (Premium Emoji)
- ویرایش نام تمام دکمه‌های ثبت‌شده
- آیکن Custom Emoji برای دکمه‌ها (در Bot APIهای جدید)
- جابه‌جایی دکمه‌ها
- مخفی/نمایش کردن دکمه‌ها
- تنظیم تعداد دکمه در هر ردیف؛ پیش‌فرض همیشه یک دکمه در هر ردیف است
- نگهداری تنظیمات در دیتابیس
"""

import datetime
import json
import logging
import re
import threading
from typing import Any

import database as db
import cache
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

# ⚡ PERFORMANCE:
# _ensure() used to execute a full DB transaction containing several
# CREATE TABLE IF NOT EXISTS / ALTER TABLE statements on EVERY outgoing
# user message because bot.py globally routes send/edit calls through
# apply_auto_text(). With Turso this is a network round-trip per message and
# was the main source of the 2-3 second latency.
#
# Keep the editor schema initialized once per process. Writes/migrations still
# use the same transaction; only the repeated read-path initialization is
# removed.
_SCHEMA_READY = False
_SCHEMA_INIT_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# رجیستری صفحه‌های مشتری
# ---------------------------------------------------------------------------
# callbackهایی که با _ تمام می‌شوند، الگوی callbackهای داینامیک هستند؛ مثلاً
# buy_ برای buy_plan_a / buy_plan_b.
SCREENS: dict[str, dict[str, Any]] = {
    # admin_panel: حذف شده از Editor چون Reply Keyboard ادمین به ۶ دسته تبدیل شد.
    # (backward compat: screens_keyboard و get_order همچنان کار می‌کنند)
    # "admin_panel": {...},
    "main_reply": {"category": "start", "label": "⌨️ منوی پایین کاربر", "default": "", "buttons": [
        ("plans", "🛒 خرید اشتراک"), ("free_test", "🎁 تست رایگان"), ("services", "📱 سرویس‌های من"),
        ("wallet", "💰 کیف پول"), ("referral", "👥 دعوت دوستان"), ("profile", "👤 پروفایل من"),
        ("support", "👨‍💻 پشتیبانی"), ("guides", "📚 راهنما"), ("agency", "🤝 درخواست نمایندگی"), ("agent_manage", "🤝 مدیریت نمایندگی"),
    ]},
    "start": {"category": "start", "label": "🚀 شروع و منوی اصلی", "default": "👋 خوش آمدید", "buttons": [
        ("plans", "🛒 خرید اشتراک"), ("buy_plan_test", "🎁 تست رایگان"),
        ("my_configs", "📱 سرویس‌های من"), ("wallet", "💰 کیف پول"),
        ("referral", "👥 دعوت دوستان و کسب درآمد"), ("profile", "👤 پروفایل من"),
        ("support", "👨‍💻 پشتیبانی"), ("user_guides", "📚 راهنما"),
    ]},
    "buy_plans": {"category": "shop", "label": "🛒 متن معرفی خرید اشتراک", "default": "🛒 انتخاب سرویس مناسب", "buttons": [
        ("plans_vip", "🚀 سرور VIP (V2Ray)"), ("cbuild_start", "🚀 کانفیگ خودتو بساز"), ("back", "🔙 بازگشت"),
    ]},
    "plans": {"category": "shop", "label": "🛒 خرید اشتراک", "default": "🛒 انتخاب سرویس مناسب", "buttons": [
        ("plans_vip", "🚀 سرور VIP (V2Ray)"), ("noop", "✨〰️〰️〰️〰️〰️✨"),
        ("cbuild_start", "🚀 کانفیگ خودتو بساز (ویژه VIP) 🛠"), ("noop", "✨〰️〰️〰️〰️〰️✨"),
        ("back", "🔙 بازگشت به منوی اصلی"),
    ]},
    "free_test": {"category": "shop", "label": "🎁 تست رایگان", "default": "🎁 تست رایگان", "buttons": [
        ("pay_wallet_", "⚡️ همین الان تست رایگان بگیر"), ("plans", "🔙 بازگشت"),
    ]},
    "vip_category_list": {"category": "shop", "label": "⭐ دسته‌بندی VIP", "default": "⭐ دسته‌بندی‌های VIP", "buttons": [
        ("vipcat_", "🚀 دسته‌بندی VIP"), ("cbuild_start", "🚀 کانفیگ خودتو بساز"), ("plans", "🔙 بازگشت"),
    ]},
    "vip_plans": {"category": "shop", "label": "🚀 پلن‌های VIP", "default": "🚀 پلن‌های VIP", "buttons": [
        ("buy_", "📅 پلن"), ("plans_vip", "🔙 بازگشت به دسته‌بندی‌ها"),
    ]},
    "plan_select": {"category": "shop", "label": "📅 انتخاب پلن", "default": "📅 انتخاب پلن", "buttons": [
        ("buy_", "📅 پلن"), ("plans", "🔙 بازگشت"),
    ]},
    "custom_build": {"category": "shop", "label": "🛠 کانفیگ خودتو بساز", "default": "🛠 کانفیگ خودتو بساز", "buttons": [
        ("cbuild_pay_wallet", "👛 پرداخت از کیف پول"), ("cbuild_pay_online", "🌐 پرداخت آنلاین"),
        ("cbuild_pay_card", "💳 کارت به کارت"), ("discount_cbuild", "🎟 ثبت کد تخفیف"),
        ("plans", "🔙 انصراف"),
    ]},
    "cbuild_payment_method": {"category": "shop", "label": "💳 روش پرداخت کانفیگ", "default": "💳 روش پرداخت", "buttons": [
        ("cbuild_pay_wallet", "👛 پرداخت از کیف پول"), ("cbuild_pay_online", "🌐 پرداخت آنلاین"),
        ("cbuild_pay_card", "💳 کارت به کارت"), ("discount_cbuild", "🎟 ثبت کد تخفیف"), ("plans", "🔙 انصراف"),
    ]},
    "cbuild_pay_wallet": {"category": "finance", "label": "👛 پرداخت کیف پول", "default": "👛 پرداخت از کیف پول", "buttons": [
        ("cbuild_change_payment", "🔄 روش پرداخت دیگر"), ("plans", "🔙 بازگشت"),
    ]},
    "cbuild_pay_online": {"category": "finance", "label": "🌐 پرداخت آنلاین", "default": "🌐 پرداخت آنلاین", "buttons": [
        ("cbuild_change_payment", "🔄 روش پرداخت دیگر"), ("plans", "🔙 بازگشت"),
    ]},
    "cbuild_pay_card": {"category": "finance", "label": "💳 پرداخت کارت به کارت", "default": "💳 پرداخت کارت به کارت", "buttons": [
        ("cbuild_change_payment", "🔄 روش پرداخت دیگر"), ("plans", "🔙 بازگشت"),
    ]},
    "plan_payment_method": {"category": "shop", "label": "💳 روش پرداخت خرید", "default": "💳 روش پرداخت", "buttons": [
        ("pay_wallet_", "👛 پرداخت از کیف پول"), ("pay_online_", "🌐 پرداخت آنلاین"),
        ("pay_card_", "💳 پرداخت کارت به کارت"), ("discount_plan_", "🎟 ثبت کد تخفیف"), ("plans", "🔙 بازگشت"),
    ]},
    "plan_pay_wallet": {"category": "finance", "label": "👛 خرید با کیف پول", "default": "👛 پرداخت از کیف پول", "buttons": [
        ("plans", "🔙 بازگشت"),
    ]},
    "plan_pay_online": {"category": "finance", "label": "🌐 خرید آنلاین", "default": "🌐 پرداخت آنلاین", "buttons": [
        ("plans", "🔙 بازگشت"),
    ]},
    "plan_pay_card": {"category": "finance", "label": "💳 خرید کارت به کارت", "default": "💳 پرداخت کارت به کارت", "buttons": [
        ("plans", "🔙 بازگشت"),
    ]},
    "discount_code_entry": {"category": "shop", "label": "🎟 ورود کد تخفیف", "default": "🎟 کد تخفیف خود را وارد کنید:", "buttons": [
        ("plans", "🔙 انصراف"), ("wallet", "🔙 بازگشت"),
    ]},
    "services": {"category": "services", "label": "📱 سرویس‌های من", "default": "📱 سرویس‌های شما", "buttons": [
        ("my_configs_vip", "🚀 سرویس‌های VIP من"), ("back", "🏠 بازگشت"),
    ]},
    "my_configs_empty": {"category": "services", "label": "📱 سرویس‌های من — خالی", "default": "📱 شما هنوز هیچ سرویسی خریداری نکرده‌اید.", "buttons": [("back", "🏠 بازگشت به منوی اصلی")]},
    "my_configs_has": {"category": "services", "label": "📱 سرویس‌های من", "default": "📱 سرویس‌های شما", "buttons": [
        ("my_configs_vip", "🚀 سرویس‌های VIP من"), ("back", "🏠 بازگشت به منوی اصلی"),
    ]},
    "my_configs_list_empty": {"category": "services", "label": "📋 لیست سرویس‌ها — خالی", "default": "📋 سرویسی برای نمایش وجود ندارد.", "buttons": [("my_configs", "🔙 بازگشت")]},
    "my_configs_list_has": {"category": "services", "label": "📋 لیست سرویس‌ها", "default": "📋 سرویس‌های شما", "buttons": [
        ("viewconfig_", "🚀 سرویس"), ("back", "🔙 بازگشت"),
    ]},
    "service_list_vip": {"category": "services", "label": "🚀 سرویس‌های VIP", "default": "🚀 سرویس‌های VIP", "buttons": [
        ("viewconfig_", "🚀 سرویس"), ("my_configs", "🔙 بازگشت"),
    ]},
    "config_detail": {"category": "services", "label": "📱 جزئیات سرویس", "default": "📱 جزئیات سرویس", "buttons": [
        ("renewcfg_", "🔁 تمدید سرویس"), ("viewqr_", "🖼 مشاهده کیوآرکد"),
        ("mirrorconfigs_", "🔗 دریافت کانفیگ‌های تکی"), ("usersvclink_", "🔄 تغییر لینک ساب"),
        ("usersvcenable_", "▶️ فعال‌کردن لینک ساب"), ("usersvcdisable_", "⏸ غیرفعال‌کردن لینک ساب"),
        ("delconfig_", "🗑 حذف سرویس"), ("back", "🔙 بازگشت"),
    ]},
    "config_delete_confirm": {"category": "services", "label": "🗑 تأیید حذف سرویس", "default": "⚠️ مطمئنی می‌خوای این سرویس رو حذف کنی؟", "buttons": [
        ("delconfirm_", "✅ بله، حذف کن"), ("viewconfig_", "❌ انصراف"),
    ]},
    "renew_done": {"category": "services", "label": "✅ نتیجه تمدید", "default": "✅ تمدید سرویس «{service_name}» با موفقیت انجام شد.\n\n📦 حجم اضافه: {added_volume}\n⏳ زمان اضافه: {added_days}\n\n📦 وضعیت فعلی: {current_package}\n🔗 لینک ساب شما تغییر نکرده است.", "buttons": [("back", "🔙 بازگشت")]},
    "renew_menu": {"category": "services", "label": "🔁 تمدید سرویس", "default": "🔁 تمدید سرویس", "buttons": [
        ("renewdays_", "⏳ انتخاب روز"), ("renewvol_", "📦 انتخاب حجم"),
        ("renew_cancel", "🔙 بازگشت"),
    ]},
    "wallet": {"category": "finance", "label": "💰 کیف پول", "default": "💰 کیف پول", "buttons": [
        ("charge", "💳 شارژ کیف پول"), ("use_discount", "🎟 ثبت کد تخفیف"), ("transactions", "📋 تراکنش‌های من"), ("back", "🏠 بازگشت"),
    ]},
    "wallet_free": {"category": "finance", "label": "💰 کیف پول آزاد", "default": "💰 کیف پول آزاد", "buttons": [("back", "🔙 بازگشت")]},
    "wallet_locked": {"category": "finance", "label": "🔒 کیف پول مسدود", "default": "🔒 کیف پول مسدود", "buttons": [("back", "🔙 بازگشت")]},
    "wallet_transactions": {"category": "finance", "label": "📋 تراکنش‌ها", "default": "📋 تراکنش‌های کیف پول", "buttons": [("back", "🔙 بازگشت")]},
    "wallet_charge": {"category": "finance", "label": "💳 شارژ کیف پول", "default": "💳 شارژ کیف پول", "buttons": [
        ("charge_", "💳 مبلغ شارژ"), ("wallet", "🔙 بازگشت"),
    ]},
    "walletcharge_method": {"category": "finance", "label": "💳 روش شارژ", "default": "💳 روش شارژ", "buttons": [
        ("chargepay_card_", "💳 کارت به کارت"), ("chargepay_online_", "🌐 پرداخت آنلاین"), ("charge", "🔙 بازگشت"),
    ]},
    "walletcharge_pay_card": {"category": "finance", "label": "💳 رسید شارژ", "default": "💳 پرداخت کارت به کارت", "buttons": [
        ("walletcharge_method", "🔄 روش پرداخت دیگر"), ("wallet", "🔙 بازگشت"),
    ]},
    "walletcharge_pay_online": {"category": "finance", "label": "🌐 شارژ آنلاین", "default": "🌐 پرداخت آنلاین", "buttons": [("wallet", "🔙 بازگشت")]},
    "referral": {"category": "finance", "label": "👥 دعوت دوستان", "default": (
        "👥 دعوت دوستان و کسب درآمد 💸\n\n"
        "دوستانتو دعوت کن و به‌ازای هر دعوت موفق، {reward_amount} تومان پاداش نقدی بگیر! 🎁\n\n"
        "🔗 لینک اختصاصی شما:\n{invite_link}\n\n"
        "🔑 کد اختصاصی: {invite_code}\n\n"
        "👤 تعداد دعوت: {invited_count}\n"
        "✅ دعوت‌های موفق: {successful_invites}\n"
        "🔓 مبلغ آزاد شده: {released_amount} تومان\n"
        "🔒 مبلغ در انتظار: {locked_wallet} تومان\n\n"
        "{condition_text}"
    ), "buttons": [("back", "🔙 بازگشت")]},
    "profile": {"category": "services", "label": "👤 پروفایل", "default": "👤 پروفایل شما", "buttons": [
        ("wallet_free", "💰 کیف پول آزاد"), ("wallet_locked", "🔒 کیف پول مسدود"), ("purchase_history", "🛒 تاریخچه خرید"),
        ("transactions", "📋 تاریخچه تراکنش"), ("referral", "🔗 لینک دعوت اختصاصی"), ("back", "🏠 بازگشت به منوی اصلی"),
    ]},
    "purchase_history": {"category": "services", "label": "🧾 تاریخچه خرید", "default": (
        "🛒 تاریخچه خرید شما:\n\n"
        "{purchase_reports}\n\n"
        "💰 مجموع خرید: {total_spent} تومان\n"
        "📊 تعداد خریدها: {purchase_count}"
    ), "buttons": [("profile", "🔙 بازگشت")]},
    "support": {"category": "services", "label": "👨‍💻 پشتیبانی", "default": "👨‍💻 پشتیبانی", "buttons": [("ticket", "🎫 ارسال تیکت"), ("back", "🏠 بازگشت")]},
    "ticket_write": {"category": "services", "label": "🎫 ارسال تیکت", "default": "🎫 متن تیکت خود را ارسال کنید:", "buttons": [("ticket_cancel", "❌ انصراف")]},
    "guides_empty": {"category": "services", "label": "📚 راهنما — خالی", "default": "📚 راهنما و آموزش‌ها", "buttons": [("back", "🔙 بازگشت")]},
    "guides_has": {"category": "services", "label": "📚 راهنما", "default": "📚 راهنما و آموزش‌ها", "buttons": [("guideopen_", "📖 راهنما"), ("user_guides", "📚 فهرست راهنماها"), ("back", "🏠 بازگشت به منوی اصلی")]},
    "join_confirmed": {"category": "start", "label": "✅ عضویت تأیید شد", "default": "منوی اصلی در پایین صفحه فعال شد ✅", "buttons": []},
    "start_join_required": {"category": "start", "label": "🔐 عضویت اجباری", "default": "⚠️ برای استفاده از ربات ابتدا در کانال‌های زیر عضو شوید:", "buttons": []},
    "start_welcome": {"category": "start", "label": "👋 خوش‌آمدگویی", "default": "👋 خوش آمدید", "buttons": [
        ("plans", "🛒 خرید اشتراک"), ("buy_plan_test", "🎁 تست رایگان"), ("my_configs", "📱 سرویس‌های من"),
        ("wallet", "💰 کیف پول"), ("referral", "👥 دعوت دوستان"), ("profile", "👤 پروفایل"),
        ("support", "👨‍💻 پشتیبانی"), ("user_guides", "📚 راهنما"),
    ]},
    "agency_request": {"category": "services", "label": "🤝 درخواست نمایندگی", "default": "🤝 درخواست نمایندگی", "buttons": [("back", "🔙 بازگشت")]},
    "agent_manage": {"category": "services", "label": "🤝 مدیریت نمایندگی", "default": "🤝 مدیریت نمایندگی", "buttons": [("agent_stats", "📊 آمار نمایندگی"), ("agent_prefix", "🏷 نام دلخواه ساخت کانفیگ"), ("back", "🔙 بازگشت")]},
    "config_delivery": {"category": "services", "label": "📤 تحویل کانفیگ", "default": (
        "✅ سرویس با موفقیت ایجاد شد\n\n"
        "👤 نام کاربری سرویس : {name}\n"
        "🇺🇳 لوکیشن: {location}\n"
        "⏳ مدت زمان: {days}\n"
        "🗜 حجم سرویس: {volume}\n"
        "👤 تعداد کاربر: {users}\n\n"
        "لینک اتصال:\n{sub_link}\n\n"
        "🧑‍🦯 شما میتوانید شیوه اتصال را با فشردن دکمه زیر دریافت کنید."
    ), "buttons": [("guide", "🧑‍🦯 دریافت روش اتصال")]},
    "receipt_submitted": {"category": "finance", "label": "🧾 ارسال رسید", "default": "رسید شما ارسال شد.", "buttons": [("back", "🏠 بازگشت به صفحه اصلی")]},
    "card_payment_actions": {"category": "finance", "label": "💳 اقدامات پرداخت کارت‌به‌کارت", "default": "", "buttons": [
        ("changepay_", "🔄 انتخاب روش پرداخت دیگر"),
    ]},
    "insufficient_balance": {"category": "finance", "label": "💰 موجودی ناکافی", "default": "❌ موجودی کیف پول کافی نیست.", "buttons": [
        ("wallet", "💵 شارژ کیف پول"), ("back", "🔙 بازگشت"),
    ]},
}

CATEGORIES = {
    # ——— کاربران ———
    "start":          "🚀 شروع",
    "main_menu":      "🏠 منوی اصلی",
    "shop":           "🛍 خرید",
    "renewal":        "🔁 تمدید",
    "services":       "📱 سرویس‌ها",
    "test":           "🎁 تست",
    "custom_service": "🛠 سرویس سفارشی",
    "wallet":         "💰 کیف پول",
    "payment":        "💳 پرداخت",
    "finance":        "🯧 فاکتور",
    "discount":       "🎟 تخفیف",
    "referral":       "👥 رفرال",
    "agency":         "🤝 نمایندگی",
    "active_services":"✅ سرویس‌های فعال",
    "expired_services":"❌ سرویس‌های منقضی",
    "errors":         "⚠️ پیام‌های خطا",
    "success":        "✅ پیام‌های موفقیت",
    "guides":         "📚 راهنما",
    "support":        "📞 پشتیبانی",
    # ——— مدیریت ———
    "admin":               "🛡 پنل مدیریت",
    "admin_users":         "👥 کاربران (ادمین)",
    "admin_stats":         "📊 آمار",
    "admin_tickets":       "🎫 تیکت‌ها",
    "admin_broadcast":     "📢 پیام همگانی",
    "admin_orders":        "📥 سفارش‌ها",
    "admin_payments":      "💳 پرداخت‌ها (ادمین)",
    "admin_receipts":      "🧲 رسیدها",
    "admin_services":      "📱 سرویس‌ها (ادمین)",
    "admin_panels":        "🖥 پنل‌ها",
    "admin_vip":           "⭐ VIP",
    "admin_test":          "🎁 تست (ادمین)",
    "admin_renewal":       "🔁 تمدید (ادمین)",
    "admin_agents":        "🤝 نمایندگان",
    "admin_discounts":     "🎟 تخفیف‌ها (ادمین)",
    "admin_referral":      "👥 رفرال (ادمین)",
    "admin_wallet":        "💰 کیف پول (ادمین)",
    "admin_payg":          "⚡ Pay As You Go",
    "admin_miniapp":       "🖥 Mini App",
    "admin_settings":      "⚙️ تنظیمات",
    # ——— مابقی ———
    "all_messages": "🧩 تمام پیام‌ها و اعلان‌ها",
    "all_buttons":  "🔘 تمام دکمه‌های قابل ویرایش",
}

def _ensure() -> None:
    """Ensure UI-editor tables exist, but never hit Turso repeatedly.

    The old implementation opened a transaction and ran all CREATE/ALTER
    statements on every call. Since get_text()/get_button()/get_layout() are
    called while rendering almost every message, that turned the global UI
    editor hook into a database round-trip for every response.
    """
    global _SCHEMA_READY

    if _SCHEMA_READY:
        return

    # Only the first caller performs the migration. Other callers wait once,
    # then return without touching the database.
    with _SCHEMA_INIT_LOCK:
        if _SCHEMA_READY:
            return

        with db.transaction() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS ui_text_overrides (
                key TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                entities_json TEXT,
                updated_at TEXT NOT NULL
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS ui_button_overrides (
                screen_key TEXT NOT NULL,
                callback_key TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY(screen_key,callback_key)
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS ui_button_meta (
                screen_key TEXT NOT NULL,
                callback_key TEXT NOT NULL,
                custom_emoji_id TEXT,
                hidden INTEGER NOT NULL DEFAULT 0,
                style TEXT,
                PRIMARY KEY(screen_key,callback_key)
            )""")
            try:
                cur.execute("ALTER TABLE ui_button_meta ADD COLUMN style TEXT")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

            cur.execute("""CREATE TABLE IF NOT EXISTS ui_layouts (
                screen_key TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'vertical',
                columns INTEGER NOT NULL DEFAULT 1,
                order_json TEXT,
                row_widths_json TEXT
            )""")
            try:
                cur.execute("ALTER TABLE ui_layouts ADD COLUMN row_widths_json TEXT")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            cur.execute("""CREATE TABLE IF NOT EXISTS ui_custom_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                screen_key TEXT NOT NULL,
                text TEXT NOT NULL,
                style TEXT NOT NULL DEFAULT 'primary',
                action_type TEXT NOT NULL,
                action_value TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""")

        _SCHEMA_READY = True


def _pattern_matches(pattern: str, callback_data: str) -> bool:
    return callback_data == pattern or (pattern.endswith("_") and callback_data.startswith(pattern))


def get_screen(key: str):
    return SCREENS.get(key)


def category_label(key: str):
    return CATEGORIES.get(key, key)


def editor_mode_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ ویرایش متن", callback_data="ui_mode:text", style="primary")],
        [InlineKeyboardButton(text="🔘 ویرایش دکمه‌ها", callback_data="ui_mode:buttons", style="primary")],
    ])


def categories_keyboard(mode: str = "text"):
    _ensure()
    items = list(CATEGORIES.items())
    rows = []
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(text=v, callback_data=f"ui_cat:{mode}:{k}", style="primary") for k, v in items[i:i + 2]]
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_text_editor", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def text_categories_keyboard():
    """کیبورد دسته‌بندی‌های متن از text_catalog"""
    try:
        import text_catalog as _tc
        cats = list(_tc.TEXT_CATEGORIES.keys())
    except Exception:
        cats = []
    rows = []
    for i in range(0, len(cats), 2):
        row = []
        for cat in cats[i:i+2]:
            row.append(InlineKeyboardButton(
                text=cat,
                callback_data=f"uitc_cat:{cat[:40]}",
                style="primary"
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_text_editor", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def text_category_entries_keyboard(category: str, page: int = 0, page_size: int = 8):
    """کیبورد فهرست متن‌های یک دسته"""
    try:
        import text_catalog as _tc
        entries = _tc.TEXT_CATEGORIES.get(category, [])
    except Exception:
        entries = []
    total = len(entries)
    start = page * page_size
    page_entries = entries[start:start + page_size]
    rows = []
    for key, default_text in page_entries:
        current = get_text(key, default_text).replace('\n', ' ').strip()
        preview = current[:28] + ('…' if len(current) > 28 else '')
        rows.append([InlineKeyboardButton(
            text=f"✏️ {preview}",
            callback_data=f"uitc_key:{key}",
            style="primary"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"uitc_pg:{category[:30]}:{page-1}", style="primary"))
    if start + page_size < total:
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"uitc_pg:{category[:30]}:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به دسته‌بندی‌ها", callback_data="uitc_cats", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def text_entry_keyboard(key: str, category: str = ""):
    """کیبورد ویرایش یک متن"""
    rows = [
        [InlineKeyboardButton(text="✏️ ویرایش این متن", callback_data=f"uitc_edit:{key}", style="primary")],
    ]
    # بازنشانی به پیش‌فرض
    rows.append([InlineKeyboardButton(text="🔄 بازنشانی به پیش‌فرض", callback_data=f"uitc_reset:{key}", style="danger")])
    if category:
        rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"uitc_cat:{category[:40]}", style="danger")])
    else:
        rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="uitc_cats", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def all_button_entries(page: int = 0, page_size: int = 8):
    """فهرست جامع دکمه‌های ثبت‌شده در Editor، مستقل از دسته‌بندی صفحه."""
    _ensure()
    items = []
    seen = set()
    for screen_key, screen in SCREENS.items():
        for cb, label in screen.get("buttons", []):
            ident = (screen_key, cb)
            if ident in seen:
                continue
            seen.add(ident)
            items.append({
                "screen_key": screen_key,
                "callback_key": cb,
                "label": label,
                "screen_label": screen.get("label", screen_key),
            })
    items.sort(key=lambda x: (x["screen_label"], x["screen_key"], x["callback_key"]))
    start = max(0, int(page)) * page_size
    return items[start:start + page_size], len(items)


def all_buttons_keyboard(page: int = 0, page_size: int = 8):
    items, total = all_button_entries(page, page_size)
    rows = []
    for item in items:
        label = get_button(item["screen_key"], item["callback_key"], item["label"])
        if len(label) > 34:
            label = label[:31] + "…"
        rows.append([InlineKeyboardButton(
            text=f"🔘 {label}",
            callback_data=f"ui_screen:buttons:{item['screen_key']}", style="primary")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"ui_buttons_page:{page-1}", style="primary"))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"ui_buttons_page:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="ui_mode:buttons", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def screens_keyboard(cat: str, mode: str = "text"):
    _ensure()
    if cat == "all_messages" and mode == "text":
        return text_categories_keyboard()
    if cat == "all_buttons" and mode == "buttons":
        return all_buttons_keyboard(0)
    rows = []
    for key, screen in SCREENS.items():
        if screen.get("category") != cat:
            continue
        # فیلتر: اگر نه default text دارد نه button، نشان نده
        has_text = bool((screen.get("default") or "").strip())
        has_buttons = bool(screen.get("buttons"))
        if mode == "text" and not has_text:
            continue
        if mode == "buttons" and not has_buttons:
            continue
        rows.append([InlineKeyboardButton(text=screen["label"], callback_data=f"ui_screen:{mode}:{key}", style="primary")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="ui_mode:" + mode, style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_text(key: str, fallback: str | None = None) -> str:
    _ensure()
    # perf: پرتکرارترین تابع کل ربات — تقریباً هر متنی که به کاربر نشون داده
    # می‌شه از همینجا رد می‌شه؛ کش کردنش بیشترین تاثیر رو روی سرعت پاسخ‌گویی داره.
    cache_key = f"uitext:{key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value if cached_value is not None else (fallback if fallback is not None else SCREENS.get(key, {}).get("default", ""))
    cur = db.get_connection().cursor()
    cur.execute("SELECT text FROM ui_text_overrides WHERE key=?", (key,))
    row = cur.fetchone()
    if row:
        cache.set(cache_key, row[0])
        return row[0]
    # Overrides saved by older Editor versions used a hash of the template only.
    # Keep them readable after the new source-specific catalog is deployed.

    cache.set(cache_key, None)
    return fallback if fallback is not None else SCREENS.get(key, {}).get("default", "")


def get_alert_text(key: str, fallback: str = "") -> str:
    """متن کوتاه Callback Alert/Toast را از همان مخزن ویرایش متن می‌خواند."""
    return get_text(key, fallback)


def get_entities(key: str):
    _ensure()
    cache_key = f"uientities:{key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value
    cur = db.get_connection().cursor()
    cur.execute("SELECT entities_json FROM ui_text_overrides WHERE key=?", (key,))
    row = cur.fetchone()
    if not row or not row[0]:
        cache.set(cache_key, [])
        return []
    try:
        value = json.loads(row[0])
    except Exception:
        value = []
    cache.set(cache_key, value)
    return value



def _utf16_to_py_index(text: str, offset_units: int) -> int:
    units = 0
    for i, ch in enumerate(text):
        if units >= offset_units:
            return i
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def _py_to_utf16_offset(text: str, index: int) -> int:
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text[:index])


def _sanitize_config_detail_template(template: str, entities: list[dict]):
    """دو فیلد بلااستفاده‌ی قدیمی جزئیات سرویس را حذف می‌کند و آفست Custom Emojiها را سالم نگه می‌دارد."""
    if not template:
        return template, entities

    blocked_labels = (
        "🔃 آخرین زمان آپدیت لینک اشتراک",
        "🌍 کلاینت متصل شده",
    )
    removed = []
    pieces = []
    cursor = 0
    pos = 0
    for line in template.splitlines(keepends=True):
        line_start = pos
        line_end = pos + len(line)
        pos = line_end
        if any(label in line for label in blocked_labels):
            removed.append((line_start, line_end))
            continue
        pieces.append(line)
    if not removed:
        return template, entities

    sanitized = "".join(pieces)
    remapped = []
    for entity in entities or []:
        try:
            old_start = _utf16_to_py_index(template, int(entity.get("offset", 0)))
            old_end = _utf16_to_py_index(template, int(entity.get("offset", 0)) + int(entity.get("length", 0)))
        except Exception:
            continue
        if any(old_start < end and old_end > start for start, end in removed):
            continue
        shift = sum(end - start for start, end in removed if end <= old_start)
        item = dict(entity)
        new_start = max(0, old_start - shift)
        new_end = max(new_start, old_end - shift)
        item["offset"] = _py_to_utf16_offset(sanitized, new_start)
        item["length"] = _py_to_utf16_offset(sanitized, new_end) - item["offset"]
        remapped.append(item)
    return sanitized, remapped


def render_template(key: str, values: dict[str, Any], fallback: str | None = None):
    """متن ذخیره‌شده را با placeholderها رندر می‌کند و Custom Emojiها را حفظ می‌کند."""
    template = get_text(key, fallback)
    entities = get_entities(key)
    if key == "config_detail":
        template, entities = _sanitize_config_detail_template(template, entities)
    if not values:
        return template, entities

    # ساخت متن جدید و نگاشت محدوده‌های متن قدیمی به متن جدید.
    parts = []
    cursor = 0
    mapping_segments = []  # (old_start, old_end, new_start, new_end)
    import re
    pattern = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    for match in pattern.finditer(template):
        parts.append(template[cursor:match.start()])
        new_start = sum(len(x) for x in parts)
        replacement = str(values.get(match.group(1), match.group(0)))
        parts.append(replacement)
        new_end = sum(len(x) for x in parts)
        # خود placeholder عمداً map نمی‌شود؛ اگر کسی Custom Emoji را داخل آن گذاشته باشد،
        # بهتر است آن entity حذف شود تا offset خراب به تلگرام ارسال نشود.
        mapping_segments.append((cursor, match.start(), sum(len(x) for x in parts[:-2]), new_start))
        cursor = match.end()
    parts.append(template[cursor:])
    rendered = "".join(parts)

    # entityها را بر اساس تعداد کاراکترهای قبل از هر placeholder جابه‌جا می‌کنیم.
    remapped = []
    for entity in entities:
        try:
            old_start = _utf16_to_py_index(template, int(entity.get("offset", 0)))
            old_end = _utf16_to_py_index(template, int(entity.get("offset", 0)) + int(entity.get("length", 0)))
        except Exception:
            continue
        delta = 0
        overlaps = False
        for match in pattern.finditer(template):
            if old_end <= match.start():
                break
            if old_start >= match.end():
                delta += len(str(values.get(match.group(1), match.group(0)))) - (match.end() - match.start())
            else:
                overlaps = True
                break
        if overlaps:
            continue
        new_start = old_start + delta
        new_end = old_end + delta
        item = dict(entity)
        item["offset"] = _py_to_utf16_offset(rendered, new_start)
        item["length"] = _py_to_utf16_offset(rendered, new_end) - item["offset"]
        remapped.append(item)
    return rendered, remapped

def set_text(key: str, text: str, entities=None):
    _ensure()
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_text_overrides(key,text,entities_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET text=excluded.text,entities_json=excluded.entities_json,updated_at=excluded.updated_at",
            (key, text, json.dumps(entities or [], ensure_ascii=False), datetime.datetime.now().isoformat()),
        )
    cache.invalidate(f"uitext:{key}")
    cache.invalidate(f"uientities:{key}")
    # Auto-rendered messages may contain this override.
    cache.invalidate_prefix("uiauto:")


def get_button(key: str, callback_key: str, default: str) -> str:
    _ensure()
    cache_key = f"uibtn:{key}:{callback_key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value if cached_value is not None else default
    cur = db.get_connection().cursor()
    cur.execute("SELECT text FROM ui_button_overrides WHERE screen_key=? AND callback_key=?", (key, callback_key))
    row = cur.fetchone()
    cache.set(cache_key, row[0] if row else None)
    return row[0] if row else default


def set_button(key: str, callback_key: str, text: str, custom_emoji_id: str | None = None):
    _ensure()
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_button_overrides(screen_key,callback_key,text) VALUES(?,?,?) "
            "ON CONFLICT(screen_key,callback_key) DO UPDATE SET text=excluded.text",
            (key, callback_key, text),
        )
        cur.execute(
            "INSERT INTO ui_button_meta(screen_key,callback_key,custom_emoji_id,hidden) VALUES(?,?,?,COALESCE((SELECT hidden FROM ui_button_meta WHERE screen_key=? AND callback_key=?),0)) "
            "ON CONFLICT(screen_key,callback_key) DO UPDATE SET custom_emoji_id=excluded.custom_emoji_id",
            (key, callback_key, custom_emoji_id, key, callback_key),
        )
    cache.invalidate(f"uibtn:{key}:{callback_key}")
    cache.invalidate(f"uibtnmeta:{key}:{callback_key}")


def get_button_meta(key: str, callback_key: str) -> dict:
    _ensure()
    cache_key = f"uibtnmeta:{key}:{callback_key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value
    cur = db.get_connection().cursor()
    cur.execute("SELECT custom_emoji_id,hidden,style FROM ui_button_meta WHERE screen_key=? AND callback_key=?", (key, callback_key))
    row = cur.fetchone()
    value = {"custom_emoji_id": row[0] if row else None, "hidden": bool(row[1]) if row else False, "style": row[2] if row else None}
    cache.set(cache_key, value)
    return value


_VALID_BUTTON_STYLES = {"primary", "secondary", "success", "danger"}
_STYLE_CYCLE = [None, "primary", "success", "danger"]
_STYLE_DOTS = {None: "⚪️", "primary": "🔵", "success": "🟢", "danger": "🔴"}


def _style_dot(style: str | None) -> str:
    return _STYLE_DOTS.get(style, "⚪️")


def next_button_style(current: str | None) -> str | None:
    """چرخه‌ی یک‌کلیکی رنگ: ⚪️(پیش‌فرض) → 🔵(آبی) → 🟢(سبز) → 🔴(قرمز) → ⚪️...
    طبق Bot API 9.4 فقط همین سه رنگ واقعاً روی تلگرام دیده می‌شوند؛ ⚪️ یعنی
    بدون override (رنگ پیش‌فرض اپ کاربر)."""
    try:
        idx = _STYLE_CYCLE.index(current)
    except ValueError:
        idx = 0
    return _STYLE_CYCLE[(idx + 1) % len(_STYLE_CYCLE)]


def set_button_style(key: str, callback_key: str, style: str | None):
    """🎨 رنگ یک دکمه‌ی ثابت را عوض می‌کند. طبق Bot API 9.4 (۹ فوریه ۲۰۲۶)،
    تلگرام واقعاً از فیلد style روی دکمه‌های inline پشتیبانی می‌کند
    ('primary'=آبی، 'success'=سبز، 'danger'=قرمز)؛ 'secondary' یعنی رنگ
    پیش‌فرض اپ (بدون override). این مقدار همان چیزی است که مستقیماً به تلگرام
    فرستاده می‌شود، پس روی کلاینت‌های به‌روز (بعد از ۹ فوریه ۲۰۲۶) واقعاً
    دیده می‌شود."""
    if style is not None and style not in _VALID_BUTTON_STYLES:
        raise ValueError("رنگ دکمه نامعتبر است")
    _ensure()
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_button_meta(screen_key,callback_key,custom_emoji_id,hidden,style) "
            "VALUES(?,?,NULL,COALESCE((SELECT hidden FROM ui_button_meta WHERE screen_key=? AND callback_key=?),0),?) "
            "ON CONFLICT(screen_key,callback_key) DO UPDATE SET style=excluded.style",
            (key, callback_key, key, callback_key, style),
        )
    cache.invalidate(f"uibtnmeta:{key}:{callback_key}")


def toggle_button(key: str, callback_key: str):
    _ensure()
    old = get_button_meta(key, callback_key)["hidden"]
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_button_meta(screen_key,callback_key,custom_emoji_id,hidden) VALUES(?,?,NULL,?) "
            "ON CONFLICT(screen_key,callback_key) DO UPDATE SET hidden=excluded.hidden",
            (key, callback_key, int(not old)),
        )
    cache.invalidate(f"uibtnmeta:{key}:{callback_key}")


def get_layout(key: str):
    _ensure()
    cache_key = f"uilayout:{key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value
    cur = db.get_connection().cursor()
    cur.execute("SELECT mode,columns,order_json,row_widths_json FROM ui_layouts WHERE screen_key=?", (key,))
    row = cur.fetchone()
    if not row:
        # پیش‌فرض همه منوها یک دکمه در هر ردیف است؛ ادمین می‌تواند
        # از Editor چیدمان ۲/۳/۴تایی را انتخاب و ذخیره کند.
        value = {"mode": "vertical", "columns": 1, "order": None, "row_widths": None}
        cache.set(cache_key, value)
        return value
    try:
        order = json.loads(row[2]) if row[2] else None
    except Exception:
        order = None
    mode = row[0] if row[0] in ("vertical", "inline") else "vertical"
    columns = int(row[1] or 1)
    if mode == "vertical":
        columns = 1
    else:
        columns = min(4, max(1, columns))
    value = {"mode": mode, "columns": columns, "order": order}
    cache.set(cache_key, value)
    return value


def set_layout(key: str, mode: str, columns: int = 1):
    if mode not in ("vertical", "inline"):
        mode = "vertical"
    columns = 1 if mode == "vertical" else min(4, max(1, int(columns)))
    # تعداد دکمه‌ها لازم نیست مضرب columns باشد؛ ردیف آخر می‌تواند ناقص باشد.
    # مثال: 7 دکمه با columns=2 => 2+2+2+1.
    _ensure()
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_layouts(screen_key,mode,columns,order_json) VALUES(?,?,?,NULL) "
            "ON CONFLICT(screen_key) DO UPDATE SET mode=excluded.mode,columns=excluded.columns",
            (key, mode, columns),
        )
    cache.invalidate(f"uilayout:{key}")


def set_row_layout(key: str, widths: list[int] | None):
    _ensure()
    clean=[max(1,min(8,int(x))) for x in (widths or []) if int(x)>0]
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_layouts(screen_key,mode,columns,order_json,row_widths_json) VALUES(?,?,?,NULL,?) "
            "ON CONFLICT(screen_key) DO UPDATE SET row_widths_json=excluded.row_widths_json",
            (key, "inline" if clean else "vertical", max(clean or [1]), json.dumps(clean, ensure_ascii=False) if clean else None),
        )
    cache.invalidate(f"uilayout:{key}")


def _base_order(key: str):
    return [cb for cb, _ in SCREENS.get(key, {}).get("buttons", [])]


def get_order(key: str):
    base = _base_order(key)
    order = get_layout(key).get("order") or base[:]
    # حذف callbackهای قدیمی/نامعتبر و اضافه‌کردن callbackهای جدید به انتهای لیست.
    order = [x for x in order if x in base]
    order += [x for x in base if x not in order]
    return order


def move_button(key: str, callback_key: str, direction: str):
    order = get_order(key)
    if callback_key not in order:
        return
    i = order.index(callback_key)
    j = i - 1 if direction == "up" else i + 1
    if j < 0 or j >= len(order):
        return
    order[i], order[j] = order[j], order[i]
    lay = get_layout(key)
    _ensure()
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO ui_layouts(screen_key,mode,columns,order_json) VALUES(?,?,?,?) "
            "ON CONFLICT(screen_key) DO UPDATE SET order_json=excluded.order_json",
            (key, lay["mode"], lay["columns"], json.dumps(order, ensure_ascii=False)),
        )
    cache.invalidate(f"uilayout:{key}")


def _button_default(key: str, callback_key: str) -> str:
    for cb, label in SCREENS.get(key, {}).get("buttons", []):
        if cb == callback_key:
            return label
    return "🔘 دکمه"


def override_button_text(callback_data: str, default: str) -> str:
    """برای سازگاری با تمام محل‌های قدیمی که screen_key را نمی‌فرستند.

    اگر callback در چند صفحه وجود داشته باشد، فقط وقتی دقیقاً یک صفحه پیدا شود
    override اعمال می‌شود؛ بنابراین «back» دیگر باعث خراب‌شدن منوی دیگری نمی‌شود.
    """
    matches = []
    for key, screen in SCREENS.items():
        for cb, label in screen.get("buttons", []):
            if _pattern_matches(cb, callback_data):
                matches.append((key, cb, label))
    if len(matches) == 1:
        key, cb, label = matches[0]
        return get_button(key, cb, default)
    # بعضی helperهای قدیمی مثل back_button بدون screen_key ساخته می‌شوند.
    # اگر فقط یک override واقعی برای این callback ثبت شده باشد، همان را اعمال کن؛
    # در غیر این صورت برای جلوگیری از تغییر ناخواسته‌ی چند صفحه، به default برگرد.
    overridden = set()
    for key, cb, label in matches:
        value = get_button(key, cb, default)
        if value != default:
            overridden.add(value)
    if len(overridden) == 1:
        return next(iter(overridden))
    return default


def button_meta_for_callback(callback_data: str):
    matches = []
    for key, screen in SCREENS.items():
        for cb, _ in screen.get("buttons", []):
            if _pattern_matches(cb, callback_data):
                matches.append((key, cb))
    if len(matches) != 1:
        return None
    key, cb = matches[0]
    meta = get_button_meta(key, cb)
    meta.update({"screen_key": key, "callback_key": cb})
    return meta


def preview_text(key: str, page: int = 0) -> str:
    base = get_text(key, SCREENS[key].get("default", ""))
    variants = auto_entries_for_screen(key)
    page_size = 6
    start = max(0, int(page)) * page_size
    visible = variants[start:start + page_size]
    chunks = [f"📝 پیش‌نمایش «{SCREENS[key]['label']}»", "", base]
    if variants:
        chunks.extend(["", f"🧩 متن‌های واقعی این مسیر — صفحه {page + 1}/{max(1, (len(variants)+page_size-1)//page_size)}"])
        for i, item in enumerate(visible, start + 1):
            preview = get_text(item["key"], item["template"])
            chunks.extend(["", f"🧩 متن واقعی {i} — {item['source']}", preview])
    result = "\n".join(chunks)
    return result[:3900] if len(result) > 3900 else result


def text_screen_keyboard(key: str, page: int = 0):
    rows = [[InlineKeyboardButton(text="✏️ تغییر متن اصلی", callback_data=f"ui_edit_text:{key}", style="primary")]]
    variants = auto_entries_for_screen(key)
    page_size = 6
    start = max(0, int(page)) * page_size
    visible = variants[start:start + page_size]
    for i, item in enumerate(visible, start + 1):
        current = get_text(item["key"], item["template"]).replace("\n", " ").strip()
        preview = current[:30] + ("…" if len(current) > 30 else "")
        rows.append([InlineKeyboardButton(text=f"🧩 متن {i}: {preview}"[:48], callback_data=f"ui_edit_auto:{item['key']}", style="primary")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"ui_screen_textpage:{key}:{page-1}", style="primary"))
    if (page + 1) * page_size < len(variants):
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"ui_screen_textpage:{key}:{page+1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به فهرست متن‌ها", callback_data="ui_mode:text", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def button_screen_keyboard(key: str, page: int = 0):
    sc = SCREENS[key]
    lay = get_layout(key)
    order = get_order(key)
    labels = dict(sc.get("buttons") or {})
    page_size = 8
    start = max(0, int(page)) * page_size
    visible_order = order[start:start + page_size]
    rows = []
    for cb in visible_order:
        if cb not in labels:
            continue
        meta = get_button_meta(key, cb)
        status = "🚫" if meta["hidden"] else "👁"
        # UX: "noop" یک دکمه‌ی تزئینی/جداکننده است (مثلاً خط‌چین بین دو بخش
        # از منو) و هیچ عملکردی هنگام کلیک نداره. قبلاً دقیقاً مثل بقیه‌ی
        # دکمه‌های واقعی نمایش داده می‌شد و ادمین گمان می‌کرد این یه دکمه‌ی
        # خراب/بی‌فایده‌ست؛ الان با برچسب مشخص می‌شه که این فقط یه جداکننده‌ی
        # ظاهریه (هنوز هم می‌شه متن/ایموجیش رو عوض کرد، فقط کلیک روش کاری
        # انجام نمی‌ده).
        if cb == "noop":
            rows.append([
                InlineKeyboardButton(text=f"🏷 (جداکننده‌ی تزئینی) {get_button(key, cb, labels[cb])}"[:60], callback_data=f"ui_button:{key}:{cb}", style="primary"),
                InlineKeyboardButton(text=status, callback_data=f"ui_toggle:{key}:{cb}", style="primary"),
            ])
            continue
        rows.append([
            InlineKeyboardButton(text=f"✏️ {get_button(key, cb, labels[cb])}"[:60], callback_data=f"ui_button:{key}:{cb}", style="primary"),
            InlineKeyboardButton(text=status, callback_data=f"ui_toggle:{key}:{cb}", style="primary"),
            InlineKeyboardButton(text=_style_dot(meta.get("style")), callback_data=f"ui_style:{key}:{cb}", style="primary"),
            InlineKeyboardButton(text="⬆️", callback_data=f"ui_move:{key}:{cb}:up", style="primary"),
            InlineKeyboardButton(text="⬇️", callback_data=f"ui_move:{key}:{cb}:down", style="primary"),
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"ui_button_page:{key}:{page-1}", style="primary"))
    if (page + 1) * page_size < len(order):
        nav.append(InlineKeyboardButton(text="بعدی ➡️", callback_data=f"ui_button_page:{key}:{page+1}", style="primary"))
    if nav:
        rows.append(nav)

    current_cols = min(4, max(1, int(lay.get("columns") or 1)))
    layout_row = []
    for cols in (1, 2, 3, 4):
        mark = "✅ " if cols == current_cols else ""
        layout_row.append(InlineKeyboardButton(
            text=f"{mark}{cols} دکمه/ردیف",
            callback_data=f"ui_layout:{key}:set:{cols}",
         style="primary"))
    rows.append(layout_row)
    rows.append([InlineKeyboardButton(
        text=f"📐 چیدمان فعلی: {current_cols} دکمه در هر ردیف",
        callback_data=f"ui_layout:{key}:cycle:{1 if current_cols >= 4 else current_cols + 1}",
     style="primary")])
    current_rows = ",".join(str(x) for x in (lay.get("row_widths") or [])) or "پیش‌فرض"
    rows.append([InlineKeyboardButton(text=f"🧩 الگوی ردیف‌ها: {current_rows}", callback_data=f"ui_rowlayout:{key}", style="primary")])
    custom_count = len(get_custom_buttons(key))
    rows.append([InlineKeyboardButton(
        text=f"➕ دکمه‌های سفارشی این صفحه ({custom_count})",
        callback_data=f"ui_cbtn_list:{key}",
     style="primary")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به فهرست دکمه‌ها", callback_data="ui_mode:buttons", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# دکمه‌های سفارشی ادمین (افزودن دکمه‌ی دلخواه به هر صفحه)
# ---------------------------------------------------------------------------

BUTTON_STYLES = {
    "primary": "🔵 آبی (پیش‌فرض)",
    "success": "🟢 سبز",
    "danger": "🔴 قرمز",
}

# مقصدهای پرتکرار داخل ربات که ادمین می‌تواند بدون دونستن callback خام،
# دکمه‌ی سفارشی‌اش را مستقیم به آن‌ها وصل کند (مثلاً «راهنما» یا «پشتیبانی»).
QUICK_DESTINATIONS = [
    ("plans", "🛒 خرید اشتراک"),
    ("my_configs", "📱 سرویس‌های من"),
    ("wallet", "💰 کیف پول"),
    ("referral", "👥 دعوت دوستان"),
    ("profile", "👤 پروفایل من"),
    ("support", "👨‍💻 پشتیبانی"),
    ("user_guides", "📚 فهرست راهنماها"),
    ("agency", "🤝 درخواست نمایندگی"),
    ("back", "🏠 منوی اصلی"),
]


def get_custom_buttons(screen_key: str):
    _ensure()
    cache_key = f"uicbtn:{screen_key}"
    cached_value, hit = cache.get(cache_key)
    if hit:
        return cached_value
    cur = db.get_connection().cursor()
    cur.execute(
        "SELECT id,screen_key,text,style,action_type,action_value,position FROM ui_custom_buttons "
        "WHERE screen_key=? ORDER BY position ASC, id ASC",
        (screen_key,),
    )
    rows = cur.fetchall()
    keys = ["id", "screen_key", "text", "style", "action_type", "action_value", "position"]
    value = [dict(zip(keys, r)) for r in rows]
    cache.set(cache_key, value)
    return value


def get_custom_button(button_id: int):
    _ensure()
    cur = db.get_connection().cursor()
    cur.execute(
        "SELECT id,screen_key,text,style,action_type,action_value,position FROM ui_custom_buttons WHERE id=?",
        (button_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    keys = ["id", "screen_key", "text", "style", "action_type", "action_value", "position"]
    return dict(zip(keys, row))


def add_custom_button(screen_key: str, text: str, style: str, action_type: str, action_value: str) -> int:
    _ensure()
    if style not in BUTTON_STYLES:
        style = "primary"
    with db.transaction() as cur:
        cur.execute("SELECT COALESCE(MAX(position),-1)+1 FROM ui_custom_buttons WHERE screen_key=?", (screen_key,))
        next_pos = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ui_custom_buttons(screen_key,text,style,action_type,action_value,position,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (screen_key, text, style, action_type, action_value, next_pos, datetime.datetime.now().isoformat()),
        )
        new_id = cur.lastrowid
    cache.invalidate(f"uicbtn:{screen_key}")
    return new_id


def delete_custom_button(button_id: int):
    _ensure()
    btn = get_custom_button(button_id)
    with db.transaction() as cur:
        cur.execute("DELETE FROM ui_custom_buttons WHERE id=?", (button_id,))
    if btn:
        cache.invalidate(f"uicbtn:{btn['screen_key']}")


def move_custom_button(button_id: int, direction: str):
    btn = get_custom_button(button_id)
    if not btn:
        return
    siblings = get_custom_buttons(btn["screen_key"])
    idx = next((i for i, b in enumerate(siblings) if b["id"] == button_id), None)
    if idx is None:
        return
    j = idx - 1 if direction == "up" else idx + 1
    if j < 0 or j >= len(siblings):
        return
    a, b = siblings[idx], siblings[j]
    with db.transaction() as cur:
        cur.execute("UPDATE ui_custom_buttons SET position=? WHERE id=?", (b["position"], a["id"]))
        cur.execute("UPDATE ui_custom_buttons SET position=? WHERE id=?", (a["position"], b["id"]))
    cache.invalidate(f"uicbtn:{btn['screen_key']}")


def custom_buttons_keyboard(screen_key: str):
    items = get_custom_buttons(screen_key)
    rows = []
    for b in items:
        kind = {"callback": "🔗", "url": "🌐", "webapp": "📲"}.get(b["action_type"], "🔘")
        rows.append([
            InlineKeyboardButton(text=f"{kind} {b['text']}"[:40], callback_data=f"ui_cbtn_open:{b['id']}", style="primary"),
            InlineKeyboardButton(text="⬆️", callback_data=f"ui_cbtn_move:{b['id']}:up", style="primary"),
            InlineKeyboardButton(text="⬇️", callback_data=f"ui_cbtn_move:{b['id']}:down", style="primary"),
            InlineKeyboardButton(text="🗑", callback_data=f"ui_cbtn_del:{b['id']}", style="primary"),
        ])
    rows.append([InlineKeyboardButton(text="➕ افزودن دکمه‌ی جدید", callback_data=f"ui_cbtn_add:{screen_key}", style="success")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به تنظیمات دکمه‌های این صفحه", callback_data=f"ui_screen:buttons:{screen_key}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def custom_button_style_keyboard(screen_key: str):
    rows = [[InlineKeyboardButton(text=v, callback_data=f"ui_cbtn_style:{screen_key}:{k}", style="primary")] for k, v in BUTTON_STYLES.items()]
    rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"ui_cbtn_list:{screen_key}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def custom_button_action_type_keyboard(screen_key: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 وصل به یکی از صفحات ربات", callback_data=f"ui_cbtn_type:{screen_key}:callback_quick", style="success")],
        [InlineKeyboardButton(text="✍️ وارد کردن callback دستی (پیشرفته)", callback_data=f"ui_cbtn_type:{screen_key}:callback_manual", style="danger")],
        [InlineKeyboardButton(text="🌐 لینک وب معمولی", callback_data=f"ui_cbtn_type:{screen_key}:url", style="primary")],
        [InlineKeyboardButton(text="📲 اپ‌لینک (Web App داخل تلگرام)", callback_data=f"ui_cbtn_type:{screen_key}:webapp", style="primary")],
        [InlineKeyboardButton(text="🔙 انصراف", callback_data=f"ui_cbtn_list:{screen_key}", style="danger")],
    ])


def quick_destination_keyboard(screen_key: str):
    rows = [[InlineKeyboardButton(text=label, callback_data=f"ui_cbtn_dest:{screen_key}:{cb}", style="primary")] for cb, label in QUICK_DESTINATIONS]
    rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"ui_cbtn_list:{screen_key}", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def screen_preview_markup(key: str):
    sc = SCREENS.get(key) or {}
    labels = dict(sc.get("buttons") or [])
    order = get_order(key)
    lay = get_layout(key)
    visible = []
    for cb in order:
        if cb not in labels or get_button_meta(key, cb)["hidden"]:
            continue
        visible.append(InlineKeyboardButton(text=get_button(key, cb, labels[cb]), callback_data=cb[:64], style="primary"))
    cols = lay["columns"] if lay["mode"] == "inline" else 1
    return InlineKeyboardMarkup(inline_keyboard=[visible[i:i+cols] for i in range(0, len(visible), cols)])


CATEGORY_LABELS = {
    # کاربران
    "start":          "🚀 شروع",
    "main_menu":      "🏠 منوی اصلی",
    "shop":           "🛒 خرید",
    "renewal":        "🔁 تمدید",
    "services":       "📱 سرویس‌ها",
    "free_test":      "🎁 تست",
    "custom_service": "🛠 سرویس سفارشی",
    "wallet":         "💰 کیف پول",
    "payment":        "💳 پرداخت",
    "invoice":        "🧧 فاکتور",
    "discount":       "🎟 تخفیف",
    "referral":       "🤝 رفرال",
    "agency":         "🤝 نمایندگی",
    "active_services":"🟢 سرویس‌های فعال",
    "expired_services":"🔴 سرویس‌های منقضی",
    "errors":         "🙅 پیام‌های خطا",
    "success":        "✅ پیام‌های موفقیت",
    "help":           "📚 راهنما",
    "support":        "🎫 پشتیبانی",
    # مدیریت
    "admin":          "🛡 پنل مدیریت",
    "admin_users":    "👥 مدیریت کاربران",
    "admin_stats":    "📊 آمار",
    "admin_tickets":  "🎫 تیکت‌ها",
    "admin_broadcast":"📢 پیام همگانی",
    "admin_orders":   "📋 سفارش‌ها",
    "admin_payments": "💰 پرداخت‌ها",
    "admin_receipts": "🧧 رسیدها",
    "admin_services": "📱 سرویس‌ها",
    "admin_panels":   "🖥 پنل‌ها",
    "admin_vip":      "🚀 VIP",
    "admin_test":     "🎁 تست",
    "admin_renewal":  "🔁 تمدید",
    "admin_agents":   "🤝 نمایندگان",
    "admin_discount": "🎟 تخفیف‌ها",
    "admin_referral": "🤝 رفرال",
    "admin_wallet":   "💰 کیف پول",
    "admin_payg":     "⚡ Pay As You Go",
    "admin_miniapp":  "🏠 Mini App",
    "admin_settings": "⚙️ تنظیمات",
    "finance":        "💰 مالی",
}
