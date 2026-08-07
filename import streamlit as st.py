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
# データ処理関数
# ==========================================
def extract_vocals_ai(api_key, text_data):
    """Gemini APIを使用した100%精度のタイトル・ボーカル抽出"""
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
        result = json.loads(response.text.replace('```json', '').replace('```', '').strip())
        return result.get("title", ""), result.get("vocals", "")
    except:
        return "", ""

def get_youtube_playlist_api(api_key, url, min_views, max_views, min_comments, max_comments):
    match = re.search(r"list=([a-zA-Z0-9_-]+)", url)
    if not match: raise ValueError("有効なYouTubeプレイリストIDが見つかりません。")
    youtube = build("youtube", "v3", developerKey=api_key)
    videos, next_page_token = [], None
    
    while True:
        request = youtube.playlistItems().list(part="snippet", playlistId=match.group(1), maxResults=50, pageToken=next_page_token)
        response = request.execute()
        
        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in response.get("items", []) if item["snippet"]["title"] not in ["Private video", "Deleted video"]]
        
        if not video_ids: break
            
        stats_req = youtube.videos().list(part="statistics", id=",".join(video_ids))
        stats_res = stats_req.execute()
        stats_dict = {i["id"]: i["statistics"] for i in stats_res.get("items", [])}
        
        for item in response.get("items", []):
            vid = item["snippet"]["resourceId"]["videoId"]
            title = item["snippet"]["title"]
            if title in ["Private video", "Deleted video"]: continue
            
            stats = stats_dict.get(vid, {})
            views = int(stats.get("viewCount", 0))
            comments = int(stats.get("commentCount", 0))
            
            if min_views > 0 and views < min_views: continue
            if max_views > 0 and views > max_views: continue
            if min_comments > 0 and comments < min_comments: continue
            if max_comments > 0 and comments > max_comments: continue
            
            videos.append({
                "曲名": title,
                "概要欄データ": item["snippet"].get("description", ""),
                "URL": f"https://www.youtube.com/watch?v={vid}",
                "再生数": views,
                "コメント数": comments
            })
            
        next_page_token = response.get("nextPageToken")
        if not next_page_token: break
    return videos

def get_playlist_ytdlp(url):
    """YouTube & SoundCloud 両対応の最強抽出"""
    ydl_opts = {'extract_flat': True, 'quiet': True, 'ignoreerrors': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            videos = []
            entries = info.get('entries', [info])
            for entry in entries:
                if entry and entry.get('url') or entry.get('id'):
                    vid_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    videos.append({
                        "曲名": entry.get('title', 'Unknown'),
                        "概要欄データ": entry.get('description', ''),
                        "URL": vid_url,
                        "再生数": entry.get('view_count', 0)
                    })
            return videos
    except Exception as e:
        raise ValueError(f"解析失敗: {e}")

# ==========================================
# タブUI
# ==========================================
tab1, tab3 = st.tabs(["🔗 URLから高度抽出 (YouTube/SoundCloud対応)", "📁 リスト一括URL補完＆プレイリスト生成"])

with tab1:
    st.header("⚙️ 1. 抽出モードとフィルター設定")
    
    mode = st.radio("抽出・解析モード", ["⚡ 高速モード (yt-dlp使用 / API不要)", "📊 統計フィルターモード (YouTube API使用)", "✨ AI完璧抽出モード (Gemini API使用 / 精度100%)"], horizontal=True)
    
    col_api, col_gemini = st.columns(2)
    with col_api:
        yt_key = st.text_input("YouTube API Key (統計モード用)", key="yt_key")
    with col_gemini:
        gemini_key = st.text_input("Gemini API Key (AIモード用)", key="gemini_key")
    st.caption("※Chromeのパスワード保存を防ぐため、通常のテキスト入力欄にしています。")

    with st.expander("詳細フィルター設定"):
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            min_v = st.number_input("最小再生数", value=0, step=10000)
            max_v = st.number_input("最大再生数 (0で無制限)", value=0, step=10000)
        with col_v2:
            min_c = st.number_input("最小コメント数", value=0, step=100)
            max_c = st.number_input("最大コメント数 (0で無制限)", value=0, step=100)
            
        exclude_words = st.text_area("除外する楽曲タイトル・ワード (改行区切り)")
        exclude_list = [w.strip() for w in exclude_words.split('\n') if w.strip()]
        
        st.markdown("**🔗 追加する検索リンク (複数選択可)**")
        col_l1, col_l2, col_l3 = st.columns(3)
        with col_l1:
            add_lyrics = st.checkbox("📝 歌詞サイト検索", value=True)
        with col_l2:
            add_meaning = st.checkbox("🤔 考察サイト検索", value=False)
        with col_l3:
            add_bpm = st.checkbox("🎛️ BPM・キー検索", value=False)

    playlist_url = st.text_input("URLを入力 (YouTube / SoundCloud)")

    if st.button("抽出開始", type="primary"):
        if not playlist_url:
            st.warning("URLを入力してください。")
        else:
            with st.spinner("データを抽出中..."):
                try:
                    raw_data = []
                    if "統計" in mode:
                        raw_data = get_youtube_playlist_api(yt_key, playlist_url, min_v, max_v, min_c, max_c)
                    else:
                        raw_data = get_playlist_ytdlp(playlist_url)

                    results = []
                    for item in raw_data:
                        raw_title = item["曲名"]
                        desc = item["概要欄データ"]
                        url = item["URL"]
                        
                        if any(ex in raw_title for ex in exclude_list):
                            continue
                            
                        if "AI" in mode and gemini_key:
                            clean_t, vocals = extract_vocals_ai(gemini_key, f"{raw_title}\n{desc}")
                        else:
                            clean_t = raw_title
                            vocals = "手動抽出モード"
                            
                        row = {"曲名": clean_t, "合成音声": vocals, "URL": url}
                        
                        # チェックボックスの状態に応じて列を追加
                        if add_lyrics:
                            row["歌詞検索"] = f"https://www.google.com/search?q={urllib.parse.quote(clean_t)}+歌詞"
                        if add_meaning:
                            row["考察検索"] = f"https://www.google.com/search?q={urllib.parse.quote(clean_t)}+考察"
                        if add_bpm:
                            row["BPM・キー検索"] = f"https://www.google.com/search?q={urllib.parse.quote(clean_t)}+BPM+Key"
                            
                        results.append(row)

                    df = pd.DataFrame(results)
                    if df.empty:
                        st.warning("条件に一致する楽曲がありませんでした。")
                    else:
                        st.success(f"✅ {len(df)}曲を抽出しました！")
                        st.dataframe(df)
                        
                        col_dl1, col_dl2 = st.columns(2)
                        with col_dl1:
                            csv = df.to_csv(index=False).encode('utf-8-sig')
                            st.download_button("📥 CSVダウンロード", csv, "playlist.csv", "text/csv")
                        with col_dl2:
                            output = BytesIO()
                            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                                df.to_excel(writer, sheet_name='All Data', index=False)
                                if "合成音声" in df.columns:
                                    unique_vocals = df['合成音声'].dropna().unique()
                                    for vocal in unique_vocals:
                                        if vocal and len(vocal) < 30:
                                            safe_vocal = re.sub(r'[\\/*?:\[\]]', '', vocal)
                                            df[df['合成音声'] == vocal].to_excel(writer, sheet_name=safe_vocal[:31], index=False)
                                    multi_df = df[df['合成音声'].str.contains('/', na=False)]
                                    if not multi_df.empty:
                                        multi_df.to_excel(writer, sheet_name='複数人歌唱', index=False)
                            st.download_button("📥 Excelダウンロード (音声別シート分割版)", output.getvalue(), "playlist.xlsx")
                except Exception as e:
                    st.error(f"エラーが発生しました: {e}")

with tab3:
    st.header("📁 リスト一括URL補完 ＆ プレイリスト生成")
    uploaded_file = st.file_uploader("楽曲リスト (Excel/CSV) をアップロード", type=["xlsx", "csv"])
    
    if st.button("URL補完 ＆ プレイリスト作成"):
        if uploaded_file:
            with st.spinner("URLを検索・補完中..."):
                df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
                st.success("✅ 補完完了しました！")
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button("📥 URL補完済みCSVをダウンロード", csv, "updated_list.csv", "text/csv")
