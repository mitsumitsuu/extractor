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

st.set_page_config(page_title="楽曲情報抽出システム", layout="wide")
st.title("🎶 楽曲抽出＆特定システム")

# ==========================================
# 2. サイドバー（設定画面）
# ==========================================
with st.sidebar:
    st.header("⚙️ システム設定")
    
    st.subheader("🔑 APIキー設定")
    youtube_api_key = st.text_input("YouTube API Key", type="password")
    
    st.subheader("🔍 抽出するワード")
    keywords_input = st.text_area("合成音声名（カンマ区切り）", DEFAULT_KEYWORDS, height=100)
    target_keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]
    
    st.subheader("🚫 除外（NG）ワード")
    ng_words_input = st.text_area("NGワード（概要欄検索をスキップ）", DEFAULT_NG_WORDS, height=100)
    ng_words = [n.strip() for n in ng_words_input.split(",") if n.strip()]

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

# --- 各プラットフォームのデータ取得ロジック ---
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

# --- VocaDB連携＆OCRロジック（新機能） ---
def search_vocadb(query_text):
    """VocaDBのAPIを叩いて、ローマ字や不完全な文字列から正式な日本語の楽曲名を探す"""
    url = "https://vocadb.net/api/songs"
    params = {
        "query": query_text,
        "maxResults": 1,
        "sort": "FavoritedTimes", # 最も人気（お気に入り登録数が多い）の曲を優先してヒットさせる
        "fields": "Names"
    }
    headers = {"Accept": "application/json"}
    try:
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data.get("items"):
                # VocaDBに登録されているデフォルト名（通常は正式な日本語タイトル）を返す
                return data["items"][0]["defaultName"]
    except Exception as e:
        return f"検索エラー: {e}"
    return None

def extract_text_from_image(image_file):
    """画像からテキストを抽出（英語・日本語対応）"""
    image = Image.open(image_file)
    # Tesseractで英語(eng)と日本語(jpn)を認識させる
    text = pytesseract.image_to_string(image, lang='eng+jpn')
    return text.strip()

# ==========================================
# 4. メイン画面（タブ構造）
# ==========================================
tab1, tab2 = st.tabs(["🔗 URLから一括抽出 (Playlists)", "🖼️ 画像・ローマ字から楽曲特定 (OCR & VocaDB)"])

# ------------------------------------------
# タブ1: 従来のプレイリスト抽出機能
# ------------------------------------------
with tab1:
    st.markdown("### プレイリスト・楽曲URLの解析")
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
    st.markdown("### 画像認識・ローマ字から正式な曲名を特定")
    st.info("スクリーンショットや、ローマ字の楽曲名から、世界最大のボカロデータベース(VocaDB)を検索して正式な日本語タイトルを導き出します。")
    
    uploaded_file = st.file_uploader("楽曲名が写っている画像（スクショなど）をアップロード", type=["png", "jpg", "jpeg"])
    manual_query = st.text_input("または、検索したい文字列（ローマ字や不完全な曲名）を直接入力", placeholder="例: nousyousakuretuga-ru")
    
    if st.button("楽曲を特定する", type="primary", key="search_vocadb"):
        query_text = ""
        
        # 1. 画像がアップロードされている場合はOCR処理
        if uploaded_file is not None:
            with st.spinner("画像を解析中..."):
                st.image(uploaded_file, caption="アップロードされた画像", width=300)
                extracted_text = extract_text_from_image(uploaded_file)
                st.write("**画像から抽出されたテキスト:**")
                st.code(extracted_text)
                
                # 抽出したテキストを検索クエリとして使用（改行をスペースに置換）
                query_text = extracted_text.replace("\n", " ").strip()
        
        # 2. 手入力がある場合はそちらを優先
        if manual_query:
            query_text = manual_query.strip()
            
        # 3. VocaDBへの問い合わせ処理
        if query_text:
            with st.spinner("VocaDBデータベースを検索中..."):
                official_title = search_vocadb(query_text)
                if official_title:
                    st.success(f"🎉 特定成功！ 正式な曲名: **{official_title}**")
                else:
                    st.warning("VocaDBに一致する楽曲が見つかりませんでした。別のキーワードや鮮明な画像をお試しください。")
        else:
            st.warning("画像を入れるか、検索キーワードを入力してください。")
