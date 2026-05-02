"""
从对话中自动学习用户投资偏好，异步更新用户画像。
"""
import json
import threading
from app.core.llm import chat
from app.auth.users import get_profile, update_profile
from app.core.logger import get_logger

logger = get_logger("profile_learner")

# LLM 提示词：从用户消息中提取结构化投资偏好信号
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


def _extract_signals(user_message: str) -> dict | None:
    """
    调用 LLM 从单条用户消息中提取投资偏好信号。

    参数:
        user_message: 用户的原始输入文本

    返回:
        包含偏好信号的字典，无有效信号时返回 None
    """
    try:
        raw_response = chat(
            system=EXTRACT_SYSTEM,
            user=f"用户消息：{user_message}",
            temperature=0.1,  # 低温度确保结构化输出稳定
        )
        # 从 LLM 响应中提取 JSON 片段（响应可能包含额外解释文字）
        import re
        json_match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if not json_match:
            return None
        parsed_data = json.loads(json_match.group())
        # 过滤掉 null 值和 reason 字段，只保留有效的偏好信号
        signals = {
            key: value for key, value in parsed_data.items()
            if key != 'reason' and value and value != 'null'
        }
        return signals if signals else None
    except Exception as error:
        logger.debug(f"信号提取失败: {error}")
        return None


def _merge_profile(current_profile: dict, new_signals: dict) -> dict:
    """
    将新提取的偏好信号合并到当前用户画像，返回实际发生变化的字段。

    合并策略：
    - focus_industries：累加去重（用户关注的行业只增不减）
    - 其他字段：直接覆盖（以最新表达为准）

    参数:
        current_profile: 数据库中当前的用户画像
        new_signals:     本次提取到的偏好信号

    返回:
        实际需要更新的字段字典（无变化则为空字典）
    """
    updates = {}
    for key, new_value in new_signals.items():
        if key == 'focus_industries' and isinstance(new_value, list):
            existing_industries = current_profile.get('focus_industries') or []
            # 使用 dict.fromkeys 去重并保持原有顺序
            merged_industries = list(dict.fromkeys(existing_industries + new_value))
            if merged_industries != existing_industries:
                updates['focus_industries'] = merged_industries
        elif current_profile.get(key) != new_value:
            # 只记录真正变化的字段，避免无意义的数据库写入
            updates[key] = new_value
    return updates


def learn_from_message(user_id: str, message: str):
    """
    从单条消息中同步学习用户偏好并更新画像。

    参数:
        user_id: 用户唯一标识
        message: 用户消息文本

    返回:
        True 表示画像有更新，False 表示无变化
    """
    extracted_signals = _extract_signals(message)
    if not extracted_signals:
        return False

    current_profile = get_profile(user_id)
    profile_updates = _merge_profile(current_profile, extracted_signals)
    if not profile_updates:
        return False

    update_profile(user_id, **profile_updates)
    logger.info(f"画像更新 user={user_id} updates={profile_updates}")
    return True


def learn_async(user_id: str, message: str):
    """
    在后台线程中异步学习用户偏好，不阻塞主流程响应。

    参数:
        user_id: 用户唯一标识
        message: 用户消息文本
    """
    background_thread = threading.Thread(
        target=learn_from_message, args=(user_id, message), daemon=True
    )
    background_thread.start()
