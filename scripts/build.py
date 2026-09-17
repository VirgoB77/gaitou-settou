"""県警CSVと境界データを結合して、地図が読むGeoJSONを作る。

サイトはこの出力だけを読む。実行時にCSVを読み直さない。

**この geojson は公開物そのもの。** ブラウザが伏せ字にしても、ファイルを開けば
実数が読めるなら伏せたことにならない（共通仕様5節「出力する値は必ず privacy を
通す。通さない経路を作らない」）。だから生の件数は書き出さず、
**画面が出す組み合わせだけを、伏せ処理を通した値で**持たせる。

親の合計も書かない。親を出すと、公開している子を引くだけで伏せた子が戻る
（共通仕様3.2）。突合率（市×年の全件数）はここから外し、docs/突合率.md に
割合だけを書く。
"""

import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import addr as addrlib
import config
import police
import privacy
import unknown

sys.stdout.reconfigure(encoding="utf-8")

SIMPLIFY = 0.00002   # 度。おおよそ2m。町丁目の形は保ったまま容量を落とす

# 隣り合っているとみなす距離（度）。おおよそ2m。
# 簡略化でわずかに開いた隙間を、隣ではないと誤判定しないため。
TOUCH = 0.00002

# 町丁目に出す期間は8年合計の1つだけ（config.WINDOW_YEARS）。
# 期間の合計と年ごとの両方を出すと引き算で戻るので、片方しか出さない。


def period_label():
    ys = [str(y) for y in config.YEARS]
    return f"{ys[0]}-{ys[-1]}"


def area_values(raw, years):
    """1区画ぶんの、層ごとの**真の件数**。まだ伏せていない。

    伏せるのは区画ごとではなく**まとまりごと**（市 × 層）。
    補完的伏せは、そのまとまり全体を見ないと決められない（共通仕様3.2）。
    """
    return [sum(raw[y]["teguchi"].get(t, 0) for y in years for t in layer["teguchi"])
            for layer in config.TOWN_LAYERS]


def city_masu(values_by_area):
    """市ぜんぶの真の件数から、公開する形を作る。

    values_by_area[区画][層] = 真の件数。
    戻り値は (n, w, 層ごとの合計)。
      n[区画][層]  … 出す数。伏せたものは None
      w[区画][層]  … True なら補完的伏せ（3件以上だが伏せた。画面は「非公開」）

    **ここが唯一の出口。** 件数を書き出す経路をほかに作らない（共通仕様5節）。
    合計は作らない。合計と内訳を両方出すと引き算で戻る（共通仕様3.2）。
    """
    n = [[None] * len(config.TOWN_LAYERS) for _ in values_by_area]
    w = [[False] * len(config.TOWN_LAYERS) for _ in values_by_area]
    totals = []
    for li in range(len(config.TOWN_LAYERS)):
        col = [row[li] for row in values_by_area]
        totals.append(sum(col))
        states = privacy.complement_suppress(col)
        for ai, (v, st) in enumerate(zip(col, states)):
            n[ai][li] = v if st == privacy.SHOWN else None
            w[ai][li] = st == privacy.WITHHELD
    return n, w, totals


def add_neighbors(features, geom_by_code):
    """隣り合う町丁目のコードを、各区画に持たせる。個別ページの導線に使う。"""
    from shapely import STRtree

    codes = [f["properties"]["code"] for f in features]
    geoms = [geom_by_code[c] for c in codes]
    tree = STRtree(geoms)

    for i, f in enumerate(features):
        near = tree.query(geoms[i].buffer(TOUCH))
        names = []
        for j in near:
            if j == i:
                continue
            if geoms[i].distance(geoms[j]) <= TOUCH:
                names.append(codes[j])
        f["properties"]["neighbors"] = sorted(names)


def fetched_on(city_code):
    """元CSVを取ってきた日。無ければ今日。"""
    pref = config.PREFS[config.city(city_code)["pref"]]
    days = []
    for year in config.YEARS:
        for teguchi in config.TEGUCHI:
            p = config.RAW / pref.csv_name(year, teguchi)
            if p.exists():
                days.append(dt.date.fromtimestamp(p.stat().st_mtime))
    return (max(days) if days else dt.date.today()).isoformat()


def build_city(city_code):
    # 地図まわりだけが使う外部ライブラリ（共通仕様9節の例外。README に理由がある）。
    # 升の作り方（area_masu）はこれに依らないので、テストから素で読み込める。
    import shapefile
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    c = config.city(city_code)
    pref = config.PREFS[c["pref"]]
    shp = config.RAW / f"境界_{c['name']}" / f"r2ka{city_code}"
    sf = shapefile.Reader(str(shp), encoding="cp932")
    bd = police.load_boundary(city_code)

    years = [str(y) for y in config.YEARS]
    stats = {y: defaultdict(lambda: {"teguchi": Counter(), "hour": Counter()})
             for y in years}
    report = {y: {"rows": 0, "unmatched": 0} for y in years}

    for year in config.YEARS:
        y = str(year)
        rows = police.load(city_code, year)
        report[y]["rows"] = len(rows)
        for r in rows:
            area, reason, _ = police.match(r, bd)
            if area is None:
                report[y]["unmatched"] += 1
                continue
            s = stats[y][area["key"]]
            s["teguchi"][r[config.COLS["teguchi"]]] += 1
            b = police.hour_band(r[config.COLS["hour"]])
            if b:
                s["hour"][b] += 1

    shapes = sf.shapes()
    features, geom_by_code, area_rows = [], {}, []
    # 市の升（counts_by_city）と市の時間帯。町丁目の升からは足し直さない。
    city_teguchi = {y: Counter() for y in years}
    city_hour = Counter()

    for area in bd.areas.values():
        geoms = [shape(shapes[i].__geo_interface__) for i in area["shape_indexes"]]
        geom = unary_union(geoms) if len(geoms) > 1 else geoms[0]
        geom = geom.simplify(SIMPLIFY, preserve_topology=True)
        code = area["key_codes"][0]
        geom_by_code[code] = geom

        raw = {y: {"teguchi": dict(stats[y][area["key"]]["teguchi"]),
                   "hour": dict(stats[y][area["key"]]["hour"])}
               if area["key"] in stats[y] else {"teguchi": {}, "hour": {}}
               for y in years}
        for y in years:
            city_teguchi[y].update(raw[y]["teguchi"])
            city_hour.update(raw[y]["hour"])

        plain = area_values(raw, years)
        area_rows.append(plain)

        key = addrlib.normalize(pref.name, c["name"], area["name"], city_code=city_code)
        if key["town_source"] == "unknown":
            # 推測で埋めない。空のまま記録する（共通仕様4節③・9節）
            unknown.note("町丁目が決められない（addr_key_townが空）",
                         f"{c['name']} {city_code}", area["name"])
        features.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                "code": code,
                "name": area["name"],
                "jinko": area["jinko"],
                "addr_key": key["addr_key"],
                "addr_key_town": key["addr_key_town"],
            },
        })

    # まとまり（市 × 層）ごとに伏せる。区画を1つずつ見ても補完的伏せは決まらない。
    n_all, w_all, layer_total = city_masu(area_rows)
    for f, n, w in zip(features, n_all, w_all):
        f["properties"]["n"] = n
        f["properties"]["w"] = w

    features.sort(key=lambda f: f["properties"]["code"])
    add_neighbors(features, geom_by_code)

    # 市平均。伏せた升の数に応じて桁を落とす（privacy.city_rate）。
    pop = sum(f["properties"]["jinko"] for f in features)
    avg = []
    for i in range(len(config.TOWN_LAYERS)):
        k = sum(1 for f in features if f["properties"]["n"][i] is None)
        avg.append(privacy.city_rate(layer_total[i], pop, k))

    fc = {
        "type": "FeatureCollection",
        "properties": {
            "city": c["name"],
            "city_code": city_code,
            "pref": pref.name,
            "center": c["center"],
            "zoom": c["zoom"],
            "period": period_label(),
            "years": years,
            # n は TOWN_LAYERS の順。合計は持たせない（共通仕様3.2）。
            "layers": [{"key": l["key"], "name": l["name"], "teguchi": l["teguchi"]}
                       for l in config.TOWN_LAYERS],
            # w が True の升は「非公開」（3件以上だが、まとまりを守るために伏せた）。
            # n が None で w が False の升は「1-2」。3つを見た目で分ける（共通仕様3.2）。
            "withheld_label": "非公開",
            # 町丁目では出さない手口。市の数字としてだけ出す。
            "city_only": list(config.CITY_ONLY),
            # 時間帯は市の数字。町丁目では出さない（町丁目の升の別の分割になり、
            # 手口の層と足し合わせると引き算の相手になる）。
            "hour_bands": list(config.HOUR_BANDS),
            "city_hours": [privacy.masked(city_hour[b]) for b in config.HOUR_BANDS],
            "class_total": config.CLASS_TOTAL,
            "class_rate": config.CLASS_RATE,
            "min_population": privacy.MIN_POPULATION,
            "max_suppress_count": privacy.MAX_SUPPRESS_COUNT,
            "suppress_label": privacy.bucket_count(1),
            "city_rate": avg,
            "population": pop,
            "areas": len(features),
            "source_url": pref.csv_base,
            "fetched_on": fetched_on(city_code),
            # 突合率（市×年の全件数）はここに置かない。市×全手口×年 の合計そのもので、
            # counts_by_city の公開値を引くと伏せた升が戻る（共通仕様3.2）。
        },
        "features": features,
    }

    out = config.BUILD / f"{city_code}.geojson"
    out.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    rows = sum(report[y]["rows"] for y in years)
    un = sum(report[y]["unmatched"] for y in years)
    print(f"{c['name']}  {out.name}  {out.stat().st_size:,} バイト  区画 {len(features)}")
    print(f"    {rows:,} 行  突合できず {un} 行  {un / rows * 100:.2f}%")
    small = sum(1 for f in features for v, w in
                zip(f["properties"]["n"], f["properties"]["w"]) if v is None and not w)
    withheld = sum(1 for f in features for w in f["properties"]["w"] if w)
    total = sum(len(f["properties"]["n"]) for f in features)
    print(f"    升 {total:,}  1-2で伏せた {small:,}  補完的に伏せた {withheld:,}"
          f"（{(small + withheld) / total * 100:.1f}%）")
    # city_teguchi は geojson には入れない。市の升を作るためだけに呼び出し元へ渡す。
    return fc, {"city": c["name"], "rows": rows, "unmatched": un}, city_teguchi


def write_suppress_report(fcs, city_teguchi):
    """なぜ伏せたかの記録。**金庫側にだけ置く。**

    公開する側に理由の欄を作ると、札をそろえた意味が消える（共通仕様6節）。
    でも記録は要る。あとから「なぜこの升が非公開なのか」を追えないと直せない。
    件数は書かない（書くと記録そのものが漏らす）。

    伏せた升は2種類ある。区別が要るのは、公開側では同じ札に見えるから。

      1-2   … 値が 1 か 2 だから伏せた
      非公開 … 3件以上だが、同じまとまりの 1-2 を守るために足した（補完的伏せ）

    **町丁目と市の升の両方を書く。** 片方だけだと、来年これを読む人が
    「補完が要らなかった」のか「そこを見ていない」のかを見分けられない。
    """
    lines = ["# 伏せた理由の記録", "",
             "**このファイルは公開しない。** 金庫側にだけ置く（共通仕様6節）。",
             "公開する `index.json` には理由の欄を作らない。作ると、",
             "まとまり単位で札をそろえた意味が消える。", "",
             "件数は書かない。記録そのものが漏らすため。", ""]
    for fc in fcs:
        P = fc["properties"]
        lines.append(f'## {P["city"]}')
        lines.append("")
        for i, layer in enumerate(P["layers"]):
            hidden = [f["properties"]["code"] for f in fc["features"]
                      if f["properties"]["n"][i] is None]
            comp = [f["properties"]["code"] for f in fc["features"]
                    if f["properties"]["w"][i] and f["properties"]["n"][i] is None]
            merged = any(f["properties"]["w"][i] for f in fc["features"])
            lines.append(f'### {layer["name"]}')
            lines.append("")
            lines.append(f'- 伏せた升 {len(hidden)}')
            lines.append(f'- 札 … {"非公開（そろえた）" if merged else "1-2"}')
            if merged:
                lines.append(f'- 補完的に足した升を含め、このまとまりの伏せた升は'
                             f'全部同じ札にした')
                lines.append(f'- 町丁目コード：{", ".join(comp)}')
            lines.append("")

    # 市の升（手口 × 年）。ここは補完的伏せを通していない。
    # この升を含む合計を公開していないので、1升ずつ伏せれば戻らない。
    # 通していないことを書き残す。書かないと、来年「見落とした」と区別がつかない。
    #
    # 「足し算の相手が無い」とは書かない。市の升は足すと町丁目の層の親 S になる
    # （`check_small_counts.py` はそうやって S を出している）。戻らない理由は
    # 関係が無いからではなく、**その合計を公開していない**から。
    lines.append("## 市の升（手口 × 年）")
    lines.append("")
    lines.append("補完的伏せは通していない。この升を含む合計を公開していないため、")
    lines.append("1升ずつ伏せれば戻らない（`check_small_counts.py` が毎回確かめる）。")
    lines.append("")
    lines.append("足し算の関係が無いわけではない。市の升を手口ごとに8年ぶん足すと、")
    lines.append("町丁目の層の親 S になる。戻らないのは、その合計を公開していないから。")
    lines.append("**公開物に市の合計を足すと、ここが崩れる。**")
    lines.append("")
    lines.append("伏せた升はすべて「値が 1 か 2 だから」。")
    lines.append("")
    for fc, teg_by_year in zip(fcs, city_teguchi):
        P = fc["properties"]
        lines.append(f'### {P["city"]}')
        lines.append("")
        for t in config.TEGUCHI:
            hid = [y for y in P["years"]
                   if privacy.masked(teg_by_year[y][t]) is None]
            if hid:
                lines.append(f'- {t} … 伏せた升 {len(hid)}（{", ".join(str(y) for y in hid)}年）  札 … 1-2')
        lines.append("")

    comp = sum(1 for fc in fcs for f in fc["features"]
               for wv in f["properties"]["w"] if wv)
    lines.append("## いま補完的に伏せている升")
    lines.append("")
    if comp:
        lines.append(f"{comp} 升。内訳は上の各層に書いた。")
    else:
        lines.append("0 升。どのまとまりも余裕が足りていて、")
        lines.append("補完を足さずに 1-2 を守れている（`check_small_counts.py` の")
        lines.append("「まとまりの余裕」を見ること）。**余裕は市や年を足すたびに変わる。**")
    lines.append("")
    (config.BUILD / "suppress-report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"伏せた理由の記録  data/build/suppress-report.md（公開しない）")


def cross_site_index(fcs, city_teguchi):
    """横断用の index.json（共通仕様6節）をサイトルートに出す。

    個票は「町丁目 × 層 × 期間」。**合計の升は作らない。**
    内訳（層）だけを出す（共通仕様3.2「合計の升と、内訳の升を、両方出さない」）。
    内訳は0件も含めて全部出す。欠けさせると「伏せた」のか「0件」のかが
    読者に分からなくなる。
    """
    records, counts = [], []
    for fc, teg_by_year in zip(fcs, city_teguchi):
        P = fc["properties"]
        period = P["period"]
        for f in fc["features"]:
            p = f["properties"]
            for i, layer in enumerate(P["layers"]):
                # 0件の層も出す。落とすと「伏せた」のか「0件」のかが
                # 読者に分からなくなる（共通仕様3.2「内訳は全部出す」）。
                total = p["n"][i]          # 伏せ処理ずみ（伏せたものは None）
                withheld = p["w"][i]
                records.append({
                    # 鍵には、あとから変わらないものだけを入れる（共通仕様9節）。
                    # 期間は窓を動かすたびに変わるので鍵に入れない。欄として持つ。
                    "id": f'{config.SITE_ID}:{P["city_code"]}:{p["code"]}:{layer["key"]}',
                    "title": f'{p["name"]}（{P["city"]}）{period}年の{layer["name"]}の窃盗認知件数',
                    "kind": f'犯罪統計/{layer["name"]}',
                    "date": f'{P["years"][-1]}-12-31',
                    "period": period,
                    "pref": P["pref"],
                    "city": P["city"],
                    "city_code": P["city_code"],
                    "addr": f'{P["city"]}{p["name"]}',
                    "addr_key": p["addr_key"],
                    "addr_key_town": p["addr_key_town"],
                    # 犯罪統計は当事者を持たない制度なので "none"（共通仕様6節）。
                    "party": "",
                    "party_kind": "none",
                    "url": f'{config.SITE_URL}/cho/{p["code"]}.html',
                    "source_url": P["source_url"],
                    "fetched_on": P["fetched_on"],
                    "population": p["jinko"],
                    "count": total,
                    # 機械は count_label で読み分ける（共通仕様6節）。
                    #   "1-2"   … 値は 1 か 2
                    #   "非公開" … 値は 1 以上。3 以上かもしれない（札をそろえたまとまり）
                    # **伏せた理由の欄は作らない。** 「1〜2件だから」「補完だから」の別を
                    # 公開側に書くと、札をそろえた意味が消える（共通仕様6節）。
                    # 理由は金庫側の data/build/suppress-report.md にだけ残す。
                    "count_label": P["withheld_label"] if withheld
                    else privacy.label_for(total),
                    "rate_per_1k": privacy.rate_for(total, p["jinko"]),
                    "rate_suppressed": privacy.rate_for(total, p["jinko"]) is None,
                })

        # 市の升は手口ごと × 年。年ごとにするのは、横断ハブが年表を作るため。
        # 町丁目は8年合計の層だけなので、市の年別と粗さがぶつからない。
        # 合計の升は作らない。
        pop = P["population"]
        for t in config.TEGUCHI:
            for y in P["years"]:
                n = teg_by_year[y][t]
                counts.append({
                    "city_code": P["city_code"],
                    "city": P["city"],
                    "kind": f"犯罪統計/{t}",
                    "period": y,
                    "count": privacy.masked(n),
                    "count_label": privacy.bucket_count(n),
                    "population": pop,
                    "rate_per_1k": privacy.rate_per_1k(n, pop),
                    "rate_suppressed": privacy.suppress_rate(n, pop),
                })

    return {
        "site": config.SITE_ID,
        "site_name": config.SITE_NAME,
        "generated_at": dt.date.today().isoformat(),
        "spec": config.SPEC_URL,
        "records": records,
        "counts_by_city": counts,
    }


def main():
    config.BUILD.mkdir(parents=True, exist_ok=True)
    fcs, cities, match, totals = [], [], [], []
    for c in config.CITIES:
        fc, m, teg_by_year = build_city(c["code"])
        fcs.append(fc)
        match.append(m)
        totals.append(teg_by_year)
        cities.append({
            "code": c["code"], "name": c["name"],
            "pref": fc["properties"]["pref"], "areas": fc["properties"]["areas"],
        })

    # 画面が読む市の一覧
    (config.BUILD / "index.json").write_text(
        json.dumps({
            "period": fcs[0]["properties"]["period"],
            "generated_at": dt.date.today().isoformat(),
            "cities": cities,
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    # 伏せた理由は金庫側にだけ残す（共通仕様6節）。公開用の木からは外す。
    write_suppress_report(fcs, totals)

    # 突合率は割合だけを docs に出す。行数（市×年の全件数）は公開物に書かない。
    lines = ["# 突合率", "",
             "県警CSVの住所を町丁目の区画に突き合わせた結果。",
             "CLAUDE.md 8節の判断基準は「1%未満」。",
             "",
             "行数そのものは書かない。市×全手口×年の合計になり、",
             "`index.json` の公開値を引くと伏せた升が戻るため（共通仕様3.2）。",
             "突き合わせできなかった行は `data/build/unmatched.csv` に理由つきで残している。",
             "", "| 市 | 突合できなかった割合 |", "|---|---:|"]
    for m in match:
        lines.append(f'| {m["city"]} | {m["unmatched"] / m["rows"] * 100:.2f}% |')
    tot_r = sum(m["rows"] for m in match)
    tot_u = sum(m["unmatched"] for m in match)
    lines += [f'| **合計** | **{tot_u / tot_r * 100:.2f}%** |', ""]
    (config.ROOT / "docs" / "突合率.md").write_text("\n".join(lines), encoding="utf-8")

    # 横断用（共通仕様6節）
    idx = cross_site_index(fcs, totals)
    out = config.ROOT / "index.json"
    out.write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\nindex.json（横断用）  {out.stat().st_size:,} バイト  "
          f"{len(idx['records'])} レコード  {len(idx['counts_by_city'])} 升")
    print(f"突合できなかった割合  {tot_u / tot_r * 100:.2f}%  → docs/突合率.md")

    # 知らないものを黙って捨てない（共通仕様9節）
    n = unknown.write_report()
    print("知らない列・値", f"★{n} 種類 → data/parse-unknown.md" if n else "なし")


if __name__ == "__main__":
    main()
