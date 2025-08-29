import discord
from discord.ext import commands
from discord import app_commands
from typing import Union, Optional, List, Dict, Any, Tuple
import random
import os
import json
from dotenv import load_dotenv



# 기본 설정

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID: Optional[int] = None  # 테스트용 길드(서버) ID 넣으면 빠르게 동기화됨 기본값 : None
DATA_FILE = "knowledge.json"

# 권한 추가 (interaction.user.name)
privileged_users: List[str] = ["lover_hoshino"]



# 기본(고정) 지식 (가르치기 불가)


default_knowledge: Dict[str, str] = {
    "hi":"hello world",
}


# 데이터 파일 입출력 & 정규화 (길드별 저장)
# 데이터 표준 형식 (새 포맷):
# {
#   "guild_id_str": {
#       "keyword": [ { "response": str, "teacher": str }, ... ]
#   },
#   ...
# }
#
# 레거시 포맷(전역):
# { "keyword": [ { "response": "...", "teacher":"..." }, ... ] }
#  -> 최초 접근 길드로 이관 (옵션)


def ensure_data_file():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)

def _is_new_format(obj: Any) -> bool:
    # 새 포맷은 최상위가 dict이고 값이 dict(=길드 맵)이어야 함
    if not isinstance(obj, dict):
        return False
    # 비어있으면 새 포맷 간주
    if len(obj) == 0:
        return True
    # 임의의 하나를 검사
    any_val = next(iter(obj.values()))
    return isinstance(any_val, dict)

def _normalize_legacy_value_to_list_of_dict(val: Any, default_teacher: str = "unknown") -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                resp = item.get("response")
                t = item.get("teacher", default_teacher)
                if resp:
                    out.append({"response": resp, "teacher": t})
            elif isinstance(item, str):
                out.append({"response": item, "teacher": default_teacher})
    elif isinstance(val, dict):
        resp = val.get("response")
        t = val.get("teacher", default_teacher)
        if resp:
            out.append({"response": resp, "teacher": t})
    elif isinstance(val, str):
        out.append({"response": val, "teacher": default_teacher})
    return out

def _migrate_any_legacy_structure(raw: Dict[str, Any]) -> Dict[str, Dict[str, List[Dict[str, str]]]]:
    """
    레거시 전역 포맷을 새 포맷으로 승격.
    규칙:
      - 키워드별로 list/dict/str을 전부 표준 리스트[{"response","teacher"}]로 변환
      - 최상단에 '0' 같은 가짜 길드로 넣는 대신, 'legacy_buffer' 영역에 임시 저장.
        실제로는 봇이 사용되는 길드에서 접근할 때 그 길드로 가져가도록 한다.
      - 단, 이 함수에서는 우선 '___LEGACY___' 라는 특수 길드 ID로 보관.
    """
    migrated: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
    LEGACY_GID = "___LEGACY___"
    migrated[LEGACY_GID] = {}
    for key, val in raw.items():
        # 레거시 특수 포맷 "keyword::teacher": [...]
        if "::" in key and isinstance(val, list):
            try:
                keyword, teacher = key.split("::", 1)
            except Exception:
                keyword, teacher = key, "unknown"
            normalized_list: List[Dict[str, str]] = []
            for item in val:
                if isinstance(item, str):
                    normalized_list.append({"response": item, "teacher": teacher})
                elif isinstance(item, dict):
                    resp = item.get("response")
                    t = item.get("teacher", teacher)
                    if resp:
                        normalized_list.append({"response": resp, "teacher": t})
            if normalized_list:
                migrated[LEGACY_GID].setdefault(keyword, []).extend(normalized_list)
        else:
            normalized_list = _normalize_legacy_value_to_list_of_dict(val)
            if normalized_list:
                migrated[LEGACY_GID].setdefault(key, []).extend(normalized_list)
    return migrated

def _adopt_legacy_into_guild(new_format_data: Dict[str, Dict[str, List[Dict[str, str]]]], guild_id_str: str) -> bool:
    """
    '___LEGACY___'에 보관된 항목을 최초 접근한 길드로 이관.
    반환값: 실제로 이관이 일어났는지 여부
    """
    LEGACY_GID = "___LEGACY___"
    if LEGACY_GID not in new_format_data:
        return False
    legacy_map = new_format_data.get(LEGACY_GID, {})
    if not legacy_map:
        # 비어있으면 제거
        new_format_data.pop(LEGACY_GID, None)
        return False
    # 이관
    target_map = new_format_data.setdefault(guild_id_str, {})
    for kw, arr in legacy_map.items():
        target_map.setdefault(kw, []).extend(arr)
    # 레거시 삭제
    new_format_data.pop(LEGACY_GID, None)
    return True

def load_data() -> Dict[str, Dict[str, List[Dict[str, str]]]]:
    ensure_data_file()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError:
            raw = {}

    # 새 포맷이면 그대로(단, 내부 타입 보정)
    if _is_new_format(raw):
        # 내부 값 보정: 리스트 내 원소가 dict로 정규화
        normalized: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
        for gid, kw_map in raw.items():
            if not isinstance(kw_map, dict):
                continue
            for kw, val in kw_map.items():
                lst = _normalize_legacy_value_to_list_of_dict(val)
                if lst:
                    normalized.setdefault(gid, {}).setdefault(kw, []).extend(lst)
        if normalized != raw:
            save_data(normalized)
        return normalized

    # 레거시 포맷이면 새 포맷으로 변환(임시 legacy 영역에 저장)
    migrated = _migrate_any_legacy_structure(raw)
    save_data(migrated)
    return migrated

def save_data(data: Dict[str, Dict[str, List[Dict[str, str]]]]):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 메모리 로드
learned_data: Dict[str, Dict[str, List[Dict[str, str]]]] = load_data()



# 봇 초기화


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# 헬퍼

def gid_str_from_guild(guild: Optional[discord.Guild]) -> Optional[str]:
    return str(guild.id) if guild else None

def build_entries_for_guild(guild_id_str: str, filter_user: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    해당 길드 안에서 (옵션: 특정 teacher 필터) 페이지 항목 구성
    항목 구조: { "guild_id": str, "keyword": str, "teacher": str, "responses": List[str] }
    """
    entries: List[Dict[str, Any]] = []
    kw_map = learned_data.get(guild_id_str, {})
    for kw, arr in kw_map.items():
        by_teacher: Dict[str, List[str]] = {}
        for e in arr:
            if not isinstance(e, dict):
                continue
            t = e.get("teacher", "unknown")
            r = e.get("response")
            if r is None:
                continue
            by_teacher.setdefault(t, []).append(r)
        for teacher, responses in by_teacher.items():
            if filter_user is None or teacher == filter_user:
                entries.append({"guild_id": guild_id_str, "keyword": kw, "teacher": teacher, "responses": responses})
    return entries

def build_entries_for_user_all_guilds(username: str) -> List[Dict[str, Any]]:
    """
    모든 길드에서 해당 유저가 가르친 항목만 통합 페이지 항목 구성
    """
    entries: List[Dict[str, Any]] = []
    for gid, kw_map in learned_data.items():
        for kw, arr in kw_map.items():
            responses: List[str] = []
            for e in arr:
                if not isinstance(e, dict):
                    continue
                if e.get("teacher", "unknown") == username:
                    r = e.get("response")
                    if r is not None:
                        responses.append(r)
            if responses:
                entries.append({"guild_id": gid, "keyword": kw, "teacher": username, "responses": responses})
    return entries



# KnowledgeView: 페이지네이션 + 이전/다음 + 삭제 버튼
# 한 페이지: 1개의 (guild, keyword, teacher) 항목 (원래 UX 유지)

class KnowledgeView(discord.ui.View):
    def __init__(self, requester: Union[discord.User, discord.Member], entries: List[Dict[str, Any]], per_page: int = 1):
        super().__init__(timeout=None)
        self.requester = requester
        self.entries = entries
        self.index = 0
        self.per_page = per_page
        self.update_buttons()

    def update_buttons(self):
        try:
            self.previous.disabled = (self.index == 0)
            self.next.disabled = (self.index >= len(self.entries) - 1)
        except Exception:
            pass

    def _guild_display_name(self, guild_id_str: str) -> str:
        try:
            gid_int = int(guild_id_str) if guild_id_str.isdigit() else None
        except Exception:
            gid_int = None
        if gid_int:
            g = bot.get_guild(gid_int)
            if g:
                return f"{g.name} (ID: {guild_id_str})"
        # 레거시 가상/미해결 케이스
        return f"ID: {guild_id_str}"

    def get_embed(self) -> discord.Embed:
        if not self.entries:
            return discord.Embed(title="📘 배운 키워드", description="아직 배운 내용이 없습니다.", color=discord.Color.green())
        e = self.entries[self.index]
        kw = e["keyword"]
        teacher = e["teacher"]
        responses = e["responses"]
        gid = e["guild_id"]
        guild_line = f"**서버**: {self._guild_display_name(gid)}\n"
        desc = guild_line + f"**{kw}** (가르친 사람: {teacher})\n\n" + "\n".join(f"- {r}" for r in responses)
        embed = discord.Embed(title=f"📘 배운 키워드 {self.index + 1}/{len(self.entries)}", description=desc, color=discord.Color.green())
        return embed

    @discord.ui.button(label="⬅️ 이전", style=discord.ButtonStyle.gray, row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.requester:
            await interaction.response.send_message("이건 당신이 조작할 수 없어요!", ephemeral=True)
            return
        if self.index > 0:
            self.index -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="🗑️ 삭제", style=discord.ButtonStyle.red, row=0)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.entries:
            await interaction.response.send_message("삭제할 항목이 없습니다.", ephemeral=True)
            return

        entry = self.entries[self.index]
        kw = entry["keyword"]
        teacher = entry["teacher"]
        responses = entry["responses"]
        gid = entry["guild_id"]
        requester_name = interaction.user.name  # username

        # 권한 체크
        is_privileged = requester_name in privileged_users
        is_owner = requester_name == teacher
        if not (is_owner or is_privileged):
            await interaction.response.send_message("이건 당신이 지울 수 없어요!", ephemeral=True)
            return

        # 해당 길드 맥락에서 삭제
        guild_kw_map = learned_data.get(gid, {})

        # 하나면 바로 삭제
        if len(responses) == 1:
            resp_to_remove = responses[0]
            arr = guild_kw_map.get(kw, [])
            new_arr = [e for e in arr if not (e.get("teacher") == teacher and e.get("response") == resp_to_remove)]
            if new_arr:
                guild_kw_map[kw] = new_arr
            else:
                guild_kw_map.pop(kw, None)

            # 반영
            if guild_kw_map:
                learned_data[gid] = guild_kw_map
            else:
                learned_data.pop(gid, None)
            save_data(learned_data)

            # entries 갱신
            self.entries.pop(self.index)
            if self.index >= len(self.entries) and self.index > 0:
                self.index -= 1
            self.update_buttons()
            await interaction.response.send_message(f"🗑️ [{self._guild_display_name(gid)}] '{kw}'의 해당 대답을 삭제했습니다.", ephemeral=True)
            try:
                await interaction.message.edit(embed=self.get_embed(), view=self)
            except Exception:
                pass
            return

        # 멀티 응답(멀티 삭제 메뉴 표시)
        options = [discord.SelectOption(label=r, value=r) for r in responses]
        view = MultiDeleteView(requester_name, gid, kw, teacher, options, self)
        await interaction.response.send_message("삭제할 대답을 선택하세요 (여러 개 선택 가능):", view=view, ephemeral=True)

    @discord.ui.button(label="➡️ 다음", style=discord.ButtonStyle.gray, row=0)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.requester:
            await interaction.response.send_message("이건 당신이 조작할 수 없어요!", ephemeral=True)
            return
        if self.index < len(self.entries) - 1:
            self.index += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)



# 멀티 삭제 메뉴 (취소)

class MultiDeleteView(discord.ui.View):
    def __init__(self, requester_name: str, guild_id_str: str, keyword: str, teacher: str,
                 options: List[discord.SelectOption], parent_view: KnowledgeView):
        super().__init__(timeout=120)
        self.requester_name = requester_name
        self.guild_id_str = guild_id_str
        self.keyword = keyword
        self.teacher = teacher
        self.parent_view = parent_view

        self.select = discord.ui.Select(placeholder="삭제할 대답 선택", min_values=1, max_values=len(options), options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)

        self.confirm = discord.ui.Button(label="삭제 확정", style=discord.ButtonStyle.danger)
        self.confirm.callback = self._on_confirm
        self.add_item(self.confirm)

        self.cancel = discord.ui.Button(label="취소", style=discord.ButtonStyle.secondary)
        self.cancel.callback = self._on_cancel
        self.add_item(self.cancel)

        self.selected_values: List[str] = []

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.name != self.requester_name:
            await interaction.response.send_message("이 메뉴는 해당 명령어 호출자만 사용할 수 있습니다.", ephemeral=True)
            return
        self.selected_values = self.select.values
        await interaction.response.defer(ephemeral=True)

    async def _on_confirm(self, interaction: discord.Interaction):
        if interaction.user.name != self.requester_name:
            await interaction.response.send_message("이 메뉴는 해당 명령어 호출자만 사용할 수 없습니다.", ephemeral=True)
            return
        if not self.selected_values:
            await interaction.response.send_message("삭제할 항목을 선택하세요.", ephemeral=True)
            return

        kw_map = learned_data.get(self.guild_id_str, {})
        arr = kw_map.get(self.keyword, [])
        new_arr = [e for e in arr if not (e.get("teacher") == self.teacher and e.get("response") in self.selected_values)]
        if new_arr:
            kw_map[self.keyword] = new_arr
        else:
            kw_map.pop(self.keyword, None)

        if kw_map:
            learned_data[self.guild_id_str] = kw_map
        else:
            learned_data.pop(self.guild_id_str, None)

        save_data(learned_data)

        pv = self.parent_view
        # parent_view.entries는 다양한 소스일 수 있으니 전체 재구성 시나리오 없음
        # 대신 현재 페이지의 항목을 갱신/삭제
        pv.entries = [e for e in pv.entries if not (e["guild_id"] == self.guild_id_str and e["keyword"] == self.keyword and e["teacher"] == self.teacher)]
        if pv.index >= len(pv.entries) and pv.index > 0:
            pv.index = len(pv.entries) - 1
        pv.update_buttons()

        await interaction.response.send_message(f"✅ {len(self.selected_values)}개 삭제 완료.", ephemeral=True)
        try:
            await interaction.message.edit(embed=pv.get_embed(), view=pv)
        except Exception:
            pass
        self.stop()

    async def _on_cancel(self, interaction: discord.Interaction):
        if interaction.user.name != self.requester_name:
            await interaction.response.send_message("이 메뉴는 해당 명령어 사용자만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.send_message("취소되었습니다.", ephemeral=True)
        self.stop()



# /커맨드

@tree.command(name="가르치기", description="호시노가 대답할 말을 가르칩니다.")
@app_commands.describe(가르칠말="가르칠 단어(혹은 문장)", 대답="호시노가 말하게 될 대답")
async def teach(interaction: discord.Interaction, 가르칠말: str, 대답: str):
    username = interaction.user.name
    if 가르칠말 in default_knowledge:
        await interaction.response.send_message("❌ 이 키워드는 수정/가르치기 할 수 없습니다.", ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message("❌ 이 명령어는 서버에서만 사용할 수 있어요.", ephemeral=True)
        return

    gid = gid_str_from_guild(interaction.guild)
    assert gid is not None

    # 레거시 이관: 만약 ___LEGACY___ 데이터가 남아있다면 현재 길드로 1회 이관
    if "_KnowledgeView__" == "_dummy_":  # (lint용, 미사용)
        pass
    if _adopt_legacy_into_guild(learned_data, gid):
        save_data(learned_data)

    learned_data.setdefault(gid, {}).setdefault(가르칠말, []).append({"response": 대답, "teacher": username})
    save_data(learned_data)
    await interaction.response.send_message(f"✅ 이곳에서 '{가르칠말}'을(를) '{대답}'라고 하면 되는거죠? (by {username})", ephemeral=True)


@tree.command(name="배운내용", description="지금까지 배운 말들 보여준다.")
@app_commands.describe(유저="특정 유저의 가르친 내용만 보기 (없으면 현재 서버에서 배운 것만)")
async def show_knowledge_command(interaction: discord.Interaction, 유저: Optional[str] = None):
    # resolve user param: accept '@name' or 'name'
    filter_user: Optional[str] = None
    if 유저:
        candidate = 유저.lstrip("@")
        # mention 형태 <@!id> 처리 시도 -> username
        if interaction.guild and candidate.startswith("<@") and candidate.endswith(">"):
            try:
                uid = int(candidate.strip("<@!>"))
                member = interaction.guild.get_member(uid)
                if member:
                    candidate = member.name
            except Exception:
                pass
        filter_user = candidate

    # 유저 미지정 -> 현재 길드에서 배운 내용만
    if not filter_user:
        if not interaction.guild:
            await interaction.response.send_message("이 명령어는 길드(서버)에서만 사용할 수 있어요.", ephemeral=True)
            return
        gid = gid_str_from_guild(interaction.guild)
        assert gid is not None

        # 레거시 이관 시도
        if _adopt_legacy_into_guild(learned_data, gid):
            save_data(learned_data)

        entries = build_entries_for_guild(gid, filter_user=None)
        if not entries:
            await interaction.response.send_message("해당 서버에서 배운 내용이 없습니다.", ephemeral=True)
            return

        view = KnowledgeView(interaction.user, entries)
        await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)
        return

    # 유저 지정 -> 그 유저가 모든 서버에서 가르친 내용 통합
    entries_all = build_entries_for_user_all_guilds(filter_user)
    if not entries_all:
        await interaction.response.send_message(f"'{filter_user}' 님이 가르친 내용이 없습니다.", ephemeral=True)
        return

    view = KnowledgeView(interaction.user, entries_all)
    await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)



# 메시지 처리 (호시노에게 가르친 단어 호출)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    content = message.content.strip()
    if content.startswith("호시노야 "):
        key = content.removeprefix("호시노야 ").strip()
        if key in default_knowledge:
            await message.channel.send(default_knowledge[key])
        else:
            # 길드 컨텍스트에서만 길드별 데이터 사용
            if message.guild:
                gid = gid_str_from_guild(message.guild)
                if gid:
                    kw_map = learned_data.get(gid, {})
                    matches: List[Tuple[str, str, List[str]]] = []  # (kw, teacher, responses)
                    for kw, arr in kw_map.items():
                        if kw == key:
                            by_teacher: Dict[str, List[str]] = {}
                            for e in arr:
                                if not isinstance(e, dict):
                                    continue
                                t = e.get("teacher", "unknown")
                                r = e.get("response")
                                if r is None:
                                    continue
                                by_teacher.setdefault(t, []).append(r)
                            for teacher, responses in by_teacher.items():
                                matches.append((kw, teacher, responses))
                    if matches:
                        chosen = random.choice(matches)
                        resp = random.choice(chosen[2])
                        await message.channel.send(f"{resp}\n-# {chosen[1]}님이 가르쳐 주셨어요!")
    await bot.process_commands(message)


# 시작 / sync

@bot.event
async def on_ready():
    global learned_data
    learned_data = load_data()  # reload/normalize on ready
    print(f"✅ 로그인됨: {bot.user} (ID: {bot.user.id})")
    try:
        if GUILD_ID:
            synced = await tree.sync(guild=discord.Object(id=GUILD_ID))
        else:
            synced = await tree.sync()
        print(f"🔧 {len(synced)}개의 슬래시 명령어를 동기화했어요.")
    except Exception as e:
        print(f"❌ 명령어 동기화 실패: {e}")


# 실행

if __name__ == "__main__":
    if not TOKEN:
        print("DISCORD_TOKEN을 .env에 넣어주세요.")
    else:
        bot.run(TOKEN)
