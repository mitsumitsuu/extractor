import streamlit as st
import pandas as pd
import re
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
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
    api_key = st.text_input("YouTube API Key", type="password")
    
    st.subheader("🔍 抽出するワード")
    keywords_input = st.text_area("合成音声名（カンマ区切り）", DEFAULT_KEYWORDS, height=100)
    target_keywords = [k.strip() for k in keywords_input.split(",") if k.strip()]
    
    st.subheader("🚫 除外（NG）ワード")
    st.write("概要欄にこれらの言葉がある場合、概要欄からの抽出をストップします（誤抽出防止）")
    ng_words_input = st.text_area("NGワード（カンマ区切り）", DEFAULT_NG_WORDS, height=100)
    ng_words = [n.strip() for n in ng_words_input.split(",") if n.strip()]

# ==========================================
# 3. データ処理ロジック（関数）
# ==========================================
def extract_vocals(title, description, keywords, ng_list):
    """タイトルと概要欄から特定のワードを抽出する（ノイズ対策版）"""
    found_vocals = set()
    
    # 1. タイトルからは無条件で抽出
    for kw in keywords:
        if kw in title:
            found_vocals.add(kw)
            
    # 2. 概要欄のチェック（NGワードが含まれていないか）
    has_ng_word = any(ng in description for ng in ng_list)
    
    # NGワードがなければ、概要欄からも抽出
    if not has_ng_word:
        for kw in keywords:
            if kw in description:
                found_vocals.add(kw)
                
    return " / ".join(list(found_vocals))

def get_youtube_playlist(api_key, playlist_id):
    """YouTube APIを利用して全件取得するループ処理"""
    youtube = build("youtube", "v3", developerKey=api_key)
    videos = []
    next_page_token = None
    
    while True:
        request = youtube.playlistItems().list(
            part="snippet",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page_token
        )
        response = request.execute()
        
        for item in response.get("items", []):
            snippet = item["snippet"]
            title = snippet["title"]
            description = snippet["description"]
            video_id = snippet["resourceId"]["videoId"]
            
            # 削除された動画や非公開動画をスキップ
            if title == "Private video" or title == "Deleted video":
                continue
                
            videos.append({
                "曲名": title,
                "概要欄データ": description,
                "URL": f"https://www.youtube.com/watch?v={video_id}"
            })
            
        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break # 次のページが無ければループ終了
            
    return videos

# ==========================================
# 4. メイン画面（実行UI）
# ==========================================
playlist_url = st.text_input("YouTubeの再生リストURLを入力してください")

if st.button("抽出を開始する", type="primary"):
    if not api_key or not playlist_url:
        st.warning("⚠️ APIキーと再生リストURLの両方を入力してください。")
    else:
        playlist_id_match = re.search(r"list=([a-zA-Z0-9_-]+)", playlist_url)
        
        if playlist_id_match:
            playlist_id = playlist_id_match.group(1)
            
            with st.spinner("YouTubeからデータを取得・解析中...（曲数が多いと数十秒かかります）"):
                try:
                    # 1. APIでデータを全件取得
                    raw_videos = get_youtube_playlist(api_key, playlist_id)
                    
                    # 2. 取得したデータからワードを抽出して整理
                    results = []
                    for video in raw_videos:
                        vocals = extract_vocals(video["曲名"], video["概要欄データ"], target_keywords, ng_words)
                        results.append({
                            "曲名": video["曲名"],
                            "合成音声": vocals,
                            "URL": video["URL"]
                        })
                    
                    df = pd.DataFrame(results)
                    
                    st.success(f"✅ {len(df)}曲の解析が完了しました！")
                    st.dataframe(df, use_container_width=True)
                    
                    # 3. Excel形式でダウンロードするための処理
                    output = BytesIO()
                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        df.to_excel(writer, index=False, sheet_name='Playlist Data')
                    excel_data = output.getvalue()
                    
                    st.download_button(
                        label="📥 Excelファイル（.xlsx）としてダウンロード",
                        data=excel_data,
                        file_name="playlist_result.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    
                except HttpError as e:
                    st.error(f"❌ YouTube APIエラーが発生しました。APIキーが正しいか確認してください。詳細: {e}")
                except Exception as e:
                    st.error(f"❌ 予期せぬエラーが発生しました: {e}")
        else:
            st.error("❌ 有効なYouTube再生リストのURLが見つかりませんでした。")
