"""完全観測かどうかを、**取得段が持った印だけで**決める。推測で埋めない。

印は3値。

    はい         確かめた
    いいえ       確かめて、足りなかった
    分からない   確かめられなかった（印が無いのも、これと同じ）

**完全観測 = 真 にできるのは、必要な印が全部「はい」のときだけ。**
「分からない」を「はい」に補わない。HTTP 200 や保存ファイルが在ることから推し量らない。

差分（増えた・消えた）を出してよいのは、直前の完全観測と今回の完全観測を比べるときだけ。
"""
HAI, IIE, WAKARANAI = "はい", "いいえ", "分からない"
SANCHI = (HAI, IIE, WAKARANAI)

# 題材ごとに、この中から要るものを選ぶ
SHIRUSHI = (
    "入口に届いた",
    "必要本文を受け取った",
    "ページ送りを最後まで受け取った",
    "辿る対象の失敗0",
    "上限未到達",
    "全件数表示と一致",
    "最終URLが承認範囲内",
    "private保存成功",
    "解析できた",
)


def kimeru(shirushi, hitsuyou):
    """(完全観測, 理由)。完全観測は True / False / None（未判定）。

    - 必要な印に1つでも「いいえ」→ False
    - 「いいえ」は無いが「分からない」か欠けがある → None
    - 全部「はい」→ True
    """
    if not hitsuyou:
        return None, "必要な印が決まっていない"
    shirushi = shirushi or {}
    iie = [k for k in hitsuyou if shirushi.get(k) == IIE]
    if iie:
        return False, "いいえ：" + "、".join(iie)
    wakaranai = [k for k in hitsuyou if shirushi.get(k) not in (HAI, IIE)]
    if wakaranai:
        return None, "分からない：" + "、".join(wakaranai)
    return True, "必要な印が全部「はい」"


def zero_gyou(mae_kensu, ima_kensu, zero_no_konkyo):
    """前の完全観測が1件以上で今回0件なら、**原典の「0件」表示が無いかぎり未判定。**

    返り値は、今回の印にかぶせる「解析できた」の値（はい／分からない）。
    """
    if ima_kensu == 0 and (mae_kensu or 0) > 0 and zero_no_konkyo is not True:
        return WAKARANAI
    return HAI


def kyugen(mae_kagi, ima_kagi):
    """前の完全観測から、半分以上が一度に消えたか。**消えたと書く前に止まる形。**

    しきい値（半分）は仮。統括の決定が出るまでは、この値で止める側に倒す。
    """
    mae_kagi, ima_kagi = set(mae_kagi or ()), set(ima_kagi or ())
    if not mae_kagi:
        return False
    return len(mae_kagi - ima_kagi) * 2 >= len(mae_kagi)


def sabun_dashite_yoi(mae, ima):
    """(出してよいか, 理由)。mae・ima は {"kanzen", "moto", "houshiki", ...} の観測。

    直前の完全観測と今回の完全観測で、観測元と取得方式が同じときだけ。
    """
    if not mae:
        return False, "比べる相手が無い（初回・直前の完全観測が無い）"
    if ima.get("kanzen") is not True:
        return False, "今回が完全観測でない"
    if mae.get("kanzen") is not True:
        return False, "前回が完全観測でない"
    if mae.get("moto") != ima.get("moto"):
        return False, "観測元が変わった"
    if mae.get("houshiki") != ima.get("houshiki"):
        return False, "取得方式が変わった"
    return True, ""
