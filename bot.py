import time
import os
from datetime import date, timedelta

import pandas as pd
import requests
from openai import OpenAI

# ===============================
# CONFIG
# ===============================
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

COOLDOWN = 0.05
DAYS_BACK = 80

# ===============================
# LOAD UNIVERSE
# ===============================
def load_russell_universe():
    df = pd.read_excel("russell3000_constituents.xlsx")
    syms = (
        df["Symbol"]
        .dropna()
        .astype(str)
        .str.upper()
        .str.strip()
        .tolist()
    )
    return sorted(set(s.replace(".", "-") for s in syms if s))

# ===============================
# FETCH DATA
# ===============================
def fetch_polygon(ticker):
    to_date = date.today()
    from_date = to_date - timedelta(days=DAYS_BACK)

    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"

    params = {
        "adjusted": "true",
        "sort": "asc",
        "apiKey": POLYGON_API_KEY,
    }

    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return None

        data = r.json()
        if "results" not in data:
            return None

        df = pd.DataFrame(data["results"])
        df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"}, inplace=True)
        return df[["Open", "High", "Low", "Close"]]

    except:
        return None

# ===============================
# HEIKIN ASHI
# ===============================
def heikin_ashi(df):
    ha = pd.DataFrame(index=df.index)

    ha["Close"] = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4

    ha_open = [(df["Open"].iloc[0] + df["Close"].iloc[0]) / 2]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i - 1] + ha["Close"].iloc[i - 1]) / 2)

    ha["Open"] = ha_open
    ha["High"] = pd.concat([df["High"], ha["Open"], ha["Close"]], axis=1).max(axis=1)
    ha["Low"] = pd.concat([df["Low"], ha["Open"], ha["Close"]], axis=1).min(axis=1)

    return ha

# ===============================
# INDICATORS
# ===============================
def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(n).mean()
    avg_loss = loss.rolling(n).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# ===============================
# SCORE PRO (AMÉLIORÉ)
# ===============================
def score_stock_pro(ha):

    latest = ha.iloc[-1]
    prev = ha.iloc[-2]
    reds = ha.iloc[-4:-1]

    score = 0

    # 🔥 RSI (clé)
    rsi_val = latest["RSI"]

    if rsi_val < 10:
        score += 40
    elif rsi_val < 15:
        score += 35
    elif rsi_val < 20:
        score += 30
    elif rsi_val < 25:
        score += 20
    elif rsi_val < 30:
        score += 10

    # 🔹 force rebond
    move = (latest["Close"] - reds.iloc[-1]["Close"]) / reds.iloc[-1]["Close"]
    score += min(move * 100, 20)

    # 🔹 EMA20
    dist_ema = (latest["Close"] - latest["EMA20"]) / latest["EMA20"]
    if dist_ema > 0:
        score += min(dist_ema * 100, 15)

    # 🔹 RR
    entry = latest["Close"]
    stop = reds["Low"].min()
    risk = entry - stop

    if risk > 0:
        tp = entry + 2 * risk
        rr = (tp - entry) / risk
        score += min((rr - 2) * 25, 10)

    # 🔹 capitulation
    recent_low = ha["Low"].rolling(20).min().iloc[-1]
    bounce = (entry - recent_low) / recent_low
    score += min(bounce * 100, 10)

    return round(score, 1)

# ===============================
# ANALYSIS
# ===============================
def tea_analysis(score):

    if score >= 80:
        return "Reversal puissant avec pression acheteuse claire."

    elif score >= 65:
        return "Rebond structuré avec bon potentiel."

    elif score >= 50:
        return "Tentative de retournement encore fragile."

    else:
        return "Setup faible."

# ===============================
# MACRO AI
# ===============================
def generate_macro(top7):

    try:
        tickers = ", ".join(top7["Ticker"].tolist())

        prompt = f"""
Tu es un analyste financier.

Voici les meilleurs setups:
{tickers}

Donne UNE phrase courte décrivant le marché.
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )

        return response.choices[0].message.content.strip()

    except:
        return "Marché en phase de stabilisation avec opportunités de rebond."

# ===============================
# DISCORD
# ===============================
def send_discord(msg):
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
        print("Discord status:", r.status_code)
    except Exception as e:
        print("Erreur Discord:", e)

# ===============================
# MAIN
# ===============================
def main():

    print("Webhook:", DISCORD_WEBHOOK)

    tickers = load_russell_universe()
    results = []

    print(f"Scan de {len(tickers)} tickers...")

    for i, ticker in enumerate(tickers):

        df = fetch_polygon(ticker)

        if df is not None and len(df) >= 30:

            ha = heikin_ashi(df)
            ha["EMA20"] = ema(ha["Close"], 20)
            ha["EMA200"] = ema(ha["Close"], 200)
            ha["RSI"] = rsi(ha["Close"], 14)

            reds = ha.iloc[-4:-1]
            green = ha.iloc[-1]

            if (
                (reds["Close"] < reds["Open"]).sum() == 3
                and green["Close"] > green["Open"]
                and green["Close"] > reds.iloc[-1]["Close"]
            ):

                score = score_stock_pro(ha)
                analysis = tea_analysis(score)

                results.append({
                    "Ticker": ticker,
                    "Score": score,
                    "Analysis": analysis
                })

        if i % 50 == 0:
            print(f"{i} tickers traités")

        time.sleep(COOLDOWN)

    print(f"Total setups trouvés: {len(results)}")

    if len(results) > 0:

        df_results = pd.DataFrame(results)\
            .sort_values("Score", ascending=False)\
            .head(7)

        macro = generate_macro(df_results)

        report = f"🟫 TEA REVERSAL PRO\n\n🌍 {macro}\n\n"

        for _, row in df_results.iterrows():
            report += f"{row['Ticker']} | {row['Score']}/100\n{row['Analysis']}\n\n"

        print(report)
        send_discord(report)

    else:
        msg = "🟫 TEA REVERSAL\n\nAucun pattern détecté aujourd’hui."
        print(msg)
        send_discord(msg)

# ===============================
# RUN
# ===============================
if __name__ == "__main__":
    main()
