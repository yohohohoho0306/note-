#!/usr/bin/env python3
"""味のたねあかし 記事チェック用スクリプト

使い方:
    python3 tools/check.py articles/drafts/001_塩の役割.md
    python3 tools/check.py articles/drafts          (フォルダ内の記事をまとめて調べる)

記事ファイルが執筆ルールに合っているかを調べ、結果を日本語で表示します。
Python の標準機能だけで動きます。
NG が1つもなければ終了コード 0、NG があれば 1 を返します。

表示の意味:
    [OK]   問題なし
    [NG]   直す必要がある
    [注意] 決まり違反とは限らないが、目で確かめてほしい
"""

import re
import sys
from pathlib import Path

SERIES_NAME = "味のたねあかし"
HASHTAG = "#味のたねあかし"
MIN_CHARS = 1800
MAX_CHARS = 2200

# 管理欄の項目(この順番で記事の冒頭に置く)
HEADER_FIELDS = ["通し番号", "タイトル", "旬の食材", "状態", "投稿予定日"]
# 空欄でもよい項目
OPTIONAL_FIELDS = ["投稿予定日"]
STATES = ["未着手", "下書き", "添削済", "投稿済"]
# フォルダ名と、そこに置く記事の状態
FOLDER_STATES = {"drafts": "下書き", "reviewed": "添削済", "published": "投稿済"}

# 管理欄と本文を分ける区切り線(この文言を含む行)
SEPARATOR_RE = re.compile(r"この線より下を\s*note\s*に貼る")

FILENAME_RE = re.compile(r'^([0-9]{3})_([^\s/\\:*?"<>|]+)\.md$')
HEADER_RE = re.compile(
    r"^\s*[-*]?\s*(" + "|".join(HEADER_FIELDS) + r")\s*[:\uff1a]\s*(.*?)\s*$"
)
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
CONFIRM_RE = re.compile(r"【確認[:\uff1a]([^】]*)】")
CONFIRM_BAD_RE = re.compile(r"【確認(?![:\uff1a])")
HIGHLIGHT_RE = re.compile(r"==(.+?)==")
# 大さじ・小さじ・カップ
MEASURE_RE = re.compile(r"大さじ|小さじ|大匙|小匙|カップ")
# 直後に数がくるもの(分量として使っているもの)
QUANTITY_AFTER_RE = re.compile(r"\s*([0-9\uff10-\uff19½⅓⅔¼¾一二三四五六七八九十半])")
# ml・cc などの体積の表記
VOLUME_RE = re.compile(
    r"[0-9\uff10-\uff19]+(?:[./\uff0f][0-9\uff10-\uff19]+)?\s*"
    r"(?:ml|mL|ML|cc|CC|ミリリットル|リットル|ℓ)"
)

# 文字数を数えないもの
HR_RE = re.compile(r"^([-*_])(\s*\1){2,}$")
HASHTAG_LINE_RE = re.compile(r"^([#\uff03]\S+\s*)+$")
TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
HEADING_RE = re.compile(r"^#{1,6}\s+")
QUOTE_RE = re.compile(r"^(>\s*)+")
LIST_RE = re.compile(r"^([-*+]|\d+[.)])\s+")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
INLINE_MARK_RE = re.compile(r"==|\*\*|__|~~|[`|*]")
SPACE_RE = re.compile(r"\s+")

OK, NG, WARN = "OK", "NG", "注意"


def read_article(path):
    """記事を読み、<!-- --> の説明書きを消した行のリストを返す(行番号は保つ)。"""
    text = path.read_text(encoding="utf-8-sig")
    text = COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return text.splitlines()


def split_article(lines):
    """管理欄と本文に分ける。どちらも (行番号, 行) のリスト。

    区切り線が見つからないときは、本文を None にして返す。
    """
    for i, line in enumerate(lines):
        if SEPARATOR_RE.search(line):
            header = list(enumerate(lines[:i], start=1))
            body = list(enumerate(lines[i + 1:], start=i + 2))
            return header, body
    return list(enumerate(lines, start=1)), None


def parse_header(header):
    """管理欄から {項目名: 値} を取り出す。同じ項目が2回あれば最初のものを使う。"""
    fields = {}
    for _, line in header:
        m = HEADER_RE.match(line)
        if m and m.group(1) not in fields:
            fields[m.group(1)] = m.group(2)
    return fields


def remove_confirm(line):
    """【確認:…】の部分を消す。"""
    return CONFIRM_RE.sub("", line)


def count_line(line):
    """1行の中で、文字数に数える字の数を返す。"""
    s = remove_confirm(line).strip()
    if not s:
        return 0
    if HR_RE.match(s) or HASHTAG_LINE_RE.match(s) or TABLE_SEP_RE.match(s):
        return 0
    s = HEADING_RE.sub("", s)
    s = QUOTE_RE.sub("", s)
    s = LIST_RE.sub("", s)
    s = IMAGE_RE.sub("", s)
    s = LINK_RE.sub(r"\1", s)
    s = INLINE_MARK_RE.sub("", s)
    s = SPACE_RE.sub("", s)
    return len(s)


def count_body(body):
    """本文の文字数を数える。

    数えないもの:空白・改行、見出し・箇条書き・強調などの記号、
    区切り線、ハッシュタグだけの行、【確認:…】の部分。
    ==ハイライト== の中の文字は本文なので数える(== の記号は数えない)。
    """
    return sum(count_line(line) for _, line in body)


def paren_depths(line):
    """行の各文字が、かっこ(全角・半角)の何重目の内側にあるかを返す。"""
    depths = []
    depth = 0
    for ch in line:
        if ch in "(\uff08":
            depth += 1
            depths.append(depth)
        elif ch in ")\uff09":
            depths.append(depth)
            depth = max(0, depth - 1)
        else:
            depths.append(depth)
    return depths


def short(text, width=40):
    """表示用に行を短くする。"""
    text = text.strip()
    return text if len(text) <= width else text[:width] + "…"


# ---------------------------------------------------------------
# 各チェック。どれも (判定, 見出し, 詳しい説明のリスト) を返す。
# ---------------------------------------------------------------

def check_header(fields):
    """管理欄の項目がそろっているか。"""
    details = []
    for name in HEADER_FIELDS:
        if name not in fields:
            details.append(f"「{name}」の欄がありません")
        elif not fields[name] and name not in OPTIONAL_FIELDS:
            details.append(f"「{name}」が空欄です")
    number = fields.get("通し番号", "")
    if number and not re.fullmatch(r"[0-9]{3}", number):
        details.append(f"通し番号「{number}」は3桁の数字にしてください(例:001)")
    state = fields.get("状態", "")
    if state and state not in STATES:
        details.append(f"状態「{state}」は {'/'.join(STATES)} のどれかにしてください")
    date = fields.get("投稿予定日", "")
    if state == "投稿済" and not date:
        details.append("投稿済なのに投稿予定日(投稿日)が空欄です")
    date_note = []
    if date and not DATE_RE.match(date):
        date_note.append(f"投稿予定日「{date}」は 2026-11-01 の形にそろえてください")
    if details:
        return NG, "管理欄に足りない所があります", details + date_note
    if date_note:
        return WARN, "管理欄はそろっています", date_note
    return OK, "管理欄(通し番号・タイトル・旬の食材・状態・投稿予定日)がそろっています", []


def check_filename(path, fields):
    """ファイル名の決まりと、置き場所(フォルダ)と状態が合っているか。"""
    details = []
    m = FILENAME_RE.match(path.name)
    if not m:
        details.append("ファイル名は「001_テーマ名.md」の形にし、"
                       "空白と記号( / \\ : * ? \" < > | )は使わないでください")
    elif fields.get("通し番号") and m.group(1) != fields["通し番号"]:
        details.append(f"ファイル名の番号 {m.group(1)} と、"
                       f"管理欄の通し番号 {fields['通し番号']} が違います")
    folder = path.resolve().parent.name
    state = fields.get("状態", "")
    expected = FOLDER_STATES.get(folder)
    if expected and state and state != expected:
        details.append(f"{folder} フォルダの記事なのに、状態が「{state}」です"
                       f"(「{expected}」のはず)")
    if details:
        return NG, "ファイル名・置き場所に問題があります", details
    return OK, "ファイル名・置き場所は決まりどおりです", []


def check_length(body):
    """本文の文字数。"""
    n = count_body(body)
    msg = f"本文 {n}字(決まりは {MIN_CHARS}〜{MAX_CHARS}字)"
    if n < MIN_CHARS:
        return NG, msg, [f"あと {MIN_CHARS - n}字以上ふやしてください"]
    if n > MAX_CHARS:
        return NG, msg, [f"{n - MAX_CHARS}字以上へらしてください"]
    return OK, msg, []


def check_hashtag(body):
    """本文の最後が #味のたねあかし になっているか。"""
    last = ""
    last_no = None
    for no, line in reversed(body):
        s = remove_confirm(line).strip()
        if s:
            last, last_no = s, no
            break
    if last.endswith(HASHTAG):
        return OK, f"本文の最後が {HASHTAG} になっています", []
    if last.endswith("＃" + SERIES_NAME):
        return NG, "末尾のハッシュタグの # が全角です", [
            f"{last_no}行目:半角の {HASHTAG} にしてください"]
    where = f"{last_no}行目「{short(last)}」" if last_no else "本文が空です"
    return NG, f"本文の最後が {HASHTAG} になっていません", [
        f"最後の行:{where}"]


def check_title(fields, body):
    """タイトルに連載名が入っていないか。本文に # の見出し(タイトル)がないか。"""
    details = []
    title = fields.get("タイトル", "")
    if SERIES_NAME in title:
        details.append(f"管理欄のタイトル「{title}」に連載名が入っています")
    for no, line in body:
        if re.match(r"^#\s+", line) and SERIES_NAME in line:
            details.append(f"{no}行目の見出しに連載名が入っています")
    if details:
        return NG, "タイトルに「味のたねあかし」が入っています", details
    h1 = [no for no, line in body if re.match(r"^#\s+", line)]
    if h1:
        return WARN, "タイトルに連載名は入っていません", [
            f"{no}行目:本文に「# 」の見出しがあります。タイトルは note の"
            "タイトル欄に入れるので、本文の見出しは ## と ### を使います"
            for no in h1]
    return OK, "タイトルに連載名は入っていません", []


def check_measures(body):
    """大さじ・小さじ・カップが、かっこの外に出ていないか。"""
    ng, warn = [], []
    for no, line in body:
        s = remove_confirm(line)
        depths = paren_depths(s)
        for m in MEASURE_RE.finditer(s):
            if depths[m.start()] > 0:
                continue
            if QUANTITY_AFTER_RE.match(s, m.end()):
                ng.append(f"{no}行目:「{m.group(0)}」がかっこの外にあります"
                          f"「{short(s)}」")
            else:
                warn.append(f"{no}行目:「{m.group(0)}」がかっこの外にあります。"
                            f"分量でなければそのままでかまいません「{short(s)}」")
        for m in VOLUME_RE.finditer(s):
            if depths[m.start()] == 0:
                warn.append(f"{no}行目:「{m.group(0)}」はグラム表記にできないか"
                            "確かめてください")
    if ng:
        return NG, "大さじ・小さじ・カップがかっこの外にあります", [
            *ng, *warn, "分量はグラム表記にし、200g(大さじ1)のようにかっこ内に残します"]
    if warn:
        return WARN, "分量の表記で確かめてほしい所があります", warn
    return OK, "大さじ・小さじ・カップは、すべてかっこ内です", []


def check_highlights(body, fields):
    """==ハイライト== の直後に【確認:…】があるか。数はいくつか。"""
    ng, warn = [], []
    n_high = n_conf = 0
    for no, line in body:
        if line.count("==") % 2:
            ng.append(f"{no}行目:== が閉じていません(ハイライトは1行の中で閉じます)")
        for m in HIGHLIGHT_RE.finditer(line):
            n_high += 1
            if not CONFIRM_RE.match(line[m.end():].lstrip(" 　")):
                ng.append(f"{no}行目:ハイライトの直後に【確認:理由】がありません"
                          f"「{short(m.group(1), 30)}」")
        for m in CONFIRM_RE.finditer(line):
            n_conf += 1
            if not m.group(1).strip():
                ng.append(f"{no}行目:【確認:】の理由が空です")
            if not line[:m.start()].rstrip(" 　").endswith("=="):
                warn.append(f"{no}行目:ハイライトのない【確認:…】があります"
                            f"「{short(m.group(1), 30)}」")
        for m in CONFIRM_BAD_RE.finditer(line):
            ng.append(f"{no}行目:【確認】は【確認:理由】の形にしてください")
    state = fields.get("状態", "")
    left = n_high + n_conf
    if left and state == "投稿済":
        ng.append(f"投稿済なのに、確認の印が残っています(ハイライト {n_high}か所、"
                  f"【確認】{n_conf}か所)。投稿前にすべて消します")
    elif left and state == "添削済":
        warn.append(f"添削済なのに、確認の印が残っています(ハイライト {n_high}か所、"
                    f"【確認】{n_conf}か所)。本人に確かめてください")
    title = f"ハイライト {n_high}か所(【確認:…】{n_conf}か所)"
    if ng:
        return NG, title + "。印の付け方に問題があります", ng + warn
    if warn:
        return WARN, title, warn
    if n_high:
        return OK, title + "。すべてに【確認:…】がついています", []
    return OK, "ハイライトはありません", []


# ---------------------------------------------------------------
# 実行
# ---------------------------------------------------------------

def check_file(path):
    """1本の記事を調べて結果を表示し、(NG の数, 注意の数) を返す。"""
    print(f"=== {path} ===")
    lines = read_article(path)
    header, body = split_article(lines)
    fields = parse_header(header)

    results = [check_header(fields), check_filename(path, fields)]
    if body is None:
        results.append((NG, "区切り線が見つかりません", [
            "管理欄と本文の間に「この線より下を note に貼る」と書いた"
            "区切り線を置いてください(templates/article.md を参照)"]))
    else:
        results += [
            check_length(body),
            check_hashtag(body),
            check_title(fields, body),
            check_measures(body),
            check_highlights(body, fields),
        ]

    n_ng = n_warn = 0
    for status, title, details in results:
        print(f"[{status}] {title}")
        for d in details:
            print(f"       - {d}")
        n_ng += status == NG
        n_warn += status == WARN
    if n_ng:
        print(f"--> 直す所があります(NG {n_ng}件、注意 {n_warn}件)")
    elif n_warn:
        print(f"--> NG はありません。注意 {n_warn}件を確かめてください")
    else:
        print("--> 問題ありません")
    print()
    return n_ng, n_warn


def collect_files(args):
    """引数のファイル・フォルダから、調べる記事ファイルを集める。"""
    files, missing = [], []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            files += sorted(f for f in p.glob("*.md") if f.is_file())
        elif p.is_file():
            files.append(p)
        else:
            missing.append(arg)
    return files, missing


def main(args):
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    files, missing = collect_files(args)
    for arg in missing:
        print(f"[NG] ファイルが見つかりません:{arg}")
    if not files and not missing:
        print("調べる記事ファイル(.md)がありません")
    total_ng, total_warn = len(missing), 0
    for path in files:
        n_ng, n_warn = check_file(path)
        total_ng += n_ng
        total_warn += n_warn
    if len(files) > 1:
        print(f"*** {len(files)}本を調べました:NG {total_ng}件、注意 {total_warn}件 ***")
    return 1 if total_ng else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
