import streamlit as st
import pandas as pd
import re
import requests
import json
from googleapiclient.discovery import build
from io import BytesIO
import pytesseract
from PIL import Image

# ==========================================
# 1. 初期設定とデフォルト辞書
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"

st.set_page_config(page_title="楽曲抽出＆特定システム", layout="wide")

# ==========================================
# 2. タイトルと初心者向けガイド
# ==========================================
st.title("🎶 楽曲抽出＆特定システム")

st.markdown("""
**はじめての方へ：このツールでできること**
このシステムは、音楽のプレイリスト整理や、わからない楽曲名の特定を自動化するお助けツールです。
上から順番に項目を埋めていくだけで、簡単に操作できます。

*   **🔗 URLから一括抽出:** YouTubeやニコニコ動画、SoundCloudのURLを入れるだけで、曲名と歌っているキャラクター（合成音声など）を自動でリストアップし、Excel形式でダウンロードできます。
*   **🖼️ 画像・ローマ字から楽曲特定:** 「スクショはあるけど曲名が読めない」「ローマ字しかわからない」といった時に、正しい日本語の曲名を探し出します。
---
""")

# ==========================================
# 3. データ処理ロジック（関数群）
# ==========================================
def extract_vocals(title, description, keywords, ng_list):
    found_vocals = set()
    title_str = str(title) if title else ""
    desc_str = str(description) if description else ""
    for kw in keywords:
        if kw in title_str:
            found_vocals.add(kw)
    has_ng_word = any(ng in desc_str for ng in ng_list)
    if not has_ng_word:
        for kw in keywords:
            if kw in desc_str:
                found_vocals.add(kw)
    return " / ".join(list(found_vocals))

def clean_title(raw_title):
    title = str(raw_title)
    title = re.sub(r"(?i)[\(（\[【].*?(remix|bootleg|edit|mashup|flip|vip|cover).*?[\)）\]】]", "", title)
    title = re.sub(r"【.*?】|\[.*?\]", "", title)
    title = re.split(r"(?i)\s+feat\.\s+|\s+ft\.\s+", title)[0]
    title = re.split(r"\s+/\s+|\s+-\s+", title)[0]
    return title.strip()

def get_youtube_playlist(api_key, url):
    match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
    if not match: raise ValueError("有効なYouTubeプレイリストIDが見つかりません。")
    youtube = build("youtube", "v3", developerKey=api_key)
    videos, next_page_token = [], None
    while True:
        request = youtube.playlistItems().list(part="snippet", playlistId=match.group(1), maxResults=50, pageToken=next_page_token)
        response = request.execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            title = snippet["title"]
            if title in ["Private video", "Deleted video"]: continue
            videos.append({"曲名": title, "概要欄データ": snippet.get("description", ""), "URL": f"https://www.youtube.com/watch?v={snippet['resourceId']['videoId']}"})
        next_page_token = response.get("nextPageToken")
        if not next_page_token: break
    return videos

def get_niconico_playlist(url):
    match = re.search(r"mylist/(\d+)", url)
    if not match: raise ValueError("有効なニコニコ動画のマイリストURLが見つかりません。")
    api_url = f"https://nvapi.nicovideo.jp/v2/mylists/{match.group(1)}"
    headers = {"X-Frontend-Id": "6", "X-Frontend-Version": "0"}
    res = requests.get(api_url, headers=headers)
    res.encoding = 'utf-8'
    if res.status_code != 200: raise ValueError("ニコニコ動画のリストが読み込めませんでした。")
    data = res.json()
    items = data.get("data", {}).get("mylist", {}).get("items", [])
    videos = []
    for item in items:
        video = item.get("video", {})
        if video: videos.append({"曲名": video.get("title", "Unknown"), "概要欄データ": video.get("shortDescription", ""), "URL": f"https://www.nicovideo.jp/watch/{video.get('id', '')}"})
    return videos

def get_soundcloud_data(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    res.encoding = 'utf-8'
    match = re.search(r"window\.__sc_hydration = (\[.*?\]);\s*</script>", res.text)
    if not match: raise ValueError("楽曲データが見つかりませんでした。")
    hydration_data = json.loads(match.group(1))
    videos = []
    for item in hydration_data:
        if item.get("hydratable") == "playlist":
            tracks = item.get("data", {}).get("tracks", [])
            for t in tracks:
                if isinstance(t, dict) and t.get("title"):
                    videos.append({"曲名": t.get("title"), "概要欄データ": f"{t.get('user', {}).get('username', '')} / {t.get('description') or ''}", "URL": t.get("permalink_url", "")})
            if videos: return videos
        elif item.get("hydratable") == "sound":
            t = item.get("data", {})
            if isinstance(t, dict) and t.get("title"):
                videos.append({"曲名": t.get("title"), "概要欄データ": f"{t.get('user', {}).get('username', '')} / {t.get('description') or ''}", "URL": t.get("permalink_url", "")})
            if videos: return videos
    return videos

def search_vocadb(query_text):
    url = "https://vocadb.net/api/songs"
    params = {"query": query_text, "maxResults": 1, "sort": "FavoritedTimes", "fields": "Names"}
    headers = {"Accept": "application/json"}
    try:
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data.get("items"): return data["items"][0]["defaultName"]
    except Exception as e:
        return f"検索エラー: {e}"
    return None

def extract_text_from_image(image_file):
    image = Image.open(image_file)
    text = pytesseract.image_to_string(image, lang='eng+jpn')
    return text.strip()

# ==========================================
# 4. メイン画面（タブとフラットレイアウト構造）
# ==========================================
tab1, tab2 = st.tabs(["🔗 URLから一括抽出", "🖼️ 画像・ローマ字から楽曲特定"])

# ------------------------------------------
# タブ1: 従来のプレイリスト抽出機能
# ------------------------------------------
with tab1:
    st.header("⚙️ 1. システム設定")
    st.markdown("抽出のベースとなる設定を行います。デフォルトのままでも動作します。")
    
    youtube_api_key = st.text_input("🔑 YouTube API Key (※YouTubeの抽出を行う場合のみ入力)", type="password")
    
    col1, col2 = st.columns(2)
    with col1:
        keywords_input = st.text_area("🔍 抽出するワード（カンマ区切り）", DEFAULT_KEYWORDS, height=100)
        target_keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]
    with col2:
        ng_words_input = st.text_area("🚫 除外（NG）ワード（概要欄検索をスキップ）", DEFAULT_NG_WORDS, height=100)
        ng_words = [n.strip() for n in ng_words_input.split(",") if n.strip()]

    st.markdown("---")
    
    st.header("🔍 2. プレイリスト・楽曲URLの解析")
    st.markdown("解析したい再生リストや動画のURLを貼り付けてください。")
    playlist_url = st.text_input("URLを入力（YouTube / ニコニコ動画 / SoundCloud）")

    if st.button("一括抽出を開始する", type="primary"):
        if not playlist_url:
            st.warning("⚠️ URLを入力してください。")
        else:
            with st.spinner("データを取得・解析中..."):
                try:
                    raw_data = []
                    if "youtube.com" in playlist_url or "youtu.be" in playlist_url:
                        if not youtube_api_key: raise ValueError("YouTube API Keyが設定されていません。")
                        raw_data = get_youtube_playlist(youtube_api_key, playlist_url)
                    elif "nicovideo.jp" in playlist_url:
                        raw_data = get_niconico_playlist(playlist_url)
                    elif "soundcloud.com" in playlist_url:
                        raw_data = get_soundcloud_data(playlist_url)
                    else:
                        raise ValueError("対応していないURLです。")

                    results = []
                    for item in raw_data:
                        vocals = extract_vocals(item["曲名"], item["概要欄データ"], target_keywords, ng_words)
                        cleaned_title = clean_title(item["曲名"])
                        results.append({
                            "曲名": cleaned_title,
                            "合成音声": vocals,
                            "URL": item["URL"]
                        })
                    
                    df = pd.DataFrame(results)
                    if df.empty:
                        st.warning("対象となる楽曲が見つかりませんでした。")
                    else:
                        st.success(f"✅ {len(df)}曲の解析が完了しました！")
                        st.dataframe(df, use_container_width=True)
                        
                        output = BytesIO()
                        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                            df.to_excel(writer, index=False, sheet_name='Playlist Data')
                        st.download_button("📥 Excelファイルとしてダウンロード", data=output.getvalue(), file_name="playlist_result.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                except Exception as e:
                    st.error(f"❌ エラーが発生しました: {e}")

# ------------------------------------------
# タブ2: 画像認識 (OCR) & VocaDB検索
# ------------------------------------------
with tab2:
    st.header("🖼️ 画像認識・ローマ字から楽曲特定")
    st.markdown("スクリーンショットや、ローマ字の楽曲名から、VocaDBを検索して正式な日本語タイトルを導き出します。")
    
    uploaded_file = st.file_uploader("楽曲名が写っている画像（スクショなど）をアップロード", type=["png", "jpg", "jpeg"])
    manual_query = st.text_input("または、検索したい文字列（ローマ字や不完全な曲名）を直接入力", placeholder="例: nousyousakuretuga-ru")
    
    if st.button("楽曲を特定する", type="primary"):
        query_text = ""
        
        if uploaded_file is not None:
            with st.spinner("画像を解析中..."):
                st.image(uploaded_file, caption="アップロードされた画像", width=300)
                extracted_text = extract_text_from_image(uploaded_file)
                st.write("**画像から抽出されたテキスト:**")
                st.code(extracted_text)
                query_text = extracted_text.replace("\n", " ").strip()
        
        if manual_query:
            query_text = manual_query.strip()
            
        if query_text:
            with st.spinner("VocaDBデータベースを検索中..."):
                official_title = search_vocadb(query_text)
                if official_title:
                    st.success(f"🎉 特定成功！ 正式な曲名: **{official_title}**")
                else:
                    st.warning("VocaDBに一致する楽曲が見つかりませんでした。別のキーワードや鮮明な画像をお試しください。")
        else:
            st.warning("画像を入れるか、検索キーワードを入力してください。")
