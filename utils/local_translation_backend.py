"""Offline CTranslate2/OPUS-MT adapter, loaded only when translation is requested."""

from __future__ import annotations

from pathlib import Path
import re


MODEL_DIR = Path(__file__).resolve().parents[1] / "assets" / "translation" / "opus-mt-en-zh"
_TRANSLATOR = None
_SOURCE_MODEL = None
_TARGET_MODEL = None
_ACADEMIC_GLOSSARY = {
    "soc": "土壤有机碳（SOC）",
    "maoc": "矿物结合态有机碳（MAOC）",
    "poc": "颗粒有机碳（POC）",
    "soil organic carbon": "土壤有机碳",
    "soil organic carbon mapping": "土壤有机碳制图",
    "soil carbon": "土壤碳",
    "carbon sequestration": "碳固存",
    "digital soil mapping": "数字土壤制图",
    "remote sensing": "遥感",
    "machine learning": "机器学习",
    "deep learning": "深度学习",
    "land use": "土地利用",
    "land cover": "土地覆被",
    "soil microbial community": "土壤微生物群落",
    "mineral-associated organic carbon": "矿物结合态有机碳",
    "particulate organic carbon": "颗粒有机碳",
    "environmental covariates": "环境协变量",
    "cropland conversion": "耕地转换",
    "cropping system": "种植制度",
    "interaction effect": "交互效应",
    "land use change": "土地利用变化",
    "land-use change": "土地利用变化",
    "threshold effect": "阈值效应",
    "subsidence": "地面沉降",
    "coal mining": "煤炭开采",
    "partial dependence plot": "偏依赖图",
    "effective range": "有效作用范围",
    "high-standard farmland": "高标准农田",
    "hot droughts": "高温干旱",
    "soil inorganic carbon": "土壤无机碳",
    "soil ph": "土壤 pH",
    "soil texture": "土壤质地",
    "carbon stabilization": "碳稳定化",
    "geospatial": "地理空间",
    "soil aggregate": "土壤团聚体",
    "spatial prediction": "空间预测",
    "microbial": "微生物",
    "mineral": "矿物",
    "green manure": "绿肥",
    "tea plantation": "茶园",
    "photovoltaic": "光伏",
    "erodibility": "可蚀性",
    "non-point source pollution": "面源污染",
    "moss": "苔藓",
    "loss-on-ignition": "烧失量",
    "conversion factors": "转换系数",
    "soil health": "土壤健康",
    "hydraulic conductivity": "水力传导度",
    "vermicompost": "蚯蚓堆肥",
    "straw return": "秸秆还田",
    "biochar": "生物炭",
    "heavy metal": "重金属",
    "preterm birth": "早产",
    "food innovation": "食品创新",
    "molecular biology": "分子生物学",
}


def glossary_translation(text: str) -> str:
    return _ACADEMIC_GLOSSARY.get(str(text or "").strip().casefold(), "")


def model_available() -> bool:
    return all((MODEL_DIR / name).is_file() for name in ("model.bin", "source.spm", "target.spm"))


def _chunks(text: str, limit: int = 420) -> list[str]:
    paragraphs = [value.strip() for value in re.split(r"\n+", str(text or "")) if value.strip()]
    result: list[str] = []
    for paragraph in paragraphs:
        sentences = [value.strip() for value in re.split(r"(?<=[.!?;。！？；])\s+", paragraph) if value.strip()]
        current = ""
        for sentence in sentences or [paragraph]:
            if current and len(current) + 1 + len(sentence) > limit:
                result.append(current)
                current = ""
            if len(sentence) > limit:
                if current:
                    result.append(current)
                    current = ""
                result.extend(sentence[index : index + limit] for index in range(0, len(sentence), limit))
            else:
                current = (current + " " + sentence).strip()
        if current:
            result.append(current)
    return result or ([str(text).strip()] if str(text).strip() else [])


def _runtime():
    global _TRANSLATOR, _SOURCE_MODEL, _TARGET_MODEL
    if _TRANSLATOR is not None:
        return _TRANSLATOR, _SOURCE_MODEL, _TARGET_MODEL
    if not model_available():
        raise RuntimeError("内置离线翻译模型缺失")
    try:
        import ctranslate2
        import sentencepiece as spm
    except ImportError as error:
        raise RuntimeError("内置离线翻译组件未完整安装") from error
    # SentencePiece's Windows filename bridge can reject valid non-ASCII
    # paths.  Loading the bytes is both portable and packaging-safe.
    _SOURCE_MODEL = spm.SentencePieceProcessor(model_proto=(MODEL_DIR / "source.spm").read_bytes())
    _TARGET_MODEL = spm.SentencePieceProcessor(model_proto=(MODEL_DIR / "target.spm").read_bytes())
    _TRANSLATOR = ctranslate2.Translator(str(MODEL_DIR), device="cpu", compute_type="int8")
    return _TRANSLATOR, _SOURCE_MODEL, _TARGET_MODEL


def translate_en_to_zh(texts: list[str]) -> list[str]:
    """Translate English with the bundled Helsinki-NLP OPUS-MT model."""
    translator, source_model, target_model = _runtime()
    output: list[str] = []
    for text in texts:
        exact = glossary_translation(text)
        if exact:
            output.append(exact)
            continue
        chunks = _chunks(text)
        tokenized = [source_model.encode(chunk, out_type=str) + ["</s>"] for chunk in chunks]
        max_length = max(64, min(256, max((len(value) for value in tokenized), default=1) * 4))
        batches = translator.translate_batch(tokenized, beam_size=4, max_decoding_length=max_length)
        output.append("\n".join(target_model.decode(result.hypotheses[0]).strip() for result in batches).strip())
    return output
