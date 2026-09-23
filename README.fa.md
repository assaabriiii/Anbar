<div dir="rtl">

# انبار (Anbar)

**سازندهٔ کیت توسعهٔ آفلاین.** هر چیزی که پروژه‌تان لازم دارد را تا اینترنت وصل است دانلود کنید و وقتی اینترنت قطع شد، همچنان نصب کنید، بیلد بگیرید و اجرا کنید.

[English](README.md) · [مشارکت](CONTRIBUTING.md) · [تغییرات](CHANGELOG.md) · [امنیت](SECURITY.md)

---

توسعه‌دهنده‌های ایرانی بارها با قطعی اینترنت و فیلترینگ شدید روبه‌رو می‌شوند. در این مواقع PyPI، npm، Docker Hub، Hugging Face و سایت‌های مستندات ممکن است چند روز پشت‌سرهم در دسترس نباشند. «انبار» پروژه را بررسی می‌کند، همهٔ وابستگی‌های آن را در یک پوشهٔ قابل‌حمل به نام **کیت** دانلود می‌کند و بعداً همان کیت را به شکل میرورهای محلی سرو می‌کند. ابزارهای شما (`pip`، `uv`، `npm`، `yarn`، `pnpm`، `docker`، `transformers` و …) طوری کار می‌کنند که انگار اتفاقی نیفتاده است.

کیت یک پوشهٔ معمولی است. می‌شود آن را روی فلش کپی کرد یا در شبکهٔ محلی سرو کرد، و به این ترتیب یک نفر که اینترنت دارد می‌تواند نیاز کل تیم را تأمین کند.

## فهرست

- [امکانات](#امکانات)
- [نصب](#نصب)
- [شروع سریع در ۵ دقیقه](#شروع-سریع-در-۵-دقیقه)
- [دستورها](#دستورها)
- [اکوسیستم‌های پشتیبانی‌شده](#اکوسیستمهای-پشتیبانیشده)
- [مرجع تنظیمات](#مرجع-تنظیمات)
- [میرورهای بالادستی و پراکسی](#میرورهای-بالادستی-و-پراکسی)
- [اشتراک کیت با تیم](#اشتراک-کیت-با-تیم)
- [ساختار کیت](#ساختار-کیت)
- [امنیت](#امنیت)
- [رفع اشکال](#رفع-اشکال)
- [مشارکت](#مشارکت)
- [مجوز](#مجوز)

## امکانات

- **یک دستور برای بررسی و یک دستور برای دانلود.** انبار خودش فایل‌های `requirements*.txt`، `pyproject.toml`، `poetry.lock`، `Pipfile.lock`، `uv.lock`، `package-lock.json`، `yarn.lock`، `pnpm-lock.yaml`، Dockerfileها و فایل‌های compose را پیدا می‌کند.
- **درخت کامل وابستگی‌ها.** وابستگی‌های غیرمستقیم پایتون، همهٔ بسته‌های lock فایل جاوااسکریپت و همهٔ ایمیج‌های پایه (از جمله بیلدهای چندمرحله‌ای و ایمیج‌های `COPY --from`) دانلود می‌شوند.
- **افزایشی، ادامه‌پذیر و موازی.** اجرای دوبارهٔ `pack` فقط موارد تازه را دانلود می‌کند. دانلودهای نیمه‌کاره از همان جایی که قطع شده‌اند ادامه پیدا می‌کنند، درخواست‌های ناموفق دوباره تکرار می‌شوند و هش sha256 یا SRI هر فایل بررسی می‌شود.
- **میرورهای محلی واقعی.** یک ایندکس PEP 503 برای pip و uv، یک رجیستری فقط‌خواندنی npm برای npm و yarn و pnpm، یک فایل‌سرور برای وزن مدل‌ها و مستندات، و در صورت نیاز یک `registry:2` برای داکر در اختیارتان است.
- **تنظیمات برگشت‌پذیر.** `anbar use` ابزارها را تنظیم می‌کند و پیش از هر تغییر، از فایل مربوط نسخهٔ پشتیبان می‌گیرد. `anbar restore` همهٔ فایل‌ها را دقیقاً، بایت به بایت، به حالت قبل برمی‌گرداند.
- **ساخته‌شده برای شبکه‌های فیلترشده.** انبار می‌تواند از میرورهای بالادستی (به ترتیب اولویت) و از پراکسی HTTP یا SOCKS دانلود کند. پیام‌های خطا هم می‌گویند قدم بعدی چیست.
- **کیت برای سیستم‌عامل‌های دیگر.** می‌توانید برای ماشین‌های دیگر هم دانلود کنید؛ مثلاً روی ویندوز توسعه می‌دهید و روی لینوکس دیپلوی می‌کنید.
- **جابه‌جایی آسان.** دستورهای `export` و `import` آرشیوهایی با چک‌سام می‌سازند و بازمی‌کنند که برای فلش و شبکهٔ محلی مناسب‌اند. اگر آرشیو را روی کیتی که از قبل دارید import کنید، فقط فایل‌های تازه اضافه می‌شوند.
- **خود انبار هم آفلاین نصب می‌شود.** هر ریلیز یک فایل zipapp تک‌فایلی و یک بستهٔ wheel دارد که بدون اینترنت نصب می‌شوند.

## نصب

انبار به پایتون ۳٫۱۰ یا جدیدتر نیاز دارد.

</div>

```bash
pipx install anbar                     # پیشنهادی
# یا
pip install --user anbar
```

<div dir="rtl">

قابلیت‌های اختیاری:

</div>

```bash
pipx inject anbar huggingface_hub      # مدل‌های Hugging Face   (pip install "anbar[models]")
pipx inject anbar PySocks              # پراکسی SOCKS           (pip install "anbar[socks]")
```

<div dir="rtl">

برای نصب آخرین نسخهٔ در حال توسعه از سورس:

</div>

```bash
pipx install git+https://github.com/assaabriiii/Anbar.git
```

<div dir="rtl">

### نصب انبار بدون اینترنت

هر [ریلیز در گیت‌هاب](https://github.com/assaabriiii/Anbar/releases) دو فایل دارد که بدون اینترنت نصب می‌شوند. یک نسخه از آن‌ها را روی فلش نگه دارید:

| فایل | روش استفاده |
| --- | --- |
| `anbar-X.Y.Z.pyz` | یک فایل تنهاست؛ فقط اجرایش کنید: `python anbar-X.Y.Z.pyz --help` |
| `anbar-X.Y.Z-offline.zip` | wheelها برای لینوکس، مک و ویندوز (پایتون ۳٫۱۰ تا ۳٫۱۳). از حالت فشرده خارجش کنید و `sh install.sh` یا `powershell -File install.ps1` را اجرا کنید |

## شروع سریع در ۵ دقیقه

روی سیستمی که اینترنت **دارد**:

</div>

```bash
cd my-project

anbar scan                      # ۱. ببینید چه چیزهایی و با چه حجمی دانلود می‌شود
anbar pack --out ~/anbar-kit    # ۲. همه‌چیز را در کیت دانلود کنید
anbar verify ~/anbar-kit        # ۳. اختیاری: هش همهٔ فایل‌ها را دوباره بررسی کنید
```

<div dir="rtl">

بعداً، **بدون** اینترنت (روی همان سیستم یا یک سیستم دیگر):

</div>

```bash
anbar serve ~/anbar-kit         # ۴. میرورهای محلی را روشن کنید (این ترمینال را باز نگه دارید)
```

<div dir="rtl">

در یک ترمینال دیگر:

</div>

```bash
anbar use ~/anbar-kit           # ۵. pip / npm / yarn / pnpm / docker / HF را به کیت وصل کنید
source ~/.anbar/env/anbar.sh    #    فقط برای uv و Hugging Face لازم است (راهنمای چاپ‌شده را ببینید)

pip install -r requirements.txt # آفلاین کار می‌کند
npm ci                          # آفلاین کار می‌کند
docker compose build            # ایمیج‌های پایه در داکر بارگذاری شده‌اند

anbar restore                   # ۶. وقتی اینترنت برگشت، همه‌چیز را دقیقاً به حالت قبل برگردانید
```

<div dir="rtl">

## دستورها

| دستور | کاری که انجام می‌دهد |
| --- | --- |
| `anbar scan [PATH]` | مانیفست‌ها را پیدا می‌کند و نشان می‌دهد `pack` چه چیزهایی را با چه حجم تقریبی دانلود خواهد کرد. `--offline` پرس‌وجو از رجیستری‌ها برای حجم دقیق را کنار می‌گذارد. `--all` همهٔ موارد را فهرست می‌کند. |
| `anbar pack [PATH] --out KIT_DIR` | همه‌چیز را در کیت دانلود می‌کند؛ به‌صورت افزایشی، ادامه‌پذیر، موازی (`-j N`) و با بررسی هش. `--mirror ECO=URL` یک میرور بالادستی اضافه می‌کند و `--refresh` همه‌چیز را دوباره از بالادست بررسی می‌کند. |
| `anbar serve KIT_DIR` | میرورهای محلی را روشن می‌کند. `--host 0.0.0.0` آن‌ها را در شبکهٔ محلی به اشتراک می‌گذارد. پورت‌ها با `--pypi-port`، `--npm-port`، `--files-port` و `--registry-port` تنظیم می‌شوند. |
| `anbar use [KIT_DIR]` | pip، uv، npm، yarn، pnpm، داکر و Hugging Face را به میرورها وصل می‌کند و پیش از هر تغییر از فایل‌ها پشتیبان می‌گیرد. با `--host IP` می‌توانید از `anbar serve` هم‌تیمی‌تان استفاده کنید. |
| `anbar restore` | تغییرات `anbar use` را با استفاده از پشتیبان‌ها دقیقاً برمی‌گرداند. |
| `anbar env [--shell sh\|fish\|powershell\|cmd]` | متغیرهای محیطی‌ای را که `use` تنظیم کرده چاپ می‌کند تا بتوانید `eval "$(anbar env)"` را اجرا کنید. |
| `anbar verify KIT_DIR` | همهٔ فایل‌ها را با sha256 و حجم ثبت‌شده در مانیفست مقایسه می‌کند. `--quick` فقط حجم را مقایسه می‌کند. |
| `anbar status KIT_DIR` | محتوا، حجم و تازگی هر اکوسیستم را نشان می‌دهد. `--stale-days N` تعیین می‌کند یک اکوسیستم بعد از چند روز کهنه به حساب بیاید. |
| `anbar export KIT_DIR --to FILE.tar` | کیت را در یک آرشیو (`.tar`، `.tar.gz` یا `.tar.xz`) می‌نویسد و یک فایل `.sha256` هم کنارش می‌سازد. |
| `anbar import FILE.tar [--to DIR]` | آرشیو را بررسی می‌کند و به‌شکل امن باز می‌کند. اگر `DIR` از قبل کیت باشد، فقط فایل‌های تازه اضافه می‌شوند. |

دستورهای `scan`، `pack`، `serve` و `use` گزینه‌های `--only ECOSYSTEM` و `--skip ECOSYSTEM` را هم می‌پذیرند (قابل تکرار). همهٔ دستورها `--help` دارند.

## اکوسیستم‌های پشتیبانی‌شده

| اکوسیستم | از کجا پیدا می‌شود | چطور ذخیره می‌شود | چطور سرو می‌شود | `anbar use` چه چیزی را تنظیم می‌کند |
| --- | --- | --- | --- | --- |
| **پایتون** | `requirements*.txt` (با `-r`/`-c`)، `pyproject.toml` (PEP 621، dependency groups، Poetry، build requires)، `poetry.lock`، `Pipfile.lock`، `uv.lock` | wheel و sdist (با `pip download` و همراه وابستگی‌های غیرمستقیم) | ایندکس PEP 503 روی `http://127.0.0.1:3141/simple/` | فایل `pip.conf`/`pip.ini` کاربر (`index-url` و `trusted-host`)؛ `UV_DEFAULT_INDEX` برای uv |
| **نود** | `package-lock.json` (نسخهٔ ۱ تا ۳)، `npm-shrinkwrap.json`، `yarn.lock` (classic و Berry)، `pnpm-lock.yaml` (نسخه‌های ۵، ۶ و ۹) | فایل‌های tarball و packumentهایی که فقط نسخه‌های موجود در کیت را نگه می‌دارند | رجیستری فقط‌خواندنی npm روی `http://127.0.0.1:4873/` | `~/.npmrc`، `~/.yarnrc`، `~/.yarnrc.yml` |
| **داکر** | خطوط `FROM` در همهٔ Dockerfileها و Containerfileها (چندمرحله‌ای، مقدار پیش‌فرض `ARG`، `--platform`)، `COPY --from=image`، `image:` در فایل‌های compose (با جایگذاری از `.env`) | خروجی `docker save` | در صورت نیاز یک `registry:2` محلی روی پورت ۵۰۰۰ | `docker load` همهٔ ایمیج‌ها (که `restore` دوباره حذفشان می‌کند) |
| **مدل‌ها** | `[[models.huggingface]]` و `[[models.urls]]` در `anbar.toml` | کش Hugging Face (با ساختار `HF_HOME`) و فایل‌های معمولی | فایل‌سرور روی `http://127.0.0.1:8765/models/` | `HF_HOME`، `HF_HUB_OFFLINE=1`، `TRANSFORMERS_OFFLINE=1` |
| **مستندات** | `[[docs]]` در `anbar.toml` | آرشیو zip یا tar که از حالت فشرده خارج می‌شود | فایل‌سرور روی `http://127.0.0.1:8765/docs/` | – |

lock فایل‌ها اولویت دارند. اگر یک پوشه هم `pyproject.toml` داشته باشد و هم `poetry.lock` (یا `uv.lock`)، نسخه‌های قفل‌شدهٔ lock فایل استفاده می‌شوند.

متغیرهای محیطی شِلی را که در حال اجراست نمی‌شود از بیرون تغییر داد. به همین دلیل `anbar use` آن‌ها را در `~/.anbar/env/anbar.sh` می‌نویسد (نسخه‌های `.fish`، `.ps1` و `.bat` هم ساخته می‌شوند). این فایل را در شل خودتان بارگذاری کنید، یا `eval "$(anbar env)"` را اجرا کنید.

## مرجع تنظیمات

همهٔ تنظیمات اختیاری‌اند. انبار فایل `anbar.toml` را از ریشهٔ پروژه، یا از مسیری که با `--config` داده شده، می‌خواند. `serve` و `use` فایل `anbar.toml` را از پوشهٔ فعلی می‌خوانند، که فقط برای پورت‌های سفارشی لازم است. نمونهٔ کامل و توضیح‌دار در [`anbar.example.toml`](anbar.example.toml) است.

</div>

```toml
ecosystems = ["python", "node", "docker", "models", "docs"]   # پیش‌فرض: همه

[python]
extra = ["gunicorn==23.0.0"]          # بسته‌هایی که در هیچ مانیفستی نیستند
exclude = ["pywin32"]                 # این‌ها هرگز دانلود نشوند
include_build_tools = true            # pip، setuptools و wheel (برای بیلد آفلاین sdistها)
only_binary = false                   # فقط wheel
platforms = ["manylinux2014_x86_64", "win_amd64"]   # با هر python_version ترکیب می‌شود
python_versions = ["3.11", "3.12"]

[node]
extra = ["typescript@5.6.3", "pnpm@latest"]

[docker]
extra = ["redis:7.4-alpine"]
exclude = []
platform = "linux/amd64"
build_args = { PYTHON_VERSION = "3.12" }   # مقدار ARGهایی که در FROM استفاده شده‌اند
registry = false                           # registry:2 را هم نگه دار و سرو کن

[[models.huggingface]]
repo = "sentence-transformers/all-MiniLM-L6-v2"
revision = "main"
allow_patterns = ["*.json", "*.safetensors", "*.txt"]

[[models.urls]]
url = "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt"
name = "yolo"          # زیرپوشهٔ اختیاری
sha256 = "..."         # اختیاری، ولی توصیه می‌شود

[[docs]]
name = "django"
url = "https://example.com/django-docs-5.1-en.zip"

[mirrors]              # برای pack، به ترتیب امتحان می‌شوند
pypi = ["https://mirror.example/simple", "https://pypi.org/simple"]
npm = ["https://npm-mirror.example"]
docker = ["docker-mirror.example", "ghcr.io=ghcr-mirror.example"]
huggingface = ["https://hf-mirror.example"]

[proxy]                # برای pack
http = "http://127.0.0.1:8080"
https = "http://127.0.0.1:8080"
socks = "socks5h://127.0.0.1:1080"
no_proxy = ["localhost", "127.0.0.1"]

[serve]
host = "127.0.0.1"
pypi_port = 3141
npm_port = 4873
files_port = 8765
registry_port = 5000

[network]
workers = 8
retries = 4
timeout = 60
ca_bundle = "/path/to/ca.pem"
```

<div dir="rtl">

| کلید | پیش‌فرض | توضیح |
| --- | --- | --- |
| `ecosystems` | همه | اکوسیستم‌هایی که بررسی می‌شوند. `--only` و `--skip` برای یک اجرا آن را تغییر می‌دهند. |
| `python.extra` / `python.exclude` | `[]` | requirementهای اضافه (با قالب PEP 508) برای دانلود / نام پروژه‌هایی که نباید دانلود شوند. |
| `python.platforms`، `python.python_versions`، `[[python.targets]]` | فقط سیستم فعلی | دانلود wheel برای پلتفرم‌ها و نسخه‌های دیگر پایتون. همهٔ ترکیب‌ها دانلود می‌شوند و برای این هدف‌ها هیچ‌وقت سراغ بیلد از سورس نمی‌رود. |
| `python.include_build_tools` | `true` | `pip`، `setuptools` و `wheel` هم نگه داشته شوند. |
| `python.only_binary` | `false` | برای سیستم فعلی هیچ‌وقت sdist دانلود نشود. |
| `node.extra` | `[]` | به شکل `name@version` یا `name@dist-tag`. وابستگی‌های این موارد حل نمی‌شوند؛ وابستگی‌های واقعی را به `package.json` و lock فایل اضافه کنید. |
| `docker.extra` / `docker.exclude` | `[]` | ایمیج‌های اضافه برای pull / ایمیج‌هایی که کنار گذاشته می‌شوند. |
| `docker.platform` | سیستم فعلی | مقدار پیش‌فرض `--platform` برای pull. خط `FROM --platform=` بر آن اولویت دارد. |
| `docker.build_args` | `{}` | مقدار `ARG`های سراسری که در `FROM` استفاده شده‌اند. برای هر تگی که مقدارش معلوم نشود هشدار داده می‌شود. |
| `docker.registry` | `false` | `registry:2` در کیت نگه داشته شود. در این حالت `anbar serve` آن را اجرا می‌کند و همهٔ ایمیج‌ها را در آن push می‌کند. |
| `models.huggingface[]` | – | `repo`، `revision`، `repo_type`، `allow_patterns`، `ignore_patterns`. |
| `models.urls[]` | – | `url`، `filename`، `name`، `sha256`. |
| `docs[]` | – | `name`، `url`، `sha256`، `extract` (پیش‌فرض `true`). |
| `mirrors.*` | `[]` | میرورهای بالادستی برای `pack` که به ترتیب امتحان می‌شوند. توضیح بیشتر در بخش بعد. |
| `proxy.*` | ندارد | پراکسی برای `pack`. نام کاربری و رمز را در `ANBAR_PROXY_USER` / `ANBAR_PROXY_PASSWORD` بگذارید، نه در این فایل. |
| `serve.*` | بالا را ببینید | آدرس و پورت‌هایی که `serve` روی آن‌ها گوش می‌دهد و `use` به آن‌ها وصل می‌کند. |
| `network.workers` / `retries` / `timeout` | ۸ / ۴ / ۶۰ | تعداد دانلودهای هم‌زمان، دفعات تکرار (با فاصلهٔ نمایی) و مهلت به ثانیه. |
| `network.ca_bundle` | مخزن سیستم | گواهی CA اضافه برای پراکسی یا میرور. گزینه‌ای برای خاموش کردن بررسی TLS وجود ندارد. |

## میرورهای بالادستی و پراکسی

وقتی رجیستری‌های عمومی مسدودند، میرورهای داخل کشور معمولاً هنوز کار می‌کنند. آن‌ها را زیر `[mirrors]` فهرست کنید یا برای یک اجرا با `--mirror` بدهید:

</div>

```bash
anbar pack --out kit \
  --mirror pypi=https://mirror.example/simple \
  --mirror npm=https://npm-mirror.example \
  --mirror docker=docker-mirror.example
```

<div dir="rtl">

میرورها **به ترتیب** امتحان می‌شوند. اگر دانلودی ناموفق باشد یا با چک‌سامش نخواند، سراغ میرور بعدی می‌رود. رجیستری عمومی فقط وقتی استفاده می‌شود که هیچ میروری تنظیم نشده باشد، یا خودتان آن را به فهرست اضافه کرده باشید (بهتر است آخرین مورد فهرست باشد).

- **pypi**: آدرس هر ایندکس ساده (simple) مطابق PEP 503.
- **npm**: آدرس پایهٔ یک رجیستری. آدرس tarballهای lock فایل به میرور تغییر داده می‌شوند.
- **docker**: یک نام میزبان ساده، Docker Hub را میرور می‌کند (`python:3.12` به شکل `HOST/library/python:3.12` دریافت می‌شود و دوباره با نام اصلی تگ می‌خورد). برای رجیستری‌های دیگر از `REGISTRY=HOST` استفاده کنید، مثلاً `ghcr.io=ghcr-mirror.example`.
- **huggingface**: یک endpoint سازگار با API هاب Hugging Face.

از جمله میرورهایی که توسعه‌دهنده‌های ایرانی امروز استفاده می‌کنند: `https://mirror-pypi.runflare.com/simple` (برای PyPI)، `https://mirror-npm.runflare.com` (برای npm) و `docker.arvancloud.ir` (برای Docker Hub). این میرورها را دیگران اداره می‌کنند، پس در دسترس بودن و محتوایشان ممکن است تغییر کند. مستندات خود میرور را ببینید و برای فایل‌های مهم بررسی `sha256` را روشن نگه دارید.

## اشتراک کیت با تیم

یک نفر که اینترنت دارد می‌تواند نیاز بقیه را تأمین کند.

### با فلش یا پوشهٔ اشتراکی

</div>

```bash
# کسی که اینترنت دارد
anbar pack ./project --out team-kit
anbar export team-kit --to /media/usb/team-kit.tar     # team-kit.tar.sha256 هم ساخته می‌شود

# بقیهٔ اعضای تیم (هر دو فایل را کپی کنید)
anbar import /media/usb/team-kit.tar --to ~/team-kit   # sha256 را بررسی و به‌شکل امن باز می‌کند
anbar serve ~/team-kit &
anbar use ~/team-kit
```

<div dir="rtl">

قالب `.tar` از همه سریع‌تر است. `.tar.gz` یا `.tar.xz` فایل کوچک‌تری می‌سازند ولی ساختنشان بیشتر طول می‌کشد. وقتی یک خروجی جدیدتر را روی کیت موجود import کنید، فقط فایل‌های تازه یا تغییرکرده اضافه می‌شوند، پس به‌روزرسانی هفتگی سریع انجام می‌شود.

### در شبکهٔ محلی

یک سیستم کیت را سرو می‌کند و بقیه به آن وصل می‌شوند. هم‌تیمی‌ها به نسخه‌ای از کیت نیاز ندارند:

</div>

```bash
# سیستمی که کیت روی آن است (مثلاً 192.168.1.10)
anbar serve ~/team-kit --host 0.0.0.0

# هر هم‌تیمی
anbar use --host 192.168.1.10          # pip، uv، npm، yarn، pnpm
pip install -r requirements.txt
```

<div dir="rtl">

برای ایمیج‌های داکر در شبکهٔ محلی، پیش از pack مقدار `[docker] registry = true` را تنظیم کنید. در این حالت `anbar serve` کانتینر `registry:2` را از روی کیت اجرا می‌کند و همهٔ ایمیج‌ها را در آن push می‌کند. هم‌تیمی‌ها `"insecure-registries": ["192.168.1.10:5000"]` را به `daemon.json` داکر اضافه می‌کنند و `docker pull 192.168.1.10:5000/python:3.12-slim` را اجرا می‌کنند. مدل‌ها و مستندات روی `http://192.168.1.10:8765/` در دسترس‌اند.

### تازه نگه داشتن کیت

- هر وقت اینترنت داشتید `anbar pack` را دوباره اجرا کنید. فقط موارد تازه دانلود می‌شوند.
- `anbar status KIT` نشان می‌دهد هر بخش کیت چقدر قدیمی است و بخش‌های کهنه را علامت می‌زند.
- `anbar verify KIT` فایل‌هایی را که به خاطر فلش یا کپی شبکه‌ای خراب شده‌اند پیدا می‌کند.

### کیت برای سیستم‌های دیگر

توسعه‌دهنده‌ای که روی ویندوز کار می‌کند و برای سرورهای لینوکسی کیت می‌سازد:

</div>

```toml
[python]
platforms = ["manylinux2014_x86_64", "win_amd64"]
python_versions = ["3.12"]

[docker]
platform = "linux/amd64"
```

<div dir="rtl">

## ساختار کیت

کیت یک پوشهٔ واحد است:

</div>

```text
KIT_DIR/
├── manifest.json          # همهٔ فایل‌ها: مسیر، اکوسیستم، منبع، sha256، حجم، تاریخ دانلود
├── python/packages/       # wheelها و sdistها
├── node/tarballs/         # <name>/-/<name>-<version>.tgz
├── node/packuments/       # متادیتای رجیستری، فقط برای نسخه‌های موجود در کیت
├── docker/images/         # خروجی docker save
├── models/hf/hub/         # کش Hugging Face (HF_HOME=models/hf)
├── models/files/          # وزن‌هایی که از URL دانلود شده‌اند
└── docs/archives/         # آرشیو مستندات (در docs/site/ باز می‌شوند)
```

<div dir="rtl">

فایل `manifest.json` یک `format_version` دارد. نسخه‌های جدید انبار همچنان کیت‌های قدیمی را می‌خوانند و آن‌ها را در حافظه به‌روز می‌کنند. کیتی که با نسخهٔ جدیدتری از انبار ساخته شده باشد، با یک پیام روشن رد می‌شود و اشتباه خوانده نمی‌شود. جزئیات در [docs/kit-format.md](docs/kit-format.md).

## امنیت

- بررسی TLS **همیشه روشن است**. اگر شبکهٔ شما TLS را رهگیری می‌کند، `[network] ca_bundle` را تنظیم کنید. هیچ گزینه‌ای برای خاموش کردن بررسی وجود ندارد.
- **هیچ اطلاعات ورودی (credential) در کیت ذخیره نمی‌شود.** نام کاربری و رمز پراکسی از `ANBAR_PROXY_USER` / `ANBAR_PROXY_PASSWORD` و توکن Hugging Face از `HF_TOKEN` خوانده می‌شوند و انبار هیچ‌کدام را روی دیسک نمی‌نویسد. آدرس ایندکس‌هایی که رمز در خود دارند هرگز از `requirements.txt` برداشته نمی‌شوند.
- هر دانلود با هشی که رجیستری منتشر کرده (هش‌های ایندکس pip، SRI در npm) یا با `sha256` داده‌شده در `anbar.toml` بررسی می‌شود، و sha256 همهٔ فایل‌ها در مانیفست ثبت می‌شود.
- `import` آرشیوهایی را که مسیر مطلق، `..` یا لینکی به بیرون از کیت دارند نمی‌پذیرد.
- میرورهای محلی فقط‌خواندنی‌اند و، مگر اینکه خودتان تغییرش دهید، فقط روی `127.0.0.1` گوش می‌دهند.

برای گزارش آسیب‌پذیری [SECURITY.md](SECURITY.md) را ببینید.

## رفع اشکال

| پیام | راه‌حل |
| --- | --- |
| `PyPI unreachable — try an upstream mirror` | با `--mirror pypi=URL` یا `[mirrors] pypi` یک میرور اضافه کنید، یا `[proxy]` را تنظیم کنید. |
| `npm registry unreachable` | همین کار را با `--mirror npm=URL` انجام دهید. |
| `Docker is installed but the daemon is not reachable` | Docker Desktop را باز کنید یا `sudo systemctl start docker` را اجرا کنید. |
| `cannot resolve ${VAR} in 'FROM …'` | به `ARG` مقدار پیش‌فرض بدهید یا آن را زیر `[docker] build_args` تنظیم کنید. |
| `TLS certificate verification failed` | شبکهٔ شما TLS را رهگیری می‌کند. `[network] ca_bundle` را روی گواهی آن تنظیم کنید. |
| `Anbar is already active` | اول `anbar restore` را اجرا کنید، یا از `anbar use --force` استفاده کنید. |
| `this kit uses format version N` | کیت با نسخهٔ جدیدتری از انبار ساخته شده است. با بستهٔ آفلاین انبار را به‌روز کنید. |
| pip هنوز به اینترنت وصل می‌شود | متغیر `PIP_INDEX_URL` در محیط شما بر `pip.conf` اولویت دارد. آن را حذف کنید. |
| `uv` یا `transformers` کیت را نادیده می‌گیرند | فایل محیطی را که `anbar use` چاپ کرده بارگذاری کنید، یا `eval "$(anbar env)"` را اجرا کنید. |
| yarn 1 از `registry.yarnpkg.com` دانلود می‌کند | `yarn install --registry http://127.0.0.1:4873/` را اجرا کنید، یا نام میزبان را در `yarn.lock` عوض کنید. |

با تنظیم `ANBAR_LOG_REQUESTS=1` همهٔ درخواست‌هایی که به میرورهای محلی می‌رسد ثبت می‌شود.

## مشارکت

از هر نوع مشارکتی استقبال می‌کنیم: کد، گزارش باگ، ترجمه، آزمایش روی شبکه‌های فیلترشدهٔ واقعی، یا فهرست میرورها. لطفاً [CONTRIBUTING.md](CONTRIBUTING.md) را بخوانید. اکوسیستم‌های برنامه‌ریزی‌شده **apt**، **Go modules** و **Maven** هستند و هرکدام یک پلاگین مستقل است.

## مجوز

[MIT](LICENSE) © مشارکت‌کنندگان انبار

</div>
