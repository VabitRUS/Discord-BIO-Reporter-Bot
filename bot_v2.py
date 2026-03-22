import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
from datetime import datetime, timedelta, timezone

# ─────────────────────────────────────────
#  НАСТРОЙКИ — измени под себя
# ─────────────────────────────────────────
TOKEN = "MTQ4NTMwNDYyMjcwMzk3MjQxNQ.G9FEt-.vGqlZZO5hvA4PU_SxQWUP8fHM968xoG4vGbgek" # Токен Бота Discord
REPORT_CHANNEL_ID   = 1288901000010137611   # канал где люди пишут отчёты
ANNOUNCE_CHANNEL_ID = 1288901000010137611   # канал куда постятся итоги
PING_ROLE_ID        = 1288901873503436872   # @Подразделение BIO — пинг при объявлении

# ID ролей — ПКМ на роль → Копировать ID
ROLE_DIVISION       = 1288901873503436872   # @[☣] Подразделение BIO  — кого считаем #Проверяем ID роли: 
ROLE_IGNORE_1       = 1288901975693594624  # @[☣] Адъютант BIO       — игнорируем
ROLE_IGNORE_2       = 1288902012359933952  # @[☣] Командир BIO       — игнорируем
ROLE_VACATION       = 1128580096509358121   # @Отпуск
ROLE_RESERVE        = 1168598614633885777   # @Резерв

PERIOD_DAYS         = 14          # длина периода в днях
QUOTA_NORMAL        = 3           # норма
QUOTA_PROMOTION     = 6           # на повышение
DB_PATH             = "reports.db"

# Ключевые слова — хватит хотя бы одного
REPORT_KEYWORDS = ["позывной", "дата", "отчёт", "отчет", "состав"]

OWNER_ID            = 936937494048559134   # твой Discord ID (Настройки → Расширенные → Режим разработчика → ПКМ на себе)

# Ключевые слова — хватит хотя бы одного
REPORT_KEYWORDS = ["позывной", "дата", "отчёт", "отчет", "состав"]

# Московское время = UTC+3
MSK = timezone(timedelta(hours=3))
ANNOUNCE_HOUR = 20   # 20:00 МСК по воскресеньям
# ─────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ══════════════════════════════════════════
#  ПЕРИОД: ВСЕГДА ПОНЕДЕЛЬНИК → ВОСКРЕСЕНЬЕ (14 ДНЕЙ)
# ══════════════════════════════════════════

def get_period_bounds(ref: datetime = None) -> tuple:
    """
    Возвращает (start, end) текущего двухнедельного периода.
    start — понедельник позапрошлой недели 00:00 МСК
    end   — ближайшее воскресенье 20:00 МСК
    """
    if ref is None:
        ref = datetime.now(MSK)

    # Ближайшее воскресенье (или сегодня если воскресенье)
    days_to_sunday = (6 - ref.weekday()) % 7
    end = (ref + timedelta(days=days_to_sunday)).replace(
        hour=20, minute=0, second=0, microsecond=0
    )

    # Понедельник — 13 дней назад от воскресенья
    start = (end - timedelta(days=13)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    return start, end


# ══════════════════════════════════════════
#  БАЗА ДАННЫХ
# ══════════════════════════════════════════

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS periods (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date  TEXT NOT NULL,
            end_date    TEXT,
            announced   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS reports (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            period_id    INTEGER NOT NULL,
            user_id      TEXT NOT NULL,
            username     TEXT NOT NULL,
            message_id   TEXT UNIQUE,
            submitted_at TEXT NOT NULL,
            FOREIGN KEY(period_id) REFERENCES periods(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    con.commit()
    con.close()


def get_current_period() -> tuple:
    """
    Возвращает (id, start_date) активного периода.
    Если его нет — создаёт с правильной датой начала (понедельник).
    """
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, start_date FROM periods WHERE announced=0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        con.close()
        return row[0], row[1]

    start, _ = get_period_bounds()
    start_str = start.isoformat()
    cur = con.execute("INSERT INTO periods(start_date) VALUES(?)", (start_str,))
    pid = cur.lastrowid
    con.commit()
    con.close()
    return pid, start_str


def message_already_counted(message_id: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT id FROM reports WHERE message_id=?", (message_id,)).fetchone()
    con.close()
    return row is not None


def add_report(period_id: int, user_id: str, username: str, message_id: str):
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO reports(period_id, user_id, username, message_id, submitted_at) "
            "VALUES(?,?,?,?,?)",
            (period_id, user_id, username, message_id, datetime.now(MSK).isoformat())
        )
        con.commit()
    except sqlite3.IntegrityError:
        pass
    con.close()


def remove_report_by_message(message_id: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM reports WHERE message_id=?", (message_id,))
    con.commit()
    con.close()


def count_reports_by_user(period_id: int) -> dict:
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT user_id, username, COUNT(*) FROM reports "
        "WHERE period_id=? GROUP BY user_id",
        (period_id,)
    ).fetchall()
    con.close()
    return {r[0]: (r[1], r[2]) for r in rows}


def close_period(period_id: int):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "UPDATE periods SET announced=1, end_date=? WHERE id=?",
        (datetime.now(MSK).isoformat(), period_id)
    )
    con.commit()
    con.close()


def get_announce_header() -> str:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT value FROM settings WHERE key='announce_header'").fetchone()
    con.close()
    return row[0] if row else (
        "Написанные отчёты зафиксированы. "
        "На следующем подсчёте наказания будут выдаваться по двойному тарифу "
        "(если вы не набрали норму)."
    )


def set_announce_header(text: str):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES('announce_header', ?)", (text,)
    )
    con.commit()
    con.close()


# ══════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════

def member_has_role(member: discord.Member, role_id: int) -> bool:
    return any(r.id == role_id for r in member.roles)


def get_tracked_members(guild: discord.Guild) -> list:
    result = []
    for m in guild.members:
        if m.bot:
            continue
        if not member_has_role(m, ROLE_DIVISION):
            continue
        if member_has_role(m, ROLE_IGNORE_1) or member_has_role(m, ROLE_IGNORE_2):
            continue
        result.append(m)
    return result


def is_report_message(text: str) -> bool:
    return any(kw in text.lower() for kw in REPORT_KEYWORDS)


def is_thread(channel) -> bool:
    return isinstance(channel, discord.Thread)


def is_admin(interaction: discord.Interaction) -> bool:
    return (
        member_has_role(interaction.user, ROLE_IGNORE_1) or
        member_has_role(interaction.user, ROLE_IGNORE_2)
    )


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == OWNER_ID


# ══════════════════════════════════════════
#  ФОРМИРОВАНИЕ ИТОГОВОГО СООБЩЕНИЯ
# ══════════════════════════════════════════

def build_summary(guild: discord.Guild, period_id: int, start_dt: datetime, next_start: datetime) -> str:
    report_map = count_reports_by_user(period_id)
    members    = get_tracked_members(guild)

    def sort_key(m):
        cnt = report_map.get(str(m.id), (None, 0))[1]
        return (-cnt, m.display_name.lower())

    members.sort(key=sort_key)

    lines = []
    for m in members:
        uid  = str(m.id)
        name = m.display_name
        cnt  = report_map.get(uid, (name, 0))[1]

        if member_has_role(m, ROLE_VACATION):
            status = "Отпуск"
        elif member_has_role(m, ROLE_RESERVE):
            status = "Резерв"
        elif cnt >= QUOTA_PROMOTION:
            status = f"{cnt} идёт на повышение"
        elif cnt >= QUOTA_NORMAL:
            status = str(cnt)
        elif cnt > 0:
            status = f"{cnt} — Сообщить причину отсутствия отчётов ответственному лицу"
        else:
            status = "0 — Сообщить причину отсутствия отчётов ответственному лицу"

        lines.append(f"{name} - {status}")

    # Следующий период: следующий понедельник → воскресенье через 14 дней
    next_end = next_start + timedelta(days=13)

    period_str   = f"{start_dt.strftime('%d.%m.%Y')} — {next_end.strftime('%d.%m.%Y')}"
    next_date_str = next_end.strftime('%d.%m.%Y')

    block = "\n".join(lines) if lines else "Нет участников"

    return (
        f"Ведётся отчёт за период **{period_str}**\n"
        f"Следующие итоги **{next_date_str}**\n"
        f"```\n{block}\n```"
    )


# ══════════════════════════════════════════
#  ПУБЛИКАЦИЯ ИТОГОВ
# ══════════════════════════════════════════

async def post_summary(pid: int, start_str: str):
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if not channel:
        print("[post_summary] Канал объявлений не найден!")
        return

    start_dt = datetime.fromisoformat(start_str)

    # Начало следующего периода — следующий понедельник
    now      = datetime.now(MSK)
    days_to_next_monday = (7 - now.weekday()) % 7 or 7
    next_start = (now + timedelta(days=days_to_next_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    role = channel.guild.get_role(PING_ROLE_ID)
    ping = role.mention if role else "@Подразделение BIO"

    header  = f"{ping} **Итоги отчётного периода**\n{get_announce_header()}\n"
    summary = build_summary(channel.guild, pid, start_dt, next_start)

    await channel.send(header + summary)

    close_period(pid)

    # Создаём новый период начиная со следующего понедельника
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT INTO periods(start_date) VALUES(?)", (next_start.isoformat(),))
    con.commit()
    con.close()

    print(f"[post_summary] Период {pid} закрыт. Новый период с {next_start.strftime('%d.%m.%Y')}.")


# ══════════════════════════════════════════
#  ТАЙМЕР — каждую минуту
#  Срабатывает в воскресенье в 20:00 МСК
# ══════════════════════════════════════════

@tasks.loop(minutes=1)
async def check_period():
    try:
        now = datetime.now(MSK)

        # Только воскресенье в 20:00
        if now.weekday() != 6 or now.hour != ANNOUNCE_HOUR or now.minute != 0:
            return

        pid, start_str = get_current_period()
        start_dt = datetime.fromisoformat(start_str)

        # Убеждаемся что прошло не менее 13 дней (период пн–вс)
        if (now - start_dt.replace(tzinfo=MSK)).days < 13:
            print(f"[check_period] Воскресенье, но период ещё не завершён ({(now - start_dt.replace(tzinfo=MSK)).days} дней).")
            return

        await post_summary(pid, start_str)

    except Exception as e:
        print(f"[check_period] Ошибка: {e}")


# ══════════════════════════════════════════
#  ОБРАБОТКА СООБЩЕНИЙ
# ══════════════════════════════════════════

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id != REPORT_CHANNEL_ID:
        await bot.process_commands(message)
        return
    if is_thread(message.channel):
        return
    if not isinstance(message.author, discord.Member):
        return
    if not member_has_role(message.author, ROLE_DIVISION):
        return
    if member_has_role(message.author, ROLE_IGNORE_1) or member_has_role(message.author, ROLE_IGNORE_2):
        return
    if not is_report_message(message.content):
        await bot.process_commands(message)
        return
    if message_already_counted(str(message.id)):
        return

    pid, _ = get_current_period()
    add_report(pid, str(message.author.id), message.author.display_name, str(message.id))

    try:
        await message.add_reaction("✅")
    except Exception:
        pass

    await bot.process_commands(message)


@bot.event
async def on_message_delete(message: discord.Message):
    if message.channel.id != REPORT_CHANNEL_ID:
        return
    if is_thread(message.channel):
        return
    remove_report_by_message(str(message.id))


# ══════════════════════════════════════════
#  SLASH-КОМАНДЫ
# ══════════════════════════════════════════

@tree.command(name="статистика", description="Показать текущую статистику периода")
async def cmd_stats(interaction: discord.Interaction):
    pid, start_str = get_current_period()
    start_dt   = datetime.fromisoformat(start_str)
    _, end_dt  = get_period_bounds()
    now        = datetime.now(MSK)
    days_left  = max(0, (end_dt - now).days)
    # next_start для build_summary
    days_to_next_monday = (7 - now.weekday()) % 7 or 7
    next_start = (now + timedelta(days=days_to_next_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    summary = build_summary(interaction.guild, pid, start_dt, next_start)
    await interaction.response.send_message(
        f"📊 Осталось дней до итогов: **{days_left}**\n{summary}",
        ephemeral=True
    )


@tree.command(name="закрыть_период", description="[Командир/Адъютант] Вручную опубликовать итоги")
async def cmd_close(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    await interaction.response.send_message("⏳ Публикую итоги...", ephemeral=True)
    pid, start_str = get_current_period()
    await post_summary(pid, start_str)
    await interaction.followup.send("✅ Готово, новый период начат.", ephemeral=True)


@tree.command(name="добавить_отчет", description="[Командир/Адъютант] Добавить отчёт вручную за участника")
@app_commands.describe(member="Участник сервера")
async def cmd_add_report(interaction: discord.Interaction, member: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    pid, _ = get_current_period()
    fake_id = f"manual_{member.id}_{datetime.now(MSK).timestamp()}"
    add_report(pid, str(member.id), member.display_name, fake_id)
    await interaction.response.send_message(
        f"✅ Отчёт добавлен для **{member.display_name}**.", ephemeral=True
    )


@tree.command(name="удалить_отчет", description="[Командир/Адъютант] Убрать один отчёт у участника")
@app_commands.describe(member="Участник сервера")
async def cmd_del_report(interaction: discord.Interaction, member: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    pid, _ = get_current_period()
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id FROM reports WHERE period_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
        (pid, str(member.id))
    ).fetchone()
    if row:
        con.execute("DELETE FROM reports WHERE id=?", (row[0],))
        con.commit()
        msg = f"🗑️ Один отчёт удалён у **{member.display_name}**."
    else:
        msg = f"⚠️ У **{member.display_name}** нет отчётов в этом периоде."
    con.close()
    await interaction.response.send_message(msg, ephemeral=True)


@tree.command(name="роль_выдать", description="[Создатель] Выдать роль участнику")
@app_commands.describe(member="Участник", role="Роль")
async def cmd_role_add(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    await member.add_roles(role)
    await interaction.response.send_message(
        f"✅ Роль **{role.name}** выдана **{member.display_name}**.", ephemeral=True
    )


@tree.command(name="роль_убрать", description="[Создатель] Убрать роль у участника")
@app_commands.describe(member="Участник", role="Роль")
async def cmd_role_remove(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    await member.remove_roles(role)
    await interaction.response.send_message(
        f"✅ Роль **{role.name}** убрана у **{member.display_name}**.", ephemeral=True
    )


@tree.command(name="роли_список", description="Показать роли участника")
@app_commands.describe(member="Участник")
async def cmd_roles_list(interaction: discord.Interaction, member: discord.Member):
    roles = [r.name for r in member.roles if r.name != "@everyone"]
    text  = ", ".join(roles) if roles else "нет ролей"
    await interaction.response.send_message(
        f"📋 Роли **{member.display_name}**: {text}", ephemeral=True
    )


@tree.command(name="изменить_шапку", description="[Создатель] Изменить текст шапки объявления")
@app_commands.describe(текст="Новый текст шапки")
async def cmd_edit_header(interaction: discord.Interaction, текст: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    set_announce_header(текст)
    await interaction.response.send_message(f"✅ Шапка обновлена:\n{текст}", ephemeral=True)


@tree.command(name="шапка_сброс", description="[Создатель] Сбросить шапку на стандартную")
async def cmd_reset_header(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)
        return
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM settings WHERE key='announce_header'")
    con.commit()
    con.close()
    await interaction.response.send_message("✅ Шапка сброшена на стандартную.", ephemeral=True)


# ══════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"Бот запущен: {bot.user} (ID: {bot.user.id})")
    init_db()
    pid, start_str = get_current_period()
    start_dt = datetime.fromisoformat(start_str)
    _, end_dt = get_period_bounds()
    print(f"Текущий период: {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}")
    try:
        synced = await tree.sync()
        print(f"Синхронизировано {len(synced)} команд")
    except Exception as e:
        print(f"Ошибка синхронизации: {e}")
    check_period.start()
    print("Таймер запущен. Объявление каждое воскресенье в 20:00 МСК.")


bot.run(TOKEN)
