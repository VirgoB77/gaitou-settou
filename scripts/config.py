"""対象の府県・市・手口と、配布元の場所。

府県を増やすときは、ここに1つ足す。他のファイルは触らない。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
BUILD = ROOT / "data" / "build"

# 対象年。古い順に並べる（最後の2つを前年比に使う）。
# 兵庫県警は平成30年（2018）から公開している。
YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]

# 県警CSVの罪名。ここに無い罪名が来たら data/parse-unknown.md に出す（共通仕様9節）。
ZAIMEI = ["窃盗"]

# 指示書で定めた対象手口。これ以外は追加しない。
TEGUCHI = [
    "自転車盗",
    "車上ねらい",
    "部品ねらい",
    "自動車盗",
    "オートバイ盗",
    "自動販売機ねらい",
    "ひったくり",
]

# 率を伏せる条件は common/privacy.py が持つ（共通仕様3.2）。
# 人口500人未満、または件数2件以下。ここで別の値を持たない。

# 町丁目に出す層（共通仕様3.2「件数の少ない種別は、まとめて1つの層にする」）。
#
# 件数の少ない手口を1つずつ出すと、町丁目の升の大半が「1-2」になる。
# 手口ごとに「8年合計で実数を出せた区画の割合」を測り、低いものを束ねた。
# 実測値は金庫側の記録に置く（このファイルは公開する木に入る）。
#
# **束ねたら、細かいほうは出さない。** 束ねた層とその中の手口を両方出すと、
# 引き算で戻る。層に入っていない手口は市の升でだけ出す（CITY_ONLY）。
#
# key は index.json の id に入るので、あとから変えない（共通仕様9節）。
TOWN_LAYERS = [
    {"key": "bicycle", "name": "自転車", "teguchi": ["自転車盗"]},
    {"key": "vehicle", "name": "車・バイク",
     "teguchi": ["車上ねらい", "部品ねらい", "自動車盗", "オートバイ盗"]},
]

# 町丁目では出さない手口。市の升でだけ出す（共通仕様3.2）。
# どちらも8年合計で3件以上になる区画がごく少なく、町丁目では地図がほぼ空になる。
#
# **8年の合計件数をここに書かない。** それは公開している市×手口×年の升の
# 親そのもので、書くと伏せた升が引き算で戻る（実際に戻ることを確認した）。
CITY_ONLY = [t for t in TEGUCHI
             if not any(t in layer["teguchi"] for layer in TOWN_LAYERS)]

# 町丁目に出す期間。**1つだけ。** 期間の合計と年ごとの両方を出すと引き算で戻る。
# 単年だと町丁目の升の大半が「1-2」になり、地図がほぼ斜線になる。
# 年ごとの移り変わりは市の升（counts_by_city）で出す。
#
# 9年目が配られたときは、**窓を1年ずらす**（積み上げない）。
# 積み上げると「今年の窓 − 去年の窓 = その年ぶん」で、出さないと決めた
# 町丁目×年 が戻る（手元の8年で先取りして測った。升数は金庫側の記録に置く）。
# ずらしなら差は「入った年 − 落ちた年」で、どちらの年も単独では決まらない。
# ただしそれは町丁目×年 をどこにも出していないから成り立つ。
WINDOW_YEARS = 8

HOUR_BANDS = ["0-6", "6-12", "12-18", "18-24"]

# 地図の色の階級（共通仕様3.3）。
# 分位（quantile）は順位そのものなので使わない。固定の絶対値で切る。
# 同じ色は同じ件数を指す。市が違っても薄い市は薄いまま出る。それが事実だから。
#
# **階級の下端は必ず 3 から始める。** 色は目で読める数字そのもので、
# 切れ目が伏せる範囲（1〜2件）の中を通ると、1件と2件が別の色に落ちる。
# 画面が「1-2」と書いても、色と凡例が1か2かを答えてしまう（共通仕様3.2）。
#   前は period [2,6,12,24] / year [1,2,4,8] だった。単年の凡例は
#   「0／1／2〜3／4〜7／8〜」と、「1」という階級名を文字で出していた。
# 0件は最も薄い色、1〜2件は階級に入れず斜線にする。
#
# 期間合計と単年では件数の桁が違うので、2組だけ持つ。
# どちらも固定で、実際の区切りは凡例に数字で出す。
# 既定の表示（自転車盗を除く6手口）で5段階すべてが使われるように決めた。
# 町丁目に出す期間は8年合計の1つだけなので、階級も1組でよい。
CLASS_TOTAL = [3, 6, 12, 24]
CLASS_RATE = [2.0, 5.0, 10.0, 20.0]

# 運営者情報（共通仕様7節）。4サイトで同じ文面を使う。氏名も所在地も載せない。
OPERATOR = "鯨屋（くじらや）"
OPERATOR_DESC = "大阪府・兵庫県で行政が公開する一次情報を、消える前に記録しています。"
CONTACT_FORM = "https://forms.gle/pp93tSJ5p8SAMEMk8"
CONTACT_NOTE = "7日以内にご返信します"

# 共通仕様の正本。各リポジトリにコピーを置かず、リンクだけ置く。
SPEC_URL = "https://github.com/VirgoB77/ogataten-nippo/blob/main/docs/kyotsu-shiyo.md"

# 横断用 index.json と個別ページの絶対URLに使う。
# ドメインは .com を使う（共通仕様2節）。
#
# SITE_ID は index.json の record id 1,698本の接頭辞になる。
# 共通仕様9節「鍵にはあとから変わらないものだけを入れる」。
# リポジトリ名は現に一度変わった（2026-09-17 の改名）ので鍵に向かない。
# ドメインは買ってあるぶん最も動きにくいので、そちらに合わせる。
SITE_ID = "gaitou-settou"

# サイト名に「見せ方」を入れない。「〜マップ」は見せ方で、変わりえる
# （表や年表を足すかもしれない）。「街頭窃盗統計」は中身を指すので、
# 府県が増えても年が増えても持つ。説明のほうは meta description に置く。
SITE_NAME = "街頭窃盗統計"

SITE_URL = "https://gaitou-settou.com"

# 取得の作法（共通仕様3.4）
# サイトが未公開のうちは、404になるURLを名乗らない。
# 確認できない名乗りは、名乗らないより不審に見える。
#
# **SITE_URL から作る。** 固定文字列で書くと、公開先が変わったときに
# ここだけ古いまま残る。作れば、ひとりでに追従する。
USER_AGENT = f"kujiraya archive bot (+{SITE_URL}/policy.html; {CONTACT_FORM})"
REQUEST_INTERVAL = 5.0   # 秒。同時接続は1本

# 県警CSVの列名。府県ごとに違うため、ここで吸収する。
COLS = {
    "code": "市区町村コード（発生地）",
    "city": "市区町村（発生地）",
    "cho": "町丁目（発生地）",
    "teguchi": "手口",
    "date": "発生年月日（始期）",
    "hour": "発生時（始期）",
}

# e-Stat 令和2年国勢調査 小地域（町丁・字等別）境界データ
ESTAT_BOUNDARY = (
    "https://www.e-stat.go.jp/gis/statmap-search/data"
    "?dlserveyId=A002005212020&code={city_code}&coordSys=1&format=shape&downloadType=5"
)


class Pref:
    def __init__(self, key, name, code, csv_base, csv_stem, filename_fixes=None):
        self.key = key
        self.name = name
        self.code = code
        self.csv_base = csv_base
        self.csv_stem = csv_stem          # 手口 -> ファイル名のローマ字
        self.filename_fixes = filename_fixes or {}

    def csv_name(self, year, teguchi):
        fix = self.filename_fixes.get((year, teguchi))
        if fix:
            return fix
        return f"{self.key}_{year}{self.csv_stem[teguchi]}.csv"

    def csv_url(self, year, teguchi):
        return self.csv_base + self.csv_name(year, teguchi)


HYOGO = Pref(
    key="hyogo",
    name="兵庫県",
    code="28",
    csv_base="https://web.pref.hyogo.lg.jp/kk26/johoseisaku/documents/",
    csv_stem={
        "自転車盗": "zitensyatou",
        "車上ねらい": "syazyounerai",
        "部品ねらい": "buhinnerai",
        "自動車盗": "zidousyatou",
        "オートバイ盗": "ootobaitou",
        "自動販売機ねらい": "zidouhanbaikinerai",
        "ひったくり": "hittakuri",
    },
    # 配布元のファイル名が hyogo ではなく hyogp になっている。
    # 年とローマ字から機械的に組み立てると、ここだけ404になる。
    filename_fixes={(2024, "部品ねらい"): "hyogp_2024buhinnerai.csv"},
)

PREFS = {p.key: p for p in [HYOGO]}

# 対象の市区町村。e-Statの5桁コードで持つ。
CITIES = [
    {"code": "28202", "name": "尼崎市", "pref": "hyogo", "center": [135.406, 34.733], "zoom": 11.6},
    {"code": "28204", "name": "西宮市", "pref": "hyogo", "center": [135.342, 34.760], "zoom": 11.0},
]


def city(code):
    for c in CITIES:
        if c["code"] == code:
            return c
    raise KeyError(code)
