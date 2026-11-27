import os, json, requests, gspread
from datetime import datetime, timezone, timedelta
from google.oauth2.service_account import Credentials

JST = timezone(timedelta(hours=+9), 'JST')
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]
TARGET_CITIES = {"1522200": "上越市", "1521700": "妙高市", "1521600": "糸魚川市"}

HOST = "https://" + "www." + "jma.go.jp"
WARNING_URLS = [HOST + "/bosai/warning/data/r8/150000.json", HOST + "/bosai/warning/data/warning/150000.json"]
FLOOD_URLS = [HOST + "/bosai/flood/data/r8/flood_xml.json", HOST + "/bosai/flood/data/flood_xml.json"]

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
LEVEL_TO_COLOR = {5: "黒（災害切迫・Lv5相当）", 4: "紫（危険・Lv4相当）", 3: "赤（警戒・Lv3相当）", 2: "黄（注意・Lv2相当）", 0: "白（危険度Lv2未満）"}

def fmt_dt(dt):
    return dt.strftime(f"%Y-%m-%d %H:%M:%S ({WEEKDAYS[dt.weekday()]})")

def parse_iso(s):
    try:
        return fmt_dt(datetime.fromisoformat(s).astimezone(JST)) if s else ""
    except Exception:
        return s

def fetch_json(urls):
    for url in urls:
        u = url.replace("https://jma.go.jp", "https://jma.go.jp")
        try:
            r = requests.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, allow_redirects=False)
            if r.status_code == 200 and r.text.strip():
                return r.json()
        except Exception:
            pass
    return None

def get_data():
    w_data = fetch_json(WARNING_URLS)
    if w_data is None:
        raise RuntimeError("気象庁JSON取得失敗")
    f_data = fetch_json(FLOOD_URLS) or []
    w_reps = w_data if isinstance(w_data, list) else [w_data]
    f_reps = f_data if isinstance(f_data, list) else [f_data]

    res = {}
    for code, city in TARGET_CITIES.items():
        warns, max_lv, rep_t = [], 0, ""
        c_lv = {"land": 0, "inundation": 0, "flood": 0, "wind_wave": 0, "other": 0}
        c_txt = {"land": "白", "inundation": "白", "flood": "白", "wind_wave": "白", "other": "白"}

        for rep in w_reps:
            rt = rep.get("reportDatetime", "")
            items = rep.get("warning", {}).get("class20Items", [])
            if not items and "areaTypes" in rep:
                for at in rep.get("areaTypes", []):
                    for a in at.get("areas", []):
                        if a.get("code") == code:
                            items += [{"areaCode": code, "kinds": a.get("warnings", [])}]
            for it in items:
                if it.get("areaCode") == code:
                    rep_t = max(rep_t, rt)
                    for k in it.get("kinds", []):
                        st = k.get("status", "")
                        if st in ("発表", "継続"):
                            wn, lv, cat, disp = WARNING_CODES.get(k.get("code", ""), ("不明", 2, "other", "黄"))
                            warns += [f"{wn}({st})"]
                            max_lv = max(max_lv, lv)
                            if lv > c_lv[cat]:
                                c_lv[cat], c_txt[cat] = lv, disp

        for fr in f_reps:
            for it in fr.get("warning", {}).get("class20Items", []):
                if it.get("areaCode") == code:
                    for k in it.get("kinds", []):
                        if k.get("status") in ("発表", "継続"):
                            fc = k.get("code", "")
                            f_map = {"30": ("レベル５氾濫特別警報", 5, "黒（Lv5特別警報）"), "40": ("レベル４氾濫危険警報", 4, "紫（Lv4危険警報）"), "01": ("レベル３氾濫警報", 3, "赤（Lv3警報）"), "20": ("レベル２氾濫注意報", 2, "黄（Lv2注意報）"), "21": ("レベル２氾濫注意報", 2, "黄（Lv2注意報）"), "22": ("レベル２氾濫注意報", 2, "黄（Lv2注意報）")}
                            if fc in f_map:
                                wn, lv, disp = f_map[fc]
                                warns += [wn]
                                max_lv = max(max_lv, lv)
                                if lv > c_lv["flood"]:
                                    c_lv["flood"], c_txt["flood"] = lv, disp

        res[code] = {"max_lv": max_lv, "color": LEVEL_TO_COLOR[max_lv], "rep_t": parse_iso(rep_t), "c": c_txt, "text": "、".join(warns) if warns else "発表なし"}
    return res

def rank_of(txt):
    for k, v in [("黒", 5), ("紫", 4), ("赤", 3), ("黄", 2)]:
        if txt.startswith(k):
            return v
    return 0

def main():
    data = get_data()
    g_host = "https://" + "www." + "googleapis.com"
    creds = Credentials.from_service_account_info(json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"]), scopes=[g_host + "/auth/spreadsheets", g_host + "/auth/drive"])
    ss = gspread.authorize(creds).open_by_key(os.environ["SPREADSHEET_ID"])
    now = datetime.now(JST)
    now_str, today_str = fmt_dt(now), now.strftime("%Y-%m-%d")

    for code, city in TARGET_CITIES.items():
        info = data[code]
        try:
            ws = ss.worksheet(city)
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=city, rows=1000, cols=15)
            ws.update(values=[[city] + [""]*10, ["取得日時(曜日)", "発表日時(曜日)", "前回最大Lv", "今回最大Lv", "変動", "土砂Lv", "浸水Lv", "洪水Lv", "風・波Lv", "その他Lv", "発表内容詳細"]], range_name="A1:K2")

        rows = ws.get_all_values()
        last = next((r for r in rows[2:] if r and r[0] and not r[0].startswith("━━")), None)
        if last and len(last) >= 4:
            prev_date, prev_col = last[0][:10], last[3]
            diff = "▲ 警戒上昇" if info["max_lv"] > rank_of(prev_col) else ("▼ 警戒低下" if info["max_lv"] < rank_of(prev_col) else "変動なし")
        else:
            prev_date, prev_col, diff = "", "初回取得", "-"

        if prev_date and prev_date != today_str:
            d_obj = datetime.strptime(prev_date, "%Y-%m-%d")
            banner = d_obj.strftime(f"━━ %Y-%m-%d ({WEEKDAYS[d_obj.weekday()]}) ━━")
            ws.insert_row([banner] + [""]*10, index=3, value_input_option="USER_ENTERED")
            ws.format("A3:K3", {"backgroundColor": {"red": 0.90, "green": 0.92, "blue": 0.95}, "textFormat": {"bold": True}})

        c = info["c"]
        new_row = [now_str, info["rep_t"], prev_col, info["color"], diff, c["land"], c["inundation"], c["flood"], c["wind_wave"], c["other"], info["text"]]
        ws.insert_row(new_row, index=3, value_input_option="USER_ENTERED")
        ws.format("A3:K3", {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "textFormat": {"bold": False}})
        print(f"[{city}] 完了")

if __name__ == "__main__":
    main()
