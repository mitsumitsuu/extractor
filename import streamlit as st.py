import streamlit as st
import pandas as pd
import re
import requests
import json
import urllib.parse
import urllib.request
from googleapiclient.discovery import build
import google.generativeai as genai
from io import BytesIO
import yt_dlp

# ==========================================
# UIカスタマイズ（GitHubリンク等の非表示）
# ==========================================
st.set_page_config(page_title="楽曲抽出＆特定システム", layout="wide")
hide_streamlit_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.st-emotion-cache-1wbqy5l {display: none;} /* GitHubアイコン非表示 */
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ==========================================
# 初期設定とデフォルト辞書
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花, GUMI"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"

col_title, col_link = st.columns([4, 1])
with col_title:
    st.title("🎶 楽曲抽出システム Pro")
with col_link:
    st.write("\n")
    st.markdown("[👤 制作者 (Mitsu) の lit.link](https://lit.link/_mitsu_3_)")

# ==========================================
# 操作方法・説明
# ==========================================
with st.expander("📖 はじめての方へ：各抽出モードの操作方法と説明（クリックで開く）", expanded=False):
    st.markdown("""
    **【抽出モードの違い】**
    *   ⚡ **高速モード (yt-dlp使用 / API不要)**: 
        APIキーの設定なしですぐに使えます。YouTubeとSoundCloudの両方に対応していますが、再生数などの詳細データは取得しません。
    *   📊 **統計フィルターモード (YouTube API使用)**: 
        YouTube専用。再生数やコメント数による足切り（〇万再生以上のみ抽出など）が可能です。
    *   ✨ **AI完璧抽出モード (Gemini API使用)**: 
        最新AIを使って、概要欄やタイトルから「純粋な曲名」と「合成音声名」を文脈から読み取り、100%に近い精度で抽出します。
        
    **【便利な使い方】**
    *   再生数などのフィルター項目は、`+` `-` ボタンを押すだけでなく、**枠の中をクリックしてキーボードから直接数字を入力**できます。
    *   不要な楽曲（〇〇の曲は除外したい等）があれば、「詳細フィルター」の除外ワードに入力してください。
    """)

# ==========================================
# データ処理関数
# ==========================================
def extract_vocals_ai(api_key, text_data):
    """Gemini APIを使用した高精度なタイトル・ボーカル抽出"""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    以下の動画タイトルと概要欄から、純粋な「楽曲の題名」と「歌唱している合成音声名(ボーカロイド等)」を抽出してください。
    - オリジナルPV、MV、〇〇P、各種記号、feat. などの余計な文字列は完全に排除すること。
    - ボーカルが複数の場合は「/」で区切ること。
    - 結果は以下のJSONフォーマットのみで出力すること。Markdown記法や説明は一切不要。
    {{"title": "純粋な曲名", "vocals": "合成音声名"}}
    
    【データ】
    {text_data}
    """
    try:
        response = model.generate_content(prompt)
        result = json.loads(response.text.replace('```json', '').replace('
