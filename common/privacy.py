# 正本は ogataten-nippo/docs/kyotsu-shiyo.md（3.1・3.2・5節）。
# https://github.com/VirgoB77/ogataten-nippo/blob/main/docs/kyotsu-shiyo.md
# Python標準ライブラリのみ。
"""3.1（個人情報）と 3.2（小さい母数）を、人の判断ではなくコードで守る層。

出力する値は必ずここを通す。通さない経路を作らない。
"""

import re

# 率を伏せる条件（3.2）
MIN_POPULATION = 500   # 人口がこれ未満なら率を出さない
MAX_SUPPRESS_COUNT = 2  # 件数がこれ以下なら率を出さない

# 件数の側も母数を見る（2026-09-20）。**率と同じ数字は使わない。守るものが違う。**
#
#   MIN_POPULATION = 500 … 率が跳ねるので率を出さない（読み違いを防ぐ）
#   TOKUTEI_FLOOR  = 100 … 住民に結びつきうるので件数を伏せる（特定を防ぐ）
#
# 500 をそのまま件数に当てると、人口 200〜499 で件数 3〜5 の升が 61 消える。
# **300 人の町の 3 件は誰も特定しない。**
#
# 県が公表しているのは1件ずつの記録で、町丁目別の件数は出していない。
# 8年ぶんを足し、手口を2層に束ねているのはこちら。
# **こちらが作った形なので、伏せる線もこちらが決める**（共通仕様3.2）。
TOKUTEI_FLOOR = 100      # 住民に結びつきうる人口
TOKUTEI_MAX = 5          # その人口帯で伏せる件数の上限

# 件数をまとめる上限（3.2）。1〜2件は実数を出さない。
BUCKET_MAX = 2

# 丸囲み・括弧書き・潰れた表記・外国法人を落とすと、公報と自治体の一覧表が
# ほとんど読めなくなる（共通仕様5節）。
# このサイトは当事者を持たないので使わないが、4サイトで同じファイルを置くため揃える。
_CORP_WORDS = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社", "相互会社",
    "特定目的会社", "投資法人", "有限責任事業組合",
    "一般社団法人", "公益社団法人", "一般財団法人", "公益財団法人", "社団法人", "財団法人",
    "医療法人", "学校法人", "宗教法人", "社会福祉法人", "独立行政法人", "地方独立行政法人",
    "国立大学法人", "特定非営利活動法人", "弁護士法人", "税理士法人",
    "生活協同組合", "農業協同組合", "漁業協同組合", "事業協同組合", "協同組合", "組合",
    "公社", "公団", "事業団", "機構", "振興会", "協会", "連合会", "商工会", "会館", "センター",
    "COOP", "コープ", "生協",
    # 丸囲み。公報はPDFから字を起こすので「株式会社」より多い
    "㈱", "㈲", "㈳", "㈶", "㈴", "㈻", "㈷",
    # 括弧書き。半角と全角が混ざるので、見る前にそろえること
    "(株)", "(有)", "(同)", "(資)", "(名)", "(福)", "(医)", "(相)",
    # 潰れた表記。「株赤ちゃん本舗」。人名に「株」は出てこない。
    # 「有」は有田・有村など姓に出るので単独では入れない
    "株",
    # 外国法人
    "Co.", "Ltd", "Inc", "LLC", "L.L.C", "Corp", "K.K.", "PLC",
    "S.L", "S.A", "N.V", "B.V", "GmbH", "A/S", "Pty",
    "エルエルシー", "リミテッド", "コーポレーション", "ホールディングス",
]


# 国と地方公共団体。法人格の語を持たないが個人ではない（共通仕様3.1）。
_GOV_TAIL = ("都", "道", "府", "県", "市", "区", "町", "村")
_GOV_WORDS = ("局", "委員会", "役所", "議会", "公所", "一部事務組合", "広域連合")


def _is_gov(n):
    """`大阪市` `兵庫県` `大阪市交通局` `○○町教育委員会` のたぐい。"""
    if n == "国" or n.startswith("国　") or n.startswith("国 "):
        return True
    if any(w in n for w in _GOV_WORDS):
        return True
    # 末尾が都道府県市区町村。ただし**列がずれて住所が入った**ものと衝突する
    # （`大阪市北区角田町３番25号`）。正本はそれを「個人として扱う（伏せる側に倒す）」
    # と定めているので、住所らしいものはここで当てない。
    #   ・数字を含む  ・丁目/番/号/地 を含む  ・自治体名にしては長い
    if any(c.isdigit() for c in n) or any(w in n for w in ("丁目", "番", "号", "地")):
        return False
    return len(n) <= 6 and n.endswith(_GOV_TAIL)


def _is_kana_or_romaji(n):
    """カタカナだけ、またはローマ字入り（共通仕様3.1）。

    戸籍の氏名はこの形にならないので、`オークワ　ほか` `F.O.B COOP` を
    個人と取り違えない。

    **ただし、名前をカタカナで書く一次情報では誤る。** そういう配布元に
    当たったら、ここではなく呼び出し側で止めること（3.1 に相談する）。
    """
    # 「ほか」「他」は名前ではなく注記。落としてから見る。
    # 正本の例 `オークワ　ほか` は、落とさないとカタカナだけに見えない。
    core = n
    for tail in ("ほか", "他", "外", "など", "ら"):
        core = core.replace(tail, "")
    core = "".join(c for c in core
                   if not c.isspace() and c not in "・，,.／/－-＆&（）()")
    if not core:
        return False
    if any("a" <= c.lower() <= "z" for c in core):
        return True
    return all("ァ" <= c <= "ヶ" or c == "ー" for c in core)


def is_corp(name):
    """法人格を表す語を含むか。含まなければ個人として扱う。

    語の一覧だけでは足りない。カタカナ・ローマ字と、国と地方公共団体を
    別に見る（共通仕様3.1・5節）。
    """
    n = (name or "")
    if any(w in n for w in _CORP_WORDS):
        return True
    return _is_gov(n) or _is_kana_or_romaji(n)


def redact_name(name):
    """画面に出す用。法人ならそのまま。個人なら「個人」を返す。

    空欄にしない。空欄だと「取れなかった」のか「個人だから伏せた」のかが
    読者に分からない。伏せたことは伏せたと書く。
    """
    return name if is_corp(name) else "個人"


def party_for_index(name):
    """index.json の party に入れる用。法人ならそのまま、個人なら空文字。

    redact_name() と混ぜてはいけない。機械が読むデータに「個人」という文字列を
    入れると、全国の別人が同じ名前として扱われ、横断ハブで混ざる（共通仕様3.1）。
    """
    return name if is_corp(name) else ""


def party_kind(name, disclosed=True):
    """当事者の出し方。4つのどれか（共通仕様3.1の表と対応する）。

        corp         法人と分かった
        individual   個人（法人と確かめられない場合を含む）
        undisclosed  一次情報の側が名前を載せていない（disclosed=False）
        none         そもそも当事者を持たない制度のレコード（呼び出し側が渡す）

    横断ハブで「法人が買った跡地」を絞るとき、undisclosed を individual に
    混ぜると取りこぼし、none に混ぜると意味が壊れる。4つを分ける。

    **disclosed=False にしてよいのは、名前の欄が無いことを確かめたときだけ。**
    読めていないだけなら "individual"（町丁目まで丸める側）にする。
    確かめずに undisclosed にすると、こちらの解析の穴が、そのまま地番の公開になる。
    """
    if not disclosed:
        return "undisclosed"
    return "corp" if is_corp(name) else "individual"


def redact_addr(addr, kind):
    """所在地の粒度。kind は party_kind() の戻り値。

    町丁目まで丸めるのは "individual" のときだけ。
    corp / undisclosed / none は地番まで出してよい。
    undisclosed は伏せるべき名前がそもそも無いので、所在地を丸める理由もない。
    ただし地番を出してよいのは、一次情報の側が公表しているときだけ。
    別のソースから補ったり、推測で足したりしない。
    """
    a = (addr or "").strip()
    if kind != "individual":
        return a
    # 町丁目までに丸める。最初のハイフンより前を残す（共通仕様4節の town と同じ切り方）。
    #   尼崎市潮江1-3-1 → 尼崎市潮江1   （丁目は残す）
    return re.split(r"[-−]", a, maxsplit=1)[0]


def suppress_rate(count, population):
    """率を伏せるべきか。人口500人未満、または件数が1件か2件で True。

    **0件は伏せない。** 3.2 が心配しているのは「小さい母数で率が跳ね上がる」
    ことで、0はその逆。件数そのものを 0 と出しているので、率を出しても
    戻るものが無い。0件を伏せると、率の地図で 0件 が「率を出していない」灰色に
    落ちて、3.2 の「0件はいちばん薄い階級の色」と食い違う。
    （2026-09-17 に正本が直った。前は「件数2件以下」だった）
    """
    # 条件を並べるときは、どちらを先に見るかも書く（共通仕様3.2）。順番は3段。
    #
    #   1. 人口0 … **率そのものが定義できない**（0では割れない）。伏せる以前の話。
    #              0件を先に見ると、ここでゼロ除算になる（2026-09-19 に踏んだ）
    #   2. 0件   … 伏せない。人口の小ささが効くのは「率×人口で件数が戻る」ため。
    #              0件には戻る先が無い。何を掛けても0
    #   3. 小人口・1〜2件 … 伏せる
    if population <= 0:
        return True
    if count == 0:
        return False
    if population < MIN_POPULATION:
        return True
    return 1 <= count <= MAX_SUPPRESS_COUNT


def bucket_count(n):
    """件数の表示。1〜2件は "1-2"、それ以外は str(n)。"""
    n = int(n)
    if n <= 0:
        return "0"
    if n <= BUCKET_MAX:
        return f"1-{BUCKET_MAX}"
    return str(n)


# 率を出さない理由は2つあり、**別のものとして扱う**（共通仕様3.2）。
#
#   出せない … 人口0。率そのものが定義できない（0では割れない）
#   伏せた   … 出せるが、出すと率×人口で件数が戻る
#
# 画面で同じ灰色・同じ「—」にすると、読者に区別がつかない。
SHOWN_RATE, NO_POPULATION, SUPPRESSED_RATE = "shown", "no_population", "suppressed"


def rate_reason(count, population):
    """率をどう扱うか。3つのどれか。**順番がそのまま意味になる。**"""
    if not population:
        return NO_POPULATION
    if suppress_rate(count, population):
        return SUPPRESSED_RATE
    return SHOWN_RATE


def rate_per_1k(count, population):
    """人口千人あたりの件数。伏せるべきときは None。"""
    if not population or suppress_rate(count, population):
        return None
    return round(count / population * 1000, 2)

def suppress_count(count, population):
    """件数を伏せるか。**母数を見る。**

    順番が効く。`suppress_rate` と同じ理由で、0件を先に外す。

      1. 0件 … 伏せない。伏せると「無い」が読めなくなる
      2. 1〜2件 … 伏せる（母数によらない）
      3. 人口0 … **伏せない。** 住んでいる人がいないので、住民に結びつかない。
         被害者は駅前や商業地に停めた人で、住民ではない
      4. 件数 > 人口 … **伏せない。** 被害者が住民でない証拠
      5. 人口が小さく、件数も小さい … 伏せる。ここが住民に結びつく

    **向きをまちがえないこと。** 「人口が小さいほど伏せる」を素直に当てると、
    3と4、つまり**もっとも特定に結びつきにくい升**を消す。
    """
    n = int(count)
    if n == 0:
        return False
    if 1 <= n <= MAX_SUPPRESS_COUNT:
        return True
    if not population:
        return False
    if n > population:
        return False
    return population < TOKUTEI_FLOOR and n <= TOKUTEI_MAX


def masked(n, population=None):
    """升の値そのもの。伏せた升は None。

    `population` を渡すと母数も見る（町丁目の升）。渡さないと件数だけで
    決める（市の升。母数が大きいので特定に結びつかない）。

    bucket_count() は人に見せる文字列、こちらは機械が持つ値（共通仕様6節の
    count / count_label の2本立てと同じ分け方）。

    **出力に実数を書くときは必ずここを通す。** 画面が伏せ字にしていても、
    元のファイルに実数が入っていれば伏せたことにならない（共通仕様5節）。
    """
    n = int(n)
    if population is None:
        return None if 1 <= n <= BUCKET_MAX else n
    return None if suppress_count(n, population) else n


def bar_width(n, scale):
    """横棒の幅（%）。伏せた升は幅を持たせない。

    幅は目で読める数字そのもの。n/最大値 で正規化すると、最大値がどこかの升の
    実数なので、幅に掛けるだけで伏せた升が戻る。だから
      - 伏せた升（1〜2件）は幅0にして、棒を出さない
      - 正規化の分母は、升の実数ではなく固定の目盛り（scale）にする
    """
    if n is None or n <= 0 or masked(n) is None:
        return 0.0
    return min(100.0, n / scale * 100.0)


def bar_scale(values):
    """横棒の目盛り。実数の最大値ではなく、固定の刻みに切り上げる。

    最大値をそのまま分母にすると、分母自体が実数を漏らす。
    1・2・5 の刻みに切り上げた値を使えば、幅から戻せるのは刻みの幅までになる。
    """
    top = max([v for v in values if v is not None and masked(v) is not None] + [0])
    if top <= 0:
        return 1
    step = 1
    while True:
        for m in (1, 2, 5):
            if step * m >= top:
                return step * m
        step *= 10

def city_rate(total, population, n_suppressed):
    """市全体の人口千人あたり件数。丸めの粗さで、伏せた升を守る。

    率を細かく出すと、率 × 人口 で市の合計が戻る。市の合計は町丁目の升の親なので、
    公開している子を引けば、伏せた子の合計が分かる（共通仕様3.2）。
    伏せた子が k 個あれば、その合計の幅は k（各1〜2件）。
    丸めの幅が k 以上になるところまで、桁を落とす。

    例（作り物の数字）：人口40万・伏せた升30の市なら、小数1桁で幅30件ぶんの
    余裕が残る。小さい市を足したときは、自動的に桁が減る。

    **実在の市の名前と、その市の伏せ方を対にして書かない。** このファイルは
    公開する木に入る。伏せた升の個数は公開値から数えられるので新しい情報では
    ないが、説明の例は作り物の数字にする（共通仕様11節）。
    """
    if not population:
        return None
    rate = total / population * 1000
    k = max(1, n_suppressed)
    for d in (2, 1, 0):
        # 小数d桁に丸めたときの、合計の取りうる幅（件）
        if (10 ** -d) * population / 1000 >= k:
            # d=0 は int で返す。round(x, 0) は 3.0 を返すので、受け取った側が
            # 「小数1桁まで出ている」と読んでしまう。
            r = round(rate) if d == 0 else round(rate, d)
            # 桁を落としきって 0 になるなら、値として意味がない。出さない。
            return r if r or not total else None
    return None

def label_for(n):
    """伏せ処理ずみの値（masked() の戻り値）を、人に見せる文字列にする。"""
    return bucket_count(1) if n is None else str(n)


def rate_for(n, population):
    """伏せ処理ずみの値から、人口千人あたりを出す。伏せた升は None のまま。"""
    if n is None:
        return None
    return rate_per_1k(n, population)

# 補完的伏せ（共通仕様3.2）。
# まとまりの親が分かると、伏せた升の合計が引き算で出る。伏せた升がどれも1件か2件なら、
# 2件の数 m がそこから確定する。m が 0 か k に張り付くと、k 升すべてが特定できる。
# k が1なら、その1升がそのまま出る。
#
# 防ぐには、**1〜2件ではない升を追加で伏せる**しかない。値が3件以上の升を1つ混ぜると、
# 合計の中に「3以上の未知数」が入り、1〜2件の升が決まらなくなる。
# 追加で伏せた升は「1-2」とは書けない（嘘になる）。別の見た目が要る。
SHOWN, SMALL, WITHHELD = "shown", "small", "withheld"


def _pin_check(states, values, total):
    """読者の立場で解いてみる。1〜2件の升が1つでも決まるなら True。

    伏せた升の合計 total は親から引けば分かる。
    1〜2件の升は [1,2]、追加で伏せた升は [3, 上限] の範囲を持つ。
    区間伝播を1本の制約に当てる。
    """
    idx = [i for i, st in enumerate(states) if st != SHOWN]
    if not idx:
        return False
    lo = [LO_SMALL if states[i] == SMALL else BUCKET_MAX + 1 for i in idx]
    hi = [BUCKET_MAX if states[i] == SMALL else total for i in idx]
    for _ in range(len(idx) + 2):
        moved = False
        for j in range(len(idx)):
            others_lo = sum(lo) - lo[j]
            others_hi = sum(hi) - hi[j]
            nlo, nhi = total - others_hi, total - others_lo
            if nlo > lo[j]:
                lo[j] = nlo; moved = True
            if nhi < hi[j]:
                hi[j] = nhi; moved = True
        if not moved:
            break
    return any(lo[j] == hi[j] for j, i in enumerate(idx) if states[i] == SMALL)


LO_SMALL = 1


def complement_suppress(values):
    """まとまりの中の升を、伏せるかどうかに分ける。

    values は同じまとまり（市 × 層など）の真の件数の並び。
    親（合計）は公開しているものとして扱う。**親を出していないなら要らない。**

    戻り値は SHOWN / SMALL / WITHHELD の並び。
      SHOWN     そのまま出す
      SMALL     1〜2件なので伏せる。画面は「1-2」
      WITHHELD  3件以上だが、上を守るために追加で伏せる。画面は「非公開」

    追加で伏せるのは、残りのうちいちばん件数の小さい升から（失う情報がいちばん少ない）。
    戻らなくなるまで1つずつ増やす。

    **札はまとまり単位でそろえる。** 補完的伏せを1つでも使ったまとまりでは、
    そのまとまりの伏せた升を全部 WITHHELD にする（画面は全部「非公開」）。
    「1-2」と「非公開」を並べると、どれが3件以上かが読者に分かる。
    すると1〜2件かもしれない升の数（＝隠れる場所）が減る。実測で、
    札を分けると実質の k が中央値1、そろえると2〜3.5 だった。
    升が特定できるわけではないが、隠れる場所は多いほうがよい。
    見た目の違いは、それ自体が数字になる（共通仕様3.2）。
    """
    states = [SMALL if masked(v) is None and v > 0 else SHOWN for v in values]
    total = sum(v for v, st in zip(values, states) if st != SHOWN)
    if not any(st == SMALL for st in states):
        return states                      # 伏せた升が無いので守るものが無い

    # 追加の候補は3件以上の升。小さいほうから。
    cand = sorted((v, i) for i, (v, st) in enumerate(zip(values, states))
                  if st == SHOWN and v > BUCKET_MAX)
    while _pin_check(states, values, total) and cand:
        v, i = cand.pop(0)
        states[i] = WITHHELD
        total += v
    if WITHHELD in states:
        # 札をそろえる。1-2 と 非公開 を並べない。
        states = [WITHHELD if st == SMALL else st for st in states]
    return states


def label_for_state(state, value, unit="件"):
    """画面に出す文字列。3つを書き分ける（共通仕様3.2「見た目で分ける」）。

    「非公開」には単位を付けない。「非公開件」は日本語として壊れている。
    """
    if state == WITHHELD:
        return "非公開"
    if state == SMALL:
        return bucket_count(1) + unit      # "1-2件"
    return ("0" if value == 0 else str(value)) + unit
