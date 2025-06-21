from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, FlexSendMessage
import os
import threading
import re
from bs4 import BeautifulSoup
import requests
import sys
import json

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
CRON_SECRET_KEY = os.getenv("CRON_SECRET_KEY", "abc123")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/broadcast", methods=['GET'])
def http_broadcast():
    secret_key = request.args.get("key")
    if secret_key != CRON_SECRET_KEY:
        return "Unauthorized", 403
    threading.Thread(target=broadcast_price_report).start()
    return "Broadcast started"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()

    user_id = event.source.user_id
    try:
        existing_ids = set()
        if os.path.exists("users.txt"):
            with open("users.txt", "r") as f:
                existing_ids = set(line.strip() for line in f)
        if user_id not in existing_ids:
            with open("users.txt", "a") as f:
                f.write(user_id + "\n")
                print(f"[✅] 已新增使用者 UID：{user_id}")
    except Exception as e:
        print("[錯誤] 無法儲存 UID：", e)

    if text in ["查價格", "價格", "椰殼價格", "煤炭價格", "溴素價格"]:
        threading.Thread(target=send_price_result, args=(user_id,)).start()
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextMessage(text="請輸入「查價格」即可查詢椰殼活性碳、煤炭與溴素價格 📊")
        )

def send_price_result(user_id):
    flex_msg = build_flex_price_report()
    line_bot_api.push_message(user_id, flex_msg)

def build_flex_price_report():
    def section(title, items):
        return {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": title, "weight": "bold", "size": "md"},
                *[{"type": "text", "text": line, "wrap": True, "size": "sm"} for line in items]
            ]
        }

    coconut = fetch_coconut_prices()
    coconut_lines = []
    if coconut:
        for region, data in coconut.items():
            arrow = "⬆️" if data["change"] > 0 else "⬇️"
            date = f"（{data['date']}）" if data['date'] else ""
            coconut_lines.append(f"{region}：US${data['price']} /KG {arrow} {abs(data['change'])}% {date}")
    else:
        coconut_lines.append("❌ 椰殼活性碳抓取失敗")

    latest_date, latest_val, change = fetch_fred_from_ycharts()
    coal_lines = []
    if latest_val:
        arrow = "⬆️" if change and "-" not in change else "⬇️"
        if change:
            coal_lines.append(f"FRED：{latest_val}（{latest_date}，月變動 {arrow} {change}）")
        else:
            coal_lines.append(f"FRED：{latest_val}（{latest_date}）")
    else:
        coal_lines.append("❌ FRED 抓取失敗")

    for kw in [["紐約煤西北歐"], ["倫敦煤澳洲"], ["大連焦煤"]]:
        result = fetch_cnyes_energy2_close_price(kw)
        if "未找到" in result or "擷取失敗" in result:
            coal_lines.append(f"❌ {kw[0]} 抓取失敗")
        else:
            coal_lines.append(f"{result}")

    bromine = fetch_bromine_details()
    bromine_lines = [bromine] if bromine else ["❌ 溴素價格抓取失敗"]

    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "📊 價格查詢報告", "weight": "bold", "size": "lg"},
                section("🥥 椰殼活性碳價格", coconut_lines),
                section("🪨 煤質活性碳價格", coal_lines),
                section("🧪 溴素價格", bromine_lines)
            ]
        }
    }
    return FlexSendMessage(alt_text="價格查詢結果", contents=bubble)

def broadcast_price_report():
    try:
        flex_msg = build_flex_price_report()
        with open("users.txt", "r") as f:
            user_ids = [line.strip() for line in f.readlines() if line.strip()]
        for uid in user_ids:
            line_bot_api.push_message(uid, flex_msg)
            print(f"✅ 已推播給 {uid}")
    except Exception as e:
        print("❌ 群發失敗：", e)

def fetch_fred_from_ycharts():
    url = "https://ycharts.com/indicators/us_producer_price_index_coal_mining"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code != 200:
            return None, None, None
        soup = BeautifulSoup(res.text, "html.parser")
        table = soup.find("table", class_="table")
        rows = table.find_all("tr") if table else []
        data = {}
        for row in rows:
            cols = row.find_all("td")
            if len(cols) == 2:
                key = cols[0].text.strip()
                value = cols[1].text.strip()
                data[key] = value
        return data.get("Latest Period"), data.get("Last Value"), data.get("Change from Last Month")
    except Exception as e:
        print("Error fetching FRED from ycharts:", e)
        return None, None, None

# 其他函式維持不變...

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "broadcast":
        broadcast_price_report()
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
