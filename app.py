from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import threading
import requests
from bs4 import BeautifulSoup
import re
import json

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

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

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()

    if text in ["查價格", "價格", "椰殼價格", "煤炭價格", "溴素價格"]:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📡 查詢中，請稍候...")
        )
        user_id = event.source.user_id
        threading.Thread(target=send_price_result, args=(user_id,)).start()
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請輸入「查價格」即可查詢椰殼活性碳、煤炭與溴素價格 📊")
        )

def send_price_result(user_id):
    try:
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

        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=reply.strip())
        )

    except Exception as e:
        print("[錯誤] 背景推播失敗：", e)

def fetch_coconut_prices():
    url = "https://businessanalytiq.com/procurementanalytics/index/activated-charcoal-prices/"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, "html.parser")

    heading = None
    for h3 in soup.find_all("h3"):
        if "activated carbon price" in h3.text.lower():
            heading = h3
            break

    prices = {}
    if heading:
        ul = heading.find_next_sibling("ul")
        if ul:
            for li in ul.find_all("li"):
                text = li.get_text(strip=True)
                match = re.match(r"(.+):US\$(\d+\.\d+)/KG,?\s*([-+]?\d+\.?\d*)%?\s*(up|down)?", text)
                if match:
                    region = match.group(1).strip()
                    price = float(match.group(2))
                    change = float(match.group(3))
                    direction = match.group(4)
                    if direction == "down":
                        change = -abs(change)
                    prices[region] = {"price": price, "change": change, "date": None}
    return prices

def fetch_fred_from_ycharts():
    try:
        url = "https://ycharts.com/indicators/us_producer_price_index_coal_mining"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, "html.parser")

        def get_val(label):
            cell = soup.find("td", string=label)
            return cell.find_next_sibling("td").text.strip() if cell else None

        latest_val = get_val("Last Value")
        latest_date = get_val("Latest Period")
        change = get_val("Change from Last Month")

        return latest_date, latest_val, change
    except Exception as e:
        print("[FRED error]", e)
        return None, None, None

def fetch_bromine_details():
    url = "https://pdata.100ppi.com/?f=basket&dir=hghy&id=643"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers)
        json_text = re.search(r"var data=(\[{.*?}\]);", res.text)
        if not json_text:
            return None
        data = json.loads(json_text.group(1))
        latest = data[-1]
        return f"{latest['date']}：{latest['value']}（漲跌 {latest['change']}）"
    except Exception as e:
        print("[Bromine Error]:", e)
        return None

def fetch_cnyes_energy2_close_price(keywords):
    try:
        url = "https://www.cnyes.com/futures/energy2.aspx"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, "html.parser")

        rows = soup.select("table tr")
        for row in rows:
            cols = row.find_all("td")
            if len(cols) >= 5 and any(k in cols[0].text for k in keywords):
                title = cols[0].text.strip()
                date = cols[1].text.strip()
                close = cols[2].text.strip()
                change = cols[3].text.strip()
                return f"{title}：{date} 收盤價 {close}（漲跌 {change}）"
        return "❌ 未找到 " + "、".join(keywords)
    except Exception as e:
        return f"❌ 擷取失敗：{e}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
