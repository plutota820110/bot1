from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import threading
import datetime
import re
from bs4 import BeautifulSoup
import requests

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

# === Selenium 建立器 ===
def get_selenium_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

# === 資料抓取 ===
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
                    match = re.match(r"(.+):US\$(\d+\.\d+)/KG,?\s*([-+]?\d+\.?\d*)%?\s*(up|down)?", text)
                    if match:
                        region = match.group(1).strip()
                        price = float(match.group(2))
                        change = float(match.group(3))
                        if match.group(4) == "down":
                            change = -abs(change)
                        date_match = re.search(r'([A-Za-z]+ \d{4})', text)
                        date = date_match.group(1) if date_match else ""
                        result[region] = {"price": price, "change": change, "date": date}
        return result
    except Exception as e:
        print("Error fetching coconut price:", e)
        return None

def fetch_fred_from_ycharts():
    url = "https://ycharts.com/indicators/us_producer_price_index_coal_mining"
    driver = get_selenium_driver()
    driver.get(url)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.table tbody tr"))
        )
        rows = driver.find_elements(By.CSS_SELECTOR, "table.table tbody tr")
        data = {}
        for row in rows:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) == 2:
                label = cells[0].text.strip()
                value = cells[1].text.strip()
                data[label] = value

        latest_val = data.get("Last Value")
        period = data.get("Latest Period")
        change = data.get("Change from Last Month")
        return period, latest_val, change
    except Exception as e:
        print("Error fetching FRED YCharts with Selenium:", e)
        return None, None, None
    finally:
        driver.quit()

def fetch_bromine_details():
    driver = get_selenium_driver()
    url = "https://pdata.100ppi.com/?f=basket&dir=hghy&id=643#hghy_643"
    driver.get(url)
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.tab2 tr"))
        )
        rows = driver.find_elements(By.CSS_SELECTOR, "table.tab2 tr")
        data_rows = [row for row in rows if len(row.find_elements(By.TAG_NAME, "td")) >= 3]
        if not data_rows:
            return "❌ 找不到溴素資料列"

        last_row = data_rows[-1]
        tds = last_row.find_elements(By.TAG_NAME, "td")
        date = tds[0].text.strip()
        price = tds[1].text.strip()
        percent = tds[2].text.strip()
        return f"{date}：{price}（漲跌 {percent}）"
    except Exception as e:
        print("Error fetching bromine price:", e)
        return None
    finally:
        driver.quit()

def fetch_cnyes_energy2_close_price(name_keywords):
    url = "https://www.cnyes.com/futures/energy2.aspx"
    headers = {"User-Agent": "Mozilla/5.0"}
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
                if any(k in name for k in name_keywords):
                    date = cells[0].text.strip()
                    close = cells[4].text.strip()
                    change = cells[5].text.strip()
                    return f"{name}：{date} 收盤價 {close}（漲跌 {change}）"
        return f"❌ 未找到 {'、'.join(name_keywords)}"
    except Exception as e:
        return f"❌ 擷取失敗：{e}"
    finally:
        driver.quit()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
