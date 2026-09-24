"""町丁目ごとの静的ページと、その一覧・サイトマップを作る。

地図のパネルと同じ内容を、JavaScriptなしで読める形で置く。
検索エンジンと、リンクを受け取った人のため。
"""

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import config
import privacy

sys.stdout.reconfigure(encoding="utf-8")

SRC = config.ROOT
OUT = SRC / "cho"
SITE = config.SITE_URL


def notes(built_on, period, up="../", any_withheld=False):
    """各ページに必ず置くもの（共通仕様8節）。たたんで隠さない。

    built_on … **このページを作り直した日。データが変わった日ではない。**
    period   … データ自身の時点（対象年）。こちらだけが読者の知りたい事実。

    前は `updated` という名前で受けて「最終更新」と書いていた。
    引数の名前そのものが、外れる名乗りだった（共通仕様3.5）。
    実測すると、CI が触った 850 ページのうち **850 ページが日付の行だけの差**で、
    中身は1件も変わっていなかった。名乗りは 100% 外れていた（2026-09-19）。

    any_withheld … 補完的に伏せた升（札が「非公開」）が実在するか。
    実在しないのに説明だけ出すと、読者が一度も出会わない札の読み方を
    常時表示することになる。注記は数字を読む前提として置いてあるので、
    実在しない表示を断定形で書くと、伏せた升の読み方そのものを誤らせる。
    """
    wh_note = ("""  <li>「非公開」と出している町丁目があります。こちらの判断で伏せた升です。<b>「非公開」は少ないという意味ではありません。値は1件以上で、3件以上のこともあります。</b></li>\n"""
               if any_withheld else "")
    layers = "／".join(l["name"] for l in config.TOWN_LAYERS)
    only = "・".join(config.CITY_ONLY)
    return f"""<h2>このサイトについて</h2>
<p>兵庫県警察が公開している犯罪オープンデータから、町丁目ごとの窃盗の認知件数を
集計しています。</p>

<div class="scope">
<p class="in"><b>このマップに含まれているもの</b><br>
自転車盗・車上ねらい・部品ねらい・オートバイ盗・自動販売機ねらい・自動車盗・ひったくり</p>
<p class="out"><b>含まれていないもの</b><br>
空き巣・忍込みなどの侵入盗、暴力をともなう犯罪<br>
<span>兵庫県警のオープンデータに、町丁目別のデータがないためです。
こちらが選んで外したものではありません。</span></p>
<p class="out"><b>データはありますが、町丁目には出していないもの</b><br>
{only}<br>
<span>件数が少なく、町丁目に割るとほとんどが1〜2件になります。
1〜2件は実数を出さない決まりのため、町丁目には出していません。
市ごと・年ごとの件数は、横断用のデータに入れています。
<b>こちらの判断で外しています。</b></span></p>
<p class="note"><span>町丁目に出しているのは {layers} の2つです。
手口を1つずつ出すと、町丁目の升の大半が「1-2」になって地図が読めなくなるため、
近いものをまとめています。</span></p>
</div>

<h2>数字の読み方</h2>
<ul>
  <li>認知件数は、警察に届け出があったものに限られます。届け出のなかった被害は含まれません。</li>
  <li>駅前や商業地など来街者の多い町丁目は、住んでいる人の数に対して件数が大きくなりやすい数字です。分母は夜間人口（住んでいる人）です。</li>
  <li>乗り物盗の発生時刻・発生日には推定が含まれます。時間帯の内訳もその推定を含みます。</li>
  <li>年は警察が認知した年です。被害に遭った時期がそれ以前の場合も含みます。</li>
  <li>件数が1〜2件のときは実数を出していません（「1-2」または「非公開」と出します）。</li>
{wh_note}  <li>人口は令和2年国勢調査の値です。人口{privacy.MIN_POPULATION}人未満、または件数{privacy.MAX_SUPPRESS_COUNT}件以下の町丁目は、人口千人あたりの値を出していません。</li>
  <li>町丁目の数字は期間の合計だけです。年ごとの内訳は出していません。単年にすると町丁目のほとんどが1〜2件になり、1件が2件になった揺れを傾向のように読ませてしまうためです。年ごとの移り変わりは市全体の数字で見てください。</li>
  <li>時間帯の内訳は<b>市全体</b>の数字です。町丁目ごとには出していません。</li>
  <li>町丁目の大きさは市によって違います。<b>市をまたいだ比較には向きません。</b></li>
</ul>

<h2>免責</h2>
<p>元の統計が後から訂正・更新されることがあり、実際と異なる場合があります。
元データそのものの内容については、配布元にお問い合わせください。</p>

<h2>出典と利用規約</h2>
<ul>
  <li>犯罪認知件数：兵庫県警察（<a href="https://web.pref.hyogo.lg.jp/kk26/johoseisaku/opendata.html">兵庫県 オープンデータ</a>）</li>
  <li>町丁目境界・人口：<a href="https://www.e-stat.go.jp/gis">e-Stat 令和2年国勢調査 小地域</a></li>
</ul>

<div class="operator">
運営者　{config.OPERATOR}<br>
　　　　{config.OPERATOR_DESC}<br>
連絡先　<a href="{config.CONTACT_FORM}">訂正・削除の申し出フォーム</a>（{config.CONTACT_NOTE}）<br>
対象期間　{period}年の認知件数<br>
このページを作り直した日　{built_on}（数字が変わったとは限りません）／
取り込みは年1回、前年分が公開されたときです
</div>

<p><a href="{up}policy.html">このサイトに載せているもの・載せていないもの</a>　／
<a href="{config.SPEC_URL}">姉妹サイト共通仕様</a></p>"""


def page(title, desc, body, depth=1, canon=""):
    """canon … このページの正しいURL（SITE からの相対）。

    同じ中身が2つのURLで出る。`/cho/` と `/cho/index.html`、`/` と `/index.html`。
    canonical が無いと、検索側がどちらを正とするかを自分で決める。
    **SITE_URL から作る**ので、公開先が変わればひとりでに追従する
    （User-Agent・robots.txt と同じ理屈）。
    """
    up = "../" * depth
    canonical = (f'\n<link rel="canonical" href="{SITE}{canon}">'
                 f'\n<meta property="og:url" content="{SITE}{canon}">') if canon else ""
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">{canonical}
<link rel="stylesheet" href="{up}style.css">
</head>
<body>
<div class="wrap">
{body}
</div>
</body>
</html>
"""


def bars(rows, unit="件"):
    """横棒。rows は (見出し, 値, 補完的伏せか) の並び。値は伏せ処理ずみ。

    **幅も数字である。** n / その表の最大値 で正規化すると、最大値がどこかの升の
    実数なので、幅に掛けるだけで伏せた升が戻る（共通仕様3.2）。だから
      - 伏せた升は棒を出さない（幅0）。本当の0件と同じ見た目になる
      - 分母は実数の最大値ではなく、privacy.bar_scale の固定の目盛り
    数字のほうは「1-2」と「0」で書き分ける。
    """
    rows = [(r + (False,))[:3] for r in rows]
    scale = privacy.bar_scale([n for _, n, _ in rows])
    out = ["<table>"]
    for k, n, wh in rows:
        # 補完的伏せの升も棒を出さない。幅は3件以上であることを漏らす。
        w = 0.0 if wh else privacy.bar_width(n, scale)
        bar = f'<i style="width:{w:.1f}%"></i>' if w else ""
        state = (privacy.WITHHELD if wh
                 else (privacy.SMALL if n is None else privacy.SHOWN))
        out.append(
            f'<tr><th scope="row">{html.escape(k)}</th>'
            f'<td class="v num">{privacy.label_for_state(state, n, unit)}</td>'
            f'<td class="barcell">{bar}</td></tr>'
        )
    out.append("</table>")
    return "".join(out)


def shown_total(values):
    """表示している層の合計。共通仕様3.2「合計の升は出さない」に沿う書き方。

    出している升を足しただけの数なら出してよい（新しい情報を足していない）。
    ただし3つ守る。
      1.「総数」と書かない。「表示している手口の合計」と書く
      2. 伏せた升が1つでもあれば「◯件以上」と下限で書く
      3. この層で出していない手口があるならそう書く（notes() に書いてある）

    下限は「出ている升の和 ＋ 伏せた層の数」。伏せた層は必ず1件以上なので、
    これがいちばん狭い正しい下限になる。伏せた層の数は表に見えているので、
    この書き方で新しく分かることは何もない（合計は 下限〜下限＋伏せた層の数 の
    幅に残る）。和だけを下限にすると、全部伏せたときに「0件以上」になって
    何も言っていない文になる。
    """
    shown = [v for v in values if v is not None]
    hidden = len(values) - len(shown)
    # 伏せた升は1件以上（補完的伏せなら3件以上だが、下限は1でよい。
    # 3を足すと「3件以上の升がある」ことを漏らす）。
    n = sum(shown) + hidden
    return (f"{n:,}", "件") if not hidden else (f"{n:,}", "件以上")


def rate_cell(r, population, count):
    """率の欄。**出せないのと、伏せたのを、別の言葉で書く**（共通仕様3.2）。

    「—」を両方に使うと、人口0の町丁目と1〜2件で伏せた町丁目が同じに見える。
    """
    if r is not None:
        return f"{r:.2f}"
    reason = privacy.rate_reason(count if count is not None else 1, population)
    return "人口0" if reason == privacy.NO_POPULATION else "—"


def build_city(fc, built_on):
    P = fc["properties"]
    city = P["city"]
    period = P["period"].replace("-", "〜") + "年"
    by_code = {f["properties"]["code"]: f["properties"] for f in fc["features"]}
    any_wh = any(w for f in fc["features"] for w in f["properties"]["w"])
    note = notes(built_on, fc["properties"]["period"], any_withheld=any_wh)
    hours = [(b + "時", v, False) for b, v in zip(P["hour_bands"], P["city_hours"])]

    written = []
    for p in by_code.values():
        # 値はすべて伏せ処理ずみ。ここで足し直さない（通さない経路を作らない）。
        n, unit = shown_total(p["n"])

        rows, rates = [], []
        for i, layer in enumerate(P["layers"]):
            v = p["n"][i]
            rows.append((layer["name"], v, p["w"][i]))
            r = privacy.rate_for(v, p["jinko"])
            avg = P["city_rate"][i]
            rates.append(
                f'<tr><th scope="row">{html.escape(layer["name"])}</th>'
                f'<td class="v num">{rate_cell(r, p["jinko"], v)}</td>'
                f'<td class="v num">{"—" if avg is None else avg}</td></tr>')

        nb = []
        for code in p["neighbors"]:
            other = by_code.get(code)
            if other:
                nb.append(f'<li><a href="{code}.html">{html.escape(other["name"])}</a></li>')

        body = f"""  <p class="crumb"><a href="../index.html">地図</a>　›　{html.escape(city)}　›　{html.escape(p["name"])}</p>

  <header>
    <h1>{html.escape(p["name"])}（{html.escape(city)}）の窃盗認知件数</h1>
    <p>{period}に認知された分の合計／人口 <span class="num">{p["jinko"]:,}</span> 人（令和2年国勢調査）</p>
  </header>

  <div class="prose">
    <dl class="figures">
      <div><dt>表示している手口の合計（{period}）</dt>
        <dd class="num">{n}<small>{unit}</small></dd>
        <p class="sub">{html.escape("・".join(config.CITY_ONLY))}は町丁目には出していません</p></div>
    </dl>

    <h2>層ごとの件数（{period}）</h2>
    {bars(rows)}

    <h2>人口千人あたり（{period}）</h2>
    <table><tr><th scope="row"></th><td class="v num">この町丁目</td><td class="v num">{html.escape(city)}全体</td></tr>
    {"".join(rates)}</table>

    <h2>発生した時間帯（{html.escape(city)}全体・推定を含む）</h2>
    <p class="sub">この町丁目のものではありません。町丁目ごとには出していません。</p>
    {bars(hours)}

    <h2>隣り合う町丁目</h2>
    <ul class="neighbors">{"".join(nb) if nb else "<li>（ありません）</li>"}</ul>

    <p style="margin-top:18px"><a href="../index.html">地図で{html.escape(city)}全体を見る</a></p>
  </div>

  <footer>
    {note}
  </footer>"""

        # サイト名はハードコードしない。config を見る（名前を変えたとき1か所で済む）。
        title = (f'{city}{p["name"]}の窃盗認知件数 {P["period"]}年'
                 f'｜{config.SITE_NAME}')
        desc = (f'{city}{p["name"]}の{period}の窃盗認知件数（表示している手口の合計）は'
                f'{n}{unit}です。層ごとの件数と人口千人あたりの件数、隣り合う町丁目も'
                f'掲載しています。出典は兵庫県警察と国勢調査。')
        (OUT / f'{p["code"]}.html').write_text(
            page(title, desc, body, canon=f'/cho/{p["code"]}.html'), encoding="utf-8")
        written.append((p["code"], p["name"]))

    return written


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = json.loads((config.BUILD / "index.json").read_text(encoding="utf-8"))
    # **作った日であって、数字が変わった日ではない。** 名前で取り違えない
    built_on = index["generated_at"]

    all_pages, sections = [], []
    any_wh_all = False
    for c in index["cities"]:
        fc = json.loads((config.BUILD / f'{c["code"]}.geojson').read_text(encoding="utf-8"))
        any_wh_all |= any(w for f in fc["features"] for w in f["properties"]["w"])
        written = build_city(fc, built_on)
        all_pages += [w[0] for w in written]
        links = "".join(
            f'<li><a href="{code}.html">{html.escape(name)}</a></li>' for code, name in written
        )
        sections.append(
            f'<h2>{html.escape(c["name"])}（{len(written)}区画）</h2>'
            f'<ul class="neighbors">{links}</ul>'
        )
        print(f'  {c["name"]}  {len(written)} ページ')

    body = f"""  <p class="crumb"><a href="../index.html">地図にもどる</a></p>
  <header>
    <h1>町丁目の一覧</h1>
    <p>掲載している町丁目のすべて</p>
  </header>
  <div class="prose">
    {"".join(sections)}
  </div>
  <footer>{notes(built_on, index["period"], up="../", any_withheld=any_wh_all)}</footer>"""
    (OUT / "index.html").write_text(
        page(f"町丁目の一覧｜{config.SITE_NAME}",
             "掲載している町丁目の一覧です。市ごとに、町丁目ごとのページへ移動できます。",
             body, canon="/cho/"), encoding="utf-8")

    urls = [f"{SITE}/", f"{SITE}/policy.html", f"{SITE}/cho/"]
    urls += [f"{SITE}/cho/{code}.html" for code in all_pages]
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "".join(f"<url><loc>{u}</loc></url>\n" for u in urls)
               + "</urlset>\n")
    (SRC / "sitemap.xml").write_text(sitemap, encoding="utf-8")

    # robots.txt も config から作る。固定文字列で書くと SITE_URL と離れて古びる
    # （User-Agent と同じ理屈）。姉妹サイトと同じ形。
    #
    # 止めるのは、ページではないもの。公開する木にはデータとスクリプトが入るが、
    # 中身に問題があるからではなく、検索結果に出ても誰の役にも立たないため。
    # docs/ は止めない。突合率は人が読む記録で、policy から辿れるようにしてある。
    robots = ("User-agent: *\n"
              "Disallow: /data/\n"
              "Disallow: /scripts/\n"
              "Disallow: /tests/\n"
              "Disallow: /common/\n"
              "Disallow: /.github/\n"
              f"Sitemap: {SITE}/sitemap.xml\n")
    (SRC / "robots.txt").write_text(robots, encoding="utf-8")

    print(f"合計 {len(all_pages)} ページ ＋ 一覧 ＋ sitemap.xml")


if __name__ == "__main__":
    main()
