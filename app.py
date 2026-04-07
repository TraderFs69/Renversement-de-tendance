import time
from datetime import date, timedelta
from typing import List

import pandas as pd
import requests
import streamlit as st
from openai import OpenAI

# ===============================
# CONFIG
# ===============================
st.set_page_config(layout="wide")
st.title("🟫 TEA REVERSAL PRO — Heikin Ashi")

API_KEY = st.secrets["POLYGON_API_KEY"]
DISCORD_WEBHOOK = st.secrets["DISCORD_WEBHOOK_URL"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

COOLDOWN = 0.08
DAYS_BACK = 80
MIN_SCORE = 50

# ===============================
# LOAD UNIVERSE
# ===============================
@st.cache_data
def load_russell_universe() -> List[str]:
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
# POLYGON FETCH
# ===============================
def fetch_polygon(ticker: str):
    to_date = date.today()
    from_date = to_date - timedelta(days=DAYS_BACK)

    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"

    params = {
        "adjusted": "true",
        "sort": "asc",
        "apiKey": API_KEY,
    }

    r = requests.get(url, params=params, timeout=15)
    if r.status_code != 200:
        return None

    data = r.json()
    if "results" not in data or not data["results"]:
        return None

    df = pd.DataFrame(data["results"])
    df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"}, inplace=True)
    return df[["Open", "High", "Low", "Close"]]

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
# INDICATEURS
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
# SCORE PRO
# ===============================
def score_stock_pro(ha):

    latest = ha.iloc[-1]
    prev = ha.iloc[-2]
    reds = ha.iloc[-4:-1]

    score = 0

    if latest["RSI"] < 35 and latest["RSI"] > prev["RSI"]:
        score += 25
    elif latest["RSI"] < 40:
        score += 15

    score += 15  # pattern

    if latest["Close"] > reds["High"].max():
        score += 10

    dist_ema = (latest["Close"] - latest["EMA20"]) / latest["EMA20"]
    if dist_ema > 0:
        score += 10

    if latest["EMA20"] > ha["EMA20"].iloc[-5]:
        score += 10

    entry = latest["Close"]
    stop = reds["Low"].min()
    risk = entry - stop

    if risk > 0:
        tp = entry + 2 * risk
        rr = (tp - entry) / risk
        if rr >= 2:
            score += 10

    recent_low = ha["Low"].rolling(20).min().iloc[-1]
    if entry > recent_low * 1.05:
        score += 10

    dist_200 = (entry / ha["EMA200"].iloc[-1]) - 1
    if 0 < dist_200 < 0.15:
        score += 10

    return min(score, 100)

# ===============================
# ANALYSE SIMPLE
# ===============================
def tea_analysis(score):

    if score >= 80:
        return "Pression acheteuse nette après excès vendeur."

    elif score >= 65:
        return "Rebond structuré avec acheteurs en contrôle."

    elif score >= 55:
        return "Tentative de retournement encore fragile."

    else:
        return "Setup faible avec peu de conviction."

# ===============================
# MACRO CHATGPT
# ===============================
def generate_macro(top7):

    try:
        tickers = ", ".join(top7["Ticker"].tolist())

        prompt = f"""
Tu es un analyste financier.

Voici les meilleurs setups de renversement aujourd'hui:
{tickers}

Donne UNE phrase courte décrivant le contexte de marché.
Max 20 mots. Ton professionnel.
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )

        return response.choices[0].message.content.strip()

    except:
        return "Marché en phase de stabilisation avec opportunités de rebonds techniques."

# ===============================
# DISCORD
# ===============================
def send_discord(msg):
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
    except:
        pass

# ===============================
# UI
# ===============================
st.sidebar.header("Paramètres")

limit_n = st.sidebar.slider("Tickers", 500, 3000, 3000, step=250)
go_scan = st.sidebar.button("🚦 Lancer")

# ===============================
# SCAN
# ===============================
if go_scan:

    tickers = load_russell_universe()[:limit_n]

    st.info(f"Scan {len(tickers)} tickers…")
    progress = st.progress(0)

    results = []

    for i, ticker in enumerate(tickers, 1):

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

                entry = green["Close"]
                stop = reds["Low"].min()

                score = score_stock_pro(ha)

                if score >= MIN_SCORE:

                    analysis = tea_analysis(score)

                    results.append({
                        "Ticker": ticker,
                        "Score": score,
                        "Analysis": analysis
                    })

        progress.progress(i / len(tickers))
        time.sleep(COOLDOWN)

    if results:

        df_results = pd.DataFrame(results).sort_values("Score", ascending=False).head(7)

        macro = generate_macro(df_results)

        report = f"🟫 TEA REVERSAL PRO\n\n🌍 {macro}\n\n"

        for _, row in df_results.iterrows():
            report += f"{row['Ticker']} | {row['Score']}/100\n{row['Analysis']}\n\n"

        st.success("Scan terminé")
        st.dataframe(df_results)

        send_discord(report)

    else:
        st.warning("Aucun signal aujourd’hui")
