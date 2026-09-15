"""Curated starter journals for land science and closely related research."""

from __future__ import annotations

from copy import deepcopy


# The catalogue is intentionally compact: it covers the main land-use, soil,
# remote-sensing and environmental outlets a land/SOC researcher is likely to
# need, without turning a personal library into an indiscriminate mega-list.
LAND_SCIENCE_JOURNALS = [
    {"name": "Land Use Policy", "publisher": "Elsevier", "issn": "0264-8377", "fields": ["土地利用", "土地政策", "区域发展"], "frontier_priority": "必看"},
    {"name": "Journal of Land Use Science", "publisher": "Taylor & Francis", "issn": "1747-423X", "fields": ["土地利用", "土地变化", "空间规划"], "frontier_priority": "关注"},
    {"name": "Land Degradation & Development", "publisher": "Wiley", "issn": "1085-3278", "fields": ["土地退化", "生态恢复", "可持续土地管理"], "frontier_priority": "必看"},
    {"name": "Land", "publisher": "MDPI", "issn": "2073-445X", "fields": ["土地科学", "土地利用", "空间规划"], "frontier_priority": "扩展"},
    {"name": "Applied Geography", "publisher": "Elsevier", "issn": "0143-6228", "fields": ["应用地理", "土地利用", "空间分析"], "frontier_priority": "关注"},
    {"name": "Landscape Ecology", "publisher": "Springer", "issn": "0921-2973", "fields": ["景观生态", "土地变化", "空间格局"], "frontier_priority": "关注"},
    {"name": "Landscape and Urban Planning", "publisher": "Elsevier", "issn": "0169-2046", "fields": ["景观规划", "土地利用", "城市生态"], "frontier_priority": "扩展"},
    {"name": "Geoderma", "publisher": "Elsevier", "issn": "0016-7061", "fields": ["土壤学", "土壤有机碳", "土壤制图"], "frontier_priority": "必看"},
    {"name": "Geoderma Regional", "publisher": "Elsevier", "issn": "2352-0094", "fields": ["土壤学", "数字土壤制图", "区域土壤"], "frontier_priority": "关注"},
    {"name": "CATENA", "publisher": "Elsevier", "issn": "0341-8162", "fields": ["土壤侵蚀", "地貌", "土地退化"], "frontier_priority": "必看"},
    {"name": "Soil & Tillage Research", "publisher": "Elsevier", "issn": "0167-1987", "fields": ["耕作", "土壤管理", "土壤碳"], "frontier_priority": "必看"},
    {"name": "European Journal of Soil Science", "publisher": "Wiley", "issn": "1351-0754", "fields": ["土壤学", "土壤碳", "土壤过程"], "frontier_priority": "关注"},
    {"name": "Soil Use and Management", "publisher": "Wiley", "issn": "0266-0032", "fields": ["土壤管理", "土地管理", "农业环境"], "frontier_priority": "扩展"},
    {"name": "Soil Science Society of America Journal", "publisher": "Wiley", "issn": "0361-5995", "fields": ["土壤学", "土壤性质", "土壤碳"], "frontier_priority": "关注"},
    {"name": "Vadose Zone Journal", "publisher": "Wiley", "issn": "1539-1663", "fields": ["土壤水分", "包气带", "土壤过程"], "frontier_priority": "扩展"},
    {"name": "Plant and Soil", "publisher": "Springer", "issn": "0032-079X", "fields": ["土壤-植物", "养分循环", "根际"], "frontier_priority": "扩展"},
    {"name": "Biology and Fertility of Soils", "publisher": "Springer", "issn": "0178-2762", "fields": ["土壤生物", "土壤肥力", "土壤碳"], "frontier_priority": "扩展"},
    {"name": "Journal of Soils and Sediments", "publisher": "Springer", "issn": "1439-0108", "fields": ["土壤环境", "沉积物", "污染"], "frontier_priority": "扩展"},
    {"name": "Soil Research", "publisher": "CSIRO Publishing", "issn": "1838-675X", "fields": ["土壤学", "土壤管理", "土壤过程"], "frontier_priority": "扩展"},
    {"name": "Soil Security", "publisher": "Elsevier", "issn": "2667-0062", "fields": ["土壤安全", "土壤健康", "可持续发展"], "frontier_priority": "扩展"},
    {"name": "SOIL", "publisher": "Copernicus Publications", "issn": "2199-3971", "fields": ["土壤学", "土壤过程", "开放科学"], "frontier_priority": "扩展"},
    {"name": "Remote Sensing of Environment", "publisher": "Elsevier", "issn": "0034-4257", "fields": ["遥感", "地表过程", "环境监测"], "frontier_priority": "必看"},
    {"name": "International Journal of Applied Earth Observation and Geoinformation", "publisher": "Elsevier", "issn": "0303-2434", "fields": ["遥感", "地球观测", "地理信息"], "frontier_priority": "关注"},
    {"name": "International Journal of Remote Sensing", "publisher": "Taylor & Francis", "issn": "0143-1161", "fields": ["遥感", "影像处理", "地表监测"], "frontier_priority": "扩展"},
    {"name": "GIScience & Remote Sensing", "publisher": "Taylor & Francis", "issn": "1548-1603", "fields": ["GIS", "遥感", "空间建模"], "frontier_priority": "关注"},
    {"name": "Remote Sensing", "publisher": "MDPI", "issn": "2072-4292", "fields": ["遥感", "数字土壤制图", "地表监测"], "frontier_priority": "扩展"},
    {"name": "ISPRS Journal of Photogrammetry and Remote Sensing", "publisher": "Elsevier", "issn": "0924-2716", "fields": ["摄影测量", "遥感", "空间智能"], "frontier_priority": "关注"},
    {"name": "Agriculture, Ecosystems & Environment", "publisher": "Elsevier", "issn": "0167-8809", "fields": ["农业生态", "土壤碳", "生态系统服务"], "frontier_priority": "关注"},
    {"name": "Agricultural Systems", "publisher": "Elsevier", "issn": "0308-521X", "fields": ["农业系统", "土地利用", "可持续农业"], "frontier_priority": "扩展"},
    {"name": "Agricultural Water Management", "publisher": "Elsevier", "issn": "0378-3774", "fields": ["农业水资源", "灌溉", "土壤水分"], "frontier_priority": "扩展"},
    {"name": "Field Crops Research", "publisher": "Elsevier", "issn": "0378-4290", "fields": ["作物", "农业管理", "土壤-作物"], "frontier_priority": "扩展"},
    {"name": "Ecological Indicators", "publisher": "Elsevier", "issn": "1470-160X", "fields": ["生态指标", "生态系统服务", "可持续性"], "frontier_priority": "扩展"},
    {"name": "Science of the Total Environment", "publisher": "Elsevier", "issn": "0048-9697", "fields": ["环境科学", "土壤环境", "污染"], "frontier_priority": "扩展"},
    {"name": "Environmental Modelling & Software", "publisher": "Elsevier", "issn": "1364-8152", "fields": ["环境建模", "机器学习", "空间模拟"], "frontier_priority": "扩展"},
    {"name": "Global Change Biology", "publisher": "Wiley", "issn": "1354-1013", "fields": ["全球变化", "碳循环", "生态系统"], "frontier_priority": "关注"},
]


def _key(name: str) -> str:
    return "".join(character for character in name.casefold() if character.isalnum())


def default_land_science_catalog() -> list[dict]:
    """Return stable, ready-to-save records for a new personal journal library."""
    return [
        {
            **deepcopy(item),
            "id": f"catalog-{_key(item['name'])}",
            "website": "",
            "notes": "",
            "favorite": False,
        }
        for item in LAND_SCIENCE_JOURNALS
    ]


def merge_land_science_catalog(existing: list[dict]) -> tuple[list[dict], int, int]:
    """Add missing curated journals and enrich existing matching entries safely."""
    result = [deepcopy(item) for item in existing]
    positions = {_key(str(item.get("name", ""))): index for index, item in enumerate(result)}
    added = 0
    enriched = 0
    for source in default_land_science_catalog():
        key = _key(source["name"])
        if key not in positions:
            result.append(deepcopy(source))
            positions[key] = len(result) - 1
            added += 1
            continue
        target = result[positions[key]]
        changed = False
        for field in ("publisher", "issn"):
            if not str(target.get(field, "")).strip() and source.get(field):
                target[field] = source[field]
                changed = True
        current_fields = [str(value).strip() for value in target.get("fields", []) if str(value).strip()]
        for value in source["fields"]:
            if value not in current_fields:
                current_fields.append(value)
                changed = True
        target["fields"] = current_fields
        if not str(target.get("frontier_priority", "")).strip():
            target["frontier_priority"] = source["frontier_priority"]
            changed = True
        if changed:
            enriched += 1
    return result, added, enriched
