import os
import json
from datetime import datetime, timezone, timedelta
import requests
import gspread
from google.oauth2.service_account import Credentials

# 日本時間 (JST)
JST = timezone(timedelta(hours=+9), 'JST')

# 対象3市の気象庁 class20s コード (7桁)
TARGET_CITIES = {
    "1522200": "上越市",
    "1521700": "妙高市",
    "1521600": "糸魚川市",
}

# 新潟県 (150000) の警報・注意報JSON (2026年5月改定 r8形式)
WARNING_URL = "https://jma.go.jp"
# 河川氾濫（指定河川洪水予報）JSON
FLOOD_URL = "https://jma.go.jp"

# 気象警報・注意報コード表 (r8形式)
WARNING_CODES = {
    "33": ("レベル５大雨特別警報", 5),
    "39": ("レベル５土砂災害特別警報", 5),
    "38": ("レベル５高潮特別警報", 5),
    "32": ("暴風雪特別警報", 0),
    "35": ("暴風特別警報", 0),
    "36": ("大雪特別警報", 0),
    "37": ("波浪特別警報", 0),
    "43": ("レベル４大雨危険警報", 4),
    "49": ("レベル４土砂災害危険警報", 4),
    "48": ("レベル４高潮危険警報", 4),
    "03": ("レベル３大雨警報", 3),
    "09": ("レベル３土砂災害警報", 3),
    "08": ("レベル３高潮警報", 3),
    "02": ("暴風雪警報", 0),
    "05": ("暴風警報", 0),
    "06": ("大雪警報", 0),
    "07": ("波浪警報", 0),
    "10": ("レベル２大雨注意報", 2),
    "29": ("レベル２土砂災害注意報", 2),
    "19": ("レベル２高潮注意報", 2),
    "12": ("大雪注意報", 0),
    "13": ("風雪注意報", 0),
    "14": ("雷注意報", 0),
    "15": ("強風注意報", 0),
    "16": ("波浪注意報", 0),
    "17": ("融雪注意報", 0),
    "20": ("濃霧注意報", 0),
    "21": ("乾燥注意報", 0),
    "22": ("なだれ注意報", 0),
    "23": ("低温注意報", 0),
    "24": ("霜注意報", 0),
    "25": ("着氷注意報", 0),
    "26": ("着雪注意報", 0),
}

# 危険度レベル -> キキクル危険度色
LEVEL_TO_COLOR = {
    5: "黒（災害切迫・Lv5相当）",
    4: "紫（危険・Lv4相当）",
    3: "赤（警戒・Lv3相当）",
    2: "黄（注意・Lv2相当）",
    0: "白（危険度Lv2未満）",
}

def fetch_jma_data():
    headers = {"User-Agent": "JMA-Alert-Tracker/1.0"}
    res_warn = requests.get(WARNING_URL, headers=headers, timeout=15)
    res_warn.raise_for_status()
    warn_reports = res_warn.json()

    # 氾濫情報も取得（エラー時は空リスト扱い）
    flood_reports = []
    try:
        res_flood = requests.get(FLOOD_URL, headers=headers, timeout=15)
        if res_flood.status_code == 200:
            flood_reports = res_flood.json()
    except Exception:
        pass

    results = {}
    for code, city_name in TARGET_CITIES.items():
        active_warnings = []
        max_level = 0
        latest_report_time = ""

        # 1. 一般の警報・注意報 (VPWW55〜61) を全報走査
        for report in warn_reports:
            rep_time = report.get("reportDatetime", "")
            class20_items = report.get("warning", {}).get("class20Items", [])
            for item in class20_items:
                if item.get("areaCode") == code:
                    if rep_time > latest_report_time:
                        latest_report_time = rep_time
                    for kind in item.get("kinds", []):
                        status = kind.get("status", "")
                        # 「発表」「継続」のみ抽出（「解除」は除外）
                        if status in ("発表", "継続"):
                            w_code = kind.get("code", "")
                            w_name, w_level = WARNING_CODES.get(w_code, (f"不明コード({w_code})", 0))
                            additions = kind.get("additions", [])
                            if additions:
                                w_name += f"[{'・'.join(additions)}]"
                            active_warnings.append(f"{w_name}({status})")
                            if w_level > max_level:
                                max_level = w_level

        # 2. 河川氾濫情報 (flood_xml.json) の該当市町村走査
        for f_rep in flood_reports if isinstance(flood_reports, list) else []:
            for item in f_rep.get("warning", {}).get("class20Items", []):
                if item.get("areaCode") == code:
                    for kind in item.get("kinds", []):
                        if kind.get("status") in ("発表", "継続"):
                            f_code = kind.get("code", "")
                            # 氾濫コード: 30=Lv5氾濫特別警報, 40=Lv4氾濫危険警報, 01=Lv3氾濫警報, 20-22=Lv2氾濫注意報
                            if f_code == "30":
                                active_warnings.append("レベル５氾濫特別警報")
                                max_level = max(max_level, 5)
                            elif f_code == "40":
                                active_warnings.append("レベル４氾濫危険警報")
                                max_level = max(max_level, 4)
                            elif f_code == "01":
                                active_warnings.append("レベル３氾濫警報")
                                max_level = max(max_level, 3)
                            elif f_code in ("20", "21", "22"):
                                active_warnings.append("レベル２氾濫注意報")
                                max_level = max(max_level, 2)

        results[code] = {
            "city": city_name,
            "color": LEVEL_TO_COLOR[max_level],
            "warnings_text": "、".join(active_warnings) if active_warnings else "発表なし",
            "report_time": latest_report_time,
        }
    return results

def append_to_sheet(data):
    creds_json = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
    sheet_id = os.environ["SPREADSHEET_ID"]

    scopes = ["https://googleapis.com"]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    gc = gspread.authorize(creds)
    worksheet = gc.open_by_key(sheet_id).sheet1

    # 1行目が空ならヘッダーを追加
    if not worksheet.row_values(1):
        worksheet.append_row([
            "取得日時(JST)",
            "市町村名",
            "キキクル危険度色",
            "発表中の警報・注意報一覧",
            "最新報の発表時刻"
        ])

    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for code in ["1522200", "1521700", "1521600"]:
        info = data[code]
        rows.append([
            now_str,
            info["city"],
            info["color"],
            info["warnings_text"],
            info["report_time"],
        ])

    worksheet.append_rows(rows, value_input_option="USER_ENTERED")

if __name__ == "__main__":
    jma_data = fetch_jma_data()
    append_to_sheet(jma_data)
