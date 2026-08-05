import streamlit as st
import pandas as pd
import re
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import feedparser
import requests
from bs4 import BeautifulSoup
import json
from io import BytesIO

# ==========================================
# 1. 初期設定とデフォルト辞書
# ==========================================
DEFAULT_KEYWORDS = "初音ミク, 鏡音リン, 鏡音レン, 巡音ルカ, MEIKO, KAITO, 星界, 可不, 重音テト, 花隈千冬, 夏色花梨, 小春六花"
DEFAULT_NG_WORDS = "アルバム, クロスフェード, 配信, BOOTH, Tracklist, 参加, 収録, 歌ってみた"

st.set_page_config(page_title="プレイリスト解析ツール", layout="wide")
st.title("🎶 楽曲情報・合成音声名 抽出システム")

# ==========================================
# 2. サイドバー（設定画面）
# ==========================================
with st.sidebar:
    st.header("⚙️ システム設定")
    
    st.subheader("🔑 APIキー設定")
    youtube_api_key = st.text_input("YouTube API Key", type="password")
    # ※Spotifyの認証キー入力欄は不要になったため削除
    
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
                "概要欄データ": snippet["description"],
                "URL": f"https://www.youtube.com/watch?v={snippet['resourceId']['videoId']}"
            })
            
        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break
    return videos

def get_niconico_playlist(url):
    if "?rss=2.0" not in url:
        rss_url = url.split("?")[0] + "?rss=2.0"
    else:
        rss_url = url
        
    feed = feedparser.parse(rss_url)
    if feed.bozo:
        raise ValueError("ニコニコ動画のリストが読み込めませんでした。非公開設定になっていないか確認してください。")
        
    videos = []
    for entry in feed.entries:
        videos.append({
            "曲名": entry.title,
            "概要欄データ": entry.get('summary', ''),
            "URL": entry.link
        })
    return videos

def get_spotify_playlist(url):
    """Spotifyの公開ページからスクレイピングでデータを取得"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
    }
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        raise ValueError(f"Spotifyページの取得に失敗しました (Status: {res.status_code})")
        
    soup = BeautifulSoup(res.text, "html.parser")
    videos = []
    
    # 検索エンジン用に埋め込まれた構造化データ（JSON-LD）を解析
    json_ld_tags = soup.find_all('script', type='application/ld+json')
    for tag in json_ld_tags:
        try:
            data = json.loads(tag.string)
            if isinstance(data, dict) and data.get('@type') == 'MusicPlaylist':
                tracks = data.get('track', [])
                # データ構造がItemListElementの場合の対応
                if isinstance(tracks, dict) and 'itemListElement' in tracks:
                    tracks = tracks['itemListElement']
                    
                for t in tracks:
                    item = t.get('item', t) if isinstance(t, dict) else {}
                    if item.get('@type') == 'MusicRecording':
                        name = item.get('name', 'Unknown')
                        url = item.get('url', '')
                        
                        # アーティスト名を概要欄代わりとして結合
                        artists_data = item.get('byArtist', [])
                        if not isinstance(artists_data, list):
                            artists_data = [artists_data]
                        artists = ", ".join([a.get('name', '') for a in artists_data if isinstance(a, dict)])
                        
                        videos.append({
                            "曲名": name,
                            "概要欄データ": artists,
                            "URL": url
                        })
                
                if videos:
                    return videos # 無事にデータが取れたら終了
        except Exception:
            continue
            
    raise ValueError("Spotifyのページから楽曲データを見つけられませんでした。非公開リストであるか、仕様変更の可能性があります。")

# ==========================================
# 4. メイン画面（実行UI）
# ==========================================
playlist_url = st.text_input("再生リストのURLを入力（YouTube / Spotify / ニコニコ動画）")

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
                    
                elif "spotify.com" in playlist_url:
                    raw_data = get_spotify_playlist(playlist_url)
                    
                elif "nicovideo.jp" in playlist_url:
                    raw_data = get_niconico_playlist(playlist_url)
                    
                else:
                    raise ValueError("対応していないURLです。YouTube、Spotify、ニコニコ動画のリストを入力してください。")

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
                    st.warning("データの取得は成功しましたが、曲が見つかりませんでした。")
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
