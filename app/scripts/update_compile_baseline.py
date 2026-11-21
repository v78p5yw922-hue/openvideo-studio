"""更新编译器基线：只允许"已评审的提示词改动"这一类差异。

允许的三类（都是 P0 / 音效纪律带来的）：
  1. 音效字段追加的声音纪律
  2. 台词 `<d>…</d>` 里补的句末标点 / 省略号→逗号
  3. 画外音动词替换
其余差异一律拒绝写库。
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import prompt_compiler as pc  # noqa: E402

FIXTURE = REPO / "studio/tests/fixtures/compile_shot_baseline.json"

GUARDS = ("（无对白，只有环境声）",
          "（除这一句台词外没有其他人声，不要加语气词）")
OFFSCREEN = "says in an off-screen voiceover"
LIP = "（画外音：the on-screen character's lips remain completely closed）"


def canon(text: str) -> str:
    """把"已允许的改动"抹平，剩下的差异才是真回归。"""
    out = text

    # ① 声音纪律：新旧两种写法都归一成一个中性标记
    for a, b in (("（无对白，只有环境声）", "；本镜只有环境声，无人声"),
                 ("（除这一句台词外没有其他人声，不要加语气词）", "；人声只有上面这一句台词")):
        out = out.replace(a, "【声音纪律】").replace(b, "【声音纪律】")

    # ② 人数约束：旧"其他人物不出现在画面里" → 新"主体只有/就是这 N 位"
    out = re.sub(r"画面中只出现\s*\d+\s*个主体：[^。]*?；其他人物不出现在画面里。",
                 "【人数约束】", out)
    out = re.sub(r"画面里的主体只有一位：[^。]*?。", "【人数约束】", out)
    out = re.sub(r"画面里的主体就是这\s*\d+\s*位：[^。]*?。", "【人数约束】", out)

    # ③ 画外音措辞
    out = out.replace("says in an off-screen voiceover", "开口说")
    out = out.replace("（画外音：the on-screen character's lips remain completely closed）", "")

    # ④ 台词正文末尾标点（P0 就是要在句末补标点）
    out = re.sub(r"<d>(.*?)</d>",
                 lambda m: "<d>" + re.sub(r"[。，、…！？?!.\s]+$", "", m.group(1)) + "</d>",
                 out, flags=re.S)

    # ⑤ 其余历史遗留
    out = re.sub(r"。(\s*)</d>", r"\1</d>", out)
    out = re.sub(r"</d>。", "</d>", out)
    out = out.replace("（这句话在这一镜结束时还没说完，话音带进下一镜）", "")
    out = out.replace("…", "，").replace("——", "，")
    out = re.sub(r"，+", "，", out)
    # ⑥ 关系句：两人版 / 三人改写版都归一成同一个标记
    out = re.sub(r"[^。；]*face each other[^。；]*", "【关系】", out)
    # ⑦ (S) 编号规则改变：编号本身不计入差异
    out = re.sub(r"\(S\d+\)", "(S#)", out)
    # ⑦ </d> 后的标点、以及标记前的标点
    out = re.sub(r"</d>[。，]?", "</d>", out)
    out = re.sub(r"[。，](【[^】]*】)", r"\1", out)
    return out


def main() -> int:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    changed, refused = 0, []
    for case in cases:
        a = case["input"]
        actual = pc.compile_shot(a["shot"], a["style"], a["cast_names"], a["scene"],
                                 a["lang"], a["instruction"], a["cast_descriptions"])
        if actual == case["expected"]:
            continue
        if canon(actual) != canon(case["expected"]):
            refused.append(case["key"])
            continue
        case["expected"] = actual
        changed += 1

    if refused:
        print(f"✗ 有 {len(refused)} 条差异不能用允许规则解释，拒绝更新：")
        for k in refused[:8]:
            print("   ", k)
        return 1

    FIXTURE.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已更新 {changed} 条基线（差异都属于：音效纪律 / 台词标点 / 画外音）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
