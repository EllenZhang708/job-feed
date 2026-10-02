"""
每天抓一次过去 24 小时内新发布的 DA / QA 职位，写进 README.md 和 data/ 文件夹。

由 .github/workflows/daily.yml 每天早上自动运行；也可以在 GitHub 仓库的 Actions 页面手动点 Run workflow。
搜索设置都在下面「搜索设置」那一段，想改关键词、地区、数量，改那里就行。
"""

import csv
import math
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jobspy import scrape_jobs

# ---------------- 搜索设置 ----------------

# 类型: 搜索词。网站自己的搜索是模糊匹配，相近的职位名（BI Analyst、QA Tester……）也会搜出来。
TERMS = {
    "DA": "data analyst",
    "QA": "QA analyst",
}

# 按优先级排列，README 里也按这个顺序显示。
PLACES = [
    {"label": "远程", "location": "Canada", "remote": True},
    {"label": "蒙特利尔", "location": "Montreal, QC", "remote": False},
    {"label": "GTA", "location": "Toronto, ON", "remote": False},
]

SITES = ["linkedin", "indeed", "glassdoor", "google"]
SITE_NAMES = {"linkedin": "LinkedIn", "indeed": "Indeed", "glassdoor": "Glassdoor", "google": "Google"}

HOURS = 24              # 只要多少小时内发布的
RESULTS = 100           # 每次搜索最多要多少条
RESULTS_LINKEDIN = 50   # LinkedIn 要逐条打开拿职位描述，要太多容易被封
PAUSE = 3               # 两次搜索之间停几秒

TZ = ZoneInfo("America/Toronto")
ROOT = Path(__file__).resolve().parent
FIELDS = ["发现日期", "类型", "地区", "职位", "公司", "地点", "远程", "网站", "发布日期", "法语", "链接", "公司官网链接"]


# ---------------- 抓取 ----------------

def fetch(site, term, place):
    kw = dict(
        site_name=[site],
        search_term=term,
        results_wanted=RESULTS,
        country_indeed="Canada",
        description_format="markdown",
        verbose=0,
    )
    if site == "google":
        where = "remote in Canada" if place["remote"] else "in " + place["location"]
        kw["google_search_term"] = f"{term} jobs {where} since yesterday"
    elif site == "linkedin":
        kw.update(location=place["location"], hours_old=HOURS,
                  results_wanted=RESULTS_LINKEDIN, linkedin_fetch_description=True)
        if place["remote"]:
            kw["is_remote"] = True
    elif site == "indeed":
        # Indeed 不能同时按「发布时间」和「远程」筛选，远程改成在地点里填 Remote
        kw.update(location="Remote" if place["remote"] else place["location"], hours_old=HOURS)
    else:
        kw.update(location=place["location"], hours_old=HOURS)
    df = scrape_jobs(**kw)
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def text(v):
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v).strip()


def flag(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, float):
        return False if math.isnan(v) else bool(v)
    return str(v).strip().lower() in ("true", "1", "yes")


def day(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y-%m-%d")
        except ValueError:  # pandas NaT
            return ""
    return str(v)[:10]


REMOTE_WORDS = re.compile(r"remote|anywhere|t[ée]l[ée]travail|work from home|wfh", re.I)


def is_remote(r):
    return flag(r.get("is_remote")) or bool(REMOTE_WORDS.search(text(r.get("location")) + " " + text(r.get("title"))))


# ---------------- 法语要求判断 ----------------
# 必须 → 去掉；可能要求 / 加分 / 无描述 → 保留并标出来。拿不准的一律保留，宁可多投一个，也不漏掉。

FR_MENTION = re.compile(r"\b(french|fran[cç]ais|bilingual|bilingue|bilinguisme|bilingualism)\b", re.I)
FR_NAMED = re.compile(r"\b(french|fran[cç]ais)\b", re.I)
OTHER_LANG = re.compile(r"\b(spanish|mandarin|cantonese|chinese|punjabi|hindi|urdu|portuguese|german|italian|arabic|japanese|korean|tagalog|vietnamese|russian)\b", re.I)
FR_ASSET = re.compile(
    r"\b(assets?|preferred|prefer|preferably|nice[\s-]to[\s-]have|a plus|an advantage|advantageous|bonus|desirable|desired"
    r"|considered|atouts?|un plus|souhait\w*|appr[ée]ci\w*|optional|not (?:required|mandatory|necessary|essential))\b", re.I)
FR_REQUIRED = re.compile(
    r"\b(required|requirement|must|mandatory|essential|necessary|needs? to|fluen\w*|proficien\w*"
    r"|requis\w*|exig\w*|obligatoire|indispensable|essentiel\w*|ma[iî]trise)\b", re.I)
HEADER_ASSET = re.compile(r"nice[\s-]to[\s-]have|preferred|bonus|\bassets?\b|\bplus\b|atouts?|souhait", re.I)
HEADER_REQ = re.compile(r"requirement|required|must[\s-]have|exigence|requis|obligatoire", re.I)
FR_STOP = re.compile(r"\b(les|des|du|une|et|vous|nous|pour|avec|dans|sur|est|sont|votre|notre|aux|le|la)\b", re.I)
EN_STOP = re.compile(r"\b(the|and|you|we|for|with|is|are|your|our|to|of|will|this)\b", re.I)


def written_in_french(desc):
    fr, en = len(FR_STOP.findall(desc)), len(EN_STOP.findall(desc))
    return fr >= 30 and fr > 2 * en


def is_header(line):
    s = line.strip()
    if s.startswith("#"):
        return True
    if s.startswith("**") and s.rstrip(":").rstrip().endswith("**"):
        return True
    plain = re.sub(r"[*_#`]", "", s).strip()
    return len(plain) <= 60 and plain.endswith(":") and not s.startswith(("-", "•", "* "))


def french_level(desc):
    if len(desc) < 80:
        return "无描述"
    if written_in_french(desc):
        return "必须"  # 整篇都是法语写的职位，基本都要求法语
    header, found = "", set()
    for line in desc.splitlines():
        if not line.strip():
            continue
        if is_header(line):
            header = line
        plain = re.sub(r"[*_#`>\\]", " ", line)
        for sent in re.split(r"(?<=[.!?;])\s+", plain):
            if not FR_MENTION.search(sent):
                continue
            if not FR_NAMED.search(sent) and OTHER_LANG.search(sent):
                continue  # 说的是别的语言的 bilingual
            if FR_ASSET.search(sent):
                found.add("加分")
            elif FR_REQUIRED.search(sent) or HEADER_REQ.search(header):
                found.add("必须")
            elif HEADER_ASSET.search(header):
                found.add("加分")
            else:
                found.add("可能要求")
    for level in ("必须", "可能要求", "加分"):
        if level in found:
            return level
    return ""


# ---------------- 输出 ----------------

def cell(s):
    return text(s).replace("|", "/").replace("\n", " ")


def md_url(u):
    return u.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def write_readme(today, rows, site_count, dropped_french, errors):
    by_place = {p["label"]: [r for r in rows if r["地区"] == p["label"]] for p in PLACES}
    lines = [
        f"# 新职位 · {today}",
        "",
        f"每天早上自动抓取过去 {HOURS} 小时内新发布的 DA / QA 职位。地区：远程、蒙特利尔、GTA。",
        "",
        f"**今天共 {len(rows)} 条**（" + " · ".join(f"{k} {len(v)}" for k, v in by_place.items())
        + f"）。明确要求法语的已去掉 {dropped_french} 条。",
        "",
        "各网站：" + " · ".join(f"{SITE_NAMES[s]} {site_count[s]}" for s in SITES),
    ]
    if errors:
        lines += ["", "**抓取出错：**", ""] + [f"- {e}" for e in errors]
    lines += [
        "",
        "「法语」列：**加分** = 法语是加分项；**可能要求** = 提到了法语，但看不出是不是必须，投之前看一眼；"
        "**无描述** = 没抓到职位描述，没法判断。空白 = 没提法语。",
        "",
    ]
    for label, items in by_place.items():
        lines += [f"## {label}（{len(items)}）", ""]
        if not items:
            lines += ["今天没有。", ""]
            continue
        lines += ["| 类型 | 职位 | 公司 | 地点 | 网站 | 发布 | 法语 | 链接 |", "|---|---|---|---|---|---|---|---|"]
        for r in items:
            lines.append("| " + " | ".join([
                r["类型"], cell(r["职位"]), cell(r["公司"]), cell(r["地点"]),
                r["网站"], r["发布日期"], r["法语"], f"[打开]({md_url(r['链接'])})",
            ]) + " |")
        lines.append("")
    lines += [
        "---",
        "",
        "每天的完整列表在 [data](data/) 文件夹里，最新一份是 [data/latest.csv](data/latest.csv)。",
        "",
        f"更新时间：{datetime.now(TZ):%Y-%m-%d %H:%M}（多伦多时间）",
    ]
    (ROOT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    rows, seen, errors = [], set(), []
    site_count = {s: 0 for s in SITES}
    dropped_french = 0

    for place in PLACES:
        for typ, term in TERMS.items():
            for site in SITES:
                try:
                    found = fetch(site, term, place)
                except Exception as e:  # 一个网站出错不影响其他网站
                    msg = (str(e).splitlines() or [type(e).__name__])[0][:160]
                    errors.append(f"{SITE_NAMES[site]} · {term} · {place['label']}：{msg}")
                    found = []
                print(f"{place['label']}\t{term}\t{site}\t{len(found)}", flush=True)

                for r in found:
                    url = text(r.get("job_url"))
                    if not url or url in seen:
                        continue  # 同一次抓取里完全相同的链接只留一条
                    if place["remote"] and site != "linkedin" and not is_remote(r):
                        continue  # 远程搜索里混进来的非远程职位，留给 GTA / 蒙特利尔那两组
                    seen.add(url)
                    fr = french_level(text(r.get("description")))
                    if fr == "必须":
                        dropped_french += 1
                        continue
                    site_count[site] += 1
                    rows.append({
                        "发现日期": today,
                        "类型": typ,
                        "地区": place["label"],
                        "职位": text(r.get("title")),
                        "公司": text(r.get("company")),
                        "地点": text(r.get("location")),
                        "远程": "是" if is_remote(r) else "",
                        "网站": SITE_NAMES[site],
                        "发布日期": day(r.get("date_posted")),
                        "法语": fr,
                        "链接": url,
                        "公司官网链接": text(r.get("job_url_direct")),
                    })
                time.sleep(PAUSE)

    # 每个地区内按发布日期从新到旧
    order = {p["label"]: i for i, p in enumerate(PLACES)}
    rows.sort(key=lambda r: r["发布日期"], reverse=True)
    rows.sort(key=lambda r: order[r["地区"]])

    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    write_csv(data / f"{today}.csv", rows)
    write_csv(data / "latest.csv", rows)
    write_readme(today, rows, site_count, dropped_french, errors)

    print(f"\n共 {len(rows)} 条，去掉要求法语的 {dropped_french} 条，出错 {len(errors)} 次。")
    for e in errors:
        print("出错：", e)


if __name__ == "__main__":
    main()
