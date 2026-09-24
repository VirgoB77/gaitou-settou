"""伏せた升が、公開しているほかの数字から戻せないかを調べる。

共通仕様3.2「伏せた升は、引き算で戻せることがある」に対応する検査。
`tests/test_output.py` は升を1つずつ見る。こちらは**升どうしの関係**を見る。
1つ1つが正しく伏せてあっても、足し算の相手がいれば戻る。

読むのは公開物だけ（`data/build/*.geojson`・`index.json`・`cho/*.html`）。
元の実数は使わない。**読者にできることしかしない。**

  直に読める   … 伏せ字を通していない実数が、そのまま公開物に入っている
  引き算で戻る … 公開している数字だけから、伏せた升の値が一意に決まる

引き算のほうは区間伝播で解く。伏せた升に [1,2]、出ている升に [n,n] を持たせ、
「子の合計＝親」を動かなくなるまで当てる。幅が1に縮んだ升は、読者が同じ
計算をすれば戻せる升。

使い方：

    python scripts/check_small_counts.py
    python scripts/check_small_counts.py --list   # 戻せる升の例も出す

戻せる升が1つでもあれば終了コード 1。公開前にここが通ること。
"""

import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import config
import privacy
import tracked

sys.stdout.reconfigure(encoding="utf-8")

LO, HI = 1, privacy.BUCKET_MAX


def load():
    fcs = [json.loads((config.BUILD / f'{c["code"]}.geojson').read_text(encoding="utf-8"))
           for c in config.CITIES]
    idx = json.loads((config.ROOT / "index.json").read_text(encoding="utf-8"))
    return fcs, idx


# ---------------------------------------------------------------- 直に読める

def raw_values(fcs, idx):
    """伏せ字を通っていない 1〜2 が、公開物に残っていないか。"""
    hits = []
    for fc in fcs:
        P = fc["properties"]
        for v in P["city_hours"]:
            if v is not None and LO <= v <= HI:
                hits.append(f'{P["city"]} city_hours')
        for f in fc["features"]:
            p = f["properties"]
            for v in p["n"]:
                if v is not None and LO <= v <= HI:
                    hits.append(f'{P["city"]} {p["name"]} n')
    for m in idx["records"] + idx["counts_by_city"]:
        if m["count"] is not None and LO <= m["count"] <= HI:
            hits.append(f'index.json {m.get("city")} {m.get("kind")}')
    return hits


def parent_totals(fcs):
    """親の合計が公開物に残っていないか。

    突合率の行数（市×全手口×年の合計）を geojson に入れていた時期がある。
    親を1つ出すだけで、公開している子を引いて伏せた子が戻る。
    """
    return [f'{fc["properties"]["city"]} properties.match'
            for fc in fcs if "match" in fc["properties"]]


def city_rate_margin(fcs):
    """市平均の丸めが、伏せた子を守れる粗さになっているか。

    率 × 人口 で市の合計が戻る。丸めの幅が、伏せた子の数（＝合計の取りうる幅）
    より狭いと、引き算で子が絞れる（privacy.city_rate）。
    """
    bad = []
    for fc in fcs:
        P = fc["properties"]
        pop = P["population"]
        for i, r in enumerate(P["city_rate"]):
            if r is None:
                continue
            k = sum(1 for f in fc["features"] if f["properties"]["n"][i] is None)
            d = len(str(r).split(".")[1]) if "." in str(r) else 0
            if k and (10 ** -d) * pop / 1000 < k:
                bad.append(f'{P["city"]} {P["layers"][i]["name"]} 率{r}（伏せた子{k}）')
    return bad


def bar_widths():
    """出来上がったページの横棒が、伏せた行に幅を持たせていないか。

    幅は目で読める数字そのもの。n / 表の最大値 で正規化していると、
    幅に最大値を掛けるだけで伏せた行の実数が戻る。
    """
    row = re.compile(
        r'<td class="v num">([^<]*?)件</td><td class="barcell">(.*?)</td>')
    label = privacy.bucket_count(1)
    bad = 0
    for path in sorted((config.ROOT / "cho").glob("*.html")):
        for value, bar in row.findall(path.read_text(encoding="utf-8")):
            if value == label and "width" in bar:
                bad += 1
    return bad


def class_breaks():
    """色の階級の切れ目が、伏せる範囲（1〜2件）の中を通っていないか。"""
    bad = []
    breaks = config.CLASS_TOTAL
    if breaks and breaks[0] <= privacy.BUCKET_MAX:
        bad.append(f"件数の階級 {breaks} の下端 {breaks[0]} が 1〜{HI} の中にある")
    return bad


def group_margin(fcs, idx):
    """まとまりごとの余裕を出す（共通仕様3.2「まとまりの中で伏せた升が1つなら…」）。

    市の升を層のぶん足すと、町丁目の層の升の**親 S** になる。
    伏せた升が k 個、どれも1件か2件なので、**2件の数 m = S − k が確定する。**

        m == 0   伏せた升は全部1件   → k 升すべて特定できる
        m == k   伏せた升は全部2件   → 同じ
        k == 1   その1升が確定する
        0 < m < k … 1件と2件が混ざるので、どれがどれかは決まらない（戻らない）

    **「戻る升0」だけを見ていると、これが運で通っているのか構造で通っているのかが
    分からない。** 区間伝播は m==0 / m==k / k==1 を捕まえるが、
    「あと1件で崩れる」状態は素通りする。ここで余裕そのものを出す。

    戻り値は (まとまりの一覧, 危ないまとまりの一覧)。
    読者にできる計算しかしていない（S も k も公開している数字から出る）。
    """
    rows, bad, thin = [], [], []
    city = {}
    for m in idx["counts_by_city"]:
        city[(m["city_code"], m["kind"].split("/", 1)[1], m["period"])] = m["count"]

    for fc in fcs:
        P = fc["properties"]
        cc = P["city_code"]
        for i, layer in enumerate(P["layers"]):
            parts = [city.get((cc, t, y)) for t in layer["teguchi"] for y in P["years"]]
            if any(v is None for v in parts) or not parts:
                continue                      # 親が伏せてあれば S が決まらない＝戻らない
            S = sum(parts)
            hidden = [f for f in fc["features"] if f["properties"]["n"][i] is None]
            wh = sum(1 for f in fc["features"] if f["properties"].get("w", [])[i:i + 1] == [True])
            k = len(hidden)
            if k == 0:
                rows.append((P["city"], layer["name"], S, 0, None, "伏せた升なし"))
                continue
            if wh:
                # 「非公開」の札でそろえたまとまり。中に3以上の未知数が混ざるので
                # 2件の数 m は決まらない。隠れる場所は伏せた升ぜんぶ。
                #
                # **ただし、そろっていることが前提。** 前はここで解くのをやめていて、
                # 「1-2」と「非公開」が混ざったまとまりも素通りした（#41）。
                # 混ざると、補完的伏せが発動していないことが読め、「非公開」は
                # 人口の線の升（3〜TOKUTEI_MAX 件）だと分かる。
                small = k - wh
                if small:
                    row = (P["city"], layer["name"], S, k, None,
                           f"札が混ざっている（1-2 が {small}升・非公開 が {wh}升）")
                    rows.append(row)
                    bad.append(row)
                    continue
                if k == 1:
                    # そろえても1升なら、親から引けばその升が出る
                    row = (P["city"], layer["name"], S, k, None, "伏せた升が1つだけ")
                    rows.append(row)
                    bad.append(row)
                    continue
                rows.append((P["city"], layer["name"], S, k, None,
                             f"札をそろえた {wh}升（m は決まらない）"))
                continue
            shown = sum(f["properties"]["n"][i] for f in fc["features"]
                        if f["properties"]["n"][i] is not None)
            m_ = (S - shown) - k          # 伏せた升のうち2件のものの数
            margin = min(m_, k - m_)
            # 当てる組合せの数。**これが1なら、どの升も決まる。**
            # 共通仕様3.2「当てる組合せが1通りしかないまとまりは、親を出さない。
            # 例外なし」。余裕0・m=k・k=1 はすべてこれに当たる（同値を確かめた）。
            ways = math.comb(k, m_) if 0 <= m_ <= k else 0
            note = f"余裕 {margin}"
            # 余裕が0かどうかだけ見ると、崖の縁に立つまで鳴らない。
            # 余裕が k に対して細ってくると、1升ずつの「2件らしさ」が
            # 0か1に寄る。升が確定しなくても、当てやすさは上がる。
            # **崩れてから鳴るのでは遅い。** 細ったら先に言う。
            if k >= 2 and 0 < m_ < k and margin < k * THIN:
                note += f"（細い／k の {margin / k:.0%}）"
                thin.append((P["city"], layer["name"], k, m_, margin))
            row = (P["city"], layer["name"], S, k, m_, f"{note}／{ways:,} 通り")
            rows.append(row)
            if ways <= 1:
                bad.append(row)
    return rows, bad, thin


# ---------------------------------------------------------------- 引き算

# 引き算の経路。どの関係で升が確定したかを、この名前で数える。
THIN = 0.25   # 余裕が k のこれを下回ったら「細い」と言う

ROUTE = {
    "市": "市の升の和 − 町丁目の層の升の和",
    "時間帯": "市の時間帯の和 − 市の手口の升の和",
}


class Grid:
    """升の集まりと、その間の「子の合計＝親」の関係。"""

    def __init__(self):
        self.lo, self.hi, self.cons = {}, {}, []
        self.hidden = set()
        self.why = {}          # 升 -> 最初にそれを確定させた関係の名前

    def cell(self, key, value, withheld=False):
        if withheld:
            # 「非公開」の札。札はまとまり単位でそろえてあるので、読者から見ると
            # 1〜2件かもしれないし3件以上かもしれない。下限は1しか言えない。
            # （中身は3件以上のものと1〜2件のものが混ざっている）
            self.lo[key], self.hi[key] = LO, 10 ** 9
            self.hidden.add(key)
        elif value is None:
            self.lo[key], self.hi[key] = LO, HI
            self.hidden.add(key)
        else:
            self.lo[key] = self.hi[key] = value
        return key

    def rel(self, parent, children, route=""):
        self.cons.append((parent, list(children), route))

    def _pin(self, key, route):
        """幅が1に縮んだ瞬間を、どの関係のせいかと一緒に記録する。"""
        if key in self.hidden and key not in self.why and self.lo[key] == self.hi[key]:
            self.why[key] = route

    def solve(self):
        changed = True
        while changed:
            changed = False
            for parent, kids, route in self.cons:
                klo = sum(self.lo[k] for k in kids)
                khi = sum(self.hi[k] for k in kids)
                if klo > self.lo[parent]:
                    self.lo[parent] = klo; changed = True
                if khi < self.hi[parent]:
                    self.hi[parent] = khi; changed = True
                self._pin(parent, route)
                for j in kids:
                    nlo = self.lo[parent] - (khi - self.hi[j])
                    nhi = self.hi[parent] - (klo - self.lo[j])
                    if nlo > self.lo[j]:
                        self.lo[j] = nlo; changed = True
                    if nhi < self.hi[j]:
                        self.hi[j] = nhi; changed = True
                    self._pin(j, route)

    def solved(self):
        return {k for k in self.hidden if self.lo[k] == self.hi[k]}


def text_parents(idx):
    """**公開する文章とソースに、親の合計が書かれていないか。**

    ここが長いあいだ空いていた穴。これまでは公開する「データ」だけを見て、
    公開する「ソースとコメント」を見ていなかった。
    公開用の木にはスクリプトもそのまま入る。コメントに真の合計を1行書けば、
    伏せた升はそこから引き算で戻る。データ側をいくら固めても意味がない。

    実際に `check_small_counts.py` 自身と `config.py` のコメントが市の升の親を
    持っていて、伏せた4升が一意に決まる状態だった（2026-09-17）。

    やり方：伏せた升を含むまとまりごとに、親としてありうる範囲を出し、
    公開物の中で **その範囲に入る数が、そのまとまりの名前と同じ行にある**
    ものを拾う。範囲だけで拾うと、伏せた升が少ないまとまりでは候補が数通りしか
    なく、人口や日付が片端から当たって狼少年になる（実測1,554件）。
    名前と同じ行、という条件がその差を分ける。実際の穴は2つとも
    「ひったくりは8年で◯件」「尼崎 … 手口 ◯」の形だった。

    **値は出さない。** 場所と、決まってしまうことだけを報告する。

    **捕まえないもの。**

      ・`.json` は見ない。縮めた JSON は1行なので、行単位の手がかり
        （まとまりの名前が同じ行にあるか）が効かない。全部が同じ行になる
      ・まとまりの名前を書かずに親の合計だけ書いたもの。
        範囲だけで拾うと人口も日付も当たる（実測1,554件の誤検出）ので、
        名前を条件にした。**その代わり、名前の無いものは抜ける**
      ・足し算以外の経路（比・割合・順位から戻すもの）

    **「0件」は「親の合計をどこにも書いていない」ことの証拠ではない。**
    この形で書かれた親を見つけていない、というだけ。
    """
    import itertools

    city_name = {c["code"]: c["name"] for c in config.CITIES}
    groups = []          # (表示名, 手がかりの語, 公開ぶんの和, 伏せた升の数)
    by_city = {}
    for m in idx["counts_by_city"]:
        by_city.setdefault(m["city_code"], []).append(m)

    for cc, rows in by_city.items():
        nm = city_name.get(cc, cc)
        shown = sum(r["count"] for r in rows if r["count"] is not None)
        k = sum(1 for r in rows if r["count"] is None)
        if k:
            groups.append((f"{nm}の手口ぜんぶ", [nm, nm.rstrip("市"), cc], shown, k))
        teg = {}
        for r in rows:
            teg.setdefault(r["kind"].split("/", 1)[-1], []).append(r)
        for t, rs in teg.items():
            sh = sum(r["count"] for r in rs if r["count"] is not None)
            kk = sum(1 for r in rs if r["count"] is None)
            if kk:
                groups.append((f"{nm}の{t}", [t], sh, kk))

    teg_all = {}
    for m in idx["counts_by_city"]:
        teg_all.setdefault(m["kind"].split("/", 1)[-1], []).append(m)
    for t, rs in teg_all.items():
        sh = sum(r["count"] for r in rs if r["count"] is not None)
        kk = sum(1 for r in rs if r["count"] is None)
        if kk:
            groups.append((f"全市の{t}", [t], sh, kk))

    hits = []
    rels, how = tracked.published({".py", ".md", ".html", ".yml", ".yaml", ".txt"})
    text_parents.how = how      # 呼び出し側が「どう決めたか」を言えるように
    for rel in rels:
        path = config.ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            nums = [int(t.replace(",", "")) for t in re.findall(r"\d[\d,]*", line)
                    if t.strip(",").isdigit() or "," in t]
            if not nums:
                continue
            for name, clues, shown, k in groups:
                if not any(c and c in line for c in clues):
                    continue
                for n in nums:
                    if not (shown + k <= n <= shown + 2 * k):
                        continue
                    sols = sum(1 for c in itertools.product((1, 2), repeat=k)
                               if shown + sum(c) == n)
                    if sols == 1:
                        hits.append(f"{rel}:{ln}  「{name}」の親になり、伏せた {k} 升が一意に決まる")
                    elif 1 < sols < 2 ** k:
                        hits.append(f"{rel}:{ln}  「{name}」の親になり、"
                                    f"候補が {2 ** k} 通り→{sols} 通りに絞れる")
    return hits


def build_grid(fcs, idx, use_hour=True):
    """公開している升と、その間の関係を並べる。

    いま公開しているのは3つだけ。
      町丁目 × 層 × 期間合計   （geojson の n、index.json の records）
      市 × 手口 × 年           （index.json の counts_by_city）
      市 × 時間帯 × 期間合計   （geojson の city_hours）

    町丁目には合計も年別も時間帯も無いので、町丁目どうしを引く相手がいない。
    残るのは市と町丁目の間と、市の中の2つの分割の間。
    """
    g = Grid()
    town = {}          # (city_code, layer_key) -> 町丁目の升のキー
    for fc in fcs:
        P = fc["properties"]
        for i, layer in enumerate(P["layers"]):
            keys = []
            for f in fc["features"]:
                p = f["properties"]
                keys.append(g.cell(("n", p["code"], layer["key"]), p["n"][i],
                                   withheld=bool(p.get("w", [False] * 9)[i])))
            town[(P["city_code"], layer["key"])] = keys

    # 市の升。手口ごと × 年。
    city = {}
    for m in idx["counts_by_city"]:
        t = m["kind"].split("/", 1)[1]
        city[(m["city_code"], t, m["period"])] = g.cell(
            ("H", m["city_code"], t, m["period"]), m["count"])

    for fc in fcs:
        P = fc["properties"]
        cc = P["city_code"]

        def known_sum(keys):
            """升の並びの合計。1つでも伏せてあれば None（定数にできない）。"""
            if any(g.lo[k] != g.hi[k] for k in keys):
                return None
            return sum(g.lo[k] for k in keys)

        # 市の升を層のぶんだけ足すと、町丁目の層の升の親になる。
        for layer in P["layers"]:
            kids = town.get((cc, layer["key"]))
            parents = [city[(cc, t, y)] for t in layer["teguchi"] for y in P["years"]
                       if (cc, t, y) in city]
            v = known_sum(parents)
            if kids and v is not None:
                g.rel(g.cell(("S", cc, layer["key"]), v), kids, "市")

        # 市の時間帯は、市の手口の合計にはならない。**関係を張ってはいけない。**
        # 発生時が読めない行があるぶん、時間帯の合計のほうが少し小さい。
        # 等式として当てると矛盾する。読者にとっても引き算の相手にならない。
        # 「Σ時間帯 ≤ Σ手口」は成り立つが、時間帯はどれも実数で出ているので、
        # そこから分かるのは手口の合計の下限だけで、何も絞れない。
        #
        # **実際の合計をここに書かない。** このファイルは公開する木に入る。
        # 市の手口の合計は、公開している市×手口×年の升の親そのもの。
        # 書くと「合計の升と内訳の升を両方出さない」を、コメントで破ることになる。
    return g


# ---------------------------------------------------------------- 出力

def main():
    show = "--list" in sys.argv
    fcs, idx = load()
    ng = 0

    print("■ 直に読める実数（伏せ字を通っていないもの）")
    checks = [
        ("geojson・index.json の生の実数", raw_values(fcs, idx)),
        ("親の合計（properties.match）", parent_totals(fcs)),
        ("市平均の丸めが粗さ不足", city_rate_margin(fcs)),
        ("色の階級が伏せる範囲を割る", class_breaks()),
        ("公開する文章・ソースに親の合計", text_parents(idx)),
    ]
    for name, hits in checks:
        ng += len(hits)
        print(f"   {name:<32}{len(hits):>7,}")
        if hits and show:
            for h in hits[:5]:
                print(f"      {h}")
    bars = bar_widths()
    ng += bars
    print(f"   {'横棒の幅から読める（cho/*.html）':<30}{bars:>7,}")

    print()
    print("■ まとまりの余裕（市の升を足すと町丁目の層の親になる）")
    print("   伏せた升 k 個のうち2件が m 個。m は公開している数字から確定する。")
    print("   m が 0 か k に張り付くと、k 升すべてが特定できる。")
    rows, bad, thin = group_margin(fcs, idx)
    ng += len(bad)
    print(f"   {'市':<8}{'層':<12}{'親 S':>8}{'伏せた k':>9}{'2件 m':>7}  余裕")
    for city, name, S, k, m_, note in rows:
        print(f"   {city:<8}{name:<12}{S:>8,}{k:>9}"
              f"{('—' if m_ is None else m_):>7}  {note}")
    if bad:
        # 鳴った理由ごとに直し方が違う。まとめて「補完的伏せが要る」と言わない
        print("   ★ 伏せた升が戻る形のまとまりがある（共通仕様3.2）。上の行の理由を見ること。")
        print("      余裕0・伏せた升が1つ → 補完的伏せが要る")
        print("      札が混ざっている     → 札をそろえる（privacy.complement_suppress）")
    if thin:
        print(f"   ▲ 余裕が細いまとまりが {len(thin)} 件ある（k の {THIN:.0%} 未満）。")
        print("      まだ崩れていないが、市や年を足すと崩れる側にある。")
        for city, name, k, m_, margin in thin:
            print(f"      {city} {name}  k={k}  余裕={margin}")

    print()
    print("■ 引き算で戻る升")
    for use_hour, name in ((False, "時間帯を使わない"), (True, "時間帯も使う")):
        g = build_grid(fcs, idx, use_hour=use_hour)
        g.solve()
        got = g.solved()
        n = len(g.hidden)
        print(f"   {name:<16} 伏せた升 {n:>7,}   戻る {len(got):>7,}  "
              f"({len(got) / n * 100:4.1f}%)" if n else f"   {name}: 伏せた升なし")
        if use_hour:
            ng += len(got)
            kinds = defaultdict(lambda: [0, 0])
            for k in g.hidden:
                kinds[k[0]][0] += 1
            for k in got:
                kinds[k[0]][1] += 1
            label = {"n": "町丁目×層×期間合計", "h": "市×時間帯×期間合計",
                     "H": "市×手口×年"}
            print()
            print(f"   {'升の種類':<24}{'伏せた':>9}{'戻る':>9}")
            for t in ("n", "h", "H"):
                if t in kinds:
                    a, b = kinds[t]
                    print(f"   {label[t]:<20}{a:>11,}{b:>9,}")

            # どの引き算で戻るのか。いちばん多い経路から潰すのが早い。
            routes = defaultdict(int)
            for k in got:
                routes[g.why.get(k, "（不明）")] += 1
            print()
            print(f"   {'引き算の経路':<34}{'戻る升':>9}{'割合':>8}")
            for r, c in sorted(routes.items(), key=lambda x: -x[1]):
                print(f"   {ROUTE.get(r, r):<30}{c:>13,}{c / len(got) * 100:>7.1f}%")
            if show:
                print("\n   戻る升の例：")
                for k in sorted(got, key=str)[:5]:
                    print(f"      {k}  = {g.lo[k]}件")

    print()
    if ng:
        print(f"伏せたつもりの升が {ng:,} 読めます。共通仕様3.2を満たしていません。")
        return 1
    print("戻せる升はありませんでした。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
