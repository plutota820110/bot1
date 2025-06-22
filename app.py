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

    coal_lines.append(fetch_cnyes_energy2_price("紐約煤西北歐"))
    coal_lines.append(fetch_cnyes_energy2_price("倫敦煤澳洲"))
    coal_lines.append(fetch_cnyes_energy2_price("大連焦煤"))

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

def fetch_cnyes_energy2_price(keyword):
    url = "https://www.cnyes.com/futures/energy2.aspx"
    driver = get_selenium_driver()
    driver.get(url)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table tr"))
        )
        rows = driver.find_elements(By.CSS_SELECTOR, "table tr")
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) > 7:
                name = cells[1].text.strip()
                if keyword in name:
                    date = cells[0].text.strip()
                    close = cells[4].text.strip()
                    percent = cells[6].text.strip()
                    arrow = "⬆️" if "-" not in percent else "⬇️"
                    return f"近月{name}：{date} 收盤價 {close}（{arrow} {percent}）"
        return f"❌ {keyword} 抓取失敗"
    except Exception as e:
        return f"❌ {keyword} 擷取失敗：{e}"
    finally:
        driver.quit()

def get_selenium_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def fetch_coconut_prices():
    url = "https://businessanalytiq.com/procurementanalytics/index/activated-charcoal-prices/"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return None
        soup = BeautifulSoup(res.text, "html.parser")
        result = {}
        heading = None
        for h3 in soup.find_all("h3"):
            if "activated carbon price" in h3.text.lower():
                heading = h3
                break
        if heading:
            ul = heading.find_next_sibling("ul")
            if ul:
                for li in ul.find_all("li"):
                    text = li.get_text(strip=True)
                    match = re.match(r"(.+):US\\$(\\d+\\.\\d+)/KG,?\\s*([-+]?\\d+\\.?\\d*)%?\\s*(up|down)?", text)
                    if match:
                        region = match.group(1).strip()
                        price = float(match.group(2))
                        change = float(match.group(3))
                        if match.group(4) == "down":
                            change = -abs(change)
                        date_match = re.search(r'([A-Za-z]+ \\d{4})', text)
                        date = date_match.group(1) if date_match else ""
                        result[region] = {"price": price, "change": change, "date": date}
        return result
    except Exception as e:
        print("[椰殼價格抓取失敗]", e)
        return None

def fetch_fred_from_ycharts():
    url = "https://ycharts.com/indicators/us_producer_price_index_coal_mining"
    driver = get_selenium_driver()
    driver.get(url)
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.table"))
        )
        tables = driver.find_elements(By.CSS_SELECTOR, "table.table")
        data = {}
        for table in tables:
            rows = table.find_elements(By.TAG_NAME, "tr")
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) == 2:
                    label = cells[0].text.strip()
                    value = cells[1].text.strip()
                    data[label] = value

        latest_val = data.get("Last Value")
        period = data.get("Latest Period")
        change = data.get("Change from Last Month")

        if latest_val and period:
            return period, latest_val, change
        else:
            raise ValueError("必要資料欄位缺失")
    except Exception as e:
        print("[FRED 抓取失敗]", e)
        return None, None, None
    finally:
        driver.quit()

def fetch_bromine_details():
    url = "https://pdata.100ppi.com/?f=basket&dir=hghy&id=643#hghy_643"
    driver = get_selenium_driver()
    driver.get(url)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.tab2 tr"))
        )
        rows = driver.find_elements(By.CSS_SELECTOR, "table.tab2 tr")
        data_rows = [row for row in rows if len(row.find_elements(By.TAG_NAME, "td")) >= 3]
        if len(data_rows) < 2:
            return None
        last_row = data_rows[-1]
        prev_row = data_rows[-2]
        last_price = float(last_row.find_elements(By.TAG_NAME, "td")[1].text.strip())
        prev_price = float(prev_row.find_elements(By.TAG_NAME, "td")[1].text.strip())
        change_percent = round((last_price - prev_price) / prev_price * 100, 2)
        arrow = "⬆️" if change_percent > 0 else "⬇️"
        date = last_row.find_elements(By.TAG_NAME, "td")[0].text.strip()
        return f"{date}：{last_price:.2f}（{arrow} {abs(change_percent)}%）"
    except Exception as e:
        print("[溴素價格抓取失敗]", e)
        return None
    finally:
        driver.quit()

def broadcast_price_report():
    try:
        if not os.path.exists("users.txt"):
            return
        with open("users.txt", "r") as f:
            user_ids = [line.strip() for line in f if line.strip()]
        flex_msg = build_flex_price_report()
        for uid in user_ids:
            line_bot_api.push_message(uid, flex_msg)
    except Exception as e:
        print("[錯誤] 群發失敗：", e)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "broadcast":
        broadcast_price_report()
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
