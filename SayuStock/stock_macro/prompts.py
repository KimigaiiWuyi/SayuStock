"""宏观定调 · 共享提示词片段。

给**低能力模型**看的版本：不讲理论，只给「看什么 → 得出什么 → 怎么做」三段式。
完整框架在 ``SayuStock/skills/macro-regime-analysis/``（技能 + 知识库）。

三份代理 prompt（模拟盘决策 / 持仓分析 / 研究代理）都拼这同一份，避免三处各写一套。
"""

from __future__ import annotations

__all__ = [
    "MACRO_REGIME_OFFENSE",
    "MACRO_REGIME_NEUTRAL",
    "MACRO_REGIME_DEFENSE",
    "MACRO_REGIME_VALUES",
    "MACRO_QUICK_CHECK",
    "MACRO_CHEATSHEET",
    "MACRO_POSITION_RULES",
    "MACRO_REASON_TEMPLATE",
    "normalize_macro_regime",
]

MACRO_REGIME_OFFENSE: str = "进攻"
MACRO_REGIME_NEUTRAL: str = "中性"
MACRO_REGIME_DEFENSE: str = "防御"
MACRO_REGIME_VALUES: tuple[str, ...] = (MACRO_REGIME_OFFENSE, MACRO_REGIME_NEUTRAL, MACRO_REGIME_DEFENSE)

# 模型常见写法 → 三档之一；对不上返回 ""（由硬闸拒绝并提示合法值）
_REGIME_ALIASES: dict[str, str] = {
    "进攻": MACRO_REGIME_OFFENSE,
    "进攻档": MACRO_REGIME_OFFENSE,
    "攻": MACRO_REGIME_OFFENSE,
    "offense": MACRO_REGIME_OFFENSE,
    "offensive": MACRO_REGIME_OFFENSE,
    "risk_on": MACRO_REGIME_OFFENSE,
    "risk-on": MACRO_REGIME_OFFENSE,
    "aggressive": MACRO_REGIME_OFFENSE,
    "中性": MACRO_REGIME_NEUTRAL,
    "中性档": MACRO_REGIME_NEUTRAL,
    "neutral": MACRO_REGIME_NEUTRAL,
    "balanced": MACRO_REGIME_NEUTRAL,
    "mixed": MACRO_REGIME_NEUTRAL,
    "防御": MACRO_REGIME_DEFENSE,
    "防御档": MACRO_REGIME_DEFENSE,
    "防守": MACRO_REGIME_DEFENSE,
    "守": MACRO_REGIME_DEFENSE,
    "defense": MACRO_REGIME_DEFENSE,
    "defensive": MACRO_REGIME_DEFENSE,
    "risk_off": MACRO_REGIME_DEFENSE,
    "risk-off": MACRO_REGIME_DEFENSE,
    "conservative": MACRO_REGIME_DEFENSE,
}


def normalize_macro_regime(raw: object) -> str:
    """把模型写的档位词归一到 进攻/中性/防御；认不出返回空串。"""
    if not isinstance(raw, str):
        return ""
    key = raw.strip().lower()
    if not key:
        return ""
    if key in _REGIME_ALIASES:
        return _REGIME_ALIASES[key]
    for alias, value in _REGIME_ALIASES.items():
        if alias in key:
            return value
    return ""


MACRO_QUICK_CHECK: str = """【宏观三问 · 每轮先答，答不出就不许 buy】
问 1：现在市场上「钱多还是钱少」？
  看 4 个方向即可，不必精确数字：
  - 原油价格：油价大涨 / 站上 100 美元 = 钱少；油价低迷 = 钱多。
  - 长端美债收益率 + 美元指数：一起往上冲 = 全球钱少；一起回落 = 钱多。
  - 国内 10 年期国债收益率：往下走 = 国内宽松（钱多）；往上走 = 收紧。
  - 中国 PPI 环比：连续转正 = 上游在修复、人民币偏强、风格往价值倾斜。
  结论只写一个词：钱多 / 钱少 / 不明。
问 2：A 股现在处于哪一「相」？
  - 利率很低 + 楼市冷 → 「成长相」：科技 / 小微盘占优，低估值价值股只当防守用。
  - 利率上行 + 楼市回暖 → 「价值相」：高股息 / 蓝筹占优，别追高市梦率成长。
  - 两者都不明显 → 「轮动相」：成长与价值来回切，仓位放中间。
问 3：有哪些重大事件正在进行？（必调 macro_event_list）
  对表里每件 open 事件问一句：「这件事还能更坏吗？更坏了双方承受得了吗？」
  - 承受得了、还在加码 → 事件未到顶，按它的 direction 收缩或放开仓位。
  - 承受不了、加码已无意义、当事方口风软化、市场低开高走翻红 → 「政策顶 = 情绪底」，
    允许对被错杀的宽基 / 龙头做试探性反向（单票 ≤5%），reason 写清依据。
  - 表里没有但快讯 / 检索里出现关税 / 战争 / 央行决议 / 油价急变这类大事 →
    先 macro_event_upsert 登记（severity 按影响范围 1~5），再继续。
三问答完 → 定档：进攻 / 中性 / 防御（规则见【三档仓位】），并写一句依据。"""

MACRO_POSITION_RULES: str = """【三档仓位 · 档位决定你能买多少，个股再好也不能越档】
- 进攻档：钱多 + 无 severity≥4 的 risk_off 事件 + 成长相或轮动相
    → 总仓位 ≤80%，单票 ≤15%；可主攻成长 / 科技 / 出口制造。
- 中性档：信号互相矛盾、或问 1 答「不明」
    → 总仓位 ≤60%，单票 ≤10%；成长与价值各配一半。
- 防御档：钱少（油价 >100 或长端美债 + 美元急升）或有 severity≥4 的 risk_off 事件进行中
    → 总仓位 ≤30%，单票 ≤5%；只买高股息 / 公用事业 / 必需消费 / 上游资源，
      不买高市梦率成长、不买刚被事件砸下来第一天的票。
- 任何档位：宏观事件表里有 severity≥4 且 risk_off 的 open 事件时，
  buy 不许标「进攻」——系统硬闸会拒单。
- 档位变差（进攻→防御）时，先减最高估值 / 最高波动的持仓，不是先减盈利最多的。"""

MACRO_CHEATSHEET: str = """【宏观速查 · 为什么这样判（只记结论，不用推导）】
1. 宏观 > 行业 > 个股：信息越宏观越透明。你能拿到最可靠的信息在宏观层，
   所以先定档位、再选行业、最后才挑个股。个股逻辑再好，档位是防御就不重仓。
2. 「钱」不是央行印出来的，是石油 / 上游资源这类关键商品的供给。供给宽 = 钱多，
   供给紧 = 钱少。所以油价是全球流动性的水龙头，PPI 是中国流动性的水龙头。
3. 油价长期低 = 「养肥期」：成长股、贵金属、小微盘吃饱做梦，估值越来越高。
   油价确认长期高位 = 「收割期」：这些高估值资产被大规模抛售。金油比极高时要警惕。
4. 滞胀 = 石油供给收紧的自然结果，不是央行造成的；美联储加息解决不了航道封锁。
   油价 >100 时：减成长、避高市梦率、转向现金流 / 上游资源 / 高股息。
5. 美联储别只看「加息 / 降息」四个字：在美国当前体系下「加息 + 扩表」偏宽松、
   「降息 + 缩表」是最严紧缩。低级模型简化看：长端美债与美元指数同升 = 收紧。
6. A 股是「成长」与「价值」两块拼起来的，底层是楼市和债市。楼市冷 + 利率极低 →
   成长横扫、价值成血包；楼市热 + 利率高 → 价值横扫。成长牛市的下一站是楼市不是价值，
   别在成长牛市里左侧重仓等价值轮动。
7. 中国 PPI 是周期的源头：PPI 环比连续转正 → 上游 / 中上游资产负债表修复 →
   人民币走强 → 利率抬升 → 风格向价值倾斜。「反内卷」提价的是我们有垄断地位的产品，
   是利好不是利空。
8. 银行 / 三桶油 / 电信 / 公用事业这类基础设施股像债券：低 PE 是制度设计，不是低估，
   它们是防御档的底仓，不是进攻档的主攻。牛市主战场在消费、出口制造与成长。
9. 「外需强 + 内需弱」不是通缩，是服务业收缩；关税 / 制裁让外循环受阻时，
   政策会转向扩内需与高质量服务业 → 利好服务消费、好房子物业链等。
10. 汇率三支柱：出口份额 / 利率 / 外储。出口份额大的国家（中国）可以低利率仍稳汇率；
    出口没落 + 不敢加息的国家（日本）汇率易贬难升。日元大波动多是干预，不是趋势反转。
11. 重大事件反转规律：暴涨暴跌的拐点在「不能更坏 / 不能更好」的那一刻。
    关税加到互相不可能做生意的税率 = 政策顶 = 情绪底；油价涨到全球承受不了 = 顶；
    冲突升级到再升级就是不可能的地面战 = 顶。只对「人为可逆」的政策 / 地缘事件用这一条，
    对产业内卷（供需没变的商品跌价）不适用。
12. 两种被验证的赢法：价值股做左侧（有股息硬指标 + 垄断护城河，跌了分批买、拿得住）；
    成长股做右侧（产业爆发 + 龙头浮现后再上车）。别追价值股右侧，别赌成长股左侧。"""

MACRO_REASON_TEMPLATE: str = """【宏观结论写法 · 低成本、可被统计】
每条 decision reason 开头固定一段（20~40 字）：
  「宏观:{档位}|{相}|{一句依据}」
  例：宏观:防御|成长相|油价站上109且红海航道受阻,中美关税战open(sev4)
  例：宏观:进攻|成长相|美债美元双回落,PPI环比连正,表内无risk_off大事
buy 的 indicators / snapshot JSON 顶层必须带：
  "macro_regime": "进攻" | "中性" | "防御"
  "macro_note": "同上那句依据（≥8 字）"
  缺任一字段 → 系统硬闸拒绝落库，并告诉你缺什么。"""
