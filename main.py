import json
import os
import random

from astrbot import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import At, Plain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)


def _get_ats(event: AiocqhttpMessageEvent) -> list[str]:
    """从消息中提取被@用户的ID列表（排除bot自身）"""
    return [
        str(seg.qq)
        for seg in event.get_messages()
        if isinstance(seg, At) and str(seg.qq) != event.get_self_id()
    ]


async def _get_nickname(event: AiocqhttpMessageEvent, user_id: int | str) -> str:
    """获取群昵称，优先群名片，其次QQ昵称"""
    user_id = int(user_id)
    group_id = event.get_group_id()
    info = {}
    if str(group_id).isdigit():
        try:
            info = await event.bot.get_group_member_info(
                group_id=int(group_id), user_id=user_id
            ) or {}
        except Exception:
            pass
    if not info:
        try:
            info = await event.bot.get_stranger_info(user_id=user_id) or {}
        except Exception:
            pass
    return info.get("card") or info.get("nickname") or info.get("nick") or str(user_id)


async def _get_bot_role(event: AiocqhttpMessageEvent) -> str:
    """获取bot在群内的身份: owner / admin / member"""
    try:
        info = await event.bot.get_group_member_info(
            group_id=int(event.get_group_id()),
            user_id=int(event.get_self_id()),
        )
        return info.get("role", "member")
    except Exception:
        return "member"


async def _get_user_role(event: AiocqhttpMessageEvent, user_id: str) -> str:
    """获取用户在群内的身份"""
    try:
        info = await event.bot.get_group_member_info(
            group_id=int(event.get_group_id()),
            user_id=int(user_id),
        )
        return info.get("role", "member")
    except Exception:
        return "member"


def _is_group_admin_or_owner(role: str) -> bool:
    """判断角色是否为群管理员或群主"""
    return role in ("admin", "owner")


class GroupFunPlugin(Star):
    """自爆插件 —— 天弃之子 / 同归于尽 / 精致睡眠"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_group_fun")

        # 配置参数
        self.tianqi_ban_min = config.get("tianqi_ban_min", 60)
        self.tianqi_ban_max = config.get("tianqi_ban_max", 600)
        self.tianqi_miss_prob = config.get("tianqi_miss_prob", 0.15)
        self.tianqi_self_ban_prob = config.get("tianqi_self_ban_prob", 0.2)  # 连带禁言发起者的概率
        self.tonggui_ban_min = config.get("tonggui_ban_min", 60)
        self.tonggui_ban_max = config.get("tonggui_ban_max", 600)
        self.sleep_duration = config.get("sleep_duration", 28800)  # 8h

        # 持久化数据文件
        self._state_path = os.path.join(self.data_dir, "group_fun_state.json")
        self._state = self._load_state()

    # ───────── 持久化工具 ─────────

    def _load_state(self) -> dict:
        if os.path.exists(self._state_path):
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self):
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    def _group_state(self, group_id: str) -> dict:
        if group_id not in self._state:
            self._state[group_id] = {
                "tianqi_enabled": True,
                "tonggui_enabled": True,
                "sleep_enabled": True,
                "tonggui_allow": {},  # user_id -> bool
            }
        return self._state[group_id]

    # ───────── 1. 天弃之子 ─────────

    @filter.command("天弃之子")
    async def tianqi_child(self, event: AiocqhttpMessageEvent):
        """随机选择一位群友禁言"""
        if event.is_private_chat():
            return

        gid = event.get_group_id()
        gs = self._group_state(gid)
        if not gs["tianqi_enabled"]:
            yield event.plain_result("天弃之子功能已关闭")
            return

        bot_role = await _get_bot_role(event)
        if not _is_group_admin_or_owner(bot_role):
            yield event.plain_result("Bot需要管理员权限才能执行此操作")
            return

        # 获取群成员列表
        try:
            members = await event.bot.get_group_member_list(
                group_id=int(gid)
            )
        except Exception as e:
            logger.error(f"获取群成员列表失败: {e}")
            yield event.plain_result("获取群成员列表失败")
            return

        self_id = event.get_self_id()
        # 过滤掉bot自身、群主、管理员
        candidates = []
        for m in members:
            uid = str(m.get("user_id", ""))
            role = m.get("role", "member")
            if uid == self_id:
                continue
            # 如果bot是管理员（非群主），则不能禁言群主和其他管理员
            if bot_role == "admin" and role in ("owner", "admin"):
                continue
            # 如果bot是群主，则除群主外都可以
            if bot_role == "owner" and role == "owner":
                continue
            candidates.append(m)

        if not candidates:
            yield event.plain_result("没有可以禁言的群友")
            return

        victim = random.choice(candidates)
        victim_id = str(victim.get("user_id", ""))
        victim_name = await _get_nickname(event, victim_id)

        # 劈空判定
        if random.random() < self.tianqi_miss_prob:
            miss_texts = [
                f"一道闪电劈向了 {victim_name}，但是歪了，毫发无伤。",
                f"雷公打了个盹，闪电劈到了旁边的树上，{victim_name} 逃过一劫。",
                f"一只鸽子挡住了闪电，{victim_name} 安然无恙。",
                f"天空一声巨响，结果只是下了场小雨，{victim_name} 今天运气不错。",
            ]
            yield event.plain_result(random.choice(miss_texts))
            event.stop_event()
            return

        ban_time = random.randint(self.tianqi_ban_min, self.tianqi_ban_max)

        try:
            await event.bot.set_group_ban(
                group_id=int(gid),
                user_id=int(victim_id),
                duration=ban_time,
            )
        except Exception as e:
            logger.error(f"天弃之子禁言失败: {e}")
            yield event.plain_result(f"禁言失败: {e}")
            return

        minutes = ban_time // 60
        seconds = ban_time % 60
        time_str = f"{minutes}分{seconds}秒" if seconds else f"{minutes}分钟"

        # 连带自爆判定
        sender_id = event.get_sender_id()
        self_ban_triggered = False
        self_ban_time = 0
        if victim_id != sender_id and random.random() < self.tianqi_self_ban_prob:
            sender_role = await _get_user_role(event, sender_id)
            can_ban_sender = not (
                (bot_role == "admin" and sender_role in ("owner", "admin"))
                or (bot_role == "owner" and sender_role == "owner")
            )
            if can_ban_sender:
                self_ban_time = random.randint(self.tianqi_ban_min, self.tianqi_ban_max)
                try:
                    await event.bot.set_group_ban(
                        group_id=int(gid),
                        user_id=int(sender_id),
                        duration=self_ban_time,
                    )
                    self_ban_triggered = True
                except Exception as e:
                    logger.error(f"天弃之子连带禁言失败: {e}")

        chain = [
            Plain(text="一道闪电劈中了 "),
            At(qq=victim_id),
            Plain(text=f" {victim_name}，成为今天的天弃之子，禁言 {time_str}"),
        ]
        if self_ban_triggered:
            sb_min = self_ban_time // 60
            sb_sec = self_ban_time % 60
            sb_str = f"{sb_min}分{sb_sec}秒" if sb_sec else f"{sb_min}分钟"
            sender_name = await _get_nickname(event, sender_id)
            chain.append(Plain(text=f"\n闪电余波波及了召唤者 {sender_name}，禁言 {sb_str}"))
        yield event.chain_result(chain)
        event.stop_event()

    # ───────── 2. 同归于尽 ─────────

    @filter.command("同归于尽")
    async def tonggui(self, event: AiocqhttpMessageEvent):
        """拉一位群友一起禁言"""
        if event.is_private_chat():
            return

        gid = event.get_group_id()
        gs = self._group_state(gid)
        if not gs["tonggui_enabled"]:
            yield event.plain_result("同归于尽功能已关闭")
            return

        bot_role = await _get_bot_role(event)
        if not _is_group_admin_or_owner(bot_role):
            yield event.plain_result("Bot需要管理员权限才能执行此操作")
            return

        sender_id = event.get_sender_id()
        sender_role = await _get_user_role(event, sender_id)

        # 不能对群主使用（如果bot不是群主）
        if bot_role != "owner" and sender_role == "owner":
            yield event.plain_result("无法对群主执行禁言")
            return

        # 获取@的目标
        target_ids = _get_ats(event)
        if not target_ids:
            yield event.plain_result("请@一位群友，用法：同归于尽 @群友")
            return

        target_id = target_ids[0]

        # 不能同归自己
        if target_id == sender_id:
            yield event.plain_result("不能和自己同归于尽")
            return

        # 不能同归bot
        if target_id == event.get_self_id():
            yield event.plain_result("不能和我同归于尽")
            return

        target_role = await _get_user_role(event, target_id)

        # 如果bot是管理员，不能禁言群主和其他管理员
        if bot_role == "admin" and target_role in ("owner", "admin"):
            yield event.plain_result("无法对管理员/群主执行禁言")
            return

        # 如果bot是群主，不能禁言群主（自己）
        if bot_role == "owner" and target_role == "owner":
            yield event.plain_result("无法对群主执行禁言")
            return

        # 检查目标是否允许被同归
        allow_map = gs.get("tonggui_allow", {})
        if allow_map.get(target_id) is False:
            target_name = await _get_nickname(event, target_id)
            yield event.plain_result(f"{target_name} 已禁止被同归于尽")
            return

        ban_time = random.randint(self.tonggui_ban_min, self.tonggui_ban_max)

        # 反弹倍数: 1~9 的加权随机，数学期望约 3.024691358
        # P(n) = 2*(10-n) / 90  →  1倍最容易，9倍最难
        weights = [2 * (10 - n) for n in range(1, 10)]
        multiplier = random.choices(range(1, 10), weights=weights, k=1)[0]
        sender_ban = ban_time * multiplier

        # 检查发起者是否可以被禁言
        can_ban_sender = True
        if bot_role == "admin" and sender_role in ("owner", "admin"):
            can_ban_sender = False
        if bot_role == "owner" and sender_role == "owner":
            can_ban_sender = False

        # 禁言目标
        try:
            await event.bot.set_group_ban(
                group_id=int(gid), user_id=int(target_id), duration=ban_time
            )
        except Exception as e:
            logger.error(f"同归于尽禁言目标失败: {e}")

        # 禁言发起者（反弹）
        if can_ban_sender:
            try:
                await event.bot.set_group_ban(
                    group_id=int(gid), user_id=int(sender_id), duration=sender_ban
                )
            except Exception as e:
                logger.error(f"同归于尽反弹禁言失败: {e}")

        def _fmt(secs: int) -> str:
            m, s = divmod(secs, 60)
            return f"{m}分{s}秒" if s else f"{m}分钟"

        t_str = _fmt(ban_time)
        s_str = _fmt(sender_ban)

        chain = [
            Plain(text="同归于尽！\n"),
            At(qq=target_id),
            Plain(text=f" 禁言 {t_str}\n"),
            At(qq=sender_id),
            Plain(text=f" 受到 {multiplier} 倍反弹，禁言 {s_str}"),
            Plain(text="" if can_ban_sender else "\n（权限不足，发起者未被禁言）"),
        ]

        yield event.chain_result(chain)
        event.stop_event()

    @filter.command("允许被同归")
    async def allow_tonggui(self, event: AiocqhttpMessageEvent):
        """允许自己被同归于尽"""
        if event.is_private_chat():
            return
        gid = event.get_group_id()
        gs = self._group_state(gid)
        allow_map = gs.setdefault("tonggui_allow", {})
        sender_id = event.get_sender_id()
        allow_map[sender_id] = True
        self._save_state()
        yield event.plain_result("你已允许被同归于尽")
        event.stop_event()

    @filter.command("禁止被同归")
    async def deny_tonggui(self, event: AiocqhttpMessageEvent):
        """禁止自己被同归于尽"""
        if event.is_private_chat():
            return
        gid = event.get_group_id()
        gs = self._group_state(gid)
        allow_map = gs.setdefault("tonggui_allow", {})
        sender_id = event.get_sender_id()
        allow_map[sender_id] = False
        self._save_state()
        yield event.plain_result("你已禁止被同归于尽")
        event.stop_event()

    # ───────── 3. 精致睡眠 ─────────

    @filter.command("睡眠套餐", alias={"精致睡眠", "来一份精致睡眠套餐"})
    async def sleep_ban(self, event: AiocqhttpMessageEvent):
        """8小时精致睡眠套餐（自我禁言）"""
        if event.is_private_chat():
            return

        gid = event.get_group_id()
        gs = self._group_state(gid)
        if not gs["sleep_enabled"]:
            yield event.plain_result("精致睡眠功能已关闭")
            return

        bot_role = await _get_bot_role(event)
        if not _is_group_admin_or_owner(bot_role):
            yield event.plain_result("Bot需要管理员权限才能执行此操作")
            return

        sender_id = event.get_sender_id()
        sender_role = await _get_user_role(event, sender_id)

        if bot_role == "admin" and sender_role in ("owner", "admin"):
            yield event.plain_result("无法对管理员/群主执行禁言")
            return
        if bot_role == "owner" and sender_role == "owner":
            yield event.plain_result("群主无法使用此功能")
            return

        try:
            await event.bot.set_group_ban(
                group_id=int(gid),
                user_id=int(sender_id),
                duration=self.sleep_duration,
            )
        except Exception as e:
            logger.error(f"精致睡眠禁言失败: {e}")
            yield event.plain_result(f"禁言失败: {e}")
            return

        hours = self.sleep_duration // 3600
        chain = [
            At(qq=sender_id),
            Plain(text=f" 晚安，已开启 {hours} 小时精致睡眠套餐。"),
        ]
        yield event.chain_result(chain)
        event.stop_event()

    # ───────── 功能开关 ─────────

    @filter.command("关闭天弃之子")
    async def disable_tianqi(self, event: AiocqhttpMessageEvent):
        """关闭天弃之子功能（需要群管理员/群主权限）"""
        if event.is_private_chat():
            return
        sender_id = event.get_sender_id()
        sender_role = await _get_user_role(event, sender_id)
        if not _is_group_admin_or_owner(sender_role):
            yield event.plain_result("需要群管理员/群主权限")
            return
        gid = event.get_group_id()
        gs = self._group_state(gid)
        gs["tianqi_enabled"] = False
        self._save_state()
        yield event.plain_result("已关闭天弃之子")
        event.stop_event()

    @filter.command("开启天弃之子")
    async def enable_tianqi(self, event: AiocqhttpMessageEvent):
        """开启天弃之子功能"""
        if event.is_private_chat():
            return
        sender_id = event.get_sender_id()
        sender_role = await _get_user_role(event, sender_id)
        if not _is_group_admin_or_owner(sender_role):
            yield event.plain_result("需要群管理员/群主权限")
            return
        gid = event.get_group_id()
        gs = self._group_state(gid)
        gs["tianqi_enabled"] = True
        self._save_state()
        yield event.plain_result("已开启天弃之子")
        event.stop_event()

    @filter.command("关闭同归于尽")
    async def disable_tonggui(self, event: AiocqhttpMessageEvent):
        """关闭同归于尽功能"""
        if event.is_private_chat():
            return
        sender_id = event.get_sender_id()
        sender_role = await _get_user_role(event, sender_id)
        if not _is_group_admin_or_owner(sender_role):
            yield event.plain_result("需要群管理员/群主权限")
            return
        gid = event.get_group_id()
        gs = self._group_state(gid)
        gs["tonggui_enabled"] = False
        self._save_state()
        yield event.plain_result("已关闭同归于尽")
        event.stop_event()

    @filter.command("开启同归于尽")
    async def enable_tonggui(self, event: AiocqhttpMessageEvent):
        """开启同归于尽功能"""
        if event.is_private_chat():
            return
        sender_id = event.get_sender_id()
        sender_role = await _get_user_role(event, sender_id)
        if not _is_group_admin_or_owner(sender_role):
            yield event.plain_result("需要群管理员/群主权限")
            return
        gid = event.get_group_id()
        gs = self._group_state(gid)
        gs["tonggui_enabled"] = True
        self._save_state()
        yield event.plain_result("已开启同归于尽")
        event.stop_event()

    @filter.command("关闭睡眠")
    async def disable_sleep(self, event: AiocqhttpMessageEvent):
        """关闭精致睡眠功能"""
        if event.is_private_chat():
            return
        sender_id = event.get_sender_id()
        sender_role = await _get_user_role(event, sender_id)
        if not _is_group_admin_or_owner(sender_role):
            yield event.plain_result("需要群管理员/群主权限")
            return
        gid = event.get_group_id()
        gs = self._group_state(gid)
        gs["sleep_enabled"] = False
        self._save_state()
        yield event.plain_result("已关闭精致睡眠")
        event.stop_event()

    @filter.command("开启睡眠")
    async def enable_sleep(self, event: AiocqhttpMessageEvent):
        """开启精致睡眠功能"""
        if event.is_private_chat():
            return
        sender_id = event.get_sender_id()
        sender_role = await _get_user_role(event, sender_id)
        if not _is_group_admin_or_owner(sender_role):
            yield event.plain_result("需要群管理员/群主权限")
            return
        gid = event.get_group_id()
        gs = self._group_state(gid)
        gs["sleep_enabled"] = True
        self._save_state()
        yield event.plain_result("已开启精致睡眠")
        event.stop_event()

