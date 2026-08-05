import streamlit as st
import pandas as pd
import re
import requests
import json
from googleapiclient.discovery import build
from io import BytesIO

# ==========================================
# 1. 初期設定とデフォルト辞書
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"

st.set_page_config(page_title="楽曲情報抽出システム", layout="wide")
st.title("🎶 楽曲抽出システム (YouTube / ニコニコ / SoundCloud)")

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
# 3. データ処理ロジック（関数）
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

def get_youtube_playlist(api_key, url):
    match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
    if not match:
        raise ValueError("有効なYouTubeプレイリストIDが見つかりません。")
    
    youtube = build("youtube", "v3", developerKey=api_key)
    videos = []
    next_page_token = None
    
    while True:
        request = youtube.playlistItems().list(
            part="snippet",
            playlistId=match.group(1),
            maxResults=50,
            pageToken=next_page_token
        )
        response = request.execute()
        
        for item in response.get("items", []):
            snippet = item["snippet"]
            title = snippet["title"]
            if title in ["Private video", "Deleted video"]:
                continue
            videos.append({
                "曲名": title,
                "概要欄データ": snippet.get("description", ""),
                "URL": f"[https://www.youtube.com/watch?v=](https://www.youtube.com/watch?v=){snippet['resourceId']['videoId']}"
            })
            
        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break
    return videos

def get_niconico_playlist(url):
    match = re.search(r"mylist/(\d+)", url)
    if not match:
        raise ValueError("有効なニコニコ動画のマイリストURLが見つかりません。")
        
    mylist_id = match.group(1)
    api_url = f"[https://nvapi.nicovideo.jp/v2/mylists/](https://nvapi.nicovideo.jp/v2/mylists/){mylist_id}"
    
    headers = {
        "X-Frontend-Id": "6",
        "X-Frontend-Version": "0"
    }
    
    res = requests.get(api_url, headers=headers)
    if res.status_code != 200:
        raise ValueError(f"ニコニコ動画のリストが読み込めませんでした (Status: {res.status_code})。")
        
    data = res.json()
    if data.get("meta", {}).get("status") != 200:
        raise ValueError("データの取得に失敗しました。非公開リストの可能性があります。")
        
    items = data.get("data", {}).get("mylist", {}).get("items", [])
    
    videos = []
    for item in items:
        video = item.get("video", {})
        if not video:
            continue
            
        videos.append({
            "曲名": video.get("title", "Unknown"),
            "概要欄データ": video.get("shortDescription", ""),
            "URL": f"[https://www.nicovideo.jp/watch/](https://www.nicovideo.jp/watch/){video.get('id', '')}"
        })
    return videos

def get_soundcloud_data(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        raise ValueError(f"SoundCloudのページ取得に失敗しました (Status: {res.status_code})")
        
    # SoundCloudのページに埋め込まれた状態データ（JSON）を正規表現で抽出
    match = re.search(r"window\.__sc_hydration = (\[.*?\]);\s*</script>", res.text)
    if not match:
        raise ValueError("楽曲データが見つかりませんでした。非公開設定の可能性があります。")
        
    try:
        hydration_data = json.loads(match.group(1))
    except:
        raise ValueError("SoundCloudのデータ解析に失敗しました。")
        
    videos = []
    
    for item in hydration_data:
        # プレイリストの場合
        if item.get("hydratable") == "playlist":
            tracks = item.get("data", {}).get("tracks", [])
            for t in tracks:
                if isinstance(t, dict) and t.get("title"):
                    # 投稿ユーザー名と説明文を概要欄として扱う
                    user = t.get("user", {}).get("username", "")
                    desc = t.get("description") or ""
                    videos.append({
                        "曲名": t.get("title"),
                        "概要欄データ": f"{user} / {desc}",
                        "URL": t.get("permalink_url", "")
                    })
            if videos:
                return videos
                
        # 単一楽曲の場合
        elif item.get("hydratable") == "sound":
            t = item.get("data", {})
            if isinstance(t, dict) and t.get("title"):
                user = t.get("user", {}).get("username", "")
                desc = t.get("description") or ""
                videos.append({
                    "曲名": t.get("title"),
                    "概要欄データ": f"{user} / {desc}",
                    "URL": t.get("permalink_url", "")
                })
            if videos:
                return videos

    if not videos:
        raise ValueError("SoundCloudから有効なデータを抽出できませんでした。")
    return videos

# ==========================================
# 4. メイン画面（実行UI）
# ==========================================
playlist_url = st.text_input("URLを入力（YouTube / ニコニコ動画 / SoundCloud）")

if st.button("抽出を開始する", type="primary"):
    if not playlist_url:
        st.warning("⚠️ URLを入力してください。")
    else:
        with st.spinner("データを取得・解析中..."):
            try:
                raw_data = []
                if "youtube.com" in playlist_url or "youtu.be" in playlist_url:
                    if not youtube_api_key:
                        raise ValueError("YouTube API Keyが設定されていません。")
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
                    results.append({
                        "曲名": item["曲名"],
                        "合成音声": vocals,
                        "URL": item["URL"]
                    })
                
                df = pd.DataFrame(results)
                
                if df.empty:
                    st.warning("データの取得は成功しましたが、対象となる楽曲が見つかりませんでした。")
                else:
                    st.success(f"✅ {len(df)}曲の解析が完了しました！")
                    st.dataframe(df, use_container_width=True)
                    
                    output = BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        df.to_excel(writer, index=False, sheet_name='Playlist Data')
                    
                    st.download_button(
                        label="📥 Excelファイル（.xlsx）としてダウンロード",
                        data=output.getvalue(),
                        file_name="playlist_result.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                
            except Exception as e:
                st.error(f"❌ エラーが発生しました: {e}")
