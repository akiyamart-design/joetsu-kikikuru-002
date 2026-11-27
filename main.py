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

# 気象庁 JSON URL候補 (新形式 r8 と 従来形式)
HOST = "https://" + "www." + "jma.go.jp"

WARNING_URLS = [
    HOST + "/bosai/warning/data/r8/150000.json",
    HOST + "/bosai/warning/data/warning/150000.json",
]
FLOOD_URLS = [
    HOST + "/bosai/flood/data/r8/flood_xml.json",
    HOST + "/bosai/flood/data/flood_xml.json",
]

# 気象警報・注意報コード表
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

LEVEL_TO_COLOR = {
    5: "黒（災害切迫・Lv5相当）",
    4: "紫（危険・Lv4相当）",
    3: "赤（警戒・Lv3相当）",
    2: "黄（注意・Lv2相当）",
    0: "白（危険度Lv2未満）",
}

def fetch_json_safely(urls):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    for url in urls:
        # 念のため強制的に www. を付与する安全装置
        safe_url = url.replace("https://jma.go.jp", "https://jma.go.jp")
        try:
            res = requests.get(safe_url, headers=headers, timeout=15, allow_redirects=False)
            print(f"URL: {safe_url} -> Status: {res.status_code}, Length: {len(res.text)}")
            if res.status_code == 200 and res.text.strip():
                return res.json()
        except Exception as e:
            print(f"Failed {safe_url}: {e}")
    return None
    
def fetch_jma_data():
    warn_data = fetch_json_safely(WARNING_URLS)
    if warn_data is None:
        raise RuntimeError("気象庁の警報JSONを取得できませんでした。上のURLログを確認してください。")

    flood_data = fetch_json_safely(FLOOD_URLS) or []

    # JSONがリスト形式(r8)か辞書形式(旧形式)かで処理を統一
    warn_reports = warn_data if isinstance(warn_data, list) else [warn_data]
    flood_reports = flood_data if isinstance(flood_data, list) else [flood_data]

    results = {}
    for code, city_name in TARGET_CITIES.items():
        active_warnings = []
        max_level = 0
        latest_report_time = ""

        # 1. 一般の警報・注意報を走査
        for report in warn_reports:
            rep_time = report.get("reportDatetime", "")
            # r8形式(warning.class20Items) と 旧形式(areaTypes[1].areas) の両対応
            class20_items = report.get("warning", {}).get("class20Items", [])
            if not class20_items and "areaTypes" in report:
                for at in report.get("areaTypes", []):
                    for area in at.get("areas", []):
                        if area.get("code") == code:
                            class20_items.append({"areaCode": code, "kinds": area.get("warnings", [])})

            for item in class20_items:
                if item.get("areaCode") == code:
                    if rep_time > latest_report_time:
                        latest_report_time = rep_time
                    for kind in item.get("kinds", []):
                        status = kind.get("status", "")
                        if status in ("発表", "継続"):
                            w_code = kind.get("code", "")
                            w_name, w_level = WARNING_CODES.get(w_code, (f"不明コード({w_code})", 0))
                            additions = kind.get("additions", [])
                            if additions:
                                w_name += f"[{'・'.join(additions)}]"
                            active_warnings.append(f"{w_name}({status})")
                            if w_level > max_level:
                                max_level = w_level

        # 2. 河川氾濫情報を走査
        for f_rep in flood_reports:
            for item in f_rep.get("warning", {}).get("class20Items", []):
                if item.get("areaCode") == code:
                    for kind in item.get("kinds", []):
                        if kind.get("status") in ("発表", "継続"):
                            f_code = kind.get("code", "")
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

            scopes = [
        "https://googleapis.com",
        "https://googleapis.com",
    ]

    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    gc = gspread.authorize(creds)
    worksheet = gc.open_by_key(sheet_id).sheet1

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
    print("スプレッドシートへの書き込みが完了しました！")

if __name__ == "__main__":
    jma_data = fetch_jma_data()
    append_to_sheet(jma_data)
