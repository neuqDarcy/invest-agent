"""
从对话中自动学习用户投资偏好，异步更新用户画像。
"""
import json
import threading
from app.core.llm import chat
from app.auth.users import get_profile, update_profile
from app.core.logger import get_logger

logger = get_logger("profile_learner")

EXTRACT_SYSTEM = """你是一个投资偏好分析助手。分析用户的对话消息，提取其中隐含的投资偏好信号。

只有当消息中有明确的偏好信号时才返回结果，否则返回 null。

返回 JSON 格式（或 null）：
{
  "invest_style": "value|growth|garp|null",
  "risk_level": "conservative|moderate|aggressive|null",
  "invest_horizon": "short|medium|long|null",
  "focus_industries": ["行业1", "行业2"] 或 null,
  "reason": "提取依据（一句话）"
}

判断标准：
- invest_style: 提到"低估值/便宜/巴菲特"→value；"高增速/成长/赛道"→growth；"PEG"→garp
- risk_level: 提到"稳健/不能亏/波动太大"→conservative；"高收益/赌一把"→aggressive
- invest_horizon: 提到"短线/做T"→short；"长期持有/5年以上"→long；"1-3年"→medium
- focus_industries: 提到关注某行业或只看某类股票
- 只有明确表达时才设置，模糊表述返回 null
"""


def _extract_signals(message: str) -> dict | None:
    """从单条消息提取偏好信号"""
    try:
        raw = chat(
            system=EXTRACT_SYSTEM,
            user=f"用户消息：{message}",
            temperature=0.1,
        )
        # 提取 JSON
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group())
        # 过滤掉 null 值
        signals = {k: v for k, v in data.items()
                   if k != 'reason' and v and v != 'null'}
        return signals if signals else None
    except Exception as e:
        logger.debug(f"信号提取失败: {e}")
        return None


def _merge_profile(current: dict, signals: dict) -> dict:
    """
    合并信号到当前画像。
    focus_industries 做累加去重，其他字段直接覆盖。
    """
    updates = {}
    for key, value in signals.items():
        if key == 'focus_industries' and isinstance(value, list):
            existing = current.get('focus_industries') or []
            merged = list(dict.fromkeys(existing + value))  # 去重保序
            if merged != existing:
                updates['focus_industries'] = merged
        elif current.get(key) != value:
            updates[key] = value
    return updates


def learn_from_message(user_id: str, message: str):
    """同步学习，返回是否有更新"""
    signals = _extract_signals(message)
    if not signals:
        return False

    current = get_profile(user_id)
    updates = _merge_profile(current, signals)
    if not updates:
        return False

    update_profile(user_id, **updates)
    logger.info(f"画像更新 user={user_id} updates={updates}")
    return True


def learn_async(user_id: str, message: str):
    """后台异步学习，不阻塞主流程"""
    t = threading.Thread(
        target=learn_from_message, args=(user_id, message), daemon=True
    )
    t.start()
