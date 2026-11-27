import os
import json
from datetime import datetime, timezone, timedelta
import requests
import gspread
from google.oauth2.service_account import Credentials

JST = timezone(timedelta(hours=+9), 'JST')
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

TARGET_CITIES = {
    "1522200": "上越市",
    "1521700": "妙高市",
    "1521600": "糸魚川市",
}

HOST = "https://" + "www." + "jma.go.jp"

WARNING_URLS = [
    HOST + "/bosai/warning/data/r8/150000.json",
    HOST + "/bosai/warning/data/warning/150000.json",
]
FLOOD_URLS = [
    HOST + "/bosai/flood/data/r8/flood_xml.json",
    HOST + "/bosai/flood/data/flood_xml.json",
]

WARNING_CODES = {
    "33": ("レベル５大雨特別警報", 5, "inundation", "黒（Lv5特別警報）"),
    "39": ("レベル５土砂災害特別警報", 5, "land", "黒（Lv5特別警報）"),
    "38": ("レベル５高潮特別警報", 5, "other", "黒（Lv5特別警報）"),
    "32": ("暴風雪特別警報", 5, "wind_wave", "黒（特別警報）"),
    "35": ("暴風特別警報", 5, "wind_wave", "黒（特別警報）"),
    "36": ("大雪特別警報", 5, "other", "黒（特別警報）"),
    "37": ("波浪特別警報", 5, "wind_wave", "黒（特別警報）"),
    "43": ("レベル４大雨危険警報", 4, "inundation", "紫（Lv4危険警報）"),
    "49": ("レベル４土砂災害危険警報", 4, "land", "紫（Lv4危険警報）"),
    "48": ("レベル４高潮危険警報", 4, "other", "紫（Lv4危険警報）"),
    "03": ("レベル３大雨警報", 3, "inundation", "赤（Lv3警報）"),
    "09": ("レベル３土砂災害警報", 3, "land", "赤（Lv3警報）"),
    "08": ("レベル３高潮警報", 3, "other", "赤（Lv3警報）"),
    "02": ("暴風雪警報", 3, "wind_wave", "赤（警報）"),
    "05": ("暴風警報", 3, "wind_wave", "赤（警報）"),
    "06": ("大雪警報", 3, "other", "赤（警報）"),
    "07": ("波浪警報", 3, "wind_wave", "赤（警報）"),
    "10": ("レベル２大雨注意報", 2, "inundation", "黄（Lv2注意報）"),
    "29": ("レベル２土砂災害注意報", 2, "land", "黄（Lv2注意報）"),
    "19": ("レベル２高潮注意報", 2, "other", "黄（Lv2注意報）"),
    "12": ("大雪注意報", 2, "other", "黄（注意報）"),
    "13": ("風雪注意報", 2, "wind_wave", "黄（注意報）"),
    "14": ("雷注意報", 2, "other", "黄（注意報）"),
    "15": ("強風注意報", 2, "wind_wave", "黄（注意報）"),
    "16": ("波浪注意報", 2, "wind_wave", "黄（注意報）"),
    "17": ("融雪注意報", 2, "other", "黄（注意報）"),
    "20": ("濃霧注意報", 2, "other", "黄（注意報）"),
    "21": ("乾燥注意報", 2, "other", "黄（注意報）"),
    "22": ("なだれ注意報", 2, "other", "黄（注意報）"),
    "23": ("低温注意報", 2, "other", "黄（注意報）"),
    "24": ("霜注意報", 2, "other", "黄（注意報）"),
    "25": ("着氷注意報", 2, "other", "黄（注意報）"),
    "26": ("着雪注意報", 2, "other", "黄（注意報）"),
}

LEVEL_TO_COLOR = {
    5: "黒（災害切迫・Lv5相当）",
    4: "紫（危険・Lv4相当）",
    3: "赤（警戒・Lv3相当）",
    2: "黄（注意・Lv2相当）",
    0: "白（危険度Lv2未満）",
}

def format_dt_with_weekday(dt):
    w = WEEKDAYS[dt.weekday()]
    return dt.strftime(f"%Y-%m-%d %H:%M:%S ({w})")

def format_date_banner(dt):
    w = WEEKDAYS[dt.weekday()]
    return dt.strftime(f"━━ %Y-%m-%d ({w}) ━━")

def parse_iso_datetime(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return format_dt_with_weekday(dt.astimezone(JST))
    except Exception:
        return iso_str

def fetch_json_safely(urls):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
    }
    for url in urls:
        safe_url = url.replace("https://jma.go.jp", "https://jma.go.jp")
        try:
            res = requests.get(safe_url, headers=headers, timeout=15, allow_redirects=False)
            if res.status_code == 200 and res.text.strip():
                return res.json()
        except Exception as e:
            print(f"Failed {safe_url}: {e}")
    return None

def fetch_jma_data():
    warn_data = fetch_json_safely(WARNING_URLS)
    if warn_data is None:
        raise RuntimeError("気象庁の警報JSONを取得できませんでした。")

    flood_data = fetch_json_safely(FLOOD_URLS) or []
    warn_reports = warn_data if isinstance(warn_data, list) else [warn_data]
    flood_reports = flood_data if isinstance(flood_data, list) else [flood_data]

    results = {}
    for code, city_name in TARGET_CITIES.items():
        active_warnings = []
        max_level = 0
        latest_report_time = ""

        cat_lv = {"land": 0, "inundation": 0, "flood": 0, "wind_wave": 0, "other": 0}
        cat_txt = {"land": "白", "inundation": "白", "flood": "白", "wind_wave": "白", "other": "白"}

        def update_category(cat, lv, label):
            if lv > cat_lv[cat]:
                cat_lv[cat] = lv
                cat_txt[cat] = label

        for report in warn_reports:
            rep_time = report.get("reportDatetime", "")
            class20_items = report.get("warning", {}).get("class20Items", [])
            if not class20_items and "areaTypes" in report:
                for at in report.get("areaTypes", []):
                    for area in at.get("areas", []):
                        if area.get("code") == code:
                            class20_items += [{"areaCode": code, "kinds": area.get("warnings", [])}]

            for item in class20_items:
                if item.get("areaCode") == code:
                    if rep_time > latest_report_time:
                        latest_report_time = rep_time
                    for kind in item.get("kinds", []):
                        status = kind.get("status", "")
                        if status in ("発表", "継続"):
                            w_code = kind.get("code", "")
                            w_name, w_lv, w_cat, w_disp = WARNING_CODES.get(
                                w_code, (f"不明({w_code})", 2, "other", "黄")
                            )
                            additions = kind.get("additions", [])
                            if additions:
                                w_name += f"[{'・'.join(additions)}]"
                            active_warnings += [f"{w_name}({status})"]
                            max_level = max(max_level, w_lv)
                            update_category(w_cat, w_lv, w_disp)

        for f_rep in flood_reports:
            for item in f_rep.get("warning", {}).get("class20Items", []):
                if item.get("areaCode") == code:
                    for kind in item.get("kinds", []):
                        if kind.get("status") in ("発表", "継続"):
                            f_code = kind.get("code", "")
                            if f_code == "30":
                                active_warnings += ["レベル５氾濫特別警報"]
                                max_level = max(max_level, 5)
                                update_category("flood", 5, "黒（Lv5特別警報）")
                            elif f_code == "40":
                                active_warnings += ["レベル４氾濫危険警報"]
                                max_level = max(max_level, 4)
                                update_category("flood", 4, "紫（Lv4危険警報）")
                            elif f_code == "01":
                                active_warnings += ["レベル３氾濫警報"]
                                max_level = max(max_level, 3)
                                update_category("flood", 3, "赤（Lv3警報）")
                            elif f_code in ("20", "21", "22"):
                                active_warnings += ["レベル２氾濫注意報"]
                                max_level = max(max_level, 2)
                                update_category("flood", 2, "黄（Lv2注意報）")

        results[code] = {
            "city": city_name,
            "max_level_num": max_level,
            "current_color": LEVEL_TO_COLOR[max_level],
            "report_time": parse_iso_datetime(latest_report_time),
            "land": cat_txt["land"],
            "inundation": cat_txt["inundation"],
            "flood": cat_txt["flood"],
            "wind_wave": cat_txt["wind_wave"],
            "other": cat_txt["other"],
            "warnings_text": "、".join(active_warnings) if active_warnings else "発表なし",
        }
    return results

def get_level_rank(color_text):
    if color_text.startswith("黒"): return 5
    if color_text.startswith("紫"): return 4
    if color_text.startswith("赤"): return 3
    if color_text.startswith("黄"): return 2
    return 0

def update_sheets_descending(data):
    creds_json = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
    sheet_id = os.environ["SPREADSHEET_ID"]

    g_host = "https://" + "www." + "googleapis.com"
    scopes = [
        g_host + "/auth/spreadsheets",
        g_host + "/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(sheet_id)

    now_dt = datetime.now(JST)
    now_str = format_dt_with_weekday(now_dt)
    today_date_str = now_dt.strftime("%Y-%m-%d")

    for code, city_name in TARGET_CITIES.items():
        info = data[code]
        try:
            ws = spreadsheet.worksheet(city_name)
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=city_name, rows=1000, cols=15)
            ws.update(values=[
                [city_name, "", "", "", "", "", "", "", "", "", ""],
                ["取得日時(曜日)", "発表日時(曜日)", "前回最大Lv", "今回最大Lv", "変動", "土砂Lv", "浸水Lv", "洪水Lv", "風・波Lv", "その他Lv", "発表内容詳細"]
            ], range_name="A1:K2")

        all_values = ws.get_all_values()
        # 3行目以降（インデックス2以降）から、日付区切り行("━━"始まり)を除いた直近のデータ行を探す
        latest_data_row = None
        for r in all_values[2:]:
            if r and r.copy().pop(0) and not r.copy().pop(0).startswith("━━"):
                latest_data_row = r
                break

        if latest_data_row and len(latest_data_row) >= 4:
            prev_dt_str = latest_data_row.copy().pop(0)
            prev_date_str = prev_dt_str[:10]  # "YYYY-MM-DD" を抽出
            prev_color = latest_data_row.copy().pop(3)
            prev_rank = get_level_rank(prev_color)
            curr_rank = info["max_level_num"]
            if curr_rank > prev_rank:
                diff_text = "▲ 警戒上昇"
            elif curr_rank < prev_rank:
                diff_text = "▼ 警戒低下"
            else:
                diff_text = "変動なし"
        else:
            prev_date_str = ""
            prev_color = "初回取得"
            diff_text = "-"

        new_row = [
            now_str,
            info["report_time"],
            prev_color,
            info["current_color"],
