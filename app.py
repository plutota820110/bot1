from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import threading
import datetime

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

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# === 全域快取 ===
price_cache = {
    "last_update": None,
    "result": ""
}

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/update_cache", methods=['GET'])
def update_cache():
    global price_cache
    try:
        reply = build_price_report()
        price_cache["last_update"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        price_cache["result"] = reply
        return "✅ Cache updated", 200
    except Exception as e:
        return f"❌ Cache update failed: {e}", 500

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()

    if text in ["查價格", "價格", "椰殼價格", "煤炭價格", "溴素價格"]:
        if price_cache["result"]:
            reply = f"📈 目前快取於 {price_cache['last_update']}：\n\n{price_cache['result']}"
        else:
            reply = "⏳ 尚未快取任何價格資料"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請輸入「查價格」即可查詢椰殼活性碳、煤炭與溴素價格 📊")
        )

def build_price_report():
    reply = ""

    coconut = fetch_coconut_prices()
    if coconut:
        reply += "🥥 椰殼活性碳價格：\n"
        for region, data in coconut.items():
            arrow = "⬆️" if data["change"] > 0 else "⬇️"
            date = f"（{data['date']}）" if data['date'] else ""
            reply += f"{region}：US${data['price']} /KG  {arrow} {abs(data['change'])}% {date}\n"
    else:
        reply += "❌ 椰殼活性碳抓取失敗\n"

    latest_date, latest_val, change = fetch_fred_from_ycharts()
    reply += "\n🪨 煤質活性碳價格：\n"
    if latest_val:
        if change:
            arrow = "⬆️" if "-" not in change else "⬇️"
            reply += f"FRED：{latest_val}（{latest_date}，月變動 {arrow} {change}）\n"
        else:
            reply += f"FRED：{latest_val}（{latest_date}）\n"
    else:
        reply += "FRED ❌ 抓取失敗\n"

    coal_keywords = [["紐約煤西北歐"], ["倫敦煤澳洲"], ["大連焦煤"]]
    for kw in coal_keywords:
        reply += fetch_cnyes_energy2_close_price(kw) + "\n"

    bromine = fetch_bromine_details()
    reply += "\n🧪 溴素最新價格：\n"
    if bromine:
        reply += bromine + "\n"
    else:
        reply += "溴素價格 ❌ 抓取失敗\n"

    return reply.strip()

# 以下維持原本函數（get_selenium_driver、fetch_coconut_prices 等）
