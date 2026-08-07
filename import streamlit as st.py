import streamlit as st
import pandas as pd
import re
import requests
import json
import urllib.parse
import urllib.request
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

*   **🔗 URLから一括抽出:** YouTubeやニコニコなどのURLを入れるだけで、曲名と合成音声名をリストアップしExcel出力します。
*   **🖼️ 画像・ローマ字から楽曲特定:** スクショやローマ字から、正しい日本語の曲名を探し出します。
*   **📁 Excelからプレイリスト生成:** 曲名のリスト（Excel）を入れると、API不要ですぐに聴けるYouTubeプレイリストURLを作ります。
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
        if kw in title_str: found_vocals.add(kw)
    if not any(ng in desc_str for ng in ng_list):
        for kw in keywords:
            if kw in desc_str: found_vocals.add(kw)
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
    res = requests.get(api_url, headers={"X-Frontend-Id": "6", "X-Frontend-Version": "0"})
    res.encoding = 'utf-8'
    if res.status_code != 200: raise ValueError("ニコニコ動画のリストが読み込めませんでした。")
    items = res.json().get("data", {}).get("mylist", {}).get("items", [])
    videos = []
    for item in items:
        video = item.get("video", {})
        if video: videos.append({"曲名": video.get("title", "Unknown"), "概要欄データ": video.get("shortDescription", ""), "URL": f"https://www.nicovideo.jp/watch/{video.get('id', '')}"})
    return videos

def get_soundcloud_data(url):
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    res.encoding = 'utf-8'
    match = re.search(r"window\.__sc_hydration = (\[.*?\]);\s*</script>", res.text)
    if not match: raise ValueError("楽曲データが見つかりませんでした。")
    hydration_data = json.loads(match.group(1))
    videos = []
    for item in hydration_data:
        if item.get("hydratable") in ["playlist", "sound"]:
            tracks = item.get("data", {}).get("tracks", []) if item.get("hydratable") == "playlist" else [item.get("data", {})]
            for t in tracks:
                if isinstance(t, dict) and t.get("title"):
                    videos.append({"曲名": t.get("title"), "概要欄データ": f"{t.get('user', {}).get('username', '')} / {t.get('description') or ''}", "URL": t.get("permalink_url", "")})
            if videos: return videos
    return videos

def search_vocadb(query_text):
    url = "https://vocadb.net/api/songs"
    params = {"query": query_text, "maxResults": 1, "sort": "FavoritedTimes", "fields": "Names"}
    try:
        response = requests.get(url, params=params, headers={"Accept": "application/json"})
        if response.status_code == 200 and response.json().get("items"):
            return response.json()["items"][0]["defaultName"]
    except Exception as e:
        return f"検索エラー: {e}"
    return None

def extract_text_from_image(image_file):
    return pytesseract.image_to_string(Image.open(image_file), lang='eng+jpn').strip()

def search_youtube_no_api(query):
    """APIを使わずにYouTube検索結果から一番上の動画IDを取得する"""
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            video_ids = re.findall(r"watch\?v=([a-zA-Z0-9_-]{11})", html)
            if video_ids:
                return video_ids[0]
    except Exception:
        pass
    return None

# ==========================================
# 4. メイン画面（タブ構造）
# ==========================================
tab1, tab2, tab3 = st.tabs(["🔗 URLから一括抽出", "🖼️ 画像・ローマ字から楽曲特定", "📁 Excelからプレイリスト生成"])

# --- タブ1: 従来のプレイリスト抽出機能 ---
with tab1:
    st.header("⚙️ 1. システム設定")
    youtube_api_key = st.text_input("🔑 YouTube API Key (※YouTube抽出を行う場合のみ入力)", type="password")
    col1, col2 = st.columns(2)
    with col1:
        target_keywords = [k.strip() for k in st.text_area("🔍 抽出するワード", DEFAULT_KEYWORDS, height=100).split(",") if k.strip()]
    with col2:
        ng_words = [n.strip() for n in st.text_area("🚫 除外（NG）ワード", DEFAULT_NG_WORDS, height=100).split(",") if n.strip()]

    st.markdown("---")
    st.header("🔍 2. プレイリスト・楽曲URLの解析")
    playlist_url = st.text_input("URLを入力（YouTube / ニコニコ動画 / SoundCloud）")

    if st.button("一括抽出を開始する", type="primary"):
        if not playlist_url: st.warning("⚠️ URLを入力してください。")
        else:
            with st.spinner("データを取得・解析中..."):
                try:
                    raw_data = []
                    if "youtube.com" in playlist_url or "youtu.be" in playlist_url:
                        if not youtube_api_key: raise ValueError("YouTube API Keyが設定されていません。")
                        raw_data = get_youtube_playlist(youtube_api_key, playlist_url)
                    elif "nicovideo.jp" in playlist_url: raw_data = get_niconico_playlist(playlist_url)
                    elif "soundcloud.com" in playlist_url: raw_data = get_soundcloud_data(playlist_url)
                    else: raise ValueError("対応していないURLです。")

                    results = [{"曲名": clean_title(item["曲名"]), "合成音声": extract_vocals(item["曲名"], item["概要欄データ"], target_keywords, ng_words), "URL": item["URL"]} for item in raw_data]
                    df = pd.DataFrame(results)
                    
                    if df.empty: st.warning("対象となる楽曲が見つかりませんでした。")
                    else:
                        st.success(f"✅ {len(df)}曲の解析が完了しました！")
                        st.dataframe(df, use_container_width=True)
                        output = BytesIO()
                        with pd.ExcelWriter(output, engine='xlsxwriter') as writer: df.to_excel(writer, index=False, sheet_name='Playlist Data')
                        st.download_button("📥 Excelファイルとしてダウンロード", data=output.getvalue(), file_name="playlist_result.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                except Exception as e: st.error(f"❌ エラーが発生しました: {e}")

# --- タブ2: 画像認識 (OCR) & VocaDB検索 ---
with tab2:
    st.header("🖼️ 画像認識・ローマ字から楽曲特定")
    uploaded_file = st.file_uploader("楽曲名が写っている画像（スクショなど）をアップロード", type=["png", "jpg", "jpeg"])
    manual_query = st.text_input("または、検索したい文字列（ローマ字など）を直接入力")
    
    if st.button("楽曲を特定する", type="primary"):
        query_text = ""
        if uploaded_file is not None:
            with st.spinner("画像を解析中..."):
                st.image(uploaded_file, caption="アップロードされた画像", width=300)
                extracted_text = extract_text_from_image(uploaded_file)
                st.write("**画像から抽出されたテキスト:**"); st.code(extracted_text)
                query_text = extracted_text.replace("\n", " ").strip()
        if manual_query: query_text = manual_query.strip()
            
        if query_text:
            with st.spinner("VocaDBデータベースを検索中..."):
                official_title = search_vocadb(query_text)
                if official_title: st.success(f"🎉 特定成功！ 正式な曲名: **{official_title}**")
                else: st.warning("VocaDBに一致する楽曲が見つかりませんでした。")
        else: st.warning("画像を入れるか、検索キーワードを入力してください。")

# --- タブ3: Excelからプレイリスト生成 (API不要版) ---
with tab3:
    st.header("📁 Excelからプレイリスト生成 (API不要版)")
    st.markdown("アップロードしたExcelファイルの楽曲リストから、即席のYouTubeプレイリストURLを生成します。（※ログインやAPIキー設定は一切不要です）")
    
    uploaded_excel = st.file_uploader("楽曲リスト（Excelファイル）をアップロード", type=["xlsx"])
    
    if st.button("プレイリストURLを生成する", type="primary"):
        if uploaded_excel is not None:
            with st.spinner("楽曲を検索してプレイリストを構築中...（曲数が多いと時間がかかります）"):
                try:
                    df = pd.read_excel(uploaded_excel)
                    # 「曲名」列があればそれを使用、なければ一番左の列を使用
                    col_name = "曲名" if "曲名" in df.columns else df.columns[0]
                    songs = df[col_name].dropna().astype(str).tolist()
                    
                    if not songs:
                        st.warning("ファイル内に楽曲名が見つかりませんでした。")
                    else:
                        video_ids = []
                        progress_bar = st.progress(0)
                        
                        for i, song in enumerate(songs):
                            vid = search_youtube_no_api(song)
                            if vid:
                                video_ids.append(vid)
                            progress_bar.progress((i + 1) / len(songs))
                            
                        if video_ids:
                            st.success(f"✅ {len(video_ids)}曲の動画データを取得しました！")
                            # YouTubeの「watch_videos」URLは長すぎるとエラーになるため、最大50曲ずつに分割して出力
                            chunked_ids = [video_ids[i:i + 50] for i in range(0, len(video_ids), 50)]
                            
                            for idx, chunk in enumerate(chunked_ids):
                                playlist_url = f"https://www.youtube.com/watch_videos?video_ids={','.join(chunk)}"
                                st.markdown(f"**🎧 プレイリスト Part {idx+1} (最大50曲):**\n[ここをクリックして連続再生を開始する]({playlist_url})")
                                st.code(playlist_url)
                        else:
                            st.error("YouTube上で一致する楽曲が一つも見つかりませんでした。")
                except Exception as e:
                    st.error(f"❌ エラーが発生しました: {e}")
        else:
            st.warning("Excelファイルをアップロードしてください。")
