GRID_CFG = {
	# بازار و صرافی
	"exchange_id": "binance",       # binance / okx / bybit / kucoin / ...
	"symbol": "BTC/USDT",           # جفت‌ارز

	# اندازه گرید
	"levels_per_side": 6,           # تعداد پله بالا و پایین (کل سطوح فعال = 2*n)
	"spacing_mode": "percent",      # percent یا arithmetic
	"upper_pct": 0.03,              # +3% بالای قیمت مرکز
	"lower_pct": 0.03,              # -3% پایین قیمت مرکز
	# اگر arithmetic خواستی، این دوتا نادیده گرفته میشن و step_absolute استفاده کن:
	"step_absolute": None,          # فاصله ثابت به دلار (مثلاً 50 دلار)؛ فقط وقتی spacing_mode="arithmetic"

	# حجم سفارش‌ها
	"quote_per_order": 20.0,        # اندازه هر سفارش بر حسب USDT (برای اسپات)
	"max_active_orders": None,      # سقف تعداد سفارش‌های باز (None یعنی بر اساس levels_per_side)

	# مدیریت ریسک و رفتار
	"recenter_on_break": True,      # اگر قیمت از بازه بالاتر/پایین‌تر رفت، گرید حول قیمت جدید بساز
	"kill_switch_pct": 0.06,        # اگر فاصله از مرکز > 6% و recenter=False → همه سفارش‌ها لغو
	"only_buy_mode": False,         # True یعنی فقط لیمیت‌خرید می‌گذاری و بعد در پله بالاتر می‌فروشی

	# سفارش و کارمزد
	"post_only": True,              # اگر صرافی پشتیبانی کند، با کارمزد میکر
	"reduce_only": False,           # برای فیوچرز
	"take_profit_pct": None,        # اگر بخواهی جدا از پله‌ها، روی هر پر شدن TP درصدی بگذاری (مثلاً 0.004 = 0.4%)

	# لاجیک قیمت مرکز
	"center_price_source": "last",  # last / mid / ema
	"ema_len_for_center": 20,       # اگر center_price_source="ema"

	# زمان‌بندی حلقه
	"poll_sec": 5,                  # هر چند ثانیه یکبار بررسی شود

	# لاگ و سیو
	"log_trades": True,
	"persist_state_path": "grid_state.json",
}

