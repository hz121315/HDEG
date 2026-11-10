# -*- coding: utf-8 -*-
"""
世界观分析引擎

分析日语文本的世界观特征：
- 文末语气词（ぞ、わ、ぜ、な、ね 等）
- 男性/女性用语
- 敬语级别
- 口癖/语气模式
"""

from __future__ import annotations
import re


# ==================== 语气词/口癖词典 ====================

# 文末助词及其性别倾向
SENTENCE_END_PARTICLES: dict[str, dict] = {
    'ぞ': {'gender': 'male', 'tone': 'assertive', 'category': 'sentence_end'},
    'ぜ': {'gender': 'male', 'tone': 'casual_rough', 'category': 'sentence_end'},
    'だぜ': {'gender': 'male', 'tone': 'casual_rough', 'category': 'sentence_end'},
    'だぞ': {'gender': 'male', 'tone': 'assertive', 'category': 'sentence_end'},
    'な': {'gender': 'neutral', 'tone': 'self_reflection', 'category': 'sentence_end'},
    'なあ': {'gender': 'male_lean', 'tone': 'emotional', 'category': 'sentence_end'},
    'ね': {'gender': 'neutral', 'tone': 'confirmation', 'category': 'sentence_end'},
    'ねえ': {'gender': 'neutral', 'tone': 'emotional_confirmation', 'category': 'sentence_end'},
    'よ': {'gender': 'neutral', 'tone': 'informative', 'category': 'sentence_end'},
    'よね': {'gender': 'neutral', 'tone': 'seeking_agreement', 'category': 'sentence_end'},
    'さ': {'gender': 'male_lean', 'tone': 'casual', 'category': 'sentence_end'},
    'わ': {'gender': 'female', 'tone': 'softening', 'category': 'sentence_end'},
    'わね': {'gender': 'female', 'tone': 'soft_confirmation', 'category': 'sentence_end'},
    'わよ': {'gender': 'female', 'tone': 'soft_assertion', 'category': 'sentence_end'},
    'の': {'gender': 'female', 'tone': 'explanatory', 'category': 'sentence_end'},
    'のね': {'gender': 'female', 'tone': 'explanatory_confirmation', 'category': 'sentence_end'},
    'のよ': {'gender': 'female', 'tone': 'explanatory_assertion', 'category': 'sentence_end'},
    'かしら': {'gender': 'female', 'tone': 'wondering', 'category': 'sentence_end'},
    'かい': {'gender': 'male', 'tone': 'question_casual', 'category': 'sentence_end'},
    'だい': {'gender': 'male', 'tone': 'question_casual', 'category': 'sentence_end'},
    'じゃん': {'gender': 'neutral', 'tone': 'assertion_casual', 'category': 'sentence_end'},
    'だろう': {'gender': 'male', 'tone': 'conjecture', 'category': 'sentence_end'},
    'でしょう': {'gender': 'neutral', 'tone': 'conjecture_polite', 'category': 'sentence_end'},
}

# 一人称代词
FIRST_PERSON_PRONOUNS: dict[str, dict] = {
    '俺': {'gender': 'male', 'formality': 'casual', 'tone': 'rough'},
    '僕': {'gender': 'male', 'formality': 'polite_casual', 'tone': 'soft_young'},
    '私': {'gender': 'neutral_formal', 'formality': 'formal', 'tone': 'neutral'},
    'あたし': {'gender': 'female', 'formality': 'casual', 'tone': 'cute'},
    'あたい': {'gender': 'female', 'formality': 'casual', 'tone': 'tough'},
    'わし': {'gender': 'neutral', 'formality': 'casual', 'tone': 'elderly'},
    'わたくし': {'gender': 'neutral', 'formality': 'very_formal', 'tone': 'humble'},
    '俺様': {'gender': 'male', 'formality': 'arrogant', 'tone': 'dominant'},
    '自分': {'gender': 'male_lean', 'formality': 'neutral', 'tone': 'stoic'},
    'うち': {'gender': 'female', 'formality': 'casual', 'tone': 'dialect_female'},
}

# 二人称代词
SECOND_PERSON_PRONOUNS: dict[str, dict] = {
    'お前': {'gender': 'male', 'formality': 'rough', 'tone': 'rough_intimate'},
    '君': {'gender': 'male', 'formality': 'casual_polite', 'tone': 'superior'},
    'あなた': {'gender': 'neutral', 'formality': 'neutral', 'tone': 'neutral'},
    'あんた': {'gender': 'neutral', 'formality': 'casual', 'tone': 'rough_familiar'},
    '貴様': {'gender': 'male', 'formality': 'very_rough', 'tone': 'hostile'},
    'てめえ': {'gender': 'male', 'formality': 'very_rough', 'tone': 'very_hostile'},
}

# 敬语程度标识
KEIGO_PATTERNS: dict[str, dict] = {
    'です': {'level': 'teineigo', 'type': 'copula'},
    'ます': {'level': 'teineigo', 'type': 'verb_ending'},
    'ございます': {'level': 'teichogo', 'type': 'extra_polite'},
    'いらっしゃる': {'level': 'sonkeigo', 'type': 'honorific'},
    'おっしゃる': {'level': 'sonkeigo', 'type': 'honorific_say'},
    'なさる': {'level': 'sonkeigo', 'type': 'honorific_do'},
    '召し上がる': {'level': 'sonkeigo', 'type': 'honorific_eat'},
    '申す': {'level': 'kenjogo', 'type': 'humble_say'},
    'いたす': {'level': 'kenjogo', 'type': 'humble_do'},
    'おる': {'level': 'kenjogo', 'type': 'humble_be'},
    'いただく': {'level': 'kenjogo', 'type': 'humble_receive'},
    '差し上げる': {'level': 'kenjogo', 'type': 'humble_give'},
    '参る': {'level': 'kenjogo', 'type': 'humble_go'},
}

# ==================== 世界观文件路径 ====================

# ==================== 从目录加载世界观 ====================

def load_worldview_from_dir(work_dir: "Path") -> tuple[dict | None, list]:
    """从工作目录中加载世界观/角色/场景设定

    返回:
        (worldview_dict, source_files)
        worldview_dict: {worldview, characters, scene} 或 None
        source_files: 找到的文件绝对路径列表
    """
    import json as _json

    worldview: dict = {
        "worldview": "",
        "characters": {},
        "scene": "",
    }
    has_any = False
    sources: list = []  # 收集所有找到的文件

    def _add_source(fpath):
        p = fpath.absolute()
        if p not in sources:
            sources.append(p)

    # 搜索世界观文件
    wv_candidates = ['worldview.json', 'world_setting.json', 'world.json',
                     '世界观.json', '世界观.txt', 'worldview.txt']
    for name in wv_candidates:
        for fpath in work_dir.rglob(name):
            try:
                content = fpath.read_text(encoding='utf-8').strip()
                if content:
                    if fpath.suffix == '.json':
                        data = _json.loads(content)
                        if isinstance(data, dict):
                            worldview['worldview'] = data.get('worldview') or data.get('description') or data.get('text') or _json.dumps(data, ensure_ascii=False)
                        elif isinstance(data, str):
                            worldview['worldview'] = data
                    else:
                        worldview['worldview'] = content
                    has_any = True
                    _add_source(fpath)
                    print(f"  [世界观] 加载文件: {fpath.absolute()} ({len(worldview['worldview'])} 字符)")
            except Exception as e:
                print(f"  [世界观] 读取 {fpath.absolute()} 失败: {e}")

    # 搜索角色文件
    char_candidates = ['characters.json', 'char.json', '角色.json', 'characters.txt', '角色.txt']
    for name in char_candidates:
        for fpath in work_dir.rglob(name):
            try:
                content = fpath.read_text(encoding='utf-8').strip()
                if fpath.suffix == '.json':
                    data = _json.loads(content)
                    if isinstance(data, dict):
                        worldview['characters'].update(data)
                else:
                    for line in content.split('\n'):
                        line = line.strip()
                        if ':' in line or '：' in line:
                            sep = ':' if ':' in line else '：'
                            name, desc = line.split(sep, 1)
                            worldview['characters'][name.strip()] = desc.strip()
                has_any = True
                _add_source(fpath)
                print(f"  [世界观] 加载角色: {fpath.absolute()} ({len(worldview['characters'])} 个角色)")
            except Exception as e:
                print(f"  [世界观] 读取角色 {fpath.absolute()} 失败: {e}")

    # 搜索场景文件
    scene_candidates = ['scene.json', 'setting.json', '场景.json', 'scene.txt', '场景.txt']
    for name in scene_candidates:
        for fpath in work_dir.rglob(name):
            try:
                content = fpath.read_text(encoding='utf-8').strip()
                if fpath.suffix == '.json':
                    data = _json.loads(content)
                    if isinstance(data, dict):
                        worldview['scene'] = data.get('scene') or data.get('description') or data.get('text') or _json.dumps(data, ensure_ascii=False)
                    elif isinstance(data, str):
                        worldview['scene'] = data
                else:
                    worldview['scene'] = content
                has_any = True
                _add_source(fpath)
                print(f"  [世界观] 加载场景: {fpath.absolute()} ({len(worldview['scene'])} 字符)")
            except Exception as e:
                print(f"  [世界观] 读取场景 {fpath.absolute()} 失败: {e}")

    # 也搜索 .worldview.json（隐藏文件命名）
    for fpath in work_dir.rglob('.worldview.json'):
        try:
            content = fpath.read_text(encoding='utf-8').strip()
            if content:
                data = _json.loads(content)
                if isinstance(data, dict):
                    if not worldview['worldview']:
                        worldview['worldview'] = data.get('worldview', '')
                    chars = data.get('characters', [])
                    if isinstance(chars, list):
                        for char in chars:
                            if isinstance(char, dict) and 'name' in char:
                                name = char['name']
                                desc = char.get('personality', '') or char.get('role', '')
                                if name not in worldview['characters']:
                                    worldview['characters'][name] = desc
                    elif isinstance(chars, dict):
                        for name, desc in chars.items():
                            if name not in worldview['characters']:
                                worldview['characters'][name] = str(desc)
                    if not worldview['scene']:
                        worldview['scene'] = data.get('scene', '')
                has_any = True
                _add_source(fpath)
                print(f"  [世界观] 加载文件: {fpath.absolute()}")
        except Exception as e:
            print(f"  [世界观] 读取 {fpath.absolute()} 失败: {e}")

    return (worldview if has_any else None, sources)

def get_worldview_path(work_dir: "Path") -> "Path":
    """获取世界观文件路径"""
    from pathlib import Path
    return work_dir / '.worldview.json'


def load_worldview(work_dir: "Path") -> dict:
    """加载已保存的世界观（从 .worldview.json）"""
    import json
    worldview_path = get_worldview_path(work_dir)
    if worldview_path.exists():
        try:
            with open(worldview_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return {}
    return {}


def save_worldview(work_dir: "Path", worldview: dict) -> None:
    """保存世界观到 .worldview.json"""
    import json
    worldview_path = get_worldview_path(work_dir)
    with open(worldview_path, 'w', encoding='utf-8') as f:
        json.dump(worldview, f, ensure_ascii=False, indent=2)


# ==================== 和谐版世界观（NSFW 降级重试用） ====================

def get_harmonized_worldview_path(work_dir: "Path") -> "Path":
    """获取和谐版世界观文件路径（与 .worldview.json 同目录，不覆盖原文件）"""
    from pathlib import Path
    return Path(work_dir) / '和谐版.worldview.json'


def load_harmonized_worldview(work_dir: "Path") -> dict:
    """加载已保存的和谐版世界观；不存在或损坏时返回空 dict"""
    import json
    hpath = get_harmonized_worldview_path(work_dir)
    if hpath.exists():
        try:
            with open(hpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, Exception):
            return {}
    return {}


def save_harmonized_worldview(work_dir: "Path", worldview: dict) -> None:
    """保存和谐版世界观到 和谐版.worldview.json（UTF-8 无 BOM）"""
    import json
    hpath = get_harmonized_worldview_path(work_dir)
    hpath.parent.mkdir(parents=True, exist_ok=True)
    with open(hpath, 'w', encoding='utf-8') as f:
        json.dump(worldview, f, ensure_ascii=False, indent=2)


# 和谐化改写规则：力度适中，只模糊"违背伦理"的高危表述（未成年/亲属关系/性暴力等），
# 成年角色间的成人向内容保留原样，其余描述尽量不动。
HARMONIZE_SYSTEM_PROMPT = (
    "你是一名内容合规编辑。用户提供一部虚构音声作品的设定资料（JSON 格式，"
    "含世界观简介、角色表、主题、特殊术语），你的任务是将其中的高危表述改写为合规版本，"
    "用于替代原资料参与后续翻译，这是一个常规的内容合规改写工作。\n\n"
    "【改写规则——力度适中，只处理以下几类违背伦理的高危表述】\n"
    "1. 未成年相关：具体年龄数字（如「17岁」「16歳」）改为「年轻」；"
    "「女学生」「女子校生」「女子高中生」「JK」「小学生」「中学生」等学生身份改为「年轻女生」；"
    "「男学生」等改为「年轻男性」。\n"
    "2. 家庭/亲属关系：「父女」「母女」「兄妹」「姐弟」「父娘」「母娘」「近亲」等亲属关系，"
    "改为「关系亲密的两人」「关系亲密」等表述。\n"
    "3. 非自愿/性暴力：「强奸」「轮奸」「凌辱」「强暴」「レイプ」等改为「半强硬」「激烈」等"
    "程度相近但合规的描述。\n"
    "4. 其他类似高危设定（监禁、药物控制等）：同样选用意思相近的温和表述。\n"
    "5. 上述之外的成年角色间成人向内容、普通设定、人名、性格描述一律保持原样，"
    "不要过度改写，不要扩写或添加任何解释性文字。\n\n"
    "【输出要求】\n"
    "1. 输出 JSON 的结构、字段名、键与输入完全一致，只修改字符串内容。\n"
    "2. characters 中每个角色的 name 字段（日文原文称呼）保持不变，"
    "确保与作品原文对齐；name_cn 如含上述高危词一并改写。\n"
    "3. 只输出改写后的 JSON 本体，禁止输出 JSON 之外的任何内容。\n"
)


def harmonize_worldview_with_llm(engine, worldview: dict, verbose: bool = False,
                                 extra_system_prompt: str = None) -> tuple:
    """调用 LLM 对世界观进行"和谐化"改写（NSFW 降级重试用）。

    参数:
        engine: 带 call_api(system_prompt, user_prompt, override_gen_params=...) 的引擎
        worldview: 原始世界观字典（会剥离 _source 等内部字段后发送）
        verbose: 是否打印调试信息
        extra_system_prompt: 用户自定义的前置 prompt（config prompts.harmonize_system_prompt_file
            的文件内容），拼接在内置规则之前——用于补充内置规则未覆盖的高危类别
            （如血腥暴力）。为空时纯用内置规则。

    返回:
        (和谐后的世界观 dict, token_stats)；失败（API 异常/JSON 解析失败/结构校验失败）返回 (None, token_stats)
    """
    import json as _json

    if not worldview:
        return {}, {}

    # 剥离内部元数据字段，只发送设定内容
    payload = {k: v for k, v in worldview.items() if not k.startswith('_')}
    user_prompt = _json.dumps(payload, ensure_ascii=False, indent=2)

    # 自定义前置 prompt 在前（优先级高），内置规则在后兜底输出格式约束
    if extra_system_prompt and extra_system_prompt.strip():
        system_prompt = f"{extra_system_prompt.strip()}\n\n{HARMONIZE_SYSTEM_PROMPT}"
    else:
        system_prompt = HARMONIZE_SYSTEM_PROMPT

    token_stats: dict = {}
    try:
        raw_output, token_stats = engine.call_api(
            system_prompt, user_prompt,
            override_gen_params={'temperature': 0.1},
        )
    except Exception as e:
        print(f"    ⚠ 世界观和谐化请求失败: {e}", flush=True)
        return None, token_stats

    if not raw_output or not raw_output.strip():
        print("    ⚠ 世界观和谐化返回为空", flush=True)
        return None, token_stats

    json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
    if not json_match:
        print(f"    ⚠ 世界观和谐化未返回 JSON（前200字: {raw_output[:200]}）", flush=True)
        return None, token_stats
    try:
        result = _json.loads(json_match.group())
    except _json.JSONDecodeError as e:
        print(f"    ⚠ 世界观和谐化 JSON 解析错误: {e}", flush=True)
        return None, token_stats

    if not isinstance(result, dict):
        print("    ⚠ 世界观和谐化结果不是 JSON 对象", flush=True)
        return None, token_stats

    # 结构校验：至少要保留 characters（后续降级级别依赖它）；空结果视为失败
    if not result.get('characters') and not result.get('worldview'):
        print("    ⚠ 世界观和谐化结果为空内容，视为失败", flush=True)
        return None, token_stats

    if verbose:
        print(f"    [DEBUG] 和谐化完成: {len(result.get('characters', []))} 个角色", flush=True)
    return result, token_stats


# ==================== 世界观 Prompt 构建 ====================

def build_worldview_prompt(worldview: dict) -> str:
    """将世界观构建为 prompt 附加内容

    兼容两种 characters 格式：
    - 数组: [{name, name_cn, role, personality}]
    - 字典: {name: description}
    """
    if not worldview:
        return ""

    lines = ["\n【作品背景（请在翻译时参考以下信息，保持设定一致）】"]

    if worldview.get('worldview'):
        lines.append(f"  世界观: {worldview['worldview']}")

    # 主题（影响翻译语气和风格）
    themes = worldview.get('themes', [])
    if themes:
        themes_str = '、'.join(themes) if isinstance(themes, list) else str(themes)
        lines.append(f"  作品主题: {themes_str}")

    characters = worldview.get('characters', [])
    if characters:
        lines.append("  角色:")
        if isinstance(characters, list):
            for char in characters[:20]:
                name = char.get('name', '') if isinstance(char, dict) else str(char)
                name_cn = char.get('name_cn', '') if isinstance(char, dict) else ''
                role = char.get('role', '') if isinstance(char, dict) else ''
                personality = char.get('personality', '') if isinstance(char, dict) else ''
                relationship = char.get('relationship', '') if isinstance(char, dict) else ''
                if name:
                    char_info = f"    {name}"
                    if name_cn:
                        char_info += f"（中文: {name_cn}）"
                    if role:
                        char_info += f" [{role}]"
                    if personality:
                        char_info += f" — 性格: {personality}"
                    if relationship:
                        char_info += f" — 关系: {relationship}"
                    lines.append(char_info)
        elif isinstance(characters, dict):
            for name, desc in list(characters.items())[:20]:
                lines.append(f"    {name}: {desc}")

    # 特殊设定术语 —— 提升为翻译硬约束
    special_terms = worldview.get('special_terms', {})
    if special_terms:
        lines.append("")
        lines.append("  【特殊设定术语——翻译时必须严格遵守以下译法，确保全文一致】")
        for term, desc in list(special_terms.items())[:20]:
            lines.append(f"    「{term}」→ 译作「{desc}」")

    lines.append("【背景信息结束】\n")
    return "\n".join(lines)


# ==================== LLM 世界观分析 ====================

WORLDVIEW_ANALYSIS_PROMPT = (
    "以下是日文成人向虚构音声（ASMR/RJ作品）的ASR转录文本样本。\n"
    "请分析这些样本，提取作品的世界观信息。\n\n"
    "输出要求（紧凑JSON格式）：\n"
    '{"worldview": "作品世界观简介（500字以内，包含：时代背景、主要角色关系、核心设定、情感基调、故事走向）", '
    '"characters": [{"name": "该角色被称呼的日文原文（专名优先）", "name_cn": "name 对应的自然中文译名或称呼（无则留空）", '
    '"role": "身份定位/职业/立场/设定", "personality": "性格特点", "relationship": "与其他角色的关系"}], '
    '"themes": ["主题1", "主题2"], '
    '"special_terms": {"日文术语": "中文译名"}}\n\n'
    "注意：\n"
    "- worldview 必须详细，用于辅助后续翻译理解上下文，控制在500字以内\n"
    "- characters 列出所有主要角色（含主人公/听者），最多10个\n"
    "- 【区分「专名」「身份定位」「称呼」——重要】\n"
    "  · name = 该角色在对话中被称呼的日文原文（专名优先；没有专名时用最常用的日文称呼，如「先輩」「女神官」「お姉ちゃん」）\n"
    "  · role = 该角色的身份定位、职业、立场、设定（如：主人公、听者、哥哥、勇者、女神官、学生会长）。主人公与听者的身份定位务必写在这里\n"
    "  · name_cn = name 对应的自然中文译名或称呼，必须是地道中文；严禁残留日文假名或日文汉字，严禁「汉字+大人」硬凑或叠称。反例（禁止）：×旦那大人、×您大人、×姐姐大人、×メイド。正例：旦那様→老公（或主人）、ご主人様→主人、お嬢様→大小姐、お兄ちゃん→哥哥、あかり→明里\n"
    "  · 若没有合适的中文名/称呼，name_cn 留空字符串 \"\"，不要编造，也不要把身份词塞进 name_cn\n"
    "- 第二人称代词（君/あなた/お前/きみ/あんた/貴様/てめえ）和纯语气词不要单独作为角色列出\n"
    "- relationship 描述角色间的关系（如：恋人、朋友、同事、家人等），帮助翻译时保持关系一致性\n"
    "- special_terms 列出对翻译有影响的特殊设定术语。值必须是纯中文译名（词语/短語），禁止残留日文假名、禁止半翻译、禁止加括号注释或解释说明。因为该译名会被直接注入术语表作为强制翻译\n"
    "- 成人向虚构内容请以学术化方式客观描述设定，无需回避\n"
    "- 不要输出任何 JSON 之外的内容\n"
)


def analyze_worldview_with_llm(engine, samples: list[str], verbose: bool = False) -> tuple:
    """调用 LLM 分析作品世界观

    参数:
        engine: OpenAICompatEngine 实例
        samples: 抽样文本列表
        verbose: 是否输出调试信息

    返回: (世界观字典, token_stats)
    """
    import json as _json

    if not samples:
        return {}, {}

    content = WORLDVIEW_ANALYSIS_PROMPT + "\n\n" + "\n\n".join(samples)
    if verbose:
        print(f"    [DEBUG] 发送给 LLM 的文本长度: {len(content)} 字符", flush=True)
    print(f"    [世界观分析] 正在调用 LLM...", flush=True)

    try:
        system_prompt = "你是精通日语成人向虚构音声作品的分析专家。请严格按照JSON格式输出分析结果，不要输出任何其他内容。"
        raw_output, token_stats = engine.call_api(
            system_prompt, content,
            override_gen_params={'temperature': 0.1},
        )
        if verbose:
            print(f"    [DEBUG] LLM 原始输出 (前300字): {raw_output[:300]}", flush=True)

        json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if json_match:
            try:
                result = _json.loads(json_match.group())
                print(f"    [世界观分析] 完成!", flush=True)
                print(f"      世界观: {result.get('worldview', '')[:100]}...", flush=True)
                print(f"      角色: {len(result.get('characters', []))} 个", flush=True)
                return result, token_stats
            except _json.JSONDecodeError as e:
                print(f"    ⚠ JSON 解析错误: {e}", flush=True)
                return {}, {}
        else:
            print(f"    ⚠ 未找到 JSON 格式内容", flush=True)
            return {}, {}

    except Exception as e:
        print(f"    ⚠ 世界观分析失败: {e}", flush=True)
        return {}, {}


# ==================== FreeTalk 检测 ====================

FREETALK_KEYWORDS = {
    'freetalk', 'free_talk', 'free talk',
    'フリートーク', 'ふりーとーく', 'フリトク',
    'フリートク', 'あとがき', 'アトガキ',
    'おまけ', 'オマケ', 'bonus', 'omake',
    '特典', 'とくてん', 'tokuten',
    'インタビュー', 'interview',
    'コメント', 'comment',
}

POPULAR_ASMR_CV_NAMES = {
    '陽向葵ゅか', 'ひなたゆか', 'hinata', 'ヒナタ',
    '柚木つばめ', 'ゆのきつばめ', 'tsubame', 'ツバメ',
    '涼花みなせ', 'すずかみなせ', 'minase', 'ミナセ',
    '御子柴泉', 'みこしばいずみ', 'izumi', 'イズミ',
    'みもりあいの', 'mimori', 'ミモリ',
    '餅梨あむ', 'もちりしあむ', 'am', 'アム',
    '乙倉ゆい', 'おとくらゆい', 'yui', 'ユイ',
    '雲八はち', 'くもぱちはち', 'hachi', 'ハチ',
    '逢坂成美', 'おうさかなるみ', 'narumi', 'ナルミ',
    '大山チロル', 'おおやまちろる', 'tirol', 'チロル',
    '恋鈴桃歌', 'こすずももか', 'momoka', 'モモカ',
    '秋野かえで', 'あきのかえで', 'kaede', 'カエデ',
    '山田じぇみ子', 'やまだじぇみこ', 'jemiko', 'ジェミ子',
    '小花衣こっこ', 'こばなごろっこ', 'kokko', 'コッコ',
    '秋山はるる', 'あきやまはるる', 'haruru', 'ハルル',
    '藤村莉央', 'ふじむらりお', 'rio', 'リオ',
    '浅木式', 'あさきしき', 'shiki', 'シキ',
    '西瓜すいか', 'すいか', 'suika', 'スイカ',
    '天知遥', 'あまちはるか', 'haruka', 'ハルカ',
    '高梨はなみ', 'たかなしはなみ', 'hanami', 'ハナミ',
    '分倍河原シホ', 'ぶばいがわらしほ', 'shiho', 'シホ',
    '伊ヶ崎綾香', 'いがさきあやか', 'ayaka', 'アヤカ',
    '海音ミヅチ', 'かいおんみづち', 'mizuchi', 'ミヅチ',
    'こまる', 'komaru', 'コマル',
    '涼貴涼', 'すきたきりょう', 'ryo', 'リョウ',
    'こやまはる', 'koyama', 'ハル',
    '一之瀬りと', 'いちのせりと', 'rito', 'リト',
    'そらまめ。', 'soramame', 'ソラマメ',
}


def is_freetalk_context(path: "Path") -> tuple[bool, str | None]:
    """检测路径是否为 freetalk 或包含热门CV名称

    返回: (is_freetalk, cv_name_or_None)
    """
    path_str = str(path).lower()
    path_parts = path.parts if hasattr(path, 'parts') else str(path).replace('\\', '/').split('/')

    for keyword in FREETALK_KEYWORDS:
        if keyword.lower() in path_str:
            return True, None

    for part in path_parts:
        for cv_name in POPULAR_ASMR_CV_NAMES:
            if len(cv_name) < 3:
                continue
            pattern = re.compile(
                r'(^|[\s\_\-\(\)（）\[\]「」『』【】])' + re.escape(cv_name) + r'($|[\s\_\-\(\)（）\[\]「」『』【】])')
            if pattern.search(part, re.IGNORECASE):
                return True, cv_name

    return False, None
