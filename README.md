# Binance সিগন্যাল-অনলি ফিউচার্স বট

এই প্রকল্পটি Binance USDⓈ-M Futures বাজারের public market data থেকে সম্ভাব্য trade setup শনাক্ত করে। এটি Python-ভিত্তিক একটি গবেষণা ও paper-trading সহায়ক ব্যবস্থা। এর কাজ হলো তথ্য সাজানো, নিয়ম পরীক্ষা করা এবং একটি পর্যালোচনাযোগ্য signal তৈরি করা।

এটি broker নয়, exchange account manager নয়, এবং স্বয়ংক্রিয় trading bot নয়। কোনো command এই software-কে order পাঠানোর অনুমতি দেয় না। এই README-তে যে ফলাফল, signal, report বা উদাহরণ আছে, সেগুলো নির্দেশনামূলক। সেগুলো live performance বা লাভের প্রমাণ নয়।

> প্রথমে `SECURITY.md`, `docs/KNOWN_UNCERTAINTIES.md` এবং `CONFIG_REFERENCE.md` পড়ুন।
> এই প্রকল্প live trading-এর জন্য যাচাইকৃত নয়।

## শুরু করার আগে পড়ুন

### এই বট কী

এটি public Binance market data পড়ে। তারপর পাঁচটি strategy-র নিয়মে candidate খোঁজে। Consensus, risk এবং veto ধাপ candidate-কে যাচাই করে। শর্ত পূরণ হলে বট entry range, SL, TP স্তর, R:R এবং মেয়াদসহ signal তৈরি করতে পারে।

Signal হলো মানুষের পর্যালোচনার জন্য একটি প্রস্তাব। Signal নিজে trade নয়। কোনো signal এলে exchange-এ সিদ্ধান্ত, order এবং তার ঝুঁকি আপনার হাতে থাকে।

### এই বট কী নয়

এটি Binance-এ login করে account balance পড়ে না। এটি live position, actual fill, margin, liquidation distance বা realized P&L দেখে না। এটি market, limit, stop বা অন্য কোনো order তৈরি করে না। এটি leverage সেট, position বন্ধ, order cancel, fund transfer বা withdrawal করতে পারে না।

### signal-only invariant সহজ ভাষায়

“Signal-only” মানে program শুধু market data পড়ে এবং advisory signal তৈরি করে। Exchange-এর trading endpoints বা trading credential এই runtime-এ ব্যবহার করা হয় না। `scan_signal_only.py` source code-এ নিষিদ্ধ capability খোঁজে। Scanner pass করলেও সেটি formal audit নয়; মানুষকেও code ও environment পর্যালোচনা করতে হবে।

### কার জন্য

Python চালাতে পারেন এবং futures-এর ঝুঁকি বোঝেন—এমন operator-দের জন্য এই repo। গবেষকরা synthetic test, local backtest এবং configuration validation করতে পারেন। শিক্ষানবিশরা glossary পড়ে terms বুঝে নিতে পারেন, কিন্তু sample signal দেখে trade করা উচিত নয়।

### কার জন্য নয়

নিশ্চিত লাভ, বিনিয়োগ পরামর্শ বা স্বয়ংক্রিয় execution খুঁজছেন—তাদের জন্য নয়। যারা software যাচাই না করে unattended ভাবে চালাতে চান—তাদের জন্য নয়। জরুরি বা ধার করা অর্থ দিয়ে futures করতে চান—তাদের জন্য নয়।

## ১. প্রজেক্টের উদ্দেশ্য

প্রকল্পের উদ্দেশ্য হলো market data-কে নিয়মতান্ত্রিকভাবে candidate signal-এ রূপ দেওয়া। এতে price bar, order book, trade flow, derivatives এবং configured news source ব্যবহার করার code আছে। প্রতিটি strategy-র শর্ত আলাদা; সব input না থাকলে strategy fail-closed হতে পারে।

Signal-এ symbol, LONG বা SHORT দিক, entry range, SL, TP1–TP4, confidence, grade এবং expiry থাকে। Signal-এ advisory sizing থাকতে পারে, কিন্তু সেটি account-এর তথ্য নয়। Signal-এর ID database ও Telegram-এ একই event চিনতে সাহায্য করে।

মানুষের manual execution দরকার, কারণ program account state বা order fill দেখে না। Exchange-এ spread, slippage, liquidity, margin এবং price দ্রুত বদলাতে পারে। আপনি trade করলে নিজের exchange interface-এ order ও risk আলাদাভাবে যাচাই করবেন।

এই repo-র test synthetic বা deterministic input ব্যবহার করে। Unit test pass মানে market-এ strategy লাভজনক—এমন নয়। Live data, out-of-sample ফল এবং দীর্ঘ paper-soak এখনও আলাদা করে যাচাই করা দরকার।

## ২. কী করতে পারে / কী করতে পারে না

| করতে পারে ✅ | করতে পারে না ❌ |
|---|---|
| Public Binance market-data পড়তে পারে। | Account balance বা private account state পড়তে পারে না। |
| S1–S5 candidate তৈরি করতে পারে। | Exchange-এ order place বা modify করতে পারে না। |
| Consensus, veto এবং advisory risk check চালাতে পারে। | Leverage সেট বা liquidation price যাচাই করতে পারে না। |
| Signal ও manual outcome local SQLite-এ রাখতে পারে। | Fill বা P&L নিজে থেকে শনাক্ত করতে পারে না। |
| Telegram-এ signal/report পাঠাতে পারে, যদি operator সেট করেন। | লাভ নিশ্চিত বা financial advice দিতে পারে না। |
| Local CSV/JSONL replay চালাতে পারে। | Backtest-কে live execution evidence বানাতে পারে না। |
| Testnet public endpoint বেছে নিতে পারে। | Testnet data-কে real liquidity-এর প্রমাণ বলতে পারে না। |

প্রথম column-এর প্রতিটি ক্ষমতার ফলও input ও configuration-এর উপর নির্ভরশীল। “পারে” মানে code path আছে; live-এ সফল হবে—এমন নিশ্চয়তা নয়।

## ৩. সিস্টেম আর্কিটেকচার (সহজ ভাষায়)

প্রধান data path:

```text
Data → Strategies → Consensus → Veto → Risk → Signal → Telegram
```

### Data

REST boot/backfill এবং WebSocket stream থেকে public price, order book, trades ও derivatives আসে। একটি in-memory cache snapshot তৈরি করে; staleness বা missing data হলে guard signal আটকে দিতে পারে। News collector আলাদা করে configured source পড়ে; source URL-গুলোর কিছু এখনও placeholder।

### Strategies

Registry-তে S1, S2, S3, S4 ও S5 আছে। প্রতিটি strategy নিজস্ব price structure, flow বা derivatives শর্তে candidate দেয়। প্রয়োজনীয় history অনুপস্থিত হলে অনুমান করে signal দেওয়ার বদলে candidate না দেওয়াই লক্ষ্য।

### Consensus

Consensus একই symbol ও direction-এর candidate একত্র করে। Configured group vote, confidence ও grade boundary থেকে A+, A বা B grade নির্ধারণ হয়। এই boundary deterministic হলেও market quality অনুযায়ী calibrate করা হয়েছে—এমন প্রমাণ নেই।

### Veto

Veto ধাপ feed, liquidity, news, risk বা অন্যান্য configured guard পরীক্ষা করে। BLOCK action হলে সেই candidate final signal বা Telegram delivery-তে যায় না। কিছু guard degrade বা warning দিতে পারে; সব warning একই ধরনের hard block নয়।

### Risk

Risk engine advisory units/notional, daily limits, cooldown ও exposure checks করে। এটি শুধু signal-এর গাণিতিক পরামর্শ; exchange-এ margin বা leverage বাস্তবে যাচাই হয় না। Paper assumptions আর actual sizing config এক জিনিস নয়—পরের অংশ পড়ুন।

### Signal

সফল candidate থেকে human-readable signal model তৈরি হয়। Signal-এর warning, lifecycle এবং unique ID persistence-এ রাখা হয়। Signal তৈরি হওয়া execution বা fill-এর সমান নয়।

### Telegram

Sender message rate-limit queue ব্যবহার করে। Default `TELEGRAM_DRY_RUN=true`, তাই network delivery বন্ধ থাকে। Dry-run output Telegram পৌঁছানোর প্রমাণ নয়।

## ৪. ইনস্টলেশন

প্রয়োজন Python 3.11 বা তার পরের সংস্করণ। Linux/macOS shell-এ নিচের command-গুলো repository root থেকে চালান। Windows-এ virtual-environment activation command আপনার shell অনুযায়ী বদলাতে পারে।

```bash
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

তারপর config, test এবং static safety checks চালান:

```bash
python scripts/validate_config.py
pytest -q
python -m compileall app/
python scripts/scan_signal_only.py
python scripts/scan_no_placeholders.py
python scripts/scan_forbidden_calls.py
```

এই command-গুলো স্থানীয় test ও configuration যাচাই করে। এগুলো Binance, Telegram, news feed বা deployed host যাচাই করে না। কোনো test failure থাকলে failure লুকাতে `skip` বা `xfail` যোগ করবেন না।

`requirements.txt`-এর dependency install করতে internet লাগতে পারে। একই project dependency-র আলাদা version ব্যবহার করলে ফল বদলাতে পারে।

## ৫. কনফিগারেশন

`config/`-এ ছয়টি YAML ফাইল আছে:

| ফাইল | কাজ |
|---|---|
| `top20_pairs.yaml` | Symbol universe ও precision metadata। |
| `system.yaml` | Mode, public endpoint, REST/Telegram limits, DB path ও boot policy। |
| `strategy.yaml` | S1–S5 strategy parameter ও consensus। |
| `veto.yaml` | Guard thresholds ও action। |
| `news_sources.yaml` | News source, credibility এবং category configuration। |
| `risk.yaml` | Risk policy, symbol tier, cluster ও paper assumption। |

### Paper assumptions

`risk.yaml`-এর paper-report block-এ capital ₹200,000 এবং আনুমানিক $2,400 লেখা আছে। USD সংখ্যাটি আনুমানিক conversion, account থেকে পড়া নয়। Block-এ প্রতি assumed R-এর জন্য ₹5,000 এবং paper হিসাবের জন্য 2.5% উল্লেখ আছে। 10x leverage operator-এর assumption; bot এটি exchange-এ সেট করে না।

গুরুত্বপূর্ণ পার্থক্য: top-level `risk_per_trade_pct` **0.5%**, এবং তা অপরিবর্তিত। Signal sizing `ASSUMED_ACCOUNT_EQUITY_USD` environment input ব্যবহার করে। Report-এর ₹5,000/R conversion এই sizing-এর সমান বলে program নিশ্চিত করে না। তাই report-এর assumed P&L-কে actual exposure বা account P&L ভাববেন না।

### Environment variables

`.env.example` কেবল উদাহরণ। এই app নিজে `.env` file পড়ে—এমন নিশ্চয়তা নেই; shell বা process manager-এ variable export করুন। `.env` local রাখুন এবং Git-এ commit করবেন না। উদাহরণ file কপি করে শুধু নিজের machine-এ environment-এ load করুন:

```bash
cp .env.example .env
chmod 600 .env
set -a
. ./.env
set +a
```

এই repository `.env` file স্বয়ংক্রিয়ভাবে load করে না; তাই run করার shell-এ variable export হয়েছে কি না যাচাই করুন।

| Variable | অর্থ |
|---|---|
| `ASSUMED_ACCOUNT_EQUITY_USD` | Positive assumed equity; উদাহরণ `2400`, Binance balance নয়। |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token; গোপন রাখুন। |
| `TELEGRAM_CHAT_ID` | Signal ও daily report-এর destination। |
| `TELEGRAM_ERROR_CHAT_ID` | Error notification-এর পৃথক destination। |
| `TELEGRAM_DRY_RUN` | `true` হলে Telegram network send বন্ধ। |
| `LOG_LEVEL` / `LOG_JSON` | Sample logging preferences; deployed logging behavior যাচাই করুন। |

Live Telegram delivery চাইলে operator-কে token ও chat ID নিজে নিরাপদভাবে configure করতে হবে। `TELEGRAM_DRY_RUN=false` হলে `TELEGRAM_ERROR_CHAT_ID`-ও প্রয়োজন। Telegram token কোনো message বা public repository-তে দেবেন না।

### Mainnet ও testnet

Default `config/system.yaml`-এ `binance_env: "mainnet"`। `testnet` বেছে নিলে public testnet REST/WebSocket host ব্যবহৃত হয়। Boot-এ sparse data warning আসে; testnet signal generation-এর জন্য নয়। Program নিজে থেকে mainnet/testnet বদলায় না।

`class: E` বা `class: F` হলো config-এর classification label। এগুলো calibrated market probability বা স্বাধীন পরীক্ষার ফল নয়। সংশ্লিষ্ট threshold অনুমোদন ছাড়া পরিবর্তন করবেন না।

## ৬. চালানোর নিয়ম

প্রথমে offline checks pass করান। Runtime command public network ব্যবহার করবে; এটি offline smoke command নয়।

```bash
python -m app.main
```

Startup-এ config, assumed equity ও named trading-credential guard পরীক্ষা হয়। এরপর public `exchangeInfo`, WebSocket readiness, news এবং evaluation শুরু হয়। Bot অন্তত 15টি configured symbol-এর ready snapshot চায়, নইলে bounded wait শেষে থামে।

Telegram default dry-run; dry-run মানে signal pipeline চলতে পারে কিন্তু message পাঠানো হয় না। Telegram live করতে operator-কে `TELEGRAM_DRY_RUN=false`, token, signal chat এবং error chat configure করতে হবে। এই পরিবর্তনের আগে destination যাচাই করুন।

### Offline tools

```bash
ASSUMED_ACCOUNT_EQUITY_USD=2400 python scripts/smoke_test.py
python scripts/healthcheck.py
python scripts/soak_test.py
python scripts/canary.py
```

`smoke_test.py` synthetic data ব্যবহার করে; এটি network-free pipeline check। `healthcheck.py` standalone ভাবে live feeds/news/queue পায় না, তাই health প্রায়ই `unknown`। `soak_test.py` নিজস্ব process observe করে; এটি bot চালায় না এবং 72 ঘণ্টা পূর্ণ না হলে soak completion নয়। `canary.py` synthetic offline flow চালায়, external delivery বন্ধ রাখে।

Dockerfile ও Compose বর্তমানে config validation-only; এগুলো `app.main` চালায় না।

## ৭. সিগন্যাল বোঝা

নিচের sample formatter test fixture থেকে হুবহু; এটি market recommendation নয়।

```text
🚨 SIGNAL | BTCUSDT — LONG
⚠️ ADVISORY ONLY — VERIFY ACCOUNT SIZING MANUALLY. No account state, fill, leverage, or liquidation distance is observed.
⚠️ LEVERAGE NOT SET BY BOT. Set leverage yourself on the exchange.
🧠 Grade: A   📊 Confidence: 84%
🎯 LIMIT ENTRY: 86120 – 86180
🛑 SL: 85620
🎯 TP1: 86680   🎯 TP2: 87240
🎯 TP3: 88020   🎯 TP4: 88980
📐 R:R: 1 : 2.1   ⏳ Expiry: 45m
📰 News: no blocking event   🏦 Binance: healthy
🛡️ Veto: PASS
ID: CSB-20260926-4F1A2C
```

- `BTCUSDT` হলো Binance USDⓈ-M Futures-এর symbol label।
- `LONG` মানে price বাড়ার দিকের setup; `SHORT` মানে কমার দিকের setup।
- `Grade` হলো configured consensus শ্রেণি, গুণমানের নিশ্চয়তা নয়।
- `Confidence` হলো model-এর internal score; win probability হিসেবে পড়বেন না।
- `LIMIT ENTRY` হলো প্রস্তাবিত entry-price range; exchange-এ order দেওয়া হয়নি।
- `SL` হলো Stop Loss স্তর; operator-কে exchange-এ নিজে যাচাই করে বসাতে হয়।
- `TP1` থেকে `TP4` হলো ক্রমানুসারে সম্ভাব্য Take Profit স্তর।
- `R:R` হলো প্রস্তাবিত risk বনাম reward অনুপাতের হিসাব।
- `Expiry` পার হলে পুরোনো signal-কে নতুন signal ভাববেন না।
- `News` ও `Binance` label বর্তমান subsystem-এর অবস্থা বোঝায়, গ্যারান্টি নয়।
- `Veto: PASS` মানে configured guard-গুলো এই evaluation-এ block করেনি; safety guarantee নয়।
- `ID` database/report-এ একই signal খুঁজতে ব্যবহৃত হয়।
- দুইটি ⚠️ line প্রত্যেক signal-এ থাকে; leverage program সেট করে না।

## ৮. Telegram সেটআপ

Telegram bot তৈরির জন্য Telegram app-এ verified `@BotFather` খুঁজে নিন। `/newbot` command দিয়ে bot তৈরি করুন এবং Telegram যে token দেয় তা গোপন রাখুন। যে chat বা group-এ signal চান, bot-কে সেই chat-এ যুক্ত করুন।

`chat ID` হলো Telegram destination-এর numeric বা group identifier। নিরাপদ পদ্ধতিতে আপনার নিজস্ব bot update বা পরিচিত tooling থেকে এটি নির্ধারণ করুন। নিজের bot-কে message পাঠিয়ে local shell-এ Telegram-এর official `getUpdates` response দেখে `chat.id` নিতে পারেন; response-এ ব্যক্তিগত message থাকতে পারে, তাই সেটি public-এ paste করবেন না। Token কোনো public “get chat id” website-এ দেবেন না।

স্থানীয় environment-এ নিচের variable রাখুন:

```bash
export TELEGRAM_BOT_TOKEN="<আপনার-গোপন-token>"
export TELEGRAM_CHAT_ID="<signal-chat-id>"
export TELEGRAM_ERROR_CHAT_ID="<পৃথক-error-chat-id>"
export TELEGRAM_DRY_RUN=true
```

প্রথমে `true` রেখেই pipeline ও formatter যাচাই করুন। এই অবস্থায় Telegram API call হওয়ার কথা নয়। Live send ইচ্ছাকৃতভাবে চালু করলে `TELEGRAM_DRY_RUN=false` দিন এবং সব chat ID পুনরায় যাচাই করুন। Live mode-এর error notification signal chat থেকে আলাদা destination-এ যায়।

কোনো token বা chat ID এই README, issue, log বা Git commit-এ লিখবেন না। Telegram delivery failure bot-এর market-data loop বন্ধ না করার জন্য fail-soft রাখা হয়েছে। Live delivery test এখানে সম্পন্ন হয়েছে—এমন দাবি করা হচ্ছে না।

## ৯. Paper Trading Flow

Paper trading হলো বাস্তব অর্থে order না দিয়ে trade idea-র ফল হাতে নথিবদ্ধ করা। Signal এলে symbol, direction, entry range, SL, TP, expiry, news label এবং veto নিজে যাচাই করুন। Signal দেরিতে এসেছে কি না এবং market liquidity যথেষ্ট কি না দেখুন।

Trade নেবেন কি না—operator নিজে সিদ্ধান্ত নেয়। Trade নিলে exchange-এ entry, SL, TP এবং leverage আলাদাভাবে manually সেট করুন। এই software order পাঠায় না, leverage সেট করে না এবং fill যাচাই করে না।

Outcome কেবল manual reconciliation শেষে লিখুন:

```bash
python scripts/record_outcome.py \
  --signal-id CSB-20261008-CCCDDD \
  --realized-r 1.25 \
  --note "operator reconciled after fees" \
  --db data/signals.db
```

বিদ্যমান signal ID লাগবে; একটি signal-এর জন্য একটি manual outcome রাখা যায়। `realized-r`-এ positive সংখ্যা লাভ এবং negative সংখ্যা ক্ষতি বোঝায়। ভুলে গেলে outcome বানিয়ে দেবেন না; report outcome না থাকলে সেটি স্পষ্ট করে।

Outcome record জরুরি, কারণ cumulative win rate, average R ও profit factor এই manual rows থেকেই আসে। কোনো fill exchange থেকে স্বয়ংক্রিয়ভাবে সংগ্রহ করা হয় না। Paper journal ব্যক্তিগত হিসাবের বিকল্প নয়; নিজের source-of-truth ledger মিলিয়ে নিন।

## ১০. Daily Report বোঝা (23:59 IST)

Bot-এর background task প্রতিদিন 23:59 Asia/Kolkata-তে local DB থেকে report গড়ে। Report-এ signal count, grade/strategy/symbol, blocked guard, log করা error এবং manual outcome থাকে। Report তৈরির task আলাদা asyncio task; এটি evaluation loop-কে অপেক্ষা করানোর জন্য নয়।

Manual preview:

```bash
python scripts/daily_report.py --dry-run
```

Preview Telegram-এ পাঠায় না। ইচ্ছা করে manual send করতে `--send` লাগে; live mode-এ Telegram environment না থাকলে script থামে। DB sample-এর জন্য `--db PATH` ব্যবহার করা যায়।

নিচের sample-টি কেবল format বোঝানোর জন্য; এটি এই মুহূর্তের database output নয়:

```text
📊 DAILY REPORT — 2026-10-08 (IST)
━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 Signals emitted: 2
   ├ A+ : 0
   ├ A  : 1
   └ B  : 1
🎯 By strategy: S1=0, S2=0, S3=1, S4=0, S5=1
🔎 By symbol: BTCUSDT=1, ETHUSDT=1
🛡️ Vetoes blocked: 2
   ├ By guard: G1=0, G2=2, G3=0, G4=0, G5=0, G6=0, G7=0, G8=0, G9=0, G10=0, G11=0, G12=0, G13=0, G14=0, G15=0
   └ Top: G2 (2), None (0)
❌ Errors: 1
   └ By severity: ERROR=1, CRITICAL=0
📝 Outcomes recorded: 2
   ├ Wins:  1
   ├ Losses: 1
   ├ Flats: 0
   └ Realized R sum: +1.00
💹 Paper P&L (assumed):
   └ +1.00R × ₹5000 = +₹5,000
🎯 Cumulative (paper):
   ├ Win rate: 50.0%
   ├ Avg R:    +0.50
   └ Profit factor: 6.00
⚠️ Assumptions: ₹200000 capital, ₹5000/trade, 10x leverage
   (operator-set; bot does not execute)
ID: DR-20261008
```

`Signals emitted` হলো DB-তে ঐ IST day-তে লেখা signal। `By strategy` ও `By symbol` signal-এর grouping দেখায়। `Vetoes blocked` কেবল persistence-এ থাকা actual blocking guard event গোনে। `Errors` ERROR/CRITICAL log event-কে severity অনুযায়ী গোনে। `Outcomes recorded` operator-প্রবেশ করানো ফল; no outcome থাকলে report বলে “No outcomes recorded yet.” `Realized R sum` হলো ঐ দিনের manual outcome-গুলোর যোগফল। `Paper P&L (assumed)` ঐ R-কে ₹5,000/R দিয়ে গুণ করে, actual account P&L নয়। `Cumulative` সব সংরক্ষিত manual outcome থেকে হিসাব হয়; sample ছোট হলে metrics অর্থবহ নাও হতে পারে।

**গুরুত্বপূর্ণ mismatch:** report ₹5,000/R ধরে, কিন্তু actual advisory sizing config-এ top-level risk percentage 0.5% এবং equity environment-নির্ভর। এই দুই হিসাব software এক করে না। তাই report-এর INR P&L-কে live loss বা account return হিসেবে ব্যবহার করবেন না।

## ১১. Error Notification বোঝা

Error notifier হলো signal message থেকে পৃথক logging-to-Telegram path। Notifier শুরু হওয়ার পরে ERROR বা CRITICAL record database-এ জমা হয়। Telegram notice সর্বোচ্চ প্রতি process-এ প্রতি পাঁচ মিনিটে একবার চেষ্টা করা হয়। Throttling state restart-এর পরে reset হয়।

নিচেরটি illustrative sample, actual incident নয়:

```text
🚨 ERROR | 2026-10-08 17:30:00 IST
━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Source: app.main
❌ Type: RuntimeError
💬 Message: startup failed
📎 Context: symbol=BTCUSDT, strategy=S1
🔍 Frames:
   app/main.py:118 in run_application
   app/bot.py:400 in evaluate_symbol
   asyncio/tasks.py:500 in wait_for
⏱️ Rate limit: 1 error / 5 min
ID: ERR-20261008-173000
```

Error এলে প্রথমে Source, Type, Message, Context ও Frames পড়ুন। তারপর timestamp ও error ID দিয়ে stdout/stderr বা operator-managed log destination মিলিয়ে দেখুন। `data/signals.db` হলো configured SQLite journal-এর default অবস্থান। এই repository নিজে rotating log file তৈরি বা log retention পরিচালনা করে না।

Database বা Telegram notification ব্যর্থ হলে notifier stderr-এ সংক্ষিপ্ত notice লেখে এবং exception main loop-এ ছড়ায় না। Config/equity/trading-credential guard-এর আগের boot error এই notifier capture করে না। Queue পূর্ণ হলে event হারাতে পারে; operator-এর durable logging ব্যবস্থা আলাদা দরকার।

## ১২. Backtest চালানো

### CSV replay

```bash
python scripts/run_backtest.py --csv ./bars.csv --symbol BTCUSDT
python scripts/run_walkforward.py --csv ./bars.csv --symbol BTCUSDT
```

CSV header: `ts_ms,open,high,low,close,volume`; row oldest-first হতে হবে। CSV path-এ শুধু OHLCV থাকে; order-book, taker flow ও derivatives নেই। তাই missing context-নির্ভর strategy fail-closed করে candidate শূন্য দিতে পারে। CSV accept gate pass মানে live profitability প্রমাণ নয়।

`--csv` বাদ দিলে দুই runner-ই `NOT RUN` দেখিয়ে exit code `3` দেয়। এটি data না থাকায় run হয়নি—কোনো test pass বা fail-এর বদলি নয়। অপেক্ষাকৃত কম row, ভুল header বা duplicate timestamp থাকলেও replay থামতে পারে।

### JSONL replay

```bash
python scripts/run_backtest_jsonl.py \
  --jsonl ./replay.jsonl \
  --symbol BTCUSDT \
  --assumed-equity-usd 2400
```

JSONL harness-এ bars, orderbook snapshot, taker flow এবং funding/OI/long-short derivative series রাখা যায়। আলাদা timeframe bar-ও সরবরাহ করা যায়। Input schema `app/backtest/harness.py`-এর loader অনুসারে বানাতে হবে। Runner local file পড়ে; live data download বা exchange call করে না।

JSONL runner-এ `--jsonl` না দিলে `NOT RUN` ও exit `3`। Malformed JSONL বা replay error হলে এটিও fail-closed `NOT RUN` দেয়। Latency cost ও deterministic bar-level fill model ব্যবহার হলেও queue position, partial fills বা intra-bar path পুরোপুরি পুনর্গঠন হয় না। CSV ও JSONL—কোনোটিই actual fill নয়।

## ১৩. নিরাপত্তা

এই codebase-এ exchange trading API key প্রয়োজন নেই। Runtime boot `BINANCE_API_KEY` বা `BINANCE_API_SECRET` পরিবেশে পেলে থামার কথা। Binance connector কেবল public market-data path-এর জন্য ব্যবহৃত হয়।

`scan_signal_only.py` forbidden trading capability-এর source search করে। তারপর `scan_forbidden_calls.py` এবং `scan_no_placeholders.py` চালান। Static scanner-কে একমাত্র security control ভাববেন না। Dependency, deployment, environment এবং code diff-ও review করুন।

Telegram token secret; `.env` Git-এ যোগ করবেন না। Public issue বা chat-এ token, account detail বা private information দেবেন না। `TELEGRAM_ERROR_CHAT_ID` আলাদা রাখতে পারেন, যাতে operational errors signal chat-এ না যায়।

Container files বর্তমানে validation-only; read-only filesystem বা dropped capability-এর অর্থ live runtime hardening সম্পন্ন—এমন নয়। Durable storage, backup, access control, log retention এবং host supervision operator-কে আলাদা পরিকল্পনা করতে হবে।

## ১৪. সীমাবদ্ধতা

- Live Binance connection এবং live Telegram delivery এখানে **NOT TESTED**।
- Out-of-sample performance **NOT VERIFIED**।
- পূর্ণ 72-hour paper-soak **NOT TESTED**।
- Class E/F thresholds calibrated—এমন প্রমাণ নেই।
- News config-এ literal placeholder URL আছে; source availability **NOT VERIFIED**।
- Testnet market data sparse হতে পারে; testnet signal generation-এর জন্য নয়।
- Paper outcome operator-এন্ট্রি; exchange fill থেকে স্বয়ংক্রিয় ফল নয়।
- Daily report-এর ₹5,000/R conversion advisory sizing-এর সাথে মেলানো হয় না।
- Background task downtime-এর report পরে নিজে replay করে না।
- Error notification limiter memory-তে থাকে এবং restart-এ reset হয়।
- Queue overflow বা database failure হলে সব error event সংরক্ষণ নিশ্চিত নয়।
- Backtest fill, latency এবং slippage model বাস্তব market microstructure নয়।
- `docs/KNOWN_UNCERTAINTIES.md`-এ বাকি evidence ও caveat দেখুন।

এই তালিকা প্রযুক্তিগত সীমাবদ্ধতা; এটি কোনো risk-free ব্যবহারের অনুমতি নয়।

## ১৫. ট্রাবলশুটিং

### Config validation ব্যর্থ

```bash
python scripts/validate_config.py
```

সুনির্দিষ্ট YAML error দেখুন এবং key-এর type/indentation ঠিক করুন। Threshold কমিয়ে বা validator বন্ধ করে run করাবেন না।

### Assumed equity অনুপস্থিত

`ASSUMED_ACCOUNT_EQUITY_USD` shell-এ export হয়েছে কি না দেখুন। Value finite ও positive হওয়া চাই। এটি Binance account balance নয়।

### Startup-এ symbol reject

Public `exchangeInfo` filters ও local symbol list তুলনা হয়। Rejected symbol reason log-এ দেখুন। Missing precision filter-কে অনুমান করে পূরণ করবেন না।

### Snapshot ready হয় না

Network, WebSocket connection, source staleness এবং per-symbol backfill log দেখুন। Timeout বাড়িয়ে guard পাশ কাটাবেন না। যে input stale বা missing, সেটি verified না হওয়া পর্যন্ত signal নেওয়া বন্ধ রাখুন।

### Telegram message আসে না

আগে `TELEGRAM_DRY_RUN` সত্যি কি না দেখুন। তারপর bot token, signal `TELEGRAM_CHAT_ID`, error `TELEGRAM_ERROR_CHAT_ID`, chat permission এবং outbound network যাচাই করুন। Dry-run test কোনো actual Telegram send পরীক্ষা করে না।

### Daily report-এ outcome নেই

এটি `No outcomes recorded yet.` দেখালে কোনো manual outcome row নেই। Signal ID যাচাই করে `scripts/record_outcome.py` চালান। অজানা signal ID, duplicate outcome বা non-finite R গ্রহণ করা হয় না।

### Healthcheck unknown

Standalone `healthcheck.py` live feed/news/queue state পায় না। Unknown output থেকে live service healthy বা unhealthy—কোনোটিই সিদ্ধান্ত নেবেন না।

### Backtest `NOT RUN`

Input flag, path এবং schema পরীক্ষা করুন। CSV-তে required OHLCV column, JSONL-এ harness schema লাগবে। Exit `3` মানে replay হয়নি।

## ১৬. ট্রেডিং পরিভাষা

| English term | বাংলা ব্যাখ্যা |
|---|---|
| Long | দাম বাড়লে লাভের প্রত্যাশায় নেওয়া দিক। |
| Short | দাম কমলে লাভের প্রত্যাশায় নেওয়া দিক। |
| Entry | trade শুরু করার পরিকল্পিত price। |
| Entry range | যে price-band-এ signal entry বিবেচনা করে। |
| Stop Loss / SL | ক্ষতি সীমিত করার জন্য নির্ধারিত exit price। |
| Take Profit / TP | পরিকল্পিত লাভ নেওয়ার exit level। |
| R | একটি trade-এর পূর্বনির্ধারিত risk-কে একক ধরা। |
| R:R | সম্ভাব্য reward ও risk-এর অনুপাত। |
| Expiry | signal কতক্ষণ নতুন হিসেবে বিবেচ্য থাকবে। |
| Veto | candidate-কে block বা degrade করার risk guard। |
| Funding rate | Futures contract-এর long/short-এর মধ্যে নির্দিষ্ট বিরতিতে settlement rate। |
| Open interest | এখনও বন্ধ না হওয়া Futures contract-এর মোট পরিমাণের একটি মাপ। |
| Basis | Futures price ও reference/spot price-এর ব্যবধান। |
| Leverage | কম collateral দিয়ে বড় notional exposure নেওয়ার অনুপাত। |
| Liquidation | margin অপর্যাপ্ত হলে exchange-এর জোরপূর্বক position বন্ধ করার প্রক্রিয়া। |
| Market order | তখনকার উপলভ্য price-এ দ্রুত fill চাওয়া order। |
| Limit order | নির্দিষ্ট বা ভালো price ছাড়া fill না করার শর্তযুক্ত order। |
| Maker fee | order book-এ liquidity যোগ করা order-এর fee category। |
| Taker fee | বিদ্যমান liquidity গ্রহণ করা order-এর fee category। |
| Slippage | প্রত্যাশিত price ও বাস্তব fill price-এর পার্থক্য। |
| Spread | best bid ও best ask-এর ব্যবধান। |
| ATR | Average True Range; সাম্প্রতিক price range-এর volatility estimate। |
| EMA | Exponential Moving Average; সাম্প্রতিক price-কে বেশি weight দেওয়া average। |
| RSI | Relative Strength Index; momentum-এর একটি bounded indicator। |
| Drawdown | equity curve বা cumulative ফলের আগের উচ্চতা থেকে পতন। |
| Win rate | নথিবদ্ধ outcome-এর মধ্যে লাভজনক outcome-এর অনুপাত। |
| Profit factor | মোট gross win R ভাগ মোট gross loss R-এর absolute মান। |
| Expectancy | প্রতি trade-এ গড় প্রত্যাশিত ফল; নির্ভরযোগ্য sample দরকার। |
| Consensus | একাধিক strategy vote মিলিয়ে grade দেওয়ার নিয়ম। |
| Order book | buy bid ও sell ask limit liquidity-এর তালিকা। |
| Taker flow | বাজারে আক্রমণাত্মক buy/sell trade-এর পরিমাপ। |
| OOS | Out-of-sample; model তৈরির বাইরে রাখা data-তে মূল্যায়ন। |
| Paper trading | বাস্তব order না দিয়ে hypothetical trade নথিবদ্ধ করা। |

উপরের সংজ্ঞাগুলো সাধারণ শিক্ষামূলক ব্যাখ্যা; exchange-specific rule আলাদা হতে পারে।

## ১৭. FAQ (Bengali)

### ১. বট কি নিজে trade করে?
না। এটি signal-only; order placement বা leverage setting নেই।

### ২. Signal দেখলেই কি trade নিতে হবে?
না। Signal শুধু advisory output; মানুষকে যাচাই ও সিদ্ধান্ত নিতে হয়।

### ৩. Dry-run কি live market data বন্ধ রাখে?
না। `TELEGRAM_DRY_RUN=true` Telegram send বন্ধ করে; runtime এখনও public data চাইতে পারে।

### ৪. `ASSUMED_ACCOUNT_EQUITY_USD` কি আমার exchange balance?
না। এটি operator-দেওয়া assumption; account endpoint থেকে পড়া হয় না।

### ৫. Daily report কি আমার প্রকৃত P&L দেখায়?
না। এটি manually recorded R-কে ₹5,000/R assumption দিয়ে হিসাব করে।

### ৬. Outcome কীভাবে লিখব?
বিদ্যমান signal ID ও operator-calculated R দিয়ে `scripts/record_outcome.py` ব্যবহার করুন।

### ৭. Bot কি stop loss exchange-এ বসায়?
না। Signal-এ SL প্রস্তাব থাকে; exchange-এ manual action operator-এর কাজ।

### ৮. Testnet কি real strategy যাচাই করে?
না। Testnet plumbing test-এর জন্য; liquidity ও signal quality mainnet-এর সমান নয়।

### ৯. CSV backtest-এ candidate না এলে কী করব?
CSV-তে শুধু OHLCV থাকে; strategy-র auxiliary input প্রয়োজন হতে পারে। JSONL harness পূর্ণ context দিতে পারে।

### ১০. `NOT RUN` কি test pass?
না। Exit code `3` মানে input না থাকায় replay চালানো হয়নি।

### ১১. Healthcheck healthy না বললে কী বুঝব?
Standalone healthcheck live feed/news/queue পায় না; `unknown` স্বাভাবিক হতে পারে।

### ১২. Telegram error অন্য chat-এ পাঠানো যায়?
হ্যাঁ। `TELEGRAM_ERROR_CHAT_ID` signal chat থেকে আলাদা রাখুন এবং live mode-এর আগে যাচাই করুন।

### ১৩. Error notifier কি প্রতিটি event সঙ্গে সঙ্গে পাঠায়?
না। Database-এ persist করার চেষ্টা প্রতিটি ERROR/CRITICAL record-এর জন্য হয়; notification সর্বোচ্চ প্রতি পাঁচ মিনিটে একবার।

### ১৪. 72 ঘণ্টার soak কি এখানে সম্পন্ন?
না। Offline soak observer live bot-এর বদলি নয়; পূর্ণ live paper-soak **NOT TESTED**।

### ১৫. Test pass হলে কি production-এ চালাতে পারি?
না। Test pass কেবল নির্দিষ্ট code path-এর evidence; live validation, monitoring ও operator review এখনও দরকার।

## ১৮. Disclaimer

এই software, README, signal, report, backtest ও glossary কোনো financial advice নয়। এগুলো trade recommendation, guaranteed return, risk-free strategy বা personalized investment guidance নয়। Crypto Futures-এ দ্রুত ও সম্পূর্ণ অর্থহানির ঝুঁকি থাকে। Leverage ক্ষতি বাড়াতে পারে; liquidation-এ collateral হারাতে পারেন।

শুধু নিজের গবেষণা, নিজের সিদ্ধান্ত এবং নিজের ঝুঁকিতে ব্যবহার করুন। আপনার স্থানীয় আইন, exchange-এর শর্ত এবং tax obligation নিজে যাচাই করুন। Software-কে এমনভাবে চালাবেন না যাতে অন্যের অর্থ বা আপনার জরুরি অর্থ ঝুঁকিতে পড়ে। কোনো outcome, report বা test pass-কে লাভের প্রতিশ্রুতি হিসেবে ব্যাখ্যা করবেন না।

কোডের current state, known limitation এবং exact validation record দেখতে `docs/KNOWN_UNCERTAINTIES.md`, `docs/DEPLOYMENT.md`, `docs/TROUBLESHOOTING.md` এবং repository tests পড়ুন। এই project-এর live Binance connection, out-of-sample ফল এবং পূর্ণ paper-soak **NOT TESTED / NOT VERIFIED**।
